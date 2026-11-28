"""Every wallbox profile follows the wallbox standard (design/wallbox.md)."""

import json
from typing import Any

import pytest
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
