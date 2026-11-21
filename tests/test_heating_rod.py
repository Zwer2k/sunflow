"""Heating rod: a relay per phase."""

import pytest
from sunflow.api.devices import ManualDevice, _manual_setup
from sunflow.control.controller import relay_states
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import ExecutorError, plan_steps

SNAPSHOT = Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None)


@pytest.mark.parametrize(
    ("count", "bound", "day", "expected"),
    [
        pytest.param(0, ["l1", "l2", "l3"], 0, {"relay_l1": "off", "relay_l2": "off", "relay_l3": "off"}, id="off"),
        pytest.param(1, ["l1", "l2", "l3"], 0, {"relay_l1": "on", "relay_l2": "off", "relay_l3": "off"}, id="one, day 0"),
        pytest.param(1, ["l1", "l2", "l3"], 1, {"relay_l1": "off", "relay_l2": "on", "relay_l3": "off"}, id="one, day 1"),
        pytest.param(2, ["l1", "l2", "l3"], 2, {"relay_l1": "on", "relay_l2": "off", "relay_l3": "on"}, id="two, day 2"),
        pytest.param(3, ["l1", "l2", "l3"], 5, {"relay_l1": "on", "relay_l2": "on", "relay_l3": "on"}, id="all"),
        pytest.param(1, ["l1", "l3"], 1, {"relay_l1": "off", "relay_l2": "off", "relay_l3": "on"}, id="two relays"),
    ],
)
def test_relay_states(count: int, bound: list[str], day: int, expected: dict[str, str]) -> None:
    """The planned number of phases is on; which ones moves on every day."""
    assert relay_states(count, bound, day) == expected


def test_relays_setup() -> None:
    """Each relay keeps its phase; the steps are 0 … n times the power of one."""
    overrides, parameters, steps = _manual_setup(
        ManualDevice(
            name="Heizstab",
            type="relays",
            kind="heater",
            relay_entities=["switch.rod_l1", None, "switch.rod_l3"],
            relay_power_w=2000,
            temperature_entity="sensor.tank",
            tank_l=300,
        ),
        SNAPSHOT,
    )

    assert {role: o.entity_id for role, o in overrides.items()} == {
        "switch_l1": "switch.rod_l1",
        "switch_l3": "switch.rod_l3",
        "temperature": "sensor.tank",
    }
    assert steps == {"0": 0, "1": 2000, "2": 4000}
    assert parameters == {"max_power": 4000, "tank_l": 300}


def test_relay_switch_state_from_runtime() -> None:
    """A switch step takes its state from the call (the relay chosen); anything but on/off is refused."""
    step = [{"switch": {"role": "switch_l2", "state": "{{ relay_l2 }}"}}]
    roles = {"switch_l2": "switch.rod_l2"}

    (call,) = plan_steps(step, {"relay_l2": "on"}, [], "ram", roles)

    assert (call.service, call.entity_id) == ("turn_on", "switch.rod_l2")
    with pytest.raises(ExecutorError):
        plan_steps(step, {"relay_l2": "maybe"}, [], "ram", roles)
