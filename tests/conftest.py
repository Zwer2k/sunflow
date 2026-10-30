"""Shared fixtures."""

from pathlib import Path
from typing import Any

import pytest
from sunflow.devices.profiles import ProfileLibrary, load_profiles, load_schema

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def schema() -> dict[str, Any]:
    """Return the profile schema."""
    return load_schema(REPO_ROOT / "schema")


@pytest.fixture
def library(schema: dict[str, Any]) -> ProfileLibrary:
    """Return the profile library shipped with the repository."""
    return load_profiles(REPO_ROOT / "profiles", schema)
