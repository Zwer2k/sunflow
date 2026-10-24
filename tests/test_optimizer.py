"""Tests for the optimizer, load forecast and plan inputs."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sunflow.load_forecast import build_load_forecast
from sunflow.optimizer import BatterySpec, PlanSlot, Settings, SlotInput, choose_action, optimize, smooth_actions
from sunflow.plan import build_inputs
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
    ],
)
def test_choose_action(pv: float, load: float, charge: float, discharge: float, expected: tuple) -> None:
    """Optimized flows map to the minimal control action."""
    assert choose_action(pv, load, charge, discharge, 100) == expected


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
