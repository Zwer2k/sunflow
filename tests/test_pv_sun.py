"""Tests for the PV correction by the sun's position."""

from datetime import UTC, datetime, timedelta
import math

import pytest
from sunflow.planning.pv_sun import backtest, fit, samples_from
from sunflow.planning.sun import sun_position

LAT, LON = 51.3, 9.5
START = datetime(2025, 9, 1, tzinfo=UTC)


def _shaded(elevation: float, azimuth: float) -> bool:
    """A tree in the south-east: shade only when the sun is low there (winter mornings)."""
    return elevation < 20 and 120 <= azimuth <= 160


def _history(days: int) -> tuple[dict[datetime, float], dict[datetime, float]]:
    forecast, measured = {}, {}
    for hour in range(days * 24):
        start = START + timedelta(hours=hour)
        elevation, azimuth = sun_position(start + timedelta(minutes=30), LAT, LON)
        if elevation <= 0:
            continue
        clear_sky = 2000 * math.sin(math.radians(elevation))
        forecast[start] = clear_sky
        measured[start] = clear_sky * (0.3 if _shaded(elevation, azimuth) else 0.95)
    return forecast, measured


def test_learns_shade_by_sun_position_not_by_hour() -> None:
    """The shaded cells get a low factor, the rest stays near the overall one; the hour model cannot tell seasons apart."""
    forecast, measured = _history(200)
    samples = samples_from(forecast, measured, LAT, LON)

    correction = fit(samples)

    assert correction.factor(12, 140, clear=True) == pytest.approx(0.3, abs=0.12)
    assert correction.factor(40, 180, clear=True) == pytest.approx(0.95, abs=0.05)
    result = backtest(samples, UTC)
    assert result is not None
    # The last weeks lie in March: the hour model carries the winter shade into hours the sun is already
    # higher in, and does worse than no correction; the sun model does not
    assert result["by_sun"] < result["raw"] < result["by_hour"]


def test_hours_without_measurement_or_sun_are_left_out() -> None:
    """Only hours with forecast, measurement and the sun above the horizon count."""
    night = START.replace(hour=0)
    noon = START.replace(hour=11)
    samples = samples_from({night: 0.0, noon: 1500.0, noon + timedelta(hours=1): 1400.0}, {night: 0.0, noon: 1200.0}, LAT, LON)

    assert [s.start for s in samples] == [noon]
