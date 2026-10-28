"""Tests for carrying out the plan on the battery."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sunflow.config_store import ControlSettings, DeviceConfig, SunflowConfig
from sunflow.control import (
    BatteryController,
    DeviceAccess,
    TakeoverMarker,
    desired_action,
    evcc_controls_battery,
)
from sunflow.executor import PlannedCall

NOW = datetime(2026, 10, 29, 10, 5, tzinfo=UTC)
SOFAR = {"capabilities": ["battery_window"]}


def _plan(action: str = "force_charge", power: float | None = 3000) -> SimpleNamespace:
    slot = SimpleNamespace(start=NOW.replace(minute=0), end=NOW.replace(minute=15), action=action, action_power_w=power)
    return SimpleNamespace(plan=SimpleNamespace(slots=[slot]))


class FakeDevice:
    """Records writes; the 'device' holds the last written values."""

    def __init__(self, mode: str = "Self Use") -> None:
        """Start in the given operating mode."""
        self.values: dict[str, float] = {}
        self.mode = mode
        self.writes: list[tuple[str, dict[str, float]]] = []
        self.modes: list[str] = []
        self.fail = False

    def access(self) -> DeviceAccess:
        """Calls as the controller gets them from the API layer."""
        async def plan(action: str, runtime: dict[str, float]) -> tuple[list[PlannedCall], dict[str, Any]]:
            target = {"auto": -10000, "hold": 0, "force_charge": runtime.get("P", 0)}[action]
            call = PlannedCall(domain="number", service="set_value", data={"value": target}, entity_id="number.min", storage="volatile", write="always")
            states = {"number.min": {"state": str(self.values["number.min"])}} if "number.min" in self.values else {}
            return [call], states

        async def run(calls: list[PlannedCall], states: dict[str, Any]) -> None:
            if self.fail:
                raise RuntimeError("Modbus timeout")
            for call in calls:
                self.values[call.entity_id] = call.data["value"]
                call.status = "done"
            self.writes.append(("window", dict(self.values)))

        async def ensure_mode() -> str | None:
            previous, self.mode = self.mode, "Passive Mode"
            self.modes.append(self.mode)
            return previous if previous != "Passive Mode" else None

        async def set_mode(option: str) -> None:
            self.mode = option
            self.modes.append(option)

        return DeviceAccess(plan=plan, run=run, ensure_mode=ensure_mode, set_mode=set_mode)


def _config(enabled: bool = True, device: bool = True, tested: bool = True) -> SunflowConfig:
    battery = DeviceConfig(
        id="bat", ha_device_id="h", profile="sofar", profile_version=1, name="Sofar",
        test_passed_at=NOW if tested else None,
    )
    return SunflowConfig(devices=[battery], control=ControlSettings(enabled=enabled, devices={"bat": device}))


def _controller(tmp_path: Path, device: FakeDevice) -> BatteryController:
    return BatteryController(TakeoverMarker(tmp_path), lambda d: device.access(), lambda: {"sofar": SOFAR})


SNAPSHOT = SimpleNamespace(states={})


@pytest.mark.parametrize(
    ("plan", "plan_at", "expected"),
    [
        pytest.param(_plan(), NOW, ("force_charge", 3000, "nach Plan"), id="current"),
        pytest.param(None, None, ("auto", None, "Kein Plan – Eigenverbrauch"), id="no-plan"),
        pytest.param(_plan(), NOW - timedelta(hours=1), ("auto", None, "Plan älter als 30 min – Eigenverbrauch"), id="stale"),
    ],
)
def test_desired_action(plan: SimpleNamespace | None, plan_at: datetime | None, expected: tuple) -> None:
    """The slot running now, otherwise self-consumption."""
    assert desired_action(plan, plan_at, NOW) == expected


async def test_takes_over_writes_once_and_hands_back(tmp_path: Path) -> None:
    """Switching on sets the mode once; unchanged values are not written again; off restores both."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)

    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW)
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=1))

    assert device.modes == ["Passive Mode"]
    assert device.writes == [("window", {"number.min": 3000})]
    assert controller.statuses["bat"].active
    assert TakeoverMarker(tmp_path).get() == {"bat": "Self Use"}

    await controller.tick(_config(enabled=False), _plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=2))

    assert device.values == {"number.min": -10000}
    assert device.mode == "Self Use"
    assert TakeoverMarker(tmp_path).get() == {}
    assert not controller.statuses["bat"].active


async def test_rebooted_inverter_gets_the_values_again(tmp_path: Path) -> None:
    """The window lives in RAM; when the device shows other values they are written again."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW)

    device.values["number.min"] = 0
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=1))

    assert len(device.writes) == 2


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        pytest.param(_config(device=False), None, id="device-off"),
        pytest.param(_config(enabled=False), None, id="main-off"),
        pytest.param(_config(tested=False), "Gerätetest noch nicht bestanden", id="untested"),
    ],
)
async def test_nothing_written_without_both_switches_and_test(tmp_path: Path, config: SunflowConfig, reason: str | None) -> None:
    """A device is controlled only with main switch, device switch and a passed device test."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)

    await controller.tick(config, _plan(), NOW, SNAPSHOT, NOW)

    assert device.writes == [] and device.modes == []
    assert controller.eligible(config, config.devices[0]) == reason


async def test_pauses_while_evcc_controls_the_battery(tmp_path: Path) -> None:
    """Two controllers would fight; Sunflow waits."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)
    snapshot = SimpleNamespace(states={"sensor.evcc_battery_mode": {"state": "hold"}})

    await controller.tick(_config(), _plan(), NOW, snapshot, NOW)

    assert device.writes == []
    assert controller.paused is not None
    assert evcc_controls_battery(SimpleNamespace(states={"binary_sensor.evcc_battery_grid_charge_active": {"state": "on"}}))
    assert not evcc_controls_battery(SimpleNamespace(states={"sensor.evcc_battery_mode_value": {"state": "normal"}}))


async def test_failure_is_reported_and_retried(tmp_path: Path) -> None:
    """A failed write shows in the status; the next minute tries again."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)
    device.fail = True

    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW)
    assert controller.statuses["bat"].error == "Modbus timeout"

    device.fail = False
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=1))
    assert controller.statuses["bat"].error is None
    assert device.values == {"number.min": 3000}


async def test_shutdown_leaves_self_consumption(tmp_path: Path) -> None:
    """Without Sunflow running no forced charge or discharge may stay active."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW)

    await controller.release_all(_config())

    assert device.values == {"number.min": -10000}
    # The mode stays: after a restart Sunflow continues
    assert TakeoverMarker(tmp_path).get() == {"bat": "Self Use"}
