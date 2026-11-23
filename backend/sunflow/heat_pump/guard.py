"""Protection layer of the strategy fixed (design/heat_pump.md, 10, 11).

Turns the flow target into the set value Sunflow writes (Max T-Vorlauf) and into the writes it causes -
each with time, value and reason (normal, start, track, block_on, block_off). In the shadow nothing is
written to the heat pump: the "written" value here is the one Sunflow would have written last (virtual,
starting from the heat pump's Max T-Vorlauf). Writing for real (switched on by the user)
the shadow control (shadow.py) sends the writes to the heat pump and takes one back when it fails (undo).
The protection layer only says which value it wants and whether that is a protection step; when it is
written (spacing, deadband, budget, "write now") decides the write layer behind it (writer.py, 12).

Rules (11): Sunflow does not predict when the compressor switches; it reacts to it.
- Normal: while the compressor runs in heating, the set value is the flow target, written only when it
  differs by more than the deadband (valid heating only).
- Restart: the compressor has been off for RESTART_AFTER although heat is wanted (program heating, mode
  heat, no block, no hot water or defrost, room below its target + RESTART_DEMAND) - whatever stopped it,
  also after hot water or defrost. Then the set value is raised in one step to Tv + start margin (protection
  step "start").
- Lowering: after a start the set value is left alone for LOWER_AFTER, then it goes to the flow target in
  one step - not below the shutdown bound (peak of Tv − stop margin + swing), unless the stop margin is being
  learned (learning phase, chosen by the user): then straight to the flow target, so the heat pump shows
  where it stops; every stop is recorded (heat_pump_samples) and classified when learning (learned.py).
- While the shutdown bound applies and Tv rises above it, the set value follows it with a swing of headroom
  (protection step "track").
- Wanted off only by the block: the room well above its target; each state of the block lasts at least
  its minimum duration. A block Sunflow did not set is never released (11).
- Foreign writers (foreign.py): a change of the real Max T-Vorlauf Sunflow did not write is noted and the
  written value follows it; while the ESP regulation is active nothing is written.
Limits (10): the set value stays within [min, max]; at a limit (set value range, heat output range) the
correction does not move further beyond it (the direction is reported).
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from sunflow.i18n import number, t

from .defaults import (
    BLOCK_ROOM_ABOVE_K,
    GUARD_RECENT_MAX,
    LOWER_AFTER_S,
    OWN_WRITE_CONFIRM_S,
    PEAK_LOOKBACK_S,
    POWER_AT_LIMIT_SHARE,
    RESTART_AFTER_S,
    RESTART_DEMAND_K,
    ROOM_DEADBAND_K,
)
from .foreign import FOREIGN_EVENT, REGULATED_ROLE, ForeignWatch, regulation_active
from .learned import Values
from .limits import ControlSettings, clamp, round_near, round_up, setpoint_range
from .states import FIXED, HEATING, Evaluation, Reading
from .writer import Pending, Request

_LOGGER = logging.getLogger(__name__)
_EPS = 1e-6

HOT_WATER = "hot_water"
DEFROST = "defrost"
IDLE = "idle"
BUSY = (HOT_WATER, DEFROST)
# Reasons of a planned write
NORMAL_WRITE = "normal"
START = "start"
TRACK = "track"
BLOCK_ON = "block_on"
BLOCK_OFF = "block_off"
# Protection steps (12): shorter spacing, no deadband, emergency budget
PROTECT_REASONS = (START, TRACK)
WRITE_EVENT = "write"
# Why Sunflow wants the heat pump off (block)
ROOM_WARM = "room_warm"
# A block Sunflow did not set (found on start or set by someone else): never released by Sunflow (11)
FOREIGN = "foreign"
# Earlier names in heat_pump_state.json: a block found on start (0.80.10); blocks of the old rules (0.80.26)
_ADOPTED = "adopted"
_OLD_BLOCKS = ("min_power", "budget")
# Reason of the written value besides the write reasons: a manual write ("write now")
MANUAL = "manual"

# Phases (status, UI)
PHASE_RUNNING = "running"
PHASE_STARTING = "starting"
PHASE_OFF = "off"
PHASE_RESTART = "restart"
PHASE_IDLE = "idle"
PHASE_BLOCKED = "blocked"
PHASE_INACTIVE = "inactive"

RESTART_AFTER = timedelta(seconds=RESTART_AFTER_S)
LOWER_AFTER = timedelta(seconds=LOWER_AFTER_S)
PEAK_LOOKBACK = timedelta(seconds=PEAK_LOOKBACK_S)
# The real block follows Sunflow's own switch within this time (writing for real)
BLOCK_CONFIRM = timedelta(seconds=OWN_WRITE_CONFIRM_S)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _time(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass(frozen=True, kw_only=True)
class GuardInput:
    """One minute of one heat pump, with what the flow controller computed."""

    at: datetime
    reading: Reading
    evaluation: Evaluation
    settings: ControlSettings
    values: Values
    # Highest tv_max published since the last minute (events between the samples)
    tv_peak_c: float | None = None
    heat_w: float | None = None
    target_c: float | None = None
    room_c: float | None = None
    plan_c: float | None = None
    mode: str = "heat"
    surface: str | None = None
    # Learning phase of the stop margin (the user's switch): lowered straight to the flow target
    learn_stops: bool = False
    # Sunflow writes to this heat pump for real (write_step)
    writing: bool = False


@dataclass(kw_only=True)
class GuardResult:
    """What the protection layer would do now."""

    phase: str
    # Why nothing would be written at all (mode off, strategy, program, ...)
    inactive: tuple[str, ...] = ()
    setpoint_c: float | None = None
    desired_c: float | None = None
    # The lowest set value the rules allow now (shutdown bound) or the value of a restart
    floor_c: float | None = None
    limits: tuple[float, float] | None = None
    # Limits the flow target or the heat pump reached (limit_max, limit_min, power_max, power_min)
    limited: tuple[str, ...] = ()
    freeze_up: bool = False
    freeze_down: bool = False
    # Compressor off although heat is wanted: since when, and when Sunflow restarts it
    off_since: datetime | None = None
    restart_at: datetime | None = None
    writes: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    # The set value wanted this minute (for the write layer) and what it decided: a value waiting
    # (spacing, budget) and the outcome of a "write now"
    request: Request | None = None
    pending: Pending | None = None
    manual: dict[str, Any] | None = None
    # A foreign change of the set value this minute (foreign.py)
    foreign: dict[str, Any] | None = None
    # Journal entries (message, warn)
    notes: list[tuple[str, bool]] = field(default_factory=list)
    # The journal entry of each write in writes (taken back with it when writing for real fails)
    write_notes: list[tuple[dict[str, Any], tuple[str, bool]]] = field(default_factory=list)


class Guard:
    """The protection layer of one heat pump; its state is a dict kept in heat_pump_state.json."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        """Start from the stored state (unreadable parts and those of the old rules are dropped)."""
        state = state if isinstance(state, dict) else {}
        self.written_c = _float(state.get("written_c"))
        self.written_at = _time(state.get("written_at"))
        self.block: bool | None = state.get("block") if isinstance(state.get("block"), bool) else None
        self.block_at = _time(state.get("block_at"))
        self.block_reason: str | None = state.get("block_reason") if isinstance(state.get("block_reason"), str) else None
        if self.block_reason == _ADOPTED:
            self.block_reason = FOREIGN
        elif self.block_reason in _OLD_BLOCKS:
            # A block of the old rules: released by the room rule after its minimum duration
            self.block_reason = ROOM_WARM
        self.recent_writes: list[dict[str, Any]] = [w for w in state.get("recent_writes") or [] if isinstance(w, dict)]
        # Why the written value is what it is (HA sensor "Stellwert", 16): normal, start, track,
        # manual or foreign; None while it is the heat pump's own value (no write seen yet)
        self.written_reason: str | None = (
            state["written_reason"] if isinstance(state.get("written_reason"), str) else self._reason_from_writes()
        )
        # A restart held back by the budget was noted (once per episode)
        self.unprotected = bool(state.get("unprotected"))
        # Foreign writers and the ESP regulation (12)
        self.foreign = ForeignWatch(state.get("foreign"))
        # In memory only: Tv of the running minutes (time, peak), when the compressor started running in
        # heating and since when it is off although heat is wanted
        self._peaks: deque[tuple[datetime, float]] = deque()
        self.run_since: datetime | None = None
        self.off_since: datetime | None = None
        self._seen = False
        self.last: GuardResult | None = None
        # Writing for real this minute and when Sunflow last switched the real block
        self._writing = False
        self.block_written_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """For heat_pump_state.json."""
        return {
            "written_c": self.written_c,
            "written_at": _iso(self.written_at) if self.written_at else None,
            "written_reason": self.written_reason,
            "block": self.block,
            "block_at": _iso(self.block_at) if self.block_at else None,
            "block_reason": self.block_reason,
            "recent_writes": self.recent_writes[-GUARD_RECENT_MAX:],
            "unprotected": self.unprotected,
            "foreign": self.foreign.as_dict(),
        }

    def _reason_from_writes(self) -> str | None:
        """State from before 0.80.17: the reason of the latest write of the set value at written_at, if kept."""
        if self.written_at is None:
            return None
        at = _iso(self.written_at)
        for event in reversed(self.recent_writes):
            if event.get("reason") in (NORMAL_WRITE, START, TRACK) and event.get("at") == at:
                return MANUAL if event.get("manual") else event["reason"]
        return None

    # Tv over the last minutes

    def _remember(self, now: datetime, peak: float | None) -> None:
        if peak is not None:
            self._peaks.append((now, peak))
        while self._peaks and now - self._peaks[0][0] > PEAK_LOOKBACK:
            self._peaks.popleft()

    def _peak(self) -> float | None:
        return max((p for _, p in self._peaks), default=None)

    # One minute

    def step(self, data: GuardInput) -> GuardResult:
        """Follow the heat pump for one minute and decide what Sunflow wants written."""
        reading, settings, now = data.reading, data.settings, data.at
        self._writing = data.writing
        tv = reading.values.get("flow_temperature")
        peak_now = max((v for v in (tv, data.tv_peak_c, reading.values.get("flow_temperature_max")) if v is not None), default=None)
        running = reading.activity == HEATING and reading.compressor is True
        self._follow_run(running, reading, now)
        # Only Tv of the heating circuit with the compressor running counts for the shutdown bound
        self._remember(now, peak_now if running else None)
        result = GuardResult(phase=reading.activity)
        low, high = setpoint_range(data.surface, settings)
        result.limits = (low, high)
        if self.written_c is None and (current := reading.values.get("max_flow_setpoint")) is not None:
            # Sunflow starts from what the heat pump has, without a write
            self.written_c = current
        self._follow_writers(data, result)
        self._follow_block(reading, result, now)
        self._limits(data, result, low, high)
        inactive = self._inactive(data, tv)
        result.inactive = tuple(inactive)
        if inactive:
            result.phase = PHASE_INACTIVE
            self.off_since = None
        else:
            self._decide(data, result, tv, running, low, high)
        result.setpoint_c = self.written_c
        self._seen = True
        self.last = result
        return result

    def _follow_run(self, running: bool, reading: Reading, now: datetime) -> None:
        """When the compressor started running in heating; since when it is off outside hot water and defrost."""
        if running:
            if self.run_since is None:
                # The first minute after Sunflow started: how long it runs is not known, it counts as settled
                self.run_since = now if self._seen else now - LOWER_AFTER
            self.off_since = None
            return
        self.run_since = None
        if reading.activity in BUSY or reading.compressor is not False:
            self.off_since = None
        elif self.off_since is None:
            self.off_since = now

    def _follow_writers(self, data: GuardInput, result: GuardResult) -> None:
        """A change of the real set value Sunflow did not write: noted, and the written value follows it.

        The spacing of the next write runs from the change (written_at), so Sunflow does not answer it at
        once; the change does not count against Sunflow's budget."""
        values, now = data.reading.values, data.at
        actual = values.get("max_flow_setpoint")
        seen = self.foreign.step(now, actual, values.get(REGULATED_ROLE), data.settings.step, self.written_c)
        if seen.change is not None and actual is not None:
            self.written_c, self.written_at, self.written_reason = actual, now, FOREIGN
            # With the activity of the heat pump at the change
            event = {**seen.change, "activity": data.reading.activity, "shadow": not data.writing}
            result.events.append(event)
            result.foreign = event
        result.notes.extend(seen.notes)

    def _follow_block(self, reading: Reading, result: GuardResult, now: datetime) -> None:
        """A block Sunflow did not set is kept and only shown (status, UI, journal), never released (11).

        In the shadow Sunflow writes nothing, so every real block is someone else's. While Sunflow writes for
        real a block it set itself is its own: the real state may take BLOCK_CONFIRM to
        follow Sunflow's switch; after that a real state other than Sunflow's is someone else's doing - a
        block set by someone else is foreign, Sunflow's block released by someone else is released.
        """
        if reading.blocked is None:
            return
        if self._writing and self.block_reason != FOREIGN and self.block is not None:
            if reading.blocked == self.block:
                return
            if self.block_written_at is not None and timedelta(0) <= now - self.block_written_at <= BLOCK_CONFIRM:
                # Sunflow's own switch not shown yet
                return
            if not reading.blocked:
                self.block, self.block_at, self.block_reason = False, now, None
                result.notes.append((t("Raumthermostat-Sperre von außen aufgehoben, nicht von Sunflow."), True))
                return
        if reading.blocked and self.block_reason != FOREIGN:
            self.block, self.block_at, self.block_reason = True, now, FOREIGN
            result.notes.append((t("Raumthermostat-Sperre nicht von Sunflow gesetzt: Sunflow hebt sie nicht auf und regelt nicht, bis sie aufgehoben ist."), True))
        elif not reading.blocked and self.block_reason == FOREIGN:
            # Released by someone else; the minimum duration runs from here before Sunflow sets its own
            self.block, self.block_at, self.block_reason = False, now, None
        elif self.block is None:
            self.block = False

    @staticmethod
    def _limits(data: GuardInput, result: GuardResult, low: float, high: float) -> None:
        """Which limit the flow target or the heat pump reached; the correction does not move beyond it."""
        limited = []
        target = data.target_c
        if target is not None:
            if target >= high - _EPS:
                limited.append("limit_max")
                result.freeze_up = True
            if target <= low + _EPS:
                limited.append("limit_min")
                result.freeze_down = True
            result.desired_c = clamp(round_near(target, data.settings.step), low, high, data.settings.step)
        if data.reading.compressor and data.reading.activity == HEATING and data.heat_w is not None:
            if data.heat_w >= data.values.power_max_w * (1 - POWER_AT_LIMIT_SHARE):
                limited.append("power_max")
                result.freeze_up = True
            if data.heat_w <= data.values.power_min_w * (1 + POWER_AT_LIMIT_SHARE):
                limited.append("power_min")
                result.freeze_down = True
        result.limited = tuple(limited)

    @staticmethod
    def _inactive(data: GuardInput, tv: float | None) -> list[str]:
        """Why Sunflow would write nothing now (12: mode off, strategy, program, activity, device)."""
        reading, frozen = data.reading, set(data.evaluation.frozen)
        reasons = []
        if data.mode != "heat":
            reasons.append("mode_off")
        if reading.strategy != FIXED:
            reasons.append(f"strategy_{reading.strategy}")
        if "unusable" in frozen:
            reasons.append("unusable")
        if reading.program != HEATING:
            reasons.append(f"program_{reading.program}")
        if reading.activity not in (HEATING, IDLE, HOT_WATER, DEFROST):
            reasons.append(f"activity_{reading.activity}")
        if reading.fault:
            reasons.append("fault")
        if tv is None:
            reasons.append("tv_missing")
        if reading.values.get("max_flow_setpoint") is None:
            reasons.append("setpoint_missing")
        if regulation_active(reading.values.get(REGULATED_ROLE)):
            # The ESP component regulates Max T-Vorlauf itself (12): no takeover
            reasons.append("esp_regulation")
        return reasons

    @staticmethod
    def _request(result: GuardResult, value: float, reason: str) -> None:
        """The set value wanted now; the write layer decides when it is written (12)."""
        result.request = Request(value_c=value, reason=reason, protect=reason in PROTECT_REASONS)

    @staticmethod
    def heat_wanted(data: GuardInput) -> bool:
        """The rooms want heat: below their target + RESTART_DEMAND (unknown rooms: yes)."""
        return data.room_c is None or data.plan_c is None or data.room_c < data.plan_c + RESTART_DEMAND_K - _EPS

    def bound(self, data: GuardInput, low: float, high: float) -> float | None:
        """Lowest set value that keeps the compressor running: peak of Tv − stop margin + swing; None while
        the stop margin is being learned or without a Tv of the running compressor."""
        peak = self._peak()
        if data.learn_stops or peak is None:
            return None
        step = data.settings.step
        return clamp(round_up(peak - data.values.stop_margin + data.values.steady_swing, step), low, high, step)

    def _decide(self, data: GuardInput, result: GuardResult, tv: float | None, running: bool, low: float, high: float) -> None:
        now, step = data.at, data.settings.step
        assert tv is not None and self.written_c is not None
        self._block(data, result)
        if self.block:
            result.phase = PHASE_BLOCKED
            self.off_since = None
            return
        activity = data.reading.activity
        if activity in BUSY:
            result.phase = activity
            return
        if not running:
            if not self.heat_wanted(data) or self.off_since is None:
                result.phase = PHASE_IDLE
                return
            result.off_since = self.off_since
            result.restart_at = self.off_since + RESTART_AFTER
            result.floor_c = clamp(round_up(tv + data.values.start_margin, step), low, high, step)
            if now < result.restart_at:
                result.phase = PHASE_OFF
                return
            result.phase = PHASE_RESTART
            if result.floor_c > self.written_c + _EPS:
                self._request(result, result.floor_c, START)
            return
        bound = self.bound(data, low, high)
        result.floor_c = bound
        settling = self.run_since is not None and now - self.run_since < LOWER_AFTER
        result.phase = PHASE_STARTING if settling else PHASE_RUNNING
        if bound is not None and bound > self.written_c + _EPS:
            # Tv rises towards the shutdown: the set value follows, a swing above the bound so a slowly rising
            # Tv does not cause a write every few minutes
            self._request(result, clamp(round_up(bound + data.values.steady_swing, step), low, high, step), TRACK)
            return
        if settling or result.desired_c is None or not data.evaluation.valid:
            return
        self._request(result, max(result.desired_c, bound) if bound is not None else result.desired_c, NORMAL_WRITE)

    # Writing for real

    def adopt(self, actual_c: float | None, blocked: bool | None, now: datetime) -> None:
        """Sunflow takes over: it starts from what the heat pump has, not from the virtual values of the shadow.

        The written value is the real Max T-Vorlauf (no write of Sunflow's yet, so no time and no reason: the
        next write goes when the rules ask for it); the block is the real one - set, it is someone else's
        (Sunflow did not set it) and stays until someone else releases it (11)."""
        if actual_c is not None:
            self.written_c = actual_c
            self.foreign.seen_c = actual_c
        self.written_at, self.written_reason = None, None
        self.foreign.expect_c = self.foreign.expect_at = None
        if blocked is True:
            self.block, self.block_at, self.block_reason = True, now, FOREIGN
        elif blocked is False:
            self.block, self.block_at, self.block_reason = False, None, None
        else:
            self.block, self.block_at, self.block_reason = None, None, None
        self.block_written_at = None
        self.unprotected = False

    def release(self) -> bool:
        """Writing switched off: nothing is reset (4). A block Sunflow set stays at the heat pump and is
        someone else's from now on (the shadow never releases a real block). True when one stays set."""
        self.block_written_at = None
        if self.block and self.block_reason != FOREIGN:
            self.block_reason = FOREIGN
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        """What a real write changes, to take it back when the write fails."""
        return {
            "written": (self.written_c, self.written_at, self.written_reason),
            "expect": (self.foreign.expect_c, self.foreign.expect_at),
            "block": (self.block, self.block_at, self.block_reason, self.block_written_at),
        }

    def undo(self, result: GuardResult, event: dict[str, Any], before: dict[str, Any]) -> None:
        """A real write failed: the event, its journal entry and its effect on the state are taken back."""
        if event.get("reason") in (BLOCK_ON, BLOCK_OFF):
            self.block, self.block_at, self.block_reason, self.block_written_at = before["block"]
        else:
            self.written_c, self.written_at, self.written_reason = before["written"]
            self.foreign.expect_c, self.foreign.expect_at = before["expect"]
            result.setpoint_c = self.written_c
        result.writes = [w for w in result.writes if w is not event]
        self.recent_writes = [w for w in self.recent_writes if w is not event]
        for written, note in [(w, n) for w, n in result.write_notes if w is event]:
            if note in result.notes:
                result.notes.remove(note)
        result.write_notes = [(w, n) for w, n in result.write_notes if w is not event]

    def not_adopted(self, actual_c: float) -> None:
        """The heat pump does not show the value Sunflow wrote: Sunflow goes on from the value it shows."""
        self.written_c = actual_c
        self.written_reason = None
        self.foreign.seen_c = actual_c
        self.foreign.expect_c = self.foreign.expect_at = None

    def record_write(self, result: GuardResult, now: datetime, value: float, reason: str, *, shadow: bool = True, manual: bool = False) -> None:
        """The write layer wrote the set value (in the shadow virtually)."""
        event = {"at": _iso(now), "kind": WRITE_EVENT, "reason": reason, "value_c": value, "from_c": self.written_c,
                 "phase": result.phase, "shadow": shadow}
        if manual:
            event["manual"] = True
        if not shadow:
            # The change of the real value to it is Sunflow's own, not a foreign one
            self.foreign.expect(value, now)
        self.written_c, self.written_at = value, now
        self.written_reason = MANUAL if manual else reason
        self.unprotected = False
        result.setpoint_c = value
        result.writes.append(event)
        self.recent_writes.append(event)
        del self.recent_writes[:-GUARD_RECENT_MAX]

    def _switch_block(self, result: GuardResult, now: datetime, on: bool, reason: str | None, why: str) -> None:
        event = {"at": _iso(now), "kind": WRITE_EVENT, "reason": BLOCK_ON if on else BLOCK_OFF, "block": on,
                 "block_reason": reason, "phase": result.phase, "shadow": not self._writing}
        self.block, self.block_at, self.block_reason = on, now, reason
        if self._writing:
            self.block_written_at = now
        result.writes.append(event)
        self.recent_writes.append(event)
        del self.recent_writes[:-GUARD_RECENT_MAX]
        if self._writing:
            message = (t("Raumthermostat-Sperre gesetzt ({why}).", why=why) if on
                       else t("Raumthermostat-Sperre aufgehoben ({why}).", why=why))
        else:
            message = (t("Nur Lernen: Sunflow würde die Raumthermostat-Sperre setzen ({why}).", why=why) if on
                       else t("Nur Lernen: Sunflow würde die Raumthermostat-Sperre aufheben ({why}).", why=why))
        result.notes.append((message, False))
        result.write_notes.append((event, (message, False)))

    def _block(self, data: GuardInput, result: GuardResult) -> None:
        """Wanted off: the room well above its target sets the block; released after the minimum duration."""
        if not data.settings.has_block or self.block_reason == FOREIGN:
            return
        now, room, plan = data.at, data.room_c, data.plan_c
        may_switch = self.block_at is None or now - self.block_at >= timedelta(seconds=data.settings.block_min_s)
        if not may_switch:
            return
        if self.block:
            if room is None or plan is None or room <= plan + ROOM_DEADBAND_K + _EPS:
                why = t("Raum {room} °C, Ziel {plan} °C", room=number(room), plan=number(plan)) if room is not None and plan is not None else t("keine Raumtemperatur")
                self._switch_block(result, now, False, None, why)
        elif room is not None and plan is not None and room >= plan + BLOCK_ROOM_ABOVE_K - _EPS:
            self._switch_block(result, now, True, ROOM_WARM, t("Raum {room} °C, {k} K über dem Ziel {plan} °C", room=number(room), k=number(room - plan), plan=number(plan)))

    def budget_unprotected(self, data: GuardInput) -> bool:
        """True the first time in an episode that a protection step is held back by the budget."""
        if self.unprotected:
            return False
        self.unprotected = True
        return True


