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
