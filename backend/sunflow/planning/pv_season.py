"""What soiling to expect by season, learned from the soiling archive; compared in shadow mode, not planned with.

Per plane and calendar month: how many of the judged days were soiled and how much the plane delivered
then. After a cleaning leaves come back: the typical days from a cleaning to the next gradual soiling.
Today the plan keeps the current level (persistence); this expectation runs next to it, and the
comparison under Analysis shows which was closer to the measured share of each day.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any

from sunflow.planning.pv_episodes import SoilingHistory
from sunflow.planning.pv_steps import (
    STEP_REFERENCE_DAYS,
    PlaneState,
    _relative,
    relative_share,
)

# Judged days of a calendar month (over all years) before it says anything
MONTH_MIN_DAYS = 10
# A gradual soiling this long after a cleaning counts as leaves coming back
REGROWTH_MAX_DAYS = 30
# How long after a cleaning the regrowth is expected instead of the month's average
REGROWTH_APPLIES_DAYS = 60
# Days a comparison needs before it is shown
CHECK_MIN_DAYS = 7


@dataclass(kw_only=True)
class Month:
    """One calendar month of a plane over all years."""

    days: int = 0
    soiled_days: int = 0
    # Day-weighted share of the clean yield while soiled
    depth: float | None = None

    @property
    def share(self) -> float:
        """Part of the judged days that were soiled."""
        return self.soiled_days / self.days if self.days else 0.0

    def expected(self) -> float | None:
        """Expected share of the clean yield on a day of this month; None without enough days."""
        if self.days < MONTH_MIN_DAYS:
            return None
        return 1 - self.share * (1 - (self.depth if self.depth is not None else 1.0))


@dataclass(kw_only=True)
class PlaneSeason:
    """A plane's soiling by month and how fast leaves come back after a cleaning."""

    months: dict[int, Month] = field(default_factory=dict)
    regrowth_days: float | None = None
    last_cleaning: date | None = None

    def expected(self, day: date, current: float, cleaned: date | None = None) -> float:
        """Expected share of the clean yield on a day: soiled now stays; after a cleaning leaves come back."""
        if current < 1:
            return current
        month = self.months.get(day.month)
        if month is None or (by_month := month.expected()) is None:
            return 1.0
        cleaned = cleaned or self.last_cleaning
        since = (day - cleaned).days if cleaned is not None else None
        # Only in the weeks after a cleaning: one from last year says nothing about this season's leaves
        if self.regrowth_days is not None and since is not None and 0 <= since <= REGROWTH_APPLIES_DAYS and month.depth is not None:
            return month.depth if since >= self.regrowth_days else 1.0
        return by_month

    def as_dict(self) -> dict[str, Any]:
        """For the API."""
        return {
            "months": {
                m: {"days": v.days, "soiled_days": v.soiled_days, "depth": round(v.depth, 3) if v.depth is not None else None}
                for m, v in sorted(self.months.items())
            },
            "regrowth_days": self.regrowth_days,
            "last_cleaning": self.last_cleaning.isoformat() if self.last_cleaning else None,
        }


def season_profile(history: SoilingHistory, today: date) -> dict[str, PlaneSeason]:
    """Soiling by calendar month per plane; snow on all planes counts for each of them."""
    result = {}
    for key, covered in history.covered.items():
        judged = set(covered)
        months: dict[int, Month] = defaultdict(Month)
        for day in judged:
            months[day.month].days += 1
        # Per soiled day its depth; a day in two episodes (its own and snow on all planes) counts once, at the lower
        soiled: dict[date, float | None] = {}
        for e in history.episodes:
            if e.plane not in (key, None):
                continue
            stop = e.end or today + timedelta(days=1)
            for offset in range((stop - e.start).days):
                day = e.start + timedelta(days=offset)
                if day not in judged and e.plane is not None:
                    continue
                known = [d for d in (soiled.get(day), e.depth) if d is not None]
                if day not in soiled and day not in judged:
                    months[day.month].days += 1
                soiled[day] = min(known) if known else None
        for day in soiled:
            months[day.month].soiled_days += 1
        for month in {day.month for day in soiled}:
            depths = [depth for day, depth in soiled.items() if day.month == month and depth is not None]
            months[month].depth = sum(depths) / len(depths) if depths else None
        cleanings = history.cleanings.get(key, [])
        gaps = [
            (e.start - max(c for c in cleanings if c <= e.start)).days
            for e in history.episodes
            if e.plane == key and e.kind == "gradual" and any(c <= e.start for c in cleanings)
        ]
        gaps = [g for g in gaps if g <= REGROWTH_MAX_DAYS]
        result[key] = PlaneSeason(
            months=dict(months), regrowth_days=median(gaps) if gaps else None, last_cleaning=max(cleanings, default=None)
        )
    return result


