"""The optimizer's thermal load: a heat pump planned with the house as a heat storage."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sunflow.planning.house_model import HouseModel
from sunflow.planning.optimizer import (
    BatterySpec,
    FlexLoad,
    SlotInput,
    ThermalLoad,
    optimize,
)

START = datetime(2026, 11, 16, tzinfo=UTC)
SLOT = timedelta(minutes=15)
# Nearly empty and small: the house is the storage that matters here
BATTERY = BatterySpec(capacity_wh=1000, soc_wh=100, min_soc_wh=100, max_charge_w=100, max_discharge_w=100)
# τ 50 h, 25 kWh/K, a little internal gain
MODEL = HouseModel(a=0.02, b=0.04, c=None, d=0.05, days=14, samples=300, error_k=0.2, baseline_k=0.8)


def _slots(hours: int, price: list[float], load: float = 2000.0) -> list[SlotInput]:
    return [
        SlotInput(start=START + i * SLOT, pv_w=0.0, load_w=load, price=price[i // 4], feed_in=0.08)
        for i in range(hours * 4)
    ]


def _house(n: int, inside: float = 21.0, outside: float = 0.0) -> ThermalLoad:
    # What holding 20 °C draws: (a·ΔT - gains) / b kWh heat per hour at COP 3
    base = (MODEL.a * (20.0 - outside) - MODEL.d) / MODEL.b / 3.0 * 1000
    return ThermalLoad(
        name="hp", a=MODEL.a, b=MODEL.b, gains=MODEL.d, inside=inside, outside=[outside] * n, cop=[3.0] * n,
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
