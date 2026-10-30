"""Tests for carrying out the plan on the battery."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sunflow.config_store import ControlSettings, DeviceConfig, SunflowConfig
from sunflow.control import (
    PlanController,
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


def _controller(tmp_path: Path, device: FakeDevice) -> PlanController:
    return PlanController(TakeoverMarker(tmp_path), lambda d: device.access(), lambda: {"sofar": SOFAR})


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


class FakeCharger:
    """A loadpoint: force state select (go-e) or evcc mode, plus the current."""

    def __init__(self, remember: bool = False) -> None:
        """go-e like without remember, evcc like with."""
        self.option = "pv" if remember else "neutral"
        self.current = 16.0
        self.remember = remember
        self.writes: list[str] = []

    def access(self) -> DeviceAccess:
        """Calls as built by device_access for a charge_control profile."""
        options = {"charge": "now" if self.remember else "charge", "follow": "pv", "stop": "off" if self.remember else "dont_charge", "release": "neutral"}

        async def plan(action: str, runtime: dict[str, float]) -> tuple[list[PlannedCall], dict[str, Any]]:
            calls = [PlannedCall(domain="select", service="select_option", data={"option": options[action]}, entity_id="select.frc", storage="volatile", write="if_differs")]
            if action == "charge" and not self.remember:
                calls.insert(0, PlannedCall(domain="number", service="set_value", data={"value": runtime["current_a"]}, entity_id="number.amp", storage="eeprom", write="if_differs", tolerance=2))
            return calls, {"select.frc": {"state": self.option}, "number.amp": {"state": str(self.current)}}

        async def run(calls: list[PlannedCall], states: dict[str, Any]) -> None:
            for call in calls:
                from sunflow.executor import current_matches

                if call.write == "if_differs" and current_matches(call, states):
                    call.status = "skipped"
                    continue
                if call.entity_id == "number.amp":
                    self.current = call.data["value"]
                else:
                    self.option = call.data["option"]
                self.writes.append(call.entity_id)
                call.status = "done"

        async def current_option() -> str | None:
            return self.option

        async def restore(option: str) -> None:
            self.option = option

        if self.remember:
            return DeviceAccess(plan=plan, run=run, ensure_mode=current_option, set_mode=restore, release=None)
        return DeviceAccess(plan=plan, run=run, release="release")


GOE = {"capabilities": ["loadpoint"], "charge_control": {"charge": [], "stop": [], "release": []}, "parameters": {"phases": {"default": 3}, "min_current_a": {"default": 6}, "max_current_a": {"default": 16}, "max_power": {"default": 11000}}}
EVCC = {"capabilities": ["loadpoint"], "charge_control": {"charge": [], "stop": [], "follow": []}, "parameters": {"max_power": {"default": 11000}}}


def _charge_plan(power: float) -> SimpleNamespace:
    slot = SimpleNamespace(start=NOW.replace(minute=0), end=NOW.replace(minute=15), action="auto", action_power_w=None, flex_w={"Wallbox": power})
    return SimpleNamespace(plan=SimpleNamespace(slots=[slot]))


def _charger_config(profile: str, enabled: bool = True) -> SunflowConfig:
    wallbox = DeviceConfig(id="wb", ha_device_id="w", profile=profile, profile_version=1, name="Wallbox")
    return SunflowConfig(devices=[wallbox], control=ControlSettings(enabled=enabled, devices={"wb": True}))


async def test_goe_charges_with_planned_current_and_stops(tmp_path: Path) -> None:
    """Planned 4.1 kW on 3 phases are 6 A; small current changes are not written to flash."""
    charger = FakeCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE})

    await controller.tick(_charger_config("goe"), _charge_plan(4100), NOW, SNAPSHOT, NOW)
    assert (charger.option, charger.current) == ("charge", 6.0)

    # 7 A would be 1 A more: within the tolerance, the flash value stays
    await controller.tick(_charger_config("goe"), _charge_plan(4700), NOW, SNAPSHOT, NOW + timedelta(minutes=1))
    assert charger.current == 6.0

    await controller.tick(_charger_config("goe"), _charge_plan(0), NOW, SNAPSHOT, NOW + timedelta(minutes=2))
    assert charger.option == "dont_charge"

    # Without a current plan the charger's own logic takes over again
    await controller.tick(_charger_config("goe"), _charge_plan(0), NOW - timedelta(hours=1), SNAPSHOT, NOW + timedelta(minutes=3))
    assert charger.option == "neutral"


async def test_evcc_follows_surplus_and_gets_its_mode_back(tmp_path: Path) -> None:
    """Part power means evcc's PV mode; switching off restores the mode evcc had before."""
    charger = FakeCharger(remember=True)
    charger.option = "minpv"
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"evcc": EVCC})

    await controller.tick(_charger_config("evcc"), _charge_plan(11000), NOW, SNAPSHOT, NOW)
    assert charger.option == "now"
    await controller.tick(_charger_config("evcc"), _charge_plan(3000), NOW, SNAPSHOT, NOW + timedelta(minutes=1))
    assert charger.option == "pv"

    await controller.tick(_charger_config("evcc", enabled=False), _charge_plan(3000), NOW, SNAPSHOT, NOW + timedelta(minutes=2))
    assert charger.option == "minpv"
    assert TakeoverMarker(tmp_path).get() == {}


async def test_shutdown_releases_loadpoints(tmp_path: Path) -> None:
    """Without Sunflow nobody would start or stop charging: the charger regulates itself again."""
    charger = FakeCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE})
    await controller.tick(_charger_config("goe"), _charge_plan(0), NOW, SNAPSHOT, NOW)

    await controller.release_all(_charger_config("goe"))

    assert charger.option == "neutral"
    assert TakeoverMarker(tmp_path).get() == {}


