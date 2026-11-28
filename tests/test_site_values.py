"""Tests for the site's PV, battery, grid and house load: measured, else from the balance."""

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sunflow.api import create_app
from sunflow.config.models import DeviceConfig, RoleOverride, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import ProfileLibrary, load_profiles, load_schema
from sunflow.devices.site_values import Term, site_values

REPO = Path(__file__).resolve().parents[1]
W = {"unit_of_measurement": "W"}
# A PV-only inverter (AC output 2000 W), a grid meter (500 W import) and a load meter (2600 W)
PV = DeviceConfig(id="pv", name="WR", ha_device_id="ha_pv", profile="huawei_solar/pv_only", profile_version=1)
GRID = DeviceConfig(
    id="grid", name="Netz", ha_device_id="ha_grid", profile="generic/grid_meter", profile_version=1,
    role_overrides={"grid_power": RoleOverride(entity_id="sensor.grid")},
)
LOAD = DeviceConfig(
    id="load", name="Hauszähler", ha_device_id="ha_load", profile="generic/load_meter", profile_version=1,
    role_overrides={"load_power": RoleOverride(entity_id="sensor.house")},
)
SNAPSHOT = Snapshot(
    entries=[], devices=[],
    entities=[{"entity_id": "sensor.pv_ac", "unique_id": "HV1_active_power", "device_id": "ha_pv"}],
    states={"sensor.pv_ac": {"state": "2000", "attributes": W}, "sensor.grid": {"state": "500", "attributes": W},
            "sensor.house": {"state": "2600", "attributes": W}},
    energy_prefs=None,
)


@pytest.mark.parametrize(
    ("devices", "quantity", "expected"),
    [
        # Nothing measures the house load: PV + grid - battery
        pytest.param([PV, GRID], "load", (2500.0, "calculated", [Term(entity_id="sensor.pv_ac", factor=1.0),
                     Term(entity_id="sensor.grid", factor=1.0)]), id="load-from-balance"),
        # A meter of its own (e.g. a template) comes first
        pytest.param([PV, GRID, LOAD], "load", (2600.0, "measured", [Term(entity_id="sensor.house", factor=1.0)]), id="load-meter"),
        # Only the house load measured: grid = load - PV
        pytest.param([PV, LOAD], "grid", (600.0, "calculated", [Term(entity_id="sensor.house", factor=1.0),
                     Term(entity_id="sensor.pv_ac", factor=-1.0)]), id="grid-from-balance"),
        pytest.param([PV], "load", (None, None, []), id="nothing-to-tell"),
    ],
)
def test_measured_else_from_the_balance(
    library: ProfileLibrary, devices: list[DeviceConfig], quantity: str, expected: tuple[float | None, str | None, list[Term]]
) -> None:
    """Each quantity is measured, else calculated with the statistics its history is summed from."""
    value = site_values(SunflowConfig(devices=devices), library.profiles, SNAPSHOT)[quantity]

    assert (value.value, value.origin, value.terms) == expected


def test_a_hybrid_gives_its_pv_not_its_ac_output(library: ProfileLibrary) -> None:
    """A hybrid's AC output also holds what its battery gives: its PV counts, else the battery counted twice."""
    entities = [
        {"entity_id": f"sensor.{key}", "unique_id": f"HV2_{key}", "device_id": "ha_hybrid"}
        for key in ("input_power", "active_power", "storage_charge_discharge_power", "storage_state_of_capacity",
                    "power_meter_active_power", "storage_maximum_discharging_power")
    ]
    states = {
        # PV 1500 W, battery discharging 1000 W, so 2500 W AC; meter + = export: 200 W imported
        "sensor.input_power": {"state": "1500", "attributes": W},
        "sensor.active_power": {"state": "2500", "attributes": W},
        "sensor.storage_charge_discharge_power": {"state": "-1000", "attributes": W},
        "sensor.power_meter_active_power": {"state": "-200", "attributes": W},
    }
    hybrid = DeviceConfig(id="h", name="Huawei", ha_device_id="ha_hybrid", profile="huawei_solar/sun2000_luna2000", profile_version=1)
    snapshot = Snapshot(entries=[], devices=[], entities=entities, states=states, energy_prefs=None)

    values = site_values(SunflowConfig(devices=[hybrid]), library.profiles, snapshot)

    assert values["pv"].value == 1500
    assert (values["load"].value, values["load"].origin) == (2700, "calculated")


def _client(tmp_path: Path, devices: list[DeviceConfig]) -> TestClient:
    app = create_app()
    app.state.store = ConfigStore(tmp_path)
    app.state.store.save(SunflowConfig(devices=devices))
    app.state.profiles = load_profiles(REPO / "profiles", load_schema(REPO / "schema"))

    async def snapshot(fresh: bool = False) -> Snapshot:
        return SNAPSHOT

    app.state.snapshots = SimpleNamespace(get=snapshot)
    return TestClient(app)


HOUSE_METER = {"name": "Haus", "type": "load_meter", "power_entity": "sensor.house"}


@pytest.mark.parametrize(
    ("meters", "load", "origin"),
    [
        pytest.param([HOUSE_METER], 2600.0, "measured", id="load-meter-by-hand"),
        pytest.param([], 2500.0, "calculated", id="from-the-balance"),
    ],
)
def test_live_house_load(tmp_path: Path, meters: list[dict[str, str]], load: float, origin: str) -> None:
    """A load meter added by hand measures the house load in Live; without it the balance gives it."""
    client = _client(tmp_path, [PV, GRID])
    added = [client.post("/api/v1/config/devices/manual", json=body).status_code for body in meters]

    live = client.get("/api/v1/live").json()

    assert added == [200] * len(meters)
    assert (live["load_w"], live["origins"]["load"]) == (load, origin)
