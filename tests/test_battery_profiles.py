"""Shipped profiles of storage units against dummy devices with the entity keys of their integrations."""

from typing import Any

import pytest
from sunflow.devices.discovery import match_profile
from sunflow.devices.executor import plan_battery
from sunflow.devices.profiles import ProfileLibrary, battery_actions
from sunflow.devices.resolver import bind_roles

# Entity keys per domain as the integrations create them (unique_id = "<prefix>_<key>")
DUMMIES: dict[str, dict[str, Any]] = {
    "indevolt/indevolt": {
        "integration": "indevolt",
        "prefix": "SolidFlex2000-87654321",
        "keys": {
            "sensor": ["6002", "6000", "1501", "1664", "1665", "2108", "2101", "142", "1505", "6006", "6007", "7101"],
            "select": ["energy_mode"],
            "number": ["max_ac_output_power", "discharge_limit", "inverter_input_limit"],
            "switch": ["grid_charging"],
        },
    },
    "homevolt/homevolt": {
        "integration": "homevolt",
        "prefix": "40580137858664",
        "keys": {
            "sensor": [
                "State of Charge", "Power", "Available Charging Energy", "Available Discharge Energy", "Power grid",
                "Homevolt battery 0", "Homevolt battery 0 power",
            ],
            "select": ["battery_mode"],
            "number": ["setpoint", "grid_import_limit", "grid_export_limit"],
            "switch": ["local_mode"],
        },
    },
    "marstek_modbus/venus": {
        "integration": "marstek_modbus",
        "prefix": "01JC9ENTRY",
        "keys": {
            "sensor": [
                "battery_soc", "battery_power", "ac_power", "battery_total_energy", "total_charging_energy",
                "total_discharging_energy", "max_cell_voltage",
            ],
            "select": ["force_mode", "user_work_mode"],
            "number": ["set_charge_power", "set_discharge_power", "max_charge_power", "max_discharge_power", "charge_to_soc"],
            "switch": ["rs485_control_mode", "backup_function"],
        },
    },
}

VARIABLES = {"P": 600, "max_charge": 2400, "max_discharge": 800, "min_soc": 10, "device_id": "ha-device"}


def dummy_entities(profile_id: str) -> list[dict[str, Any]]:
    """Registry entries of the dummy device."""
    dummy = DUMMIES[profile_id]
    return [
        {"entity_id": f"{domain}.d_{key}", "unique_id": f"{dummy['prefix']}_{key}", "device_id": "dev"}
        for domain, keys in dummy["keys"].items()
        for key in keys
    ]


@pytest.mark.parametrize("profile_id", list(DUMMIES))
def test_dummy_matches_its_profile(library: ProfileLibrary, profile_id: str) -> None:
    """The dummy is recognized as this profile, and every required role binds."""
    entities = dummy_entities(profile_id)
    device = {"id": "dev", "manufacturer": None, "model": None}
    assert match_profile(library, DUMMIES[profile_id]["integration"], device, entities, True) == profile_id
    profile = library.profiles[profile_id]
    bound = bind_roles(profile, entities)
    assert [role for role, spec in profile["roles"].items() if not spec.get("optional") and bound[role] is None] == []


@pytest.mark.parametrize("profile_id", list(DUMMIES))
def test_every_action_resolves(library: ProfileLibrary, profile_id: str) -> None:
    """Every battery action plans calls on existing entities."""
    profile = library.profiles[profile_id]
    entities = dummy_entities(profile_id)
    existing = {e["entity_id"] for e in entities}
    for action in [*battery_actions(profile), "release"]:
        for call in plan_battery(profile, action, {"capacity_wh": 2000}, VARIABLES, entities):
            assert call.entity_id is None or call.entity_id in existing, (action, call)


def test_indevolt_actions(library: ProfileLibrary) -> None:
    """Forced charge and discharge are real-time actions; holding caps the AC output in self-consumption."""
    profile = library.profiles["indevolt/indevolt"]
    entities = dummy_entities("indevolt/indevolt")

    def calls(action: str) -> list[tuple[str, str, dict[str, Any]]]:
        return [(c.domain, c.service, c.data) for c in plan_battery(profile, action, {}, VARIABLES, entities)]

    assert battery_actions(profile) == {"auto", "hold", "limit_discharge", "force_charge", "force_discharge"}
    assert calls("force_charge") == [("indevolt", "charge", {"device_id": "ha-device", "power": 600, "target_soc": 100})]
    assert calls("force_discharge") == [("indevolt", "discharge", {"device_id": "ha-device", "power": 600, "target_soc": 10})]
    assert calls("hold") == [
        ("select", "select_option", {"option": "self_consumed_prioritized"}),
        ("number", "set_value", {"value": 0}),
    ]
    assert calls("release") == [
        ("select", "select_option", {"option": "self_consumed_prioritized"}),
        ("number", "set_value", {"value": 800}),
    ]


