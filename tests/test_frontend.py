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

    # Edit afterwards: other switch, no power sensor, no daily energy; planning settings stay
    edited = client.put(
        f"/api/v1/config/devices/manual/{device['id']}",
        json={"name": "E-Bike Keller", "switch_entity": "switch.plug_2", "max_power": 300},
    ).json()
    assert edited["name"] == "E-Bike Keller"
    assert edited["role_overrides"] == {"switch": {"entity_id": "switch.plug_2", "unit": None, "sign": None}}
    assert edited["parameters"] == {"max_power": 300}
    assert edited["flex"]["strategy"] == "pv_only"
    assert client.put(
        f"/api/v1/config/devices/manual/{device['id']}", json={"name": "x", "switch_entity": "sensor.x"}
    ).status_code == 422


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

    assert client.put("/api/v1/control/strategy", json={"strategy": "autarky"}).status_code == 200
    assert store.load().effective_strategy(datetime.now(UTC)) == "autarky"

    assert client.put("/api/v1/control/priorities", json=["battery", "ev"]).json() == ["battery", "ev"]
    assert client.put("/api/v1/control/priorities", json=["nope"]).status_code == 422

    client.delete("/api/v1/control/devices/ev")
    client.delete("/api/v1/control/strategy")
    config = store.load()
    assert config.overrides == {} and config.strategy_override is None
    assert len(replans) == 5


def test_priority_conditions(tmp_path: Path) -> None:
    """Conditions are stored per consumer, validated and removed with an empty list."""
    from sunflow.config_store import ConfigStore, DeviceConfig, SunflowConfig
    from sunflow.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    store = ConfigStore(tmp_path)
    store.save(
        SunflowConfig(
            devices=[
                DeviceConfig(id="ev", ha_device_id="x", profile="evcc_intg/loadpoint", profile_version=1, name="Auto"),
                DeviceConfig(id="ww", ha_device_id="y", profile="evcc_intg/loadpoint", profile_version=1, name="Warmwasser"),
            ],
            priorities=["battery", "ev", "ww"],
        )
    )
    app = create_app()
    app.state.store = store
    app.state.profiles = load_profiles(repo / "profiles", load_schema(repo / "schema"))
    app.state.runner = type("Runner", (), {"request_replan": lambda self: None})()
    client = TestClient(app)
    conditions = [{"device_id": "battery", "min_pct": 80.0}, {"device_id": "ev", "min_pct": 90.0}]

    assert client.put("/api/v1/control/priorities/ww/conditions", json=conditions).status_code == 200
    assert [c.model_dump() for c in store.load().priority_conditions["ww"]] == conditions
    assert client.put("/api/v1/control/priorities/ww/conditions", json=[{"device_id": "ww", "min_pct": 50}]).status_code == 422
    assert client.put("/api/v1/control/priorities/ww/conditions", json=[{"device_id": "battery", "min_pct": 120}]).status_code == 422

    # Only higher entries: the car (2nd) cannot wait for the hot water (3rd), the battery (1st) has no limits
    assert client.put("/api/v1/control/priorities/ev/conditions", json=[{"device_id": "ww", "min_pct": 50}]).status_code == 422
    # The battery has no entry above it in this order
    assert client.put("/api/v1/control/priorities/battery/conditions", json=[{"device_id": "ev", "min_pct": 80}]).status_code == 422

    # Moving the car below the hot water hides its limit but keeps it stored
    client.put("/api/v1/control/priorities", json=["battery", "ww", "ev"])
    config = store.load()
    assert [c.device_id for c in config.valid_conditions("ww")] == ["battery"]
    assert [c.device_id for c in config.hidden_conditions("ww")] == ["ev"]

    # Clearing the visible limits keeps the hidden one; moving the car up again restores it
    assert client.put("/api/v1/control/priorities/ww/conditions", json=[]).status_code == 200
    # Below the car, the battery can wait for it
    client.put("/api/v1/control/priorities", json=["ev", "battery", "ww"])
    assert client.put("/api/v1/control/priorities/battery/conditions", json=[{"device_id": "ev", "min_pct": 80}]).status_code == 200
    client.put("/api/v1/control/priorities", json=["battery", "ev", "ww"])
    assert [c.model_dump() for c in store.load().valid_conditions("ww")] == [{"device_id": "ev", "min_pct": 90.0}]


