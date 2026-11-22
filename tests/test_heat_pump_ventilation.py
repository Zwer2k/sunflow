"""Fast changes per room (design/heat_pump.md, 6.2; airing, cooking ...): events down and up in kelvin,
independent of the sensor's resolution, with sensors reporting every 15 min or in steps of 0.3/0.5 K; room
temperature and correction meanwhile, flags.ventilation in heat_pump_samples, state across a restart, the
setting house.fast_change and the status for the UI."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import pytest
from sunflow.config.models import DeviceConfig, FastChangeSettings, HouseSettings, Room, SunflowConfig
from sunflow.heat_pump.rooms import room_temperature
from sunflow.heat_pump.samples import Minute
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from sunflow.heat_pump.states import Evaluation, Reading
from sunflow.heat_pump.ventilation import Event, VentilationWatch

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
MIN = timedelta(minutes=1)
ROOM = "sensor.wohnzimmer"
OTHER = "sensor.kueche"
PROFILE = "esphome/rotex_hpsu_can"


class Sensor:
    """A room sensor that reports every `every` minutes; the state keeps last_changed of the last change."""

    def __init__(self, value: float, every: int = 15, start: datetime = NOW - timedelta(hours=3)) -> None:
        self.every, self.value, self.shown, self.changed = every, value, value, start

    def at(self, now: datetime, minute: int) -> dict[str, Any]:
        if minute % self.every == 0 and round(self.value, 1) != self.shown:
            self.shown, self.changed = round(self.value, 1), now
        return {"state": str(self.shown), "attributes": {"unit_of_measurement": "°C"}, "last_changed": self.changed.isoformat()}


def _airing(minute: int) -> float:
    """Window open 10 min from minute 30: -1 K in 10 min, back by 0.6 K in 30 min, then slowly."""
    if minute < 30:
        return 21.0
    if minute < 40:
        return 21.0 - 0.1 * (minute - 30)
    if minute < 70:
        return 20.0 + 0.02 * (minute - 40)
    return min(21.0, 20.6 + 0.001 * (minute - 70))


def _run(watch: VentilationWatch, sensor: Sensor, curve: Any, minutes: int, start: int = 0) -> list[tuple[str, ...]]:
    result = []
    for minute in range(start, start + minutes):
        now = NOW + minute * MIN
        sensor.value = curve(minute)
        result.append(watch.update([ROOM], {ROOM: sensor.at(now, minute)}, now))
    return result


@pytest.mark.parametrize("every", [1, 5, 15])
def test_airing_detected_and_over_after_the_fast_recovery(every: int) -> None:
    watch = VentilationWatch()

    aired = _run(watch, Sensor(21.0, every), _airing, 4 * 60)

    first = next(i for i, a in enumerate(aired) if a)
    last = max(i for i, a in enumerate(aired) if a)
    # Noticed at the first report with 0.8 K less, over once the room moves by at most 0.5 K in 30 min
    # (the fast part of the recovery is over), within 2 h
    assert 38 <= first <= 46
    assert 70 <= last < first + 60
    [event] = watch.recent
    assert (event.start_c, event.direction) == (21.0, "down")
    assert event.extreme_c == pytest.approx(20.0, abs=0.11)
    assert event.as_dict()["drop_k"] == pytest.approx(1.0, abs=0.11)
    assert not watch.active


def test_slow_cooling_is_no_airing() -> None:
    """0.4 K per hour (heating off on a cold night) does not count."""
    watch = VentilationWatch()

    aired = _run(watch, Sensor(21.0), lambda m: 21.0 - 0.4 * m / 60, 4 * 60)

    assert not any(aired)


def test_single_report_lower_counts_with_the_value_shown_before() -> None:
    """A sensor reporting only on a change: 21.0 for hours, then 20.1 - the 21.0 was shown within 20 min."""
    watch = VentilationWatch()
    states = {ROOM: {"state": "21.0", "last_changed": (NOW - timedelta(hours=2)).isoformat()}}
    assert watch.update([ROOM], states, NOW) == ()

    states = {ROOM: {"state": "20.1", "last_changed": (NOW + 10 * MIN).isoformat()}}

    assert watch.update([ROOM], states, NOW + 10 * MIN) == (ROOM,)


@pytest.mark.parametrize("value", ["20.7", "20.5"])
def test_one_step_of_a_coarse_sensor_is_no_fast_change(value: str) -> None:
    """One step of a sensor reporting in 0.3 or 0.5 K is below the threshold in kelvin."""
    watch = VentilationWatch()
    watch.update([ROOM], {ROOM: {"state": "21.0", "last_changed": (NOW - timedelta(hours=2)).isoformat()}}, NOW)

    assert watch.update([ROOM], {ROOM: {"state": value, "last_changed": (NOW + MIN).isoformat()}}, NOW + MIN) == ()


def _replay(watch: VentilationWatch, series: list[tuple[str, float]], until: str, settings: Any = None) -> dict[str, tuple[str, ...]]:
    """Reports of a sensor (local time of the day, value) replayed minute by minute up to until."""
    def at(hhmm: str) -> datetime:
        return NOW.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]))

    result = {}
    now, end, shown = at(series[0][0]) - timedelta(hours=1), at(until), None
    reports = [(at(t), v) for t, v in series]
    while now <= end:
        for time, value in reports:
            if time <= now:
                shown = (time, value)
        assert shown is not None or now < reports[0][0]
        state = {"state": str(shown[1]), "last_changed": shown[0].isoformat()} if shown else {"state": str(series[0][1]), "last_changed": (now - timedelta(hours=1)).isoformat()}
        result[now.strftime("%H:%M")] = watch.update([ROOM], {ROOM: state}, now, settings)
        now += MIN
    return result


# Reports of real sensors (0.3 K steps): a room aired 20 min, a kitchen while cooking
AIRED_0_3 = [("09:00", 22.7), ("09:12", 22.4), ("09:13", 22.1), ("09:14", 21.8), ("09:15", 21.5), ("09:16", 21.2),
             ("09:18", 20.9), ("09:19", 20.6), ("09:22", 20.3), ("09:24", 20.0), ("09:27", 19.7), ("09:30", 19.4),
             ("09:33", 19.1), ("09:38", 19.4), ("09:40", 19.7), ("09:43", 20.0), ("09:48", 20.3), ("09:54", 20.6),
             ("09:59", 20.9), ("10:10", 21.2), ("10:20", 21.5), ("10:42", 21.8), ("11:19", 22.1)]
COOKING_0_3 = [("16:00", 24.7), ("17:22", 25.0), ("17:30", 25.3), ("17:32", 25.6), ("17:35", 25.9), ("17:38", 26.2),
               ("17:45", 26.5), ("17:50", 26.2), ("18:23", 25.9)]


def test_airing_of_a_coarse_sensor_ends_with_the_fast_part_of_the_recovery() -> None:
    watch = VentilationWatch()

    aired = _replay(watch, AIRED_0_3, "12:00")

    first = min(t for t, a in aired.items() if a)
    last = max(t for t, a in aired.items() if a)
    # Three steps of 0.3 K: 0.9 K below; over once the room recovers by at most 0.5 K in 30 min
    assert first == "09:14"
    # The window covers 21.2 → 21.5 then; 1.2 K below the start the slow rest counts again
    assert "10:35" <= last <= "10:45"
    [event] = watch.recent
    assert (event.start_c, event.extreme_c, event.direction) == (22.7, 19.1, "down")


def test_cooking_is_a_fast_rise_and_the_cooling_after_it_belongs_to_it() -> None:
    watch = VentilationWatch()

    changed = _replay(watch, COOKING_0_3, "20:00")

    first = min(t for t, a in changed.items() if a)
    last = max(t for t, a in changed.items() if a)
    assert first == "17:32"
    # Around the peak the room moved by 0.3 K in 30 min: the fast part is over, the warm kitchen counts
    assert "18:00" <= last <= "18:10"
    # The fall of 0.6 K after the peak is the slow cooling: no further event
    [event] = watch.recent
    assert (event.direction, event.start_c, event.extreme_c) == ("up", 24.7, 26.5)
    data = event.as_dict()
    assert data["change_k"] == pytest.approx(1.8) and "low_c" not in data and "drop_k" not in data


def test_slow_cooling_after_cooking_is_no_fast_change_alone() -> None:
    """The kitchen cooling by one 0.3 K step after a quiet spell (detection starting late) counts."""
    watch = VentilationWatch()

    changed = _replay(watch, [("17:00", 26.2), ("18:23", 25.9)], "19:30")

    assert not any(changed.values())


def test_thresholds_from_the_setting() -> None:
    strict = FastChangeSettings(start_k=1.5, start_min=20, settle_k=0.5, settle_min=30, max_h=2)
    watch = VentilationWatch()

    _run_set = _replay(watch, [("09:00", 21.0), ("09:05", 20.0)], "09:30", strict)
    assert not any(_run_set.values())

    loose = FastChangeSettings(start_k=0.5, settle_min=10, max_h=0.5)
    watch = VentilationWatch()
    changed = _replay(watch, [("09:00", 21.0), ("09:05", 20.4), ("09:06", 20.0)], "10:00", loose)
    assert min(t for t, a in changed.items() if a) == "09:05"
    # Over after 10 min without a change, at the latest after half an hour
    assert max(t for t, a in changed.items() if a) == "09:14"


def test_event_from_an_older_state_file() -> None:
    """0.80.19 and before: falls only, with low_c."""
    event = Event.from_dict({"entity_id": ROOM, "since": NOW.isoformat(), "until": None, "start_c": 22.7, "low_c": 19.1, "drop_k": 3.6})

    assert event is not None and (event.direction, event.extreme_c, event.change_k) == ("down", 19.1, pytest.approx(-3.6))


def test_noise_of_one_step_is_no_airing() -> None:
    watch = VentilationWatch()

    aired = _run(watch, Sensor(21.0), lambda m: 21.0 if (m // 15) % 2 else 20.9, 4 * 60)

    assert not any(aired)


def test_over_after_two_hours_at_the_latest_and_not_noticed_again_by_the_same_drop() -> None:
    """Window left open: the room keeps falling after the first drop; over after 2 h."""
    watch = VentilationWatch()

    aired = _run(watch, Sensor(21.0, 1), lambda m: 21.0 if m < 10 else max(17.0, 20.0 - 0.02 * (m - 10)), 4 * 60)

    first = next(i for i, a in enumerate(aired) if a)
    assert first == 10
    assert not aired[first + 120]
    assert not any(aired[first + 120:])


def test_unknown_room_history_after_a_restart_does_not_end_at_once() -> None:
    """An active event survives a restart; it ends only once the room is known flat for 30 min."""
    watch = VentilationWatch()
    _run(watch, Sensor(21.0, 1), _airing, 45)
    assert watch.active
    stored = json.loads(json.dumps(watch.as_dict()))

    again = VentilationWatch(stored)
    state = {ROOM: {"state": "20.3", "last_changed": (NOW + 50 * MIN).isoformat()}}

    assert again.update([ROOM], state, NOW + 50 * MIN) == (ROOM,)
    assert again.update([ROOM], state, NOW + 79 * MIN) == (ROOM,)
    assert again.update([ROOM], state, NOW + 80 * MIN) == ()


def test_removed_room_forgotten() -> None:
    watch = VentilationWatch()
    _run(watch, Sensor(21.0, 1), _airing, 45)

    assert watch.update([OTHER], {}, NOW + 46 * MIN) == ()
    assert not watch.active


def test_unreadable_state_ignored() -> None:
    assert VentilationWatch({"active": [{"entity_id": ROOM}], "recent": "x"}).active == {}
    assert VentilationWatch([1, 2]).active == {}


def test_aired_room_not_in_the_room_temperature() -> None:
    state = {"state": "19.0", "attributes": {"unit_of_measurement": "°C"}}
    rooms = [Room(entity_id=ROOM), Room(entity_id=OTHER)]

    room = room_temperature(rooms, {ROOM: state, OTHER: state | {"state": "21.0"}}, {}, NOW, ventilated={ROOM})

    assert room.value_c == 21.0
    assert (room.used, room.ventilated) == ((OTHER,), (ROOM,))


# Shadow control: room temperature and correction while a room is aired


def _minute(at: datetime) -> Minute:
    reading = Reading(program="heating", activity="heating", strategy="fixed", values={"flow_setpoint": 31.0})
    return Minute(at=at, reading=reading, evaluation=Evaluation(valid=True), heat_w=None, heat_source=None, outside_c=None, rooms={})


class House:
    """Two rooms, one heat pump, the shadow control."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.device = DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=2, name="HPSU CAN")
        self.config = SunflowConfig(devices=[self.device], house=HouseSettings(rooms=[Room(entity_id=ROOM), Room(entity_id=OTHER)]))
        self.sensors = {ROOM: Sensor(21.0), OTHER: Sensor(20.6)}
        self.shadow = FlowShadow(HeatPumpStateStore(tmp_path))

    async def run(self, curve: Any, minutes: int, start: int = 0) -> list[dict[str, Any]]:
        flows = []
        for minute in range(start, start + minutes):
            now = NOW + minute * MIN
            self.sensors[ROOM].value = curve(minute)
            states = {e: s.at(now, minute) for e, s in self.sensors.items()}
            await self.shadow.tick(self.config, states, {"hp": (self.device, _minute(now))}, now)
            flows.append(self.shadow.flow_status("hp"))
        return flows


