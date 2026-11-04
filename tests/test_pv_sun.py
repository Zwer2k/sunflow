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


def test_factor_for_a_slot_ahead() -> None:
    """A clear winter morning in the shade gets the shade factor, a cloudy one the cloudy factor, night none."""
    forecast, measured = _history(200)
    correction = fit(samples_from(forecast, measured, LAT, LON))
    morning = datetime(2026, 12, 15, 8, 45, tzinfo=UTC)
    elevation, azimuth = sun_position(morning, LAT, LON)
    clear_sky = 2000 * math.sin(math.radians(elevation))

    assert _shaded(elevation, azimuth)
    assert correction.factor_at(morning, clear_sky, LAT, LON) == pytest.approx(0.3, abs=0.12)
    assert correction.factor_at(morning, clear_sky / 4, LAT, LON) == correction.factor(elevation, azimuth, clear=False)
    assert correction.factor_at(datetime(2026, 12, 15, 22, tzinfo=UTC), 0.0, LAT, LON) == 1.0


@pytest.mark.parametrize(
    ("days", "by_sun", "plannable"),
    [
        pytest.param(595, 131.0, True, id="long-and-better"),
        pytest.param(10, 131.0, False, id="too-short"),
        pytest.param(595, 200.0, False, id="not-better"),
    ],
)
def test_planning_uses_a_plane_only_when_proven(days: int, by_sun: float, plannable: bool) -> None:
    """Enough history and better than the correction by time of day in the backtest."""
    from sunflow.planning.pv_sun import PlaneSun, SunCorrection

    plane = PlaneSun(
        key="solcast:x", name="Süd-Ost", source="solcast",
        correction=SunCorrection(days=days, hours=days * 10),
        backtest={"raw": 270.0, "by_hour": 176.0, "by_sun": by_sun},
    )

    assert plane.plannable is plannable


def test_leaves_on_the_modules_are_recognised() -> None:
    """The last days far below the plane's own history: soiled since the first of them, left out of learning."""
    from datetime import date

    from sunflow.planning.pv_sun import daily_ratios, fit, soiled_run

    forecast, measured = _history(200)
    leaves_from = START + timedelta(days=192)
    measured = {hour: value * (0.3 if hour >= leaves_from else 1.0) for hour, value in measured.items()}
    samples = samples_from(forecast, measured, LAT, LON)
    reference = fit([s for s in samples if s.start < START + timedelta(days=170)])

    run = soiled_run(daily_ratios([s for s in samples if s.start >= START + timedelta(days=170)], reference, UTC), 0.7)

    assert run is not None
    assert run["since"] == leaves_from.date().isoformat()
    assert run["ratio"] == pytest.approx(0.3, abs=0.05)
    # Clean days give no hint
    assert soiled_run(daily_ratios([s for s in samples if s.start < leaves_from], reference, UTC), 0.7) is None
    assert date.fromisoformat(run["since"]) == leaves_from.date()


def test_soiling_recommendation() -> None:
    """With a history the text compares with it, without it with the forecast."""
    from sunflow.analysis.recommendations import pv_soiling
    from sunflow.planning.pv_sun import PlaneSun

    planes = [
        PlaneSun(key="solcast:c", name="Carport", source="solcast", soiling={"since": "2026-10-26", "days": 9, "ratio": 0.3}),
        PlaneSun(key="solcast:s", name="Süd-Ost", source="solcast"),
    ]

    found = pv_soiling(planes)

    assert [r.id for r in found] == ["pv_soiling:solcast:c"]
    assert "30 %" in found[0].title
    assert "Prognose" in found[0].title
