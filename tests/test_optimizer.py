"""Tests for the optimizer, load forecast and plan inputs."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sunflow.api import plan_response
from sunflow.config_store import SunflowConfig
from sunflow.load_forecast import build_load_forecast
from sunflow.optimizer import BatterySpec, Plan, PlanSlot, Settings, SlotInput, choose_action, optimize, smooth_actions
from sunflow.plan import PlanResult, build_inputs, daily_needs
from sunflow.sources import ForecastPoint, PricePoint

BERLIN = ZoneInfo("Europe/Berlin")
START = datetime(2026, 10, 26, 22, 0, tzinfo=BERLIN)
SLOT = timedelta(minutes=15)

BATTERY = BatterySpec(
    capacity_wh=20000, soc_wh=2000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000
)


def _slots(hours: int, price: callable, pv: callable = lambda t: 0.0, load: float = 500) -> list[SlotInput]:
    return [
        SlotInput(start=START + i * SLOT, pv_w=pv(i), load_w=load, price=price(i), feed_in=0.08)
        for i in range(hours * 4)
    ]


def test_cheap_night_charging() -> None:
    """With a cheap window (Octopus Go style) the battery charges from the grid and is used later."""
    # 22-24 h expensive, 0-5 h cheap, then expensive again
    inputs = _slots(24, price=lambda i: 0.226 if 8 <= i < 28 else 0.326)

    plan = optimize(inputs, BATTERY, Settings(wear_ct_per_kwh=3))

    cheap_actions = {s.action for s in plan.slots[8:28]}
    assert "force_charge" in cheap_actions
    assert max(s.soc_wh for s in plan.slots) > BATTERY.soc_wh + 5000
    assert plan.savings > 0
    # Never below the minimum
    assert min(s.soc_wh for s in plan.slots) >= BATTERY.min_soc_wh - 1


def test_no_grid_charge_when_disabled() -> None:
    """Without permission and without PV the battery is not charged."""
    inputs = _slots(24, price=lambda i: 0.226 if 8 <= i < 28 else 0.326)

    plan = optimize(inputs, BATTERY, Settings(allow_grid_charge=False))

    assert all(s.charge_w < 1 for s in plan.slots)
    assert all(s.action != "force_charge" for s in plan.slots)


def test_wear_prevents_small_spreads() -> None:
    """A spread smaller than wear and losses is not worth a cycle."""
    inputs = _slots(24, price=lambda i: 0.30 if 8 <= i < 28 else 0.31)

    plan = optimize(inputs, BATTERY, Settings(wear_ct_per_kwh=3))

    assert all(s.action != "force_charge" for s in plan.slots)


def test_pv_surplus_is_self_consumption() -> None:
    """Charging from PV surplus needs no explicit command."""
    inputs = _slots(4, price=lambda i: 0.30, pv=lambda i: 3000.0)

    plan = optimize(inputs, BATTERY, Settings())

    assert plan.status
    assert {s.action for s in plan.slots} == {"auto"}
    assert plan.slots[-1].soc_wh > BATTERY.soc_wh


@pytest.mark.parametrize(
    ("pv", "load", "charge", "discharge", "expected"),
    [
        pytest.param(3000, 500, 2500, 0, ("auto", None), id="charge_from_surplus"),
        pytest.param(0, 500, 3000, 0, ("force_charge", 3000), id="grid_charge"),
        pytest.param(0, 500, 0, 500, ("auto", None), id="cover_deficit"),
        pytest.param(0, 500, 0, 0, ("hold", None), id="save_for_later"),
        pytest.param(0, 500, 0, 3000, ("force_discharge", 3000), id="export_from_battery"),
        pytest.param(0, 2000, 0, 800, ("limit_discharge", 800), id="cover_part_of_deficit"),
        pytest.param(4000, 500, 1000, 0, ("limit_charge", 1000), id="charge_less_and_export"),
    ],
)
def test_choose_action(pv: float, load: float, charge: float, discharge: float, expected: tuple) -> None:
    """Optimized flows map to the minimal control action."""
    assert choose_action(pv, load, charge, discharge, 100) == expected


def test_choose_action_full_battery() -> None:
    """A full battery exporting the surplus is plain self-consumption."""
    assert choose_action(4000, 500, 0, 0, 100, full=True) == ("auto", None)


def test_load_forecast() -> None:
    """Same day type wins, other hours fall back, no history gives the default."""
    monday_18 = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)  # 18:00 local (CEST)
    history = [(monday_18 + timedelta(days=d), 1000.0 + d) for d in (0, 1, 2)]  # Mon-Wed
    history.append((datetime(2026, 10, 24, 16, 0, tzinfo=UTC), 3000.0))  # Saturday 18:00 local

    start = datetime(2026, 10, 26, 18, 0, tzinfo=BERLIN)  # Monday
    forecast = build_load_forecast(history, start, 1, BERLIN)
    assert forecast.points[0].power == pytest.approx(1001)  # weekday mean, not the Saturday
    assert forecast.history_hours == 4

    saturday = datetime(2026, 10, 31, 18, 0, tzinfo=BERLIN)
    # Only one Saturday sample: falls back to the mean of all days at 18:00
    assert build_load_forecast(history, saturday, 1, BERLIN).points[0].power == pytest.approx(1500.75)

    empty = build_load_forecast([], start, 1, BERLIN)
    assert empty.quality == "default"
    assert len(empty.points) == 4


def test_build_inputs_stops_with_prices() -> None:
    """The horizon ends where prices end; PV is resampled to 15 minutes."""
    prices = [PricePoint(start=START, end=START + timedelta(hours=2), price=0.3)]
    pv = [ForecastPoint(start=START, end=START + timedelta(hours=1), power=1200)]
    load = build_load_forecast([], START, 48, BERLIN)

    inputs = build_inputs(START, prices, pv, load, 0.08)

    assert len(inputs) == 8
    assert [i.pv_w for i in inputs[:5]] == [1200, 1200, 1200, 1200, 0]


def _slot(action: str) -> PlanSlot:
    return PlanSlot(
        start=START, end=START + SLOT, pv_w=0, load_w=300, price=0.2, feed_in=0.08, charge_w=0, discharge_w=0,
        import_w=300, export_w=0, soc_wh=5000, action=action, action_power_w=None,
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param("HHAHHAAHHHH", "HHHHHHHHHHH", id="blips_bridged"),
        pytest.param("HHAAAHH", "HHAAAHH", id="long_enough_kept"),
        pytest.param("HHFHH", "HHFHH", id="force_untouched"),
        pytest.param("AHHF", "AHHF", id="different_neighbours"),
    ],
)
def test_smooth_actions(before: str, after: str) -> None:
    """Short auto/hold segments between equal neighbours are bridged."""
    names = {"H": "hold", "A": "auto", "F": "force_charge"}
    slots = [_slot(names[c]) for c in before]

    smooth_actions(slots)

    assert "".join({v: k for k, v in names.items()}[s.action] for s in slots) == after


def test_backup_reserve_is_kept() -> None:
    """With a backup reserve the battery is never drained below it."""
    battery = BatterySpec(capacity_wh=20000, soc_wh=15000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000)
    inputs = _slots(24, price=lambda i: 0.35)

    plan = optimize(inputs, battery, Settings(reserve_wh=6000))

    assert min(s.soc_wh for s in plan.slots) >= 6000 - 1
    assert min(s.soc_wh for s in optimize(inputs, battery, Settings()).slots) < 6000


def test_pv_absorption_exports_less() -> None:
    """Preferring PV absorption keeps room in the battery: less is exported."""
    battery = BatterySpec(capacity_wh=10000, soc_wh=1000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    # Night 22-06 cheaper than the feed-in tariff: plain cost optimization fills the battery
    # from the grid and exports the PV surplus of the following day instead
    def price(i: int) -> float:
        return 0.05 if i < 32 else 0.40

    def pv(i: int) -> float:
        return 1500.0 if 40 <= i < 72 else 0.0

    inputs = _slots(30, price=price, pv=pv)
    exported = lambda plan: sum(s.export_w for s in plan.slots)  # noqa: E731

    balanced = optimize(inputs, battery, Settings(wear_ct_per_kwh=1))
    absorbing = optimize(inputs, battery, Settings(wear_ct_per_kwh=1, export_penalty_ct_per_kwh=5))

    assert exported(absorbing) < exported(balanced)


def test_import_cheaper_than_feed_in() -> None:
    """Import below the feed-in tariff must not lead to unbounded grid pass-through."""
    inputs = _slots(8, price=lambda i: 0.05, pv=lambda i: 2000.0)

    plan = optimize(inputs, BATTERY, Settings())

    assert plan.slots, plan.status
    assert all(min(s.import_w, s.export_w) < 1 for s in plan.slots)


def test_negative_prices_do_not_burn_energy() -> None:
    """At negative prices the battery is not charged and discharged at the same time."""
    inputs = _slots(8, price=lambda i: -0.10)

    plan = optimize(inputs, BATTERY, Settings())

    assert plan.slots, plan.status
    assert all(min(s.charge_w, s.discharge_w) < 1 for s in plan.slots)


def test_load_forecast_excludes_large_loads_and_scales() -> None:
    """A one-off EV charge is left out; the daily total follows the configured consumption."""
    day = datetime(2026, 10, 24, 0, 0, tzinfo=BERLIN)
    history = [(day + timedelta(hours=h), 400.0) for h in range(24)]
    history[1] = (history[1][0], 10900.0)  # EV charging 01:00-02:00

    start = datetime(2026, 10, 27, 0, 0, tzinfo=BERLIN)
    plain = build_load_forecast(history, start, 24, BERLIN)
    filtered = build_load_forecast(history, start, 24, BERLIN, large_load_w=3500)
    scaled = build_load_forecast(history, start, 24, BERLIN, large_load_w=3500, daily_kwh=14)

    def daily_kwh(forecast: object) -> float:
        return sum(p.power for p in forecast.points) / 4 / 1000

    assert daily_kwh(plain) == pytest.approx(23 * 0.4 + 10.9)
    assert filtered.excluded_hours == 1
    assert filtered.points[4].power == pytest.approx(400)  # 01:00 falls back to the other hours
    assert daily_kwh(filtered) == pytest.approx(9.6)
    assert daily_kwh(scaled) == pytest.approx(14)
    assert scaled.scaled_to_kwh == 14


def test_load_forecast_typical_day_without_history() -> None:
    """Without history the typical household day is scaled to the configured consumption."""
    forecast = build_load_forecast([], START, 24, BERLIN, daily_kwh=14)

    assert sum(p.power for p in forecast.points) / 4 / 1000 == pytest.approx(14)
    evening = max(forecast.points, key=lambda p: p.power)
    assert evening.start.astimezone(BERLIN).hour in (18, 19)


def test_subtract_loadpoints() -> None:
    """Measured loadpoint power is removed from the household load where available."""
    from sunflow.load_forecast import subtract_loads

    t0 = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    hour = timedelta(hours=1)
    history = [(t0, 11300.0), (t0 + hour, 400.0), (t0 + 2 * hour, 9000.0)]
    wallbox = [(t0, 10900.0), (t0 + hour, 0.0)]
    heater = [(t0, 100.0)]

    base, separated = subtract_loads(history, [wallbox, heater])

    assert base == [(t0, 300.0), (t0 + hour, 400.0), (t0 + 2 * hour, 9000.0)]
    assert separated == 2


def test_ev_charges_in_cheap_window() -> None:
    """A plugged-in EV gets its energy in the cheap slots; the battery is not used for it."""
    from sunflow.optimizer import FlexLoad

    inputs = _slots(10, price=lambda i: 0.226 if 8 <= i < 28 else 0.326)
    ev = FlexLoad(name="Auto", energy_wh=10000, max_power_w=11000, available=[True] * 40)

    plan = optimize(inputs, BATTERY, Settings(), [ev])

    (result,) = plan.flex
    assert result.planned_wh == pytest.approx(10000)
    assert result.unmet_wh == pytest.approx(0, abs=1)
    charged_cheap = sum(s.flex_w["Auto"] for s in plan.slots[8:28]) / 4
    assert charged_cheap == pytest.approx(10000)
    # The battery does not cover the EV during the cheap window
    assert all(s.discharge_w < s.load_w + 1 for s in plan.slots[8:28])


def test_flex_unmet_when_not_available() -> None:
    """A load that cannot run leaves its need unmet but the plan stays solvable."""
    from sunflow.optimizer import FlexLoad

    inputs = _slots(4, price=lambda i: 0.3)
    ev = FlexLoad(name="Auto", energy_wh=5000, max_power_w=11000, available=[False] * 16)

    plan = optimize(inputs, BATTERY, Settings(), [ev])

    assert plan.slots
    assert plan.flex[0].unmet_wh == pytest.approx(5000)


def test_heater_uses_pv_surplus() -> None:
    """A heater with a whole day to go runs in the PV surplus, not from the grid."""
    from sunflow.optimizer import FlexLoad

    full = BatterySpec(capacity_wh=10000, soc_wh=10000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    inputs = _slots(24, price=lambda i: 0.30, pv=lambda i: 4000.0 if 40 <= i < 64 else 0.0)
    heater = FlexLoad(name="Warmwasser", energy_wh=5600, max_power_w=3000, available=[True] * 96)

    plan = optimize(inputs, full, Settings(), [heater])

    assert sum(s.flex_w["Warmwasser"] for s in plan.slots[40:64]) / 4 == pytest.approx(5600, abs=10)
    assert plan.savings > 0


def test_surplus_export_is_smooth() -> None:
    """With a full battery and a heater, the PV surplus is not split into a zigzag."""
    from sunflow.optimizer import FlexLoad

    full = BatterySpec(capacity_wh=10000, soc_wh=9900, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    pv = [0.0] * 36 + [2500.0] * 24 + [0.0] * 36
    inputs = _slots(24, price=lambda i: 0.30, pv=lambda i: pv[i])
    heater = FlexLoad(name="Warmwasser", energy_wh=2000, max_power_w=3000, available=[True] * 96)

    plan = optimize(inputs, full, Settings(), [heater])

    export = [s.export_w for s in plan.slots]
    steps = [b - a for a, b in zip(export, export[1:], strict=False) if abs(b - a) > 50]
    reversals = sum(1 for a, b in zip(steps, steps[1:], strict=False) if (a > 0) != (b > 0))
    # Export rises while the battery fills and falls once in the evening: no zigzag
    assert reversals <= 1


def _heater(**options: object) -> object:
    from sunflow.optimizer import FlexLoad

    return FlexLoad(name="Last", energy_wh=4000, max_power_w=2000, available=[True] * 96, **options)


def test_pv_only_never_uses_grid() -> None:
    """A PV-only load runs only in the surplus; the rest stays unmet."""
    pv = [0.0] * 40 + [1500.0] * 8 + [0.0] * 48  # 2 h with 1 kW surplus = 2 kWh
    plan = optimize(_slots(24, price=lambda i: 0.10, pv=lambda i: pv[i]), BATTERY, Settings(), [_heater(pv_only=True)])

    assert all(s.flex_w["Last"] <= max(s.pv_w - s.load_w, 0) + 1 for s in plan.slots)
    assert plan.flex[0].planned_wh == pytest.approx(2000, abs=5)
    assert plan.flex[0].unmet_wh == pytest.approx(2000, abs=5)


def test_urgency_runs_early() -> None:
    """Fast charging starts right away even though later slots are cheaper."""
    plan = optimize(
        _slots(12, price=lambda i: 0.35 if i < 24 else 0.20), BATTERY, Settings(), [_heater(urgency=1.0)]
    )

    assert sum(s.flex_w["Last"] for s in plan.slots[:8]) / 4 == pytest.approx(4000, abs=5)


def test_alternative_heat_is_used_when_cheaper() -> None:
    """With district heating at 12 ct/kWh, grid power at 30 ct is not used for heating."""
    full = BatterySpec(capacity_wh=10000, soc_wh=10000, min_soc_wh=10000, max_charge_w=5000, max_discharge_w=5000)
    pv = [0.0] * 40 + [1500.0] * 8 + [0.0] * 48
    plan = optimize(
        _slots(24, price=lambda i: 0.30, pv=lambda i: pv[i]), full, Settings(allow_grid_charge=False),
        [_heater(unmet_cost=0.12)],
    )

    # With a full battery the surplus would be exported at 8 ct: heating with it beats 12 ct
    assert plan.flex[0].planned_wh == pytest.approx(2000, abs=50)
    assert plan.flex[0].unmet_wh == pytest.approx(2000, abs=50)


def test_battery_not_used_for_load() -> None:
    """Without permission the home battery does not discharge into the load."""
    full = BatterySpec(capacity_wh=20000, soc_wh=18000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000)
    inputs = _slots(4, price=lambda i: 0.35)

    allowed = optimize(inputs, full, Settings(), [_heater()])
    denied = optimize(inputs, full, Settings(), [_heater(uses_battery=False)])

    assert sum(s.import_w for s in allowed.slots) < 100
    assert all(s.discharge_w <= s.load_w + 1 for s in denied.slots)
    assert sum(s.import_w for s in denied.slots) / 4 == pytest.approx(4000, abs=10)


@pytest.mark.parametrize(
    ("after", "hhmm", "expected"),
    [
        pytest.param(datetime(2026, 10, 24, 22, 0, tzinfo=BERLIN), "07:00", datetime(2026, 10, 25, 7, 0, tzinfo=BERLIN), id="next_morning_over_dst"),
        pytest.param(datetime(2026, 10, 26, 6, 0, tzinfo=BERLIN), "07:00", datetime(2026, 10, 26, 7, 0, tzinfo=BERLIN), id="same_day"),
        pytest.param(datetime(2026, 10, 26, 7, 0, tzinfo=BERLIN), "07:00", datetime(2026, 10, 27, 7, 0, tzinfo=BERLIN), id="exactly_now"),
    ],
)
def test_next_time(after: datetime, hhmm: str, expected: datetime) -> None:
    """Daily ready-by times resolve to the next local wall-clock time."""
    from sunflow.plan import next_time

    result = next_time(hhmm, after, BERLIN)
    assert result == expected
    assert result.astimezone(BERLIN).hour == 7


def test_round_trip_efficiency_parameter(library: object) -> None:
    """Battery round trip is split per direction; the inverter adds to grid charging and discharging."""
    from types import SimpleNamespace

    from sunflow.config_store import DeviceConfig
    from sunflow.plan import battery_spec

    device = DeviceConfig(
        ha_device_id="d_inv", profile="solax_modbus/sofar", profile_version=1, name="Sofar",
        parameters={"capacity_wh": 20000, "efficiency_pct": 93.7, "inverter_efficiency_pct": 96.5},
    )
    snapshot = SimpleNamespace(
        devices=[{"id": "d_inv"}],
        entities=[{"entity_id": "sensor.soc", "unique_id": "x_battery_capacity_total", "device_id": "d_inv"}],
        states={"sensor.soc": {"state": "50", "attributes": {"unit_of_measurement": "%"}}},
    )

    spec, problem = battery_spec(library.profiles["solax_modbus/sofar"], device, snapshot)

    assert problem is None
    # PV -> battery -> house: battery round trip and one inverter pass
    assert spec.efficiency_charge * spec.efficiency_discharge == pytest.approx(0.937 * 0.965)
    # Grid -> battery -> house: two inverter passes
    assert spec.efficiency_charge_grid * spec.efficiency_discharge == pytest.approx(0.937 * 0.965**2)


def test_battery_fills_before_export() -> None:
    """With more PV surplus than room, the battery is charged first and the rest exported later."""
    battery = BatterySpec(capacity_wh=10000, soc_wh=5000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    pv = [0.0] * 32 + [2500.0] * 32 + [0.0] * 32  # 8 h with 2 kW surplus = 16 kWh, room 5 kWh
    plan = optimize(_slots(24, price=lambda i: 0.326, pv=lambda i: pv[i]), battery, Settings())

    first_hours = plan.slots[32:40]
    assert sum(s.export_w for s in first_hours) < 50
    assert all(s.charge_w > 1900 for s in first_hours)
    assert max(s.soc_wh for s in plan.slots) == pytest.approx(10000, abs=10)


def test_priority_between_loads() -> None:
    """With limited PV surplus the higher-priority load gets the energy."""
    full = BatterySpec(capacity_wh=10000, soc_wh=10000, min_soc_wh=10000, max_charge_w=5000, max_discharge_w=5000)
    pv = [0.0] * 40 + [1500.0] * 8 + [0.0] * 48  # 2 kWh surplus
    inputs = _slots(24, price=lambda i: 0.30, pv=lambda i: pv[i])
    from sunflow.optimizer import FlexLoad

    def load(name: str, bonus: float) -> FlexLoad:
        return FlexLoad(name=name, energy_wh=2000, max_power_w=2000, available=[True] * 96, pv_only=True, priority_bonus=bonus)

    plan = optimize(inputs, full, Settings(), [load("A", 0.0), load("B", 0.001)])

    results = {r.name: r.planned_wh for r in plan.flex}
    assert results["B"] == pytest.approx(2000, abs=5)
    assert results["A"] == pytest.approx(0, abs=5)


@pytest.mark.parametrize(("below_battery", "load_gets"), [(False, 2000), (True, 0)])
def test_battery_position_in_priorities(below_battery: bool, load_gets: float) -> None:
    """A PV-only load ranked below the battery only gets surplus the battery cannot take."""
    from sunflow.optimizer import FlexLoad

    room = BatterySpec(capacity_wh=10000, soc_wh=5000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    pv = [0.0] * 40 + [1500.0] * 8 + [0.0] * 48  # 2 kWh surplus, battery has room for it
    plan = optimize(
        _slots(24, price=lambda i: 0.30, pv=lambda i: pv[i]), room, Settings(),
        [FlexLoad(name="E-Bike", energy_wh=2000, max_power_w=2000, available=[True] * 96, pv_only=True, below_battery=below_battery)],
    )

    assert plan.flex[0].planned_wh == pytest.approx(load_gets, abs=10)


@pytest.mark.parametrize(
    ("reserve_wh", "expected"),
    [
        pytest.param(0.0, 0.0, id="no-reserve"),
        pytest.param(6000.0, 6000.0, id="backup-reserve"),
    ],
)
def test_plan_response_battery_limits(reserve_wh: float, expected: float) -> None:
    """The plan reports capacity, discharge limit and reserve for the SoC chart."""

    battery = BatterySpec(capacity_wh=20000, soc_wh=10000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000)
    result = PlanResult(plan=Plan(status="optimal"), battery=battery, reserve_wh=reserve_wh)
    assert plan_response(result, SunflowConfig())["battery"] == {
        "capacity_wh": 20000,
        "min_soc_wh": 2000,
        "reserve_wh": expected,
    }


def test_heater_reheats_every_day() -> None:
    """A heater heated on day one still uses the PV surplus of day two for that day's need."""
    from sunflow.optimizer import FlexLoad

    full = BatterySpec(capacity_wh=10000, soc_wh=10000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    sunny = lambda i: 4000.0 if 40 <= i % 96 < 64 else 0.0  # noqa: E731
    inputs = _slots(48, price=lambda i: 0.30, pv=sunny)
    heater = FlexLoad(
        name="Warmwasser", energy_wh=5000, max_power_w=3000, available=[True] * 192, later_needs=[(96, 192, 3500)]
    )

    plan = optimize(inputs, full, Settings(), [heater])

    assert sum(s.flex_w["Warmwasser"] for s in plan.slots[:96]) / 4 == pytest.approx(5000, abs=10)
    assert sum(s.flex_w["Warmwasser"] for s in plan.slots[136:160]) / 4 == pytest.approx(3500, abs=10)
    assert plan.flex[0].energy_wh == 8500
    assert plan.flex[0].unmet_wh == pytest.approx(0, abs=1)


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        pytest.param(48, [(96, 192, 3500)], id="second-day-fits"),
        pytest.param(40, [], id="second-day-cut-off"),
        pytest.param(72, [(96, 192, 3500), (192, 288, 3500)], id="two-more-days"),
    ],
)
def test_daily_needs(hours: int, expected: list[tuple[int, int, float]]) -> None:
    """Recurring needs only cover whole 24 h windows inside the horizon."""
    starts = [START + i * SLOT for i in range(hours * 4)]
    assert daily_needs(starts, START + timedelta(hours=24), 3500) == expected


