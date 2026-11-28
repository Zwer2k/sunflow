"""Every wallbox profile follows the wallbox standard (design/wallbox.md)."""

import json
from typing import Any

import pytest
from sunflow.devices.discovery import match_profile
from sunflow.devices.executor import plan_steps
from sunflow.devices.profiles import ProfileLibrary
from sunflow.devices.resolver import bind_roles


def _wallboxes(library: ProfileLibrary) -> list[str]:
    return sorted(p for p, profile in library.profiles.items() if "loadpoint" in profile["capabilities"])


def _steps(profile: dict[str, Any]) -> str:
    return json.dumps({k: profile.get(k) for k in ("charge_control", "phase_switch")})


def _problems(profile: dict[str, Any]) -> list[str]:
    """What the profile lacks of the standard."""
    roles, params, control = profile["roles"], profile.get("parameters", {}), profile.get("charge_control", {})
    steps = _steps(profile)
    checks = {
        "role charge_power": "charge_power" in roles and not roles["charge_power"].get("optional"),
        # Only a loadpoint that knows the car itself (evcc) may do without
        "role connected": "connected" in roles or "mode" in roles,
        "action charge": "charge" in control,
        "action stop": "stop" in control,
        "release or remember": "release" in control or "remember" in control,
        "parameter max_power": "max_power" in params,
        "current limits": "current_a" not in steps or {"min_current_a", "max_current_a", "phases"} <= params.keys(),
        "phase switching thresholds": "phase_switch" not in profile or {"phase_mode", "phase_up_w", "phase_down_w"} <= params.keys(),
        "eeprom guard": '"eeprom"' not in steps or "eeprom_guard" in profile,
    }
    return [name for name, ok in checks.items() if not ok]


def test_wallboxes_follow_the_standard(library: ProfileLibrary) -> None:
    """Roles, parameters and actions the control relies on are there in every wallbox profile."""
    wallboxes = _wallboxes(library)

    assert {p: problems for p in wallboxes if (problems := _problems(library.profiles[p]))} == {}
    assert len(wallboxes) >= 3


@pytest.mark.parametrize(
    ("spec", "bound"),
    [
        pytest.param({"key": "charger_operation_mode"}, "switch.op", id="any-domain-shortest-first"),
        pytest.param({"key": "charger_operation_mode", "domain": "sensor"}, "sensor.op", id="sensor-only"),
    ],
)
def test_role_domain_tells_platforms_apart(spec: dict[str, str], bound: str) -> None:
    """Zaptec names its operation mode sensor and switch alike: the role picks the domain."""
    entities = [
        {"entity_id": "switch.op", "unique_id": "u_charger_operation_mode"},
        {"entity_id": "sensor.op", "unique_id": "uu_charger_operation_mode"},
    ]

    assert bind_roles({"roles": {"mode": spec}}, entities) == {"mode": bound}


