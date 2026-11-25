"""The optimizer's thermal load: a heat pump planned with the house as a heat storage."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sunflow.planning.optimizer import (
    BatterySpec,
    FlexLoad,
    SlotInput,
    ThermalLoad,
    optimize,
)

from house_models import house_model

START = datetime(2026, 11, 16, tzinfo=UTC)
SLOT = timedelta(minutes=15)
# Nearly empty and small: the house is the storage that matters here
BATTERY = BatterySpec(capacity_wh=1000, soc_wh=100, min_soc_wh=100, max_charge_w=100, max_discharge_w=100)
# House τ 50 h, 25 kWh/K, a little internal gain; a fast surface
P = house_model().params


def _slots(hours: int, price: list[float], load: float = 2000.0) -> list[SlotInput]:
    return [
        SlotInput(start=START + i * SLOT, pv_w=0.0, load_w=load, price=price[i // 4], feed_in=0.08)
        for i in range(hours * 4)
    ]


def _house(n: int, inside: float = 21.0, outside: float = 0.0) -> ThermalLoad:
    # What holding 20 °C draws: u·ΔT - gains kW heat at COP 3; the surface as warm as that heat needs
    heat = P.u * (20.0 - outside) - P.g
    base = heat / 3.0 * 1000
    return ThermalLoad(
        name="hp", u=P.u, kf=P.kf, cs=P.cs, cb=P.cb, gains=P.g, inside=inside, surface=inside + heat / P.kf,
        outside=[outside] * n, cop=[3.0] * n,
        lower=[20.0] * n, upper=[22.0] * n, max_power_w=3000, baseline_w=[base] * n, cold_cost=50.0, warm_cost=0.2,
    )


def test_preheats_before_dear_hours() -> None:
    """Cheap first, dear later: the house is heated ahead and coasts through the dear hours."""
    prices = [0.10] * 4 + [0.50] * 4
    inputs = _slots(8, prices)
    plan = optimize(inputs, BATTERY, thermal=[_house(len(inputs))])

    cheap = sum(s.heat_w["hp"] for s in plan.slots[:16])
    dear = sum(s.heat_w["hp"] for s in plan.slots[16:])
    assert cheap > 3 * dear
    assert max(s.room_c["hp"] for s in plan.slots) <= 22.0 + 1e-6
    assert min(s.room_c["hp"] for s in plan.slots) >= 20.0 - 1e-6
    assert plan.cost < plan.baseline_cost


def test_keeps_the_lower_bound() -> None:
    """Flat prices: it heats just enough to hold the band, no more."""
    inputs = _slots(12, [0.30] * 12)
    plan = optimize(inputs, BATTERY, thermal=[_house(len(inputs), inside=20.0)])

    assert min(s.room_c["hp"] for s in plan.slots) >= 20.0 - 1e-6
    assert max(s.room_c["hp"] for s in plan.slots) <= 20.5
    assert sum(s.heat_w["hp"] for s in plan.slots) > 0


def test_end_warmth_counts_only_up_to_the_band() -> None:
    """Cheap hours at the end of the plan do not heat the house beyond the upper bound."""
    prices = [0.30] * 6 + [0.10] * 2
    inputs = _slots(8, prices)
    house = replace(_house(len(inputs), outside=10.0), max_power_w=8000)

    plan = optimize(inputs, BATTERY, thermal=[house])

    assert max(s.room_c["hp"] for s in plan.slots) <= 22.0 + 1e-6


def test_battery_may_serve_the_heat_pump() -> None:
    """A load kept from the battery does not keep the heat pump from it: dear grid, full battery."""
    inputs = _slots(4, [0.50] * 4, load=200.0)
    battery = BatterySpec(capacity_wh=20000, soc_wh=15000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)
    car = FlexLoad(name="car", energy_wh=0.0, max_power_w=0.0, available=[False] * 16, uses_battery=False)

    plan = optimize(inputs, battery, flex=[car], thermal=[_house(len(inputs))])

    assert sum(s.import_w for s in plan.slots) < 1.0


def test_the_surface_delays_the_heat() -> None:
    """A slow surface (screed): the heat of the first hours reaches the room only later, so preheating before
    dear hours starts earlier than with a fast one."""
    prices = [0.10] * 6 + [0.50] * 6
    inputs = _slots(12, prices)
    fast = optimize(inputs, BATTERY, thermal=[_house(len(inputs), inside=20.0)])
    slow_house = replace(_house(len(inputs), inside=20.0), cs=30.0, kf=3.0)
    slow = optimize(inputs, BATTERY, thermal=[slow_house])

    def heat_share(plan: object, slots: slice) -> float:
        values = [s.heat_w["hp"] for s in plan.slots]  # type: ignore[attr-defined]
        return sum(values[slots]) / max(sum(values), 1e-9)

    assert heat_share(slow, slice(0, 12)) >= heat_share(fast, slice(0, 12)) - 1e-6
    # The band is a soft bound: at most a trace below it
    assert min(s.room_c["hp"] for s in slow.slots) >= 20.0 - 0.01


def test_exact_step_matches_the_model() -> None:
    """Without heat the room in the plan follows the model's own free run (same exact discretization)."""
    import numpy as np

    from sunflow.heat_pump.house import Params, Row, simulate

    house = replace(_house(4, inside=21.0, outside=0.0), max_power_w=0.0, cold_cost=0.0, warm_cost=0.0)
    plan = optimize(_slots(1, [0.3]), BATTERY, thermal=[house])
    rows = [Row(start=START, heat_kw=0.0, outside_c=0.0, room_c=None, share=0.0, water_c=None, offset_k=None, heating=False)] * 12
    _, end = simulate(Params(u=P.u, kf=P.kf, cs=P.cs, cb=P.cb, g=P.g), rows, house.surface, 21.0)

    assert plan.slots[-1].room_c["hp"] == pytest.approx(float(end[1]), abs=1e-6)
    assert np.isfinite(plan.slots[0].room_c["hp"])
