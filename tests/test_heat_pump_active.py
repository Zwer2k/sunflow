"""Writing to the heat pump for real (design/heat_pump.md, 4, 12).

The user's switch per heat pump (DeviceConfig.flow_active, default off); taking over with the base frozen and
the correction at 0; the set value (EEPROM, EepromGuard) and the block go to the heat pump through a fake Home
Assistant; a failed write is taken back; the check after adopt_delay; switching off resets nothing. The
acceptance is a simulation with a simple HPSU (section 14): it takes Max T-Vorlauf over after 60 s, starts
and stops by the 3 K rules, jumps after a start and follows the room thermostat block. Nothing here talks to
a real heat pump.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from heat_pump_node import joined
import pytest
from sunflow.api.heat_pump import router
from sunflow.config.models import DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.executor import EepromGuard
from sunflow.heat_pump.active import ADOPTED, LIMITED, MISSING, AdoptCheck, WriteTargets, write_targets
from sunflow.heat_pump.budget import EepromLog, VirtualLog
from sunflow.heat_pump.guard import FOREIGN
from sunflow.heat_pump.limits import ControlSettings
from sunflow.heat_pump.samples import Minute
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from sunflow.heat_pump.states import HeatingTracker, Reading, StateSettings

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
MIN = timedelta(minutes=1)
PROFILE = "esphome/rotex_hpsu_can"
ROOM = "sensor.wohnzimmer"
SETPOINT_ENTITY = "number.hpsu_can_max_t_vorlauf"
BLOCK_ENTITY = "select.hpsu_can_raum_thermostat"
TARGETS = WriteTargets(setpoint_entity=SETPOINT_ENTITY, block_entity=BLOCK_ENTITY, block_on="An", block_off="Aus")
SETTINGS = ControlSettings(step=0.1, entity_min=20.0, entity_max=90.0)


@dataclass
class Hpsu:
    """A simple HPSU in fixed mode: Max T-Vorlauf taken over after the delay, the effective setpoint at most
    its own limit (T-Vorlauf Tag), 3 K start/stop, a jump after the start, cooling in pauses and while blocked."""

    max_setpoint: float = 31.0
    tv: float = 30.0
    compressor: bool = True
    blocked: bool = False
    cap: float = 40.0
    delay: timedelta = timedelta(seconds=60)
    jump_k: float = 4.0
    cool_k: float = 0.3
    # Share of the way to its setpoint Tv makes each minute
    follow: float = 0.5
    pending: tuple[float, datetime] | None = None
    started_at: datetime | None = None
    stops: list[datetime] = field(default_factory=list)
    shown_setpoint: float | None = None
    # Writes the heat pump ignores (lost on the bus)
    lose_writes: bool = False

    @property
    def effective(self) -> float:
        return min(self.max_setpoint, self.cap)

    def write(self, value: float, at: datetime) -> None:
        if not self.lose_writes:
            self.pending = (value, at)

    def step(self, now: datetime) -> None:
        if self.pending is not None and now - self.pending[1] >= self.delay:
            self.max_setpoint, self.pending = self.pending[0], None
        if self.blocked:
            self.compressor = False
        if self.compressor:
            since = now - self.started_at if self.started_at else timedelta(hours=1)
            if since <= MIN:
                self.tv += self.jump_k
            else:
                self.tv += (self.effective - 0.5 - self.tv) * self.follow
            if self.tv >= self.effective + 3.0:
                self.compressor = False
                self.stops.append(now)
        else:
            self.tv = max(22.0, self.tv - self.cool_k)
            if not self.blocked and self.effective >= self.tv + 3.0:
                self.compressor, self.started_at = True, now

    def reading(self) -> Reading:
        return Reading(
            program="heating", activity="heating" if self.compressor else "idle", strategy="fixed", blocked=self.blocked,
            error="err_0", compressor=self.compressor,
            values={
                "flow_temperature": round(self.tv, 1), "flow_setpoint": self.effective,
                "max_flow_setpoint": self.max_setpoint if self.shown_setpoint is None else self.shown_setpoint, "bypass_valve": 0.0,
                # Slope 1 of the heat pump's curve: flow target = 21 + (21 − outside) at the default setpoint
                "heating_curve_slope": 1.0,
            },
        )


class FakeHA:
    """Home Assistant as far as writing goes: the calls, applied to the HPSU."""

    def __init__(self, hpsu: Hpsu) -> None:
        self.hpsu = hpsu
        self.calls: list[tuple[str, str, dict[str, Any], str]] = []
        self.fail = False
        self.now = NOW

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        return None

    async def call_service(self, domain: str, service: str, data: dict[str, Any] | None = None,
                           target: dict[str, Any] | None = None, return_response: bool = False) -> None:
        if self.fail:
            raise RuntimeError("node away")
        entity = (target or {})["entity_id"]
        self.calls.append((domain, service, dict(data or {}), entity))
        if entity == SETPOINT_ENTITY:
            self.hpsu.write(float((data or {})["value"]), self.now)
        elif entity == BLOCK_ENTITY:
            self.hpsu.blocked = (data or {})["option"] == "An"


class Plant:
    """FlowShadow on one heat pump with the HPSU behind a fake Home Assistant, minute by minute."""

    def __init__(self, tmp_path: Path, hpsu: Hpsu | None = None, room: float = 21.0) -> None:
        self.hpsu = hpsu or Hpsu()
        self.ha = FakeHA(self.hpsu)
        self.tmp_path = tmp_path
        self.device = DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=3, name="HPSU CAN")
        self.config = SunflowConfig(devices=[self.device], house=HouseSettings(rooms=[Room(entity_id=ROOM)]))
        self.flow = FlowShadow(HeatPumpStateStore(tmp_path), self.ha, tmp_path)
        self.tracker = HeatingTracker(StateSettings(
            settle_after_bypass=timedelta(0), frozen_after={"hot_water": timedelta(minutes=10)}, deadband=0.5,
            self_limit_after=timedelta(minutes=5),
        ))
        self.room = room
        self.now = NOW
        self.notes: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.flows: list[dict[str, Any]] = []
        # Outside temperature: with slope 1 and room setpoint 21 °C the flow target is 42 − outside
        self.outside = 11.0

    async def minute(self, hpsu_steps: bool = True) -> dict[str, Any]:
        if hpsu_steps:
            self.hpsu.step(self.now)
        self.ha.now = self.now
        reading = self.hpsu.reading()
        evaluation = self.tracker.evaluate(reading, self.now, True)
        minute = Minute(at=self.now, reading=reading, evaluation=evaluation, heat_w=None, heat_source=None, outside_c=self.outside,
                        rooms={}, control=SETTINGS, targets=TARGETS)
        states = {ROOM: {"state": str(self.room), "last_updated": self.now.isoformat()}}
        await self.flow.tick(self.config, states, {"hp": (self.device, minute)}, self.now)
        self.notes += [m for _, m, _ in self.flow.notes]
        self.events += [e for _, e in self.flow.source_events]
        self.flow.notes, self.flow.source_events = [], []
        status = self.flow.flow_status("hp")
        assert status is not None
        self.flows.append(status)
        self.now += MIN
        return status

    def target(self, flow_c: float) -> None:
        """Outside temperature for a flow target of the curve, at once (no averaging over the last minutes)."""
        self.outside = 42.0 - flow_c
        self.flow._outside.pop("hp", None)

    async def run(self, minutes: int) -> dict[str, Any]:
        status: dict[str, Any] = {}
        for _ in range(minutes):
            status = await self.minute()
        return status

    def writes(self, shadow: bool | None = False) -> list[tuple[str, Any]]:
        return [(e["reason"], e.get("value_c", e.get("block"))) for e in self.events
                if e["kind"] == "write" and (shadow is None or e["shadow"] is shadow)]

    def eeprom_times(self) -> list[datetime]:
        return EepromGuard(self.tmp_path, 72).times(SETPOINT_ENTITY)


# The switch


def test_switch_is_off_by_default_and_only_true_switches_it_on() -> None:
    assert DeviceConfig(ha_device_id="x", profile=PROFILE, profile_version=3, name="HPSU").flow_active is False
    for value in ("true", 1, "on", None):
        assert DeviceConfig(ha_device_id="x", profile=PROFILE, profile_version=3, name="HPSU", flow_active=value).flow_active is False
    assert DeviceConfig(ha_device_id="x", profile=PROFILE, profile_version=3, name="HPSU", flow_active=True).flow_active is True


def test_write_targets_from_the_profile_and_the_manifest(library: Any) -> None:
    entities = {"max_flow_setpoint": SETPOINT_ENTITY, "heating_block": BLOCK_ENTITY}

    targets = write_targets(library.profiles[PROFILE], joined(), entities)

    assert targets == WriteTargets(setpoint_entity=SETPOINT_ENTITY, setpoint_storage="eeprom", block_entity=BLOCK_ENTITY, block_on="An", block_off="Aus")
    setpoint = targets.setpoint_call(32.5)
    assert setpoint is not None and (setpoint.domain, setpoint.service, setpoint.data, setpoint.storage) == ("number", "set_value", {"value": 32.5}, "eeprom")
    block = targets.block_call(True)
    assert block is not None and (block.domain, block.service, block.data, block.storage) == ("select", "select_option", {"option": "An"}, "volatile")
    # A switch as block, a sensor where the number should be: no setpoint target
    switch = write_targets(library.profiles[PROFILE], joined(), {"max_flow_setpoint": "sensor.x", "heating_block": "switch.block"})
    assert switch.setpoint_entity is None
    off = switch.block_call(False)
    assert off is not None and (off.domain, off.service) == ("switch", "turn_off")


async def test_shadow_writes_nothing(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(5)
    plant.target(33.0)
    status = await plant.run(3)

    assert plant.ha.calls == []
    assert status["setpoint"]["mode"] == "shadow" and status["target_c"] == 33.0
    assert [r for r, _ in plant.writes(shadow=True)] == ["normal"]
    assert plant.eeprom_times() == []


# Taking over


async def test_switching_on_starts_the_correction_at_0(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(5)
    # A correction run up in the shadow, a virtual write in the guard
    plant.flow._correction("hp", "fixed").value_k = 2.0
    plant.flow.guard("hp").written_c = 35.0
    plant.device.flow_active = True
    taken = await plant.minute()

    assert taken["correction_k"] == 0.0 and taken["target_c"] == pytest.approx(31.0)
    assert taken["setpoint"]["mode"] == "active"
    # The guard starts from the real Max T-Vorlauf, without time or reason; nothing needed, nothing written
    assert (taken["setpoint"]["written_c"], taken["setpoint"]["written_at"], taken["setpoint"]["written_reason"]) == (31.0, None, None)
    assert plant.ha.calls == []
    assert any("Lernen + Regeln eingeschaltet" in n for n in plant.notes)
    assert any("übernimmt die Wärmepumpe: Korrektur 0" in n for n in plant.notes)
    # The flags survive a restart: no second takeover
    stored = HeatPumpStateStore(tmp_path).devices["hp"]
    assert (stored["active"], stored["writing"], stored["corrections"]["fixed"]) == (True, True, 0.0)


async def test_takeover_with_a_foreign_block_keeps_it(tmp_path: Path) -> None:
    plant = Plant(tmp_path, Hpsu(blocked=True, compressor=False))
    await plant.run(3)
    plant.device.flow_active = True
    status = await plant.minute()

    guard = plant.flow.guard("hp")
    assert (guard.block, guard.block_reason) == (True, FOREIGN)
    assert status["guard"]["phase"] == "blocked"
    assert plant.ha.calls == []
    assert any("nicht von Sunflow: Sunflow hebt sie nicht auf" in n for n in plant.notes)


# Writing for real


async def test_a_real_write_goes_to_the_heat_pump_and_counts_in_eeprom(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(3)
    plant.device.flow_active = True
    await plant.run(6)
    # Colder outside: the curve raises the flow target
    plant.target(33.0)
    await plant.run(2)

    assert plant.writes() == [("normal", 33.0)]
    assert plant.ha.calls == [("number", "set_value", {"value": 33.0}, SETPOINT_ENTITY)]
    assert len(plant.eeprom_times()) == 1
    assert isinstance(plant.flow.budget("hp").log, EepromLog)
    assert any(n.startswith("Max T-Vorlauf geschrieben: 33,0 °C (vorher 31,0 °C)") for n in plant.notes)
    # The change of the real value to it is Sunflow's own, not foreign; the heat pump takes it over after 60 s
    await plant.run(3)
    assert plant.hpsu.max_setpoint == 33.0
    assert [e for e in plant.events if e["kind"] == "foreign_write"] == []
    assert plant.flow.adopt_check("hp").state == ADOPTED
    budget = plant.flows[-1]["guard"]["budget"]
    assert (budget["virtual"], budget["used_hour"]) == (False, 1)


async def test_a_failed_write_is_taken_back_and_tried_again(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(3)
    plant.device.flow_active = True
    await plant.run(6)
    plant.target(33.0)
    plant.ha.fail = True
    status = await plant.minute()

    assert plant.writes() == [] and plant.eeprom_times() == []
    assert status["setpoint"]["written_c"] == 31.0
    assert not any(n.startswith("Max T-Vorlauf geschrieben") for n in plant.notes)
    assert any(n.startswith("Max T-Vorlauf nicht geschrieben (node away)") for n in plant.notes)
    # Still failing: tried each minute, noted once
    await plant.run(3)
    assert sum("nicht geschrieben" in n for n in plant.notes) == 1
    # Back: written with the next minute (no spacing from the failed write)
    plant.ha.fail = False
    await plant.minute()
    assert plant.writes() == [("normal", 33.0)] and len(plant.eeprom_times()) == 1


async def test_eeprom_limit_refuses_the_write(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    guard = EepromGuard(tmp_path, 72)
    for _ in range(72):
        guard.record(SETPOINT_ENTITY, NOW - timedelta(days=2))
    await plant.run(3)
    plant.device.flow_active = True
    await plant.run(6)
    plant.target(33.0)
    await plant.minute()

    # The daily counter of EepromGuard is per date: the old writes are of today's file date
    assert plant.ha.calls == []
    assert any("Max T-Vorlauf nicht geschrieben (EEPROM write limit reached)" in n for n in plant.notes)


async def test_not_taken_over_the_guard_follows_the_value_shown(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(3)
    plant.device.flow_active = True
    await plant.run(6)
    plant.hpsu.lose_writes = True
    plant.target(33.0)
    await plant.run(3)

    assert plant.writes() == [("normal", 33.0)]
    assert plant.flow.adopt_check("hp").state == MISSING
    assert plant.flow.guard("hp").written_c == 31.0
    assert sum("nicht übernommen" in n for n in plant.notes) == 1
    # Tried again after the spacing; a second miss is not noted again
    await plant.run(6)
    assert plant.writes() == [("normal", 33.0), ("normal", 33.0)]
    assert sum("nicht übernommen" in n for n in plant.notes) == 1


def test_adopt_check_limited_while_heating() -> None:
    check = AdoptCheck()
    check.wrote(45.0, NOW)
    reading = Reading(program="heating", activity="heating", strategy="fixed", compressor=True,
                      values={"max_flow_setpoint": 45.0, "flow_setpoint": 40.0, "bypass_valve": 0.0})

    early = check.step(NOW + timedelta(seconds=30), reading, 60, 0.1, 0.5)
    hot_water = check.step(NOW + MIN, Reading(program="heating", activity="hot_water", values={"max_flow_setpoint": 45.0, "flow_setpoint": 48.0, "bypass_valve": 100.0}), 60, 0.1, 0.5)
    limited = check.step(NOW + 2 * MIN, reading, 60, 0.1, 0.5)

    assert early.notes == [] and hot_water.notes == [] and check.state == LIMITED
    [(message, warn)] = limited.notes or []
    assert warn and "bleibt bei 40,0 °C" in message
    assert check.status() == {"value_c": 45.0, "written_at": NOW.isoformat(), "state": LIMITED,
                              "checked_at": (NOW + 2 * MIN).isoformat(), "effective_c": 40.0}


async def test_own_block_is_not_foreign_and_goes_to_the_heat_pump(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(3)
    plant.device.flow_active = True
    await plant.run(2)
    # The room 0.6 K above its target: Sunflow sets the block
    plant.room = 21.6
    await plant.run(3)

    assert ("select", "select_option", {"option": "An"}, BLOCK_ENTITY) in plant.ha.calls
    guard = plant.flow.guard("hp")
    assert (guard.block, guard.block_reason) == (True, "room_warm")
    assert plant.hpsu.blocked and not plant.hpsu.compressor
    assert any(n.startswith("Raumthermostat-Sperre gesetzt") for n in plant.notes)
    assert not any("nicht von Sunflow" in n for n in plant.notes)
    # The block does not count in the EEPROM budget
    assert plant.eeprom_times() == []


async def test_own_block_released_by_someone_else(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(3)
    plant.device.flow_active = True
    await plant.run(2)
    plant.room = 21.6
    await plant.run(2)
    plant.hpsu.blocked = False
    # Within the confirm time the own switch counts; after it the heat pump's state
    await plant.run(11)

    guard = plant.flow.guard("hp")
    assert guard.block is False and guard.block_reason is None
    assert sum("von außen aufgehoben" in n for n in plant.notes) == 1


# Switching off, mode off


async def test_switching_off_resets_nothing(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(3)
    plant.device.flow_active = True
    await plant.run(2)
    plant.room = 21.6
    await plant.run(2)
    plant.target(33.0)
    calls = len(plant.ha.calls)
    plant.device.flow_active = False
    status = await plant.run(30)

    # Nothing written any more, the block stays set at the heat pump and is someone else's now
    assert len(plant.ha.calls) == calls
    assert plant.hpsu.blocked
    assert plant.flow.guard("hp").block_reason == FOREIGN
    assert status["setpoint"]["mode"] == "shadow"
    assert any("Regeln ausgeschaltet" in n and "Sperre bleibt gesetzt" in n for n in plant.notes)
    assert isinstance(plant.flow.budget("hp").log, VirtualLog)
    assert HeatPumpStateStore(tmp_path).devices["hp"]["active"] is False


async def test_mode_off_writes_nothing_and_takes_over_anew(tmp_path: Path) -> None:
    plant = Plant(tmp_path)
    await plant.run(3)
    plant.device.flow_active = True
    await plant.run(2)
    plant.config.house.climate.mode = "off"
    plant.target(34.0)
    plant.flow._correction("hp", "fixed").value_k = 1.0
    status = await plant.run(10)

    assert plant.ha.calls == []
    assert status["guard"]["inactive"] == ["mode_off"]
    plant.config.house.climate.mode = "heat"
    back = await plant.minute()
    assert back["correction_k"] == 0.0 and back["target_c"] == pytest.approx(34.0)
    assert sum("Sunflow übernimmt die Wärmepumpe" in n for n in plant.notes) == 2


# Acceptance: simulation with the HPSU


async def test_simulation_block_pause_start_without_self_caused_shutdown(tmp_path: Path) -> None:
    """Running, the room gets warm (block), cools again (release), the compressor stays off: Sunflow restarts it
    after 5 minutes, leaves it 10 minutes, lowers once above the shutdown bound; all through the fake Home
    Assistant, within the budget, no shutdown (learning phase off)."""
    plant = Plant(tmp_path, Hpsu(max_setpoint=27.0, tv=26.5, cool_k=0.02))
    plant.device.flow_learn_stops = False
    plant.target(27.0)
    await plant.run(10)
    plant.device.flow_active = True
    await plant.run(10)
    plant.room = 21.6
    await plant.run(15)
    # Released after the block's minimum duration (20 min): the HPSU would not start on 27 °C by itself
    plant.room = 21.1
    await plant.run(60)

    setpoint_calls = [c for c in plant.ha.calls if c[3] == SETPOINT_ENTITY]
    block_calls = [c[2]["option"] for c in plant.ha.calls if c[3] == BLOCK_ENTITY]
    writes = plant.writes()
    assert block_calls == ["An", "Aus"]
    reasons = [r for r, _ in writes if r in ("normal", "start", "track")]
    # One restart: two writes of the set value (restart, lowered once)
    assert reasons == ["start", "normal"]
    assert writes[:3] == [("block_on", True), ("block_off", False), ("start", 29.0)]
    restart = next(f for f in plant.flows if f["guard"]["phase"] == "restart")
    assert restart["guard"]["restart_at"] is not None
    assert len(setpoint_calls) == len(reasons) == len(plant.eeprom_times())
    assert plant.hpsu.stops == []
    assert [e for e in plant.events if e["kind"] == "foreign_write"] == []
    assert plant.hpsu.compressor and not plant.hpsu.blocked
    # The real write events carry shadow false; the budget counts them per hour
    assert all(e["shadow"] is False for e in plant.events if e["kind"] == "write")
    assert plant.flows[-1]["guard"]["budget"]["used_today"] == len(setpoint_calls)


async def test_simulation_learning_phase_lets_it_stop_and_restarts(tmp_path: Path) -> None:
    """Learning the stop margin: lowered straight to the flow target, the HPSU switches off (Tv ≥ setpoint + 3 K);
    5 minutes later Sunflow restarts it, 10 minutes after the start it lowers again."""
    plant = Plant(tmp_path, Hpsu(max_setpoint=35.0, tv=34.0, follow=0.1))
    plant.target(29.0)
    await plant.run(5)
    plant.device.flow_active = True
    await plant.run(62)

    writes = [(r, v) for r, v in plant.writes() if r in ("normal", "start", "track")]
    assert plant.hpsu.stops, "the learning phase lets the compressor stop"
    assert writes[0] == ("normal", 29.0) and ("start", writes[1][1]) == writes[1]
    # Each stop is followed by a restart 5 to 6 minutes later
    starts = [datetime.fromisoformat(e["at"]) for e in plant.events if e["kind"] == "write" and e["reason"] == "start"]
    for stop in plant.hpsu.stops[: len(starts)]:
        assert any(timedelta(minutes=5) <= at - stop <= timedelta(minutes=6) for at in starts)
    assert all(r != "track" for r, _ in writes)


# API


def _api(tmp_path: Path, library: Any, flow: Any, usable: bool = True, strategy: str = "fixed", sampled: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.profiles = library
    app.state.store = ConfigStore(tmp_path / "config", lambda: library.profiles)
    config = app.state.store.load()
    config.devices = [DeviceConfig(id="hp", ha_device_id="x", profile=PROFILE, profile_version=3, name="HPSU")]
    app.state.store.save(config)
    app.state.manifests = SimpleNamespace(usable=lambda device_id: usable, status=dict, notes=list)
    status = {"hp": {"strategy": strategy}} if sampled else {}
    app.state.runner = SimpleNamespace(heat_pump=SimpleNamespace(flow=flow, status=lambda: status), shadow=None, request_replan=lambda: None)
    return TestClient(app)


@pytest.mark.parametrize(
    ("usable", "strategy", "sampled", "regulating", "reason"),
    [
        (False, "fixed", True, False, "incompatible"),
        (True, "fixed", False, False, "no_sample"),
        (True, "weather", True, False, "strategy"),
        (True, "fixed", True, True, "esp_regulation"),
    ],
)
def test_api_refuses_switching_on(tmp_path: Path, library: Any, usable: bool, strategy: str, sampled: bool, regulating: bool, reason: str) -> None:
    flow = FlowShadow(HeatPumpStateStore(tmp_path))
    if regulating:
        flow.guard("hp").foreign.regulation_since = NOW
    client = _api(tmp_path, library, flow, usable=usable, strategy=strategy, sampled=sampled)

    answer = client.put("/api/v1/heat_pump/hp/writing", json={"active": True})

    assert answer.status_code == 409 and answer.json()["detail"] == {"reason": reason}
    assert ConfigStore(tmp_path / "config", lambda: library.profiles).load().devices[0].flow_active is False


def test_api_switches_on_and_off(tmp_path: Path, library: Any) -> None:
    client = _api(tmp_path, library, FlowShadow(HeatPumpStateStore(tmp_path)))
    store = ConfigStore(tmp_path / "config", lambda: library.profiles)

    on = client.put("/api/v1/heat_pump/hp/writing", json={"active": True})
    assert on.status_code == 200 and on.json() == {"active": True}
    assert store.load().devices[0].flow_active is True
    off = client.put("/api/v1/heat_pump/hp/writing", json={"active": False})
    assert off.json() == {"active": False} and store.load().devices[0].flow_active is False
    assert client.put("/api/v1/heat_pump/nope/writing", json={"active": True}).status_code == 404
    assert client.put("/api/v1/heat_pump/hp/writing", json={"active": "yes"}).status_code == 422
