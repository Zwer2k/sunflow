"""Past soiling of the roof planes (leaves, snow, dirt) over the whole history: an archive, not planned with.

Each plane against the others (pv_steps) finds the periods one plane delivered less; days on which
every plane fell far below its learned yield in frost are snow on all of them. What season they fall in,
how deep and how long they were is what a later model can learn from.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta, tzinfo
from statistics import median
from typing import TYPE_CHECKING, Any

from sunflow.planning.pv_steps import (
    STEP_REFERENCE_DAYS,
    _pooled,
    _relative,
    plane_state,
)

if TYPE_CHECKING:
    from sunflow.planning.pv_sun import Sample

# Hours with a low sun or little forecast say little about soiling
MIN_ELEVATION = 10.0
MIN_HOUR_WH = 50.0
# Snow on every plane: all below this share of their learned yield, on frosty days, at least this long
ALL_LOW_RATIO = 0.5
ALL_MIN_DAYS = 2
FROST_MAX_C = 1.0


@dataclass(frozen=True, kw_only=True)
class Episode:
    """A soiled period of one plane (plane None: all planes at once)."""

    plane: str | None
    kind: str
    start: date
    # Exclusive; None while it lasts
    end: date | None
    days: int
    # Share of the clean yield the plane delivered meanwhile
    depth: float | None
    min_temp: float | None = None
    frost_days: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """For the API."""
        return {
            "plane": self.plane,
            "kind": self.kind,
            "from": self.start.isoformat(),
            "to": self.end.isoformat() if self.end else None,
            "days": self.days,
            "depth": round(self.depth, 3) if self.depth is not None else None,
            "min_temp": round(self.min_temp, 1) if self.min_temp is not None else None,
            "frost_days": self.frost_days,
        }


@dataclass(kw_only=True)
class SoilingHistory:
    """The soiled periods of the history, the cleanings per plane and the days each plane could be judged."""

    episodes: list[Episode] = field(default_factory=list)
    cleanings: dict[str, list[date]] = field(default_factory=dict)
    covered: dict[str, list[date]] = field(default_factory=dict)


def daily_energy(samples: dict[str, list[Sample]], tz: tzinfo) -> dict[date, dict[str, tuple[float, float]]]:
    """Measured and forecast energy (Wh) per local day and plane, from the hourly history."""
    days: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for key, plane in samples.items():
        for s in plane:
            if s.elevation < MIN_ELEVATION or s.forecast_wh < MIN_HOUR_WH:
                continue
            total = days[s.start.astimezone(tz).date()][key]
            total[0] += s.actual_wh
            total[1] += s.forecast_wh
    return {day: {key: (a, f) for key, (a, f) in values.items()} for day, values in days.items()}


def _weather(temps: dict[date, float] | None, start: date, end: date) -> tuple[float | None, int | None]:
    """Lowest temperature and frosty days in [start, end)."""
    if temps is None:
        return None, None
    lows = [low for day, low in temps.items() if start <= day < end]
    if not lows:
        return None, None
    return min(lows), sum(1 for low in lows if low <= FROST_MAX_C)


def plane_episodes(
    energy: dict[date, dict[str, tuple[float, float]]], key: str, today: date, temps: dict[date, float] | None
) -> list[Episode]:
    """The plane's soiled periods with their depth against the clean days before."""
    return _plane(energy, key, today, temps)[0]


def _plane(
    energy: dict[date, dict[str, tuple[float, float]]], key: str, today: date, temps: dict[date, float] | None
) -> tuple[list[Episode], list[date], list[date]]:
    """Soiled periods, cleanings (sudden rises) and the days the plane could be compared."""
    state = plane_state(energy, key)
    days = [(day, values) for day in sorted(energy) if (values := _relative(energy[day], key)) is not None]
    cleanings = [date.fromisoformat(c["since"]) for c in state.changes if c["change"] > 1]
    result = []
    for (start, end), kind in zip(state.soiled, state.kinds, strict=True):
        stop = end or today + timedelta(days=1)
        inside = [v for day, v in days if start <= day < stop]
        # The clean days before; soiled since the data began: the clean days after
        clean = [v for day, v in days if day < start and not state.is_soiled(day)][-STEP_REFERENCE_DAYS:] or [
            v for day, v in days if day >= stop and not state.is_soiled(day)
        ][:STEP_REFERENCE_DAYS]
        depth = _pooled(inside) / _pooled(clean) if inside and clean else None
        low, frost = _weather(temps, start, stop)
        result.append(
            Episode(plane=key, kind=kind, start=start, end=end, days=(stop - start).days, depth=depth, min_temp=low, frost_days=frost)
        )
    return result, cleanings, [day for day, _ in days]


def all_plane_episodes(ratios: dict[str, dict[date, float]], temps: dict[date, float] | None) -> list[Episode]:
    """Frosty days in a row on which every plane delivered far less than learned: snow on all of them."""
    if temps is None or not ratios:
        return []
    low_days = []
    for day in sorted({d for plane in ratios.values() for d in plane}):
        shares = [plane[day] for plane in ratios.values() if day in plane]
        if len(shares) == len(ratios) and max(shares) < ALL_LOW_RATIO and temps.get(day, FROST_MAX_C + 1) <= FROST_MAX_C:
            low_days.append((day, sum(shares) / len(shares)))
    result = []
    run: list[tuple[date, float]] = []
    for day, share in [*low_days, (None, 0.0)]:
        if run and (day is None or (day - run[-1][0]).days > 1):
            if len(run) >= ALL_MIN_DAYS:
                start, end = run[0][0], run[-1][0] + timedelta(days=1)
                low, frost = _weather(temps, start, end)
                result.append(
                    Episode(
                        plane=None, kind="all_planes", start=start, end=end, days=(end - start).days,
                        depth=median(s for _, s in run), min_temp=low, frost_days=frost,
                    )
                )
            run = []
        if day is not None:
            run.append((day, share))
    return result


def soiling_history(
    energy: dict[date, dict[str, tuple[float, float]]],
    ratios: dict[str, dict[date, float]],
    temps: dict[date, float] | None,
    today: date,
) -> SoilingHistory:
    """Every soiled period found in the history (the latest first), with the cleanings and judged days."""
    history = SoilingHistory()
    keys = sorted({key for values in energy.values() for key in values})
    found = []
    for key in keys if len(keys) > 1 else []:
        plane, history.cleanings[key], history.covered[key] = _plane(energy, key, today, temps)
        found += plane
    found += all_plane_episodes(ratios, temps)
    history.episodes = sorted(found, key=lambda e: e.start, reverse=True)
    return history


def episodes(
    energy: dict[date, dict[str, tuple[float, float]]],
    ratios: dict[str, dict[date, float]],
    temps: dict[date, float] | None,
    today: date,
) -> list[Episode]:
    """Every soiled period found in the history, the latest first."""
    return soiling_history(energy, ratios, temps, today).episodes
