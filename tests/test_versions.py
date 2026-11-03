"""One version for the App, the backend and the integration HACS installs."""

import json
from pathlib import Path
import re

from sunflow import __version__

REPO = Path(__file__).resolve().parents[1]


def test_versions_match() -> None:
    """Edge App, backend package and integration carry the backend's version."""
    edge = re.search(r'^version: "(.*)"$', (REPO / "sunflow_app_edge" / "config.yaml").read_text(), re.M)
    package = re.search(r'^version = "(.*)"$', (REPO / "backend" / "pyproject.toml").read_text(), re.M)
    manifest = json.loads((REPO / "custom_components" / "sunflow_ems" / "manifest.json").read_text())

    assert edge is not None and package is not None
    assert {edge[1], package[1], manifest["version"]} == {__version__}
