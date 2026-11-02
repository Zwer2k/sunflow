"""Reloading the Home Assistant integration of a device that delivers no values.

Only for devices where the user allowed it (DeviceConfig.reload_after_min). Some integrations do
not reconnect by themselves, e.g. a Modbus integration after Home Assistant restarted while the
inverter was busy; reloading the config entry makes them connect again. Nothing is written to
the device itself.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.resolver import UNAVAILABLE_STATES, preview
from sunflow.ha import HAClient, HAError
from sunflow.i18n import t

from .journal import ControlJournal

_LOGGER = logging.getLogger(__name__)

# The integration needs time to connect after a reload before it is judged again
RELOAD_PAUSE = timedelta(minutes=30)
MAX_RELOADS_PER_DAY = 6
# A gap in the checks (App or Home Assistant away) starts the silence anew
MAX_CHECK_GAP = timedelta(minutes=3)


@dataclass
class _Watch:
    silent_since: datetime | None = None
    last_reload: datetime | None = None
    reloads: list[datetime] = field(default_factory=list)


def silent(profile: dict[str, Any], device: DeviceConfig, snapshot: Snapshot) -> bool:
    """Every measured value of the device is unavailable or unknown (0 W at night is a value)."""
    overrides = {k: o.model_dump() for k, o in device.role_overrides.items()}
    roles = [r for r in preview(profile, snapshot, device.ha_device_id, overrides) if r.entity_id]
    return bool(roles) and all(r.raw_state is None or r.raw_state in UNAVAILABLE_STATES for r in roles)


def config_entry(snapshot: Snapshot, ha_device_id: str) -> str | None:
    """The integration entry a device belongs to."""
    device = next((d for d in snapshot.devices if d.get("id") == ha_device_id), None)
    if device is None:
        return None
    return device.get("primary_config_entry") or next(iter(device.get("config_entries") or []), None)


class ReloadWatchdog:
    """Reloads the integration of an allowed device after it stayed silent for its minutes."""

    def __init__(
        self, client: HAClient, profiles: Callable[[], dict[str, dict[str, Any]]], journal: ControlJournal | None = None
    ) -> None:
        """Initialize."""
        self._client = client
        self._profiles = profiles
        self._journal = journal
        self._watches: dict[str, _Watch] = {}
        self._last_check: datetime | None = None

    async def check(self, config: SunflowConfig, snapshot: Snapshot, now: datetime, busy: set[str]) -> None:
        """One check; busy: devices that must not be disturbed (e.g. a running device test)."""
        if self._last_check is not None and now - self._last_check > MAX_CHECK_GAP:
            for watch in self._watches.values():
                watch.silent_since = None
        self._last_check = now
        for device in config.devices:
            watch = self._watches.setdefault(device.id, _Watch())
            if (
                device.reload_after_min is None
                or device.connection is not None
                or device.id in busy
                or not silent(self._profiles()[device.profile], device, snapshot)
            ):
                watch.silent_since = None
                continue
            watch.silent_since = watch.silent_since or now
            watch.reloads = [r for r in watch.reloads if now - r < timedelta(days=1)]
            if (
                now - watch.silent_since < timedelta(minutes=device.reload_after_min)
                or (watch.last_reload is not None and now - watch.last_reload < RELOAD_PAUSE)
                or len(watch.reloads) >= MAX_RELOADS_PER_DAY
                or (entry_id := config_entry(snapshot, device.ha_device_id)) is None
            ):
                continue
            await self._reload(device, entry_id, watch, now)

    async def _reload(self, device: DeviceConfig, entry_id: str, watch: _Watch, now: datetime) -> None:
        minutes = int((now - watch.silent_since).total_seconds() // 60) if watch.silent_since else 0
        _LOGGER.warning("%s delivered no values for %s min: reloading its integration", device.name, minutes)
        watch.last_reload = now
        watch.reloads.append(now)
        try:
            await self._client.call_service("homeassistant", "reload_config_entry", {"entry_id": entry_id})
            reason = t("Integration neu geladen: seit {minutes} min keine Werte", minutes=minutes)
        except HAError as err:
            _LOGGER.warning("Reloading the integration of %s failed: %s", device.name, err)
            reason = t("Integration neu laden fehlgeschlagen: {error}", error=err)
        if self._journal is not None:
            self._journal.note(device.id, device.name, "reload", reason, now)

    def status(self) -> dict[str, dict[str, Any]]:
        """Per device: silent since, last reload and reloads in the last 24 hours."""
        return {
            device_id: {
                "silent_since": watch.silent_since.isoformat() if watch.silent_since else None,
                "last_reload": watch.last_reload.isoformat() if watch.last_reload else None,
                "reloads_today": len(watch.reloads),
            }
            for device_id, watch in self._watches.items()
        }
