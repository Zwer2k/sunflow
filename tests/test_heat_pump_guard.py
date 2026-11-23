"""Limits and protection layer in shadow (design/heat_pump.md, 8.3, 8.4, 16 step 3 part 3), with synthetic
runs of a heat pump: start from a pause, start after hot water, defrost, block on and off, tracking a
rising Tv, unwanted shutdowns; learned swing, jumps and heat output range; daily counts."""

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
from sunflow.heat_pump.defaults import BUMP_DECAY_K_PER_DAY, BUMP_EXTRA_K, JUMP_MIN_EVENTS, SWING_MIN_ROWS
from sunflow.heat_pump.guard import Guard, GuardInput, GuardResult, daily_counts
from sunflow.heat_pump.learned import Learned, learn, values
from sunflow.heat_pump.limits import ControlSettings, clamp, implausible, round_up, setpoint_range
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
        self.now = NOW
        self.real_setpoint = written
        self.effective = written
        self.target: float | None = written
        self.room: float | None = 21.0
        self.plan = 21.0
        self.heat_needed: float | None = None
        self.base: float | None = None
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
            values=values(self.settings, self.learned, self.guard.bumps, self.now), tv_peak_c=peak, heat_w=self.heat,
            target_c=self.target, heat_needed_w=self.heat_needed, base_c=self.base, room_c=self.room, plan_c=self.plan, mode=self.mode,
            surface=self.surface,
        ), active=self.active, budget=self.budget)
        self.results.append((self.now, max(tv, peak or tv), result))
        self.now += MIN
        return result

    def run(self, tvs: list[float], **kwargs: Any) -> list[GuardResult]:
        return [self.minute(tv, **kwargs) for tv in tvs]

    def writes(self) -> list[tuple[str, float | bool | None]]:
        return [(w["reason"], w.get("value_c", w.get("block"))) for _, _, r in self.results for w in r.writes]

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [e for _, _, r in self.results for e in r.events if e["kind"] == kind]

    def notes(self) -> list[str]:
        return [m for _, _, r in self.results for m, _ in r.notes]


def _no_self_caused_shutdown(sim: Sim, since: int = 0) -> None:
    """While running, Tv (with its peaks) stayed below what Sunflow would have written + 3 K."""
    written = None
    for _, tv, result in sim.results[since:]:
        if written is not None and result.phase in ("running", "start_hold"):
            assert tv < written + 3.0, (tv, written)
        written = result.setpoint_c


# Start from a pause, hold, lower once


def test_start_from_pause_hold_and_lower_once() -> None:
    sim = Sim(written=30.0)
    sim.run([30.0] * 6)
    # Pause: the start rule raises the set value to max(Tv + 3, Tv + jump − 2.9)
    sim.run([29.5, 29.0, 28.5, 28.0], compressor=False, activity="idle")
    # The heat pump starts: Tv jumps by 5.5 K within the first minute and settles
    sim.minute(28.0)
    sim.minute(33.0, peak=33.5)
    sim.run([31.5, 30.6, 30.4, 30.5, 30.4, 30.5, 30.4, 30.5, 30.4, 30.5, 30.4])

    assert sim.writes() == [("start", 32.6), ("normal", 30.0)]
    phases = [r.phase for _, _, r in sim.results]
    assert phases[6] == "idle" and "start_hold" in phases and phases[-1] == "running"
    [jump] = sim.events("start_jump")
    assert (jump["start_kind"], jump["ref_c"], jump["jump_k"]) == ("normal", 28.0, 5.5)
    assert 120 <= jump["settle_s"] <= 240
    _no_self_caused_shutdown(sim)


def test_hold_does_not_lower_before_start_settle() -> None:
    """A long settling time learned: the set value stays up although Tv is calm and the target is lower."""
    learned = Learned(computed_at=NOW, start_settle_s={"normal": 15 * 60})
    sim = Sim(written=30.0, learned=learned)
    sim.minute(28.0, compressor=False, activity="idle")
    sim.minute(28.0)
    sim.run([30.5] * 12)

    assert sim.writes() == [("start", 31.1)]
    assert sim.results[-1][2].phase == "start_hold"
    sim.run([30.5] * 4)
    assert sim.writes() == [("start", 31.1), ("normal", 30.0)]


# Start after hot water: prepared during hot water, at most two writes


