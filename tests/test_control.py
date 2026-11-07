"""Tests for carrying out the plan on the battery."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sunflow.config.models import ControlSettings, DeviceConfig, PriorityCondition, SunflowConfig
from sunflow.control.controller import (
    DeviceAccess,
    PlanController,
    TakeoverMarker,
    desired_action,
    device_access,
    evcc_controls_battery,
)
from sunflow.devices.executor import EepromGuard, ExecutorError, PlannedCall
from sunflow.devices.profiles import load_profiles, load_schema, volatile_action

REPO = Path(__file__).resolve().parents[1]
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
        self.runtimes: list[dict[str, float]] = []

    def access(self) -> DeviceAccess:
        """Calls as the controller gets them from the API layer."""
        async def plan(action: str, runtime: dict[str, float]) -> tuple[list[PlannedCall], dict[str, Any]]:
            self.runtimes.append(runtime)
            target = {"auto": -10000, "hold": 0, "force_charge": runtime.get("P", 0)}[action]
            call = PlannedCall(domain="number", service="set_value", data={"value": target}, entity_id="number.min", storage="volatile", write="always")
            states = {"number.min": {"state": str(self.values["number.min"])}} if "number.min" in self.values else {}
            return [call], states

        async def run(calls: list[PlannedCall], states: dict[str, Any], force: bool = False) -> None:
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


@pytest.mark.parametrize(
    ("battery", "expected"),
    [
        pytest.param("house", ("hold", None, "nach Plan"), id="first"),
        pytest.param("plug", ("limit_discharge", 400, "nach Plan"), id="second"),
        pytest.param("gone", ("auto", None, "Speicher nicht im Plan – Eigenverbrauch"), id="not-planned"),
    ],
)
def test_desired_action_per_battery(battery: str, expected: tuple) -> None:
    """With several batteries each device carries out its own share of the slot."""
    plan = _plan("hold", None)
    plan.plan.slots[0].batteries = {
        "house": SimpleNamespace(action="hold", action_power_w=None),
        "plug": SimpleNamespace(action="limit_discharge", action_power_w=400),
    }

    assert desired_action(plan, NOW, NOW, battery=battery) == expected


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


async def test_hands_back_while_another_app_runs(tmp_path: Path) -> None:
    """With the other Sunflow App running, a taken-over device is handed back and nothing is written."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW)

    controller.blocked = "Sunflow Edge"
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=1))

    assert device.mode == "Self Use"
    assert TakeoverMarker(tmp_path).get() == {}
    assert controller.paused == "Sunflow Edge"
    writes = len(device.writes)
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=2))
    assert len(device.writes) == writes


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
    # No grid buffer: the inverter stays in passive mode and would feed the battery into the grid
    assert device.runtimes[-1] == {"grid_offset": 0.0}
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

        async def run(calls: list[PlannedCall], states: dict[str, Any], force: bool = False) -> None:
            for call in calls:
                from sunflow.devices.executor import current_matches

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


def _charge_plan(power: float, pv: float = 6000, grid: float = 0) -> SimpleNamespace:
    slot = SimpleNamespace(
        start=NOW.replace(minute=0), end=NOW.replace(minute=15), action="auto", action_power_w=None, flex_w={"Wallbox": power},
        pv_w=pv, import_w=grid,
    )
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

        async def run(calls: list[PlannedCall], states: dict[str, Any], force: bool = False) -> None:
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
    from sunflow.config.models import ChargingProfile, Vehicle

    charger = AuthorizingCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE_CARDS})
    config = _charger_config("goe")
    config.vehicles = [Vehicle(id="tesla", name="Model 3", capacity_wh=60000)]
    config.charging = [ChargingProfile(vehicle_id="tesla", wallbox_id="wb", card=2)]
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
    from sunflow.control.controller import follow_surplus

    live = SimpleNamespace(pv_w=pv, grid_w=grid)

    assert follow_surplus(planned, 6000 - 1000, live, charging, PARAMS) == expected


