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
