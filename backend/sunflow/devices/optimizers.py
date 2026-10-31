"""PV module optimizers: found in Home Assistant, assigned to strings and placed on the roof.

Huawei optimizers are sub-devices of their inverter (huawei_solar). The string they
hang on is not exposed by the integration: with one string in use it is that one.
Tigo optimizers (tigo_rs485) carry the label given in that integration; by Tigo's
convention "A1" is string A, module 1.
"""

from dataclasses import dataclass, field
import re
from typing import Any

from sunflow.config.models import SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.resolver import normalize, preview
from sunflow.devices.site import pv_strings


@dataclass(frozen=True, kw_only=True)
class OptimizerSource:
    """An integration that has a Home Assistant device per optimizer."""

    key: str
    name: str
    domain: str
    # Value -> end of the entity's unique_id
    values: dict[str, str]
    # Values that must be there for a device to count as optimizer
    required: tuple[str, ...]
    # Optimizers are sub-devices of their inverter (which has values of the same names)
    sub_device: bool = False


SOURCES = (
    OptimizerSource(
        key="huawei",
        name="Huawei",
        domain="huawei_solar",
        values={
            "power": "_output_power",
            "voltage_in": "_input_voltage",
            "current_in": "_input_current",
            "voltage_out": "_output_voltage",
            "current_out": "_output_current",
            "temperature": "_temperature",
        },
        required=("power", "voltage_in"),
        sub_device=True,
    ),
    OptimizerSource(
        key="tigo",
        name="Tigo",
        domain="tigo_rs485",
        values={
            "power": "_power",
            "voltage_in": "_voltage_in",
            "current_in": "_current",
            "voltage_out": "_voltage_out",
            "temperature": "_temperature",
            "pwm": "_pwm",
        },
        required=("power",),
    ),
)
VALUE_UNITS = {
    "power": "W",
    "voltage_in": "V",
    "current_in": "A",
    "voltage_out": "V",
    "current_out": "A",
    "temperature": "°C",
    "pwm": "",
}
TIGO_LABEL = re.compile(r"^([A-Za-z])\s*0*(\d+)$")
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
    string: str | None = None
    string_auto: bool = False
    row: int | None = None
    col: int | None = None
    rotated: bool = False
    values: dict[str, float | None] = field(default_factory=dict)


def _label(source: OptimizerSource, device: dict[str, Any]) -> str:
    name = device.get("name_by_user") or device.get("name") or device["id"]
    return name.removeprefix("Tigo Optimizer ").strip() if source.key == "tigo" else name


def discover(snapshot: Snapshot) -> list[Optimizer]:
    """All optimizers Home Assistant knows, with the entities of their values."""
    by_device: dict[str, list[dict[str, Any]]] = {}
    for entity in snapshot.entities:
        if entity.get("device_id") and not entity.get("disabled_by"):
            by_device.setdefault(entity["device_id"], []).append(entity)
    found = []
    for device in snapshot.devices:
        domains = {identifier[0] for identifier in device.get("identifiers", [])}
        for source in SOURCES:
            if source.domain not in domains or (source.sub_device and not device.get("via_device_id")):
                continue
            entities = {}
            for value, suffix in source.values.items():
                # The shortest match: "_power" must not take "_output_power"
                matches = sorted(
                    (e for e in by_device.get(device["id"], []) if (e.get("unique_id") or "").endswith(suffix)),
                    key=lambda e: len(e.get("unique_id") or ""),
                )
                if matches:
                    entities[value] = matches[0]["entity_id"]
            if all(v in entities for v in source.required):
                label = _label(source, device)
                found.append(
                    Optimizer(
                        id=device["id"], source=source.key, name=label, label=label,
                        parent=device.get("via_device_id"), entities=entities,
                    )
                )
    return sorted(found, key=lambda o: (o.source, _natural(o.label)))


def _natural(text: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


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


def tigo_plant(config: SunflowConfig, strings: list[dict[str, Any]], optimizers: list[Optimizer]) -> str | None:
    """The plant the Tigo optimizers hang on: chosen, or the only one without own optimizers."""
    if config.optimizer_settings.tigo_plant:
        return config.optimizer_settings.tigo_plant
    with_own = {o.parent for o in optimizers if o.source != "tigo"}
    plants = list(dict.fromkeys(s["device_id"] for s in strings if s["ha_device_id"] not in with_own))
    return plants[0] if len(plants) >= 1 else None


def _auto_place(optimizer: Optimizer, strings: list[dict[str, Any]], tigo: str | None, index: int) -> None:
    keys = {s["key"] for s in strings}
    if optimizer.source == "tigo":
        if (match := TIGO_LABEL.match(optimizer.label)) is not None:
            number = ord(match.group(1).upper()) - ord("A") + 1
            if tigo and f"{tigo}:{number}" in keys:
                optimizer.string = f"{tigo}:{number}"
            optimizer.row, optimizer.col = 0, int(match.group(2)) - 1
            return
    else:
        own = [s for s in strings if s["ha_device_id"] == optimizer.parent]
        used = [s for s in own if s["in_use"]]
        # Several strings in use: which one the optimizer hangs on is not known
        if len(own) == 1 or len(used) == 1:
            optimizer.string = (own if len(own) == 1 else used)[0]["key"]
    optimizer.row, optimizer.col = 0, index


def optimizers(config: SunflowConfig, profiles: dict[str, Any], snapshot: Snapshot) -> tuple[list[Optimizer], list[dict[str, Any]]]:
    """All optimizers with string, roof position (set or proposed) and current values; and the strings."""
    strings = plant_strings(config, profiles, snapshot)
    found = discover(snapshot)
    tigo = tigo_plant(config, strings, found)
    placements = {p.ha_device_id: p for p in config.optimizer_settings.placements}
    counters: dict[str | None, int] = {}
    placed: set[str] = set()
    for optimizer in found:
        placement = placements.get(optimizer.id)
        counter = counters.get(optimizer.parent, 0)
        counters[optimizer.parent] = counter + 1
        _auto_place(optimizer, strings, tigo, counter)
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