def test_goe_current_is_the_requested_current() -> None:
    """Firmware 56.x ignores the temporary amx: the current is the requested current (amp), like evcc does for API v2."""
    from pathlib import Path

    from sunflow.control.controller import device_variables
    from sunflow.devices.executor import plan_charge
    from sunflow.devices.profiles import load_profiles, load_schema

    repo = Path(__file__).resolve().parents[1]
    profile = load_profiles(repo / "profiles", load_schema(repo / "schema")).profiles["goecharger_mqtt/charger"]
    entities = [
        {"entity_id": "select.goe_frc", "unique_id": "123456-select-frc-frc", "device_id": "d"},
        {"entity_id": "number.goe_amp", "unique_id": "123456-number-amp-0", "device_id": "d"},
    ]
    snapshot = SimpleNamespace(
        devices=[{"id": "d", "identifiers": [["goecharger_mqtt", "123456"]]}],
        entities=entities,
        states={"number.goe_amp": {"state": "16", "attributes": {"unit_of_measurement": "A"}}, "select.goe_frc": {"state": "neutral"}},
    )
    device = DeviceConfig(id="wb", ha_device_id="d", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")

    variables = device_variables(profile, device, snapshot)
    charge = plan_charge(profile, "charge", {}, variables | {"current_a": 7}, entities)
    release = plan_charge(profile, "release", {}, variables, entities)

    assert (charge[0].domain, charge[0].service, charge[0].entity_id) == ("number", "set_value", "number.goe_amp")
    assert charge[0].data == {"value": 7}
    # Handing back sets the temporary current to the stored one again
    # The user's current comes back through the remembered value, not a release step
    assert [c.entity_id for c in release] == ["select.goe_frc"]


async def test_disabled_device_is_handed_back(tmp_path: Path) -> None:
    """Switching a device off in the setup ends its control like the device switch does."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)
    config = _config()
    await controller.tick(config, _plan(), NOW, SNAPSHOT, NOW)

    config.disabled_devices, config.devices = config.devices, []
    await controller.tick(config, _plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=1))

    assert device.mode == "Self Use"
    assert TakeoverMarker(tmp_path).get() == {}


@pytest.mark.parametrize(
    ("buffer", "export", "planned", "expected"),
    [
        pytest.param(-200, 1500, True, -200, id="surplus-exported-anyway"),
        pytest.param(-200, 0, True, 0, id="little-pv-regulates-to-zero"),
        pytest.param(-200, 150, True, 0, id="export-smaller-than-buffer"),
        pytest.param(-200, 1500, False, 0, id="no-current-plan"),
        pytest.param(100, 0, True, 100, id="import-on-purpose-always"),
    ],
)
def test_grid_buffer(buffer: float, export: float, planned: bool, expected: float) -> None:
    """The feed-in buffer only costs nothing while the plan exports PV surplus anyway."""
    from sunflow.control.controller import grid_buffer

    assert grid_buffer(buffer, SimpleNamespace(export_w=export), planned) == expected


async def test_battery_window_gets_the_smart_grid_target(tmp_path: Path) -> None:
    """The runtime grid target overrides the stored buffer in the written window."""
    seen: list[dict] = []
    device = FakeDevice()
    base = device.access()

    async def plan(action: str, runtime: dict) -> tuple:
        seen.append(runtime)
        return await base.plan(action, runtime)

    access = DeviceAccess(plan=plan, run=base.run, ensure_mode=base.ensure_mode, set_mode=base.set_mode)
    profile = SOFAR | {"parameters": {"grid_offset": {"unit": "W", "default": -200}}}
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: access, lambda: {"sofar": profile})
    slot_plan = _plan("auto", None)
    slot_plan.plan.slots[0].export_w = 0.0

    await controller.tick(_config(), slot_plan, NOW, SNAPSHOT, NOW)

    # The first plan is the control's; the watchdog's hand-back calls follow
    assert seen[0]["grid_offset"] == 0
    assert controller.statuses["bat"].grid_target_w == 0


@pytest.mark.parametrize(
    ("power", "expected"),
    [
        pytest.param(2500, "2,1kW", id="largest-fitting"),
        pytest.param(2830, "2,8kW", id="within-tolerance"),
        pytest.param(700, "0,7kW", id="smallest"),
        pytest.param(500, None, id="below-smallest"),
    ],
)
def test_choose_power_step(power: float, expected: str | None) -> None:
    """The heating rod gets the largest step that fits the planned power."""
    from sunflow.control.controller import choose_step, off_step

    steps = {"off": 0, "0,7kW": 700, "1,4kW": 1400, "2,1kW": 2100, "2,8kW": 2800}

    assert choose_step(steps, power) == expected
    assert off_step(steps) == "off"


async def test_fallback_calls_hand_the_battery_back(tmp_path: Path) -> None:
    """While controlling, the watchdog gets the calls that put the battery back to self-consumption."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)
    await controller.tick(_config(), _plan(), NOW, SNAPSHOT, NOW)
    assert controller.fallback == [
        {"domain": "number", "service": "set_value", "data": {"value": -10000}, "target": {"entity_id": "number.min"}}
    ]


def test_wrong_mode_explains_a_failed_write() -> None:
    """A Sofar switched out of passive mode (e.g. in its app) rejects the passive values."""
    from sunflow.control.controller import wrong_mode
    from sunflow.devices.discovery import Snapshot

    profile = {"control_mode": {"key": "charger_use_mode", "option": "Passive Mode"}}
    device = DeviceConfig(id="bat", ha_device_id="h", profile="sofar", profile_version=1, name="Sofar")
    entities = [{"entity_id": "select.sofar_mode", "unique_id": "sofar_charger_use_mode", "device_id": "h"}]

    def snapshot(mode: str) -> Snapshot:
        return Snapshot(entries=[], devices=[], entities=entities, states={"select.sofar_mode": {"state": mode}}, energy_prefs=None)

    assert wrong_mode(profile, device, snapshot("Self Use")) == "Self Use"
    assert wrong_mode(profile, device, snapshot("Passive Mode")) is None
    assert wrong_mode({}, device, snapshot("Self Use")) is None


@pytest.mark.parametrize(
    ("pv", "grid"),
    [
        pytest.param(0, 4800, id="night-from-grid"),
        pytest.param(1500, 3300, id="partly-from-grid"),
    ],
)
async def test_charging_planned_from_the_grid_is_held(tmp_path: Path, pv: float, grid: float) -> None:
    """Grid charging at night is not cut down to the (missing) PV surplus; evcc charges, not waits for PV."""
    charger = FakeCharger(remember=True)
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"evcc": EVCC})
    live = SimpleNamespace(pv_w=0.0, grid_w=4800.0)

    await controller.tick(_charger_config("evcc"), _charge_plan(4800, pv, grid), NOW, SNAPSHOT, NOW, live)

    assert charger.option == "now"
    assert controller.statuses["wb"].reason == "nach Plan"