# Unique ids as the integrations build them (see each profile's header)
DUMMIES: dict[str, dict[str, list[str]]] = {
    "peblar/charger": {
        "sensor": ["S1_power_total", "S1_cp_state", "S1_energy_session"],
        "number": ["S1_charge_current_limit"], "switch": ["S1_charge", "S1_force_single_phase"],
    },
    "nrgkick/charger": {
        "sensor": ["N1_total_active_power", "N1_status", "N1_charged_energy", "N1_rated_current"],
        "number": ["N1_current_set", "N1_phase_count"], "switch": ["N1_charging_enabled"],
    },
    "openevse/charger": {
        "sensor": ["oe-current_power", "oe-usage_session", "oe-max_amps"], "binary_sensor": ["oe-vehicle"],
        "number": ["oe-charge_rate"], "select": ["oe-override_state"],
    },
    "v2c/charger": {
        "sensor": ["E1_charge_power", "E1_charge_energy"], "binary_sensor": ["E1_connected"],
        "number": ["E1_intensity", "E1_min_intensity", "E1_max_intensity"], "switch": ["E1_paused"],
    },
    "wallbox/charger": {
        "sensor": ["charging_power-123", "status_description-123", "added_energy-123", "max_available_power-123"],
        "number": ["max_charging_current-123"], "switch": ["paused-123"],
    },
    "lektrico/charger": {
        "sensor": ["L1_power", "L1_state", "L1_energy"], "number": ["L1_dynamic_limit"], "switch": ["L1_force_single_phase"],
    },
    "easee/charger": {
        "sensor": ["EH1_power", "EH1_status", "EH1_session_energy", "EH1_dynamic_charger_limit", "EH1_max_charger_limit"],
    },
    "keba/charger": {
        "sensor": ["keba-1-P", "keba-1-PF", "keba-1-E pres", "keba-1-Curr user", "keba-1-Curr HW"],
        "binary_sensor": ["keba-1-Plug_EV"],
    },
    "heidelberg_energy_control/charger": {
        "sensor": ["E2_charging_power", "E2_session_energy", "E2_hw_max_current"],
        "binary_sensor": ["E2_is_plugged", "E2_is_charging"], "number": ["E2_virtual_current"], "switch": ["E2_virtual_enable"],
    },
    "unite_ev_charger/charger": {
        "sensor": ["U1_active_power", "U1_session_energy", "U1_set_current"], "binary_sensor": ["U1_vehicle_connected"],
        "number": ["U1_charge_current"], "switch": ["U1_charging_enabled"], "select": ["U1_phase_select"],
    },
    "cfos_powerbrain/charger": {
        "sensor": ["C1_power", "C1_s0_1_power", "C1_session_energy"], "binary_sensor": ["C1_vehicle_connected"],
        "number": ["C1_charging_current_limit"], "switch": ["C1_charging_enabled"], "select": ["C1_phase_mode"],
    },
    "mennekes_amtron/charger": {
        "sensor": ["E3_total_power", "E3_vehicle_state", "E3_session_energy"], "number": ["E3_hems_current_limit"],
    },
    "smaev/charger": {
        "sensor": ["S2-charging_station_power", "S2-charging_session_status", "S2-charging_session_energy"],
        "number": ["S2-charge_current_limit"], "select": ["S2-operating_mode_of_charge_session"],
    },
    "wattpilot/charger": {
        "sensor": ["wp-nrg", "wp-car", "wp-wh"], "number": ["wp-amp"], "select": ["wp-frc", "wp-psm"],
    },
    "ocpp/chargepoint": {
        "sensor": ["ocpp.cp1.power_active_import.sensor", "ocpp.cp1.status_connector.sensor", "ocpp.cp1.energy_session.sensor"],
        "number": ["number.ocpp.cp1.maximum_current"],
    },
}
VARIABLES = {"current_a": 10, "power_w": 6900, "max_current_a": 16, "role_stored_current": 16, "device_serial": "s", "device_id": "d"}


def _entities(profile_id: str) -> list[dict[str, Any]]:
    return [
        {"entity_id": f"{domain}.e{i}", "unique_id": unique_id, "device_id": "dev"}
        for domain, unique_ids in DUMMIES[profile_id].items()
        for i, unique_id in enumerate(unique_ids)
    ]


@pytest.mark.parametrize("profile_id", list(DUMMIES))
def test_wallbox_dummy_matches_and_every_step_resolves(library: ProfileLibrary, profile_id: str) -> None:
    """The integration's entities are recognized, required roles bind and every step finds its entity."""
    profile = library.profiles[profile_id]
    entities = _entities(profile_id)
    existing = {e["entity_id"] for e in entities}

    assert match_profile(library, profile["match"]["integration"], {"id": "dev"}, entities, False) == profile_id
    bound = bind_roles(profile, entities)
    assert [r for r, spec in profile["roles"].items() if not spec.get("optional") and bound[r] is None] == []
    sections = [*profile["charge_control"].values(), *(v for k, v in profile.get("phase_switch", {}).items() if k != "remember")]
    for steps in [s for s in sections if isinstance(s, list)]:
        for call in plan_steps(steps, VARIABLES, entities, "volatile"):
            assert call.entity_id is None or call.entity_id in existing, (profile_id, call)


