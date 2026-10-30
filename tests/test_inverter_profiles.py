"""Shipped inverter profiles against dummy devices with the entity keys of their integrations."""

from pathlib import Path
from typing import Any

from dummy_inverter import DummyInverter
import pytest
from sunflow.config.models import Connection, DeviceConfig, SunflowConfig
from sunflow.control.controller import device_variables, percent_variables
from sunflow.devices.discovery import Snapshot, match_profile
from sunflow.devices.executor import EepromGuard, execute, plan_battery, plan_pv_limit
from sunflow.devices.profiles import ProfileLibrary, battery_actions, controls_battery
from sunflow.devices.resolver import bind_roles, preview
from sunflow.devices.site import derive_parameter
from sunflow.direct.hub import DirectHub, direct_device_id

# Entity keys per domain as the integrations create them (unique_id = "<prefix>_<key>")
DUMMIES: dict[str, dict[str, Any]] = {
    "solax_modbus/solax_gen4_5": {
        "integration": "solax_modbus",
        "prefix": "solax",
        "keys": {
            "sensor": [
                "battery_capacity", "battery_power_charge", "pv_power_total", "pv_power_1", "pv_power_2",
                "measured_power", "house_load", "grid_import_total", "grid_export_total",
                "battery_input_energy_total", "battery_output_energy_total",
            ],
            "select": ["remotecontrol_power_control", "remotecontrol_set_type"],
            "number": ["remotecontrol_active_power", "remotecontrol_autorepeat_duration", "remotecontrol_duration"],
            "button": ["remotecontrol_trigger"],
        },
    },
    "huawei_solar/sun2000_luna2000": {
        "integration": "huawei_solar",
        "prefix": "HV2150012345",
        "keys": {
            "sensor": [
                "storage_state_of_capacity", "storage_charge_discharge_power", "input_power", "active_power",
                "power_meter_active_power", "accumulated_yield_energy", "grid_accumulated_energy",
                "grid_exported_energy", "storage_total_charge", "storage_total_discharge", "storage_rated_capacity",
                "storage_maximum_charge_power", "storage_maximum_discharge_power", "P_max",
            ],
            "number": [
                "storage_maximum_charging_power", "storage_maximum_discharging_power",
                "active_power_fixed_value_derating",
            ],
        },
    },
    "huawei_solar/pv_only": {
        "integration": "huawei_solar",
        "prefix": "HV2150099999",
        "has_battery": False,
        "keys": {
            "sensor": ["active_power", "input_power", "daily_yield_energy", "device_status", "P_max"],
            "number": ["active_power_fixed_value_derating"],
        },
    },
    "solaredge_modbus/storedge": {
        "integration": "solaredge_modbus",
        "prefix": "7E123456",
        "keys": {
            "sensor": [
                "dc_power", "ac_power", "ac_energy", "battery_4A1B2C_dc_power", "battery_4A1B2C_state_of_energy",
                "battery_4A1B2C_energy_max", "battery_4A1B2C_max_charge_power", "battery_4A1B2C_max_discharge_power",
                "battery_4A1B2C_energy_imported", "battery_4A1B2C_energy_exported",
                "meter_slot_1_ac_power", "meter_slot_1_energy_imported", "meter_slot_1_energy_exported",
            ],
            "select": ["storage_control_mode", "storage_command_mode", "storage_default_mode"],
            "number": ["charge_limit", "discharge_limit", "backup_reserve"],
        },
    },
    "goodwe/hybrid": {
        "integration": "goodwe",
        "unique_ids": {
            "sensor": [
                f"goodwe-{key}-9010KETU000W0000"
                for key in (
                    "battery_soc", "pbattery1", "ppv", "active_power", "house_consumption", "e_total",
                    "meter_e_total_imp", "meter_e_total_exp", "e_bat_charge_total", "e_bat_discharge_total",
                )
            ],
            "select": ["goodwe-operation_mode-9010KETU000W0000"],
        },
    },
    "kostal_plenticore/plenticore": {
        "integration": "kostal_plenticore",
        "unique_ids": {
            "sensor": [
                f"entry1_{key}"
                for key in (
                    "devices:local:battery_SoC", "devices:local:battery_P", "devices:local_Dc_P", "devices:local:pv1_P",
                    "devices:local:pv2_P", "devices:local:ac_P", "devices:local_Grid_P", "devices:local_Home_P",
                )
            ],
            "number": ["entry1_devices:local_Battery:MinSoc", "entry1_devices:local_Inverter:ActivePowerLimitation"],
        },
    },
    "fronius/gen24": {
        "integration": "fronius",
        "unique_ids": {
            "sensor": [
                "net1-power_flow-power_battery", "net1-power_flow-power_photovoltaics", "net1-power_flow-power_grid",
                "net1-power_flow-power_load", "BAT123-state_of_charge", "INV1-energy_total",
                "MTR1-energy_real_ac_plus", "MTR1-energy_real_ac_minus",
            ],
            "number": [
                f"INV1-modbus-{key}"
                for key in ("battery_charge_power_limit", "battery_discharge_power_limit", "ac_power_limit")
            ],
            "switch": [
                f"INV1-modbus-{key}"
                for key in (
                    "battery_charge_power_limit_enabled", "battery_discharge_power_limit_enabled",
                    "ac_power_limit_enabled",
                )
            ],
        },
    },
}


