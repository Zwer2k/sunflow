"""Limits and protection layer (design/heat_pump.md, 10, 11), with synthetic runs of a
heat pump: restart after the compressor stayed off, lowering after a start, tracking with the learned stop
margin, the learning phase, block on and off; learned swing, stop margin and heat output range; daily counts."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
from heat_pump_node import joined
import pytest
from sunflow.api.heat_pump import router
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.heat_pump.correction import Correction
from sunflow.heat_pump.defaults import STOP_MIN_EVENTS, STOP_MIN_EVENTS_ABOVE, SWING_MIN_ROWS
from sunflow.heat_pump.guard import Guard, GuardInput, GuardResult, daily_counts
from sunflow.heat_pump.learned import Learned, classify_stops, learn, values
from sunflow.heat_pump.limits import ControlSettings, clamp, round_up, setpoint_range
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from sunflow.heat_pump.states import Evaluation, Reading
from sunflow.heat_pump.writer import Writer, write_step

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
MIN = timedelta(minutes=1)
FIVE = timedelta(minutes=5)
PROFILE = "esphome/rotex_hpsu_can"


class Sim:
    """A heat pump minute by minute as the protection layer sees it; the guard decides what it would write."""

    def __init__(self, written: float = 30.0, learned: Learned | None = None, **settings: Any) -> None:
        self.guard = Guard()
        self.writer = Writer()
        self.active = False
        self.budget: Any = None
        self.settings = ControlSettings(step=0.1, **settings)
        self.learned = learned
        self.learn_stops = False
        self.now = NOW
        self.real_setpoint = written
        self.effective = written
        self.target: float | None = written
        self.room: float | None = 21.0
        self.plan = 21.0
        self.heat: float | None = None
        self.surface = "underfloor"
        self.mode = "heat"
        # Further role values of the reading (e.g. regulated_setpoint)
        self.extra: dict[str, float | None] = {}
        self.results: list[tuple[datetime, float, GuardResult]] = []

    def minute(
        self, tv: float, compressor: bool = True, activity: str = "heating", valid: bool | None = None,
        bypass: float = 0.0, peak: float | None = None, blocked: bool = False, strategy: str = "fixed",
    ) -> GuardResult:
        reading = Reading(
            program="heating", activity=activity, strategy=strategy, blocked=blocked, error="err_0", compressor=compressor,
            values={"flow_temperature": tv, "flow_setpoint": self.effective, "max_flow_setpoint": self.real_setpoint, "bypass_valve": bypass} | self.extra,
        )
        if valid is None:
            valid = activity == "heating" and compressor and bypass == 0
        evaluation = Evaluation(valid=valid, frozen=() if valid else ("activity_idle",))
        result = write_step(self.guard, self.writer, GuardInput(
            at=self.now, reading=reading, evaluation=evaluation, settings=self.settings,
            values=values(self.settings, self.learned), tv_peak_c=peak, heat_w=self.heat,
            target_c=self.target, room_c=self.room, plan_c=self.plan, mode=self.mode,
            surface=self.surface, learn_stops=self.learn_stops,
        ), active=self.active, budget=self.budget)
        self.results.append((self.now, max(tv, peak or tv), result))
        self.now += MIN
        return result

    def run(self, tvs: list[float], **kwargs: Any) -> list[GuardResult]:
        return [self.minute(tv, **kwargs) for tv in tvs]

    def off(self, minutes: int, tv: float = 27.0, **kwargs: Any) -> list[GuardResult]:
        """The compressor off in heating (pause)."""
        return self.run([tv] * minutes, compressor=False, activity="idle", **kwargs)

    def writes(self) -> list[tuple[str, float | bool | None]]:
        return [(w["reason"], w.get("value_c", w.get("block"))) for _, _, r in self.results for w in r.writes]

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [e for _, _, r in self.results for e in r.events if e["kind"] == kind]

    def notes(self) -> list[str]:
        return [m for _, _, r in self.results for m, _ in r.notes]


# Restart: the compressor stays off although heat is wanted


def test_restart_after_five_minutes_off() -> None:
    """Whatever stopped it: after 5 minutes off Sunflow raises the set value to Tv + start margin, once."""
    sim = Sim(written=28.0)
    sim.run([30.0] * 3)
    waiting = sim.off(5, tv=27.0)
    restart = sim.minute(26.8, compressor=False, activity="idle")
    later = sim.off(6, tv=26.5)

    assert [r.phase for r in waiting] == ["off"] * 5 and all(not r.writes for r in waiting)
    assert waiting[0].restart_at == waiting[0].off_since + FIVE
    assert restart.phase == "restart"
    # 26.8 + 3 K, rounded up; Tv falling further asks for less: no second write
    assert sim.writes() == [("start", 29.8)]
    assert all(not r.writes for r in later)


def test_restart_raises_above_the_written_value() -> None:
    sim = Sim(written=28.0)
    sim.off(5, tv=27.0)
    sim.minute(27.0, compressor=False, activity="idle")

    assert sim.writes() == [("start", 30.0)]


def test_no_restart_while_the_room_is_warm() -> None:
    """Room at its target + 0.5 K (no block input): off is wanted, nothing raised."""
    sim = Sim(written=28.0, has_block=False)
    sim.room = 21.5
    results = sim.off(12, tv=27.0)

    assert sim.writes() == [] and {r.phase for r in results} == {"idle"}
    # The room cools: restart at once, the compressor has long been off
    sim.room = 21.2
    sim.minute(27.0, compressor=False, activity="idle")
    assert sim.writes() == [("start", 30.0)]


def test_hot_water_and_defrost_start_the_wait_anew() -> None:
    """Nothing written during hot water or defrost; the 5 minutes count from their end."""
    sim = Sim(written=28.0)
    sim.run([45.0] * 15, compressor=True, activity="hot_water", bypass=100.0)
    sim.run([25.0] * 5, compressor=False, activity="defrost")
    assert sim.writes() == []
    waiting = sim.off(5, tv=27.0)
    sim.minute(27.0, compressor=False, activity="idle")

    assert all(r.phase == "off" for r in waiting)
    assert sim.writes() == [("start", 30.0)]


def test_user_maximum_caps_the_restart() -> None:
    sim = Sim(written=28.0, user_max=29.5)
    sim.off(6, tv=27.0)

    assert sim.writes() == [("start", 29.5)]
    assert sim.results[-1][2].limits == (25.0, 29.5)


# Lowering after a start, tracking, learning phase


def test_lowered_once_after_the_start() -> None:
    """After a restart the set value stays for 10 minutes, then goes to the flow target in one step."""
    sim = Sim(written=28.0)
    sim.target = 29.0
    sim.off(6, tv=27.0)
    sim.real_setpoint = sim.effective = 30.0
    starting = sim.run([29.0, 30.5, 31.0, 30.8, 30.6, 30.4, 30.3, 30.2, 30.1, 30.0])
    after = sim.run([30.0] * 3)

    assert {r.phase for r in starting} == {"starting"}
    assert sim.writes() == [("start", 30.0), ("normal", 29.0)]
    assert after[0].phase == "running"


def test_lowering_keeps_above_the_shutdown_bound() -> None:
    """Not below peak − stop margin + swing: Tv 33.5 keeps the set value at 31.0, not at the target 29."""
    sim = Sim(written=35.0)
    sim.target = 29.0
    sim.run([33.5] * 12)

    assert sim.writes() == [("normal", 31.0)]


def test_learning_phase_lowers_straight_to_the_target() -> None:
    """Learning the stop margin: down to the flow target although Tv is far above it, no tracking."""
    sim = Sim(written=35.0)
    sim.learn_stops = True
    sim.target = 29.0
    sim.run([33.5] * 12)
    sim.run([round(33.5 + 0.2 * i, 1) for i in range(20)])

    assert sim.writes() == [("normal", 29.0)]
    assert sim.results[-1][2].floor_c is None


def test_tracking_a_rising_tv_with_headroom() -> None:
    sim = Sim(written=30.0)
    tvs = [round(30.0 + 0.1 * i, 1) for i in range(60)]
    sim.run(tvs)

    assert {reason for reason, _ in sim.writes()} == {"track"}
    # A swing above the bound: not a write every few minutes
    assert 5 <= len(sim.writes()) <= 7
    for at, tv, result in sim.results:
        assert result.setpoint_c is None or tv < result.setpoint_c + 3.0


def test_learned_stop_margin_counts() -> None:
    learned = Learned(computed_at=NOW, stop_margin=2.0, stop_samples=6)
    sim = Sim(written=35.0, learned=learned)
    sim.target = 29.0
    sim.run([33.5] * 12)

    # 33.5 − 2.0 + 0.5
    assert sim.writes() == [("normal", 32.0)]


def test_normal_writes_only_in_valid_heating() -> None:
    sim = Sim(written=35.0)
    sim.target = 29.0
    sim.learn_stops = True
    sim.run([30.0] * 12, valid=False)

    assert sim.writes() == []


# Block (11)


def test_block_on_and_off_with_minimum_duration() -> None:
    sim = Sim(written=30.0)
    sim.room = 21.6
    blocked = sim.minute(30.0)
    sim.room = 21.1
    during = sim.run([30.0] * 19)
    released = sim.minute(30.0)
    after = sim.minute(27.0, compressor=False, activity="idle")

    assert blocked.phase == "blocked" and all(r.phase == "blocked" and not r.writes for r in during)
    assert released.writes[0]["reason"] == "block_off"
    assert sim.writes() == [("block_on", True), ("block_off", False)]
    assert after.phase == "off"
    notes = sim.notes()
    assert notes[0].startswith("Nur Lernen: Sunflow würde die Raumthermostat-Sperre setzen (Raum 21,6 °C, 0,6 K über dem Ziel")
    assert notes[1].startswith("Nur Lernen: Sunflow würde die Raumthermostat-Sperre aufheben")


def test_block_found_on_is_kept_and_shown() -> None:
    """A block Sunflow did not set is not released, only shown (11); nothing written while it lasts."""
    sim = Sim(written=30.0)
    sim.room = 20.0
    found = sim.off(30, blocked=True)

    assert sim.writes() == []
    assert all(r.phase == "blocked" for r in found)
    assert (sim.guard.block, sim.guard.block_reason) == (True, "foreign")
    notes = [(m, warn) for _, _, r in sim.results for m, warn in r.notes]
    assert notes == [("Raumthermostat-Sperre nicht von Sunflow gesetzt: Sunflow hebt sie nicht auf und regelt nicht, bis sie aufgehoben ist.", True)]


def test_block_set_by_someone_else_later_is_not_released() -> None:
    """Set by hand while Sunflow controls (also over its own virtual block): kept until released outside."""
    sim = Sim(written=28.0)
    sim.room = 21.6
    sim.minute(30.0)
    assert sim.guard.block_reason == "room_warm"
    sim.room = 20.0
    during = sim.off(40, blocked=True)

    assert sim.writes() == [("block_on", True)]
    assert all(r.phase == "blocked" for r in during) and sim.guard.block_reason == "foreign"
    # Released outside: the wait for a restart starts there
    sim.off(5)
    assert (sim.guard.block, sim.guard.block_reason) == (False, None)
    assert sim.writes() == [("block_on", True)]
    sim.off(1)
    assert sim.writes()[-1] == ("start", 30.0)


def test_released_foreign_block_counts_for_the_minimum_duration() -> None:
    """After someone else released it, Sunflow sets its own block only after the minimum duration."""
    sim = Sim(written=30.0)
    sim.minute(30.0, blocked=True)
    sim.room = 21.6
    # Released in the first of these minutes; 20 minutes from there
    early = sim.run([30.0] * 20)
    late = sim.minute(30.0)

    assert all(not any(w["reason"] == "block_on" for w in r.writes) for r in early)
    assert late.writes[0]["reason"] == "block_on" and sim.guard.block_reason == "room_warm"


@pytest.mark.parametrize("old", ["adopted", "min_power", "budget"])
def test_block_reasons_of_older_versions(old: str) -> None:
    """"adopted" is read as foreign; blocks of the old rules become the room rule's."""
    guard = Guard({"block": True, "block_at": NOW.isoformat(), "block_reason": old})

    assert guard.block_reason == ("foreign" if old == "adopted" else "room_warm")


