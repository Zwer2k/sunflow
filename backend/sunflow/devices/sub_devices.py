"""Home Assistant devices that a profile describes by their entities (optimizers, battery modules).

A technical helper: optimizers and battery modules have profiles of their own; both name the
integration, how its devices are recognised and which entity gives which value. A device
counts when it has the required values; each value is the entity whose unique_id ends with
the profile's key.
"""

from dataclasses import dataclass, field
import re
from statistics import fmean
from typing import Any

from .discovery import Snapshot, entity_has_key
from .resolver import normalize

AGGREGATES = {"max": max, "min": min, "mean": fmean}


@dataclass(kw_only=True)
class SubDevice:
    """One device with the entities of its values (several for an aggregated value)."""

    id: str
    profile: str
    name: str
    parent: str | None
    entities: dict[str, list[str]]
    entry_ids: list[str] = field(default_factory=list)


def natural_key(text: str) -> list[Any]:
    """Sort key with numbers by value: "Module 2" before "Module 10"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def value_keys(spec: str | dict[str, Any]) -> list[str]:
    """The keys of a value spec (a string or {key, or, aggregate}), in the order they are tried."""
    return [spec] if isinstance(spec, str) else [spec["key"], *spec.get("or", [])]


def device_name(profile: dict[str, Any], device: dict[str, Any]) -> str:
    """The device's name without the profile's prefix."""
    name = device.get("name_by_user") or device.get("name") or device["id"]
    strip = profile.get("name_strip")
    return name.removeprefix(strip).strip() if strip else name


def find_sub_devices(snapshot: Snapshot, profiles: list[dict[str, Any]]) -> list[SubDevice]:
    """All devices that match one of the profiles, with the entities of their values."""
    by_device: dict[str, list[dict[str, Any]]] = {}
    for entity in snapshot.entities:
        if entity.get("device_id") and not entity.get("disabled_by"):
            by_device.setdefault(entity["device_id"], []).append(entity)
    found = []
    for device in snapshot.devices:
        identifiers = device.get("identifiers", [])
        domains = {identifier[0] for identifier in identifiers}
        for profile in profiles:
            match = profile["match"]
            if match["integration"] not in domains or (match.get("sub_device") and not device.get("via_device_id")):
                continue
            if (marker := match.get("exclude_identifier")) and any(marker in str(i[1]) for i in identifiers):
                continue
            entities = {}
            for value, spec in profile["values"].items():
                # The first key that matches (older integration versions name it differently);
                # the shortest first: "power" must not take "output_power" where both exist
                matches = []
                for key in value_keys(spec):
                    matches = sorted(
                        (e for e in by_device.get(device["id"], []) if entity_has_key(e, key)),
                        key=lambda e: len(e.get("unique_id") or ""),
                    )
                    if matches:
                        break
                if matches:
                    entities[value] = [e["entity_id"] for e in (matches if isinstance(spec, dict) else matches[:1])]
            if all(v in entities for v in profile["required"]):
                found.append(
                    SubDevice(
                        id=device["id"], profile=profile["profile"], name=device_name(profile, device),
                        parent=device.get("via_device_id"), entities=entities,
                        entry_ids=list(device.get("config_entries", [])),
                    )
                )
                break
    return found


def current_values(
    device: SubDevice, profile: dict[str, Any], snapshot: Snapshot, units: dict[str, str | None]
) -> dict[str, float | None]:
    """Current values of a device in the given units (None: as the entity reports); aggregated ones combined."""
    values: dict[str, float | None] = {}
    for key, entity_ids in device.entities.items():
        readings = []
        for entity_id in entity_ids:
            state = snapshot.states.get(entity_id)
            raw_unit = state.get("attributes", {}).get("unit_of_measurement") if state else None
            value, _ = normalize(state["state"] if state else None, raw_unit or units.get(key) or None, None)
            if value is not None:
                readings.append(value)
        spec = profile["values"][key]
        if isinstance(spec, dict) and "aggregate" in spec:
            values[key] = AGGREGATES[spec["aggregate"]](readings) if readings else None
        else:
            values[key] = readings[0] if readings else None
    return values
