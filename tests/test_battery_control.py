"""Tests for batteries controlled by actions (battery_control) instead of a power window."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dummy_inverter import DummyInverter
import pytest
from sunflow.config.models import (
    Connection,
    ControlSettings,
    DeviceConfig,
    SunflowConfig,
)
from sunflow.control.controller import PlanController, TakeoverMarker, device_access
from sunflow.devices.device_test import choose_test
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import EepromGuard, ExecutorError, plan_battery
from sunflow.devices.profiles import ProfileLibrary, battery_actions, supported_action
from sunflow.direct.hub import DirectHub, direct_device_id
from sunflow.planning.optimizer import PlanSlot, Settings
from sunflow.planning.plan import device_actions, restrict_to_device

NOW = datetime(2026, 10, 29, 10, 5, tzinfo=UTC)

# Mode register 200 (0 auto, 1 charge, 2 hold), power register 201; no limits, no forced discharge
PROFILE: dict[str, Any] = {
    "profile": "modbus/dummy_modes",
    "version": 1,
    "connection": {"type": "modbus_tcp", "points": {"battery_power": {"address": 100, "encoding": "int16", "unit": "W"}}},
    "capabilities": ["battery", "battery_control"],
    "roles": {"battery_power": {"key": "battery_power", "unit": "W", "sign": "charge_positive"}},
    "battery_control": {
        "auto": [{"modbus": {"address": 200, "value": 0, "write": "if_differs"}}],
        "hold": [{"modbus": {"address": 200, "value": 2, "write": "if_differs"}}],
        "force_charge": [
            {"modbus": {"address": 201, "value": "{{ P }}", "write": "if_differs", "tolerance": 50}},
            {"modbus": {"address": 200, "value": 1, "write": "if_differs"}},
        ],
        "release": [{"modbus": {"address": 200, "value": 0}}],
        "refresh": "5min",
    },
}
WINDOW = {"capabilities": ["battery", "battery_window"]}


@pytest.mark.parametrize(
    ("profile", "action", "power", "expected"),
    [
        pytest.param(PROFILE, "force_charge", 3000, ("force_charge", 3000), id="supported"),
        pytest.param(PROFILE, "limit_discharge", 800, ("hold", None), id="limit-to-hold"),
        pytest.param(PROFILE, "limit_charge", 500, ("auto", None), id="limit-charge-to-auto"),
        pytest.param(PROFILE, "force_discharge", 2000, ("auto", None), id="no-discharge"),
        pytest.param(
            {**PROFILE, "battery_control": {"auto": [], "limit_discharge": []}}, "hold", None, ("limit_discharge", 0.0),
            id="hold-as-zero-limit",
        ),
        pytest.param(WINDOW, "limit_charge", 500, ("limit_charge", 500), id="window-does-all"),
    ],
)
def test_supported_action(profile: dict[str, Any], action: str, power: float | None, expected: tuple) -> None:
    """An action the device lacks becomes the closest it has."""
    assert supported_action(profile, action, power) == expected


def test_battery_actions() -> None:
    """Actions are the sections of battery_control; a window can do everything."""
    assert battery_actions(PROFILE) == {"auto", "hold", "force_charge"}
    assert "force_discharge" in battery_actions(WINDOW)
    assert battery_actions({"capabilities": ["pv"]}) == {"auto"}


def test_plan_battery_uses_action_steps() -> None:
    """Steps of the action with the power; release has its own steps."""
    calls = plan_battery(PROFILE, "force_charge", {}, {"P": 2500}, [])
    assert [(c.entity_id, c.data["value"]) for c in calls] == [("direct:modbus:201", 2500), ("direct:modbus:200", 1)]
    assert [c.data["value"] for c in plan_battery(PROFILE, "release", {}, {}, [])] == [0]
    with pytest.raises(ExecutorError, match="Unsupported"):
        plan_battery(PROFILE, "force_discharge", {}, {}, [])


def test_planner_only_plans_what_the_device_can() -> None:
    """Without forced charging no grid charging is planned; slot actions are the device's."""
    warnings: list[str] = []
    settings = restrict_to_device(Settings(allow_grid_charge=True), battery_actions(PROFILE), warnings)
    assert settings.allow_grid_charge
    assert warnings == []
    no_force = restrict_to_device(Settings(allow_grid_charge=True), {"auto", "hold"}, warnings)
    assert not no_force.allow_grid_charge
    assert len(warnings) == 1

    slot = PlanSlot.__new__(PlanSlot)
    slot.action, slot.action_power_w = "limit_discharge", 700
    device_actions(SimpleNamespace(slots=[slot]), PROFILE)
    assert (slot.action, slot.action_power_w) == ("hold", None)