def test_no_block_without_the_input() -> None:
    sim = Sim(written=30.0, has_block=False)
    sim.room = 23.0
    sim.minute(30.0)

    assert sim.writes() == []


# Limits (10)


@pytest.mark.parametrize(("surface", "high"), [("underfloor", 40.0), ("mixed", 50.0), ("radiator", 55.0)])
def test_set_value_range_by_heating_surface(surface: str, high: float) -> None:
    sim = Sim(written=high - 2)
    sim.surface = surface
    sim.target = high + 5
    sim.effective = sim.real_setpoint = high - 2
    sim.run([high - 3] * 7)

    result = sim.results[-1][2]
    assert result.limits[1] == high
    assert result.desired_c == high
    assert "limit_max" in result.limited and result.freeze_up and not result.freeze_down


def test_entity_range_wins() -> None:
    settings = ControlSettings(entity_min=27.0, entity_max=38.0)

    assert setpoint_range("underfloor", settings) == (27.0, 38.0)
    assert setpoint_range("unknown", ControlSettings()) == (25.0, 40.0)
    assert clamp(45.0, 27.0, 38.0, 0.1) == 38.0
    assert round_up(30.01, 0.1) == 30.1 and round_up(30.0, 0.5) == 30.0 and round_up(30.1, 0.5) == 30.5


