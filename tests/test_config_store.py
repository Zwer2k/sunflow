"""Tests for the configuration store."""

from pathlib import Path

from sunflow.config_store import ConfigStore, DeviceConfig, RoleOverride


def test_roundtrip(tmp_path: Path) -> None:
    """A saved configuration loads back unchanged."""
    store = ConfigStore(tmp_path / "data")
    assert store.load().devices == []

    config = store.load()
    config.devices.append(
        DeviceConfig(
            ha_device_id="d_inv",
            profile="solax_modbus/sofar",
            profile_version=1,
            name="Sofar Inverter",
            parameters={"max_charge": 5000},
            role_overrides={"grid_power": RoleOverride(sign="export_positive")},
        )
    )
    store.save(config)

    loaded = store.load()
    assert loaded == config
    assert loaded.device(config.devices[0].id) == config.devices[0]
    assert loaded.device("nope") is None
