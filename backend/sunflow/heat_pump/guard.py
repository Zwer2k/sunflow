"""Protection layer of the strategy fixed (design/heat_pump.md, 8.3, 8.4).

Turns the flow target into the set value Sunflow writes (Max T-Vorlauf) and into the writes it causes -
each with time, value and reason (normal, start, track, block_on, block_off). In the shadow nothing is
written to the heat pump: the "written" value here is the one Sunflow would have written last (virtual,
starting from the heat pump's Max T-Vorlauf), the block is the room thermostat input as Sunflow would
have set it. Writing for real (step 3 part 4.7, switched on by the user) the shadow control (shadow.py)
sends the writes to the heat pump and takes one back when it fails (undo). The protection layer only says which value it wants and whether that is a
protection step; when it is written (spacing, deadband, "write now") decides the write layer behind it
(writer.py, 8.5, step 3 part 4.2), within the budget (budget.py, part 4.3).

Rules (8.4), with the learned values (learned.py; start values until there are enough data):
- Start: Sunflow wants heat (no block) and the compressor is off → set value ≥ max(Tv + start_margin,
  Tv + start_jump − 2.9) (2.9 = stop margin − one tenth).
- Prepare before the end of hot water: during hot water the set value ≥ Tv before hot water +
  start_jump (after hot water) − 2.9; likewise between the end of hot water or defrost and the start.
- Hold after a start: not lowered until start_settle passed and Tv is calm.
- Then lowered once: max(flow target, Tv + steady_swing − 2.9), when that changes it by more than the
  deadband - like every normal write, only in valid heating and with Tv calm.
- Track in running operation: when Tv + steady_swing reaches the shutdown limit, raise (a headroom
  above the bound); when the minimum output exceeds the heat needed, the block instead.
- Wanted off only by the block: the room well above its target, or minimum output above the need;
  each state of the block lasts at least its minimum duration.
- A block Sunflow did not set (found on start, set by hand, automation or another controller) is not
  released: phase blocked, journal, shown in status and UI, until someone else releases it (8.6).
- Budget used up for a protection step (emergency quota or hourly limit): the block instead of the
  write (reason budget), released after its minimum duration once a protection step may be written again.
- Foreign writers (foreign.py, step 3 part 4.4): a change of the real Max T-Vorlauf Sunflow did not write
  is noted (journal once per episode, status, summary) and the written value follows it; while the ESP
  regulation is active nothing is written (inactive esp_regulation).
- Unwanted shutdown: the compressor stops in heating at Tv ≥ effective setpoint + 3 K without block,
  hot water or defrost → journal; the value concerned (jump of that start, else the swing) is raised.
Limits (8.3): the set value stays within [min, max]; a flow target beyond the plausibility band is not
written; at a limit (set value range, heat output range, plausibility) the correction does not move
further beyond it (the direction is reported).
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from sunflow.i18n import number, t

from .defaults import (
    BLOCK_ROOM_ABOVE_K,
    BUMP_EXTRA_K,
    GUARD_RECENT_MAX,
    OWN_WRITE_CONFIRM_S,
    POWER_AT_LIMIT_SHARE,
    ROOM_DEADBAND_K,
    START_AFTER_S,
    START_OBSERVE_MAX_S,
    STOP_LOOKBACK_S,
    STOP_TOLERANCE_K,
    TRACK_HEADROOM_K,
    TRACK_LOOKBACK_S,
    TV_FLAT_WINDOW_S,
)
from .foreign import FOREIGN_EVENT, REGULATED_ROLE, ForeignWatch, regulation_active
from .learned import NORMAL, START_JUMP_EVENT, Values
from .limits import ControlSettings, clamp, implausible, round_near, round_up, setpoint_range
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
# Protection steps (8.5): shorter spacing, no deadband
PROTECT_REASONS = (START, TRACK)
WRITE_EVENT = "write"
STOP_EVENT = "unwanted_stop"
# Why Sunflow wants the heat pump off (block)
ROOM_WARM = "room_warm"
MIN_POWER = "min_power"
# The budget holds a protection step back (8.5): the block instead
BUDGET_BLOCK = "budget"
# A block Sunflow did not set (found on start or set by someone else): never released by Sunflow (8.6)
FOREIGN = "foreign"
# Earlier name in heat_pump_state.json (0.80.10)
_ADOPTED = "adopted"
# Reason of the written value besides the write reasons: a manual write ("write now")
MANUAL = "manual"

FLAT = timedelta(seconds=TV_FLAT_WINDOW_S)
TRACK_LOOKBACK = timedelta(seconds=TRACK_LOOKBACK_S)
STOP_LOOKBACK = timedelta(seconds=STOP_LOOKBACK_S)
START_AFTER = timedelta(seconds=START_AFTER_S)
# The real block follows Sunflow's own switch within this time (writing for real, step 3 part 4.7)
BLOCK_CONFIRM = timedelta(seconds=OWN_WRITE_CONFIRM_S)
OBSERVE_MAX = timedelta(seconds=START_OBSERVE_MAX_S)
# Tv kept in memory: enough for every window above
HISTORY = max(FLAT, TRACK_LOOKBACK, STOP_LOOKBACK) + timedelta(minutes=5)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _time(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass(kw_only=True)
class Start:
    """A start being followed: its kind, the Tv it is measured from, its peak and lower bound."""

    kind: str
    at: datetime
    ref_c: float
    peak_c: float
    floor_c: float
    # Tv calm again since (start_settle measured); the start was noted in events
    settled_at: datetime | None = None
    noted: bool = False
    # Holding ended (lowered once may follow)
    held: bool = True

    def as_dict(self) -> dict[str, Any]:
        """For the state file and the status."""
        return {
            "kind": self.kind, "at": _iso(self.at), "ref_c": round(self.ref_c, 2), "peak_c": round(self.peak_c, 2),
            "floor_c": round(self.floor_c, 2), "settled_at": _iso(self.settled_at) if self.settled_at else None,
            "noted": self.noted, "held": self.held,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Start | None":
        """From the state file; None if unreadable."""
        try:
            return cls(
                kind=str(data["kind"]), at=datetime.fromisoformat(data["at"]), ref_c=float(data["ref_c"]),
                peak_c=float(data["peak_c"]), floor_c=float(data["floor_c"]), settled_at=_time(data.get("settled_at")),
                noted=bool(data.get("noted")), held=bool(data.get("held", True)),
            )
        except (KeyError, TypeError, ValueError):
            return None


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
    curve_c: float | None = None
    heat_needed_w: float | None = None
    room_c: float | None = None
    plan_c: float | None = None
    mode: str = "heat"
    surface: str | None = None
    # The budget would let a protection step be written now (write_step); releases a budget block
    protect_budget: bool = True
    # Sunflow writes to this heat pump for real (write_step; not before step 3 part 4.7)
    writing: bool = False


@dataclass(kw_only=True)
class GuardResult:
    """What the protection layer would do now."""

    phase: str
    # Why nothing would be written at all (mode off, strategy, program, ...)
    inactive: tuple[str, ...] = ()
    setpoint_c: float | None = None
    desired_c: float | None = None
    floor_c: float | None = None
    limits: tuple[float, float] | None = None
    # Limits the flow target or the heat pump reached (limit_max, limit_min, power_max, power_min, implausible)
    limited: tuple[str, ...] = ()
    freeze_up: bool = False
    freeze_down: bool = False
    implausible_k: float | None = None
    calm: bool = False
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
        """Start from the stored state (unreadable parts are dropped)."""
        state = state if isinstance(state, dict) else {}
        self.written_c = _float(state.get("written_c"))
        self.written_at = _time(state.get("written_at"))
        self.block: bool | None = state.get("block") if isinstance(state.get("block"), bool) else None
        self.block_at = _time(state.get("block_at"))
        self.block_reason: str | None = state.get("block_reason") if isinstance(state.get("block_reason"), str) else None
        if self.block_reason == _ADOPTED:
            self.block_reason = FOREIGN
        before = state.get("before") if isinstance(state.get("before"), dict) else {}
        self.before: dict[str, float] = {k: v for k, v in before.items() if k in BUSY and isinstance(v, (int, float))}
        ended = state.get("ended") if isinstance(state.get("ended"), dict) else {}
        self.ended: tuple[str, datetime] | None = (
            (ended["kind"], at) if ended.get("kind") in BUSY and (at := _time(ended.get("at"))) is not None else None
        )
        self.start = Start.from_dict(state.get("start")) if state.get("start") else None
        self.bumps: dict[str, Any] = state.get("bumps") if isinstance(state.get("bumps"), dict) else {}
        self.recent_writes: list[dict[str, Any]] = [w for w in state.get("recent_writes") or [] if isinstance(w, dict)]
        # Why the written value is what it is (HA sensor "Stellwert", step 3 part 4.6): normal, start, track,
        # manual or foreign; None while it is the heat pump's own value (no write seen yet)
        self.written_reason: str | None = (
            state["written_reason"] if isinstance(state.get("written_reason"), str) else self._reason_from_writes()
        )
        self.recent_stops: list[dict[str, Any]] = [s for s in state.get("recent_stops") or [] if isinstance(s, dict)]
        self.implausible = bool(state.get("implausible"))
        # A protection step held back by the budget without a block possible was noted (once per episode)
        self.unprotected = bool(state.get("unprotected"))
        # Foreign writers and the ESP regulation (8.5, step 3 part 4.4)
        self.foreign = ForeignWatch(state.get("foreign"))
        # In memory only: Tv of the last minutes (time, Tv, peak) and the previous minute
        self._history: deque[tuple[datetime, float, float]] = deque()
        self._activity: str | None = None
        self._compressor: bool | None = None
        self._running = False
        self._effective: float | None = None
        self._circuit_tv: float | None = None
        self._prev_tv: float | None = None
        self.last: GuardResult | None = None
        # Writing for real this minute (step 3 part 4.7) and when Sunflow last switched the real block
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
            "before": {k: round(v, 2) for k, v in self.before.items()},
            "ended": {"kind": self.ended[0], "at": _iso(self.ended[1])} if self.ended else None,
            "start": self.start.as_dict() if self.start else None,
            "bumps": self.bumps,
            "recent_writes": self.recent_writes[-GUARD_RECENT_MAX:],
            "recent_stops": self.recent_stops[-GUARD_RECENT_MAX:],
            "implausible": self.implausible,
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

    def _remember(self, now: datetime, tv: float | None, peak: float | None) -> None:
        if tv is not None:
            self._history.append((now, tv, max(tv, peak) if peak is not None else tv))
        while self._history and now - self._history[0][0] > HISTORY:
            self._history.popleft()

    def _peak(self, now: datetime, window: timedelta) -> float | None:
        peaks = [p for at, _, p in self._history if now - at <= window]
        return max(peaks) if peaks else None

    def _calm(self, now: datetime, swing: float, since: datetime | None = None) -> bool:
        """Tv moved by at most swing over the last FLAT window, which must be known (and after since)."""
        start = now - FLAT
        if since is not None and since > start:
            return False
        inside = [(tv, p) for at, tv, p in self._history if at >= start - timedelta(seconds=1)]
        if not self._history or self._history[0][0] > start + timedelta(seconds=61) or len(inside) < 2:
            return False
        values = [tv for tv, _ in inside]
        return max(max(values), max(p for _, p in inside)) - min(values) <= swing + _EPS

    def _mean_tv(self, now: datetime, window: timedelta) -> float | None:
        values = [tv for at, tv, _ in self._history if timedelta(0) <= now - at <= window]
        return sum(values) / len(values) if values else None

    # One minute

    def step(self, data: GuardInput) -> GuardResult:
        """Follow the heat pump for one minute and decide what Sunflow would write."""
        reading, settings, values, now = data.reading, data.settings, data.values, data.at
        self._writing = data.writing
        tv = reading.values.get("flow_temperature")
        peak_now = max((v for v in (tv, data.tv_peak_c, reading.values.get("flow_temperature_max")) if v is not None), default=None)
        running = reading.activity == HEATING and reading.compressor is True
        # Only Tv of the heating circuit with the compressor running counts for calm, peaks and swing
        self._remember(now, tv if running else None, peak_now)
        result = GuardResult(phase=reading.activity)
        low, high = setpoint_range(data.surface, settings)
        result.limits = (low, high)
        if self.written_c is None and (current := reading.values.get("max_flow_setpoint")) is not None:
            # Sunflow starts from what the heat pump has, without a write
            self.written_c = current
        self._follow_writers(data, result)
        self._follow_block(reading, result, now)

        self._follow_activity(reading, now, tv)
        self._detect_stop(data, result, tv)
        self._follow_start(data, result, tv, peak_now)

        guard = settings.shutdown_guard
        swing = values.steady_swing
        result.calm = self._calm(now, swing)
        self._limits(data, result, low, high)

        inactive = self._inactive(data, tv)
        result.inactive = tuple(inactive)
        if inactive:
            result.phase = "inactive"
        else:
            self._decide(data, result, tv, low, high, guard, swing)
        result.setpoint_c = self.written_c
        self._activity, self._compressor = reading.activity, reading.compressor
        self._running = reading.activity == HEATING and reading.compressor is True
        self._effective = reading.values.get("flow_setpoint")
        self._prev_tv = tv
        self.last = result
        return result

    def _follow_writers(self, data: GuardInput, result: GuardResult) -> None:
        """A change of the real set value Sunflow did not write: noted, and the written value follows it.

        The spacing of the next write runs from the change (written_at), so Sunflow does not answer it at
        once; the change does not count against Sunflow's budget."""
        values, now = data.reading.values, data.at
        actual = values.get("max_flow_setpoint")
        seen = self.foreign.step(now, actual, values.get(REGULATED_ROLE), data.settings.step, self.written_c)
        if seen.change is not None and actual is not None:
            self.written_c, self.written_at, self.written_reason = actual, now, FOREIGN
            # With the activity: a change during hot water answers open point 8 (15)
            event = {**seen.change, "activity": data.reading.activity, "shadow": not data.writing}
            result.events.append(event)
            result.foreign = event
        result.notes.extend(seen.notes)

    def _follow_block(self, reading: Reading, result: GuardResult, now: datetime) -> None:
        """A block Sunflow did not set is kept and only shown (status, UI, journal), never released (8.6).

        In the shadow Sunflow writes nothing, so every real block is someone else's. While Sunflow writes for
        real (step 3 part 4.7) a block it set itself is its own: the real state may take BLOCK_CONFIRM to
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

    def _follow_activity(self, reading: Reading, now: datetime, tv: float | None) -> None:
        """Tv before hot water or defrost, and when they ended."""
        activity, previous = reading.activity, self._activity
        if activity in BUSY and previous != activity and self._circuit_tv is not None:
            self.before[activity] = self._circuit_tv
        if previous in BUSY and activity != previous:
            self.ended = (previous, now)
        if activity in (HEATING, IDLE) and tv is not None and not reading.bypass:
            self._circuit_tv = tv

    def _detect_stop(self, data: GuardInput, result: GuardResult, tv: float | None) -> None:
        """An unwanted shutdown: the compressor stopped in heating at Tv ≥ setpoint + stop margin."""
        reading, now = data.reading, data.at
        if not (self._compressor is True and reading.compressor is False):
            return
        if self._activity != HEATING or reading.activity in BUSY or reading.blocked or self._effective is None:
            return
        # Peaks of the running minutes before and what was published since the last minute
        seen = [p for p in (self._peak(now, STOP_LOOKBACK), data.tv_peak_c, data.reading.values.get("flow_temperature_max")) if p is not None]
        peak = max(seen) if seen else None
        limit = self._effective + data.settings.stop_margin
        if peak is None or peak < limit - STOP_TOLERANCE_K - _EPS:
            return
        starting = self.start if self.start is not None and now - self.start.at <= OBSERVE_MAX else None
        if starting is not None:
            name, seen = f"start_jump.{starting.kind}", peak - starting.ref_c
            current = data.values.start_jump.get(starting.kind, 0.0)
        else:
            mean = self._mean_tv(now, FLAT)
            name, seen = "steady_swing", peak - (mean if mean is not None else peak)
            current = data.values.steady_swing
        raised = round(max(seen, 0.0) + BUMP_EXTRA_K, 2)
        if raised > current + _EPS:
            self.bumps[name] = {"k": raised, "at": _iso(now)}
        event = {
            "at": _iso(now), "kind": STOP_EVENT, "tv_c": tv, "peak_c": round(peak, 2), "setpoint_c": self._effective,
            "max_setpoint_c": data.reading.values.get("max_flow_setpoint"),
            "during": starting.kind if starting else "running", "raised": name,
            "value": max(raised, current), "shadow": not data.writing,
        }
        result.events.append(event)
        self.recent_stops.append(event)
        when = {NORMAL: t("nach dem Start"), HOT_WATER: t("nach dem Start nach Warmwasser"), DEFROST: t("nach dem Start nach Abtauen")}
        message = t(
            "Ungewollte Abschaltung des Kompressors {when}: Vorlauf {peak} °C, wirksamer Soll {setpoint} °C + {margin} K. {value} jetzt {k} K.",
            when=when.get(starting.kind, "") if starting else t("im laufenden Betrieb"),
            peak=number(peak), setpoint=number(self._effective), margin=number(data.settings.stop_margin),
            value=t("Sprung beim Start") if starting else t("Schwankung im Betrieb"), k=number(max(raised, current)),
        )
        result.notes.append((message, True))

    def _follow_start(self, data: GuardInput, result: GuardResult, tv: float | None, peak: float | None) -> None:
        """A start: heating with the compressor on begins; its jump and the time until Tv is calm again."""
        reading, values, now = data.reading, data.values, data.at
        running = reading.activity == HEATING and reading.compressor is True
        # The first minute after (re)start is no start: what came before is not known
        if running and not self._running and self._activity is not None and tv is not None:
            kind = self.ended[0] if self.ended and now - self.ended[1] <= START_AFTER else NORMAL
            ref = self.before.get(kind) if kind != NORMAL else self._prev_tv
            if ref is None:
                ref = tv
            jump = values.start_jump.get(kind, data.settings.start_jump)
            self.start = Start(kind=kind, at=now, ref_c=ref, peak_c=peak or tv, floor_c=ref + jump - data.settings.shutdown_guard)
        start = self.start
        if start is None:
            return
        if running and peak is not None:
            start.peak_c = max(start.peak_c, peak)
        if running and start.settled_at is None and now - start.at >= FLAT and self._calm(now, values.steady_swing, since=start.at):
            start.settled_at = now - FLAT
        over = not running or start.settled_at is not None or now - start.at >= OBSERVE_MAX
        if over and not start.noted:
            start.noted = True
            result.events.append({
                "at": _iso(now), "kind": START_JUMP_EVENT, "start_kind": start.kind, "start_at": _iso(start.at),
                "ref_c": round(start.ref_c, 2), "peak_c": round(start.peak_c, 2), "jump_k": round(start.peak_c - start.ref_c, 2),
                "settle_s": round((start.settled_at - start.at).total_seconds()) if start.settled_at else None,
            })
        settle = timedelta(seconds=values.start_settle_s.get(start.kind, data.settings.start_settle_s))
        if start.held and running and now - start.at >= settle and self._calm(now, values.steady_swing):
            start.held = False
        if not running:
            self.start = None

    def _limits(self, data: GuardInput, result: GuardResult, low: float, high: float) -> None:
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
            distance = implausible(target, data.reading.values.get("flow_setpoint"), data.curve_c)
            if distance is not None:
                limited.append("implausible")
                result.implausible_k = round(distance, 2)
                result.freeze_up = result.freeze_up or distance > 0
                result.freeze_down = result.freeze_down or distance < 0
        if data.reading.compressor and data.reading.activity == HEATING and data.heat_w is not None:
            if data.heat_w >= data.values.power_max_w * (1 - POWER_AT_LIMIT_SHARE):
                limited.append("power_max")
                result.freeze_up = True
            if data.heat_w <= data.values.power_min_w * (1 + POWER_AT_LIMIT_SHARE):
                limited.append("power_min")
                result.freeze_down = True
        result.limited = tuple(limited)
        if target is not None:
            result.desired_c = clamp(round_near(target, data.settings.step), low, high, data.settings.step)
        if result.implausible_k is not None and not self.implausible:
            values = {"target": number(target or 0.0), "k": number(result.implausible_k)}
            if data.writing:
                message = t("Vorlauf-Ziel {target} °C nicht plausibel ({k} K vom wirksamen Soll bzw. der Kennlinie): Sunflow schreibt es nicht.", **values)
            else:
                message = t("Vorlauf-Ziel {target} °C nicht plausibel ({k} K vom wirksamen Soll bzw. der Kennlinie): Sunflow würde es nicht schreiben.", **values)
            result.notes.append((message, True))
        self.implausible = result.implausible_k is not None

    @staticmethod
    def _inactive(data: GuardInput, tv: float | None) -> list[str]:
        """Why Sunflow would write nothing now (8.5: mode off, strategy, program, activity, device)."""
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
            # The ESP component regulates Max T-Vorlauf itself (8.5): no takeover
            reasons.append("esp_regulation")
        return reasons

    @staticmethod
    def _request(result: GuardResult, value: float, reason: str) -> None:
        """The set value wanted now; the write layer decides when it is written (8.5)."""
        result.request = Request(value_c=value, reason=reason, protect=reason in PROTECT_REASONS)

    # Writing for real (step 3 part 4.7)

    def adopt(self, actual_c: float | None, blocked: bool | None, now: datetime) -> None:
        """Sunflow takes over: it starts from what the heat pump has, not from the virtual values of the shadow.

        The written value is the real Max T-Vorlauf (no write of Sunflow's yet, so no time and no reason: the
        next write goes when the rules ask for it); the block is the real one - set, it is someone else's
        (Sunflow did not set it) and stays until someone else releases it (8.6)."""
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
        """Writing switched off: nothing is reset (8.6). A block Sunflow set stays at the heat pump and is
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
            message = (t("Schatten: Sunflow würde die Raumthermostat-Sperre setzen ({why}).", why=why) if on
                       else t("Schatten: Sunflow würde die Raumthermostat-Sperre aufheben ({why}).", why=why))
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
            if self.block_reason == BUDGET_BLOCK:
                if data.protect_budget:
                    self._switch_block(result, now, False, None, t("Schreibbudget wieder verfügbar"))
                return
            if self.block_reason == MIN_POWER:
                release = room is None or plan is None or room <= plan - ROOM_DEADBAND_K + _EPS
            else:
                release = room is None or plan is None or room <= plan + ROOM_DEADBAND_K + _EPS
            if release:
                why = t("Raum {room} °C, Ziel {plan} °C", room=number(room), plan=number(plan)) if room is not None and plan is not None else t("keine Raumtemperatur")
                self._switch_block(result, now, False, None, why)
        elif room is not None and plan is not None and room >= plan + BLOCK_ROOM_ABOVE_K - _EPS:
            self._switch_block(result, now, True, ROOM_WARM, t("Raum {room} °C, {k} K über dem Ziel {plan} °C", room=number(room), k=number(room - plan), plan=number(plan)))

    def budget_block(self, result: GuardResult, data: GuardInput) -> bool:
        """The budget holds a protection step back: the block instead (8.5). False when it cannot be set
        (no block role, someone else's block, its minimum duration since the last change)."""
        settings, now = data.settings, data.at
        if not settings.has_block or self.block_reason == FOREIGN:
            return False
        if self.block:
            return True
        if self.block_at is not None and now - self.block_at < timedelta(seconds=settings.block_min_s):
            return False
        self._switch_block(result, now, True, BUDGET_BLOCK, t("Schreibbudget erschöpft"))
        result.phase = "blocked"
        self.unprotected = False
        return True

    def budget_unprotected(self, data: GuardInput) -> bool:
        """True the first time in an episode that a protection step is held back without a block."""
        if self.unprotected:
            return False
        self.unprotected = True
        return True

    def _decide(self, data: GuardInput, result: GuardResult, tv: float | None, low: float, high: float, guard: float, swing: float) -> None:
        reading, values, settings, now = data.reading, data.values, data.settings, data.at
        assert tv is not None and self.written_c is not None
        self._block(data, result)
        if self.block:
            result.phase = "blocked"
            return
        step = settings.step
        activity = reading.activity
        running = activity == HEATING and reading.compressor is True
        floor: float | None = None
        reason = START
        recent_end = self.ended if self.ended and now - self.ended[1] <= START_AFTER else None
        if activity == HOT_WATER:
            result.phase = "hot_water"
            if (before := self.before.get(HOT_WATER)) is not None:
                floor = before + values.start_jump[HOT_WATER] - guard
        elif activity == DEFROST:
            result.phase = "defrost"
        elif not running:
            result.phase = "idle"
            if recent_end is not None and (before := self.before.get(recent_end[0])) is not None:
                # After hot water or defrost the heat pump starts by itself: prepared for that start
                floor = before + values.start_jump[recent_end[0]] - guard
            else:
                floor = max(tv + settings.start_margin, tv + values.start_jump[NORMAL] - guard)
        else:
            peak = self._peak(now, TRACK_LOOKBACK) or tv
            holding = self.start is not None and self.start.held
            # While holding, the start's bound covers the jump: raised only for a peak beyond it
            track = peak - guard if holding else peak + swing - guard
            result.phase = "start_hold" if holding else "running"
            result.floor_c = clamp(round_up(track, step), low, high, step)
            if holding and self.start is not None:
                floor = self.start.floor_c
            if round_up(track, step) > self.written_c + _EPS:
                needed = data.heat_needed_w
                if not holding and settings.has_block and needed is not None and needed < values.power_min_w and (
                    self.block_at is None or now - self.block_at >= timedelta(seconds=settings.block_min_s)
                ):
                    # Minimum output above the need: the heat pump would cycle; the block instead of tracking
                    self._switch_block(result, now, True, MIN_POWER, t(
                        "Mindestleistung {min} kW über dem Bedarf {need} kW", min=number(values.power_min_w / 1000), need=number(needed / 1000)))
                    result.phase = "blocked"
                    result.floor_c = clamp(round_up(track, step), low, high, step)
                    return
                if floor is None or track + TRACK_HEADROOM_K > floor:
                    floor, reason = track + TRACK_HEADROOM_K, TRACK
        if floor is not None:
            result.floor_c = clamp(round_up(floor, step), low, high, step)
            if result.floor_c > self.written_c + _EPS:
                self._request(result, result.floor_c, reason)
                return
        if result.phase == "running" and data.evaluation.valid and result.calm and result.desired_c is not None and result.implausible_k is None:
            bound = clamp(round_up((self._peak(now, TRACK_LOOKBACK) or tv) + swing - guard, step), low, high, step)
            wanted = max(result.desired_c, bound)
            if abs(wanted - self.written_c) > settings.deadband + _EPS:
                self._request(result, wanted, NORMAL_WRITE)


