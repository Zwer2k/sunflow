"""The set value for the HA sensors (design/heat_pump.md, 16): flow target, set value
written/pending with time, reason and why it is held back, writes today; one mark shadow | active. Nothing is
written to a heat pump: every heat pump runs in the shadow."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sunflow.heat_pump.budget import VirtualLog, WriteBudget
from sunflow.heat_pump.guard import Guard
from sunflow.heat_pump.shadow import FlowShadow, FlowState
from sunflow.heat_pump.state_store import HeatPumpStateStore
from test_heat_pump_guard import Sim

MIN = timedelta(minutes=1)
BERLIN = ZoneInfo("Europe/Berlin")


def _steady(written: float = 30.0) -> Sim:
    """Heating validly with Tv calm and the flow target at the set value: nothing to write."""
    sim = Sim(written=written)
    log = VirtualLog()
    log.seed([], sim.now)
    sim.budget = WriteBudget(log)
    sim.run([written] * 6)
    return sim


def _status(sim: Sim, tmp_path: Path, tz: Any = BERLIN, active: bool = False) -> dict[str, Any]:
    """setpoint_status of a FlowShadow that holds the simulated heat pump."""
    flow = FlowShadow(HeatPumpStateStore(tmp_path))
    flow.guards["hp"], flow.writers["hp"], flow.budgets["hp"] = sim.guard, sim.writer, sim.budget
    flow.flows["hp"] = FlowState(
        strategy="fixed", correction_k=0.0, target_c=sim.target, frozen=(), reason=None,
        guard=sim.results[-1][2] if sim.results else None,
    )
    flow.active["hp"] = active
    flow.tz = tz
    if sim.budget is not None:
        sim.budget.tz = tz
    flow._observed_at = sim.now - MIN
    return flow.setpoint_status("hp")


def test_written_value_and_pending_with_time_and_reason(tmp_path: Path) -> None:
    sim = _steady(30.0)
    sim.target = 32.0
    wrote_at = sim.now
    sim.minute(30.0)
    sim.target = 31.0
    sim.minute(30.0)

    status = _status(sim, tmp_path)
    assert status["mode"] == "shadow"
    assert (status["written_c"], status["written_at"], status["written_reason"]) == (32.0, wrote_at.isoformat(), "normal")
    assert (status["pending_c"], status["pending_reason"], status["held"]) == (31.0, "normal", "spacing")
    assert status["pending_at"] == (wrote_at + 5 * MIN).isoformat()
    assert status["held_budget"] is None
    # The virtual write counts for today
    assert status["writes_today"] == 1


def test_nothing_pending_nothing_held(tmp_path: Path) -> None:
    status = _status(_steady(30.0), tmp_path)

    # The heat pump's own value, no write seen yet: no time, no reason
    assert (status["written_c"], status["written_at"], status["written_reason"]) == (30.0, None, None)
    assert (status["pending_c"], status["pending_at"], status["pending_reason"], status["held"]) == (None, None, None, None)
    assert status["writes_today"] == 0


def test_a_foreign_change_is_taken_over_with_reason_foreign(tmp_path: Path) -> None:
    sim = _steady(30.0)
    sim.real_setpoint = 33.0
    changed_at = sim.now
    sim.minute(30.0)

    status = _status(sim, tmp_path)
    assert (status["written_c"], status["written_at"], status["written_reason"]) == (33.0, changed_at.isoformat(), "foreign")
    # Sunflow wants its value back, after the spacing from the change; not counted as Sunflow's write
    assert (status["pending_c"], status["held"]) == (30.0, "spacing")
    assert status["writes_today"] == 0


def test_held_by_the_budget_with_the_limit(tmp_path: Path) -> None:
    sim = _steady(30.0)
    sim.budget.limits = type(sim.budget.limits)(per_day=0, emergency_per_day=24, per_hour=6)
    sim.target = 32.0
    sim.minute(30.0)

    status = _status(sim, tmp_path)
    assert (status["pending_c"], status["pending_at"], status["held"], status["held_budget"]) == (32.0, None, "budget", "day")


def test_held_by_the_esp_regulation_and_by_the_block(tmp_path: Path) -> None:
    sim = _steady(30.0)
    sim.target = 35.0
    sim.extra = {"regulated_setpoint": 32.0}
    sim.minute(30.0)
    esp = _status(sim, tmp_path)

    blocked = Sim(written=30.0)
    blocked.room = 21.6
    blocked.minute(30.0)
    block = _status(blocked, tmp_path)

    assert (esp["pending_c"], esp["held"]) == (None, "esp_regulation")
    assert (block["pending_c"], block["held"]) == (None, "block")


def test_writes_today_unknown_until_counted_and_the_time_zone_is_known(tmp_path: Path) -> None:
    sim = _steady(30.0)
    assert _status(sim, tmp_path, tz=None)["writes_today"] is None

    unseeded = Sim(written=30.0)
    unseeded.budget = WriteBudget()
    unseeded.run([30.0] * 3)
    assert _status(unseeded, tmp_path)["writes_today"] is None


def test_the_local_day_counts(tmp_path: Path) -> None:
    sim = _steady(30.0)
    # A write yesterday (local time) and one today
    yesterday = datetime.combine(sim.now.astimezone(BERLIN).date(), datetime.min.time(), BERLIN) - timedelta(minutes=10)
    sim.budget.log.add(yesterday)
    sim.budget.log.add(sim.now - 30 * MIN)

    assert _status(sim, tmp_path)["writes_today"] == 1


def test_mode_active_while_writing_for_real(tmp_path: Path) -> None:
    status = _status(_steady(30.0), tmp_path, active=True)

    assert status["mode"] == "active"


def test_manual_write_has_reason_manual(tmp_path: Path) -> None:
    sim = _steady(30.0)
    sim.active = True
    sim.target = 32.0
    sim.minute(30.0)
    sim.target = 34.0
    sim.minute(30.0)
    sim.writer.request_now(sim.now)
    sim.minute(30.0)

    status = _status(sim, tmp_path, active=True)
    assert (status["written_c"], status["written_reason"], status["pending_c"]) == (34.0, "manual", None)


def test_without_a_guard_everything_is_unknown(tmp_path: Path) -> None:
    flow = FlowShadow(HeatPumpStateStore(tmp_path))

    status = flow.setpoint_status("hp")
    assert status["mode"] == "shadow"
    assert all(status[k] is None for k in status if k != "mode")


def test_written_reason_kept_in_the_state_file_and_derived_from_older_state() -> None:
    sim = _steady(30.0)
    sim.target = 32.0
    sim.minute(30.0)

    stored = sim.guard.as_dict()
    assert stored["written_reason"] == "normal"
    assert Guard(stored).written_reason == "normal"
    # State from before 0.80.17: the write at written_at in the recent writes
    old = {k: v for k, v in stored.items() if k != "written_reason"}
    assert Guard(old).written_reason == "normal"
    # Not among the recent writes (a foreign change then, or dropped): unknown
    assert Guard({**old, "recent_writes": []}).written_reason is None
    assert Guard({"written_c": 30.0}).written_reason is None


async def test_recorder_status_carries_the_set_value(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    now = datetime(2026, 11, 21, 10, tzinfo=UTC)
    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        await pump.minutes(now, 3)
        flow = pump.recorder.status()["hp"]["flow"]
    finally:
        pump.store.close()

    setpoint = flow["setpoint"]
    assert setpoint["mode"] == "shadow" and flow["shadow"] is True
    assert set(setpoint) == {
        "mode", "written_c", "written_at", "written_reason", "pending_c", "pending_at", "pending_reason", "held",
        "held_budget", "writes_today",
    }
    # Home Assistant's time zone not read (no client): writes today unknown, not 0
    assert setpoint["writes_today"] is None
