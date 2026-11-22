"""Means per slot over a local day from Home Assistant's statistics (or recorded states)."""

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from sunflow.devices.state_history import state_means
from sunflow.ha import HAClient, HAError

from .history import FIVE_MINUTE_LIMIT

# Statistics come in these units whatever the sensor reports
STATISTIC_UNITS = {"power": "W", "voltage": "V", "electric_current": "A", "temperature": "°C"}


def day_step(start: datetime, now: datetime) -> timedelta:
    """5 minutes while Home Assistant still keeps them, else hourly."""
    return timedelta(minutes=5) if start >= now - FIVE_MINUTE_LIMIT else timedelta(hours=1)


def day_window(day: date, tz: tzinfo, now: datetime) -> tuple[datetime, datetime, timedelta] | None:
    """Start, end and step of a local day; None for a day that has not begun."""
    start = datetime.combine(day, time(), tz).astimezone(UTC)
    if start > now:
        return None
    end = datetime.combine(day + timedelta(days=1), time(), tz).astimezone(UTC)
    return start, end, day_step(start, now)


def series_by_slot(
    rows: list[dict[str, Any]], start: datetime, end: datetime, step: timedelta
) -> list[float | None]:
    """Means per slot from start to end; slots without a row stay None."""
    count = -(-(end - start) // step)
    values: list[float | None] = [None] * count
    for row in rows:
        if row.get("mean") is None:
            continue
        index = (datetime.fromtimestamp(row["start"] / 1000, UTC) - start) // step
        if 0 <= index < count:
            values[index] = row["mean"]
    return values


def energy_wh(values: list[float | None], step: timedelta) -> float | None:
    """Energy of the measured slots; None without any."""
    measured = [v for v in values if v is not None]
    return sum(measured) * step.total_seconds() / 3600 if measured else None


async def slot_means(
    client: HAClient, entity_ids: list[str], start: datetime, end: datetime, step: timedelta
) -> dict[str, list[float | None]]:
    """Mean per slot of each entity: statistics, or the recorded states of sensors without them."""
    if not entity_ids:
        return {}
    ids = sorted(set(entity_ids))
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=ids,
        period="5minute" if step < timedelta(hours=1) else "hour",
        types=["mean"],
        units=STATISTIC_UNITS,
    )
    for entity_id in sorted(set(ids) - {k for k, rows in result.items() if rows}):
        # Without state_class there are no statistics: means from the recorded states
        try:
            means = await state_means(client, entity_id, start, end, step)
        except HAError:
            continue
        result[entity_id] = [{"start": moment.timestamp() * 1000, "mean": value} for moment, value in means]
    return {entity_id: series_by_slot(result.get(entity_id, []), start, end, step) for entity_id in ids}


def combine(series: list[list[float | None]], how: str) -> list[float | None]:
    """Slot by slot max, min or difference (first minus second) of several series."""
    if not series:
        return []
    combined: list[float | None] = []
    for values in zip(*series, strict=True):
        known = [v for v in values if v is not None]
        if how == "difference":
            combined.append(values[0] - values[1] if None not in values[:2] else None)
        else:
            combined.append((max if how == "max" else min)(known) if known else None)
    return combined
