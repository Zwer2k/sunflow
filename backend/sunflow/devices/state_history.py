"""Means over time from the recorded states of a sensor without statistics (no state_class).

Home Assistant keeps the states for its purge period (default 10 days, often longer),
also for sensors it keeps no statistics for. Each state holds until the next one;
the mean of an interval weights them by how long they held.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sunflow.ha import HAClient

from .resolver import normalize

# An interval counts once states cover this share of it (a sensor offline leaves gaps)
MIN_COVERAGE = 0.5


def means_from_states(
    rows: list[dict[str, Any]], start: datetime, end: datetime, step: timedelta
) -> list[tuple[datetime, float]]:
    """Time weighted means per interval of minimal history rows ({"s", "lu"/"lc", first one "a"})."""
    unit = (rows[0].get("a") or {}).get("unit_of_measurement") if rows else None
    changes = []
    for row in rows:
        moment = datetime.fromtimestamp(row.get("lu") or row.get("lc") or 0, UTC)
        value, _ = normalize(row.get("s"), unit, None)
        changes.append((max(moment, start), value))
    if not changes:
        return []
    sums: dict[datetime, list[float]] = {}
    for (since, value), (until, _) in zip(changes, [*changes[1:], (end, None)], strict=True):
        if value is None:
            continue
        moment = since
        while moment < min(until, end):
            bucket = start + ((moment - start) // step) * step
            edge = min(until, end, bucket + step)
            seconds = (edge - moment).total_seconds()
            total = sums.setdefault(bucket, [0.0, 0.0])
            total[0] += value * seconds
            total[1] += seconds
            moment = edge
    return [
        (bucket, weighted / seconds)
        for bucket, (weighted, seconds) in sorted(sums.items())
        if seconds >= MIN_COVERAGE * step.total_seconds()
    ]


async def state_means(
    client: HAClient, entity_id: str, start: datetime, end: datetime, step: timedelta
) -> list[tuple[datetime, float]]:
    """Means per interval from the recorder's state history of one entity."""
    result = await client.command(
        "history/history_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        entity_ids=[entity_id],
        minimal_response=True,
        # The first state brings the unit
        no_attributes=False,
        significant_changes_only=False,
    )
    return means_from_states(result.get(entity_id, []), start, end, step)