# Daily counts for the user: writes Sunflow caused (or would have) and foreign writes

COUNTED_REASONS = (NORMAL_WRITE, START, TRACK, BLOCK_ON, BLOCK_OFF)


def daily_counts(events: list[dict[str, Any]], tz: Any, days: int, now: datetime) -> list[dict[str, Any]]:
    """Per local day (newest first, days of them): writes by reason, compressor stops and foreign writes."""
    today = now.astimezone(tz).date()
    counts: dict[Any, dict[str, Any]] = {}
    for back in range(days):
        day = today - timedelta(days=back)
        counts[day] = {"date": day.isoformat(), **dict.fromkeys(COUNTED_REASONS, 0), "stops": 0, "foreign_writes": 0}
    seen: set[tuple[Any, ...]] = set()
    for event in events:
        if not isinstance(event, dict) or (at := _time(event.get("at"))) is None:
            continue
        key = (event.get("at"), event.get("kind"), event.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        day = counts.get(at.astimezone(tz).date())
        if day is None:
            continue
        if event.get("kind") == WRITE_EVENT and event.get("reason") in COUNTED_REASONS:
            day[event["reason"]] += 1
        elif event.get("kind") == "stop" and event.get("activity") in (None, HEATING):
            # Compressor stops in heating (heat_pump_samples), whatever the reason
            day["stops"] += 1
        elif event.get("kind") == FOREIGN_EVENT:
            # Changes of the real set value by someone else (12); not Sunflow's budget
            day["foreign_writes"] += 1
    for day in counts.values():
        # Normal budget and emergency budget (12): normal writes; restart and tracking (protection steps)
        day["setpoint"] = day[NORMAL_WRITE] + day[START] + day[TRACK]
        day["protect"] = day[START] + day[TRACK]
        day["block"] = day[BLOCK_ON] + day[BLOCK_OFF]
    return [counts[day] for day in sorted(counts, reverse=True)]
