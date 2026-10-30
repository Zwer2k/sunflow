"""Shipped inverter profiles against dummy devices with the entity keys of their integrations."""

from typing import Any

import pytest
from sunflow.devices.discovery import match_profile
from sunflow.devices.executor import plan_battery, plan_pv_limit
from sunflow.devices.profiles import ProfileLibrary, battery_actions
from sunflow.devices.resolver import bind_roles

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
}


def dummy_entities(profile_id: str) -> list[dict[str, Any]]:
    """Registry entries of the dummy device."""
    dummy = DUMMIES[profile_id]
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
    assert match_profile(library, DUMMIES[profile_id]["integration"], device, entities, True) == profile_id
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
    for action in [*battery_actions(profile), "release"]:
        for call in plan_battery(profile, action, {"capacity_wh": 10000}, {"P": 2000}, entities):
            assert call.entity_id in existing, (action, call)
    if "pv_limit" in profile:
        for action in ("set", "release"):
            for call in plan_pv_limit(profile, action, {}, {"limit_w": 1500}, entities):
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
