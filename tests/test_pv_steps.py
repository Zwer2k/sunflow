"""Sudden changes of one plane against the others: soiled days are not learned, the plane is planned lower."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from sunflow.planning.pv_learning import learn_planes
from sunflow.planning.pv_steps import plane_state, plane_states

TZ = ZoneInfo("Europe/Berlin")
FIRST = date(2026, 10, 27)
# Measured on a site (Wh per day, Oct 27 to Nov 8): carport under leaves until Nov 6, cleared then
LEAVES = [
    (1622, 8411), (1294, 5666), (2454, 7343), (2291, 7555), (1866, 6827), (1978, 6120), (2196, 7066),
    (735, 3602), (911, 2563), (1784, 5853), (620, 4160),
]  # fmt: skip
CLEAR = [(1800, 2930), (2510, 4899)]
SLIDE = [0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.7]
ROOF = [
    (17753, 28246), (11888, 19359), (18825, 25217), (16461, 26081), (10114, 21752), (13477, 19976),
    (15821, 21955), (5745, 11831), (4993, 8514), (13785, 20872), (3215, 13764), (8678, 9719), (11784, 16416),
]  # fmt: skip


def energy(carport: list[tuple[float, float]]) -> dict[date, dict[str, tuple[float, float]]]:
    """Energy per day of the carport and the roof."""
    return {
        FIRST + timedelta(days=i): {"carport": c, "roof": ROOF[i % len(ROOF)]}
        for i, c in enumerate(carport)
    }


def clean(days: int) -> list[tuple[float, float]]:
    """Days the carport delivers 70 % of the roof's share of its forecast (clean)."""
    return [(r[0] * 0.7, r[1]) for r in (ROOF * 2)[13 : 13 + days]]


@pytest.mark.parametrize(
    ("carport", "expected"),
    [
        pytest.param(
            [*LEAVES, *CLEAR],
            {"changes": [{"since": "2026-11-07", "change": 1.57}], "soiled": [{"from": "2026-10-27", "to": "2026-11-07"}], "level": 1.0},
            id="cleared",
        ),
        pytest.param(
            # Clear from Nov 7 (the dull Nov 6 already near it), leaves at half from Nov 12
            [*LEAVES, *CLEAR, *clean(3), *[(a * 0.5, f) for a, f in clean(3)]],
            {
                "changes": [{"since": "2026-11-06", "change": 1.56}, {"since": "2026-11-12", "change": 0.55}],
                "soiled": [{"from": "2026-10-27", "to": "2026-11-06"}, {"from": "2026-11-12", "to": None}],
                "level": 0.551,
            },
            id="leaves again",
        ),
        pytest.param(
            # Leaves gathering slowly after the cleaning
            [*LEAVES, *CLEAR, *clean(3), *[(a * share, f) for (a, f), share in zip(clean(10)[3:], SLIDE, strict=True)]],
            {
                "changes": [{"since": "2026-11-06", "change": 1.56}],
                "soiled": [{"from": "2026-10-27", "to": "2026-11-06"}, {"from": "2026-11-15", "to": None}],
                "level": 0.708,
            },
            id="leaves gathering",
        ),
        pytest.param(
            [*LEAVES, *CLEAR, *clean(5), *[(a * 0.2, f) for a, f in clean(8)[5:]]],
            {
                "changes": [{"since": "2026-11-06", "change": 1.56}, {"since": "2026-11-14", "change": 0.2}],
                "soiled": [{"from": "2026-10-27", "to": "2026-11-06"}, {"from": "2026-11-14", "to": None}],
                "level": 0.197,
            },
            id="snow",
        ),
        pytest.param([*LEAVES, CLEAR[0], LEAVES[0]], {"changes": [], "soiled": [], "level": 1.0}, id="one bright day"),
        pytest.param(LEAVES, {"changes": [], "soiled": [], "level": 1.0}, id="no change"),
    ],
)
def test_plane_state(carport: list[tuple[float, float]], expected: dict[str, object]) -> None:
    """Changes, soiled periods and the share now."""
    assert plane_state(energy(carport), "carport").as_dict() == expected


