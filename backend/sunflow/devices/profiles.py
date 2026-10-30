"""Profile library: loading and validation of manufacturer profiles."""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any

import jsonschema
import yaml

_LOGGER = logging.getLogger(__name__)


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


def battery_actions(profile: dict[str, Any]) -> set[str]:
    """Actions the device supports; a power window supports all."""
    if "battery_control" in profile:
        return {a for a in BATTERY_ACTIONS if a in profile["battery_control"]}
    return set(BATTERY_ACTIONS) if controls_battery(profile) else {"auto"}


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


def load_profiles(profiles_dir: Path, schema: dict[str, Any]) -> ProfileLibrary:
    """Load and validate all profiles below profiles_dir."""
    library = ProfileLibrary()
    validator = jsonschema.Draft202012Validator(schema)
    for path in sorted(profiles_dir.rglob("*.yaml")):
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
        if data["profile"] in library.profiles:
            library.errors.append(
                ProfileError(path=path, message=f"Duplicate profile {data['profile']}")
            )
            continue
        library.profiles[data["profile"]] = data

    for error in library.errors:
        _LOGGER.error("Invalid profile %s: %s", error.path, error.message)
    return library
