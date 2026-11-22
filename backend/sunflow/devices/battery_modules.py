"""Battery modules: the units of kind "battery_module" below each configured battery, with their values.

Which devices are modules and which entity gives which value is described by the unit
profiles (profiles/units/); a module has only the values its integration provides.
"""

from typing import Any

from sunflow.config.models import SunflowConfig

from .discovery import Snapshot
from .profiles import ProfileLibrary
from .units import Unit, find_units, natural_key, unit_values

KIND = "battery_module"
# Text or bit states; reported as the problem text, None while all is well
STATUS_VALUES = ("alarm", "protection", "fault")
VALUE_UNITS: dict[str, str | None] = {
    "soc": "%",
    "voltage": "V",
    "current": "A",
    "power": "W",
    "temperature_max": "°C",
    "temperature_min": "°C",
    "cell_voltage_max": "V",
    "cell_voltage_min": "V",
    "soh": "%",
    "cycles": None,
    "capacity_full_ah": "Ah",
    "capacity_remaining_ah": "Ah",
    "balancing": None,
}
# Modules may hang below a tower controller below the inverter
MAX_DEPTH = 3
IGNORED_STATES = ("unknown", "unavailable", "")


def _below(snapshot: Snapshot, device_id: str, ancestor: str) -> bool:
    parents = {d["id"]: d.get("via_device_id") for d in snapshot.devices}
    current = parents.get(device_id)
    for _ in range(MAX_DEPTH):
        if current is None:
            return False
        if current == ancestor:
            return True
        current = parents.get(current)
    return False


def status(unit: Unit, profile: dict[str, Any], snapshot: Snapshot) -> dict[str, str | None]:
    """Per status value the problem text, None while all is well (or unknown)."""
    result: dict[str, str | None] = {}
    for key in STATUS_VALUES:
        if key not in unit.entities:
            continue
        spec = profile["values"][key]
        ok = spec.get("ok", ["0"]) if isinstance(spec, dict) else ["0"]
        problems = []
        for entity_id in unit.entities[key]:
            state = str((snapshot.states.get(entity_id) or {}).get("state", ""))
            if state not in IGNORED_STATES and state not in ok and state.removesuffix(".0") not in ok:
                problems.append(state)
        result[key] = ", ".join(problems) or None
    return result


def battery_modules(config: SunflowConfig, library: ProfileLibrary, snapshot: Snapshot) -> list[dict[str, Any]]:
    """Each configured battery with the modules found below it (or chosen for it in the setup)."""
    profiles = {u["unit"]: u for u in library.units_of(KIND)}
    units = find_units(snapshot, list(profiles.values()))
    devices = {d["id"]: d for d in snapshot.devices}
    result = []
    for device in config.devices:
        if "battery" not in library.profiles.get(device.profile, {}).get("capabilities", []):
            continue
        chosen = config.battery_modules.get(device.id)
        mine = [
            u for u in units
            if (u.id in chosen if chosen is not None else _below(snapshot, u.id, device.ha_device_id))
        ]
        if not mine:
            continue
        mine.sort(key=lambda u: natural_key(u.name))
        modules = []
        for unit in mine:
            profile = profiles[unit.unit]
            numeric = Unit(**{**unit.__dict__, "entities": {k: v for k, v in unit.entities.items() if k not in STATUS_VALUES}})
            modules.append(
                {
                    "id": unit.id,
                    "name": unit.name,
                    "model": devices.get(unit.id, {}).get("model"),
                    "source": unit.unit,
                    "values": unit_values(numeric, profile, snapshot, VALUE_UNITS),
                    "status": status(unit, profile, snapshot),
                    "entities": {k: v for k, v in unit.entities.items() if k not in STATUS_VALUES},
                }
            )
        result.append({"device_id": device.id, "name": device.name, "modules": modules})
    return result
