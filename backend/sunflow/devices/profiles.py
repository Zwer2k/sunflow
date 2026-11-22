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

# Folders below the profiles folder that hold no device profiles, with their schema files
OPTIMIZERS_DIR = "optimizers"
BATTERY_MODULES_DIR = "battery_modules"


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
    # PV module optimizers by profile id (profiles/optimizers/)
    optimizers: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Battery modules by profile id (profiles/battery_modules/)
    battery_modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[ProfileError] = field(default_factory=list)

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


def _schema_problems(validator: jsonschema.Draft202012Validator, data: Any) -> str | None:
    problems = sorted(validator.iter_errors(data), key=lambda e: e.path)
    return "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in problems) or None


def _load_sub_device_profiles(
    folder: Path, schema: dict[str, Any], target: dict[str, dict[str, Any]], errors: list[ProfileError]
) -> None:
    """Optimizer or battery module profiles: the schema plus checks it cannot express."""
    validator = jsonschema.Draft202012Validator(schema)
    for path in sorted(folder.rglob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as err:
            errors.append(ProfileError(path=path, message=str(err)))
            continue
        if message := _schema_problems(validator, data):
            errors.append(ProfileError(path=path, message=message))
            continue
        if "label_position" in data:
            try:
                pattern = re.compile(data["label_position"])
            except re.error as err:
                errors.append(ProfileError(path=path, message=f"label_position: {err}"))
                continue
            if pattern.groups != 2:
                errors.append(ProfileError(path=path, message="label_position needs two groups (string, module)"))
                continue
        if missing := sorted(set(data["required"]) - set(data["values"])):
            errors.append(ProfileError(path=path, message=f"required names no value: {', '.join(missing)}"))
            continue
        if data["profile"] in target:
            errors.append(ProfileError(path=path, message=f"Duplicate profile {data['profile']}"))
            continue
        target[data["profile"]] = data


def load_profiles(profiles_dir: Path, schema: dict[str, Any], schema_dir: Path | None = None) -> ProfileLibrary:
    """Load and validate all device profiles below profiles_dir; with schema_dir also optimizers and battery modules."""
    library = ProfileLibrary()
    validator = jsonschema.Draft202012Validator(schema)
    other = (profiles_dir / OPTIMIZERS_DIR, profiles_dir / BATTERY_MODULES_DIR)
    for path in sorted(profiles_dir.rglob("*.yaml")):
        if any(path.is_relative_to(folder) for folder in other):
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
    if schema_dir is not None:
        for folder, schema_file, target in (
            (OPTIMIZERS_DIR, "optimizer.schema.json", library.optimizers),
            (BATTERY_MODULES_DIR, "battery_module.schema.json", library.battery_modules),
        ):
            sub_schema = json.loads((schema_dir / schema_file).read_text())
            _load_sub_device_profiles(profiles_dir / folder, sub_schema, target, library.errors)

    for error in library.errors:
        _LOGGER.error("Invalid profile %s: %s", error.path, error.message)
    return library