async def test_correction_not_driven_by_airing(tmp_path: Path) -> None:
    """Room temperature 0.2 K below the setpoint (deadband): airing one room must not raise the correction."""
    house = House(tmp_path)

    flows = await house.run(_airing, 4 * 60)

    aired = [f for f in flows if "ventilation" in f["frozen"]]
    assert aired
    # P part: airing would have raised it by ~0.5 K; while aired nothing changes, after it the room
    # comes back without a jump and the slow rest of the recovery counts (the integral moves a little)
    corrections = [f["correction_k"] for f in flows]
    assert max(abs(c) for c in corrections) < 0.15
    back = max(i for i, f in enumerate(flows) if "ventilation" in f["frozen"]) + 1
    assert corrections[back] == corrections[back - 1]
    status = house.shadow.house_status()
    assert status["rooms_ventilated"] == []
    assert [e["entity_id"] for e in status["ventilation_recent"]] == [ROOM]
    assert status["ventilation_recent"][0]["drop_k"] == pytest.approx(1.0, abs=0.11)


async def test_aired_room_left_out_of_room_temperature_and_status(tmp_path: Path) -> None:
    house = House(tmp_path)

    await house.run(_airing, 50)

    status = house.shadow.house_status()
    assert status["rooms_ventilated"] == [ROOM]
    assert status["rooms_used"] == [OTHER]
    assert status["room_c"] == 20.6
    assert status["ventilation"][0]["entity_id"] == ROOM
    assert house.shadow.flow_status("hp")["frozen"] == ["ventilation"]


