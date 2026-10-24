"""Household load forecast from Home Assistant long-term statistics.

First version without ML: the mean load per hour of day, preferring the
same day type (weekday / weekend), falling back to all days and finally to
a constant when there is too little history.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from statistics import fmean
from typing import Any

from .ha import HAClient

HISTORY_DAYS = 28
MIN_SAMPLES = 2
DEFAULT_LOAD_W = 400.0
SLOT = timedelta(minutes=15)


@dataclass(frozen=True, kw_only=True)
class LoadPoint:
    """Expected average household load for an interval, in W."""

    start: datetime
    end: datetime
    power: float


@dataclass(kw_only=True)
class LoadForecast:
    """Forecast plus how much history it is based on."""

    points: list[LoadPoint]
    history_hours: int
    quality: str  # "day_type", "all_days", "mixed" or "default"


async def fetch_hourly_means(
    client: HAClient, statistic_id: str, now: datetime, days: int = HISTORY_DAYS
) -> list[tuple[datetime, float]]:
    """Return (hour start, mean W) from the recorder's hourly statistics."""
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=(now - timedelta(days=days)).isoformat(),
        statistic_ids=[statistic_id],
        period="hour",
        types=["mean"],
        units={"power": "W"},
    )
    rows = []
    for row in result.get(statistic_id, []):
        if row.get("mean") is None:
            continue
        rows.append((datetime.fromtimestamp(row["start"] / 1000, UTC), float(row["mean"])))
    return rows


def _day_type(moment: datetime) -> str:
    return "weekend" if moment.weekday() >= 5 else "weekday"


def build_load_forecast(
    history: list[tuple[datetime, float]],
    start: datetime,
    hours: int,
    tz: tzinfo,
    default: float = DEFAULT_LOAD_W,
) -> LoadForecast:
    """Forecast the load per 15 minutes from hourly means in local time."""
    by_type: dict[tuple[str, int], list[float]] = {}
    by_hour: dict[int, list[float]] = {}
    for moment, mean in history:
        local = moment.astimezone(tz)
        by_type.setdefault((_day_type(local), local.hour), []).append(mean)
        by_hour.setdefault(local.hour, []).append(mean)
    overall = fmean(v for _, v in history) if history else default

    used: set[str] = set()
    points = []
    moment = start.astimezone(UTC)
    for _ in range(hours * 4):
        local = moment.astimezone(tz)
        same_type = by_type.get((_day_type(local), local.hour), [])
        same_hour = by_hour.get(local.hour, [])
        if len(same_type) >= MIN_SAMPLES:
            power, kind = fmean(same_type), "day_type"
        elif same_hour:
            power, kind = fmean(same_hour), "all_days"
        elif history:
            power, kind = overall, "all_days"
        else:
            power, kind = default, "default"
        used.add(kind)
        points.append(LoadPoint(start=moment.astimezone(tz), end=(moment + SLOT).astimezone(tz), power=power))
        moment += SLOT

    quality = used.pop() if len(used) == 1 else "mixed"
    return LoadForecast(points=points, history_hours=len(history), quality=quality)


def find_load_entity(roles: list[Any]) -> str | None:
    """Return the entity of the load_power role from resolved role values."""
    return next((r.entity_id for r in roles if r.role == "load_power" and r.entity_id), None)