def test_gate_waits_for_battery_level() -> None:
    """A load released at 80 % battery only takes PV once the battery got there."""
    from sunflow.optimizer import FlexLoad

    battery = BatterySpec(capacity_wh=10000, soc_wh=2000, min_soc_wh=1000, max_charge_w=3000, max_discharge_w=3000)
    inputs = _slots(24, price=lambda i: 0.30, pv=lambda i: 5000.0 if 32 <= i < 72 else 0.0)
    heater = FlexLoad(
        name="Warmwasser", energy_wh=4000, max_power_w=3000, available=[True] * 96,
        unmet_cost=0.12, gate_battery_wh=8000,
    )

    plan = optimize(inputs, battery, Settings(), [heater])

    running = [t for t, s in enumerate(plan.slots) if s.flex_w["Warmwasser"] > 10]
    assert running
    assert all(plan.slots[t - 1].soc_wh >= 8000 - 1 for t in running)
    assert all(plan.slots[t].import_w >= plan.slots[t].flex_w["Warmwasser"] - 1 for t in running if t < 40)


def test_gate_waits_for_vehicle() -> None:
    """A load released after the vehicle got its energy runs after the car in the PV surplus."""
    from sunflow.optimizer import FlexLoad

    full = BatterySpec(capacity_wh=10000, soc_wh=10000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    inputs = _slots(24, price=lambda i: 0.30, pv=lambda i: 3500.0 if 40 <= i < 72 else 0.0)
    car = FlexLoad(name="Auto", energy_wh=6000, max_power_w=3000, available=[True] * 96, unmet_cost=0.12)
    heater = FlexLoad(
        name="Warmwasser", energy_wh=3000, max_power_w=3000, available=[True] * 96, unmet_cost=0.12,
        priority_bonus=0.01, gate_after=[("Auto", 6000)],
    )

    plan = optimize(inputs, full, Settings(), [car, heater])

    car_done = next(t for t in range(96) if sum(s.flex_w["Auto"] for s in plan.slots[: t + 1]) / 4 >= 6000 - 1)
    assert all(s.flex_w["Warmwasser"] < 10 for s in plan.slots[: car_done + 1])
    assert plan.flex[1].planned_wh > 1000


@pytest.mark.parametrize(
    ("conditions", "strategy", "battery_wh", "after"),
    [
        pytest.param([("battery", 80.0)], "cost", 16000.0, [], id="battery"),
        pytest.param([("ev", 90.0)], "cost", None, [("Auto", 20 * 400.0)], id="vehicle"),
        pytest.param([("ev", 95.0)], "cost", None, [("Auto", 20 * 400.0)], id="vehicle-above-target"),
        pytest.param([("battery", 80.0), ("ev", 90.0)], "fast", None, [], id="fast-ignores"),
    ],
)
def test_with_conditions(
    conditions: list[tuple[str, float]], strategy: str, battery_wh: float | None, after: list[tuple[str, float]]
) -> None:
    """Priority conditions become gates on the planned loads."""
    from sunflow.config_store import DeviceConfig, FlexSettings, PriorityCondition
    from sunflow.optimizer import FlexLoad
    from sunflow.plan import with_conditions

    devices = [
        DeviceConfig(id="ev", ha_device_id="x", profile="evcc_intg/loadpoint", profile_version=1, name="Auto"),
        DeviceConfig(
            id="ww", ha_device_id="y", profile="evcc_intg/loadpoint", profile_version=1, name="Warmwasser",
            flex=FlexSettings(strategy=strategy),
        ),
    ]
    config = SunflowConfig(
        devices=devices,
        priorities=["battery", "ev", "ww"],
        priority_conditions={"ww": [PriorityCondition(device_id=d, min_pct=p) for d, p in conditions]},
    )
    loads = {
        "ev": FlexLoad(name="Auto", energy_wh=10000, max_power_w=11000, available=[True] * 4),
        "ww": FlexLoad(name="Warmwasser", energy_wh=3000, max_power_w=2800, available=[True] * 4),
    }
    # Car at 70 %, target 90 %, 400 Wh per %
    result = with_conditions(config, loads, {"ev": (70.0, 90.0, 400.0)}, 20000.0, START)

    assert result["ww"].gate_battery_wh == battery_wh
    assert result["ww"].gate_after == after
    assert not result["ev"].gated


def test_battery_gate_waits_for_vehicle() -> None:
    """A battery ranked below the car only charges from PV once the car got its energy."""
    from sunflow.optimizer import FlexLoad

    battery = BatterySpec(capacity_wh=10000, soc_wh=1000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    inputs = _slots(24, price=lambda i: 0.30, pv=lambda i: 4000.0 if 40 <= i < 72 else 0.0)
    car = FlexLoad(name="Auto", energy_wh=6000, max_power_w=3000, available=[True] * 96, unmet_cost=0.12)

    plan = optimize(inputs, battery, Settings(battery_gate_after=(("Auto", 6000.0),)), [car])

    car_done = next(t for t in range(96) if sum(s.flex_w["Auto"] for s in plan.slots[: t + 1]) / 4 >= 6000 - 1)
    assert all(s.charge_w <= s.import_w + 1 for s in plan.slots[: car_done + 1])
    assert max(s.soc_wh for s in plan.slots) > 3000


def test_with_conditions_hot_water_tank() -> None:
    """The car can wait for the hot water tank (°C); the battery for both."""
    from sunflow.config_store import DeviceConfig, PriorityCondition
    from sunflow.optimizer import FlexLoad
    from sunflow.plan import storage_gates, with_conditions

    config = SunflowConfig(
        devices=[
            DeviceConfig(id="ww", ha_device_id="y", profile="evcc_intg/loadpoint", profile_version=1, name="Warmwasser"),
            DeviceConfig(id="ev", ha_device_id="x", profile="evcc_intg/loadpoint", profile_version=1, name="Auto"),
        ],
        priorities=["ww", "ev", "battery"],
        priority_conditions={
            "ev": [PriorityCondition(device_id="ww", min_pct=55)],
            "battery": [PriorityCondition(device_id="ww", min_pct=60), PriorityCondition(device_id="ev", min_pct=80)],
        },
    )
    loads = {
        "ww": FlexLoad(name="Warmwasser", energy_wh=5000, max_power_w=2800, available=[True] * 4),
        "ev": FlexLoad(name="Auto", energy_wh=10000, max_power_w=11000, available=[True] * 4),
    }
    # Tank at 45 °C of 60 °C with 349 Wh per K; car at 70 % of 90 % with 400 Wh per %
    storages = {"ww": (45.0, 60.0, 349.0), "ev": (70.0, 90.0, 400.0)}

    assert with_conditions(config, loads, storages, 20000.0, START)["ev"].gate_after == [("Warmwasser", 10 * 349.0)]
    assert storage_gates(config, "battery", loads, storages) == (("Warmwasser", 15 * 349.0), ("Auto", 10 * 400.0))


def test_baseline_runs_optional_loads_on_pv_only() -> None:
    """Without Sunflow a heater with district heating only uses surplus; heating covers the rest."""
    from sunflow.optimizer import FlexLoad

    full = BatterySpec(capacity_wh=10000, soc_wh=10000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    inputs = _slots(24, price=lambda i: 0.30, pv=lambda i: 1500.0 if 40 <= i < 48 else 0.0)
    heater = FlexLoad(name="Warmwasser", energy_wh=5000, max_power_w=3000, available=[True] * 96, unmet_cost=0.12)

    plan = optimize(inputs, full, Settings(), [heater])

    # 2 h with 1 kW surplus: 2 kWh from PV, 3 kWh by district heating at 12 ct
    assert plan.baseline_alternative_cost == pytest.approx(3 * 0.12, abs=0.01)
    assert plan.alternative_cost == pytest.approx(plan.flex[0].unmet_wh / 1000 * 0.12)
    assert plan.savings >= -0.01


def test_load_forecast_scales_each_day() -> None:
    """With a daily total every calendar day adds up to it, also when day types differ."""
    monday = datetime(2026, 10, 19, 0, 0, tzinfo=BERLIN)
    # Weekdays use 400 W, the weekend 800 W: day types with different shapes
    history = [
        ((monday + timedelta(days=d, hours=h)).astimezone(UTC), 800.0 if d % 7 >= 5 else 400.0)
        for d in range(14)
        for h in range(24)
    ]
    start = datetime(2026, 10, 30, 12, 0, tzinfo=BERLIN)  # Friday noon, horizon into the weekend

    forecast = build_load_forecast(history, start, 48, BERLIN, daily_kwh=14)

    saturday = [p.power for p in forecast.points if p.start.date() == datetime(2026, 10, 31).date()]
    assert sum(saturday) / 4 == pytest.approx(14000, rel=0.01)


def test_grid_charging_needs_more_spread_than_pv_charging() -> None:
    """With inverter losses on grid charging, a spread that pays for PV storage does not pay from the grid."""
    battery = BatterySpec(
        capacity_wh=10000, soc_wh=1000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000,
        efficiency_charge=0.97, efficiency_charge_grid=0.97 * 0.9, efficiency_discharge=0.97 * 0.9,
    )
    # Cheap 0.25 at night, 0.30 in the evening: 0.25 / (0.97 * 0.9 * 0.97 * 0.9) = 0.33 > 0.30
    inputs = _slots(24, price=lambda i: 0.25 if i < 20 else 0.30)

    plan = optimize(inputs, battery, Settings(wear_ct_per_kwh=0))

    assert max(s.charge_w for s in plan.slots) < 1


async def test_last_valid_value_skips_unknown() -> None:
    """The newest numeric state wins; "unknown" entries are skipped."""
    from sunflow.plan import last_valid_value

    class Client:
        async def command(self, msg_type: str, **data: object) -> dict:
            return {"sensor.soc": [{"s": "11", "lu": 1000.0}, {"s": "10", "lu": 2000.0}, {"s": "unknown", "lu": 3000.0}]}

    value, since = await last_valid_value(Client(), "sensor.soc", timedelta(hours=6))

    assert value == 10
    assert since == datetime.fromtimestamp(2000, UTC)
    assert await last_valid_value(Client(), None, timedelta(hours=6)) is None
