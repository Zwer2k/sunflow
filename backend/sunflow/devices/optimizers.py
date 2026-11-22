"""PV module optimizers: found in Home Assistant, assigned to strings and placed on the roof.

Which integrations provide optimizers and how is described by the optimizer profiles
(profiles/optimizers/). Only optimizers of integrations taken over under "Geräte" count.

Optimizers that are sub-devices of their inverter hang on one of its strings; which one is
not known unless only one string is in use. Optimizers of an integration of their own hang on
a chosen plant; a label_position in their profile places them by label ("A1": string A, module 1).
"""

from dataclasses import dataclass, field
import re
from typing import Any

from sunflow.config.models import SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary
from sunflow.devices.resolver import normalize, preview
from sunflow.devices.site import pv_strings
from sunflow.devices.sub_devices import find_sub_devices, natural_key

VALUE_UNITS = {
    "power": "W",
    "voltage_in": "V",
    "current_in": "A",
    "voltage_out": "V",
    "current_out": "A",
    "temperature": "°C",
    "pwm": "",
    "energy_today": "kWh",
    "energy_total": "kWh",
}
# A string counts as in use with this much voltage
STRING_IN_USE_V = 5.0


@dataclass(kw_only=True)
class Optimizer:
    """One optimizer with where it hangs and sits, and its current values."""

    id: str
    source: str
    name: str
    label: str
    parent: str | None
    entities: dict[str, str]
    # Config entries of the integration that provides the device
    entry_ids: list[str] = field(default_factory=list)
    string: str | None = None
    string_auto: bool = False
    row: int | None = None
    col: int | None = None
    rotated: bool = False
    values: dict[str, float | None] = field(default_factory=dict)


def sources(library: ProfileLibrary) -> dict[str, dict[str, Any]]:
    """The optimizer profiles by id."""
    return library.optimizers


def discover(snapshot: Snapshot, library: ProfileLibrary) -> list[Optimizer]:
    """All optimizers Home Assistant knows, with the entities of their values."""
    found = [
        Optimizer(
            id=d.id, source=d.profile, name=d.name, label=d.name, parent=d.parent,
            entities={k: ids[0] for k, ids in d.entities.items()}, entry_ids=d.entry_ids,
        )
        for d in find_sub_devices(snapshot, list(library.optimizers.values()))
    ]
    return sorted(found, key=lambda o: (o.source, natural_key(o.label)))


def plant_strings(config: SunflowConfig, profiles: dict[str, Any], snapshot: Snapshot) -> list[dict[str, Any]]:
    """PV strings of all plants: key "<device id>:<index>", names and whether in use now."""
    result = []
    for device in config.devices:
        profile = profiles[device.profile]
        if "pv" not in profile["capabilities"]:
            continue
        roles = {
            r.role: r
            for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
        }
        for string in pv_strings(roles):
            voltage = roles.get(f"pv{string['index']}_voltage")
            result.append(
                {
                    "key": f"{device.id}:{string['index']}",
                    "device_id": device.id,
                    "ha_device_id": device.ha_device_id,
                    "plant": device.name,
                    "name": f"{device.name} {string['name']}",
                    "power_w": string["power_w"],
                    "in_use": string["power_w"] > 0
                    or (voltage is not None and (voltage.value or 0) > STRING_IN_USE_V),
                }
            )
    return result


def standalone_plant(
    config: SunflowConfig, strings: list[dict[str, Any]], optimizers: list[Optimizer], profiles: dict[str, dict[str, Any]]
) -> str | None:
    """The plant optimizers of an integration of their own hang on: chosen, or the first without own optimizers."""
    if config.optimizer_settings.tigo_plant:
        return config.optimizer_settings.tigo_plant
    with_own = {o.parent for o in optimizers if profiles[o.source]["match"].get("sub_device")}
    plants = list(dict.fromkeys(s["device_id"] for s in strings if s["ha_device_id"] not in with_own))
    return plants[0] if plants else None


def _auto_place(
    optimizer: Optimizer, profile: dict[str, Any], strings: list[dict[str, Any]], plant: str | None, index: int
) -> None:
    if profile["match"].get("sub_device"):
        own = [s for s in strings if s["ha_device_id"] == optimizer.parent]
        used = [s for s in own if s["in_use"]]
        # Several strings in use: which one the optimizer hangs on is not known
        if len(own) == 1 or len(used) == 1:
            optimizer.string = (own if len(own) == 1 else used)[0]["key"]
    elif "label_position" in profile and (match := re.match(profile["label_position"], optimizer.label)) is not None:
        number = ord(match.group(1).upper()) - ord("A") + 1
        if plant and any(s["key"] == f"{plant}:{number}" for s in strings):
            optimizer.string = f"{plant}:{number}"
        optimizer.row, optimizer.col = 0, int(match.group(2)) - 1
        return
    optimizer.row, optimizer.col = 0, index


def used_entries(config: SunflowConfig, found: list[Optimizer]) -> set[str]:
    """Config entries whose optimizers are taken over; before that choice existed, those with placed optimizers."""
    if config.optimizer_settings.entries is not None:
        return set(config.optimizer_settings.entries)
    placed = {p.ha_device_id for p in config.optimizer_settings.placements}
    return {entry for o in found if o.id in placed for entry in o.entry_ids}


def optimizers(
    config: SunflowConfig, library: ProfileLibrary, snapshot: Snapshot
) -> tuple[list[Optimizer], list[dict[str, Any]]]:
    """The taken-over optimizers with string, roof position (set or proposed) and current values; and the strings."""
    strings = plant_strings(config, library.profiles, snapshot)
    found = discover(snapshot, library)
    entries = used_entries(config, found)
    found = [o for o in found if entries & set(o.entry_ids)]
    units = sources(library)
    plant = standalone_plant(config, strings, found, units)
    placements = {p.ha_device_id: p for p in config.optimizer_settings.placements}
    counters: dict[str | None, int] = {}
    placed: set[str] = set()
    for optimizer in found:
        placement = placements.get(optimizer.id)
        counter = counters.get(optimizer.parent, 0)
        counters[optimizer.parent] = counter + 1
        _auto_place(optimizer, units[optimizer.source], strings, plant, counter)
        optimizer.string_auto = True
        if placement is not None:
            if placement.string is not None:
                optimizer.string, optimizer.string_auto = placement.string or None, False
            if placement.row is not None and placement.col is not None:
                optimizer.row, optimizer.col = placement.row, placement.col
                placed.add(optimizer.id)
            if placement.name:
                optimizer.name = placement.name
            optimizer.rotated = bool(placement.rotated)
        for key, entity_id in optimizer.entities.items():
            state = snapshot.states.get(entity_id)
            raw_unit = state.get("attributes", {}).get("unit_of_measurement") if state else None
            value, _ = normalize(state["state"] if state else None, raw_unit or VALUE_UNITS[key] or None, None)
            optimizer.values[key] = value
    _free_places(found, placed)
    return found, strings


def _free_places(found: list[Optimizer], placed: set[str]) -> None:
    """Proposed places taken by a placed module move right, so no module hides another."""
    taken = {(o.string, o.row, o.col) for o in found if o.id in placed}
    for optimizer in found:
        if optimizer.id in placed or optimizer.row is None or optimizer.col is None:
            continue
        while (optimizer.string, optimizer.row, optimizer.col) in taken:
            optimizer.col += 1
        taken.add((optimizer.string, optimizer.row, optimizer.col))
