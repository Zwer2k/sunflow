"""Write budget of the set value (design/heat_pump.md, 8.5, 16 step 3 part 4.3): 48 normal writes per day,
24 more only for protection steps, at most 6 in any hour; the local day in Home Assistant's time zone;
counted virtually in the shadow (from the write events), never in eeprom_writes.json; a protection step the
budget holds back sets the block instead; EepromGuard keeps the times for active writing."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sunflow.devices.executor import EepromGuard
from sunflow.heat_pump.budget import BudgetLimits, EepromLog, VirtualLog, WriteBudget, day_start
from sunflow.heat_pump.limits import ControlSettings
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from test_heat_pump_guard import Sim

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
MIN = timedelta(minutes=1)
PROFILE = "esphome/rotex_hpsu_can"
BERLIN = ZoneInfo("Europe/Berlin")


def _budget(times: list[datetime], tz: Any = None, **limits: int) -> WriteBudget:
    log = VirtualLog()
    for at in times:
        log.add(at)
    return WriteBudget(log, BudgetLimits(**limits) if limits else None, tz)


# Limits


def test_limits_from_the_profile(library: Any) -> None:
    settings = ControlSettings.from_profile(library.profiles[PROFILE], None)

    assert settings.budget == BudgetLimits(per_day=48, emergency_per_day=24, per_hour=6)
    # Missing or not a count: the defaults; 0 is a valid limit (nothing of that kind)
    assert BudgetLimits.from_writes({"per_day": "x", "per_hour": True, "emergency_per_day": 0}) == BudgetLimits(
        per_day=48, emergency_per_day=0, per_hour=6,
    )
    assert ControlSettings().budget == BudgetLimits()


def test_normal_writes_stop_at_the_daily_budget_protection_steps_go_on() -> None:
    # 48 writes today, spread so the hourly limit does not bite
    budget = _budget([NOW - timedelta(hours=11) + i * timedelta(minutes=13) for i in range(48)])

    assert budget.used(NOW + timedelta(hours=1)) == (48, 0)
    assert budget.check(False, NOW + timedelta(hours=1)) == "day"
    assert budget.check(True, NOW + timedelta(hours=1)) is None


def test_protection_steps_stop_after_the_emergency_quota() -> None:
    budget = _budget([NOW - timedelta(hours=11) + i * timedelta(minutes=9) for i in range(72)])

    assert budget.check(True, NOW + timedelta(hours=1)) == "emergency"
    status = budget.status(NOW + timedelta(hours=1), virtual=True)
    assert (status["used_today"], status["normal_left"], status["emergency_left"]) == (72, 0, 0)
    assert (status["normal"], status["protect"], status["virtual"]) == ("day", "emergency", True)


def test_at_most_six_in_any_sixty_minutes_also_for_protection_steps() -> None:
    budget = _budget([NOW + i * 5 * MIN for i in range(6)])
    last = NOW + 25 * MIN

    assert budget.check(False, last + MIN) == "hour"
    assert budget.check(True, last + MIN) == "hour"
    # Rolling: one hour after the first write the next one may go out
    assert budget.check(True, NOW + 60 * MIN) is None
    assert budget.used(NOW + 60 * MIN) == (6, 5)


def test_the_day_is_the_local_day_of_home_assistant() -> None:
    # 22:30 UTC = 23:30 in Berlin (yesterday locally), 23:30 UTC = 00:30 (today)
    times = [datetime(2026, 11, 20, 22, 30, tzinfo=UTC), datetime(2026, 11, 20, 23, 30, tzinfo=UTC)]
    local = _budget(times, BERLIN)
    rolling = _budget(times)

    assert day_start(NOW, BERLIN) == datetime(2026, 11, 20, 23, tzinfo=UTC)
    assert local.used(NOW)[0] == 1
    # Without the time zone the last 24 hours count: never more permissive than a calendar day
    assert rolling.used(NOW)[0] == 2
    assert local.status(NOW, virtual=True)["local_day"] is True


def test_the_day_changes_with_daylight_saving_time() -> None:
    # 2027-03-28: Berlin changes to summer time; the local day starts at 23:00 UTC the day before
    at = datetime(2027, 3, 28, 12, tzinfo=UTC)

    assert day_start(at, BERLIN) == datetime(2027, 3, 27, 23, tzinfo=UTC)
    assert day_start(datetime(2027, 3, 29, 12, tzinfo=UTC), BERLIN) == datetime(2027, 3, 28, 22, tzinfo=UTC)


# Virtual counting


def test_virtual_log_from_the_write_events() -> None:
    events = [
        {"at": "2026-11-21T08:00:00+00:00", "kind": "write", "reason": "normal", "shadow": True},
        {"at": "2026-11-21T08:00:00+00:00", "kind": "write", "reason": "normal", "shadow": True},
        {"at": "2026-11-21T09:00:00+00:00", "kind": "write", "reason": "start", "shadow": False},
        {"at": "2026-11-21T09:30:00+00:00", "kind": "write", "reason": "track", "manual": True},
        # The block is not stored in the EEPROM: it does not count
        {"at": "2026-11-21T10:00:00+00:00", "kind": "write", "reason": "block_on", "block": True},
        {"at": "2026-11-21T10:00:00+00:00", "kind": "start"},
        {"at": "not a time", "kind": "write", "reason": "normal"},
        {"at": "2026-11-19T10:00:00+00:00", "kind": "write", "reason": "normal"},
        "junk",
    ]
    log = VirtualLog()
    log.seed(events, NOW)

    # A write seen twice counts once, a real one counts too; older than a day and a bit is dropped
    assert log.seeded
    assert [at.hour for at in log.times()] == [8, 9, 9]


def test_shadow_counts_nothing_in_eeprom_writes(tmp_path: Path) -> None:
    sim = Sim(written=30.0)
    sim.budget = WriteBudget()
    sim.run([30.0] * 5)
    sim.target = 33.0
    sim.run([30.0] * 3)

    assert sim.writes() == [("normal", 33.0)]
    assert sim.budget.used(sim.now) == (1, 1)
    assert not (tmp_path / "eeprom_writes.json").exists()


# Through the write layer and the protection layer


def _exhausted(per_day: int, emergency: int, per_hour: int = 6, used: int = 0) -> WriteBudget:
    return _budget([NOW - timedelta(hours=3) + i * 20 * MIN for i in range(used)], per_day=per_day, emergency_per_day=emergency, per_hour=per_hour)


def test_normal_value_waits_without_a_time_once_the_budget_is_used_up() -> None:
    sim = Sim(written=30.0)
    sim.budget = _exhausted(per_day=2, emergency=1, used=2)
    sim.run([30.0] * 5)
    sim.target = 33.0
    results = sim.run([30.0] * 3)

    assert sim.writes() == []
    pending = results[-1].pending
    assert (pending.value_c, pending.held, pending.budget, pending.due_at) == (33.0, "budget", "day", None)
    # Journal once per day, marked as shadow
    budget_notes = [n for n in sim.notes() if "Schreibbudget für heute erschöpft" in n]
    assert budget_notes == ["Schatten: Schreibbudget für heute erschöpft (2 Schreibvorgänge): normale Werte erst morgen wieder, Schutzschritte über das Notkontingent."]


def _pause(sim: Sim) -> None:
    """Running, then a pause: the start rule wants a protection step."""
    sim.run([30.0] * 6)
    sim.run([29.5, 29.0, 28.5, 28.0], compressor=False, activity="idle")


def test_protection_step_from_the_emergency_quota() -> None:
    sim = Sim(written=30.0)
    sim.budget = _exhausted(per_day=2, emergency=1, used=2)
    _pause(sim)

    assert sim.writes() == [("start", 32.6)]
    assert sim.budget.status(sim.now, virtual=True)["emergency_left"] == 0


def test_block_instead_of_a_protection_step_when_the_emergency_quota_is_used_up() -> None:
    sim = Sim(written=30.0)
    sim.budget = _exhausted(per_day=2, emergency=1, used=3)
    _pause(sim)

    assert sim.writes() == [("block_on", True)]
    assert (sim.guard.block, sim.guard.block_reason) == (True, "budget")
    last = sim.results[-1][2]
    assert last.phase == "blocked" and last.pending is None
    notes = sim.notes()
    assert "Schatten: Sunflow würde die Raumthermostat-Sperre setzen (Schreibbudget erschöpft)." in notes
    assert any(n.startswith("Schatten: Notkontingent für heute erschöpft (1 Schreibvorgänge zusätzlich)") for n in notes)


def test_budget_block_released_once_a_protection_step_may_be_written() -> None:
    sim = Sim(written=30.0)
    # The hourly limit: six writes in the last 50 minutes
    sim.budget = _budget([NOW - 50 * MIN + i * MIN for i in range(6)])
    _pause(sim)
    blocked_at = sim.guard.block_at
    assert sim.writes() == [("block_on", True)]
    # Still blocked while the hour is not over (and for the minimum duration)
    sim.run([28.0] * 10, compressor=False, activity="idle")
    assert sim.guard.block is True
    # Minimum duration passed and the first write left the hour: released, then the start step
    sim.run([28.0] * 12, compressor=False, activity="idle")

    # Tv 28 °C now: max(28 + 3, 28 + 6 − 2.9)
    assert sim.writes() == [("block_on", True), ("block_off", False), ("start", 31.1)]
    assert sim.guard.block is False and sim.now - blocked_at >= timedelta(minutes=20)


def test_without_a_block_the_protection_step_waits_and_is_noted_once() -> None:
    sim = Sim(written=30.0, has_block=False)
    sim.budget = _exhausted(per_day=2, emergency=1, used=3)
    _pause(sim)
    sim.run([28.0] * 3, compressor=False, activity="idle")

    assert sim.writes() == []
    assert sim.results[-1][2].pending.held == "budget"
    unprotected = [n for n in sim.notes() if "Schutzschritt wegen des Budgets nicht möglich" in n]
    assert len(unprotected) == 1 and unprotected[0].startswith("Schatten: ")


def test_a_foreign_block_is_not_taken_over_by_the_budget() -> None:
    sim = Sim(written=30.0)
    sim.budget = _exhausted(per_day=2, emergency=1, used=3)
    sim.run([30.0] * 3)
    sim.run([28.0] * 3, compressor=False, activity="idle", blocked=True)

    assert sim.guard.block_reason == "foreign"
    assert all(w[0] != "block_on" for w in sim.writes())


# FlowShadow: time zone, status


async def test_flow_shadow_reads_the_time_zone_and_shows_the_budget(tmp_path: Path) -> None:
    class Ha:
        calls = 0

        async def get_state(self, entity_id: str) -> dict[str, Any] | None:
            return None

        async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
            Ha.calls += 1
            assert msg_type == "get_config"
            return {"time_zone": "Europe/Berlin"}

    flow = FlowShadow(HeatPumpStateStore(tmp_path), Ha())
    await flow._timezone(NOW)
    await flow._timezone(NOW + 30 * MIN)
    await flow._timezone(NOW + 61 * MIN)

    assert flow.tz == BERLIN and Ha.calls == 2
    assert flow.budget("hp").seeded is False


async def test_time_zone_failure_keeps_the_last_one(tmp_path: Path) -> None:
    class Ha:
        async def get_state(self, entity_id: str) -> dict[str, Any] | None:
            return None

        async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
            raise ConnectionError("away")

    flow = FlowShadow(HeatPumpStateStore(tmp_path), Ha())
    flow.tz = BERLIN
    await flow._timezone(NOW)

    assert flow.tz == BERLIN


async def test_recorder_seeds_the_budget_from_heat_pump_samples(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        start = NOW - timedelta(hours=2)
        pump.store.store_heat_pump_samples([{
            "device_id": "hp", "start": start, "samples": 5,
            "events": [
                {"at": (start + MIN).isoformat(), "kind": "write", "reason": "normal", "value_c": 31.0, "shadow": True},
                {"at": (start + 2 * MIN).isoformat(), "kind": "write", "reason": "block_on", "block": True, "shadow": True},
            ],
        }])
        await pump.minutes(NOW, 2)
        budget = pump.recorder.flow.budget("hp")
        status = pump.recorder.status()["hp"]["flow"]["guard"]["budget"]
    finally:
        pump.store.close()

    assert budget.seeded and budget.used(NOW + MIN)[0] >= 1
    assert (status["per_day"], status["emergency_per_day"], status["per_hour"], status["virtual"]) == (48, 24, 6, True)
    assert not (tmp_path / "eeprom_writes.json").exists() and not (tmp_path / "data" / "eeprom_writes.json").exists()


# EepromGuard (for active writing)


def test_eeprom_guard_keeps_the_times(tmp_path: Path) -> None:
    guard = EepromGuard(tmp_path, 10)
    guard.record("number.max", NOW - timedelta(hours=30))
    guard.record("number.max", NOW - MIN)
    guard.record("select.other", NOW)

    assert guard.times("number.max") == [NOW - MIN]
    assert guard.times("missing") == []
    # Today's counters as before
    assert guard.allowed("number.max") is True
    log = EepromLog(guard, "number.max")
    log.add(NOW)
    assert WriteBudget(log).used(NOW) == (1, 1)


def test_eeprom_guard_reads_an_old_file(tmp_path: Path) -> None:
    # EepromGuard counts per date.today()
    today = datetime.now().date().isoformat()
    (tmp_path / "eeprom_writes.json").write_text(json.dumps({today: {"number.max": 2}}))
    guard = EepromGuard(tmp_path, 3)

    assert guard.times("number.max") == []
    guard.record("number.max")
    assert guard.allowed("number.max") is False
    assert len(guard.times("number.max")) == 1
