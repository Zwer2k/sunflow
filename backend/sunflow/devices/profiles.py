"""Profile library: loading and validation of manufacturer profiles."""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
from typing import Any

import jsonschema
import yaml

_LOGGER = logging.getLogger(__name__)

# Below the profiles folder; its files follow schema/unit.schema.json
UNITS_DIR = "units"


BATTERY_ACTIONS = ("auto", "hold", "force_charge", "force_discharge", "limit_charge", "limit_discharge")
# An action the device cannot do is replaced by the first of these it can
ACTION_FALLBACKS = {
    "hold": ("limit_discharge", "auto"),
    "limit_discharge": ("hold", "auto"),
    "limit_charge": ("auto",),
    "force_charge": ("hold", "auto"),
    "force_discharge": ("auto",),
}


def controls_battery(profile: dict[str, Any]) -> bool:
    """The profile can control a battery: a power window or actions."""
    return bool({"battery_window", "battery_control"} & set(profile["capabilities"]))


def planned_battery[D](devices: list[D], profiles: dict[str, dict[str, Any]]) -> D | None:
    """The battery Sunflow plans: the first device that controls one (one battery for now)."""
    return next((d for d in devices if controls_battery(profiles[d.profile])), None)  # type: ignore[attr-defined]


def battery_actions(profile: dict[str, Any]) -> set[str]:
    """Actions the device supports; a power window supports all."""
    if "battery_control" in profile:
        return {a for a in BATTERY_ACTIONS if a in profile["battery_control"]}
    return set(BATTERY_ACTIONS) if controls_battery(profile) else {"auto"}


def volatile_action(profile: dict[str, Any], action: str) -> bool:
    """The action writes no value to flash: it may be rewritten as often as needed."""
    if "battery_control" in profile:
        steps, default = profile["battery_control"].get(action, []), "volatile"
    elif primitives := profile.get("primitive"):
        ((_, primitive),) = primitives.items()
        steps, default = primitive["steps"], primitive.get("storage", "eeprom")
    else:
        return False
    return bool(steps) and all(spec.get("storage", default) != "eeprom" for step in steps for spec in step.values())


def supported_action(profile: dict[str, Any], action: str, power: float | None = None) -> tuple[str, float | None]:
    """The action and its power, or the closest action the device supports."""
    supported = battery_actions(profile)
    if action in supported:
        return action, power
    replacement = next((a for a in ACTION_FALLBACKS.get(action, ()) if a in supported), "auto")
    # Holding is limiting the discharge to nothing
    return replacement, 0.0 if replacement == "limit_discharge" else None


@dataclass(frozen=True, kw_only=True)
class ProfileError:
    """A profile file that could not be loaded."""

    path: Path
    message: str


@dataclass(kw_only=True)
class ProfileLibrary:
    """All valid profiles plus the files that failed to load."""

    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Units below devices (optimizers, battery modules) by their id, from profiles/units/
    units: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[ProfileError] = field(default_factory=list)

    def units_of(self, kind: str) -> list[dict[str, Any]]:
        """Return the unit profiles of one kind."""
        return [u for u in self.units.values() if u["kind"] == kind]

    @property
    def integrations(self) -> set[str]:
        """Return the integration domains with at least one profile."""
        return {p["match"]["integration"] for p in self.profiles.values() if "match" in p}

    def for_integration(self, domain: str) -> list[dict[str, Any]]:
        """Return the profiles for an integration domain."""
        return [
            p for p in self.profiles.values() if p.get("match", {}).get("integration") == domain
        ]

    @property
    def direct(self) -> list[dict[str, Any]]:
        """Return the profiles of devices Sunflow connects to itself (no integration)."""
        return [p for p in self.profiles.values() if "connection" in p]


def load_schema(schema_dir: Path) -> dict[str, Any]:
    """Load the profile JSON schema."""
    return json.loads((schema_dir / "profile.schema.json").read_text())


def load_unit_schema(schema_dir: Path) -> dict[str, Any]:
    """Load the JSON schema of unit profiles."""
    return json.loads((schema_dir / "unit.schema.json").read_text())


def _schema_problems(validator: jsonschema.Draft202012Validator, data: Any) -> str | None:
    problems = sorted(validator.iter_errors(data), key=lambda e: e.path)
    return "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in problems) or None


def _load_units(units_dir: Path, schema: dict[str, Any], library: ProfileLibrary) -> None:
    validator = jsonschema.Draft202012Validator(schema)
    for path in sorted(units_dir.rglob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as err:
            library.errors.append(ProfileError(path=path, message=str(err)))
            continue
        if message := _schema_problems(validator, data):
            library.errors.append(ProfileError(path=path, message=message))
            continue
        if "label_position" in data:
            try:
                pattern = re.compile(data["label_position"])
            except re.error as err:
                library.errors.append(ProfileError(path=path, message=f"label_position: {err}"))
                continue
            if pattern.groups != 2:
                library.errors.append(ProfileError(path=path, message="label_position needs two groups (string, module)"))
                continue
        if missing := sorted(set(data["required"]) - set(data["values"])):
            library.errors.append(ProfileError(path=path, message=f"required names no value: {', '.join(missing)}"))
            continue
        if data["unit"] in library.units:
            library.errors.append(ProfileError(path=path, message=f"Duplicate unit {data['unit']}"))
            continue
        library.units[data["unit"]] = data


def load_profiles(profiles_dir: Path, schema: dict[str, Any], unit_schema: dict[str, Any] | None = None) -> ProfileLibrary:
    """Load and validate all profiles below profiles_dir; with unit_schema also the units in its units/ folder."""
    library = ProfileLibrary()
    validator = jsonschema.Draft202012Validator(schema)
    units_dir = profiles_dir / UNITS_DIR
    for path in sorted(profiles_dir.rglob("*.yaml")):
        if path.is_relative_to(units_dir):
            continue
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as err:
            library.errors.append(ProfileError(path=path, message=str(err)))
            continue
        if problems := sorted(validator.iter_errors(data), key=lambda e: e.path):
            message = "; ".join(
                f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
                for e in problems
            )
            library.errors.append(ProfileError(path=path, message=message))
            continue
        if wrong := sorted(r for r, spec in data["roles"].items() if spec.get("minus") not in (None, *data["roles"]) or spec.get("minus") == r):
            library.errors.append(ProfileError(path=path, message=f"minus names no other role: {', '.join(wrong)}"))
            continue
        if data["profile"] in library.profiles:
            library.errors.append(
                ProfileError(path=path, message=f"Duplicate profile {data['profile']}")
            )
            continue
        library.profiles[data["profile"]] = data
    if unit_schema is not None:
        _load_units(units_dir, unit_schema, library)

    for error in library.errors:
        _LOGGER.error("Invalid profile %s: %s", error.path, error.message)
    return library
