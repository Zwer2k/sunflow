"""What a battery one might buy would have saved, simulated over the measured history.

Per hour from the energy counters: what went to and came from the grid, and what the existing
battery charged and discharged. "add" puts the new battery behind the existing one (it only sees
what the existing one left to the grid), "replace" takes the existing one out first. Hourly values
net out import and export within an hour, so the result is a little optimistic.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
import math
from typing import Any

from sunflow.config.models import BatteryScenario
from sunflow.ha import HAClient
from sunflow.planning.optimizer import BatterySpec, Settings, SlotInput, optimize

from .costs import hourly_changes, hourly_prices
from .energy import EnergySources

# The optimized run looks this far ahead and keeps the first day of each window
WINDOW_HOURS = 48
STEP_HOURS = 24
QUARTERS = 4


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


def optimized(hours: list[Hour], scenario: BatteryScenario) -> Run:
    """Planned with the prices known in advance (as Sunflow would with a perfect forecast).

    Each window of two days is optimized and its first day kept, starting where the last ended.
    """
    eta_c, eta_d = _efficiencies(scenario)
    capacity, floor = _bounds(scenario)
    soc = floor
    run = Run()
    settings = Settings(wear_ct_per_kwh=scenario.wear_ct_per_kwh(), allow_grid_charge=scenario.grid_charge)
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


def simulate(hours: list[Hour], scenarios: list[BatteryScenario]) -> dict[str, Any]:
    """Measured operation and, per scenario, both ways of running the battery."""
    measured = Run()
    for hour in hours:
        measured.add(hour.imported, hour.exported, hour)
    days = len(hours) / 24
    year = 365 / days if days else 0.0
    results = []
    for scenario in scenarios:
        runs = {"self_consumption": self_consumption(hours, scenario), "optimized": optimized(hours, scenario)}
        usable = scenario.capacity_kwh * scenario.usable_pct / 100
        entry: dict[str, Any] = {"id": scenario.id, "name": scenario.name, "mode": scenario.mode}
        for key, run in runs.items():
            saving = measured.cost - run.cost
            entry[key] = run.as_dict() | {
                "saving": saving,
                "saving_per_year": saving * year,
                "cycles_per_year": run.discharged_wh / 1000 / usable * year if usable else None,
                "payback_years": scenario.price_eur / (saving * year) if scenario.price_eur and saving > 0 else None,
            }
        results.append(entry)
    return {
        "days": days,
        "start": hours[0].start.isoformat() if hours else None,
        "end": (hours[-1].start + timedelta(hours=1)).isoformat() if hours else None,
        "measured": measured.as_dict(),
        "scenarios": results,
    }