async def test_airing_survives_a_restart(tmp_path: Path) -> None:
    house = House(tmp_path)
    await house.run(_airing, 50)

    house.shadow = FlowShadow(HeatPumpStateStore(tmp_path))
    flows = await house.run(_airing, 1, start=50)

    assert flows[0]["frozen"] == ["ventilation"]
    stored = json.loads((tmp_path / "heat_pump_state.json").read_text())
    assert stored["house"]["ventilation"]["active"][0]["entity_id"] == ROOM


async def test_room_leaving_and_coming_back_causes_no_jump(tmp_path: Path) -> None:
    """A room going stale (no aired room) changes the room temperature by 1 K: no step of the P part."""
    house = House(tmp_path)
    house.sensors[OTHER] = Sensor(22.0)
    await house.run(lambda _: 20.0, 20)
    assert house.shadow.house_status()["room_c"] == 21.0

    now = NOW + 20 * MIN
    states = {ROOM: house.sensors[ROOM].at(now, 20), OTHER: {"state": "unavailable"}}
    await house.shadow.tick(house.config, states, {"hp": (house.device, _minute(now))}, now)

    # The room temperature falls by 1 K only because a room left: without the new reference the
    # P part would raise the correction by 1 K
    assert house.shadow.house_status()["room_c"] == 20.0
    assert house.shadow.flow_status("hp")["correction_k"] == 0.0