async def test_goe_on_one_phase_shows_what_it_can_give(tmp_path: Path) -> None:
    """16 A on one phase are 3.7 kW, not the 4.8 kW planned."""
    charger = FakeCharger()
    goe = GOE | {"parameters": GOE["parameters"] | {"phases": {"default": 1}}}
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": goe})

    await controller.tick(_charger_config("goe"), _charge_plan(4800, 0, 4800), NOW, SNAPSHOT, NOW)

    assert charger.current == 16
    assert controller.statuses["wb"].power_w == pytest.approx(16 * 230)


@pytest.mark.parametrize(
    ("phases", "expected"),
    [
        pytest.param(1, 21, id="one-phase-above-16-a"),
        # Fiat 500e: 11 kW on three phases, though the wallbox gives 32 A
        pytest.param(3, 16, id="three-phases-car-limit"),
    ],
)
async def test_current_within_wallbox_and_car(tmp_path: Path, phases: int, expected: int) -> None:
    """A 22 kW go-e gives up to 32 A; the car's own limit per phase count applies on top."""
    from sunflow.config.models import Vehicle

    charger = FakeCharger()
    goe = GOE | {"parameters": GOE["parameters"] | {"phases": {"default": phases}, "max_current_a": {"default": 32}, "max_power": {"default": 22000}}}
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": goe})
    config = _charger_config("goe")
    config.vehicles = [Vehicle(id="fiat", name="500e", capacity_wh=37000, max_power_1p_w=7400, max_power_3p_w=11000)]
    plan = _charge_plan({1: 4800, 3: 22000}[phases], 0, 22000)
    plan.flex = [SimpleNamespace(device_id="wb", unknown_vehicle=False, vehicle_id="fiat", vehicle_method="single", reason=None)]

    await controller.tick(config, plan, NOW, SNAPSHOT, NOW)

    assert charger.current == expected


