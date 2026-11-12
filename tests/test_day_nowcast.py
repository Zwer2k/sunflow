"""Tests for carrying how the day went so far over to the rest of it."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.planning.optimizer import SlotInput
from sunflow.planning.plan import day_nowcast

NOON = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)
RAW = 2000.0
# Three hours later today, and the same time tomorrow
LATER, TOMORROW = 12, 96


def _slots() -> list[SlotInput]:
    """The learned expectation is 60 % of the raw forecast."""
    return [
        SlotInput(start=NOON + t * SLOT, pv_w=0.6 * RAW, pv_low_w=0.3 * RAW, load_w=400.0, price=0.3, feed_in=0.08)
        for t in range(TOMORROW + 1)
    ]


@pytest.mark.parametrize(
    ("recent", "now", "later"),
    [
        # The day so far brought all of the raw forecast: now fully, after three hours half way
        pytest.param((3000.0, 3000.0, 1800.0), 1.0, 0.8, id="sunnier-than-learned"),
        pytest.param((900.0, 3000.0, 1800.0), 0.3, 0.45, id="duller-than-learned"),
        pytest.param((100.0, 300.0, 180.0), 0.6, 0.6, id="too-little-to-tell"),
        # Shaded all morning exactly as learned (30 % of the raw forecast): no reason to lower the afternoon
        pytest.param((900.0, 3000.0, 900.0), 0.6, 0.6, id="as-learned-for-those-hours"),
    ],
)
def test_rest_of_the_day_follows_the_measurement(recent: tuple[float, float, float], now: float, later: float) -> None:
    """Today moves by measured against expected of the last hours, fading out; tomorrow keeps the learned one."""
    slots, weights = day_nowcast(_slots(), [RAW] * (TOMORROW + 1), recent, UTC)

    assert slots[0].pv_w == pytest.approx(now * RAW)
    assert slots[LATER].pv_w == pytest.approx(later * RAW)
    assert slots[TOMORROW].pv_w == pytest.approx(0.6 * RAW)
    assert weights[TOMORROW] == 0.0
    # The pessimistic case moves along and never above the expectation
    assert slots[0].pv_low_w == pytest.approx(min(0.3 * RAW * now / 0.6, now * RAW))


def test_expected_of_a_recorded_slot_uses_each_planes_correction() -> None:
    """A plane with its own learned factor, the rest with the site's; without planes the whole forecast."""
    from sunflow.planning.pv_learning import PvCorrection
    from sunflow.shadow.runner import expected_pv_w

    site = PvCorrection(overall=0.8, slots=100)
    own = {"roof": PvCorrection(overall=0.3, slots=100)}
    row = {"start": NOON.isoformat(), "forecast_pv_w": 3000.0, "weather_cloud": None, "plane_forecasts": {"roof": 2000.0, "carport": 1000.0}}

    assert expected_pv_w(row, UTC, site, own, {}, None) == pytest.approx(2000 * 0.3 + 1000 * 0.8)
    assert expected_pv_w(row | {"plane_forecasts": {}}, UTC, site, own, {}, None) == pytest.approx(3000 * 0.8)
    assert expected_pv_w(row, UTC, None, {}, {}, None) == pytest.approx(3000.0)
