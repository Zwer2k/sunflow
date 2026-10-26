"""Tests for the configuration store."""

from pathlib import Path

import pytest

from sunflow.config_store import ConfigStore, DeviceConfig, RoleOverride, SunflowConfig


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


def test_save_keeps_backup_and_unknown_fields(tmp_path: Path) -> None:
    """Saving keeps the previous file; fields of a newer version survive a load/save cycle."""
    import json

    store = ConfigStore(tmp_path)
    store.save(SunflowConfig())
    raw = json.loads((tmp_path / "config.json").read_text())
    raw["future_feature"] = {"on": True}
    (tmp_path / "config.json").write_text(json.dumps(raw))

    store.save(store.load())

    assert json.loads((tmp_path / "config.json").read_text())["future_feature"] == {"on": True}
    assert (tmp_path / "config.json.bak").exists()


def test_invalid_config_raises_config_error(tmp_path: Path) -> None:
    """A broken file gives a clear error instead of a validation traceback."""
    import pytest

    from sunflow.config_store import ConfigError

    (tmp_path / "config.json").write_text('{"devices": "broken"}')
    with pytest.raises(ConfigError, match="ungültig"):
        ConfigStore(tmp_path).load()


def test_wear_from_purchase_price() -> None:
    """Price / (cycles x mean capacity x depth of discharge) gives the wear per kWh."""
    from sunflow.config_store import OptimizerConfig, WearCalculation

    calc = WearCalculation(price_eur=8000, cycles=6000, capacity_kwh=20, depth_of_discharge_pct=90, end_of_life_pct=70)
    # 6000 x 17 kWh x 0.9 = 91 800 kWh -> 8,71 ct/kWh
    assert calc.ct_per_kwh() == pytest.approx(8.71, abs=0.01)
    assert OptimizerConfig(wear_ct_per_kwh=1, wear_calculation=calc).wear_ct_per_kwh == pytest.approx(8.71)
    assert OptimizerConfig(wear_ct_per_kwh=4).wear_ct_per_kwh == 4


def test_migrates_older_version_and_keeps_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Steps run in order on the raw JSON; the original stays next to the migrated file."""
    import json

    from sunflow import config_store

    def rename_strategy(raw: dict) -> dict:
        raw.setdefault("optimizer", {})["strategy"] = raw.pop("old_strategy")
        return raw

    monkeypatch.setattr(config_store, "CONFIG_VERSION", 2)
    monkeypatch.setattr(config_store, "MIGRATIONS", {1: rename_strategy})
    (tmp_path / "config.json").write_text(json.dumps({"version": 1, "old_strategy": "autarky"}))

    config = ConfigStore(tmp_path).load()

    assert config.optimizer.strategy == "autarky"
    assert config.version == 2
    assert json.loads((tmp_path / "config.v1.json").read_text())["old_strategy"] == "autarky"
    stored = json.loads((tmp_path / "config.json").read_text())
    assert stored["version"] == 2
    assert "old_strategy" not in stored


def test_newer_version_falls_back_to_defaults(tmp_path: Path) -> None:
    """After a downgrade unknown values get their default instead of stopping Sunflow."""
    import json

    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "version": 99,
                "future_field": {"a": 1},
                "optimizer": {"strategy": "future_strategy", "max_soc_pct": 90},
                "devices": [
                    {"ha_device_id": "d", "profile": "p", "profile_version": 1, "name": "Ok"},
                    {"profile": "p", "profile_version": 1, "name": "Without device"},
                ],
            }
        )
    )

    config = ConfigStore(tmp_path).load()

    assert config.optimizer.strategy == "balanced"
    assert config.optimizer.max_soc_pct == 90
    assert [d.name for d in config.devices] == ["Ok"]
    # Kept for the newer version when saved again
    assert config.version == 99
    assert config.model_extra == {"future_field": {"a": 1}}


def test_broken_json_raises_config_error(tmp_path: Path) -> None:
    """A truncated file is reported, not replaced by an empty configuration."""
    from sunflow.config_store import ConfigError

    (tmp_path / "config.json").write_text('{"version": 1, "devi')

    with pytest.raises(ConfigError):
        ConfigStore(tmp_path).load()


@pytest.mark.parametrize(
    ("stored", "strategy", "override"),
    [
        pytest.param({"optimizer": {"strategy": "backup", "backup_reserve_pct": 40}}, "balanced", None, id="default"),
        pytest.param(
            {"optimizer": {"strategy": "autarky"}, "strategy_override": {"strategy": "backup"}},
            "autarky",
            None,
            id="temporary",
        ),
    ],
)
def test_backup_strategy_becomes_reserve(tmp_path: Path, stored: dict, strategy: str, override: None) -> None:
    """Version 1 had backup power as a strategy; version 2 keeps the reserve in every strategy."""
    import json

    (tmp_path / "config.json").write_text(json.dumps({"version": 1} | stored))

    config = ConfigStore(tmp_path).load()

    assert config.optimizer.strategy == strategy
    assert config.optimizer.backup_reserve
    assert config.optimizer.backup_reserve_pct == stored["optimizer"].get("backup_reserve_pct", 30)
    assert config.strategy_override is override


@pytest.mark.parametrize(
    ("strategy", "reserve", "expected"),
    [
        pytest.param("balanced", True, 5000, id="balanced"),
        pytest.param("pv_absorption", True, 5000, id="pv_absorption"),
        pytest.param("autarky", True, 5000, id="autarky"),
        pytest.param("balanced", False, 0, id="off"),
    ],
)
def test_reserve_applies_in_every_strategy(strategy: str, reserve: bool, expected: float) -> None:
    """The reserve is a floor for the optimizer whatever the strategy."""
    from sunflow.config_store import OptimizerConfig
    from sunflow.optimizer import BatterySpec
    from sunflow.plan import optimizer_settings

    battery = BatterySpec(capacity_wh=20_000, soc_wh=10_000, min_soc_wh=2000, max_charge_w=5000, max_discharge_w=5000)
    config = SunflowConfig(optimizer=OptimizerConfig(strategy=strategy, backup_reserve=reserve, backup_reserve_pct=25))

    assert optimizer_settings(config, battery).reserve_wh == expected
