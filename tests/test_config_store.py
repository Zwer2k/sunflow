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


def test_cost_override_beats_pv_only_default() -> None:
    """A device set to "only PV" by default can be switched to cost optimal temporarily."""
    from datetime import UTC, datetime

    from sunflow.config_store import DeviceOverride, FlexSettings, SunflowConfig

    device = DeviceConfig(
        id="ww", ha_device_id="x", profile="evcc_intg/loadpoint", profile_version=1, name="Warmwasser",
        flex=FlexSettings(strategy="pv_only"),
    )
    config = SunflowConfig(devices=[device], overrides={"ww": DeviceOverride(strategy="cost")})

    effective, paused = config.effective_flex(device, datetime.now(UTC))

    assert effective.strategy == "cost"
    assert not paused
