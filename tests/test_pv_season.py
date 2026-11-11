"""Soiling to expect by season, from the archive; compared with keeping the current level."""

from datetime import date
from pathlib import Path

import pytest
from sunflow.planning.pv_episodes import soiling_history
from sunflow.planning.pv_season import Month, PlaneSeason, compare, day_levels, season_profile, snow_ahead, snow_on_all
from sunflow.planning.pv_steps import plane_state
from sunflow.shadow.store import ShadowStore
from test_pv_steps import CLEAR, LEAVES, SLIDE, clean, energy

# Leaves until a cleaning on Nov 6, coming back from Nov 15
HISTORY = energy([*LEAVES, *CLEAR, *clean(3), *[(a * share, f) for (a, f), share in zip(clean(10)[3:], SLIDE, strict=True)]])
TODAY = max(HISTORY)


def test_season_profile() -> None:
    """Soiled days and depth per month, and the days from the cleaning until leaves come back."""
    seasons = season_profile(soiling_history(HISTORY, {}, None, TODAY), TODAY)
    assert {key: season.as_dict() for key, season in seasons.items()} == {
        "carport": {
            "months": {10: {"days": 5, "soiled_days": 5, "depth": 0.656}, 11: {"days": 18, "soiled_days": 9, "depth": 0.708}},
            "regrowth_days": 9,
            "last_cleaning": "2026-11-06",
        },
        "roof": {
            "months": {10: {"days": 5, "soiled_days": 0, "depth": None}, 11: {"days": 18, "soiled_days": 0, "depth": None}},
            "regrowth_days": None,
            "last_cleaning": None,
        },
    }


@pytest.mark.parametrize(
    ("day", "current", "cleaned", "expected"),
    [
        pytest.param(date(2026, 11, 20), 0.6, None, 0.6, id="soiled now stays"),
        pytest.param(date(2026, 11, 10), 1.0, date(2026, 11, 6), 1.0, id="just cleaned"),
        pytest.param(date(2026, 11, 20), 1.0, date(2026, 11, 6), 0.7, id="leaves back after regrowth days"),
        pytest.param(date(2026, 12, 1), 1.0, None, 1.0, id="month without data"),
    ],
)
def test_expected(day: date, current: float, cleaned: date | None, expected: float) -> None:
    """Expected share of the clean yield on a day."""
    season = PlaneSeason(months={11: Month(days=20, soiled_days=10, depth=0.7)}, regrowth_days=9)
    assert season.expected(day, current, cleaned) == expected


def test_expected_by_month_without_cleaning() -> None:
    """Without a known cleaning: the month's soiled part at its depth."""
    season = PlaneSeason(months={11: Month(days=20, soiled_days=10, depth=0.6)})
    assert season.expected(date(2026, 11, 20), 1.0) == pytest.approx(0.8)


def test_day_levels() -> None:
    """The measured share of the clean yield while leaves gather; too few days to compare yet."""
    levels = day_levels(HISTORY, "carport", plane_state(HISTORY, "carport"))
    assert {day.isoformat(): round(v, 2) for day, v in levels.items()} == {
        "2026-11-13": 0.9, "2026-11-14": 0.86, "2026-11-15": 0.83, "2026-11-16": 0.78, "2026-11-17": 0.72, "2026-11-18": 0.72,
    }
    assert compare({day: (0.7, 1.0) for day in levels}, levels) is None


def test_compare() -> None:
    """Mean deviation of both expectations from the measured share."""
    days = [date(2026, 11, d) for d in range(10, 18)]
    actual = dict(zip(days, [1.0, 1.0, 0.9, 0.8, 0.7, 0.7, 0.7, 0.7], strict=True))
    stored = {day: (season, 1.0) for day, season in zip(days, [1.0, 1.0, 1.0, 0.7, 0.7, 0.7, 0.7, 0.7], strict=True)}
    assert compare(stored, actual) == {"days": 8, "season": 0.025, "current": 0.188}


def test_store_soiling_ahead(tmp_path: Path) -> None:
    """The first expectation for a day stays."""
    store = ShadowStore(tmp_path / "shadow.db")
    store.store_soiling_ahead(date(2026, 11, 12), "carport", 0.7, 1.0)
    store.store_soiling_ahead(date(2026, 11, 12), "carport", 0.5, 0.9)
    assert store.soiling_ahead(date(2026, 11, 1)) == {"carport": {date(2026, 11, 12): (0.7, 1.0)}}


@pytest.mark.parametrize(
    ("levels", "low", "bright", "unit", "expected"),
    [
        pytest.param(
            {"roof": 0.1, "west": 0.2}, -3.0, 40_000.0, "lx",
            {"levels": {"roof": 0.1, "west": 0.2}, "min_temp": -3.0, "bright": 40000, "unit": "lx"},
            id="bright frost, nothing delivered",
        ),
        pytest.param(
            {"roof": 0.1, "west": 0.2}, -3.0, None, None,
            {"levels": {"roof": 0.1, "west": 0.2}, "min_temp": -3.0, "bright": None, "unit": None},
            id="no brightness source",
        ),
        pytest.param({"roof": 0.1, "west": 0.2}, -3.0, 4_000.0, "lx", None, id="dark grey day"),
        pytest.param({"roof": 0.1, "west": 0.7}, -3.0, 40_000.0, "lx", None, id="one plane delivers"),
        pytest.param({"roof": 0.1, "west": 0.2}, 6.0, 40_000.0, "lx", None, id="no frost"),
        pytest.param({"roof": 0.1}, None, 40_000.0, "lx", None, id="no temperature"),
    ],
)
def test_snow_on_all(
    levels: dict[str, float], low: float | None, bright: float | None, unit: str | None, expected: dict[str, object] | None
) -> None:
    """Snow on all planes needs every plane far below, frost and, where measured, light."""
    assert snow_on_all(levels, low, bright, unit) == expected


def _hour(hour: int, temperature: float, precipitation: float, condition: str = "cloudy") -> dict[str, object]:
    return {"datetime": f"2026-11-13T{hour:02d}:00:00+00:00", "temperature": temperature, "precipitation": precipitation, "condition": condition}


@pytest.mark.parametrize(
    ("forecast", "expected"),
    [
        pytest.param(
            [_hour(1, 3.0, 0.5), _hour(2, 0.2, 0.8), _hour(3, -0.5, 0.6), _hour(4, -1.0, 0.0)],
            {"from": "2026-11-13T02:00:00+00:00", "hours": 2, "precipitation": 1.4},
            id="precipitation in frost",
        ),
        pytest.param(
            [_hour(5, 1.5, 1.2, "snowy")], {"from": "2026-11-13T05:00:00+00:00", "hours": 1, "precipitation": 1.2}, id="forecast says snow"
        ),
        pytest.param([_hour(1, 4.0, 3.0, "rainy")], None, id="rain"),
        pytest.param([_hour(1, -2.0, 0.3)], None, id="a few flakes"),
        pytest.param([_hour(1, -5.0, 0.0, "sunny")], None, id="dry frost"),
    ],
)
def test_snow_ahead(forecast: list[dict[str, object]], expected: dict[str, object] | None) -> None:
    """Snow the weather forecast expects, from 1 mm on."""
    assert snow_ahead(forecast) == expected
