"""Own loads with a battery of their own: test charge after a trigger, done when the power drops."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sunflow.config.models import ChargerSettings, DeviceConfig, RoleOverride, SunflowConfig
from sunflow.control.charger import ChargerReady, ChargerWatch
from sunflow.devices.discovery import Snapshot
from sunflow.planning.plan import flex_loads

NOW = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)
LOCKED = {"lock.shed": {"state": "locked"}}
UNLOCKED = {"lock.shed": {"state": "unlocked"}}


def _bike(**changes: object) -> DeviceConfig:
    settings = {"trigger_entity": "lock.shed", "trigger_state": "locked", "trigger_delay_min": 2, "energy_wh": 400.0}
    return DeviceConfig(
        id="bike", ha_device_id="manual:bike", profile="generic/switch", profile_version=2, name="E-Bike",
        charger=ChargerSettings(**(settings | changes)),
    )


def _minutes(watch: ChargerWatch, device: DeviceConfig, states: dict, start: int, count: int, switch_on: bool, power: float) -> list:
    return [
        watch.step(device, switch_on, power, states, NOW + timedelta(minutes=start + m))
        for m in range(count)
    ]


@pytest.mark.parametrize(
    ("power", "ready", "after"),
    [
        pytest.param(120.0, True, None, id="draws-power-ready"),
        pytest.param(0.0, False, "stop", id="no-power-not-ready"),
    ],
)
def test_trigger_starts_a_test_charge(tmp_path: Path, power: float, ready: bool, after: str | None) -> None:
    """Shed locked: after the delay the switch goes on for a moment; power decides about readiness."""
    watch, bike = ChargerWatch(tmp_path), _bike()
    assert watch.step(bike, False, 0.0, UNLOCKED, NOW) == ("stop", "Nicht ladebereit")
    # Locked: nothing yet during the delay
    assert watch.step(bike, False, 0.0, LOCKED, NOW + timedelta(minutes=1)) == ("stop", "Nicht ladebereit")
    test = watch.step(bike, False, 0.0, LOCKED, NOW + timedelta(minutes=3))
    assert test is not None and test[0] == "charge"
    result = watch.step(bike, True, power, LOCKED, NOW + timedelta(minutes=4))

    assert (result[0] if result else None) == after
    assert watch.ready(bike, LOCKED) is ready
    assert watch.changed is ready


def test_other_trigger_states_do_not_test(tmp_path: Path) -> None:
    """Only the chosen state of the trigger starts a test (unlocking does not)."""
    watch, bike = ChargerWatch(tmp_path), _bike()
    watch.step(bike, False, 0.0, LOCKED, NOW)
    results = _minutes(watch, bike, UNLOCKED, 1, 10, False, 0.0)
    assert {r[0] for r in results} == {"stop"}


def test_full_after_low_power_and_energy_learned(tmp_path: Path) -> None:
    """Switched on by the plan, power below the threshold for a few minutes: full; the charge is learned."""
    watch, bike = ChargerWatch(tmp_path), _bike()
    watch.step(bike, False, 0.0, UNLOCKED, NOW)
    watch.step(bike, False, 0.0, LOCKED, NOW + timedelta(minutes=1))
    watch.step(bike, False, 0.0, LOCKED, NOW + timedelta(minutes=3))
    watch.step(bike, True, 150.0, LOCKED, NOW + timedelta(minutes=4))
    # Charging by plan for two hours at 150 W, then the charger stops
    assert set(_minutes(watch, bike, LOCKED, 5, 120, True, 150.0)) == {None}
    results = _minutes(watch, bike, LOCKED, 125, 5, True, 2.0)

    assert ("stop", "Voll oder abgesteckt") in results
    assert not watch.ready(bike, LOCKED)
    assert watch.energy_wh(bike) == pytest.approx(300, abs=5)
    # Kept across restarts
    assert ChargerWatch(tmp_path).energy_wh(bike) == pytest.approx(300, abs=5)


def test_low_power_right_after_switching_on_is_not_full(tmp_path: Path) -> None:
    """Chargers take a moment to start."""
    watch, bike = ChargerWatch(tmp_path), _bike(connected_entity="binary_sensor.bike_plugged")
    states = {"binary_sensor.bike_plugged": {"state": "on"}}
    assert _minutes(watch, bike, states, 0, 2, True, 0.0) == [None, None]


def test_plug_entity_decides_and_full_waits_for_unplugging(tmp_path: Path) -> None:
    """With a plug entity no test is needed; full stays full until it was unplugged once."""
    watch, bike = ChargerWatch(tmp_path), _bike(connected_entity="binary_sensor.bike_plugged", trigger_entity=None)
    plugged, unplugged = {"binary_sensor.bike_plugged": {"state": "on"}}, {"binary_sensor.bike_plugged": {"state": "off"}}
    assert watch.step(bike, False, 0.0, unplugged, NOW) == ("stop", "Nicht angesteckt")
    assert watch.ready(bike, plugged)
    _minutes(watch, bike, plugged, 1, 10, True, 0.0)
    assert not watch.ready(bike, plugged)
    watch.step(bike, False, 0.0, unplugged, NOW + timedelta(minutes=20))
    assert watch.ready(bike, plugged)


@pytest.mark.parametrize(
    ("charger", "states", "need", "reason"),
    [
        # Without a SoC with a reserve: the charge may take longer than the learned mean
        pytest.param(ChargerReady(ready=True, energy_wh=400.0), {}, 500.0, None, id="ready-energy-per-charge"),
        pytest.param(ChargerReady(ready=False, energy_wh=400.0), {}, None, "Nicht ladebereit (nicht angesteckt oder voll)", id="not-ready"),
        pytest.param(
            ChargerReady(ready=True, energy_wh=None), {"sensor.bike_soc": {"state": "40"}},
            pytest.approx(0.6 * 500 / 0.85), None, id="from-soc",
        ),
    ],
)
def test_planned_only_while_ready(library, charger: ChargerReady, states: dict, need: object, reason: str | None) -> None:
    """Ready: planned with the energy per charge or what its battery needs; not ready: nothing."""
    bike = _bike(soc_entity="sensor.bike_soc", capacity_wh=500.0)
    bike.role_overrides = {"switch": RoleOverride(entity_id="switch.bike")}
    snapshot = Snapshot(entries=[], devices=[], entities=[], states={k: v | {"attributes": {}} for k, v in (states | {"switch.bike": {"state": "off"}}).items()}, energy_prefs=None)
    starts = [NOW + timedelta(minutes=15 * i) for i in range(96)]

    loads, (info,), _, _ = flex_loads(
        SunflowConfig(devices=[bike]), library.profiles, snapshot, starts, ZoneInfo("Europe/Berlin"), chargers={"bike": charger}
    )

    assert [load.energy_wh for load in loads] == ([need] if need is not None else [])
    assert info.reason == reason


class _ShedHA:
    """Home Assistant with the bike's switch, its power and the shed lock; records actions."""

    def __init__(self) -> None:
        self.states = {
            "switch.bike": {"entity_id": "switch.bike", "state": "off", "attributes": {}},
            "sensor.bike_power": {"entity_id": "sensor.bike_power", "state": "0", "attributes": {"unit_of_measurement": "W"}},
            "lock.shed": {"entity_id": "lock.shed", "state": "unlocked", "attributes": {}},
        }
        self.calls: list[tuple[str, str]] = []

    async def get_config_entries(self) -> list:
        return []

    async def get_device_registry(self) -> list:
        return []

    async def get_entity_registry(self) -> list:
        return []

    async def get_energy_prefs(self) -> None:
        return None

    async def get_states(self) -> list:
        return list(self.states.values())

    async def command(self, *_: object, **__: object) -> list:
        return []

    async def call_service(self, domain: str, service: str, data: dict | None = None, target: dict | None = None, **_: object) -> None:
        self.calls.append((domain, service))
        if domain == "switch":
            self.states["switch.bike"]["state"] = "on" if service == "turn_on" else "off"


