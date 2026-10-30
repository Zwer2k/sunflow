"""Profile library: loading and validation of manufacturer profiles."""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any

import jsonschema
import yaml

_LOGGER = logging.getLogger(__name__)


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
