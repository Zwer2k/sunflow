"""Candidates for the optional learning sources, found by their device class."""

from typing import Any

from sunflow.config.models import SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.optimizers import discover as discover_optimizers, optimizers

DEVICE_CLASSES = {
    "temperature": ("temperature",),
    "humidity": ("humidity",),
    "irradiance": ("irradiance", "illuminance"),
    "precipitation": ("precipitation", "precipitation_intensity"),
}
# Many DIY sensors (e.g. via MQTT) have a unit but no device class
UNITS = {
    "temperature": ("°C", "°F", "K"),
    "humidity": (),
    "irradiance": ("W/m²", "lx"),
    "precipitation": ("mm", "mm/h", "in", "in/h"),
}
# Names that point to a sensor outside, listed first
OUTDOOR_HINTS = (
    "aussen", "außen", "outdoor", "outside", "garten", "garden", "wetter", "weather",
    "balkon", "terrasse", "dach", "roof", "modul", "pv",
)


def _sunflow_devices(config: SunflowConfig, snapshot: Snapshot) -> set[str]:
    """HA devices of configured devices and their sub-devices (e.g. battery modules), but not optimizers.

    An optimizer sits on its module: its temperature is the module temperature.
    """
    own = {d.ha_device_id for d in config.devices}
    optimizers = {o.id for o in discover_optimizers(snapshot)}
    return (own | {d["id"] for d in snapshot.devices if d.get("via_device_id") in own}) - optimizers


def learning_candidates(config: SunflowConfig, snapshot: Snapshot) -> dict[str, list[dict[str, Any]]]:
    """Sensors per kind of source; the inverter's and battery's own temperatures are left out."""
    registry = {e["entity_id"]: e for e in snapshot.entities}
    excluded = _sunflow_devices(config, snapshot)
    result: dict[str, list[dict[str, Any]]] = {kind: [] for kind in DEVICE_CLASSES}
    for entity_id, state in snapshot.states.items():
        if not entity_id.startswith("sensor."):
            continue
        entry = registry.get(entity_id, {})
        if entry.get("device_id") in excluded or entry.get("platform") == "sunflow_ems":
            continue
        attributes = state.get("attributes", {})
        for kind, classes in DEVICE_CLASSES.items():
            device_class = attributes.get("device_class")
            if device_class in classes or (device_class is None and attributes.get("unit_of_measurement") in UNITS[kind]):
                result[kind].append(
                    {
                        "entity_id": entity_id,
                        "name": attributes.get("friendly_name", entity_id),
                        "unit": attributes.get("unit_of_measurement"),
                        "state": state.get("state"),
                        "state_class": attributes.get("state_class"),
                    }
                )
    for candidates in result.values():
        candidates.sort(
            key=lambda c: (
                not any(h in f"{c['entity_id']} {c['name']}".lower() for h in OUTDOOR_HINTS),
                c["name"].lower(),
            )
        )
    return result


def plane_optimizer_temperatures(
    config: SunflowConfig, profiles: dict[str, Any], snapshot: Snapshot
) -> dict[str, list[str]]:
    """Temperature entities of the optimizers on each plane in use, found by the plane's strings."""
    found, _ = optimizers(config, profiles, snapshot)
    result = {}
    for plane in config.forecast_planes:
        if not plane.device_id:
            continue
        entities = [o.entities["temperature"] for o in found if o.string in plane.strings and "temperature" in o.entities]
        if entities:
            result[plane.key] = entities
    return result


def module_temperature_sources(
    config: SunflowConfig, profiles: dict[str, Any], snapshot: Snapshot
) -> dict[str, list[str]]:
    """Per plane in use: the chosen sensor, else the mean of its optimizers' temperatures."""
    chosen = config.learning_sources.module_temperature
    in_use = {p.key for p in config.forecast_planes if p.device_id}
    return plane_optimizer_temperatures(config, profiles, snapshot) | {
        plane: [entity_id] for plane, entity_id in chosen.items() if plane in in_use
    }
