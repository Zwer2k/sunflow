"""The room temperature of the house: weighted mean of the room sensors (design/heat_pump.md, 6).

A room counts while its sensor is available and its device reported within ROOM_STALE_AFTER. When it
last reported comes from last_reported, read per entity over REST (`reported_times`): the states Sunflow
keeps by state_changed events do not see a sensor reporting an unchanged value. Some integrations (MQTT,
e.g. Zigbee2MQTT) do not even write an unchanged value again, so last_reported of a steady temperature
stands still while the device keeps reporting its humidity: the latest last_reported of the sensor and
the other sensor entities of its device counts (`device_sensors`). Without last_reported (or when reading
it failed) there is no staleness check; last_updated is never used for it, or a room with a steady
temperature would drop out. Meant to be shared by the correction,
later the ventilation detection and the house model.
"""

import asyncio
from collections.abc import Collection, Iterable
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


def device_sensors(registry: Iterable[dict[str, Any]], entity_ids: Iterable[str]) -> dict[str, list[str]]:
    """Per room sensor the other enabled sensor entities of its device (entity registry); empty without a device."""
    entries = [e for e in registry if isinstance(e, dict) and isinstance(e.get("entity_id"), str)]
    by_device: dict[str, list[str]] = {}
    for entry in entries:
        if entry.get("device_id") and entry["entity_id"].startswith("sensor.") and not entry.get("disabled_by"):
            by_device.setdefault(entry["device_id"], []).append(entry["entity_id"])
    devices = {e["entity_id"]: e.get("device_id") for e in entries}
    result: dict[str, list[str]] = {}
    for entity_id in entity_ids:
        device = devices.get(entity_id)
        result[entity_id] = sorted(e for e in by_device.get(device, []) if e != entity_id) if device else []
    return result


async def reported_times(
    client: StateReader, entity_ids: Iterable[str], siblings: dict[str, list[str]] | None = None,
) -> dict[str, datetime | None]:
    """When each sensor or its device last reported (latest last_reported of the sensor and its siblings);
    None where unknown or every request failed."""
    rooms = list(dict.fromkeys(entity_ids))
    siblings = siblings or {}
    ids = list(dict.fromkeys([*rooms, *(e for r in rooms for e in siblings.get(r, []))]))

    async def one(entity_id: str) -> datetime | None:
        try:
            state = await client.get_state(entity_id)
        except Exception as err:  # noqa: BLE001 - one sensor must not stop the others
            _LOGGER.debug("last_reported of %s not read: %s", entity_id, err)
            return None
        return _time((state or {}).get("last_reported"))

    times = dict(zip(ids, await asyncio.gather(*(one(e) for e in ids)), strict=True))
    result: dict[str, datetime | None] = {}
    for room in rooms:
        known = [at for e in (room, *siblings.get(room, [])) if (at := times.get(e)) is not None]
        result[room] = max(known) if known else None
    return result


def celsius(state: dict[str, Any] | None) -> float | None:
    """The temperature a room sensor shows in °C; None if unavailable or no number."""
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
    # Rooms left out: unavailable or no number; not reported for too long; being aired
    missing: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    ventilated: tuple[str, ...] = ()


def room_temperature(
    rooms: Iterable[Room],
    states: dict[str, dict[str, Any]],
    reported: dict[str, datetime | None],
    now: datetime,
    ventilated: Collection[str] = (),
) -> RoomTemperature:
    """Weighted mean of the rooms that are available, reported recently enough and are not being aired."""
    total = weight = 0.0
    used: list[str] = []
    missing: list[str] = []
    stale: list[str] = []
    aired: list[str] = []
    for room in rooms:
        value = celsius(states.get(room.entity_id))
        if value is None:
            missing.append(room.entity_id)
            continue
        if (at := reported.get(room.entity_id)) is not None and now - at > ROOM_STALE_AFTER:
            stale.append(room.entity_id)
            continue
        if room.entity_id in ventilated:
            aired.append(room.entity_id)
            continue
        total += value * room.weight
        weight += room.weight
        used.append(room.entity_id)
    return RoomTemperature(
        value_c=total / weight if weight > 0 else None, used=tuple(used), missing=tuple(missing), stale=tuple(stale),
        ventilated=tuple(aired),
    )
