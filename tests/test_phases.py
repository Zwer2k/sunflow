"""Tests for switching a charger between 1 and 3 phases."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sunflow.control.controller import PhaseSettings, PhaseState, choose_phases

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
SETTINGS = PhaseSettings()


def _state(powers: list[float], last_switch: datetime | None = None, switches: int = 0) -> PhaseState:
    """Minute samples ending now, oldest first."""
    state = PhaseState(last_switch=last_switch, day=NOW.date() if switches else None, switches=switches)
    for i, power in enumerate(powers):
        state.add(NOW - timedelta(minutes=len(powers) - 1 - i), power)
    return state


@pytest.mark.parametrize(
    ("current", "powers", "ahead", "expected"),
    [
        pytest.param(1, [5000] * 6, 6000, 3, id="up-after-five-stable-minutes"),
        pytest.param(1, [5000] * 3, 6000, 1, id="up-not-before-the-delay"),
        pytest.param(1, [6000, 6000, 2000, 6000, 6000, 6000], None, 3, id="up-by-mean-despite-a-cloud"),
        pytest.param(1, [5000] * 6, 2000, 1, id="up-not-when-the-plan-expects-little-next"),
        pytest.param(1, [4200] * 6, 6000, 1, id="between-the-thresholds-stays"),
        pytest.param(3, [4200] * 6, None, 3, id="between-the-thresholds-stays-up"),
        pytest.param(3, [3000] * 3, None, 1, id="down-after-two-minutes"),
        pytest.param(3, [5000, 5000, 3000], None, 3, id="down-not-on-one-dip"),
    ],
)
def test_following_pv(current: int, powers: list[float], ahead: float | None, expected: int) -> None:
    """Following the surplus switches by the mean over the delay, with hysteresis and a look ahead."""
    assert choose_phases(current, NOW, powers[-1], _state(powers), SETTINGS, False, ahead) == expected


@pytest.mark.parametrize(
    ("current", "power", "expected"),
    [
        pytest.param(1, 7000, 3, id="planned-high-at-once"),
        pytest.param(3, 2500, 1, id="planned-low-at-once"),
        pytest.param(1, 4200, 1, id="planned-in-between-keeps"),
    ],
)
def test_steady_plan_switches_at_once(current: int, power: float, expected: int) -> None:
    """A planned fixed power (e.g. from the grid) picks the phases without waiting."""
    assert choose_phases(current, NOW, power, _state([power]), SETTINGS, True, None) == expected


@pytest.mark.parametrize(
    ("state", "settings", "expected"),
    [
        pytest.param(_state([3000] * 3, last_switch=NOW - timedelta(minutes=10)), SETTINGS, 3, id="minimum-interval"),
        pytest.param(_state([3000] * 3, switches=12), SETTINGS, 3, id="daily-limit"),
        pytest.param(_state([3000] * 3), PhaseSettings(mode=3), 3, id="fixed-three"),
        pytest.param(_state([9000] * 6), PhaseSettings(mode=1), 1, id="fixed-one"),
    ],
)
def test_limits_and_fixed_modes(state: PhaseState, settings: PhaseSettings, expected: int) -> None:
    """Interval, daily limit and a fixed mode keep the phases."""
    current = 1 if settings.mode == 1 else 3
    assert choose_phases(current, NOW, state.samples[-1][1], state, settings, False, None) == expected


def test_settings_from_parameters() -> None:
    """Down never lies above up, whatever was entered."""
    settings = PhaseSettings.of({"phase_mode": 0, "phase_up_w": 5000, "phase_down_w": 6000, "phase_up_delay_min": 3})
    assert (settings.up_w, settings.down_w, settings.up_delay) == (5000, 5000, timedelta(minutes=3))


GOE = {
    "capabilities": ["loadpoint"],
    "roles": {"phase_mode": {"key": "select-psm-psm"}},
    "charge_control": {"charge": [], "stop": [], "release": []},
    "phase_switch": {"remember": {"key": "select-psm-psm"}, "one": [], "three": []},
    "parameters": {
        "phases": {"default": 3}, "min_current_a": {"default": 6}, "max_current_a": {"default": 16},
        "max_power": {"default": 11000}, "phase_mode": {"default": 0},
    },
}


class PhaseCharger:
    """A charger with a phase mode select; records charge currents and phase switches."""

    def __init__(self) -> None:
        """Start on three phases."""
        self.option = "three_phases"
        self.actions: list[tuple[str, Any]] = []

    def snapshot(self) -> Any:
        """Its phase mode select as Home Assistant shows it."""
        from types import SimpleNamespace

        entity = {"entity_id": "select.psm", "unique_id": "216505-select-psm-psm", "device_id": "d"}
        return SimpleNamespace(devices=[], entities=[entity], states={"select.psm": {"state": self.option}})

    def access(self) -> Any:
        """Calls as the controller gets them."""
        from sunflow.control.controller import DeviceAccess
        from sunflow.devices.executor import PlannedCall

        options = {"phases_one": "one_phase", "phases_three": "three_phases"}

        async def plan(action: str, runtime: dict[str, Any]) -> tuple[list[PlannedCall], dict[str, Any]]:
            option = options.get(action) or runtime.get("phase_option")
            data = {"option": option} if option else dict(runtime)
            return [PlannedCall(domain="x", service=action, data=data, entity_id=f"x.{action}", storage="volatile", write="always")], {}

        async def run(calls: list[PlannedCall], states: dict[str, Any], force: bool = False) -> None:
            for call in calls:
                if call.service.startswith("phases_"):
                    self.option = call.data["option"]
                self.actions.append((call.service, call.data.get("option", call.data.get("current_a"))))
                call.status = "done"

        return DeviceAccess(plan=plan, run=run, release="release")


async def test_charger_drops_to_one_phase_and_gets_its_mode_back(tmp_path: Path) -> None:
    """Too little surplus for 3 x 6 A: after the delay it charges on one phase; handing back restores the mode."""
    from types import SimpleNamespace

    from sunflow.config.models import ControlSettings, DeviceConfig, SunflowConfig
    from sunflow.control.controller import PlanController, TakeoverMarker

    charger = PhaseCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE})
    device = DeviceConfig(id="wb", ha_device_id="d", profile="goe", profile_version=1, name="Wallbox")
    config = SunflowConfig(devices=[device], control=ControlSettings(enabled=True, devices={"wb": True}))
    slot = SimpleNamespace(
        start=NOW.replace(minute=0), end=NOW.replace(minute=15), action="auto", action_power_w=None,
        flex_w={"Wallbox": 2500}, pv_w=4000,
    )
    plan = SimpleNamespace(plan=SimpleNamespace(slots=[slot]))
    live = SimpleNamespace(pv_w=4000, grid_w=0)

    for minute in range(3):
        await controller.tick(config, plan, NOW, charger.snapshot(), NOW + timedelta(minutes=minute), live)

    assert ("phases_one", "one_phase") in charger.actions
    # 2.5 kW on one phase: 11 A
    assert charger.actions[-1] == ("charge", 11)
    assert controller.statuses["wb"].phases == 1

    off = config.model_copy(update={"control": ControlSettings(enabled=False)})
    await controller.tick(off, plan, NOW, charger.snapshot(), NOW + timedelta(minutes=4), live)
    assert charger.option == "three_phases"


def test_delays_and_interval_never_below_their_minimum() -> None:
    """Values stored before the lower bounds still never switch at once or more often than every 5 minutes."""
    settings = PhaseSettings.of(
        {"phase_up_delay_min": 0, "phase_down_delay_min": 0, "phase_min_interval_min": 0, "phase_max_switches_day": 0}
    )
    assert (settings.up_delay, settings.down_delay, settings.min_interval, settings.max_per_day) == (
        timedelta(minutes=1), timedelta(minutes=1), timedelta(minutes=5), 1,
    )
