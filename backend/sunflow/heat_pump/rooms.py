"""The room temperature of the house: weighted mean of the room sensors (design/heat_pump.md, 6.1).

A room counts while its sensor is available and reported within ROOM_STALE_AFTER. When a sensor last
reported comes from last_reported, read per sensor over REST (`reported_times`): the states Sunflow
keeps by state_changed events do not see a sensor reporting an unchanged value. Without
last_reported (or when reading it failed) there is no staleness check; last_updated is never used
for it, or a room with a steady temperature would drop out. Meant to be shared by the correction,
later the ventilation detection and the house model.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
from typing import Any, Protocol

from sunflow.config.models import Room
from sunflow.devices.resolver import UNAVAILABLE_STATES, normalize

_LOGGER = logging.getLogger(__name__)

# A room whose sensor has not reported for longer does not count
ROOM_STALE_AFTER = timedelta(hours=2)


class StateReader(Protocol):
    """What reading one state needs (HAClient.get_state)."""

    async def get_state(self, entity_id: str) -> dict[str, Any] | None: ...


def _time(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


async def reported_times(client: StateReader, entity_ids: Iterable[str]) -> dict[str, datetime | None]:
    """When each sensor last reported (last_reported); None where unknown or the request failed."""
    ids = list(dict.fromkeys(entity_ids))

    async def one(entity_id: str) -> datetime | None:
        try:
            state = await client.get_state(entity_id)
        except Exception as err:  # noqa: BLE001 - one sensor must not stop the others
            _LOGGER.debug("last_reported of %s not read: %s", entity_id, err)
            return None
        return _time((state or {}).get("last_reported"))

    return dict(zip(ids, await asyncio.gather(*(one(e) for e in ids)), strict=True))


def _celsius(state: dict[str, Any] | None) -> float | None:
    if not state or state.get("state") in UNAVAILABLE_STATES:
        return None
    unit = (state.get("attributes") or {}).get("unit_of_measurement") or "°C"
    value, _ = normalize(state.get("state"), unit, None)
    return value if value is not None and math.isfinite(value) else None


@dataclass(frozen=True, kw_only=True)
class RoomTemperature:
    """The weighted room temperature (None if no room counts) and which rooms it is made of."""

    value_c: float | None
    used: tuple[str, ...] = ()
    # Rooms left out: unavailable or no number; not reported for too long
    missing: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()


def room_temperature(
    rooms: Iterable[Room],
    states: dict[str, dict[str, Any]],
    reported: dict[str, datetime | None],
    now: datetime,
) -> RoomTemperature:
    """Weighted mean of the rooms that are available and reported recently enough."""
    total = weight = 0.0
    used: list[str] = []
    missing: list[str] = []
    stale: list[str] = []
    for room in rooms:
        value = _celsius(states.get(room.entity_id))
        if value is None:
            missing.append(room.entity_id)
            continue
        if (at := reported.get(room.entity_id)) is not None and now - at > ROOM_STALE_AFTER:
            stale.append(room.entity_id)
            continue
        total += value * room.weight
        weight += room.weight
        used.append(room.entity_id)
    return RoomTemperature(
        value_c=total / weight if weight > 0 else None, used=tuple(used), missing=tuple(missing), stale=tuple(stale)
    )
