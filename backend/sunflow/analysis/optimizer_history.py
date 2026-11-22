"""Power of the optimizers of a string over a day."""

from datetime import datetime, timedelta
from typing import Any

from sunflow.devices.optimizers import Optimizer
from sunflow.ha import HAClient

from .day_series import energy_wh, slot_means


async def optimizer_day(
    client: HAClient, optimizers: list[Optimizer], start: datetime, end: datetime, step: timedelta
) -> list[dict[str, Any]]:
    """Power per slot and energy of each optimizer with a power entity."""
    entities = {o.id: o.entities["power"] for o in optimizers if "power" in o.entities}
    means = await slot_means(client, list(entities.values()), start, end, step)
    days = []
    for optimizer_id, entity_id in entities.items():
        values = [None if v is None else max(v, 0.0) for v in means[entity_id]]
        days.append({"id": optimizer_id, "power": values, "energy_wh": energy_wh(values, step)})
    return days
