"""What a battery one might buy would have saved, simulated over the measured history.

Per hour from the energy counters: what went to and came from the grid, and what the existing
battery charged and discharged. "add" puts the new battery behind the existing one (it only sees
what the existing one left to the grid), "replace" takes the existing one out first. Hourly values
net out import and export within an hour, so the result is a little optimistic.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
import math
from typing import Any

from sunflow.config.models import BatteryScenario
from sunflow.ha import HAClient
from sunflow.planning.optimizer import BatterySpec, Settings, SlotInput, optimize
from sunflow.planning.plan import (
    AUTARKY_IMPORT_PENALTY_CT,
    BUFFER_PENALTY_SHARE,
    BUFFER_Z,
    DEFAULT_LOAD_SPREAD,
    MAX_BUFFER_SHARE,
    PV_ABSORPTION_PENALTY_CT,
    STORAGE_PREMIUM,
    cheap_slots,
    storage_buffers,
)

from .costs import hourly_changes, hourly_prices
from .energy import EnergySources

# The optimized run looks this far ahead and keeps the first day of each window
WINDOW_HOURS = 48
STEP_HOURS = 24
QUARTERS = 4
# Planned as Sunflow does with each of its strategies; "self_consumption" runs without control
STRATEGIES = ("balanced", "pv_absorption", "autarky", "storage")


@dataclass(frozen=True, kw_only=True)
class Hour:
    """Measured energy of one hour (Wh) and its prices (€/kWh)."""

    start: datetime
    imported: float
    exported: float
    charged: float
    discharged: float
    price: float
    feed_in: float


@dataclass(kw_only=True)
class Run:
    """Totals of one simulated (or the measured) operation."""

    imported_wh: float = 0.0
    exported_wh: float = 0.0
    cost: float = 0.0
    discharged_wh: float = 0.0

    def add(self, imported: float, exported: float, hour: Hour, discharged: float = 0.0) -> None:
        """Count one hour's grid energy at its prices."""
        self.imported_wh += imported
        self.exported_wh += exported
        self.cost += (imported * hour.price - exported * hour.feed_in) / 1000
        self.discharged_wh += discharged

    def as_dict(self) -> dict[str, float]:
        """For the API, in kWh and €."""
        return {
            "imported_kwh": self.imported_wh / 1000,
            "exported_kwh": self.exported_wh / 1000,
            "cost": self.cost,
            "discharged_kwh": self.discharged_wh / 1000,
        }


async def history_hours(
    client: HAClient,
    sources: EnergySources,
    price_entity: str | None,
    fixed_price: float | None,
    feed_in: float,
    days: int,
    tz: tzinfo,
) -> list[Hour]:
    """Complete hours of the last days with grid counters and a known price."""
    end = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    start = (end - timedelta(days=days)).replace(hour=0)
    keys = ("grid_import", "grid_export", "battery_charge", "battery_discharge")
    per_hour = await hourly_changes(client, sources, keys, start, end)
    price_at = await hourly_prices(client, price_entity, fixed_price, start, end)
    hours = []
    for start_hour in sorted(set(per_hour["grid_import"]) & set(per_hour["grid_export"])):
        if (price := price_at(start_hour)) is None:
            continue
        hours.append(
            Hour(
                start=start_hour,
                imported=max(per_hour["grid_import"][start_hour], 0.0),
                exported=max(per_hour["grid_export"][start_hour], 0.0),
                charged=max(per_hour["battery_charge"].get(start_hour, 0.0), 0.0),
                discharged=max(per_hour["battery_discharge"].get(start_hour, 0.0), 0.0),
                price=price,
                feed_in=feed_in,
            )
        )
    return hours


def _net(hour: Hour, mode: str) -> float:
    """What the house needed from the grid (Wh, negative: surplus) before the new battery."""
    net = hour.imported - hour.exported
    # Without the existing battery its charge would have gone to the grid, its discharge come from it
    return net + hour.discharged - hour.charged if mode == "replace" else net