def test_start_after_hot_water_prepared_during_hot_water() -> None:
    sim = Sim(written=30.0)
    sim.run([30.0] * 6)
    hot_water = sim.run([45.0] * 15, activity="hot_water", bypass=100.0, valid=False)
    # Hot water ends: transition with the bypass valve, then the heat pump starts by itself
    sim.run([40.0, 38.0, 36.0], compressor=False, activity="idle", bypass=50.0, valid=False)
    sim.minute(35.0, peak=35.6, valid=False)
    sim.run([33.0, 31.5, 30.8, 30.6, 30.5, 30.6, 30.5, 30.6, 30.5], valid=False)
    sim.run([30.5, 30.6, 30.5])

    assert hot_water[0].phase == "hot_water"
    # Tv before hot water 30 + jump after hot water 6 − 2.9, then lowered once in valid heating
    assert sim.writes() == [("start", 33.1), ("normal", 30.0)]
    assert [w["phase"] for _, _, r in sim.results for w in r.writes] == ["hot_water", "running"]
    [jump] = sim.events("start_jump")
    assert (jump["start_kind"], jump["ref_c"], jump["jump_k"]) == ("hot_water", 30.0, 5.6)
    # Without preparing, 35.6 would have reached the shutdown limit 30 + 3
    assert 35.6 >= 30.0 + 3.0
    _no_self_caused_shutdown(sim)


def test_nothing_prepared_when_already_high_enough() -> None:
    sim = Sim(written=34.0)
    sim.target = 34.0
    sim.run([30.0] * 3)
    sim.run([45.0] * 5, activity="hot_water", bypass=100.0, valid=False)

    assert sim.writes() == []


# Defrost


def test_defrost_writes_nothing_and_start_after_it_is_protected() -> None:
    sim = Sim(written=31.0)
    sim.target = 31.0
    sim.run([31.0] * 6)
    defrost = sim.run([22.0] * 6, activity="defrost", bypass=100.0, valid=False)
    after = sim.run([24.0, 26.0, 28.0, 30.0, 31.0, 31.0, 31.1, 31.0, 31.1, 31.0, 31.0])

    assert all(r.writes == [] and r.phase == "defrost" for r in defrost)
    assert after[0].phase == "start_hold"
    assert sim.writes() == [("start", 34.1), ("normal", 31.0)]
    [jump] = sim.events("start_jump")
    assert (jump["start_kind"], jump["ref_c"]) == ("defrost", 31.0)


# Block on and off (wanted off)


def test_block_on_and_off_with_minimum_duration() -> None:
    sim = Sim(written=30.0)
    sim.room = 21.6
    blocked = sim.minute(30.0)
    sim.room = 21.1
    during = sim.run([30.0] * 19)
    released = sim.minute(30.0)
    restart = sim.minute(27.0, compressor=False, activity="idle")

    assert blocked.phase == "blocked" and all(r.phase == "blocked" and not r.writes for r in during)
    assert released.writes[0]["reason"] == "block_off"
    assert sim.writes() == [("block_on", True), ("block_off", False), ("start", 30.1)]
    assert restart.phase == "idle"
    notes = sim.notes()
    assert notes[0].startswith("Nur Lernen: Sunflow würde die Raumthermostat-Sperre setzen (Raum 21,6 °C, 0,6 K über dem Ziel")
    assert notes[1].startswith("Nur Lernen: Sunflow würde die Raumthermostat-Sperre aufheben")


def test_block_found_on_is_kept_and_shown() -> None:
    """A block Sunflow did not set is not released, only shown (8.6); nothing written while it lasts."""
    sim = Sim(written=30.0)
    sim.room = 20.0
    found = sim.run([27.0] * 30, compressor=False, activity="idle", blocked=True)

    assert sim.writes() == []
    assert all(r.phase == "blocked" for r in found)
    assert (sim.guard.block, sim.guard.block_reason) == (True, "foreign")
    notes = [(m, warn) for _, _, r in sim.results for m, warn in r.notes]
    assert notes == [("Raumthermostat-Sperre nicht von Sunflow gesetzt: Sunflow hebt sie nicht auf und regelt nicht, bis sie aufgehoben ist.", True)]


def test_block_set_by_someone_else_later_is_not_released() -> None:
    """Set by hand while Sunflow controls (also over its own virtual block): kept until released outside."""
    sim = Sim(written=30.0)
    sim.room = 21.6
    sim.minute(30.0)
    assert sim.guard.block_reason == "room_warm"
    sim.room = 20.0
    during = sim.run([27.0] * 40, compressor=False, activity="idle", blocked=True)

    assert sim.writes() == [("block_on", True)]
    assert all(r.phase == "blocked" for r in during) and sim.guard.block_reason == "foreign"
    # Released outside: Sunflow controls again, the start rule applies
    after = sim.minute(27.0, compressor=False, activity="idle")
    assert (sim.guard.block, sim.guard.block_reason) == (False, None)
    assert after.phase == "idle" and sim.writes()[-1] == ("start", 30.1)


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


