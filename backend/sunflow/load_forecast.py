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
    excluded_hours: int = 0
    scaled_to_kwh: float | None = None
    separated_hours: int = 0


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


def subtract_loads(
    history: list[tuple[datetime, float]], loads: list[list[tuple[datetime, float]]]
) -> tuple[list[tuple[datetime, float]], int]:
    """Subtract separately measured loads (e.g. evcc loadpoints) hour by hour.

    Returns the base load history and how many hours were separated; hours
    without data of the separate loads stay unchanged.
    """
    by_hour: dict[datetime, float] = {}
    for series in loads:
        for moment, power in series:
            by_hour[moment] = by_hour.get(moment, 0.0) + power
    base = []
    separated = 0
    for moment, power in history:
        if moment in by_hour:
            base.append((moment, max(power - by_hour[moment], 0.0)))
            separated += 1
        else:
            base.append((moment, power))
    return base, separated


def _day_type(moment: datetime) -> str:
    return "weekend" if moment.weekday() >= 5 else "weekday"


# Typical household day (share of daily energy per local hour, sums to 1);
# used for hours without history, scaled to the configured daily consumption
TYPICAL_DAY = (
    0.025, 0.025, 0.025, 0.025, 0.025, 0.025, 0.035, 0.045, 0.045, 0.04, 0.04, 0.045,
    0.05, 0.045, 0.04, 0.04, 0.045, 0.055, 0.065, 0.065, 0.06, 0.055, 0.045, 0.035,
)


def build_load_forecast(
    history: list[tuple[datetime, float]],
    start: datetime,
    hours: int,
    tz: tzinfo,
    default: float = DEFAULT_LOAD_W,
    large_load_w: float | None = None,
    daily_kwh: float | None = None,
) -> LoadForecast:
    """Forecast the load per 15 minutes from hourly means in local time.

    Hours above large_load_w (e.g. EV charging) are left out: one-off large
    loads would otherwise repeat every day. With daily_kwh the daily shape is
    scaled to that total.
    """
    kept = [(m, v) for m, v in history if large_load_w is None or v <= large_load_w]
    by_type: dict[tuple[str, int], list[float]] = {}
    by_hour: dict[int, list[float]] = {}
    for moment, mean in kept:
        local = moment.astimezone(tz)
        by_type.setdefault((_day_type(local), local.hour), []).append(mean)
        by_hour.setdefault(local.hour, []).append(mean)
    overall = fmean(v for _, v in kept) if kept else None
    typical_w = (daily_kwh * 1000 if daily_kwh else default * 24)

    def hour_value(local: datetime) -> tuple[float, str]:
        same_type = by_type.get((_day_type(local), local.hour), [])
        same_hour = by_hour.get(local.hour, [])
        if len(same_type) >= MIN_SAMPLES:
            return fmean(same_type), "day_type"
        if same_hour:
            return fmean(same_hour), "all_days"
        if overall is not None:
            return overall, "all_days"
        return typical_w * TYPICAL_DAY[local.hour], "default"

    factor = 1.0
    if daily_kwh:
        # Scale so that one day of the (all days) shape adds up to daily_kwh
        reference = start.astimezone(tz).replace(minute=0, second=0, microsecond=0)
        day_wh = sum(hour_value(reference + timedelta(hours=h))[0] for h in range(24))
        factor = daily_kwh * 1000 / day_wh if day_wh > 0 else 1.0

    used: set[str] = set()
    points = []
    moment = start.astimezone(UTC)
    for _ in range(hours * 4):
        local = moment.astimezone(tz)
        power, kind = hour_value(local)
        used.add(kind)
        points.append(
            LoadPoint(start=local, end=(moment + SLOT).astimezone(tz), power=power * factor)
        )
        moment += SLOT

    quality = used.pop() if len(used) == 1 else "mixed"
    return LoadForecast(
        points=points,
        history_hours=len(history),
        quality=quality,
        excluded_hours=len(history) - len(kept),
        scaled_to_kwh=daily_kwh,
    )


def find_load_entity(roles: list[Any]) -> str | None:
    """Return the entity of the load_power role from resolved role values."""
    return next((r.entity_id for r in roles if r.role == "load_power" and r.entity_id), None)