def dummy_entities(profile_id: str) -> list[dict[str, Any]]:
    """Registry entries of the dummy device."""
    dummy = DUMMIES[profile_id]
    if "unique_ids" in dummy:
        return [
            {"entity_id": f"{domain}.e{i}", "unique_id": unique_id, "device_id": "dev"}
            for domain, unique_ids in dummy["unique_ids"].items()
            for i, unique_id in enumerate(unique_ids)
        ]
    return [
        {"entity_id": f"{domain}.{dummy['prefix']}_{key}", "unique_id": f"{dummy['prefix']}_{key}", "device_id": "dev"}
        for domain, keys in dummy["keys"].items()
        for key in keys
    ]


@pytest.mark.parametrize("profile_id", list(DUMMIES))
def test_dummy_matches_its_profile(library: ProfileLibrary, profile_id: str) -> None:
    """The dummy is recognized as this profile, and every required role binds."""
    entities = dummy_entities(profile_id)
    device = {"id": "dev", "manufacturer": None, "model": None}
    has_battery = DUMMIES[profile_id].get("has_battery", True)
    assert match_profile(library, DUMMIES[profile_id]["integration"], device, entities, has_battery) == profile_id
    profile = library.profiles[profile_id]
    bound = bind_roles(profile, entities)
    missing = [role for role, spec in profile["roles"].items() if not spec.get("optional") and bound[role] is None]
    assert missing == []


@pytest.mark.parametrize("profile_id", list(DUMMIES))
def test_every_action_resolves(library: ProfileLibrary, profile_id: str) -> None:
    """Every battery action and the PV limit plan calls on existing entities."""
    profile = library.profiles[profile_id]
    entities = dummy_entities(profile_id)
    existing = {e["entity_id"] for e in entities}
    variables = {
        "P": 2000, "limit_w": 1500, "max_charge": 5000, "max_discharge": 5000, "max_ac_power": 10000,
        "device_id_batteries": "b", "P_pct": 40, "limit_pct": 15, "role_battery_soc": 55, "min_soc": 10,
    }
    actions = [*battery_actions(profile), "release"] if controls_battery(profile) else []
    for action in actions:
        for call in plan_battery(profile, action, {"capacity_wh": 10000}, variables, entities):
            assert call.entity_id is None or call.entity_id in existing, (action, call)
    if "pv_limit" in profile:
        for action in ("set", "release"):
            for call in plan_pv_limit(profile, action, {}, variables, entities):
                assert call.entity_id in existing, (action, call)


def test_solax_actions(library: ProfileLibrary) -> None:
    """SolaX remote control mode 1: charge and discharge by battery power, hold as no discharge."""
    profile = library.profiles["solax_modbus/solax_gen4_5"]
    entities = dummy_entities("solax_modbus/solax_gen4_5")

    def calls(action: str) -> list[tuple[str, Any]]:
        return [(c.entity_id, c.data) for c in plan_battery(profile, action, {}, {"P": 2500}, entities)]

    assert battery_actions(profile) == {"auto", "hold", "force_charge", "force_discharge"}
    assert calls("force_discharge") == [
        ("select.solax_remotecontrol_power_control", {"option": "Enabled Battery Control"}),
        ("number.solax_remotecontrol_active_power", {"value": -2500}),
        ("number.solax_remotecontrol_autorepeat_duration", {"value": 1800}),
        ("button.solax_remotecontrol_trigger", {}),
    ]
    assert calls("hold")[0][1] == {"option": "Enabled No Discharge"}
    assert calls("release") == [
        ("select.solax_remotecontrol_power_control", {"option": "Disabled"}),
        ("button.solax_remotecontrol_trigger", {}),
    ]


def test_sofar_is_not_taken_for_solax(library: ProfileLibrary) -> None:
    """Both run in solax_modbus; the Sofar keeps its passive mode profile."""
    entities = [
        {"entity_id": f"sensor.sofar_{key}", "unique_id": f"sofar_{key}", "device_id": "dev"}
        for key in ("charger_use_mode", "passive_mode_battery_charge_discharge", "battery_power_charge")
    ]
    assert match_profile(library, "solax_modbus", {"id": "dev"}, entities, True) == "solax_modbus/sofar"