def test_foreign_block_in_state_file_and_old_name() -> None:
    """Kept over a restart without a second journal entry; the earlier name "adopted" is read as foreign."""
    sim = Sim(written=30.0)
    sim.minute(27.0, compressor=False, activity="idle", blocked=True)
    again = Guard(sim.guard.as_dict())
    assert again.block_reason == "foreign"
    old = Guard({**sim.guard.as_dict(), "block_reason": "adopted"})
    assert old.block_reason == "foreign"
    sim.guard = old
    sim.minute(27.0, compressor=False, activity="idle", blocked=True)
    assert sim.notes() == sim.notes()[:1] and sim.writes() == []


def test_no_block_without_the_input() -> None:
    sim = Sim(written=30.0, has_block=False)
    sim.room = 23.0
    sim.minute(30.0)

    assert sim.writes() == []


# Tracking a rising Tv


def test_tracking_a_rising_tv() -> None:
    sim = Sim(written=30.0)
    tvs = [round(30.0 + 0.1 * i, 1) for i in range(60)]
    sim.run(tvs)

    reasons = {reason for reason, _ in sim.writes()}
    assert reasons == {"track"}
    # A headroom above the bound: not a write every minute
    assert 5 <= len(sim.writes()) <= 8
    _no_self_caused_shutdown(sim)


def test_minimum_output_above_need_blocks_instead_of_tracking() -> None:
    sim = Sim(written=30.0)
    sim.heat_needed = 300.0
    sim.run([round(30.0 + 0.1 * i, 1) for i in range(30)])

    assert sim.writes() == [("block_on", True)]
    assert sim.guard.block_reason == "min_power"
    assert "Mindestleistung 0,6 kW über dem Bedarf 0,3 kW" in sim.notes()[0]


# Unwanted shutdowns


def test_unwanted_shutdown_in_running_operation_raises_the_swing() -> None:
    sim = Sim(written=30.0)
    sim.run([30.0] * 9)
    sim.minute(30.0, peak=33.1)
    sim.minute(30.2, compressor=False, activity="idle")

    [stop] = sim.events("unwanted_stop")
    assert (stop["during"], stop["raised"], stop["peak_c"], stop["setpoint_c"]) == ("running", "steady_swing", 33.1, 30.0)
    assert stop["value"] == pytest.approx(3.1 + BUMP_EXTRA_K)
    raised = values(sim.settings, None, sim.guard.bumps, sim.now)
    assert (raised.steady_swing, raised.sources["steady_swing"]) == (pytest.approx(3.6, abs=0.01), "raised")
    # Fades over weeks
    later = values(sim.settings, None, sim.guard.bumps, sim.now + timedelta(days=10))
    assert later.steady_swing == pytest.approx(3.6 - 10 * BUMP_DECAY_K_PER_DAY, abs=0.01)
    assert values(sim.settings, None, sim.guard.bumps, sim.now + timedelta(days=60)).steady_swing == 0.5
    assert sim.notes()[0].startswith("Ungewollte Abschaltung des Kompressors im laufenden Betrieb: Vorlauf 33,1 °C")


def test_mode_off_learns_nothing_from_a_shutdown() -> None:
    """Mode off ("Aus"): a stop at the shutdown limit raises no jump or swing."""
    sim = Sim(written=30.0)
    sim.mode = "off"
    sim.run([30.0] * 9)
    sim.minute(30.0, peak=33.1)
    sim.minute(30.2, compressor=False, activity="idle")

    assert sim.events("unwanted_stop") == [] and sim.guard.bumps == {}


def test_unwanted_shutdown_during_a_start_raises_its_jump() -> None:
    sim = Sim(written=30.0)
    sim.minute(27.0, compressor=False, activity="idle")
    sim.real_setpoint = sim.effective = 30.0
    sim.minute(27.0)
    sim.minute(31.0, peak=33.2)
    sim.minute(32.0, compressor=False, activity="idle")

    [stop] = sim.events("unwanted_stop")
    assert (stop["during"], stop["raised"]) == ("normal", "start_jump.normal")
    assert values(sim.settings, None, sim.guard.bumps, sim.now).start_jump["normal"] == pytest.approx(33.2 - 27.0 + BUMP_EXTRA_K, abs=0.01)


