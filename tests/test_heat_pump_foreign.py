"""Foreign writers of the set value (design/heat_pump.md, 12): a change of the real
Max T-Vorlauf Sunflow did not write is noted once per episode (journal, status, setup summary), the written
value follows it (in the shadow the virtual one); while the ESP regulation is active nothing is written."""

import copy
from datetime import timedelta
from pathlib import Path
from typing import Any

from heat_pump_node import joined
from sunflow.analysis.summary import build_summary
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.heat_pump.budget import WriteBudget
from sunflow.heat_pump.compat import check_compatibility
from sunflow.heat_pump.guard import Guard, GuardResult, daily_counts
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from test_heat_pump_guard import NOW, Sim
from test_heat_pump_manifest import _without

PROFILE = "esphome/rotex_hpsu_can"
MIN = timedelta(minutes=1)


def _steady(written: float = 30.0) -> Sim:
    """Heating validly with Tv calm and the flow target at the set value: nothing to write."""
    sim = Sim(written=written)
    sim.budget = WriteBudget()
    sim.run([written] * 6)
    return sim


def _foreign(sim: Sim) -> list[dict[str, Any]]:
    return sim.events("foreign_write")


# Shadow: every real change is foreign


def test_foreign_change_is_noted_once_and_the_written_value_follows() -> None:
    sim = _steady()
    sim.real_setpoint = 33.0
    changed_at = sim.now
    result = sim.minute(30.0)

    [event] = _foreign(sim)
    assert (event["from_c"], event["value_c"], event["written_c"], event["shadow"], event["regulation"]) == (30.0, 33.0, 30.0, True, False)
    assert event["activity"] == "heating"
    assert result.foreign == event
    # The virtual written value follows the real one; the spacing runs from the change
    assert (sim.guard.written_c, sim.guard.written_at) == (33.0, changed_at)
    assert result.pending is not None and (result.pending.held, result.pending.value_c) == ("spacing", 30.0)
    assert result.pending.due_at == changed_at + timedelta(minutes=5)
    [note] = sim.notes()
    assert note.startswith("Max T-Vorlauf von außen geändert: 30,0 → 33,0 °C, nicht von Sunflow")
    # A foreign change is not Sunflow's write: neither a write event nor counted against the budget
    assert [w for w in sim.writes() if w[0] != "normal"] == []
    assert sim.budget.used(sim.now) == (0, 0)


def test_further_changes_count_in_the_episode_without_new_notes() -> None:
    sim = _steady()
    for value in (33.0, 32.0, 31.5):
        sim.real_setpoint = value
        sim.minute(30.0)

    assert [e["value_c"] for e in _foreign(sim)] == [33.0, 32.0, 31.5]
    assert len([n for n in sim.notes() if n.startswith("Max T-Vorlauf von außen")]) == 1
    status = sim.guard.foreign.status(sim.now)
    assert (status["episode"]["count"], status["episode"]["from_c"], status["episode"]["value_c"]) == (3, 30.0, 31.5)
    assert [c["value_c"] for c in status["recent"]] == [31.5, 32.0, 33.0]


def test_episode_ends_a_day_after_the_last_change() -> None:
    sim = _steady()
    sim.real_setpoint = 33.0
    sim.minute(30.0)
    sim.now += timedelta(hours=23)
    sim.minute(30.0)
    assert sim.guard.foreign.episode is not None

    sim.now += timedelta(hours=1, minutes=1)
    sim.minute(30.0)
    assert sim.guard.foreign.episode is None
    assert sim.notes()[-1] == "Seit 24 h keine Änderung von Max T-Vorlauf von außen mehr (zuvor 1× geändert)."

    # The next foreign change starts a new episode with a new warning
    sim.real_setpoint = 34.0
    sim.minute(30.0)
    assert sim.notes()[-1].startswith("Max T-Vorlauf von außen geändert: 33,0 → 34,0 °C")