async def test_controller_switches_for_the_test_only_when_triggered(library, tmp_path: Path) -> None:
    """Not ready: the switch stays off; the locked shed switches it on for the test."""
    from types import SimpleNamespace

    from sunflow.config.models import ControlSettings
    from sunflow.control.controller import PlanController, TakeoverMarker, device_access
    from sunflow.devices.executor import EepromGuard

    ha = _ShedHA()
    bike = _bike()
    bike.role_overrides = {"switch": RoleOverride(entity_id="switch.bike"), "power": RoleOverride(entity_id="sensor.bike_power")}
    config = SunflowConfig(devices=[bike], control=ControlSettings(enabled=True, devices={"bike": True}))
    profiles = library.profiles
    controller = PlanController(
        TakeoverMarker(tmp_path), lambda d: device_access(ha, profiles[d.profile], d, EepromGuard(tmp_path, 10)), lambda: profiles
    )

    async def tick(minute: int) -> None:
        snapshot = Snapshot(entries=[], devices=[], entities=[], states=dict(ha.states), energy_prefs=None)
        await controller.tick(config, None, None, snapshot, NOW + timedelta(minutes=minute), SimpleNamespace())

    await tick(0)
    await tick(1)
    assert ("switch", "turn_on") not in ha.calls
    ha.states["lock.shed"]["state"] = "locked"
    await tick(2)
    await tick(5)
    assert ("switch", "turn_on") in ha.calls


@pytest.mark.parametrize("plugged", [True, False])
def test_plugged_by_hand(tmp_path: Path, plugged: bool) -> None:
    """"Plugged in now" in the control tab makes it ready without a test; "no longer" ends it."""
    watch, bike = ChargerWatch(tmp_path), _bike()
    watch.set_plugged(bike, True, NOW)
    watch.set_plugged(bike, plugged, NOW + timedelta(minutes=1))

    assert watch.ready(bike, UNLOCKED) is plugged
    assert watch.changed
    assert (watch.step(bike, False, 0.0, UNLOCKED, NOW + timedelta(minutes=2)) is None) is plugged


@pytest.mark.parametrize(
    ("minutes", "remaining"),
    [
        pytest.param(0, 400.0, id="not-started"),
        pytest.param(60, 250.0, id="window-ended-halfway"),
        # Longer than usual: a part stays planned until the charger is found full
        pytest.param(200, 80.0, id="more-than-usual"),
    ],
)
def test_interrupted_charge_plans_the_rest(tmp_path: Path, minutes: int, remaining: float) -> None:
    """A charge the plan switched off before full goes on with the rest, not with a whole charge."""
    watch, bike = ChargerWatch(tmp_path), _bike()
    watch.set_plugged(bike, True, NOW)
    watch.step(bike, True, 150.0, LOCKED, NOW)
    _minutes(watch, bike, LOCKED, 1, minutes, True, 150.0)
    # The plan switches off at the end of its window
    watch.step(bike, False, 0.0, LOCKED, NOW + timedelta(minutes=minutes + 1))

    planning = watch.planning(SunflowConfig(devices=[bike]), LOCKED)

    assert planning["bike"].ready
    assert planning["bike"].energy_wh == pytest.approx(remaining, abs=3)