@pytest.mark.parametrize(
    ("baseline", "supported", "action"),
    [
        pytest.param(-1500, {"hold", "auto"}, "hold", id="discharging-hold"),
        pytest.param(-1500, {"limit_discharge", "force_charge"}, "limit_discharge", id="discharging-limit"),
        pytest.param(1500, {"force_charge", "force_discharge"}, "force_discharge", id="charging-discharge"),
        pytest.param(0, {"force_charge"}, "force_charge", id="idle-charge"),
    ],
)
def test_device_test_picks_a_supported_action(baseline: float, supported: set[str], action: str) -> None:
    """The device test only commands what the device supports."""
    assert choose_test(baseline, supported)[0] == action


def test_device_test_needs_a_checkable_action() -> None:
    """A device that can only run on its own cannot be tested."""
    with pytest.raises(ExecutorError):
        choose_test(0, {"auto"})


class FakeHA:
    """Home Assistant without any devices."""

    generation = 0

    async def get_config_entries(self) -> list[Any]:
        """No entries."""
        return []

    async def get_device_registry(self) -> list[Any]:
        """No devices."""
        return []

    async def get_entity_registry(self) -> list[Any]:
        """No entities."""
        return []

    async def get_energy_prefs(self) -> None:
        """No energy dashboard."""

    async def get_states(self) -> list[Any]:
        """No states."""
        return []

    async def command(self, *_: Any, **__: Any) -> list[Any]:
        """No manifests."""
        return []


def _plan(action: str, power: float | None) -> SimpleNamespace:
    slot = SimpleNamespace(
        start=NOW.replace(minute=0), end=NOW.replace(minute=15), action=action, action_power_w=power, export_w=0.0
    )
    return SimpleNamespace(plan=SimpleNamespace(slots=[slot]))


async def test_controller_drives_a_direct_battery(tmp_path: Path) -> None:
    """Plan actions reach the registers: fallback, unchanged values not again, refresh, release."""
    async with DummyInverter({100: 0, 200: 0, 201: 0}) as dummy:
        device = DeviceConfig(
            id="modes1", ha_device_id=direct_device_id("modes1"), profile=PROFILE["profile"], profile_version=1,
            name="Dummy", test_passed_at=NOW, connection=Connection(host="127.0.0.1", port=dummy.port),
        )
        config = SunflowConfig(devices=[device], control=ControlSettings(enabled=True, devices={"modes1": True}))
        hub = DirectHub()
        await hub.sync(config, ProfileLibrary(profiles={PROFILE["profile"]: PROFILE}))
        controller = PlanController(
            TakeoverMarker(tmp_path),
            lambda d: device_access(FakeHA(), PROFILE, d, EepromGuard(tmp_path, 10), hub),
            lambda: {PROFILE["profile"]: PROFILE},
        )
        snapshot = SimpleNamespace(states={})

        await controller.tick(config, _plan("force_charge", 3000), NOW, snapshot, NOW)
        await controller.tick(config, _plan("force_charge", 3020), NOW, snapshot, NOW + timedelta(minutes=1))
        assert dummy.writes == [(201, [3000]), (200, [1])]

        # A device without a limit holds instead
        await controller.tick(config, _plan("limit_discharge", 500), NOW, snapshot, NOW + timedelta(minutes=2))
        assert dummy.writes[-1] == (200, [2])
        status = controller.statuses["modes1"]
        assert status.action == "hold"
        assert "kann das Gerät nicht" in status.reason

        # Unchanged for the refresh time: sent again so the device does not fall back
        count = len(dummy.writes)
        await controller.tick(config, _plan("limit_discharge", 500), NOW, snapshot, NOW + timedelta(minutes=8))
        assert dummy.writes[count:] == [(200, [2])]

        await controller.tick(
            config.model_copy(update={"control": ControlSettings(enabled=False)}), None, None, snapshot, NOW + timedelta(minutes=9)
        )
        assert dummy.writes[-1] == (200, [0])
        await hub.stop()