def day_levels(energy: dict[date, dict[str, tuple[float, float]]], key: str, state: PlaneState) -> dict[date, float]:
    """The plane's share of its clean yield per day: its share against the others over that of the clean days before."""
    days = [(day, values) for day in sorted(energy) if (values := _relative(energy[day], key)) is not None]
    result = {}
    for index, (day, values) in enumerate(days):
        clean = [v for d, v in days[:index] if not state.is_soiled(d)][-STEP_REFERENCE_DAYS:]
        if len(clean) >= STEP_REFERENCE_DAYS // 2:
            result[day] = min(relative_share([values], clean) or 1.0, 1.5)
    return result


def compare(expected: dict[date, tuple[float, float]], actual: dict[date, float]) -> dict[str, Any] | None:
    """Mean deviation of the season expectation and of keeping the current level from the measured share."""
    pairs = [(e, c, actual[day]) for day, (e, c) in expected.items() if day in actual]
    if len(pairs) < CHECK_MIN_DAYS:
        return None
    return {
        "days": len(pairs),
        "season": round(sum(abs(e - a) for e, _, a in pairs) / len(pairs), 3),
        "current": round(sum(abs(c - a) for _, c, a in pairs) / len(pairs), 3),
    }


# Snow on all planes now: every learned plane at most this share of its learned yield, in frost while bright
SNOW_LEVEL = 0.5
SNOW_FROST_C = 1.0
# Bright enough that the modules would deliver: lux or W/m²
SNOW_BRIGHT = {"lx": 15_000.0, "W/m²": 150.0}


def snow_on_all(levels: dict[str, float], low: float | None, bright: float | None, unit: str | None) -> dict[str, Any] | None:
    """Snow on every plane: all far below their learned yield, frost, and light that should have reached them.

    Without a brightness source a grey day looks the same, so only frost and the planes decide then ("bright": None).
    """
    if not levels or max(levels.values()) > SNOW_LEVEL or low is None or low > SNOW_FROST_C:
        return None
    threshold = SNOW_BRIGHT.get(unit or "")
    if bright is not None and threshold is not None and bright < threshold:
        return None
    return {
        "levels": {key: round(level, 3) for key, level in levels.items()},
        "min_temp": round(low, 1),
        "bright": round(bright) if bright is not None and threshold is not None else None,
        "unit": unit if threshold is not None else None,
    }


# Snow ahead in the weather forecast: precipitation in frost, or the forecast says snow; from this much on
SNOW_CONDITIONS = ("snowy", "snowy-rainy")
SNOW_AHEAD_FROST_C = 0.5
SNOW_AHEAD_MIN_MM = 1.0


def snow_ahead(forecast: list[dict[str, Any]]) -> dict[str, Any] | None:
    """When the hourly weather forecast expects snow to fall, and how much precipitation (mm) it brings."""
    hours = [
        item
        for item in forecast
        if item.get("datetime")
        and (item.get("precipitation") or 0.0) > 0
        and (
            item.get("condition") in SNOW_CONDITIONS
            or (item.get("temperature") is not None and item["temperature"] <= SNOW_AHEAD_FROST_C)
        )
    ]
    total = sum(item["precipitation"] for item in hours)
    if total < SNOW_AHEAD_MIN_MM:
        return None
    return {"from": min(item["datetime"] for item in hours), "hours": len(hours), "precipitation": round(total, 1)}

