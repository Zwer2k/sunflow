"""Tests for the room kept free for a sunnier day than planned."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.planning.optimizer import BatterySpec, Plan, Settings, SlotInput, optimize
from sunflow.planning.plan import pv_headrooms

START = datetime(2026, 11, 7, 22, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)
NIGHT = range(4, 24)  # 23:00 to 04:00, cheap
NEXT_NIGHT = range(100, 120)
SUN = range(40, 56)  # 08:00 to 12:00
BATTERY = BatterySpec(capacity_wh=10000, soc_wh=7000, min_soc_wh=1000, max_charge_w=5000, max_discharge_w=5000)


def _slots(pv: float = 2000.0) -> list[SlotInput]:
    """A cheap night, then four sunny hours whose planned surplus nearly fills the battery."""
    return [
        SlotInput(
            start=START + t * SLOT, pv_w=pv if t in SUN else 0.0, load_w=400.0,
            price=0.226 if t in NIGHT or t in NEXT_NIGHT else 0.326, feed_in=0.08,
        )
        for t in range(128)
    ]


def _band(high: float) -> list[tuple[float | None, float | None]]:
    return [(None, high if t in SUN else 0.0) for t in range(128)]


@pytest.mark.parametrize(
    ("high", "share", "room"),
    [
        # 4 h with 2 kW more surplus up to the optimistic forecast, 30 % of it kept free
        pytest.param(4000.0, 0.3, 0.3 * 4 * 2000, id="share-of-the-upside"),
        pytest.param(2000.0, 0.3, None, id="no-upside"),
        pytest.param(4000.0, 0.0, None, id="no-share"),
    ],
)
def test_room_for_the_optimistic_forecast(high: float, share: float, room: float | None) -> None:
    """At the end of the sunny period the battery should leave room for part of the extra PV."""
    headrooms = pv_headrooms(_slots(), _band(high), share, 9800.0)

    assert [h.wh for h in headrooms] == ([pytest.approx(room)] if room else [])
    assert all(h.slot == SUN[-1] and h.max_wh == pytest.approx(9800.0 - h.wh) for h in headrooms)
    # PV above the plan would replace a kWh at the cheapest price after it, less the feed-in
    assert all(h.penalty == pytest.approx(0.226 - 0.08) for h in headrooms)


def _night_import(plan: Plan) -> float:
    return sum(plan.slots[t].import_w for t in NIGHT)


def test_battery_covers_the_night_instead_of_holding_for_the_sun() -> None:
    """With room to keep for a sunnier day, the battery covers the cheap night instead of holding."""
    slots = _slots()
    plain = optimize(slots, BATTERY, Settings(wear_ct_per_kwh=0.0))
    headroom = pv_headrooms(slots, _band(4000.0), 0.3, 9800.0)
    roomy = optimize(slots, BATTERY, Settings(wear_ct_per_kwh=0.0, headroom=tuple((h.slot, h.max_wh, h.penalty) for h in headroom)))

    assert _night_import(roomy) < _night_import(plain)
    assert roomy.slots[SUN[-1]].soc_wh < plain.slots[SUN[-1]].soc_wh