def test_user_limits_within_the_entity_range() -> None:
    """The user's range replaces the heating surface type's start limit; the entity's range still narrows it."""
    assert setpoint_range("underfloor", ControlSettings(user_min=27.0, user_max=35.0)) == (27.0, 35.0)
    # Only one side set: the other from the heating surface type
    assert setpoint_range("radiator", ControlSettings(user_max=45.0)) == (30.0, 45.0)
    assert setpoint_range("underfloor", ControlSettings(user_min=28.0, user_max=60.0, entity_min=20.0, entity_max=45.0)) == (28.0, 45.0)
    # A maximum below the type's minimum (heating surface type changed later): the maximum protects, it wins
    assert setpoint_range("radiator", ControlSettings(user_max=28.0)) == (28.0, 28.0)


def test_power_limits_freeze_the_correction_in_their_direction() -> None:
    sim = Sim(written=30.0)
    sim.heat = 5900.0
    top = sim.minute(30.0)
    sim.heat = 600.0
    bottom = sim.minute(30.0)

    assert ("power_max" in top.limited, top.freeze_up, top.freeze_down) == (True, True, False)
    assert ("power_min" in bottom.limited, bottom.freeze_up, bottom.freeze_down) == (True, False, True)


def test_correction_does_not_move_beyond_a_limit() -> None:
    correction = Correction()
    correction.step(20.0, 21.0, NOW)

    assert not correction.step(20.0, 21.0, NOW + timedelta(minutes=30), block_up=True)
    assert correction.value_k == 0.0
    # Away from the limit it counts
    assert correction.step(19.5, 21.0, NOW + timedelta(minutes=31), block_down=True)
    assert correction.value_k == pytest.approx(0.5, abs=0.01)


