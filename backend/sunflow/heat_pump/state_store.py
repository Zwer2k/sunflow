"""Runtime state of the flow temperature control, kept in heat_pump_state.json (design/heat_pump.md, 10).

Small and discardable: a missing or broken file only means starting afresh (no base, correction 0).
Unknown keys are ignored. Written only when something changed, atomically.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

FILE_NAME = "heat_pump_state.json"
FORMAT = 1


class HeatPumpStateStore:
    """The house (last setpoint seen, setpoint change followed) and per device base and corrections."""

    def __init__(self, data_dir: Path) -> None:
        """Read the file; anything unreadable is dropped."""
        self._path = data_dir / FILE_NAME
        self.house: dict[str, Any] = {}
        self.devices: dict[str, dict[str, Any]] = {}
        self._written: str | None = None
        self._read()

    def _read(self) -> None:
        try:
            raw = json.loads(self._path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as err:
            _LOGGER.warning("%s unreadable, starting afresh: %s", self._path.name, err)
            return
        if not isinstance(raw, dict):
            return
        if isinstance(house := raw.get("house"), dict):
            self.house = house
        if isinstance(devices := raw.get("devices"), dict):
            self.devices = {k: v for k, v in devices.items() if isinstance(v, dict)}
        self._written = self._dump()

    def _dump(self) -> str:
        return json.dumps({"format": FORMAT, "house": self.house, "devices": self.devices}, ensure_ascii=False, sort_keys=True)

    def save(self) -> None:
        """Write when the content changed since the last write."""
        content = self._dump()
        if content == self._written:
            return
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            tmp.replace(self._path)
            directory = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._written = content
        except OSError as err:
            _LOGGER.warning("%s not written: %s", self._path.name, err)
