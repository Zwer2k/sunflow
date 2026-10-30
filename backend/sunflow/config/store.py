"""Load and save the configuration, migrating older and repairing newer versions."""

from collections.abc import Callable
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Any

from pydantic import ValidationError

from .models import CONFIG_VERSION, SunflowConfig

_LOGGER = logging.getLogger(__name__)


def _backup_reserve_for_all_strategies(raw: dict[str, Any]) -> dict[str, Any]:
    """The "backup" strategy became a reserve that applies to every strategy."""
    optimizer = raw.get("optimizer") or {}
    if optimizer.get("strategy") == "backup":
        optimizer["strategy"] = "balanced"
        optimizer["backup_reserve"] = True
    override = raw.get("strategy_override") or {}
    if override.get("strategy") == "backup":
        raw["strategy_override"] = None
        optimizer["backup_reserve"] = True
    if optimizer:
        raw["optimizer"] = optimizer
    return raw


# Version n -> n + 1 on the raw JSON, before validation. Steps only ever get added:
# a step that renames or restructures a field goes here instead of a silent default
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {1: _backup_reserve_for_all_strategies}
# A configuration of a newer Sunflow can hold values this one does not know
MAX_REPAIRS = 20
_warned: set[str] = set()


class ConfigError(Exception):
    """The stored configuration cannot be read."""


def _repaired(raw: dict[str, Any], version: int) -> SunflowConfig:
    """Load a newer configuration after a downgrade; values this version rejects get their default."""
    for _ in range(MAX_REPAIRS):
        try:
            return SunflowConfig.model_validate(raw)
        except ValidationError as err:
            for error in err.errors():
                _drop(raw, error["loc"])
                field = ".".join(str(part) for part in error["loc"])
                # The configuration is loaded on every request
                if field not in _warned:
                    _warned.add(field)
                    _LOGGER.warning(
                        "Configuration of newer version %s: %s not supported by this version, using the default",
                        version,
                        field,
                    )
    return SunflowConfig.model_validate(raw)


def _drop(raw: Any, loc: tuple[int | str, ...]) -> None:
    """Remove the value at loc; a missing required field removes the object holding it."""
    parent, key, node = None, None, raw
    for part in loc:
        if (isinstance(node, dict) and part in node) or (
            isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node)
        ):
            parent, key, node = node, part, node[part]
        # Other parts name a union member or the missing field itself
    if parent is not None:
        parent.pop(key)


class ConfigStore:
    """Load and save the configuration as JSON in the data directory."""

    def __init__(self, data_dir: Path) -> None:
        """Initialize the store."""
        self._path = data_dir / "config.json"

    def load(self) -> SunflowConfig:
        """Return the stored configuration or an empty one, migrated to the current version."""
        if not self._path.exists():
            return SunflowConfig()
        try:
            raw = json.loads(self._path.read_text())
            version = raw.get("version", 1)
            if version < CONFIG_VERSION:
                return self._migrate(raw, version)
            if version > CONFIG_VERSION:
                return _repaired(raw, version)
            return SunflowConfig.model_validate(raw)
        except (ValueError, AttributeError, KeyError) as err:
            raise ConfigError(
                f"Konfiguration {self._path} ist ungültig ({err.__class__.__name__}); "
                f"die vorige Fassung liegt in {self._backup.name}"
            ) from err

    def _migrate(self, raw: dict[str, Any], version: int) -> SunflowConfig:
        # The original stays as config.v<n>.json, also for going back to that release
        kept = self._path.with_name(f"config.v{version}.json")
        if not kept.exists():
            shutil.copy2(self._path, kept)
        for step in range(version, CONFIG_VERSION):
            raw = MIGRATIONS[step](raw)
            raw["version"] = step + 1
        config = SunflowConfig.model_validate(raw)
        self.save(config)
        _LOGGER.info("Configuration migrated from version %s to %s (original in %s)", version, CONFIG_VERSION, kept.name)
        return config

    @property
    def _backup(self) -> Path:
        return self._path.with_suffix(".json.bak")

    def save(self, config: SunflowConfig) -> None:
        """Write the configuration atomically and durably, keeping the previous version."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w") as file:
            file.write(json.dumps(config.model_dump(mode="json"), indent=2))
            file.flush()
            # Without fsync a power loss on SD cards can leave an empty config.json
            os.fsync(file.fileno())
        if self._path.exists():
            shutil.copy2(self._path, self._backup)
        tmp.replace(self._path)
        directory = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
