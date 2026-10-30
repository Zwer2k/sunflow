"""Poll directly connected devices and show their values as snapshot entities."""

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from sunflow.devices.discovery import DIRECT_DOMAIN, Snapshot

from .transports import HttpTransport, Transport, TransportError, make_transport

_LOGGER = logging.getLogger(__name__)

DOMAIN = DIRECT_DOMAIN
ENTRY = {"entry_id": DIRECT_DOMAIN, "domain": DIRECT_DOMAIN, "title": "Direkt angebunden", "state": "loaded"}
DEVICE_PREFIX = "direct_"
DEFAULT_POLL = timedelta(seconds=10)


def direct_device_id(device_id: str) -> str:
    """Pseudo Home Assistant device id of a directly connected device."""
    return f"{DEVICE_PREFIX}{device_id}"


def parse_duration(text: str | None, default: timedelta) -> timedelta:
    """Duration of a profile ("10s", "5min", "1h")."""
    if not text:
        return default
    for suffix, unit in (("min", "minutes"), ("s", "seconds"), ("h", "hours")):
        if text.endswith(suffix):
            return timedelta(**{unit: int(text.removesuffix(suffix))})
    raise ValueError(f"Bad duration {text}")


@dataclass(kw_only=True)
class DirectDevice:
    """A configured device with its connection and last read values."""

    id: str
    name: str
    profile: dict[str, Any]
    transport: Transport
    address: tuple[str, int | None, int | None]
    values: dict[str, float | None] = field(default_factory=dict)
    # Last value per write target, for write=if_differs (targets are not read back)
    values_written: dict[str, Any] = field(default_factory=dict)
    read_at: datetime | None = None
    error: str | None = None

    @property
    def ha_device_id(self) -> str:
        """Pseudo device id used in the snapshot."""
        return direct_device_id(self.id)

    @property
    def points(self) -> dict[str, dict[str, Any]]:
        """Readable values of the profile: key -> register or JSON path."""
        return self.profile["connection"].get("points", {})

    async def poll(self) -> None:
        """Read every point; a failed point is unknown, the others stay readable."""
        if isinstance(self.transport, HttpTransport):
            self.transport.new_poll()
        errors = []
        for key, point in self.points.items():
            try:
                self.values[key] = await self.transport.read(point)
            except TransportError as err:
                self.values[key] = None
                errors.append(f"{key}: {err}")
        self.read_at = datetime.now(UTC)
        self.error = "; ".join(errors) or None

    async def write(self, spec: dict[str, Any], value: Any) -> None:
        """Run a write step of the profile."""
        await self.transport.write(spec, value)

    def entities(self) -> list[dict[str, Any]]:
        """Registry entries of the pseudo entities, one per point."""
        return [
            {
                "entity_id": self.entity_id(key),
                "unique_id": f"{self.id}_{key}",
                "platform": DOMAIN,
                "device_id": self.ha_device_id,
                "config_entry_id": DOMAIN,
                "disabled_by": None,
            }
            for key in self.points
        ]

    def entity_id(self, key: str) -> str:
        """Entity id of a point."""
        return f"sensor.{DOMAIN}_{self.id[:8]}_{key}"

    def states(self) -> dict[str, dict[str, Any]]:
        """Current states of the pseudo entities."""
        changed = (self.read_at or datetime.now(UTC)).isoformat()
        result = {}
        for key, point in self.points.items():
            value = self.values.get(key)
            attributes = {"friendly_name": f"{self.name} {key}"}
            if unit := point.get("unit"):
                attributes["unit_of_measurement"] = unit
            if device_class := point.get("device_class"):
                attributes["device_class"] = device_class
            result[self.entity_id(key)] = {
                "entity_id": self.entity_id(key),
                "state": "unknown" if value is None else str(float(round(value, 6))),
                "attributes": attributes,
                "last_changed": changed,
                "last_updated": changed,
            }
        return result


class DirectHub:
    """All directly connected devices; polls them in the background."""

    def __init__(self) -> None:
        """Initialize an empty hub."""
        self.devices: dict[str, DirectDevice] = {}
        self._task: asyncio.Task[None] | None = None

    async def configure(self, devices: list[tuple[Any, dict[str, Any]]]) -> None:
        """Set the devices: (device config with connection, profile); others are closed."""
        wanted = {}
        for config, profile in devices:
            connection = config.connection
            address = (connection.host, connection.port, connection.unit_id)
            current = self.devices.get(config.id)
            if current is not None and current.profile is profile and current.address == address:
                current.name = config.name
                wanted[config.id] = current
                continue
            wanted[config.id] = DirectDevice(
                id=config.id,
                name=config.name,
                profile=profile,
                transport=make_transport(profile["connection"], *address),
                address=address,
            )
        for device_id, device in self.devices.items():
            if wanted.get(device_id) is not device:
                await device.transport.close()
        self.devices = wanted

    async def sync(self, config: Any, library: Any) -> None:
        """Follow the configuration: every device with a connection and a known profile (disabled ones are not in devices)."""
        await self.configure(
            [
                (d, library.profiles[d.profile])
                for d in config.devices
                if d.connection is not None and d.profile in library.profiles
            ]
        )

    def by_ha_device(self, ha_device_id: str) -> DirectDevice | None:
        """Device behind a pseudo device id."""
        return next((d for d in self.devices.values() if d.ha_device_id == ha_device_id), None)

    async def poll(self) -> None:
        """Read all devices once."""
        for device in list(self.devices.values()):
            await device.poll()

    def start(self, source: Callable[[], tuple[Any, Any]]) -> None:
        """Poll in the background; source returns (config, profile library), followed each cycle."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(source))

    async def stop(self) -> None:
        """Stop polling and close all connections."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for device in self.devices.values():
            await device.transport.close()

    async def _run(self, source: Callable[[], tuple[Any, Any]]) -> None:
        while True:
            try:
                await self.sync(*source())
                await self.poll()
            except Exception:
                _LOGGER.exception("Polling direct devices failed")
            interval = min(
                (parse_duration(d.profile["connection"].get("poll"), DEFAULT_POLL) for d in self.devices.values()),
                default=DEFAULT_POLL,
            )
            await asyncio.sleep(interval.total_seconds())

    def contribute(self, snapshot: Snapshot) -> Snapshot:
        """Snapshot with the pseudo devices, entities and states added."""
        if not self.devices:
            return snapshot
        devices = [
            {
                "id": d.ha_device_id,
                "name": d.name,
                "manufacturer": d.profile["connection"].get("manufacturer"),
                "model": d.profile["profile"],
                "config_entries": [DOMAIN],
                "via_device_id": None,
            }
            for d in self.devices.values()
        ]
        entities = [e for d in self.devices.values() for e in d.entities()]
        states = {k: v for d in self.devices.values() for k, v in d.states().items()}
        return replace(
            snapshot,
            entries=[*snapshot.entries, ENTRY],
            integration_names={**snapshot.integration_names, DOMAIN: "Direkt angebunden (Modbus/HTTP)"},
            devices=[*snapshot.devices, *devices],
            entities=[*snapshot.entities, *entities],
            states={**snapshot.states, **states},
        )