@pytest.mark.parametrize(
    ("allowed", "warned"),
    [
        pytest.param(6.0, True, id="current-does-not-arrive"),
        pytest.param(16.0, False, id="current-arrives"),
    ],
)
async def test_warns_when_the_current_does_not_arrive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allowed: float, warned: bool) -> None:
    """The go-e reports what it allows: staying at 6 A while 16 A are set is shown and logged once."""
    from sunflow.control import controller as module
    from sunflow.control.journal import ControlJournal

    monkeypatch.setattr(module, "preview", lambda *_: [SimpleNamespace(role="allowed_current", value=allowed, raw_state=str(allowed), entity_id="sensor.acu")])
    charger = FakeCharger()
    goe = GOE | {"roles": {"allowed_current": {}}}
    journal = ControlJournal(tmp_path)
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": goe}, journal)

    for minute in (0, 1, 3, 4):
        await controller.tick(_charger_config("goe"), _charge_plan(11000, 0, 11000), NOW, SNAPSHOT, NOW + timedelta(minutes=minute))

    assert (controller.statuses["wb"].warning is not None) == warned
    assert len([e for e in journal.entries(NOW - timedelta(hours=1)) if e["action"] == "warning"]) == int(warned)


class RememberingCharger(FakeCharger):
    """go-e whose requested current Sunflow overwrites: the user's value is remembered and restored."""

    def __init__(self) -> None:
        """The user set 8 A."""
        super().__init__()
        self.current = 8.0
        self.restored: list[str] = []

    def access(self) -> DeviceAccess:
        """Like device_access with a remembered number."""
        base = super().access()

        async def current_value() -> str | None:
            return str(int(self.current))

        async def restore(value: str) -> None:
            self.current = float(value)
            self.restored.append(value)

        return DeviceAccess(
            plan=base.plan, run=base.run, ensure_mode=current_value, set_mode=restore, release="release", remember_value=True
        )


async def test_requested_current_changes_calmly_and_comes_back(tmp_path: Path) -> None:
    """The current changes at most every 3 minutes; released, the charger gets the user's 8 A again."""
    charger = RememberingCharger()
    goe = GOE | {"parameters": GOE["parameters"] | {"current_interval_min": {"default": 3}}}
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": goe})
    # An older version took the charger over without remembering the current
    TakeoverMarker(tmp_path).set("wb", None)

    await controller.tick(_charger_config("goe"), _charge_plan(11000, 0, 11000), NOW, SNAPSHOT, NOW)
    assert charger.current == 16
    await controller.tick(_charger_config("goe"), _charge_plan(6900, 0, 6900), NOW, SNAPSHOT, NOW + timedelta(minutes=1))
    assert charger.current == 16
    await controller.tick(_charger_config("goe"), _charge_plan(6900, 0, 6900), NOW, SNAPSHOT, NOW + timedelta(minutes=3))
    assert charger.current == 10

    # No current plan: the charger regulates itself with the user's current
    await controller.tick(_charger_config("goe"), _charge_plan(0), NOW - timedelta(hours=1), SNAPSHOT, NOW + timedelta(minutes=4))
    assert (charger.option, charger.current) == ("neutral", 8.0)
    assert TakeoverMarker(tmp_path).get() == {"wb": "8"}


