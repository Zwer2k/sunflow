"""Importing an export: the recording, and when moving also the configuration."""

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

from fastapi.testclient import TestClient
import pytest
from sunflow.api import create_app
from sunflow.config.models import ControlSettings, DeviceConfig, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.shadow.store import ShadowStore


def _export(tmp_path: Path) -> bytes:
    """An export of another installation with control switched on."""
    source = tmp_path / "source"
    ShadowStore(source).close()
    ConfigStore(source).save(
        SunflowConfig(
            devices=[DeviceConfig(id="d", ha_device_id="x", profile="p", profile_version=1, name="Sofar")],
            control=ControlSettings(enabled=True, devices={"d": True}),
        )
    )
    (source / "eeprom_writes.json").write_text('{"d": 3}')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in ("shadow.db", "config.json", "eeprom_writes.json"):
            archive.write(source / name, name)
    return buffer.getvalue()


@pytest.fixture
def target(tmp_path: Path) -> tuple[TestClient, SimpleNamespace]:
    """The App the export is imported into."""
    data = tmp_path / "target"
    data.mkdir()
    app = create_app()
    app.state.store = ConfigStore(data)
    app.state.settings = SimpleNamespace(data_dir=data)
    runner = SimpleNamespace(shadow=ShadowStore(data), request_replan=Mock())
    app.state.runner = runner
    return TestClient(app), runner


@pytest.mark.parametrize(("config", "devices"), [(False, []), (True, ["Sofar"])], ids=["recording", "moving"])
def test_import(
    tmp_path: Path, target: tuple[TestClient, SimpleNamespace], config: bool, devices: list[str]
) -> None:
    """Without the flag only the recording is merged; with it the configuration follows, control off."""
    client, runner = target
    response = client.post(
        f"/api/v1/import?config={str(config).lower()}", files={"file": ("export.zip", _export(tmp_path))}
    )
    assert response.status_code == 200
    assert response.json()["config"] is config
    stored = client.app.state.store.load()
    assert [d.name for d in stored.devices] == devices
    assert stored.control.enabled is False
    assert (tmp_path / "target" / "eeprom_writes.json").exists() is config
    assert runner.request_replan.called is config
