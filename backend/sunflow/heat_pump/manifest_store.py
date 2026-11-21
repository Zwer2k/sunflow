"""The last manifest loaded per device, kept in heat_pump_manifests.json.

Small and discardable: a missing or broken file only means the next load counts as new.
Written only when something changed (manifest, firmware version or a note), atomically.
"""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

FILE_NAME = "heat_pump_manifests.json"
FORMAT = 1


class ManifestStore:
    """Per Sunflow device id: manifest, hash, load time, firmware version and notes already made."""

    def __init__(self, data_dir: Path) -> None:
        """Read the file; anything unreadable is dropped."""
        self._path = data_dir / FILE_NAME
        self._devices: dict[str, dict[str, Any]] = self._read()

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as err:
            _LOGGER.warning("%s unreadable, manifests are loaded anew: %s", self._path.name, err)
            return {}
        devices = raw.get("devices") if isinstance(raw, dict) else None
        if not isinstance(devices, dict):
            return {}
        return {
            device_id: entry
            for device_id, entry in devices.items()
            if isinstance(entry, dict) and isinstance(entry.get("hash"), str) and isinstance(entry.get("manifest"), dict)
        }

    def get(self, device_id: str) -> dict[str, Any] | None:
        """The stored entry: manifest, hash, loaded_at, sw_version, notes."""
        return self._devices.get(device_id)

    def put(
        self, device_id: str, manifest: dict[str, Any], digest: str, sw_version: str | None, now: datetime
    ) -> bool:
        """Store a loaded manifest; True if it differs from the stored one (or none was stored)."""
        entry = self._devices.get(device_id)
        changed = entry is None or entry["hash"] != digest
        if not changed and entry.get("sw_version") == sw_version:
            return False
        self._devices[device_id] = {
            "manifest": manifest,
            "hash": digest,
            "loaded_at": now.isoformat() if changed else entry.get("loaded_at", now.isoformat()),
            "sw_version": sw_version,
            "notes": (entry or {}).get("notes", {}) if isinstance((entry or {}).get("notes"), dict) else {},
        }
        self._write()
        return changed

    def note(self, device_id: str, name: str) -> Any:
        """A note made about the device (e.g. the compatibility last reported)."""
        return ((self._devices.get(device_id) or {}).get("notes") or {}).get(name)

    def set_note(self, device_id: str, name: str, value: Any) -> None:
        """Remember a note; written only when it changed and a manifest is stored."""
        entry = self._devices.get(device_id)
        if entry is None or self.note(device_id, name) == value:
            return
        entry.setdefault("notes", {})[name] = value
        self._write()

    def prune(self, keep: set[str]) -> None:
        """Drop entries of devices that no longer exist."""
        if gone := set(self._devices) - keep:
            for device_id in gone:
                del self._devices[device_id]
            self._write()

    def _write(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w") as file:
                file.write(json.dumps({"format": FORMAT, "devices": self._devices}, ensure_ascii=False))
                file.flush()
                os.fsync(file.fileno())
            tmp.replace(self._path)
            directory = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as err:
            _LOGGER.warning("%s not written: %s", self._path.name, err)