@pytest.mark.parametrize(
    ("car", "connected"),
    [
        pytest.param("Waiting for vehicle", "on", id="waiting"),
        pytest.param("charging finished, vehicle still connected", "on", id="finished"),
        pytest.param("Charger ready, no vehicle", "off", id="no-car"),
    ],
)
def test_goe_api_v1_profile(car: str, connected: str) -> None:
    """An older go-e (API v1): temporary current through the integration's action by charger name, car status as plugged in."""
    from pathlib import Path

    from sunflow.control.controller import device_variables
    from sunflow.devices.executor import plan_charge
    from sunflow.devices.profiles import load_profiles, load_schema
    from sunflow.devices.resolver import preview

    repo = Path(__file__).resolve().parents[1]
    profile = load_profiles(repo / "profiles", load_schema(repo / "schema")).profiles["goecharger/charger"]
    entities = [
        {"entity_id": "sensor.goecharger_garage_p_all", "unique_id": "garage_p_all", "device_id": "d"},
        {"entity_id": "sensor.goecharger_garage_car_status", "unique_id": "garage_car_status", "device_id": "d"},
        {"entity_id": "sensor.goecharger_garage_charger_max_current", "unique_id": "garage_charger_max_current", "device_id": "d"},
        {"entity_id": "switch.goecharger_garage_allow_charging", "unique_id": "garage_allow_charging", "device_id": "d"},
    ]
    snapshot = SimpleNamespace(
        devices=[{"id": "d", "identifiers": [["goecharger", "garage"]]}],
        entities=entities,
        states={
            "sensor.goecharger_garage_car_status": {"state": car, "attributes": {}},
            "sensor.goecharger_garage_charger_max_current": {"state": "10", "attributes": {"unit_of_measurement": "A"}},
            "switch.goecharger_garage_allow_charging": {"state": "off", "attributes": {}},
        },
    )
    device = DeviceConfig(id="wb", ha_device_id="d", profile="goecharger/charger", profile_version=1, name="Wallbox")

    roles = {r.role: r for r in preview(profile, snapshot, "d")}
    variables = device_variables(profile, device, snapshot)
    charge = plan_charge(profile, "charge", {}, variables | {"current_a": 7}, entities)
    release = plan_charge(profile, "release", {}, variables, entities)

    assert roles["connected"].raw_state == connected
    assert (charge[0].domain, charge[0].service, charge[0].data) == ("goecharger", "set_max_current", {"charger_name": "garage", "max_current": 7})
    assert (charge[1].entity_id, charge[1].service) == ("switch.goecharger_garage_allow_charging", "turn_on")
    assert release[0].data["max_current"] == 10