# flags.ventilation in heat_pump_samples


async def test_ventilation_flag_in_the_samples(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    sensor = Sensor(21.0, 1)
    try:
        for minute in range(60):
            now = NOW + minute * MIN
            sensor.value = _airing(minute)
            pump.extra[ROOM] = sensor.at(now, minute)
            await pump.tick(now)
        rows = pump.store.heat_pump_samples("hp", NOW - timedelta(hours=1))
    finally:
        pump.store.close()

    flags = {row["start"]: row["flags"]["ventilation"] for row in rows}
    assert flags[NOW.isoformat()] == []
    assert flags[(NOW + 35 * MIN).isoformat()] == [ROOM]
    assert flags[(NOW + 50 * MIN).isoformat()] == [ROOM]
    # Not counted as invalid heating: the heating curve leaves the interval out by this flag
    assert all(row["flags"]["valid"] for row in rows)


# Setting house.fast_change


def test_fast_change_setting_defaults_and_broken_values() -> None:
    assert HouseSettings().fast_change == FastChangeSettings(start_k=0.8, start_min=20, settle_k=0.5, settle_min=30, max_h=2.0)
    stored = HouseSettings.model_validate({"fast_change": {"start_k": "x", "start_min": 2.5, "settle_k": 0.3, "settle_min": 999, "max_h": True}})

    assert stored.fast_change.model_dump() == {"start_k": 0.8, "start_min": 20, "settle_k": 0.3, "settle_min": 30, "max_h": 2.0}
    assert HouseSettings.model_validate({"fast_change": "x"}).fast_change == FastChangeSettings()
    assert HouseSettings.model_validate({"fast_change": None}).fast_change == FastChangeSettings()
