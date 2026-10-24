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