def test_first_value_small_changes_and_unknown_values_are_no_change() -> None:
    sim = _steady(30.0)
    # Less than half a step (0.1): noise of the state, no change
    sim.real_setpoint = 30.04
    sim.minute(30.0)
    # Unknown for a while: skipped, the last known value stays
    sim.real_setpoint = None
    sim.minute(30.0)
    sim.real_setpoint = 30.0
    sim.minute(30.0)

    assert _foreign(sim) == []
    assert sim.notes() == []
    assert sim.guard.foreign.seen_c == 30.0


def test_a_change_while_sunflow_was_not_running_is_noted() -> None:
    sim = _steady(30.0)
    stored = sim.guard.as_dict()
    assert stored["foreign"]["seen_c"] == 30.0

    restarted = Sim(written=31.5)
    restarted.guard = Guard(stored)
    restarted.minute(30.0)

    [event] = _foreign(restarted)
    assert (event["from_c"], event["value_c"]) == (30.0, 31.5)
    assert restarted.guard.written_c == 31.5


# Active writing (from part 4.7): Sunflow's own change is not foreign


def test_own_write_is_not_foreign_another_value_is() -> None:
    sim = _steady(30.0)
    sim.active = True
    sim.guard.record_write(GuardResult(phase="running"), sim.now, 32.0, "normal", shadow=False)
    sim.now += MIN
    # The heat pump takes the value over: Sunflow's own
    sim.real_setpoint = 32.0
    sim.minute(30.0)
    assert _foreign(sim) == []
    assert sim.guard.foreign.expect_c is None

    # Someone else changes it afterwards
    sim.real_setpoint = 35.0
    sim.minute(30.0)
    [event] = _foreign(sim)
    assert (event["from_c"], event["value_c"], event["shadow"]) == (32.0, 35.0, False)


def test_own_write_counts_as_own_only_for_a_while() -> None:
    sim = _steady(30.0)
    sim.guard.record_write(GuardResult(phase="running"), sim.now, 32.0, "normal", shadow=False)
    sim.now += timedelta(minutes=11)
    sim.real_setpoint = 32.0
    sim.minute(30.0)

    assert [e["value_c"] for e in _foreign(sim)] == [32.0]


def test_shadow_writes_are_never_own() -> None:
    sim = _steady(30.0)
    sim.guard.record_write(GuardResult(phase="running"), sim.now, 32.0, "normal", shadow=True)
    sim.now += MIN
    sim.real_setpoint = 32.0
    sim.minute(30.0)

    assert [e["value_c"] for e in _foreign(sim)] == [32.0]


# ESP regulation (optional role regulated_setpoint)


def test_esp_regulation_writes_nothing_and_is_noted() -> None:
    sim = _steady(30.0)
    sim.target = 35.0
    sim.extra = {"regulated_setpoint": 32.0}
    result = sim.minute(30.0)

    assert "esp_regulation" in result.inactive and result.phase == "inactive"
    assert result.request is None and result.pending is None
    assert sim.notes()[-1].startswith("ESP-Regelung des Vorlaufs aktiv (32,0 °C)")
    # The ESP writes Max T-Vorlauf: foreign, marked as regulation
    sim.real_setpoint = 32.0
    sim.minute(30.0)
    [event] = _foreign(sim)
    assert event["regulation"] is True
    assert sim.notes()[-1].endswith("Die ESP-Regelung ist aktiv.")
    status = sim.guard.foreign.status(sim.now)
    assert (status["regulation"]["active"], status["regulation"]["value_c"]) == (True, 32.0)

    # Set to 0: Sunflow writes again by its rules
    sim.extra = {"regulated_setpoint": 0.0}
    result = sim.minute(30.0)
    assert "esp_regulation" not in result.inactive
    assert sim.notes()[-1] == "ESP-Regelung des Vorlaufs beendet."
    assert len([n for n in sim.notes() if n.startswith("ESP-Regelung des Vorlaufs aktiv")]) == 1


def test_unknown_regulation_counts_as_off() -> None:
    sim = _steady(30.0)
    sim.extra = {"regulated_setpoint": None}
    result = sim.minute(30.0)

    assert "esp_regulation" not in result.inactive
    assert sim.notes() == []