def _efficiencies(scenario: BatteryScenario) -> tuple[float, float]:
    battery = math.sqrt(scenario.efficiency_pct / 100)
    inverter = scenario.inverter_efficiency_pct / 100
    return battery * inverter, battery * inverter


def _bounds(scenario: BatteryScenario) -> tuple[float, float]:
    capacity = scenario.capacity_kwh * 1000
    return capacity, capacity * (1 - scenario.usable_pct / 100)


def _credit_rest(run: Run, hours: list[Hour], soc: float, floor: float, eta_d: float) -> Run:
    """Energy left in the battery at the end is worth what it saves later (as the optimizer values it)."""
    if hours:
        mean_price = sum(h.price for h in hours) / len(hours)
        run.cost -= (soc - floor) * eta_d / 1000 * mean_price
    return run


def self_consumption(hours: list[Hour], scenario: BatteryScenario) -> Run:
    """The battery only takes surplus and covers what is missing, as any battery on its own."""
    eta_c, eta_d = _efficiencies(scenario)
    capacity, floor = _bounds(scenario)
    soc = floor
    run = Run()
    for hour in hours:
        net = _net(hour, scenario.mode)
        discharged = 0.0
        if net < 0:
            charge = min(-net, scenario.max_charge_kw * 1000, (capacity - soc) / eta_c)
            soc += charge * eta_c
            net += charge
        else:
            discharged = min(net, scenario.max_discharge_kw * 1000, (soc - floor) * eta_d)
            soc -= discharged / eta_d
            net -= discharged
        run.add(max(net, 0.0), max(-net, 0.0), hour, discharged)
    return _credit_rest(run, hours, soc, floor, eta_d)


def _strategy(
    inputs: list[SlotInput], scenario: BatteryScenario, strategy: str, round_trip: float
) -> tuple[list[SlotInput], Settings]:
    """The window's slots and optimizer settings as the strategy plans them (see plan.compute_plan)."""
    autarky = strategy == "autarky"
    settings = Settings(
        wear_ct_per_kwh=scenario.wear_ct_per_kwh(),
        allow_grid_charge=scenario.grid_charge and not autarky,
        import_penalty_ct_per_kwh=AUTARKY_IMPORT_PENALTY_CT if autarky else 0.0,
        export_penalty_ct_per_kwh=PV_ABSORPTION_PENALTY_CT if strategy == "pv_absorption" else 0.0,
    )
    premium = STORAGE_PREMIUM if strategy == "storage" else 0.0
    if premium:
        cheap = cheap_slots([slot.price for slot in inputs])
        inputs = [replace(slot, import_penalty=0.0 if c else premium) for slot, c in zip(inputs, cheap, strict=True)]
    if settings.allow_grid_charge and strategy in BUFFER_Z:
        capacity, floor = _bounds(scenario)
        buffers = storage_buffers(
            inputs, BUFFER_Z[strategy], DEFAULT_LOAD_SPREAD, round_trip, BUFFER_PENALTY_SHARE,
            MAX_BUFFER_SHARE * (capacity - floor), premium,
        )
        settings = replace(settings, buffer=tuple((b.slot, b.wh, b.penalty) for b in buffers))
    return inputs, settings


