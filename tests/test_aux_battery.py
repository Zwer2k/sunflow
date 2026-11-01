"""Tests for topping up the 12 V battery of a plugged-in car."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sunflow.config.models import (
    AuxBatteryGuard,
    ChargingProfile,
    SunflowConfig,
    Vehicle,
)
from sunflow.control import controller as module
from sunflow.control.aux_battery import AuxCharge, aux_charge_due, aux_voltage
from sunflow.control.controller import DeviceAccess, PlanController, TakeoverMarker
from sunflow.devices.executor import PlannedCall
from test_control import EVCC, GOE, NOW, FakeCharger, _charge_plan, _charger_config

GUARD = AuxBatteryGuard(voltage_entity="sensor.fiat_12v", min_voltage_v=12.2, charge_min=5, pause_min=60)
VOLTAGE = "sensor.fiat_12v"


def test_tops_up_for_the_set_time_then_pauses() -> None:
    """Below the voltage it charges 5 min; a sensor still showing the low value starts it again only after the pause."""
    state = AuxCharge()

    assert aux_charge_due(GUARD, "fiat", 11.9, True, state, NOW)
    assert aux_charge_due(GUARD, "fiat", 11.9, True, state, NOW + timedelta(minutes=4))
    assert not aux_charge_due(GUARD, "fiat", 11.9, True, state, NOW + timedelta(minutes=5))
    assert not aux_charge_due(GUARD, "fiat", 11.9, True, state, NOW + timedelta(minutes=64))
    assert aux_charge_due(GUARD, "fiat", 11.9, True, state, NOW + timedelta(minutes=65))


@pytest.mark.parametrize(
    ("guard", "voltage", "connected"),
    [
        pytest.param(GUARD, 12.6, True, id="voltage-fine"),
        pytest.param(GUARD, 11.9, False, id="not-plugged-in"),
        pytest.param(GUARD, None, True, id="sensor-unreadable"),
        pytest.param(AuxBatteryGuard(), 11.9, True, id="not-watched"),
    ],
)
def test_no_top_up(guard: AuxBatteryGuard, voltage: float | None, connected: bool) -> None:
    """Only a plugged-in car with a watched voltage below the limit."""
    assert not aux_charge_due(guard, "fiat", voltage, connected, AuxCharge(), NOW)


def test_unplugging_ends_the_top_up() -> None:
    """The car leaves: the top-up ends at once, the pause counts from then."""
    state = AuxCharge()
    aux_charge_due(GUARD, "fiat", 11.9, True, state, NOW)

    assert not aux_charge_due(GUARD, "fiat", 11.9, False, state, NOW + timedelta(minutes=2))
    assert state.last_end == NOW + timedelta(minutes=2)


def test_voltage_from_the_cars_sensor() -> None:
    """Unreadable states give no voltage."""
    vehicle = Vehicle(name="500e", capacity_wh=37000, aux_battery=GUARD)

    assert aux_voltage(vehicle, {VOLTAGE: {"state": "12.1"}}) == pytest.approx(12.1)
    assert aux_voltage(vehicle, {VOLTAGE: {"state": "unavailable"}}) is None


class PhaseCharger(FakeCharger):
    """go-e that switches phases; records them."""

    def __init__(self, remember: bool = False) -> None:
        """Starts on three phases."""
        super().__init__(remember)
        self.phases: list[str] = []

    def access(self) -> DeviceAccess:
        """Phase and minimum actions besides the charge actions."""
        base = super().access()

        async def plan(action: str, runtime: dict[str, Any]) -> tuple[list[PlannedCall], dict[str, Any]]:
            if action.startswith(module.PHASES_PREFIX):
                return [PlannedCall(domain="select", service="select_option", data={"option": action}, entity_id="select.psm", storage="eeprom", write="always")], {}
            if action == "minimum":
                return [PlannedCall(domain="select", service="select_option", data={"option": "minpv"}, entity_id="select.frc", storage="volatile", write="if_differs")], {"select.frc": {"state": self.option}}
            return await base.plan(action, runtime)

        async def run(calls: list[PlannedCall], states: dict[str, Any], force: bool = False) -> None:
            if calls[0].entity_id == "select.psm":
                self.phases.append(calls[0].data["option"])
                calls[0].status = "done"
                return
            await base.run(calls, states)

        return DeviceAccess(plan=plan, run=run, ensure_mode=base.ensure_mode, set_mode=base.set_mode, release=base.release)


def _config(profile: str) -> SunflowConfig:
    config = _charger_config(profile)
    config.vehicles = [Vehicle(id="fiat", name="500e", capacity_wh=37000, aux_battery=GUARD)]
    config.charging = [ChargingProfile(vehicle_id="fiat", wallbox_id="wb")]
    return config


@pytest.fixture(autouse=True)
def _plugged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "preview", lambda *_: [SimpleNamespace(role="connected", raw_state="on", value=None, entity_id=None)])


async def test_wallbox_charges_minimum_on_one_phase(tmp_path: Path) -> None:
    """The plan says stop; the low 12 V battery gets 6 A on one phase for 5 min, then the plan applies again."""
    charger = PhaseCharger()
    profile = GOE | {"phase_switch": {"one": [], "three": []}}
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": profile})
    snapshot = SimpleNamespace(states={VOLTAGE: {"state": "11.8"}}, devices=[], entities=[])

    await controller.tick(_config("goe"), _charge_plan(0), NOW, snapshot, NOW)

    assert (charger.option, charger.current, charger.phases) == ("charge", 6, ["phases_one"])
    assert controller.statuses["wb"].reason.startswith("12-V-Batterie von 500e bei 11,8 V (unter 12,2 V)")

    await controller.tick(_config("goe"), _charge_plan(0), NOW, snapshot, NOW + timedelta(minutes=5))
    assert charger.option == "dont_charge"


async def test_planned_charging_is_not_reduced(tmp_path: Path) -> None:
    """Charging after the plan anyway charges the 12 V battery too."""
    charger = PhaseCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE})
    snapshot = SimpleNamespace(states={VOLTAGE: {"state": "11.8"}}, devices=[], entities=[])

    await controller.tick(_config("goe"), _charge_plan(11000), NOW, snapshot, NOW)

    assert (charger.option, charger.current) == ("charge", 16)


async def test_evcc_uses_its_minimum_mode(tmp_path: Path) -> None:
    """An evcc loadpoint charges with its minimum current itself (Min+PV)."""
    charger = PhaseCharger(remember=True)
    profile = EVCC | {"charge_control": EVCC["charge_control"] | {"minimum": []}}
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"evcc": profile})
    snapshot = SimpleNamespace(states={VOLTAGE: {"state": "11.8"}}, devices=[], entities=[])

    await controller.tick(_config("evcc"), _charge_plan(0), NOW, snapshot, NOW)

    assert charger.option == "minpv"


def test_control_shows_the_12v_battery() -> None:
    """The control tab gets voltage, threshold and the end of a running top-up; nothing for unwatched cars."""
    from sunflow.api.control import _aux_battery

    vehicle = Vehicle(id="fiat", name="500e", capacity_wh=37000, aux_battery=GUARD)
    controller = PlanController.__new__(PlanController)
    controller._aux = {"wb": AuxCharge(vehicle_id="fiat", until=NOW + timedelta(minutes=3))}
    states = {VOLTAGE: {"state": "11.9"}}

    assert _aux_battery(vehicle, states, controller, "wb", NOW) == {
        "voltage": 11.9,
        "min_voltage": 12.2,
        "charging_until": (NOW + timedelta(minutes=3)).isoformat(),
    }
    assert _aux_battery(vehicle, states, controller, "wb", NOW + timedelta(minutes=3))["charging_until"] is None
    assert _aux_battery(Vehicle(id="x", name="X", capacity_wh=1), states, controller, "wb", NOW) is None
    assert _aux_battery(None, states, controller, "wb", NOW) is None