def test_measurement_sources(tmp_path: Path) -> None:
    """A dedicated grid meter wins automatically; the user can choose another device or go back."""
    from sunflow.config_store import ConfigStore, DeviceConfig, SunflowConfig
    from sunflow.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    store = ConfigStore(tmp_path)
    store.save(
        SunflowConfig(
            devices=[
                DeviceConfig(id="sofar", ha_device_id="x", profile="solax_modbus/sofar", profile_version=1, name="Sofar"),
                DeviceConfig(id="pulse", ha_device_id="y", profile="tibber_local/pulse", profile_version=1, name="Pulse"),
            ]
        )
    )
    app = create_app()
    app.state.store = store
    app.state.profiles = load_profiles(repo / "profiles", load_schema(repo / "schema"))
    client = TestClient(app)

    grid = client.get("/api/v1/config/measurements").json()["grid"]
    assert grid["selected"] is None
    assert grid["effective"] == "pulse"
    assert [o["id"] for o in grid["options"]] == ["pulse", "sofar"]

    assert client.put("/api/v1/config/measurements", json={"grid": "sofar"}).json()["grid"]["effective"] == "sofar"
    assert client.put("/api/v1/config/measurements", json={"load": "pulse"}).status_code == 422
    assert client.put("/api/v1/config/measurements", json={"grid": None}).json()["grid"]["effective"] == "pulse"


def test_ingress_only(tmp_path: Path) -> None:
    """As an App only the Ingress proxy may use the API; health stays open for the integration."""
    settings = Settings(
        ha_url="http://localhost:8123",
        ha_token="x",
        port=0,
        data_dir=tmp_path,
        profiles_dir=tmp_path,
        schema_dir=tmp_path,
        frontend_dir=tmp_path / "missing",
        ingress_only=True,
    )
    from sunflow.config_store import ConfigStore

    app = create_app(settings)
    app.state.store = ConfigStore(tmp_path)
    other = TestClient(app, client=("172.30.33.5", 50000))
    ingress = TestClient(app, client=("172.30.32.2", 50000))

    assert other.get("/api/v1/config").status_code == 403
    assert other.post("/api/v1/config/devices/x/test?confirm=true").status_code == 403
    assert other.get("/api/v1/health").status_code == 200
    assert ingress.get("/api/v1/config").status_code == 200


