"""Tests for the device test sequence."""

import asyncio

import pytest

from sunflow.device_test import (
    DeviceTestState,
    Timings,
    choose_test,
    evaluate,
    run_device_test,
)
from sunflow.executor import PlannedCall

FAST = Timings(baseline=0.03, hold=0.06, settle=0.02, verify_restore=0.02, sample=0.01)


class FakeInverter:
    """Battery that follows the commanded window (or not, if broken)."""

    def __init__(self, idle_power: float, follows: bool = True, sign: int = 1) -> None:
        """Initialize."""
        self.power = idle_power
        self.idle_power = idle_power
        self.follows = follows
        self.sign = sign
        self.actions: list[tuple[str, dict[str, float]]] = []

    async def read(self) -> tuple[float | None, float | None]:
        """Return battery power as the configured sensor reports it."""
        return self.sign * self.power, 50.0

    async def run_action(self, action: str, runtime: dict[str, float]) -> list[PlannedCall]:
        """Apply the action to the simulated battery."""
        self.actions.append((action, runtime))
        if self.follows:
            if action == "limit_charge":
                self.power = min(self.power, runtime["P"])
            elif action == "limit_discharge":
                self.power = max(self.power, -runtime["P"])
            elif action == "force_charge":
                self.power = max(self.power, runtime["P"])
            elif action == "auto":
                self.power = self.idle_power
        return [PlannedCall(domain="button", service="press", data={}, entity_id="button.x", storage="volatile", write="always", status="done")]


@pytest.mark.parametrize(
    ("baseline", "action"),
    [
        pytest.param(2000, "limit_charge", id="charging"),
        pytest.param(-1500, "limit_discharge", id="discharging"),
        pytest.param(50, "force_charge", id="idle"),
    ],
)
async def test_successful_test(baseline: float, action: str) -> None:
    """The test picks a harmless command, sees it work and restores."""
    inverter = FakeInverter(baseline)
    state = DeviceTestState(device_id="sofar")

    await run_device_test(state, inverter.read, inverter.run_action, FAST)

    assert state.phase == "done"
    assert state.test_action == action
    assert (state.control_works, state.sign_correct, state.restored) == (True, True, True)
    assert [a for a, _ in inverter.actions] == [action, "auto"]


async def test_inverted_sign_is_detected() -> None:
    """A battery sensor with the wrong sign fails the sign check."""
    inverter = FakeInverter(50, sign=-1)
    state = DeviceTestState(device_id="sofar")

    await run_device_test(state, inverter.read, inverter.run_action, FAST)

    assert state.test_action == "force_charge"
    assert state.control_works is True
    assert state.sign_correct is False


async def test_ignored_command_is_detected() -> None:
    """An inverter that ignores the command fails the control check but is restored."""
    inverter = FakeInverter(2000, follows=False)
    state = DeviceTestState(device_id="sofar")

    await run_device_test(state, inverter.read, inverter.run_action, FAST)

    assert state.control_works is False
    assert inverter.actions[-1][0] == "auto"


async def test_abort_restores() -> None:
    """Cancelling during the command phase restores self-consumption."""
    inverter = FakeInverter(2000)
    state = DeviceTestState(device_id="sofar")
    slow = Timings(baseline=0.02, hold=10, settle=5, verify_restore=1, sample=0.01)

    task = asyncio.create_task(run_device_test(state, inverter.read, inverter.run_action, slow))
    while state.phase != "command":
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.phase == "aborted"
    assert state.restored is True
    assert [a for a, _ in inverter.actions] == ["limit_charge", "auto"]


async def test_failure_restores() -> None:
    """An error after the command was sent still restores."""
    inverter = FakeInverter(2000)
    reads = 0

    async def flaky_read() -> tuple[float | None, float | None]:
        nonlocal reads
        reads += 1
        if reads > 4:
            raise RuntimeError("connection lost")
        return await inverter.read()

    state = DeviceTestState(device_id="sofar")
    await run_device_test(state, flaky_read, inverter.run_action, FAST)

    assert state.phase == "failed"
    assert "connection lost" in state.error
    assert [a for a, _ in inverter.actions] == ["limit_charge", "auto"]


def test_no_reading_means_no_command() -> None:
    """Without battery readings nothing is written."""
    async def no_reading() -> tuple[float | None, float | None]:
        return None, None

    inverter = FakeInverter(0)
    state = DeviceTestState(device_id="sofar")
    asyncio.run(run_device_test(state, no_reading, inverter.run_action, FAST))

    assert state.phase == "failed"
    assert inverter.actions == []


def test_choose_and_evaluate() -> None:
    """Thresholds and tolerances."""
    assert choose_test(301)[0] == "limit_charge"
    assert choose_test(-301)[0] == "limit_discharge"
    assert choose_test(0)[0] == "force_charge"
    assert evaluate("limit_charge", 300, [420, 410]) == (True, True)
    assert evaluate("limit_charge", 300, [1500, 1600]) == (False, True)
    assert evaluate("force_charge", 500, []) == (False, False)