@pytest.mark.parametrize(
    ("profile_id", "role", "unique_id"),
    [
        # Short keys must not take a longer entity: KEBA's power factor, cFos' S0 meters
        pytest.param("keba/charger", "charge_power", "keba-1-P", id="keba-power-not-power-factor"),
        pytest.param("cfos_powerbrain/charger", "charge_power", "C1_power", id="cfos-own-power"),
        pytest.param("v2c/charger", "stored_current", "E1_intensity", id="v2c-intensity-not-its-limits"),
    ],
)
def test_short_keys_bind_the_right_entity(library: ProfileLibrary, profile_id: str, role: str, unique_id: str) -> None:
    """Where an integration's keys end alike, the role still takes the charger's own entity."""
    entities = _entities(profile_id)
    by_id = {e["entity_id"]: e["unique_id"] for e in entities}

    assert by_id[bind_roles(library.profiles[profile_id], entities)[role]] == unique_id


def _direct_wallboxes(library: ProfileLibrary) -> list[str]:
    return sorted(p for p in _wallboxes(library) if "connection" in library.profiles[p])


def test_direct_wallboxes_are_there(library: ProfileLibrary) -> None:
    """Wallboxes without a Home Assistant integration, straight over Modbus TCP."""
    assert len(_direct_wallboxes(library)) >= 10


@pytest.mark.parametrize(
    "profile_id",
    [
        "modbus/abb_terra_ac", "modbus/alfen_eve", "modbus/webasto_next", "modbus/vestel_evc04", "modbus/keba_p30",
        "modbus/bender_cc613", "modbus/mennekes_hcc3", "modbus/phoenix_ev_eth", "modbus/compleo_ebox",
        "modbus/cfos_powerbrain", "modbus/amperfied_connect",
    ],
)
def test_direct_wallbox_roles_and_writes(library: ProfileLibrary, profile_id: str) -> None:
    """Required roles name the profile's points; every step writes a number to a register."""
    profile = library.profiles[profile_id]
    entities = [{"entity_id": f"sensor.d_{key}", "unique_id": f"d_{key}", "device_id": "dev"} for key in profile["connection"]["points"]]
    params = {name: spec["default"] for name, spec in profile["parameters"].items() if "default" in spec}

    bound = bind_roles(profile, entities)
    assert [r for r, spec in profile["roles"].items() if not spec.get("optional") and bound[r] is None] == []
    sections = [*profile["charge_control"].values(), *(v for k, v in profile.get("phase_switch", {}).items() if k != "remember")]
    for steps in sections:
        for call in plan_steps(steps, params | VARIABLES, entities, "volatile"):
            assert (call.domain, isinstance(call.data["value"], int | float)) == ("direct", True), (profile_id, call)


def test_eebus_wallbox_through_the_eebus_app(library: ProfileLibrary) -> None:
    """The EEBUS wallbox reads and writes the EEBUS App's API for its SKI."""
    profile = library.profiles["eebus/wallbox"]
    entities = [{"entity_id": f"sensor.d_{key}", "unique_id": f"d_{key}", "device_id": "dev"} for key in profile["connection"]["points"]]
    params = {name: spec["default"] for name, spec in profile["parameters"].items() if "default" in spec}

    calls = {action: plan_steps(steps, params | VARIABLES, entities, "volatile") for action, steps in profile["charge_control"].items()}

    assert [r for r, spec in profile["roles"].items() if not spec.get("optional") and bind_roles(profile, entities)[r] is None] == []
    assert {a: [(c.data["spec"].get("method", "POST"), c.data["spec"]["path"], c.data["value"]) for c in cs] for a, cs in calls.items()} == {
        "charge": [("POST", "/devices/{remote}/limit", {"current_a": 10})],
        "stop": [("POST", "/devices/{remote}/limit", {"current_a": 0})],
        "release": [("DELETE", "/devices/{remote}/limit", None)],
    }