def test_ac_coupled_storage_converts_pv_as_well(library: ProfileLibrary) -> None:
    """A plug-in storage stores roof PV through its own inverter: PV charging loses like grid charging."""
    from types import SimpleNamespace

    from sunflow.config.models import DeviceConfig
    from sunflow.planning.plan import battery_spec

    device = DeviceConfig(
        id="plug", ha_device_id="dev", profile="indevolt/indevolt", profile_version=1, name="Indevolt",
        parameters={"capacity_wh": 1790, "efficiency_pct": 92, "inverter_efficiency_pct": 95},
    )
    entities = dummy_entities("indevolt/indevolt")
    snapshot = SimpleNamespace(
        devices=[{"id": "dev"}],
        entities=entities,
        states={e["entity_id"]: {"state": "50", "attributes": {}} for e in entities},
    )

    spec, problem = battery_spec(library.profiles["indevolt/indevolt"], device, snapshot)

    assert problem is None
    assert spec.key == "plug"
    assert spec.efficiency_charge == pytest.approx(spec.efficiency_charge_grid)
    assert spec.efficiency_charge * spec.efficiency_discharge == pytest.approx(0.92 * 0.95**2)


def test_homevolt_actions(library: ProfileLibrary) -> None:
    """Local mode first, then the manual schedule; switching local mode off hands the battery back."""
    profile = library.profiles["homevolt/homevolt"]
    entities = dummy_entities("homevolt/homevolt")

    def calls(action: str) -> list[tuple[str, str, dict[str, Any]]]:
        return [(c.domain, c.service, c.data) for c in plan_battery(profile, action, {}, VARIABLES, entities)]

    assert battery_actions(profile) == {"auto", "hold", "force_charge", "force_discharge"}
    assert calls("force_discharge") == [
        ("switch", "turn_on", {}),
        ("select", "select_option", {"option": "inverter_discharge"}),
        ("number", "set_value", {"value": 600}),
    ]
    assert calls("release") == [("switch", "turn_off", {})]


def test_homevolt_capacity_from_available_energies(library: ProfileLibrary) -> None:
    """Usable capacity is what the battery can still take plus what it can give."""
    from types import SimpleNamespace

    from sunflow.devices.site import derive_parameter

    entities = dummy_entities("homevolt/homevolt")
    values = {"Available Charging Energy": "5320", "Available Discharge Energy": "7980"}
    states = {e["entity_id"]: {"state": values.get(e["unique_id"].split("_", 1)[1], "0")} for e in entities}
    snapshot = SimpleNamespace(devices=[{"id": "dev"}], entities=entities, states=states)

    spec = library.profiles["homevolt/homevolt"]["parameters"]["capacity_wh"]

    assert derive_parameter(spec, snapshot, "dev") == 13300


def test_indevolt_capacity_from_kwh(library: ProfileLibrary) -> None:
    """The rated capacity comes in kWh; its unit alone turns it into Wh (no extra factor)."""
    from types import SimpleNamespace

    from sunflow.devices.site import derive_parameter

    entities = dummy_entities("indevolt/indevolt")
    states = {
        e["entity_id"]: {"state": "1.79", "attributes": {"unit_of_measurement": "kWh"}} if e["unique_id"].endswith("_142") else {"state": "0"}
        for e in entities
    }
    snapshot = SimpleNamespace(devices=[{"id": "dev"}], entities=entities, states=states)

    assert derive_parameter(library.profiles["indevolt/indevolt"]["parameters"]["capacity_wh"], snapshot, "dev") == 1790


def test_marstek_actions(library: ProfileLibrary) -> None:
    """RS485 control first, then power and force mode; switching it off hands the battery back."""
    profile = library.profiles["marstek_modbus/venus"]
    entities = dummy_entities("marstek_modbus/venus")

    def calls(action: str) -> list[tuple[str, str | None, dict[str, Any]]]:
        return [(c.domain, c.entity_id, c.data) for c in plan_battery(profile, action, {}, VARIABLES, entities)]

    assert calls("force_charge") == [
        ("switch", "switch.d_rs485_control_mode", {}),
        ("number", "number.d_set_charge_power", {"value": 600}),
        ("select", "select.d_force_mode", {"option": "charge"}),
    ]
    assert calls("hold")[1] == ("select", "select.d_force_mode", {"option": "standby"})
    assert calls("release") == [("switch", "switch.d_rs485_control_mode", {})]