@pytest.mark.parametrize(
    ("peak", "kwargs"),
    [
        pytest.param(32.5, {"activity": "idle"}, id="below the limit"),
        pytest.param(33.5, {"activity": "idle", "blocked": True}, id="block"),
        pytest.param(33.5, {"activity": "hot_water", "bypass": 100.0}, id="hot water"),
    ],
)
def test_stops_that_are_not_unwanted(peak: float, kwargs: dict[str, Any]) -> None:
    sim = Sim(written=30.0)
    sim.run([30.0] * 3)
    sim.minute(30.0, peak=peak)
    sim.minute(30.0, compressor=False, **kwargs)

    assert sim.events("unwanted_stop") == []


# Limits (8.3)


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


def test_user_maximum_caps_the_protection_layer() -> None:
    """The start rule wants more than the screed may get: the set value stays at the user's maximum."""
    sim = Sim(written=30.0, user_max=32.0)
    sim.run([30.0] * 3)
    sim.run([29.5, 29.0], compressor=False, activity="idle")

    assert sim.writes() == [("start", 32.0)]
    assert all(r.setpoint_c is None or r.setpoint_c <= 32.0 for _, _, r in sim.results)
    assert sim.results[-1][2].limits == (25.0, 32.0)


def test_implausible_target_is_not_written_and_noted_once() -> None:
    sim = Sim(written=30.0)
    sim.target = 39.0
    sim.run([30.0] * 8)

    assert implausible(39.0, 30.0, None) == pytest.approx(9.0)
    assert implausible(37.0, 30.0, None) is None
    result = sim.results[-1][2]
    assert result.implausible_k == pytest.approx(9.0) and result.freeze_up
    assert sim.writes() == []
    assert len([n for n in sim.notes() if "nicht plausibel" in n]) == 1


def test_own_raised_setpoint_does_not_make_the_target_implausible() -> None:
    """Writing for real, the effective setpoint is Sunflow's own protection step (e.g. 39.6 °C after a defrost):
    the plausibility compares with the base instead, so the set value is lowered again once Tv is calm."""
    shadow = Sim(written=39.6)
    shadow.target = shadow.base = 29.4
    shadow.run([31.0] * 8)
    active = Sim(written=39.6)
    active.active = True
    active.target = active.base = 29.4
    active.run([31.0] * 8)
    # The base itself far from the target (e.g. a curve far off) still counts as implausible
    stale = Sim(written=39.6)
    stale.active = True
    stale.target, stale.base = 39.0, 29.4
    last = stale.minute(31.0)

    assert shadow.results[-1][2].implausible_k == pytest.approx(-10.2)
    assert shadow.writes() == []
    assert all(r.implausible_k is None for _, _, r in active.results)
    [(reason, value)] = active.writes()
    assert reason == "normal" and value < 39.6
    assert last.implausible_k == pytest.approx(9.6)


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
    sim = Sim(written=30.0)
    sim.mode = change.get("mode", "heat")
    result = sim.minute(27.0, compressor=False, activity="idle", strategy=change.get("strategy", "fixed"))

    assert reason in result.inactive and result.writes == []


# Learned values (8.4, 8.3)


def _steady_rows(count: int, swing: float = 0.3, heat: float = 2000.0) -> list[dict[str, Any]]:
    return [
        {"start": (NOW - (count - i) * FIVE).isoformat(), "samples": 5, "tv_c": 30.0, "tv_max_c": 30.0 + swing * (i % 3) / 2,
         "heat_w": heat + 10 * (i % 50), "compressor": 1.0, "flags": {"valid": True}, "events": []}
        for i in range(count)
    ]


def test_learned_from_the_table() -> None:
    rows = _steady_rows(SWING_MIN_ROWS + 10)
    jumps = [4.0, 4.5, 5.0, 5.5, 6.5, 4.2]
    rows[-1]["events"] = [
        {"kind": "start_jump", "start_kind": "hot_water", "jump_k": j, "settle_s": 300 + 60 * i} for i, j in enumerate(jumps)
    ] + [{"kind": "start_jump", "start_kind": "normal", "jump_k": 3.0, "settle_s": 120}]

    learned = learn(rows, NOW)

    assert learned.steady_swing == pytest.approx(0.3)
    assert learned.start_jump == {"hot_water": 5.5}
    assert learned.start_settle_s == {"hot_water": 540}
    assert learned.starts == {"hot_water": 6, "normal": 1}
    assert learned.power_min_w is not None and 2000 <= learned.power_min_w <= 2050
    used = values(ControlSettings(), learned, {}, NOW)
    # Learned replaces the start value, a kind without enough starts keeps it; the maximum is only raised
    assert (used.steady_swing, used.start_jump["hot_water"], used.start_jump["normal"]) == (pytest.approx(0.3), 5.5, 6.0)
    assert used.power_max_w == 6000.0
    assert Learned.from_dict(learned.as_dict()) == learned
    assert len(jumps) >= JUMP_MIN_EVENTS