def test_huawei_battery_actions_name_the_battery_device(library: ProfileLibrary) -> None:
    """Forced charging is an action of the battery sub-device; holding caps the discharge."""
    profile = library.profiles["huawei_solar/sun2000_luna2000"]
    entities = dummy_entities("huawei_solar/sun2000_luna2000")
    variables = {"P": 3000, "max_discharge": 5000, "device_id_batteries": "bat-device"}
    charge = plan_battery(profile, "force_charge", {}, variables, entities)
    assert [(c.domain, c.service, c.data) for c in charge] == [
        ("number", "set_value", {"value": 5000}),
        ("huawei_solar", "forcible_charge", {"device_id": "bat-device", "duration": 30, "power": 3000}),
    ]
    hold = plan_battery(profile, "hold", {}, variables, entities)
    assert [c.data for c in hold] == [{"device_id": "bat-device"}, {"value": 0}]
    assert hold[1].storage == "eeprom"
    assert battery_actions(profile) == {"auto", "hold", "force_charge", "force_discharge"}


def victron_registers() -> tuple[dict[int, int], dict[int, int]]:
    """GX at 62 % SoC, discharging 1.2 kW, 3 x 400 W import, 2 kW DC PV and 500 W AC PV."""
    inputs = {842: 0x10000 - 1200, 843: 62, 820: 400, 821: 400, 822: 400, 850: 2000, 808: 500}
    inputs |= {a: 0 for a in (809, 810, 811, 812, 813)}
    return {2901: 100}, inputs


async def test_victron_over_modbus(library: ProfileLibrary, tmp_path: Path) -> None:
    """Values read by unit 100 sum up per role; holding writes the current SoC as minimum."""
    profile = library.profiles["modbus/victron_gx"]
    holding, inputs = victron_registers()
    async with DummyInverter(holding, inputs) as dummy:
        device = DeviceConfig(
            id="victron1", ha_device_id=direct_device_id("victron1"), profile="modbus/victron_gx", profile_version=1,
            name="Victron", connection=Connection(host="127.0.0.1", port=dummy.port),
        )
        hub = DirectHub()
        await hub.sync(SunflowConfig(devices=[device]), library)
        await hub.poll()
        snapshot = hub.contribute(Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None))
        values = {r.role: r.value for r in preview(profile, snapshot, device.ha_device_id, {})}
        assert values == {"battery_soc": 62, "battery_power": -1200, "pv_power": 2500, "grid_power": 1200}

        variables = device_variables(profile, device, snapshot)
        direct = hub.by_ha_device(device.ha_device_id)
        for action in ("hold", "force_charge", "auto"):
            calls = plan_battery(profile, action, {}, variables, [])
            await execute(None, calls, {}, EepromGuard(tmp_path, 30), dry_run=False, direct=direct)
        await hub.stop()
    assert dummy.writes == [(2901, [620]), (2901, [1000]), (2901, [100])]


def test_solaredge_reads_attached_battery_and_meter(library: ProfileLibrary) -> None:
    """Wildcard keys find the battery and meter by serial; PV adds the battery's charge to the DC power."""
    profile = library.profiles["solaredge_modbus/storedge"]
    entities = dummy_entities("solaredge_modbus/storedge")
    bound = bind_roles(profile, entities)
    assert bound["battery_power"] == "sensor.7E123456_battery_4A1B2C_dc_power"
    assert bound["grid_power"] == "sensor.7E123456_meter_slot_1_ac_power"
    assert bound["ac_power"] == "sensor.7E123456_ac_power"
    states = {
        "sensor.7E123456_dc_power": {"state": "2600", "attributes": {"unit_of_measurement": "W"}},
        "sensor.7E123456_battery_4A1B2C_dc_power": {"state": "1400", "attributes": {"unit_of_measurement": "W"}},
        "sensor.7E123456_battery_4A1B2C_energy_max": {"state": "9.7", "attributes": {"unit_of_measurement": "kWh"}},
    }
    snapshot = Snapshot(entries=[], devices=[], entities=entities, states=states, energy_prefs=None)
    values = {r.role: r.value for r in preview(profile, snapshot, "dev", {})}
    assert values["pv_power"] == 4000
    assert derive_parameter(profile["parameters"]["capacity_wh"], snapshot, "dev") == 9700
    hold = plan_battery(profile, "hold", {}, {"max_charge": 5000}, entities)
    assert [c.data for c in hold] == [{"option": "maximize_self_consumption"}, {"value": 5000}, {"value": 0}]


def test_percent_variables() -> None:
    """Integrations taking percent get P of the action's direction and the limit of the AC power."""
    variables = {"P": 1500, "max_charge": 6000, "max_discharge": 3000, "limit_w": 2500, "max_ac_power": 10000}
    assert percent_variables("limit_charge", variables)["P_pct"] == 25
    assert percent_variables("limit_discharge", variables)["P_pct"] == 50
    assert percent_variables("pv_set", variables)["limit_pct"] == 25
    assert "P_pct" not in percent_variables("auto", {"max_charge": 6000})
