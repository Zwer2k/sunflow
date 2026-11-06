"""Tests for the buffer against a day above the forecast."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sunflow.planning.load_forecast import daily_spread
from sunflow.planning.optimizer import BatterySpec, Settings, SlotInput, optimize
from sunflow.planning.plan import Buffer, storage_buffers

BERLIN = ZoneInfo("Europe/Berlin")
# 22:00: two hours expensive, 0-5 cheap, 19 hours expensive, 0-5 cheap, 19 hours expensive
START = datetime(2026, 11, 6, 22, 0, tzinfo=BERLIN)
SLOT = timedelta(minutes=15)
BATTERY = BatterySpec(capacity_wh=20000, soc_wh=2000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000)
DAY_END = 103  # last expensive slot before the second cheap night


def _octopus(i: int) -> float:
    if 8 <= i < 28:
        return 0.226
    # The second night a little cheaper: without a buffer nothing is carried over to it
    return 0.216 if 104 <= i < 124 else 0.326


def _slots(price: Callable[[int], float] = _octopus, pv: float = 0.0, pv_low: float | None = None, load: float = 600) -> list[SlotInput]:
    return [
        SlotInput(start=START + i * SLOT, pv_w=pv, pv_low_w=pv_low, load_w=load, price=price(i), feed_in=0.08)
        for i in range(48 * 4)
    ]


def _buffers(inputs: list[SlotInput]) -> list[Buffer]:
    return storage_buffers(inputs, 0.84, 0.15, 0.9, 0.5, 9000)


def test_buffer_at_the_end_of_each_expensive_period() -> None:
    """Before every cheap night, sized by the day's consumption; none after the last one."""
    buffers = _buffers(_slots())

    assert [b.slot for b in buffers] == [7, DAY_END]
    # 19 h at 600 W: 11.4 kWh, 15 % deviation, 0.84 standard deviations
    assert buffers[1].wh == pytest.approx(0.84 * 0.15 * 11400)
    # Half of what charging at night saves per kWh
    assert buffers[1].penalty == pytest.approx(0.5 * (0.326 - 0.216 / 0.9))


@pytest.mark.parametrize(
    "price",
    [
        pytest.param(lambda i: 0.3, id="flat-tariff"),
        # The spread is too small for charging at night to pay
        pytest.param(lambda i: 0.29 if 8 <= i < 28 or 104 <= i < 124 else 0.3, id="losses-eat-the-saving"),
    ],
)
def test_no_buffer_without_cheap_charging(price: Callable[[int], float]) -> None:
    """A buffer is only worth it where the battery can be charged cheaper."""
    assert _buffers(_slots(price)) == []


def test_uncertain_pv_needs_more_buffer() -> None:
    """PV that may not come counts as far as it would cover the house, not the surplus."""
    sure = _buffers(_slots(pv=300, pv_low=300))[1].wh
    unsure = _buffers(_slots(pv=300, pv_low=0))[1].wh
    surplus_only = _buffers(_slots(pv=3000, pv_low=600))[1].wh

    assert unsure > sure
    assert surplus_only == pytest.approx(_buffers(_slots(pv=600, pv_low=600))[1].wh)


def test_battery_keeps_the_buffer_for_a_day_above_the_forecast() -> None:
    """Charged at night for the expected day plus the buffer; on an expected day it is left over."""
    inputs = _slots()
    buffers = _buffers(inputs)

    plain = optimize(inputs, BATTERY, Settings(wear_ct_per_kwh=0))
    buffered = optimize(
        inputs, BATTERY, Settings(wear_ct_per_kwh=0, buffer=tuple((b.slot, b.wh, b.penalty) for b in buffers))
    )

    assert plain.slots[DAY_END].soc_wh == pytest.approx(BATTERY.min_soc_wh, abs=50)
    assert buffered.slots[DAY_END].soc_wh == pytest.approx(BATTERY.min_soc_wh + buffers[1].wh, abs=50)
    # The house still runs on the battery all day
    assert all(s.import_w < 1 for s in buffered.slots[28:DAY_END])


def test_buffer_is_used_rather_than_bought_at_the_expensive_price() -> None:
    """Once the day ran above the forecast, the battery covers it and is not refilled before the night."""
    inputs = _slots()[28:]
    buffers = _buffers(inputs)
    low = BatterySpec(capacity_wh=20000, soc_wh=3000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000)

    plan = optimize(inputs, low, Settings(wear_ct_per_kwh=0, buffer=tuple((b.slot, b.wh, b.penalty) for b in buffers)))

    assert all(s.charge_w < 1 for s in plan.slots[: DAY_END - 28])
    assert plan.slots[0].discharge_w > 0


def test_buffer_lies_above_the_backup_reserve() -> None:
    """Also while the battery is below the reserve now: the reserve is no buffer for the day."""
    inputs = _slots()
    buffers = _buffers(inputs)

    plan = optimize(
        inputs,
        BATTERY,
        Settings(wear_ct_per_kwh=0, reserve_wh=6000, buffer=tuple((b.slot, b.wh, b.penalty) for b in buffers)),
    )

    assert plan.slots[DAY_END].soc_wh == pytest.approx(6000 + buffers[1].wh, abs=50)


def test_daily_spread_of_the_consumption() -> None:
    """Deviation of whole days from their day type's mean; too few days give none."""
    start = datetime(2026, 10, 1, tzinfo=UTC)
    history = [
        (start + timedelta(hours=h), 500.0 * (1.2 if (h // 24) % 2 else 0.8)) for h in range(28 * 24)
    ]

    assert daily_spread(history, UTC) == pytest.approx(0.2, abs=0.02)
    assert daily_spread(history[: 5 * 24], UTC) is None


def test_battery_below_the_reserve_is_refilled_first() -> None:
    """Back above the backup reserve right away, even at the expensive price, and never below it again."""
    inputs = _slots()

    plan = optimize(inputs, BATTERY, Settings(wear_ct_per_kwh=6, reserve_wh=6000))

    assert plan.slots[3].soc_wh >= 6000 - 1
    assert min(s.soc_wh for s in plan.slots[3:]) >= 6000 - 1


@pytest.mark.parametrize(("premium", "charged"), [pytest.param(0.0, False, id="balanced"), pytest.param(0.1, True, id="storage")])
def test_storage_charges_from_the_grid_despite_high_losses(premium: float, charged: bool) -> None:
    """With 72 % round trip and wear, night power is dearer than day power; "storage" uses the battery anyway."""
    lossy = BatterySpec(
        capacity_wh=20000, soc_wh=2000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000,
        efficiency_charge=0.85, efficiency_discharge=0.85,
    )
    inputs = [replace(slot, import_penalty=premium if slot.price > 0.3 else 0.0) for slot in _slots()]

    plan = optimize(inputs, lossy, Settings(wear_ct_per_kwh=6))

    assert (max(s.soc_wh for s in plan.slots[8:28]) > 6000) is charged


def test_running_slot_moves_only_the_energy_of_its_rest() -> None:
    """Planned two minutes before the slot ends, full power in it charges only two minutes' worth."""
    inputs = _slots()

    plan = optimize(inputs, BATTERY, Settings(wear_ct_per_kwh=0, reserve_wh=6000, first_slot_share=2 / 15))

    assert plan.slots[0].action == "force_charge"
    assert plan.slots[0].soc_wh <= BATTERY.soc_wh + BATTERY.max_charge_w * 2 / 60 + 1