# Daily counts for the user: writes Sunflow would have caused and unwanted shutdowns (shadow)

COUNTED_REASONS = (NORMAL_WRITE, START, TRACK, BLOCK_ON, BLOCK_OFF)


def daily_counts(events: list[dict[str, Any]], tz: Any, days: int, now: datetime) -> list[dict[str, Any]]:
    """Per local day (newest first, days of them): planned writes by reason, unwanted shutdowns and foreign writes."""
    today = now.astimezone(tz).date()
    counts: dict[Any, dict[str, Any]] = {}
    for back in range(days):
        day = today - timedelta(days=back)
        counts[day] = {"date": day.isoformat(), **dict.fromkeys(COUNTED_REASONS, 0), "unwanted_stops": 0, "foreign_writes": 0}
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
        elif event.get("kind") == STOP_EVENT:
            day["unwanted_stops"] += 1
        elif event.get("kind") == FOREIGN_EVENT:
            # Changes of the real set value by someone else (step 3 part 4.4); not Sunflow's budget
            day["foreign_writes"] += 1
    for day in counts.values():
        # Normal budget and emergency budget (8.5): normal writes; start and tracking (protection steps)
        day["setpoint"] = day[NORMAL_WRITE] + day[START] + day[TRACK]
        day["protect"] = day[START] + day[TRACK]
        day["block"] = day[BLOCK_ON] + day[BLOCK_OFF]
    return [counts[day] for day in sorted(counts, reverse=True)]
