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