def test_regulation_state_survives_a_restart() -> None:
    sim = _steady(30.0)
    sim.extra = {"regulated_setpoint": 32.0}
    sim.minute(30.0)

    restarted = Sim(written=30.0)
    restarted.guard = Guard(sim.guard.as_dict())
    restarted.extra = {"regulated_setpoint": 32.0}
    restarted.minute(30.0)
    assert restarted.notes() == []


def test_missing_regulation_key_is_nothing_missing(library: Any) -> None:
    profile = library.profiles[PROFILE]

    assert check_compatibility(profile, _without(joined(), "supply_setpoint_regulated")).problems == ()
    # Present but not as needed: an optional problem as for every role
    manifest = copy.deepcopy(joined())
    entity = next(e for e in manifest["entities"] if e["key"] == "supply_setpoint_regulated")
    entity["unit"] = "K"
    [problem] = check_compatibility(profile, manifest).problems
    assert (problem.role, problem.optional) == ("regulated_setpoint", True)


# Status, summary, counts


def test_summary_names_foreign_writers_and_the_regulation(tmp_path: Path, library: Any) -> None:
    sim = _steady(30.0)
    sim.real_setpoint = 33.0
    sim.extra = {"regulated_setpoint": 31.0}
    sim.minute(30.0)
    flow = FlowShadow(HeatPumpStateStore(tmp_path))
    flow.guards["hp"] = sim.guard

    notes = flow.summary_notes({"hp": "HPSU CAN"}, sim.now + timedelta(minutes=30))
    assert notes == [
        "HPSU CAN: Max T-Vorlauf wird von außen geändert, nicht von Sunflow (1× geändert, zuletzt vor 31 min) – andere Regler abschalten, die ihn schreiben.",
        "HPSU CAN: ESP-Regelung des Vorlaufs aktiv – Sunflow schreibt nicht, solange sie aktiv ist (in ESPHome auf 0 setzen).",
    ]
    assert flow.summary_notes({"hp": "HPSU CAN"}, sim.now + timedelta(hours=5))[0].endswith("zuletzt vor 5 h) – andere Regler abschalten, die ihn schreiben.")
    # After the episode only the regulation remains
    assert len(flow.summary_notes({"hp": "HPSU CAN"}, sim.now + timedelta(days=2))) == 1

    device = DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=3, name="HPSU CAN")
    summary = build_summary(SunflowConfig(devices=[device]), library.profiles, heat_pump_notes=notes)
    assert [i.message for i in summary.issues if i.level == "warning" and i.message in notes] == notes


def test_daily_counts_count_foreign_writes() -> None:
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    events = [
        {"at": "2026-11-21T08:00:00+00:00", "kind": "foreign_write", "value_c": 33.0},
        {"at": "2026-11-21T08:00:00+00:00", "kind": "foreign_write", "value_c": 33.0},
        {"at": "2026-11-21T09:00:00+00:00", "kind": "foreign_write", "value_c": 32.0},
    ]
    [today] = daily_counts(events, ZoneInfo("Europe/Berlin"), 1, NOW)

    # Counted, but not as Sunflow's writes
    assert (today["foreign_writes"], today["setpoint"]) == (2, 0)


async def test_recorder_reads_the_regulation_role(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        await pump.minutes(NOW, 2)
        pump.states["supply_setpoint_regulated"] = ("32.0", "°C")
        pump.states["max_target_flow_temp"] = ("32.5", "°C")
        await pump.minutes(NOW + 2 * MIN, 4)
        status = pump.recorder.status()["hp"]["flow"]["guard"]
        rows = pump.rows()
    finally:
        pump.store.close()

    assert "esp_regulation" in status["inactive"]
    assert status["foreign"]["regulation"]["active"] is True
    assert status["foreign"]["episode"]["value_c"] == 32.5
    assert status["setpoint_c"] == 32.5
    assert any(n.startswith("ESP-Regelung des Vorlaufs aktiv (32,0 °C)") for n in pump.notes())
    assert any(n.startswith("Max T-Vorlauf von außen geändert: 31,0 → 32,5 °C") for n in pump.notes())
    assert [e["value_c"] for row in rows for e in row["events"] if e["kind"] == "foreign_write"] == [32.5]