@pytest.mark.parametrize(
    ("change", "reason"),
    [({"mode": "off"}, "mode_off"), ({"strategy": "weather"}, "strategy_weather")],
)
def test_inactive_writes_nothing(change: dict[str, str], reason: str) -> None:
    sim = Sim(written=28.0)
    sim.mode = change.get("mode", "heat")
    results = sim.off(8, strategy=change.get("strategy", "fixed"))

    assert reason in results[-1].inactive and sim.writes() == []


# Learned values (10, 11)


def _steady_rows(count: int, swing: float = 0.3, heat: float = 2000.0) -> list[dict[str, Any]]:
    return [
        {"start": (NOW - (count - i) * FIVE).isoformat(), "samples": 5, "tv_c": 30.0, "tv_max_c": 30.0 + swing * (i % 3) / 2,
         "heat_w": heat + 10 * (i % 50), "compressor": 1.0, "activity": "heating", "flags": {"valid": True}, "events": []}
        for i in range(count)
    ]


def _stop(at: datetime, margin: float, **extra: Any) -> dict[str, Any]:
    return {"at": at.isoformat(), "kind": "stop", "tv_c": 30.0 + margin - 0.1, "tv_max_c": 30.0 + margin, "setpoint_c": 30.0,
            "max_setpoint_c": 30.0, "tvbh_c": 30.0 + margin, "tv_uart_c": 30.0 + margin - 0.3, "activity": "heating", "blocked": False} | extra