def optimized(hours: list[Hour], scenario: BatteryScenario, strategy: str = "balanced") -> Run:
    """Planned with prices and consumption known in advance, as Sunflow would with a perfect forecast.

    Each window of two days is optimized and its first day kept, starting where the last ended.
    """
    eta_c, eta_d = _efficiencies(scenario)
    capacity, floor = _bounds(scenario)
    soc = floor
    run = Run()
    for first in range(0, len(hours), STEP_HOURS):
        window = hours[first : first + WINDOW_HOURS]
        inputs = [
            SlotInput(
                start=hour.start + timedelta(minutes=15 * q),
                pv_w=max(-_net(hour, scenario.mode), 0.0),
                load_w=max(_net(hour, scenario.mode), 0.0),
                price=hour.price,
                feed_in=hour.feed_in,
            )
            for hour in window
            for q in range(QUARTERS)
        ]
        battery = BatterySpec(
            capacity_wh=capacity, soc_wh=soc, min_soc_wh=floor,
            max_charge_w=scenario.max_charge_kw * 1000, max_discharge_w=scenario.max_discharge_kw * 1000,
            efficiency_charge=eta_c, efficiency_discharge=eta_d,
        )
        inputs, settings = _strategy(inputs, scenario, strategy, eta_c * eta_d)
        plan = optimize(inputs, battery, settings)
        kept = min(STEP_HOURS, len(window))
        if len(plan.slots) < kept * QUARTERS:
            # Not solvable (should not happen): this day as on its own
            day = self_consumption(window[:kept], scenario)
            run.imported_wh += day.imported_wh
            run.exported_wh += day.exported_wh
            run.cost += day.cost
            run.discharged_wh += day.discharged_wh
            continue
        for index, hour in enumerate(window[:kept]):
            slots = plan.slots[index * QUARTERS : (index + 1) * QUARTERS]
            run.add(
                sum(s.import_w for s in slots) / QUARTERS,
                sum(s.export_w for s in slots) / QUARTERS,
                hour,
                sum(s.discharge_w for s in slots) / QUARTERS,
            )
        soc = plan.slots[kept * QUARTERS - 1].soc_wh
    return _credit_rest(run, hours, soc, floor, eta_d)


def without_battery(hours: list[Hour]) -> Run:
    """The house with no battery at all: the existing one taken out, nothing added."""
    run = Run()
    for hour in hours:
        net = _net(hour, "replace")
        run.add(max(net, 0.0), max(-net, 0.0), hour)
    return run


def _payback(price: float | None, saving_per_year: float) -> float | None:
    return price / saving_per_year if price and saving_per_year > 0 else None


def simulate(
    hours: list[Hour],
    scenarios: list[BatteryScenario],
    existing_price: float | None = None,
    strategies: tuple[str, ...] = ("balanced",),
) -> dict[str, Any]:
    """Measured operation, the house without any battery and, per scenario, both ways of running the battery.

    The existing battery pays back against no battery at all, a scenario against today.
    """
    measured = Run()
    for hour in hours:
        measured.add(hour.imported, hour.exported, hour)
    none = without_battery(hours)
    days = len(hours) / 24
    year = 365 / days if days else 0.0
    results = []
    for scenario in scenarios:
        runs = {"self_consumption": self_consumption(hours, scenario)}
        runs |= {strategy: optimized(hours, scenario, strategy) for strategy in strategies}
        usable = scenario.capacity_kwh * scenario.usable_pct / 100
        entry: dict[str, Any] = {"id": scenario.id, "name": scenario.name, "mode": scenario.mode, "runs": {}}
        for key, run in runs.items():
            saving = measured.cost - run.cost
            entry["runs"][key] = run.as_dict() | {
                "saving": saving,
                "saving_per_year": saving * year,
                "saving_vs_none_per_year": (none.cost - run.cost) * year,
                "cycles_per_year": run.discharged_wh / 1000 / usable * year if usable else None,
                "payback_years": _payback(scenario.price_eur, saving * year),
            }
        results.append(entry)
    return {
        "days": days,
        "start": hours[0].start.isoformat() if hours else None,
        "end": (hours[-1].start + timedelta(hours=1)).isoformat() if hours else None,
        "measured": measured.as_dict()
        | {
            "saving_vs_none_per_year": (none.cost - measured.cost) * year,
            "payback_years": _payback(existing_price, (none.cost - measured.cost) * year),
        },
        "without_battery": none.as_dict(),
        "has_battery": any(h.charged or h.discharged for h in hours),
        "scenarios": results,
    }
