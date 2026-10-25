"""Tests for serving the frontend under a versioned asset path."""

from pathlib import Path

from fastapi.testclient import TestClient

from sunflow.api import create_app
from sunflow.settings import Settings


def _client(frontend: Path) -> TestClient:
    settings = Settings(
        ha_url="http://localhost:8123",
        ha_token="x",
        port=0,
        data_dir=frontend / "data",
        profiles_dir=frontend,
        schema_dir=frontend,
        frontend_dir=frontend,
    )
    # Not used as context manager: lifespan (HA connection) is not started
    return TestClient(create_app(settings))


def test_versioned_assets(tmp_path: Path) -> None:
    """Index references assets by version; the version changes with the files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "index.html").write_text('<script src="./__ASSETS__/src/app.js"></script>')
    (tmp_path / "src" / "app.js").write_text("console.log(1)")
    client = _client(tmp_path)

    index = client.get("/")
    assert index.headers["cache-control"] == "no-cache"
    path = index.text.split('"./')[1].split('"')[0]
    assert path.startswith("assets/") and path.endswith("/src/app.js")

    asset = client.get(f"/{path}")
    assert asset.text == "console.log(1)"
    assert "immutable" in asset.headers["cache-control"]

    (tmp_path / "src" / "app.js").write_text("console.log(2)")
    assert client.get("/").text != index.text


def test_asset_outside_frontend_is_rejected(tmp_path: Path) -> None:
    """Paths escaping the frontend directory are not served."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("")
    (tmp_path / "secret.txt").write_text("secret")
    client = _client(frontend)

    assert client.get("/assets/x/%2E%2E/secret.txt").status_code == 404
    assert client.get("/assets/x/missing.js").status_code == 404


def test_manual_device_and_flex_settings(tmp_path: Path) -> None:
    """A switchable consumer can be added and its planning strategy stored."""
    from sunflow.config_store import ConfigStore
    from sunflow.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    app = create_app()
    app.state.store = ConfigStore(tmp_path)
    app.state.profiles = load_profiles(repo / "profiles", load_schema(repo / "schema"))
    client = TestClient(app)

    response = client.post(
        "/api/v1/config/devices/manual",
        json={"name": "E-Bike", "switch_entity": "switch.shelly_ebike", "power_entity": "sensor.shelly_ebike_power",
              "max_power": 250, "daily_energy_wh": 500},
    )
    assert response.status_code == 200
    device = response.json()
    assert device["profile"] == "generic/switch"
    assert device["role_overrides"]["switch"]["entity_id"] == "switch.shelly_ebike"
    assert device["parameters"] == {"max_power": 250, "daily_energy_wh": 500}

    bad = client.post("/api/v1/config/devices/manual", json={"name": "x", "switch_entity": "sensor.x"})
    assert bad.status_code == 422

    update = client.patch(
        f"/api/v1/config/devices/{device['id']}",
        json={"flex": {"strategy": "pv_only", "ready_by": "18:00", "uses_battery": False}},
    )
    assert update.json()["flex"] == {
        "strategy": "pv_only", "target": None, "ready_by": "18:00", "uses_battery": False, "alternative_cost_ct": None
    }
    assert client.patch(f"/api/v1/config/devices/{device['id']}", json={"flex": {"ready_by": "25:00"}}).status_code == 422


def test_combine_batteries() -> None:
    """Several batteries become one node with capacity-weighted SoC."""
    from sunflow.api import combine_batteries

    assert combine_batteries([]) is None
    combined = combine_batteries(
        [
            {"name": "A", "soc_pct": 80, "capacity_wh": 20000, "power_w": 1000},
            {"name": "B", "soc_pct": 20, "capacity_wh": 5000, "power_w": -400},
        ]
    )
    assert combined["soc_pct"] == 68
    assert combined["power_w"] == 600
    assert combined["count"] == 2
    assert [p["name"] for p in combined["parts"]] == ["A", "B"]


def test_control_overrides_and_priorities(tmp_path: Path) -> None:
    """Overrides, battery strategy and priorities are stored and trigger a replan."""
    from datetime import UTC, datetime, timedelta

    from sunflow.config_store import ConfigStore, DeviceConfig, SunflowConfig
    from sunflow.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(devices=[DeviceConfig(id="ev", ha_device_id="x", profile="evcc_intg/loadpoint", profile_version=1, name="Auto")]))
    replans: list[bool] = []
    app = create_app()
    app.state.store = store
    app.state.profiles = load_profiles(repo / "profiles", load_schema(repo / "schema"))
    app.state.runner = type("Runner", (), {"request_replan": lambda self: replans.append(True)})()
    client = TestClient(app)

    until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert client.put("/api/v1/control/devices/ev", json={"strategy": "fast", "until": until}).status_code == 200
    config = store.load()
    effective, paused = config.effective_flex(config.devices[0], datetime.now(UTC))
    assert effective.strategy == "fast" and not paused

    assert client.put("/api/v1/control/strategy", json={"strategy": "backup"}).status_code == 200
    assert store.load().effective_strategy(datetime.now(UTC)) == "backup"

    assert client.put("/api/v1/control/priorities", json=["battery", "ev"]).json() == ["battery", "ev"]
    assert client.put("/api/v1/control/priorities", json=["nope"]).status_code == 422

    client.delete("/api/v1/control/devices/ev")
    client.delete("/api/v1/control/strategy")
    config = store.load()
    assert config.overrides == {} and config.strategy_override is None
    assert len(replans) == 5
