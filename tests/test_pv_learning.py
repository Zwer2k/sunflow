"""Tests for learning the site's PV forecast correction."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sunflow.planning.pv_learning import cloud_class, learn

BERLIN = ZoneInfo("Europe/Berlin")


def _rows(days: int, factor_by_cloud: dict[str, float], cloud_by_day: list[float]) -> list[dict[str, Any]]:
    """Forecast 2 kW from 10 to 14 local; the site delivers factor x forecast, depending on clouds."""
    rows = []
    start = datetime(2026, 10, 1, tzinfo=BERLIN)
    for day in range(days):
        cloud = cloud_by_day[day % len(cloud_by_day)]
        for quarter in range(10 * 4, 14 * 4):
            moment = (start + timedelta(days=day, minutes=15 * quarter)).astimezone(UTC)
            rows.append(
                {
                    "start": moment.isoformat(),
                    "planned_pv_w": 2000.0,
                    "actual_pv_w": 2000.0 * factor_by_cloud[cloud_class(cloud)],
                    "weather_cloud": cloud,
                }
            )
    return rows


def test_learns_factor_per_cloud_class() -> None:
    """Overcast days deliver 60 %, clear days 105 % of the forecast."""
    rows = _rows(60, {"clear": 1.05, "overcast": 0.6, "mixed": 0.8}, [10, 90])

    correction = learn(rows, BERLIN)

    assert correction.days == 60
    # Close to the truth after two months; shrinkage keeps a little distance
    assert correction.factor(11, "overcast") == pytest.approx(0.6, abs=0.05)
    assert correction.factor(11, "clear") == pytest.approx(1.05, abs=0.05)
    assert correction.error_after < correction.error_before


def test_little_data_stays_cautious() -> None:
    """One day of data moves the factor only part of the way; no data means no correction."""
    correction = learn(_rows(1, {"clear": 0.5, "overcast": 0.5, "mixed": 0.5}, [10]), BERLIN)

    assert 0.5 < correction.factor(11, "clear") < 1.0
    assert learn([], BERLIN).factor(11, None) == 1.0


def test_without_weather_uses_hour_factor() -> None:
    """Old rows without cloud data still teach the per hour factor."""
    rows = _rows(20, {"clear": 0.8, "overcast": 0.8, "mixed": 0.8}, [10])
    for row in rows:
        row["weather_cloud"] = None

    correction = learn(rows, BERLIN)

    assert correction.factor(12, "overcast") == pytest.approx(0.8, abs=0.03)


def test_learns_each_plane_from_its_strings() -> None:
    """The north-east roof is shaded in the morning, the south-east one delivers as forecast."""
    from sunflow.planning.pv_learning import learn_planes

    rows = []
    start = datetime(2026, 10, 1, tzinfo=BERLIN)
    for day in range(30):
        for quarter in range(8 * 4, 12 * 4):  # 08-12 local
            moment = (start + timedelta(days=day, minutes=15 * quarter)).astimezone(UTC)
            rows.append({
                "start": moment.isoformat(),
                "plane_forecasts": {"solcast:ne": 1000.0, "solcast:se": 2000.0},
                "actual_strings": {"sofar:1": 600.0, "sofar:2": 1000.0, "huawei:1": 1000.0},
                "weather_cloud": None,
            })

    corrections = learn_planes(rows, {"solcast:ne": ["sofar:1"], "solcast:se": ["sofar:2", "huawei:1"]}, BERLIN)

    assert corrections["solcast:ne"].factor(9, None) == pytest.approx(0.6, abs=0.05)
    assert corrections["solcast:se"].factor(9, None) == pytest.approx(1.0, abs=0.03)


def test_shaded_plane_learns_below_a_third() -> None:
    """A plane delivering a fifth of its forecast for weeks gets a factor close to that."""
    from datetime import UTC, datetime, timedelta

    from sunflow.planning.pv_learning import learn

    start = datetime(2026, 10, 1, 11, tzinfo=UTC)
    rows = [
        {"start": (start + timedelta(days=d, minutes=15 * q)).isoformat(), "forecast_pv_w": 2000.0, "actual_pv_w": 400.0}
        for d in range(28)
        for q in range(16)
    ]

    assert learn(rows, UTC).factor(12, None) < 0.25


def test_plane_restart_ignores_older_rows() -> None:
    """After cleaning the modules only the new days count for that plane."""
    from sunflow.planning.pv_learning import learn_planes

    start = datetime(2026, 10, 1, tzinfo=BERLIN)
    cleaned = start + timedelta(days=20)
    rows = [
        {
            "start": (moment := (start + timedelta(days=day, minutes=15 * quarter)).astimezone(UTC)).isoformat(),
            "plane_forecasts": {"solcast:carport": 1000.0},
            # Covered by leaves before, as forecast afterwards
            "actual_strings": {"huawei:1": 200.0 if moment < cleaned else 1000.0},
            "weather_cloud": None,
        }
        for day in range(30)
        for quarter in range(10 * 4, 14 * 4)
    ]

    corrections = learn_planes(rows, {"solcast:carport": ["huawei:1"]}, BERLIN, {"solcast:carport": cleaned})

    assert corrections["solcast:carport"].days == 10
    assert corrections["solcast:carport"].factor(11, None) == pytest.approx(1.0, abs=0.03)


def test_recent_days_count_more() -> None:
    """A change weeks ago is followed: the factor ends closer to the recent level than the mean."""
    rows = []
    start = datetime(2026, 9, 1, tzinfo=BERLIN)
    for day in range(90):
        for quarter in range(10 * 4, 14 * 4):
            moment = (start + timedelta(days=day, minutes=15 * quarter)).astimezone(UTC)
            rows.append({"start": moment.isoformat(), "forecast_pv_w": 2000.0, "actual_pv_w": 1000.0 if day < 60 else 2000.0})

    # Plain mean over all days would be 0.67
    assert learn(rows, BERLIN).factor(11, None) > 0.75
