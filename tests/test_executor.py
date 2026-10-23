"""Tests for planning and executing profile actions."""

from pathlib import Path
from typing import Any

import pytest

from sunflow.executor import (
    EepromGuard,
    ExecutorError,
    PlannedCall,
    execute,
    plan_action,
    plan_setup,
    render,
)
from sunflow.profiles import ProfileLibrary

KEYS = {
    "number": ["passive_mode_grid_power", "passive_mode_battery_power_min", "passive_mode_battery_power_max"],
    "button": ["passive_mode_battery_charge_discharge", "passive_mode_update_timeout"],
    "select": ["passive_mode_timeout", "charger_use_mode"],
}
ENTITIES = [
    {"entity_id": f"{domain}.sofar_{key}", "unique_id": f"sofar_{key}"}
    for domain, keys in KEYS.items()
    for key in keys
]


class FakeClient:
    """Records action calls."""

    def __init__(self) -> None:
        """Initialize."""
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any] | None]] = []

    async def call_service(self, domain: str, service: str, data: dict[str, Any], target: dict[str, Any] | None) -> None:
        """Record a call."""
        self.calls.append((domain, service, data, target))


@pytest.fixture
def sofar(library: ProfileLibrary) -> dict[str, Any]:
    """Return the Sofar profile."""
    return library.profiles["solax_modbus/sofar"]


@pytest.mark.parametrize(
    ("action", "runtime", "expected"),
    [
        pytest.param("auto", {}, {"bat_min": -20000, "bat_max": 20000, "grid_target": -200}, id="auto"),
        pytest.param("force_charge", {"P": 500}, {"bat_min": 500, "bat_max": 20000, "grid_target": -200}, id="force_charge"),
        pytest.param("force_discharge", {"P": 800}, {"bat_min": -20000, "bat_max": -800, "grid_target": -200}, id="force_discharge"),
        pytest.param("absorb", {}, {"bat_min": 0, "bat_max": 20000, "grid_target": 0}, id="absorb"),
    ],
)
def test_plan_action(sofar: dict[str, Any], action: str, runtime: dict[str, float], expected: dict[str, float]) -> None:
    """Presets resolve to window parameters and four calls."""
    params, calls = plan_action(sofar, action, {}, runtime, ENTITIES)

    assert params == expected
    assert [(c.entity_id, c.service, c.data) for c in calls] == [
        ("number.sofar_passive_mode_grid_power", "set_value", {"value": expected["grid_target"]}),
        ("number.sofar_passive_mode_battery_power_min", "set_value", {"value": expected["bat_min"]}),
        ("number.sofar_passive_mode_battery_power_max", "set_value", {"value": expected["bat_max"]}),
        ("button.sofar_passive_mode_battery_charge_discharge", "press", {}),
    ]
    assert {c.storage for c in calls} == {"volatile"}


def test_device_parameters_override_defaults(sofar: dict[str, Any]) -> None:
    """Configured device parameters replace profile defaults."""
    params, _ = plan_action(sofar, "auto", {"max_charge": 5000, "grid_offset": -100}, {}, ENTITIES)

    assert params == {"bat_min": -20000, "bat_max": 5000, "grid_target": -100}


@pytest.mark.parametrize(
    ("action", "runtime", "entities", "message"),
    [
        pytest.param("teleport", {}, ENTITIES, "Unknown action", id="unknown_action"),
        pytest.param("force_charge", {}, ENTITIES, "Missing value for P", id="missing_runtime"),
        pytest.param("auto", {}, ENTITIES[1:], "No number entity for key passive_mode_grid_power", id="missing_entity"),
    ],
)
def test_plan_action_errors(sofar: dict[str, Any], action: str, runtime: dict[str, float], entities: list[dict[str, Any]], message: str) -> None:
    """Planning errors are reported."""
    with pytest.raises(ExecutorError, match=message):
        plan_action(sofar, action, {}, runtime, entities)


def test_render() -> None:
    """Placeholders resolve to numbers, also negated and inside text."""
    assert render("{{ x }}", {"x": 5}) == 5
    assert render("{{-x}}", {"x": 5}) == -5
    assert render("limit {{ x }} W", {"x": 5}) == "limit 5 W"
    assert render(3, {}) == 3


async def test_execute_dry_run_sends_nothing(sofar: dict[str, Any], tmp_path: Path) -> None:
    """A dry run marks calls but does not call Home Assistant."""
    client = FakeClient()
    _, calls = plan_action(sofar, "auto", {}, {}, ENTITIES)

    await execute(client, calls, {}, EepromGuard(tmp_path, 10), dry_run=True)

    assert client.calls == []
    assert {c.status for c in calls} == {"dry_run"}


async def test_execute_setup(sofar: dict[str, Any], tmp_path: Path) -> None:
    """Setup skips the mode switch when already set and counts EEPROM writes."""
    client = FakeClient()
    calls = plan_setup(sofar, ENTITIES)
    states = {"select.sofar_charger_use_mode": {"state": "Passive Mode"}}
    guard = EepromGuard(tmp_path, 10)

    await execute(client, calls, states, guard, dry_run=False)

    assert [(c.entity_id, c.status) for c in calls] == [
        ("select.sofar_passive_mode_timeout", "done"),
        ("button.sofar_passive_mode_update_timeout", "done"),
        ("select.sofar_charger_use_mode", "skipped"),
    ]
    assert [c[1] for c in client.calls] == ["select_option", "press"]
    assert client.calls[0][3] == {"entity_id": "select.sofar_passive_mode_timeout"}


async def test_press_skipped_without_preceding_write(tmp_path: Path) -> None:
    """A committing press is skipped when nothing was written before it."""
    client = FakeClient()
    calls = [
        PlannedCall(domain="select", service="select_option", data={"option": "A"}, entity_id="select.x", storage="eeprom", write="if_differs"),
        PlannedCall(domain="button", service="press", data={}, entity_id="button.commit", storage="eeprom", write="if_differs"),
    ]

    await execute(client, calls, {"select.x": {"state": "A"}}, EepromGuard(tmp_path, 10), dry_run=False)

    assert [c.status for c in calls] == ["skipped", "skipped"]
    assert client.calls == []


async def test_eeprom_guard_blocks(tmp_path: Path) -> None:
    """EEPROM writes beyond the daily limit are blocked, volatile writes are not."""
    client = FakeClient()
    guard = EepromGuard(tmp_path, 1)

    def calls() -> list[PlannedCall]:
        return [
            PlannedCall(domain="select", service="select_option", data={"option": "A"}, entity_id="select.mode", storage="eeprom", write="always"),
            PlannedCall(domain="number", service="set_value", data={"value": 1}, entity_id="number.ram", storage="volatile", write="always"),
        ]

    first, second = calls(), calls()
    await execute(client, first, {}, guard, dry_run=False)
    await execute(client, second, {}, guard, dry_run=False)

    assert [c.status for c in first] == ["done", "done"]
    assert [c.status for c in second] == ["blocked", "done"]
    assert EepromGuard(tmp_path, 1).allowed("select.mode") is False