def test_too_little_data_keeps_the_start_values() -> None:
    learned = learn(_steady_rows(20), NOW)

    used = values(ControlSettings(), learned, {}, NOW)
    assert (learned.steady_swing, learned.power_min_w) == (None, None)
    assert (used.steady_swing, used.start_jump["normal"], used.start_settle_s["normal"], used.power_min_w) == (0.5, 6.0, 300, 600)
    assert used.sources["steady_swing"] == "start"


def test_settings_from_the_profile_and_manifest(library: Any) -> None:
    settings = ControlSettings.from_profile(library.profiles[PROFILE], joined())

    assert (settings.step, settings.entity_min, settings.entity_max) == (0.1, 20.0, 90.0)
    assert (settings.start_margin, settings.stop_margin, settings.shutdown_guard) == (3.0, 3.0, pytest.approx(2.9))
    assert (settings.steady_swing, settings.start_jump, settings.start_settle_s) == (0.5, 6.0, 300)
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
        {"at": "2026-11-20T22:30:00+00:00", "kind": "unwanted_stop"},
        {"at": "2026-11-21T09:00:00+00:00", "kind": "flow_source"},
    ]

    today, yesterday = daily_counts(events, tz, 2, NOW)

    assert (today["date"], today["start"], today["normal"], today["track"], today["block_on"]) == ("2026-11-21", 1, 1, 1, 1)
    assert (today["setpoint"], today["protect"], today["block"], today["unwanted_stops"]) == (3, 2, 1, 0)
    assert (yesterday["date"], yesterday["unwanted_stops"], yesterday["setpoint"]) == ("2026-11-20", 1, 0)


# Through the recorder: events in heat_pump_samples, journal, status, counts


async def test_recorder_notes_planned_writes(tmp_path: Path, library: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return (NOW + 12 * MIN).astimezone(tz)

    # The counts per day run up to "today": the API's clock at the time of the run, not the real one
    monkeypatch.setattr("sunflow.api.heat_pump.datetime", Clock)
    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        await pump.minutes(NOW, 3)
        # Pause: compressor off, Tv down; the start rule would raise Max T-Vorlauf
        pump.states["status_kompressor"] = ("off", None)
        pump.states["mode_of_operating"] = ("Bereitschaft", None)
        pump.states["tv"] = ("28.0", "°C")
        await pump.minutes(NOW + 3 * MIN, 2)
        # The room well above the setpoint: the block
        pump.extra["sensor.wohnzimmer"] = pump._state("21.6", "°C")
        await pump.minutes(NOW + 5 * MIN, 6)
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

    writes = [e for row in rows for e in row["events"] if e["kind"] == "write"]
    assert [(w["reason"], w.get("value_c")) for w in writes] == [("start", 31.1), ("block_on", None)]
    assert all(w["shadow"] for w in writes)
    assert (status["shadow"], status["block"], status["block_reason"], status["setpoint_c"]) == (True, True, "room_warm", 31.1)
    assert status["values"]["steady_swing"] == 0.5
    assert any(n.startswith("Nur Lernen: Sunflow würde die Raumthermostat-Sperre setzen") for n in pump.notes())
    today = answer["days"][0]
    assert (today["start"], today["block_on"], today["setpoint"]) == (1, 1, 1)
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
    """Written value, block, Tv before hot water and raises are kept in heat_pump_state.json."""
    sim = Sim(written=30.0)
    sim.run([30.0] * 9)
    sim.minute(30.0, peak=33.1)
    sim.minute(30.2, compressor=False, activity="idle")
    sim.room = 21.6
    sim.minute(30.0, compressor=False, activity="idle")

    again = Guard(sim.guard.as_dict())

    assert (again.written_c, again.block, again.block_reason) == (sim.guard.written_c, True, "room_warm")
    assert again.bumps == sim.guard.bumps and again.bumps["steady_swing"]["k"] == pytest.approx(3.6)
    assert [w["reason"] for w in again.recent_writes] == [w["reason"] for w in sim.guard.recent_writes]
    assert Guard({"written_c": "x", "start": {"kind": 1}}).written_c is None