def test_rejects_nan_parameters(tmp_path: Path) -> None:
    """NaN must not reach config.json, where it would break every later request."""
    from sunflow.config_store import ConfigStore, DeviceConfig, SunflowConfig

    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(devices=[DeviceConfig(id="d", ha_device_id="x", profile="solax_modbus/sofar", profile_version=1, name="S")]))
    app = create_app()
    app.state.store = store
    client = TestClient(app)

    response = client.patch(
        "/api/v1/config/devices/d", content='{"parameters": {"capacity_wh": NaN}}', headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert store.load().devices[0].parameters == {}


def test_remove_device_cleans_references(tmp_path: Path) -> None:
    """Removing a device drops its overrides, priority entry, limits and measurement choice."""
    from sunflow.config_store import ConfigStore, DeviceConfig, DeviceOverride, PriorityCondition, SunflowConfig

    store = ConfigStore(tmp_path)
    store.save(
        SunflowConfig(
            devices=[
                DeviceConfig(id="ev", ha_device_id="x", profile="evcc_intg/loadpoint", profile_version=1, name="Auto"),
                DeviceConfig(id="ww", ha_device_id="y", profile="evcc_intg/loadpoint", profile_version=1, name="WW"),
            ],
            overrides={"ev": DeviceOverride(paused=True)},
            priorities=["battery", "ev", "ww"],
            priority_conditions={"ww": [PriorityCondition(device_id="ev", min_pct=80)]},
            measurement_sources={"grid": "ev"},
        )
    )
    app = create_app()
    app.state.store = store
    client = TestClient(app)

    assert client.delete("/api/v1/config/devices/ev").status_code == 200

    config = store.load()
    assert [d.id for d in config.devices] == ["ww"]
    assert config.overrides == {}
    assert config.priorities == ["battery", "ww"]
    assert config.priority_conditions == {}
    assert config.measurement_sources == {}


def test_restore_requires_confirmation(tmp_path: Path) -> None:
    """Restoring writes to the device: only with confirm=true; without a marker nothing is written."""
    from sunflow.config_store import ConfigStore
    from sunflow.device_test import RestoreMarker

    app = create_app()
    app.state.store = ConfigStore(tmp_path)
    app.state.restore_marker = RestoreMarker(tmp_path)
    client = TestClient(app)

    assert client.get("/api/v1/device-test/pending-restore").json() is None
    app.state.restore_marker.set({"device_id": "gone", "started": None, "previous_mode": "Self Use"})
    assert client.get("/api/v1/device-test/pending-restore").json()["previous_mode"] == "Self Use"
    assert client.post("/api/v1/device-test/restore").status_code == 400
    # No battery device configured: nothing to restore with
    assert client.post("/api/v1/device-test/restore?confirm=true").status_code == 404


def test_pv_strings() -> None:
    """String power is measured or voltage x current; strings without entities are left out."""
    from types import SimpleNamespace

    from sunflow.site import pv_strings

    def role(value: float | None, entity: str | None = "sensor.x") -> SimpleNamespace:
        return SimpleNamespace(value=value, entity_id=entity, display={"unit": "kW", "precision": 2})

    sofar = {"pv1_power": role(1200.0), "pv2_power": role(None)}
    huawei = {
        "pv1_voltage": role(400.0), "pv1_current": role(2.5),
        "pv2_voltage": role(None, entity=None), "pv2_current": role(None, entity=None),
    }

    kw = {"unit": "kW", "precision": 2}
    assert pv_strings(sofar) == [
        {"index": 1, "name": "String 1", "power_w": 1200.0, "display": kw},
        {"index": 2, "name": "String 2", "power_w": 0.0, "display": kw},
    ]
    # Computed from voltage x current: whole watts
    assert pv_strings(huawei) == [{"index": 1, "name": "String 1", "power_w": 1000.0, "display": {"unit": "W", "precision": 0}}]


def test_display_precision_from_registry() -> None:
    """The user's display precision wins over the integration's suggestion."""
    from sunflow.resolver import display_precision

    assert display_precision({"options": {"sensor": {"suggested_display_precision": 1}}}) == 1
    assert display_precision({"options": {"sensor": {"suggested_display_precision": 1, "display_precision": 3}}}) == 3
    assert display_precision({"options": {}}) is None
    assert display_precision(None) is None


async def test_live_weather_hours_and_days() -> None:
    """Hourly and daily forecast are fetched once each and cached; past days are left out."""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from sunflow import api

    now = datetime.now(UTC)
    calls = []

    class Client:
        async def call_service(self, domain: str, service: str, data: dict, **kwargs: object) -> dict:
            calls.append(data["type"])
            if data["type"] == "hourly":
                forecast = [{"datetime": (now + timedelta(hours=1)).isoformat(), "condition": "sunny", "temperature": 12}]
            else:
                forecast = [
                    {"datetime": (now - timedelta(days=2)).isoformat(), "condition": "rainy"},
                    {"datetime": (now + timedelta(days=1)).isoformat(), "condition": "cloudy", "temperature": 14, "templow": 6},
                ]
            return {"response": {"weather.home": {"forecast": forecast}}}

        async def command(self, msg_type: str, **data: object) -> dict:
            return {"time_zone": "Europe/Berlin"}

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ha=Client())))
    config = api.SunflowConfig()
    config.optimizer.weather_entity = "weather.home"
    snapshot = SimpleNamespace(states={"weather.home": {"state": "sunny", "attributes": {"temperature": 11}}})

    weather = await api._live_weather(request, config, snapshot)
    await api._live_weather(request, config, snapshot)

    assert [h["condition"] for h in weather["hours"]] == ["sunny"]
    assert weather["days"] == [
        {"time": (now + timedelta(days=1)).isoformat(), "condition": "cloudy", "temperature": 14, "templow": 6, "cloud": None, "precipitation": None}
    ]
    assert calls == ["hourly", "daily"]