PV_PROFILE: dict[str, Any] = {
    "profile": "modbus/dummy_pv",
    "version": 1,
    "connection": {"type": "modbus_tcp", "points": {"ac_power": {"address": 100, "encoding": "uint16", "unit": "W"}}},
    "capabilities": ["pv", "curtail"],
    "roles": {"ac_power": {"key": "ac_power", "unit": "W"}},
    "pv_limit": {
        "set": [{"modbus": {"address": 300, "value": "{{ limit_w }}", "write": "if_differs"}}],
        "release": [{"modbus": {"address": 300, "value": 10000, "write": "if_differs"}}],
    },
}


def _curtail_plan(curtail: float) -> SimpleNamespace:
    slot = SimpleNamespace(
        start=NOW.replace(minute=0), end=NOW.replace(minute=15), action="auto", action_power_w=None,
        export_w=1000.0, pv_w=5000.0, curtail_w=curtail,
    )
    return SimpleNamespace(plan=SimpleNamespace(slots=[slot]))


async def test_controller_limits_pv_by_the_measured_export(tmp_path: Path) -> None:
    """The limit takes the export above the plan off the output; no curtailment or off lifts it."""
    async with DummyInverter({100: 5000, 300: 10000}) as dummy:
        device = DeviceConfig(
            id="pv1", ha_device_id=direct_device_id("pv1"), profile=PV_PROFILE["profile"], profile_version=1,
            name="PV", connection=Connection(host="127.0.0.1", port=dummy.port),
        )
        config = SunflowConfig(devices=[device], control=ControlSettings(enabled=True, devices={"pv1": True}))
        hub = DirectHub()
        await hub.sync(config, ProfileLibrary(profiles={PV_PROFILE["profile"]: PV_PROFILE}))
        controller = PlanController(
            TakeoverMarker(tmp_path),
            lambda d: device_access(FakeHA(), PV_PROFILE, d, EepromGuard(tmp_path, 10), hub),
            lambda: {PV_PROFILE["profile"]: PV_PROFILE},
        )

        async def tick(plan: Any, export: float, minute: int, cfg: SunflowConfig = config) -> None:
            await hub.poll()
            snapshot = hub.contribute(Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None))
            live = SimpleNamespace(pv_w=None, grid_w=-export)
            await controller.tick(cfg, plan, NOW, snapshot, NOW + timedelta(minutes=minute), live)

        await tick(_curtail_plan(2000), export=3000, minute=0)
        assert dummy.writes == [(300, [3000])]
        assert controller.statuses["pv1"].limit_w == 3000

        # Limited, the export matches the plan: the limit stays
        dummy.holding[100] = 3000
        await tick(_curtail_plan(2000), export=1000, minute=1)
        assert dummy.writes == [(300, [3000])]

        await tick(_curtail_plan(0), export=1000, minute=2)
        assert dummy.writes[-1] == (300, [10000])
        assert controller.statuses["pv1"].limit_w is None

        await tick(_curtail_plan(2000), export=3000, minute=3)
        off = config.model_copy(update={"control": ControlSettings(enabled=False)})
        await tick(_curtail_plan(2000), export=3000, minute=4, cfg=off)
        assert dummy.writes[-1] == (300, [10000])
        assert TakeoverMarker(tmp_path).get() == {}
        await hub.stop()