class AuthorizingCharger(FakeCharger):
    """go-e with card authorization: records the transaction Sunflow starts."""

    def __init__(self) -> None:
        """No transaction yet."""
        super().__init__()
        self.transaction = "none"

    def access(self) -> DeviceAccess:
        """Like the go-e profile, with the authorize action."""
        base = super().access()

        async def plan(action: str, runtime: dict[str, float]) -> tuple[list[PlannedCall], dict[str, Any]]:
            if action != "authorize":
                return await base.plan(action, runtime)
            call = PlannedCall(domain="select", service="select_option", data={"option": runtime["transaction"]}, entity_id="select.trx", storage="volatile", write="always")
            return [call], {"select.trx": {"state": self.transaction}}

        async def run(calls: list[PlannedCall], states: dict[str, Any]) -> None:
            if calls[0].entity_id == "select.trx":
                self.transaction = calls[0].data["option"]
                calls[0].status = "done"
                return
            await base.run(calls, states)

        return DeviceAccess(plan=plan, run=run, release="release")


GOE_CARDS = GOE | {"charge_control": {"charge": [], "stop": [], "release": [], "authorize": []}}


@pytest.mark.parametrize(
    ("method", "transaction"),
    [
        pytest.param("sensor", "card_1", id="sensor-authorizes"),
        pytest.param("manual", "card_1", id="choice-authorizes"),
        # "The only vehicle" is no proof: a foreign car without card must not get charging
        pytest.param("single", "none", id="single-does-not"),
        pytest.param("card", "none", id="scanned-needs-nothing"),
    ],
)
async def test_authorizes_only_surely_recognized_vehicles(tmp_path: Path, method: str, transaction: str) -> None:
    """With a card required, Sunflow starts the transaction for a car recognized by sensor or choice."""
    from sunflow.config_store import Vehicle

    charger = AuthorizingCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE_CARDS})
    config = _charger_config("goe")
    config.vehicles = [Vehicle(id="tesla", name="Model 3", capacity_wh=60000, card=2, wallboxes=["wb"])]
    plan = _charge_plan(11000)
    plan.flex = [SimpleNamespace(device_id="wb", unknown_vehicle=False, vehicle_id="tesla", vehicle_method=method, reason=None)]

    await controller.tick(config, plan, NOW, SNAPSHOT, NOW)

    assert charger.transaction == transaction
    assert charger.option == "charge"


async def test_unknown_vehicle_leaves_the_wallbox_to_itself(tmp_path: Path) -> None:
    """A foreign car: Sunflow does not start or stop it."""
    charger = FakeCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE})
    plan = _charge_plan(0)
    plan.flex = [SimpleNamespace(device_id="wb", unknown_vehicle=True, vehicle_id=None, vehicle_method=None, reason="Fahrzeug nicht erkannt – Wallbox regelt selbst")]
    charger.option = "dont_charge"

    await controller.tick(_charger_config("goe"), plan, NOW, SNAPSHOT, NOW)

    assert charger.option == "neutral"
    assert controller.statuses["wb"].reason.startswith("Fahrzeug nicht erkannt")


PARAMS = {"phases": 3, "min_current_a": 6, "max_current_a": 16}


@pytest.mark.parametrize(
    ("planned", "pv", "grid", "charging", "expected"),
    [
        pytest.param(5000, 7000, 0, True, 7000, id="more-pv-than-forecast"),
        pytest.param(5000, 5500, 900, True, 4900, id="grid-import-taken-from-car"),
        pytest.param(5000, 3000, 0, False, None, id="too-little-stops"),
        # Already charging: keeps the minimum current down to half of it
        pytest.param(5000, 3000, 0, True, 6 * 230 * 3, id="hysteresis"),
        pytest.param(5000, 500, 0, True, None, id="far-too-little"),
    ],
)
def test_follow_surplus(planned: float, pv: float, grid: float, charging: bool, expected: float | None) -> None:
    """The planned power corrected by the PV actually there (planned PV 5 kW)."""
    from sunflow.control import follow_surplus

    live = SimpleNamespace(pv_w=pv, grid_w=grid)

    assert follow_surplus(planned, 6000 - 1000, live, charging, PARAMS) == expected


def test_amx_topic_from_device_serial() -> None:
    """The go-e topic uses the serial from the device registry; role values are available too."""
    from pathlib import Path

    from sunflow.control import device_variables
    from sunflow.executor import plan_charge
    from sunflow.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    profile = load_profiles(repo / "profiles", load_schema(repo / "schema")).profiles["goecharger_mqtt/charger"]
    entities = [
        {"entity_id": "select.goe_frc", "unique_id": "216505-select-frc-frc", "device_id": "d"},
        {"entity_id": "number.goe_amp", "unique_id": "216505-number-amp-0", "device_id": "d"},
    ]
    snapshot = SimpleNamespace(
        devices=[{"id": "d", "identifiers": [["goecharger_mqtt", "216505"]]}],
        entities=entities,
        states={"number.goe_amp": {"state": "16", "attributes": {"unit_of_measurement": "A"}}, "select.goe_frc": {"state": "neutral"}},
    )
    device = DeviceConfig(id="wb", ha_device_id="d", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")

    variables = device_variables(profile, device, snapshot)
    charge = plan_charge(profile, "charge", {}, variables | {"current_a": 7}, entities)
    release = plan_charge(profile, "release", {}, variables, entities)

    assert (charge[0].domain, charge[0].service) == ("mqtt", "publish")
    assert charge[0].data == {"topic": "go-eCharger/216505/amx/set", "payload": 7}
    # Handing back sets the temporary current to the stored one again
    assert release[0].data["payload"] == 16
