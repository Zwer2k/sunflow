"""Load and save the configuration, migrating older and repairing newer versions."""

from collections.abc import Callable, Collection
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Any

from pydantic import ValidationError

from sunflow.i18n import t

from .models import CONFIG_VERSION, DeviceConfig, FlexSettings, SunflowConfig, VehicleDetection

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


def _relations_out_of_devices(raw: dict[str, Any]) -> dict[str, Any]:
    """Devices and vehicles keep what they are; plans, links and earnings move to own sections."""
    relations = {"flex": "planning", "vehicle_detection": "vehicle_detection", "battery_modules": "battery_modules",
                 "feed_in": "feed_in", "measured_by": "measured_by"}
    # Defaults stay implicit: only what was actually set moves
    defaults = {"flex": FlexSettings, "vehicle_detection": VehicleDetection}

    def is_default(field: str, value: Any) -> bool:
        model = defaults.get(field)
        return model is not None and model.model_validate(value) == model()

    for device in [*(raw.get("devices") or []), *(raw.get("disabled_devices") or [])]:
        for field, section in relations.items():
            value = device.pop(field, None)
            if value is not None and not is_default(field, value):
                raw.setdefault(section, {})[device["id"]] = value
    for vehicle in raw.get("vehicles") or []:
        card, flex = vehicle.pop("card", None), vehicle.pop("flex", None) or {}
        for wallbox_id in vehicle.pop("wallboxes", None) or []:
            raw.setdefault("charging", []).append(
                {"vehicle_id": vehicle["id"], "wallbox_id": wallbox_id, "card": card, "flex": flex}
            )
    return raw


def _cards_with_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """One card per vehicle and wallbox became a list of cards, each with its own settings."""
    for profile in raw.get("charging") or []:
        card = profile.pop("card", None)
        if card and not profile.get("cards"):
            # Until now the card charged with the vehicle's settings at this wallbox
            profile["cards"] = [{"number": card, "flex": dict(profile.get("flex") or {})}]
    return raw


# The former heat pump (measured, SG-Ready and the like); replaced by the flow temperature control
OLD_HEAT_PUMP = "generic/heat_pump"


def _without_ids(raw: dict[str, Any], ids: set[str]) -> None:
    """Drop every reference to these device ids: relations, priorities, display and control settings."""

    def drop(value: Any, *path: str) -> Any:
        for key in path[:-1]:
            value = value.get(key) if isinstance(value, dict) else None
        return value.get(path[-1]) if isinstance(value, dict) else None

    def owned(key: str) -> bool:
        # Per device, per string ("<id>:<string>") or per string index
        return key in ids or key.split(":", 1)[0] in ids

    for section in ("planning", "vehicle_detection", "battery_modules", "battery_costs", "battery_overrides", "feed_in",
                    "overrides", "priority_conditions", "selected_vehicles"):
        if isinstance(mapping := raw.get(section), dict):
            raw[section] = {k: v for k, v in mapping.items() if k not in ids}
    if isinstance(conditions := raw.get("priority_conditions"), dict):
        raw["priority_conditions"] = {
            k: [c for c in v if not (isinstance(c, dict) and c.get("device_id") in ids)] for k, v in conditions.items()
        }
    if isinstance(measured := raw.get("measured_by"), dict):
        raw["measured_by"] = {
            k: v for k, v in measured.items() if k not in ids and not (isinstance(v, dict) and v.get("device_id") in ids)
        }
    if isinstance(sources := raw.get("measurement_sources"), dict):
        raw["measurement_sources"] = {k: v for k, v in sources.items() if v not in ids}
    if isinstance(priorities := raw.get("priorities"), list):
        raw["priorities"] = [p for p in priorities if p not in ids]
    if isinstance(charging := raw.get("charging"), list):
        raw["charging"] = [c for c in charging if not (isinstance(c, dict) and c.get("wallbox_id") in ids)]
    for plane in raw.get("forecast_planes") or []:
        if isinstance(plane, dict):
            if plane.get("device_id") in ids:
                plane["device_id"] = None
            plane["strings"] = [s for s in plane.get("strings") or [] if not owned(s)]
    if isinstance(switches := drop(raw, "control", "devices"), dict):
        raw["control"]["devices"] = {k: v for k, v in switches.items() if k not in ids}
    if isinstance(controls := drop(raw, "display", "live_controls"), list):
        raw["display"]["live_controls"] = [d for d in controls if d not in ids]
    if isinstance(scene := drop(raw, "display", "scene"), dict):
        for key in ("roofs", "consumers"):
            if isinstance(places := scene.get(key), dict):
                scene[key] = {k: v for k, v in places.items() if not owned(k)}
        if isinstance(inverters := scene.get("inverters"), list):
            scene["inverters"] = [i for i in inverters if i not in ids]
    if isinstance(optimizers := raw.get("optimizer_settings"), dict):
        if optimizers.get("tigo_plant") in ids:
            optimizers["tigo_plant"] = None
        if isinstance(layouts := optimizers.get("layouts"), dict):
            optimizers["layouts"] = {k: v for k, v in layouts.items() if not owned(k)}
        for placement in optimizers.get("placements") or []:
            if isinstance(placement, dict) and placement.get("string") and owned(placement["string"]):
                placement["string"] = None


