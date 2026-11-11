"""Soiled periods over the whole history: per plane against the others, and snow on all planes."""

from datetime import date, timedelta

from sunflow.planning.pv_episodes import episodes
from sunflow.planning.pv_steps import plane_state
from test_pv_steps import CLEAR, FIRST, LEAVES, clean, energy

SNOW_DAY = FIRST + timedelta(days=18)


def test_plane_episodes_with_weather() -> None:
    """Leaves since the data began, then snow on the carport: kind, depth and the frost meanwhile."""
    history = energy([*LEAVES, *CLEAR, *clean(5), *[(a * 0.2, f) for a, f in clean(8)[5:]]])
    lows = {FIRST + timedelta(days=i): 5.0 if FIRST + timedelta(days=i) < SNOW_DAY else -3.0 for i in range(25)}
    found = [e.as_dict() for e in episodes(history, {}, lows, max(history))]
    assert found == [
        {"plane": "carport", "kind": "drop", "from": "2026-11-14", "to": None, "days": 3, "depth": 0.199, "min_temp": -3.0, "frost_days": 3},
        {
            "plane": "carport", "kind": "before_cleaning", "from": "2026-10-27", "to": "2026-11-06", "days": 10,
            "depth": 0.63, "min_temp": 5.0, "frost_days": 0,
        },
    ]


def test_snow_on_all_planes() -> None:
    """Frosty days on which every plane fell far below its learned yield; a mild day is no snow."""
    shares = {"roof": [0.9, 0.2, 0.1, 0.3, 0.95, 0.2, 0.2], "west": [0.8, 0.1, 0.2, 0.4, 0.9, 0.1, 0.1]}
    ratios = {key: {date(2026, 1, 5 + i): r for i, r in enumerate(values)} for key, values in shares.items()}
    lows = {date(2026, 1, 4 + i): -2.0 for i in range(7)} | {date(2026, 1, 10): 4.0, date(2026, 1, 11): 4.0}
    found = [e.as_dict() for e in episodes({}, ratios, lows, date(2026, 1, 12))]
    assert found == [
        {"plane": None, "kind": "all_planes", "from": "2026-01-06", "to": "2026-01-09", "days": 3, "depth": 0.15, "min_temp": -2.0, "frost_days": 3},
    ]


def test_no_snow_without_temperature() -> None:
    """Without an outdoor temperature a grey week cannot be told from snow."""
    ratios = {"roof": {date(2026, 1, 5 + i): 0.1 for i in range(4)}, "west": {date(2026, 1, 5 + i): 0.1 for i in range(4)}}
    assert episodes({}, ratios, None, date(2026, 1, 10)) == []


def test_one_other_plane_in_the_window_decides_by_own_share() -> None:
    """A third plane known only long before: around the cleaning the roof is compared with the carport alone."""
    history = energy([*LEAVES, *CLEAR])
    for i in range(14):
        history[FIRST - timedelta(days=30 - i)] = {"roof": (14000, 20000), "west": (7000, 10000)}
    assert plane_state(history, "roof").as_dict() == {"changes": [], "soiled": [], "level": 1.0}
    assert plane_state(history, "carport").kinds == ["before_cleaning"]
