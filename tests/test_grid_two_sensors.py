"""Grid power measured by two sensors (import and export) and grid meters added by hand."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sunflow.analysis.history import Series, history_series
from sunflow.api import create_app
from sunflow.config.models import DeviceConfig, RoleOverride, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary, load_profiles, load_schema
from sunflow.devices.resolver import preview

REPO = Path(__file__).resolve().parents[1]
STATES = {
    "sensor.import": {"state": "0", "attributes": {"unit_of_measurement": "W"}},
    "sensor.export": {"state": "1.2", "attributes": {"unit_of_measurement": "kW"}},
    "sensor.signed": {"state": "-800", "attributes": {"unit_of_measurement": "W"}},
}


def _snapshot(states: dict[str, Any] = STATES) -> Snapshot:
    return Snapshot(entries=[], devices=[], entities=[], states=states, energy_prefs=None)


def _meter(**overrides: str) -> DeviceConfig:
    return DeviceConfig(
        id="meter", ha_device_id="manual:meter", profile="generic/grid_meter", profile_version=1, name="Netz",
        role_overrides={role: RoleOverride(entity_id=entity) for role, entity in overrides.items()},
    )


def _grid(library: ProfileLibrary, device: DeviceConfig, snapshot: Snapshot) -> float | None:
    overrides = {r: o.model_dump() for r, o in device.role_overrides.items()}
    return next(v.value for v in preview(library.profiles[device.profile], snapshot, device.ha_device_id, overrides) if v.role == "grid_power")


@pytest.mark.parametrize(
    ("overrides", "states", "grid"),
    [
        pytest.param({"grid_power": "sensor.import", "grid_export_power": "sensor.export"}, STATES, -1200.0, id="import minus export"),
        pytest.param({"grid_power": "sensor.signed"}, STATES, -800.0, id="one signed sensor"),
        pytest.param(
            {"grid_power": "sensor.import", "grid_export_power": "sensor.export"},
            STATES | {"sensor.export": {"state": "unavailable", "attributes": {}}},
            None,
            id="export unavailable",
        ),
    ],
)
def test_grid_from_two_sensors(library: ProfileLibrary, overrides: dict[str, str], states: dict[str, Any], grid: float | None) -> None:
    """Import minus export in the same unit; without the export reading there is no grid value."""
    assert _grid(library, _meter(**overrides), _snapshot(states)) == grid


def test_history_subtracts_the_export(library: ProfileLibrary) -> None:
    """The grid history sums the import and the negated export statistics."""
    config = SunflowConfig(devices=[_meter(grid_power="sensor.import", grid_export_power="sensor.export")])

    series = [s for s in history_series(config, library.profiles, _snapshot()) if s.key == "grid_w"]

    assert series == [
        Series(key="grid_w", statistic_id="sensor.import", factor=1.0),
        Series(key="grid_w", statistic_id="sensor.export", factor=-1.0),
    ]


def test_signed_sensor_chosen_for_a_two_sensor_profile(tmp_path: Path, schema: dict[str, Any]) -> None:
    """A sensor chosen for the grid role alone is signed already: the profile's export is not subtracted."""
    (tmp_path / "x.yaml").write_text(
        "profile: x/hybrid\nversion: 1\nmatch: {integration: x}\ncapabilities: [grid_meter]\nroles:\n"
        "  grid_power: {key: to_user, unit: W, sign: import_positive, minus: grid_export_power}\n"
        "  grid_export_power: {key: to_grid, unit: W, optional: true}\n"
    )
    library = load_profiles(tmp_path, schema)
    entities = [
        {"entity_id": "sensor.to_user", "unique_id": "sn_to_user", "device_id": "inv"},
        {"entity_id": "sensor.to_grid", "unique_id": "sn_to_grid", "device_id": "inv"},
    ]
    states = {
        "sensor.to_user": {"state": "0", "attributes": {"unit_of_measurement": "W"}},
        "sensor.to_grid": {"state": "500", "attributes": {"unit_of_measurement": "W"}},
    } | STATES
    snapshot = Snapshot(entries=[], devices=[{"id": "inv"}], entities=entities, states=states, energy_prefs=None)
    device = DeviceConfig(id="inv", ha_device_id="inv", profile="x/hybrid", profile_version=1, name="WR")
    chosen = device.model_copy(update={"role_overrides": {"grid_power": RoleOverride(entity_id="sensor.signed")}})

    assert _grid(library, device, snapshot) == -500.0
    assert _grid(library, chosen, snapshot) == -800.0


def test_minus_must_name_another_role(tmp_path: Path, schema: dict[str, Any]) -> None:
    """A profile subtracting a role it does not have is refused."""
    (tmp_path / "x.yaml").write_text(
        "profile: x/y\nversion: 1\nmatch: {integration: x}\ncapabilities: [grid_meter]\n"
        "roles:\n  grid_power: {key: p, unit: W, minus: grid_export}\n"
    )

    library = load_profiles(tmp_path, schema)

    assert library.profiles == {}
    assert library.errors[0].message == "minus names no other role: grid_power"


def _client(tmp_path: Path) -> TestClient:
    app = create_app()
    app.state.store = ConfigStore(tmp_path)
    app.state.profiles = load_profiles(REPO / "profiles", load_schema(REPO / "schema"))

    async def snapshot(fresh: bool = False) -> SimpleNamespace:
        return SimpleNamespace(states={}, entities=[], devices=[])

    app.state.snapshots = SimpleNamespace(get=snapshot)
    return TestClient(app)


@pytest.mark.parametrize(
    ("body", "overrides"),
    [
        pytest.param(
            {"power_entity": "sensor.import", "export_power_entity": "sensor.export", "import_energy_entity": "sensor.in_kwh"},
            {"grid_power": ("sensor.import", None), "grid_export_power": ("sensor.export", None), "grid_import_energy": ("sensor.in_kwh", None)},
            id="import and export",
        ),
        pytest.param({"power_entity": "sensor.signed", "export_positive": True}, {"grid_power": ("sensor.signed", "export_positive")}, id="export positive"),
    ],
)
def test_grid_meter_by_hand(tmp_path: Path, body: dict[str, Any], overrides: dict[str, tuple[str, str | None]]) -> None:
    """Any sensors make a grid meter; it becomes the automatic grid measurement."""
    client = _client(tmp_path)

    device = client.post("/api/v1/config/devices/manual", json={"name": "Netz", "type": "grid_meter"} | body).json()

    assert {r: (o["entity_id"], o["sign"]) for r, o in device["role_overrides"].items()} == overrides
    assert client.get("/api/v1/config/measurements").json()["grid"]["effective"] == device["id"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"power_entity": "switch.x"}, id="no sensor"),
        pytest.param({"power_entity": "sensor.import", "export_power_entity": "sensor.export", "export_positive": True}, id="two signs"),
    ],
)
def test_grid_meter_by_hand_refused(tmp_path: Path, body: dict[str, Any]) -> None:
    """The grid power must be a sensor; with an export sensor it cannot also count export as positive."""
    assert _client(tmp_path).post("/api/v1/config/devices/manual", json={"name": "Netz", "type": "grid_meter"} | body).status_code == 422