def _with_stops(rows: list[dict[str, Any]], stops: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Each stop into the row it falls in (index from the end)."""
    for back, event in stops:
        rows[-back]["events"].append(event)
    return rows


def test_stops_are_classified() -> None:
    rows = _steady_rows(40)
    at = lambda back: datetime.fromisoformat(rows[-back]["start"]) + timedelta(minutes=2)  # noqa: E731
    rows[-28]["activity"] = "defrost"
    rows[-18]["activity"] = "hot_water"
    _with_stops(rows, [
        (30, _stop(at(30), 2.5)),
        (20, _stop(at(20), 1.0)),
        (12, _stop(at(12), 2.2, blocked=True)),
        (10, _stop(at(10), 2.4, activity="hot_water")),
        (8, _stop(at(8), 2.1)),
        (1, _stop(at(1), 2.0)),
    ])

    stops = classify_stops(rows, NOW)

    assert [s["kind"] for s in stops] == ["defrost", "hot_water", "block", "other", "unexplained", "pending"]
    assert (stops[4]["margin_k"], stops[4]["margin_uart_k"]) == (2.1, pytest.approx(1.8))


def test_stop_margin_learned_from_unexplained_stops() -> None:
    rows = _steady_rows(SWING_MIN_ROWS + 10)
    margins = [2.4, 2.1, 2.6, 2.2, 2.3, 0.2, 9.0]
    _with_stops(rows, [(20 + 3 * i, _stop(datetime.fromisoformat(rows[-(20 + 3 * i)]["start"]) + MIN, m)) for i, m in enumerate(margins)])

    learned = learn(rows, NOW, start_stop_margin=3.0)

    # Margins outside 0.5–8 K do not count; the low quantile of the others
    assert (learned.stop_samples, learned.stop_margin) == (5, 2.1)
    assert learned.stop_counts["unexplained"] == 7
    assert learned.stops[0]["at"] > learned.stops[-1]["at"]
    used = values(ControlSettings(), learned)
    assert (used.stop_margin, used.sources["stop_margin"], used.steady_swing) == (2.1, "learned", pytest.approx(0.3))
    assert Learned.from_dict(learned.as_dict()) == learned
    assert len(margins) - 2 >= STOP_MIN_EVENTS


def test_a_larger_stop_margin_needs_more_stops() -> None:
    """The safe direction first: above the profile's value only from STOP_MIN_EVENTS_ABOVE stops."""
    rows = _steady_rows(80)
    few = [(10 + 3 * i, _stop(datetime.fromisoformat(rows[-(10 + 3 * i)]["start"]) + MIN, 3.5)) for i in range(STOP_MIN_EVENTS)]

    assert learn(_with_stops(rows, few), NOW, start_stop_margin=3.0).stop_margin is None
    rows = _steady_rows(80)
    many = [(10 + 3 * i, _stop(datetime.fromisoformat(rows[-(10 + 3 * i)]["start"]) + MIN, 3.5)) for i in range(STOP_MIN_EVENTS_ABOVE)]
    assert learn(_with_stops(rows, many), NOW, start_stop_margin=3.0).stop_margin == 3.5


def test_too_little_data_keeps_the_start_values() -> None:
    learned = learn(_steady_rows(20), NOW)

    used = values(ControlSettings(), learned)
    assert (learned.steady_swing, learned.power_min_w, learned.stop_margin) == (None, None, None)
    assert (used.steady_swing, used.start_margin, used.stop_margin, used.power_min_w) == (0.5, 3.0, 3.0, 600)
    assert used.sources["stop_margin"] == "start"


def test_settings_from_the_profile_and_manifest(library: Any) -> None:
    settings = ControlSettings.from_profile(library.profiles[PROFILE], joined())

    assert (settings.step, settings.entity_min, settings.entity_max) == (0.1, 20.0, 90.0)
    assert (settings.start_margin, settings.stop_margin, settings.steady_swing) == (3.0, 3.0, 0.5)
    assert (settings.power_min_w, settings.power_max_w, settings.block_min_s, settings.has_block) == (600, 6000, 1200, True)
    # Without a manifest: the coarsest allowed step, rounding stays safe
    assert ControlSettings.from_profile(library.profiles[PROFILE], None).step == 0.5


# Daily counts


def test_daily_counts_in_local_time() -> None:
    tz = ZoneInfo("Europe/Berlin")
    events = [
        {"at": "2026-11-21T10:00:00+00:00", "kind": "write", "reason": "start"},
        {"at": "2026-11-21T10:00:00+00:00", "kind": "write", "reason": "start"},
        {"at": "2026-11-21T11:00:00+00:00", "kind": "write", "reason": "normal"},
        {"at": "2026-11-21T11:30:00+00:00", "kind": "write", "reason": "block_on"},
        {"at": "2026-11-20T23:30:00+00:00", "kind": "write", "reason": "track"},
        {"at": "2026-11-20T22:30:00+00:00", "kind": "stop", "activity": "heating"},
        {"at": "2026-11-20T22:40:00+00:00", "kind": "stop", "activity": "hot_water"},
        {"at": "2026-11-21T09:00:00+00:00", "kind": "flow_source"},
    ]

    today, yesterday = daily_counts(events, tz, 2, NOW)

    assert (today["date"], today["start"], today["normal"], today["track"], today["block_on"]) == ("2026-11-21", 1, 1, 1, 1)
    assert (today["setpoint"], today["protect"], today["block"], today["stops"]) == (3, 2, 1, 0)
    assert (yesterday["date"], yesterday["stops"], yesterday["setpoint"]) == ("2026-11-20", 1, 0)


# Through the recorder: events in heat_pump_samples, journal, status, counts


async def test_recorder_notes_planned_writes(tmp_path: Path, library: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return (NOW + 20 * MIN).astimezone(tz)

    # The counts per day run up to "today": the API's clock at the time of the run, not the real one
    monkeypatch.setattr("sunflow.api.heat_pump.datetime", Clock)
    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    pump.states["max_target_flow_temp"] = pump.states["target_supply_temperature"] = ("29.0", "°C")
    # Heating curve 21 + 0.5·(21 − 5) = 29: nothing to change while running
    pump.states["heating_curve"] = ("0.5", None)
    try:
        await pump.minutes(NOW, 3)
        # Pause: compressor off, Tv down; after 5 minutes Sunflow would restart it
        pump.event("status_kompressor", "on", "off", NOW + 3 * MIN - timedelta(seconds=20))
        pump.states["status_kompressor"] = ("off", None)
        pump.states["mode_of_operating"] = ("Bereitschaft", None)
        pump.states["tv"] = ("28.0", "°C")
        await pump.minutes(NOW + 3 * MIN, 7)
        # The room well above the setpoint: the block
        pump.extra["sensor.wohnzimmer"] = pump._state("21.6", "°C")
        await pump.minutes(NOW + 10 * MIN, 6)
        rows = pump.rows()
        status = pump.recorder.status()["hp"]["flow"]["guard"]
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.state.profiles = library
        app.state.store = ConfigStore(tmp_path / "config", lambda: library.profiles)
        config = app.state.store.load()
        config.devices = pump.config.devices
        app.state.store.save(config)

        class Ha:
            async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
                return {"time_zone": "Europe/Berlin"}

        app.state.ha = Ha()
        app.state.runner = SimpleNamespace(heat_pump=pump.recorder, shadow=pump.store)
        answer = TestClient(app).get("/api/v1/heat_pump/hp/shadow_writes", params={"days": 3}).json()
    finally:
        pump.store.close()

    events = [e for row in rows for e in row["events"]]
    writes = [e for e in events if e["kind"] == "write"]
    assert [(w["reason"], w.get("value_c")) for w in writes] == [("start", 31.0), ("block_on", None)]
    assert all(w["shadow"] for w in writes)
    [stop] = [e for e in events if e["kind"] == "stop"]
    assert (stop["activity"], stop["blocked"], stop["tvbh_c"], stop["tv_uart_c"]) == ("heating", False, 30.2, None)
    assert (status["shadow"], status["block"], status["block_reason"], status["setpoint_c"]) == (True, True, "room_warm", 31.0)
    assert status["values"]["stop_margin"] == 3.0 and status["learn_stops"] is True
    assert any(n.startswith("Nur Lernen: Sunflow würde die Raumthermostat-Sperre setzen") for n in pump.notes())
    today = answer["days"][0]
    assert (today["start"], today["block_on"], today["setpoint"], today["stops"]) == (1, 1, 1, 1)
    assert answer["budget"] == {"per_day": 48, "emergency_per_day": 24, "per_hour": 6}
    # Nothing written to the heat pump: Max T-Vorlauf as it was
    assert rows[-1]["written_c"] is None


def _limits_api(tmp_path: Path, library: Any, recorder: Any = None) -> tuple[TestClient, ConfigStore]:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.profiles = library
    app.state.store = ConfigStore(tmp_path / "config", lambda: library.profiles)
    config = app.state.store.load()
    config.devices = []
    app.state.store.save(config)
    app.state.runner = SimpleNamespace(heat_pump=recorder, shadow=None)
    return TestClient(app), app.state.store


async def test_flow_limits_setting(tmp_path: Path, library: Any) -> None:
    """PUT /heat_pump/<id>/flow_limits stores the range; the status shows it with defaults and the entity range."""
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    try:
        await pump.minutes(NOW, 1)
        client, store = _limits_api(tmp_path, library, pump.recorder)
        config = store.load()
        config.devices = pump.config.devices
        store.save(config)

        answer = client.put("/api/v1/heat_pump/hp/flow_limits", json={"min_c": 26.04, "max_c": 35.0}).json()
        status = client.get("/api/v1/heat_pump/status").json()["devices"][0]["flow_limits"]
        refused = [
            client.put("/api/v1/heat_pump/hp/flow_limits", json=body).status_code
            for body in ({"min_c": 35.0, "max_c": 30.0}, {"max_c": 24.0}, {"min_c": 41.0}, {"max_c": 80.0}, {"min_c": 30, "x": 1})
        ]
        missing = client.put("/api/v1/heat_pump/nope/flow_limits", json={}).status_code
        cleared = client.put("/api/v1/heat_pump/hp/flow_limits", json={"min_c": None, "max_c": None}).json()
    finally:
        pump.store.close()

    assert (answer["min_c"], answer["max_c"], answer["effective_min_c"], answer["effective_max_c"]) == (26.0, 35.0, 26.0, 35.0)
    assert (status["default_min_c"], status["default_max_c"], status["entity_min_c"], status["entity_max_c"]) == (25.0, 40.0, 20.0, 90.0)
    # Max below the default min, min above the default max, beyond the allowed range, unknown key
    assert refused == [422, 422, 422, 422, 422] and missing == 404
    assert (cleared["min_c"], cleared["max_c"], cleared["effective_min_c"], cleared["effective_max_c"]) == (None, None, 25.0, 40.0)
    device = ConfigStore(tmp_path / "config", lambda: library.profiles).load().devices[0]
    assert (device.flow_min_c, device.flow_max_c) == (None, None)


async def test_learn_stops_switch(tmp_path: Path, library: Any) -> None:
    """PUT /heat_pump/<id>/learn_stops switches the learning phase; on by default, shown in the status."""
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    try:
        await pump.minutes(NOW, 1)
        client, store = _limits_api(tmp_path, library, pump.recorder)
        config = store.load()
        config.devices = pump.config.devices
        store.save(config)

        before = client.get("/api/v1/heat_pump/status").json()["devices"][0]["learn_stops"]
        answer = client.put("/api/v1/heat_pump/hp/learn_stops", json={"active": False}).json()
        after = client.get("/api/v1/heat_pump/status").json()["devices"][0]["learn_stops"]
        refused = client.put("/api/v1/heat_pump/hp/learn_stops", json={"active": "no"}).status_code
        missing = client.put("/api/v1/heat_pump/nope/learn_stops", json={"active": True}).status_code
    finally:
        pump.store.close()

    assert (before, answer, after) == (True, {"active": False}, False)
    assert (refused, missing) == (422, 404)
    assert ConfigStore(tmp_path / "config", lambda: library.profiles).load().devices[0].flow_learn_stops is False


@pytest.mark.parametrize("stored", [300, "x", True, 10.0])
def test_broken_flow_limit_in_config_counts_as_not_set(tmp_path: Path, stored: Any) -> None:
    """A hand-edited limit outside the allowed range does not stop loading; the start limit applies."""
    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(devices=[DeviceConfig(id="hp", ha_device_id="x", profile=PROFILE, profile_version=2, name="n")]))
    raw = json.loads((tmp_path / "config.json").read_text())
    raw["devices"][0] |= {"flow_max_c": stored, "flow_min_c": 26.0}
    (tmp_path / "config.json").write_text(json.dumps(raw))

    device = ConfigStore(tmp_path).load().devices[0]
    assert (device.flow_min_c, device.flow_max_c) == (26.0, None)


async def test_recorder_uses_the_user_limits(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.config.devices[0].flow_max_c = 33.0
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        await pump.minutes(NOW, 2)
        guard = pump.recorder.status()["hp"]["flow"]["guard"]
    finally:
        pump.store.close()

    assert (guard["min_c"], guard["max_c"]) == (25.0, 33.0)


def test_state_survives_a_restart() -> None:
    """Written value, block and the latest writes are kept in heat_pump_state.json; old keys are dropped."""
    sim = Sim(written=28.0)
    sim.off(6)
    sim.room = 21.6
    sim.off(1)

    again = Guard(sim.guard.as_dict() | {"start": {"kind": 1}, "bumps": {"x": 1}})

    assert (again.written_c, again.block, again.block_reason) == (sim.guard.written_c, True, "room_warm")
    assert [w["reason"] for w in again.recent_writes] == ["start", "block_on"]
    assert "bumps" not in again.as_dict() and "start" not in again.as_dict()
    assert Guard({"written_c": "x"}).written_c is None