class _SofarHA:
    """Home Assistant with a Sofar whose mode select shows the given state; records service calls."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get_config_entries(self) -> list[Any]:
        return []

    async def get_device_registry(self) -> list[Any]:
        return [{"id": "h"}]

    async def get_entity_registry(self) -> list[Any]:
        return [{"entity_id": "select.sofar_charger_use_mode", "unique_id": "sofar_charger_use_mode", "device_id": "h"}]

    async def get_energy_prefs(self) -> None:
        return None

    async def get_states(self) -> list[Any]:
        return [{"entity_id": "select.sofar_charger_use_mode", "state": self.mode, "attributes": {}}]

    async def command(self, *_: Any, **__: Any) -> list[Any]:
        return []

    async def call_service(self, domain: str, service: str, data: dict[str, Any], **_: Any) -> None:
        self.calls.append((domain, service, data))


@pytest.mark.parametrize("mode", ["unknown", "unavailable"])
async def test_no_takeover_while_the_mode_is_unreadable(tmp_path: Path, mode: str) -> None:
    """Without the current mode nothing could be restored: taking over waits and writes nothing."""
    profile = load_profiles(REPO / "profiles", load_schema(REPO / "schema")).profiles["solax_modbus/sofar"]
    ha = _SofarHA(mode)
    access = device_access(ha, profile, _config().devices[0], EepromGuard(tmp_path, 10))

    with pytest.raises(ExecutorError, match="nicht lesbar"):
        await access.ensure_mode()
    assert ha.calls == []


async def test_unreadable_mode_is_never_restored(tmp_path: Path) -> None:
    """A mode remembered as "unknown" by an older version is not written back on hand back."""
    device = FakeDevice()
    controller = _controller(tmp_path, device)
    TakeoverMarker(tmp_path).set("bat", "unknown")

    await controller.tick(_config(enabled=False), _plan(), NOW, SNAPSHOT, NOW)

    assert "unknown" not in device.modes
    assert TakeoverMarker(tmp_path).get() == {}


async def test_switches_back_into_the_control_mode(tmp_path: Path) -> None:
    """Taken over while the integration was down, the switch got lost: the next step switches again."""
    device = FakeDevice()
    profile = SOFAR | {"control_mode": {"key": "charger_use_mode", "option": "Passive Mode"}}
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: device.access(), lambda: {"sofar": profile})
    # Remembered as "unknown" when it was taken over without an answer
    TakeoverMarker(tmp_path).set("bat", "unknown")
    snapshot = SimpleNamespace(
        devices=[{"id": "h"}],
        entities=[{"entity_id": "select.sofar_charger_use_mode", "unique_id": "sofar_charger_use_mode", "device_id": "h"}],
        states={"select.sofar_charger_use_mode": {"state": "Self Use"}},
    )

    await controller.tick(_config(), _plan(), NOW, snapshot, NOW)

    assert device.modes == ["Passive Mode"]
    # Handed back later, it gets the mode it really had
    assert TakeoverMarker(tmp_path).get() == {"bat": "Self Use"}


def _window(storage: str) -> dict[str, Any]:
    window = {"storage": storage, "steps": [{"number": {"key": "passive_mode_battery_power_min"}}]}
    return SOFAR | {"primitive": {"battery_window": window}, "parameters": {"max_discharge": {"unit": "W", "default": 5000}}}


@pytest.mark.parametrize(
    ("storage", "expected"),
    [
        pytest.param(
            "volatile",
            [("limit_discharge", 5000), ("limit_discharge", 5000), ("limit_discharge", 5000), ("limit_discharge", 4000),
             ("limit_discharge", 2000), ("hold", None)],
            id="ram-covers-the-peak-until-the-energy-is-used",
        ),
        pytest.param("eeprom", [("limit_discharge", 1000)] * 6, id="flash-keeps-the-planned-limit"),
    ],
)
async def test_partial_discharge_is_energy_for_the_slot(tmp_path: Path, storage: str, expected: list) -> None:
    """1000 W planned for the rest of the slot (10 min, ~167 Wh): a 2 kW peak gets it all, then the battery holds."""
    written: list[tuple[str, float | None]] = []

    async def plan(action: str, runtime: dict) -> tuple:
        written.append((action, runtime.get("P")))
        return [], {}

    async def run(calls: list, states: dict, force: bool = False) -> None:
        return None

    access = DeviceAccess(plan=plan, run=run, release=None)
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: access, lambda: {"sofar": _window(storage)})
    peak = SimpleNamespace(battery_w=-2000.0)

    for minute in range(6):
        await controller.tick(_config(), _plan("limit_discharge", 1000), NOW, SNAPSHOT, NOW + timedelta(minutes=minute), peak)

    assert written[: len(expected)] == expected


async def test_replan_within_the_slot_starts_a_new_budget(tmp_path: Path) -> None:
    """A replan gives the power for the rest of the slot; what was used before does not count against it."""
    written: list[tuple[str, float | None]] = []

    async def plan(action: str, runtime: dict) -> tuple:
        written.append((action, runtime.get("P")))
        return [], {}

    async def run(calls: list, states: dict, force: bool = False) -> None:
        return None

    access = DeviceAccess(plan=plan, run=run, release=None)
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: access, lambda: {"sofar": _window("volatile")})
    peak = SimpleNamespace(battery_w=-2000.0)
    for minute in range(6):
        await controller.tick(_config(), _plan("limit_discharge", 1000), NOW, SNAPSHOT, NOW + timedelta(minutes=minute), peak)
    assert written[-1] == ("hold", None)

    await controller.tick(_config(), _plan("limit_discharge", 600), NOW, SNAPSHOT, NOW + timedelta(minutes=6), peak)

    # 600 W for the 4 minutes left: 40 Wh, at most 2.4 kW for the next minute
    assert written[-1] == ("limit_discharge", 2400)


@pytest.mark.parametrize(
    ("profile", "volatile"),
    [
        pytest.param("solax_modbus/sofar", True, id="sofar-passive-mode-in-ram"),
        pytest.param("huawei_solar/sun2000_luna2000", False, id="huawei-max-discharge-in-flash"),
    ],
)
def test_limit_discharge_storage_of_the_profiles(profile: str, volatile: bool) -> None:
    """Only inverters that keep the discharge limit in RAM get it rewritten every minute."""
    library = load_profiles(REPO / "profiles", load_schema(REPO / "schema"))
    assert volatile_action(library.profiles[profile], "limit_discharge") is volatile


GOE_CURRENT = GOE | {"roles": {"current": {}}}


def _surplus_plan() -> SimpleNamespace:
    """Nothing planned now for the wallbox (forecast too low); the car still needs energy."""
    plan = _charge_plan(0, pv=700)
    plan.flex = [
        SimpleNamespace(
            device_id="wb", unknown_vehicle=False, vehicle_id=None, vehicle_method="card", reason=None, need_wh=13750.0,
            current=47.0, target=80.0,
        )
    ]
    plan.battery = SimpleNamespace(capacity_wh=20000.0)
    return plan


def _sample(grid: float, battery: float = 0.0, soc_pct: float = 97.0, own: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(pv_w=4400.0, grid_w=grid, battery_w=battery, soc_wh=200.0 * soc_pct, consumers={"wb": own})


async def test_unplanned_surplus_charges_after_it_lasts(tmp_path: Path) -> None:
    """Export the plan did not expect goes to the car, once it lasted two minutes, not at the first reading."""
    charger = FakeCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE_CURRENT})
    config = _charger_config("goe")

    await controller.tick(config, _surplus_plan(), NOW, SNAPSHOT, NOW, _sample(-5000))
    assert charger.option == "dont_charge"
    await controller.tick(config, _surplus_plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=2), _sample(-5000))

    assert (charger.option, charger.current) == ("charge", 8)
    assert controller.statuses["wb"].reason == "nutzt PV-Überschuss, den der Plan nicht erwartet hat"


async def test_unplanned_surplus_rides_out_a_short_dip(tmp_path: Path) -> None:
    """A cloud keeps the minimum current for three minutes before charging stops."""
    charger = FakeCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE_CURRENT})
    config = _charger_config("goe")
    for minute in (0, 2):
        await controller.tick(config, _surplus_plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=minute), _sample(-5000))

    # The car takes 4.1 kW, the house now imports 1 kW
    await controller.tick(config, _surplus_plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=3), _sample(1000, own=4140))
    assert charger.option == "charge"
    await controller.tick(config, _surplus_plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=6), _sample(1000, own=4140))

    assert charger.option == "dont_charge"


@pytest.mark.parametrize(
    ("soc_pct", "option"),
    [pytest.param(60.0, "charge", id="above-the-condition"), pytest.param(40.0, "dont_charge", id="below-the-condition")],
)
async def test_battery_condition_puts_the_car_first(tmp_path: Path, soc_pct: float, option: str) -> None:
    """Once the battery reached the car's condition, its charging power counts as surplus for the car."""
    charger = FakeCharger()
    controller = PlanController(TakeoverMarker(tmp_path), lambda d: charger.access(), lambda: {"goe": GOE_CURRENT})
    config = _charger_config("goe")
    config.priorities = ["battery", "wb"]
    config.priority_conditions = {"wb": [PriorityCondition(device_id="battery", min_pct=50)]}

    for minute in (0, 2):
        await controller.tick(config, _surplus_plan(), NOW, SNAPSHOT, NOW + timedelta(minutes=minute), _sample(0, 5000, soc_pct))

    assert charger.option == option