def test_soiled_days_are_not_learned() -> None:
    """The carport learns only from its clear days."""
    rows = [
        {
            "start": f"{day.isoformat()}T11:00:00+00:00",
            "plane_forecasts": {"carport": c[1] * 4, "roof": r[1] * 4},
            "actual_strings": {"c:1": c[0] * 4, "r:1": r[0] * 4},
        }
        for day, c, r in zip((FIRST + timedelta(days=i) for i in range(13)), [*LEAVES, *CLEAR], ROOF, strict=True)
    ]
    planes = {"carport": ["c:1"], "roof": ["r:1"]}
    states = plane_states(rows, planes, TZ)
    assert list(states) == ["carport"]
    learned = learn_planes(rows, planes, TZ, states=states)["carport"]
    # Only the two clear days: 4310 of 7829 Wh, shrunk towards 1 with so few data
    assert learned.days == 2
    assert learned.overall > learn_planes(rows, planes, TZ)["carport"].overall
    # Without a second plane nothing to compare with
    assert plane_states(rows, {"carport": ["c:1"]}, TZ) == {}


# Measured on a site, Oct 27 to Nov 11 (Wh per day: measured, forecast); the carport was cleared on Nov 7,
# a few leaves lay on the north-west roof
SITE = {
    "north_west": [
        (6231, 8850), (4541, 9073), (7587, 9407), (5732, 9235), (4002, 7404), (5196, 8273), (5184, 6276), (2690, 5519),
        (2336, 4185), (5341, 8468), (1479, 6609), (3839, 4787), (4790, 7934), (3165, 5835), (1776, 3045), (2531, 2772),
    ],
    "south_east": [
        (11502, 19393), (7328, 10262), (11177, 15780), (10645, 16812), (6037, 14267), (8226, 11689), (10596, 15636),
        (2994, 6244), (2603, 4306), (8444, 12396), (1736, 7136), (4839, 4922), (9176, 11006), (6393, 7414), (2092, 3136),
        (3063, 2858),
    ],
    "carport": [
        (1622, 8410), (1294, 5659), (2454, 7335), (2290, 7545), (1866, 6804), (1978, 6116), (2194, 7054), (731, 3582),
        (908, 2557), (1784, 5851), (620, 4155), (1800, 2926), (2805, 5514), (2034, 4061), (635, 1856), (944, 1689),
    ],
}  # fmt: skip


@pytest.mark.parametrize(
    ("plane", "changes"),
    [
        pytest.param("carport", [{"since": "2026-11-07", "change": 1.54}], id="the cleaned carport changed"),
        pytest.param("north_west", [], id="the roof next to it did not"),
        pytest.param("south_east", [], id="nor the other roof"),
    ],
)
def test_three_planes_only_the_one_furthest_from_the_others_changed(plane: str, changes: list[dict[str, object]]) -> None:
    """A carport cleared of leaves must not make a roof look soiled against the sum of the others."""
    history = {FIRST + timedelta(days=i): {key: days[i] for key, days in SITE.items()} for i in range(16)}
    assert plane_state(history, plane).changes == changes


def test_daily_energy_against_the_learned_expectation() -> None:
    """With an expectation per plane the forecast side is what the plane delivers when clean."""
    from datetime import datetime

    from sunflow.planning.pv_steps import daily_energy

    rows = [
        {
            "start": f"2026-11-07T{hour:02d}:00:00+00:00",
            "plane_forecasts": {"roof": 2000.0, "carport": 1000.0},
            "actual_strings": {"a:1": 1200.0, "b:1": 500.0},
        }
        for hour in (10, 11)
    ]

    def expected(key: str, start: datetime, forecast_w: float) -> float:
        return forecast_w * {"roof": 0.6}.get(key, 1.0)

    energy = daily_energy(rows, {"roof": ["a:1"], "carport": ["b:1"]}, TZ, expected)
    assert energy == {date(2026, 11, 7): {"roof": (600.0, 600.0), "carport": (250.0, 500.0)}}


def test_a_plane_that_joins_later_is_no_step_for_the_others() -> None:
    """A new carport far below its forecast changes what "the others" deliver; the roofs did not change."""
    history = {
        FIRST + timedelta(days=i): {"south_east": (9000.0, 10000.0), "north_west": (4500.0, 5000.0)} for i in range(40)
    }
    for i in range(30, 40):
        history[FIRST + timedelta(days=i)]["carport"] = (1500.0, 6000.0)
    assert [plane_state(history, key).changes for key in ("south_east", "north_west", "carport")] == [[], [], []]