def _old_heat_pumps_removed(raw: dict[str, Any]) -> dict[str, Any]:
    """The former heat pump devices go, with everything that refers to them; they are added anew."""
    ids = set()
    for section in ("devices", "disabled_devices"):
        kept = []
        for device in raw.get(section) or []:
            if isinstance(device, dict) and device.get("profile") == OLD_HEAT_PUMP:
                ids.add(device.get("id"))
                _LOGGER.warning(
                    "Heat pump '%s' removed: new heat pump control, please add the device again", device.get("name")
                )
            else:
                kept.append(device)
        if section in raw:
            raw[section] = kept
    if ids:
        _without_ids(raw, ids)
    return raw


# Version n -> n + 1 on the raw JSON, before validation. Steps only ever get added:
# a step that renames or restructures a field goes here instead of a silent default
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _backup_reserve_for_all_strategies,
    2: _relations_out_of_devices,
    3: _cards_with_settings,
    4: _old_heat_pumps_removed,
}
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
    """Load and save the configuration as JSON in the data directory.

    With known_profiles, devices whose profile is not loaded (e.g. one of a newer version after a
    downgrade) are left out of the loaded configuration and written back unchanged on save.
    """

    def __init__(self, data_dir: Path, known_profiles: Callable[[], Collection[str]] | None = None) -> None:
        """Initialize the store."""
        self._path = data_dir / "config.json"
        self.known_profiles = known_profiles

    def load(self) -> SunflowConfig:
        """Return the stored configuration or an empty one, migrated to the current version."""
        return self._set_aside(self._load())

    def _set_aside(self, config: SunflowConfig) -> SunflowConfig:
        """Devices with an unknown profile: kept for saving, but no other code sees them."""
        if self.known_profiles is None:
            return config
        known = self.known_profiles()
        for section in ("devices", "disabled_devices"):
            devices: list[DeviceConfig] = getattr(config, section)
            unknown = [d for d in devices if d.profile not in known]
            if not unknown:
                continue
            setattr(config, section, [d for d in devices if d.profile in known])
            config.set_aside[section] = unknown
            for device in unknown:
                if device.id not in _warned:
                    _warned.add(device.id)
                    _LOGGER.warning("Device '%s' skipped: profile %s is not known to this version", device.name, device.profile)
        return config

    def _load(self) -> SunflowConfig:
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
                t(
                    "Konfiguration {path} ist ungültig ({error}); die vorige Fassung liegt in {backup}",
                    path=self._path, error=err.__class__.__name__, backup=self._backup.name,
                )
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
            data = config.model_dump(mode="json")
            for section, devices in config.set_aside.items():
                data[section] = [*data.get(section, []), *(d.model_dump(mode="json") for d in devices)]
            file.write(json.dumps(data, indent=2))
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