def test_restart_plane_learning_survives_setup_save(tmp_path: Path) -> None:
    """The restart is stored with the plane; saving the planes from the setup keeps it."""
    from sunflow.config_store import ConfigStore, ForecastPlane, SunflowConfig

    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(forecast_planes=[ForecastPlane(source="solcast", ref="cp", name="Carport", strings=["h:1"])]))
    relearned: list[bool] = []
    app = create_app()
    app.state.store = store
    app.state.runner = type(
        "Runner", (), {"request_replan": lambda self: None, "relearn": lambda self: relearned.append(True)}
    )()
    client = TestClient(app)

    assert client.post("/api/v1/pv-learning/planes/solcast:cp/restart").status_code == 200
    since = store.load().forecast_planes[0].learn_since
    assert since is not None and relearned == [True]

    planes = [{"source": "solcast", "ref": "cp", "name": "Carport", "custom_name": "Carport", "strings": ["h:1"]}]
    assert client.put("/api/v1/config/forecast", json=planes).status_code == 200
    assert store.load().forecast_planes[0].learn_since == since
    assert client.post("/api/v1/pv-learning/planes/solcast:nope/restart").status_code == 404


def test_takeover_needs_confirmation_and_a_passed_test(tmp_path: Path) -> None:
    """Switching control on writes to the inverter: confirm=true and a passed device test are required."""
    from datetime import UTC, datetime

    from sunflow.config_store import ConfigStore, DeviceConfig, SunflowConfig
    from sunflow.control import PlanController, TakeoverMarker
    from sunflow.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(devices=[
        DeviceConfig(id="bat", ha_device_id="x", profile="solax_modbus/sofar", profile_version=1, name="Sofar"),
    ]))
    app = create_app()
    app.state.store = store
    app.state.profiles = load_profiles(repo / "profiles", load_schema(repo / "schema"))
    app.state.controller = PlanController(TakeoverMarker(tmp_path), lambda d: None, lambda: app.state.profiles.profiles)
    from types import SimpleNamespace

    async def snapshot(fresh: bool = False) -> SimpleNamespace:
        return SimpleNamespace(states={}, entities=[], devices=[])

    app.state.snapshots = SimpleNamespace(get=snapshot)
    client = TestClient(app)

    assert client.put("/api/v1/control/takeover", json={"device_id": "bat", "device_enabled": True}).status_code == 422

    config = store.load()
    config.devices[0].test_passed_at = datetime.now(UTC)
    store.save(config)
    assert client.put("/api/v1/control/takeover", json={"device_id": "bat", "device_enabled": True}).status_code == 200
    assert client.put("/api/v1/control/takeover", json={"enabled": True}).status_code == 400
    response = client.put("/api/v1/control/takeover?confirm=true", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["enabled"] and response.json()["devices"][0]["enabled"]
    # Switching off needs no confirmation
    assert client.put("/api/v1/control/takeover", json={"enabled": False}).status_code == 200


def test_vehicles_cards_and_manual_choice(tmp_path: Path) -> None:
    """Vehicles are stored per wallbox; a card may belong to one vehicle per wallbox; the choice is validated."""
    from sunflow.config_store import ConfigStore, DeviceConfig, SunflowConfig

    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(devices=[DeviceConfig(id="wb", ha_device_id="x", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")]))
    from types import SimpleNamespace

    from sunflow.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    app = create_app()
    app.state.store = store
    app.state.profiles = load_profiles(repo / "profiles", load_schema(repo / "schema"))
    app.state.runner = type("Runner", (), {"request_replan": lambda self: None})()

    async def snapshot(fresh: bool = False) -> SimpleNamespace:
        return SimpleNamespace(states={}, entities=[], devices=[])

    app.state.snapshots = SimpleNamespace(get=snapshot)
    client = TestClient(app)

    fiat = {"id": "fiat", "name": "Fiat", "capacity_wh": 37500, "card": 1, "wallboxes": ["wb"]}
    tesla = {"id": "tesla", "name": "Tesla", "capacity_wh": 60000, "card": 1, "wallboxes": ["wb"]}
    assert client.put("/api/v1/config/vehicles", json=[fiat, tesla]).status_code == 422
    assert client.put("/api/v1/config/vehicles", json=[fiat, tesla | {"card": 2}, {"name": "X", "capacity_wh": 1, "wallboxes": ["nope"]}]).status_code == 422

    stored = client.put("/api/v1/config/vehicles", json=[fiat, tesla | {"card": 2}])
    assert stored.status_code == 200
    assert stored.json()["wallboxes"][0]["cards"] is True
    assert [v.name for v in store.load().vehicles] == ["Fiat", "Tesla"]

    assert client.put("/api/v1/control/vehicles/wb", json={"vehicle_id": "tesla"}).status_code == 200
    assert store.load().selected_vehicles == {"wb": "tesla"}
    assert client.put("/api/v1/control/vehicles/wb", json={"vehicle_id": "other"}).status_code == 422
    assert client.put("/api/v1/control/vehicles/wb", json={"vehicle_id": None}).status_code == 200
    assert store.load().selected_vehicles == {}

    detection = {"card": True, "manual": True, "sensor": False, "single": True, "authorize": False}
    assert client.put("/api/v1/config/devices/wb/vehicle-detection", json=detection).status_code == 200
    assert store.load().devices[0].vehicle_detection.authorize is False


def test_disable_device_keeps_settings(tmp_path: Path) -> None:
    """A switched off device is kept aside with its settings and comes back unchanged."""
    from sunflow.config_store import ConfigStore, DeviceConfig, SunflowConfig

    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(devices=[
        DeviceConfig(id="wb", ha_device_id="x", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox", parameters={"max_power": 7400}),
    ]))
    app = create_app()
    app.state.store = store
    app.state.runner = type("Runner", (), {"request_replan": lambda self: None})()
    client = TestClient(app)

    assert client.put("/api/v1/config/devices/wb/enabled", json={"enabled": False}).status_code == 200
    config = store.load()
    assert config.devices == [] and config.disabled_devices[0].parameters == {"max_power": 7400}

    assert client.put("/api/v1/config/devices/wb/enabled", json={"enabled": True}).status_code == 200
    assert store.load().devices[0].parameters == {"max_power": 7400}
    assert client.put("/api/v1/config/devices/nope/enabled", json={"enabled": True}).status_code == 404


def test_loadpoint_kind_sets_the_category(tmp_path: Path) -> None:
    """An evcc loadpoint is a wallbox or heat; the choice is stored and wins over the SoC unit."""
    from types import SimpleNamespace

    from sunflow.config_store import ConfigStore, DeviceConfig, SunflowConfig
    from sunflow.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(devices=[
        DeviceConfig(id="ww", ha_device_id="x", profile="evcc_intg/loadpoint", profile_version=1, name="Warmwasser"),
        DeviceConfig(id="bat", ha_device_id="y", profile="solax_modbus/sofar", profile_version=1, name="Sofar"),
    ]))
    app = create_app()
    app.state.store = store
    app.state.profiles = load_profiles(repo / "profiles", load_schema(repo / "schema"))
    app.state.runner = type("Runner", (), {"request_replan": lambda self: None})()

    async def snapshot(fresh: bool = False) -> SimpleNamespace:
        return SimpleNamespace(states={}, entities=[], devices=[])

    app.state.snapshots = SimpleNamespace(get=snapshot)
    client = TestClient(app)

    # Unreadable SoC: counted as wallbox until chosen
    assert client.get("/api/v1/config/categories").json() == {"ww": "wallbox", "bat": "inverter"}
    assert client.put("/api/v1/config/devices/ww/kind", json={"kind": "heater"}).status_code == 200
    assert client.get("/api/v1/config/categories").json()["ww"] == "heat"
    assert client.put("/api/v1/config/devices/bat/kind", json={"kind": "heater"}).status_code == 404
