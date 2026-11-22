"""Power of each optimizer over a day from Home Assistant's statistics."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sunflow.devices.optimizers import Optimizer
from sunflow.devices.state_history import state_means
from sunflow.ha import HAClient, HAError

from .history import FIVE_MINUTE_LIMIT


def day_step(start: datetime, now: datetime) -> timedelta:
    """5 minutes while Home Assistant still keeps them, else hourly."""
    return timedelta(minutes=5) if start >= now - FIVE_MINUTE_LIMIT else timedelta(hours=1)


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
            values[index] = max(row["mean"], 0.0)
    return values


def energy_wh(values: list[float | None], step: timedelta) -> float | None:
    """Energy of the measured slots; None without any."""
    measured = [v for v in values if v is not None]
    return sum(measured) * step.total_seconds() / 3600 if measured else None


async def optimizer_day(
    client: HAClient, optimizers: list[Optimizer], start: datetime, end: datetime, step: timedelta
) -> list[dict[str, Any]]:
    """Power per slot and energy of each optimizer with a power entity."""
    entities = {o.id: o.entities["power"] for o in optimizers if "power" in o.entities}
    if not entities:
        return []
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=sorted(set(entities.values())),
        period="5minute" if step < timedelta(hours=1) else "hour",
        types=["mean"],
        units={"power": "W"},
    )
    for entity_id in sorted(set(entities.values()) - {k for k, rows in result.items() if rows}):
        # Without state_class there are no statistics: means from the recorded states
        try:
            means = await state_means(client, entity_id, start, end, step)
        except HAError:
            continue
        result[entity_id] = [{"start": moment.timestamp() * 1000, "mean": value} for moment, value in means]
    days = []
    for optimizer_id, entity_id in entities.items():
        values = series_by_slot(result.get(entity_id, []), start, end, step)
        days.append({"id": optimizer_id, "power": values, "energy_wh": energy_wh(values, step)})
    return days
