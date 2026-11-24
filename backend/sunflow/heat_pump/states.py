"""States of a heat pump and whether it heats validly (design/heat_pump.md, section 4).

Two axes through language-neutral keys of the manifest, mapped by the profile's `states`: the
program the user chose (operating mode) and what the heat pump is doing (activity). Besides them
the strategy (`hk_function`), the room thermostat block and faults. "Valid heating" is the
condition for control (tracking, correction) and learning; outside it everything is frozen.
Reads only; nothing here writes to a device.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Any

from sunflow.devices.resolver import UNAVAILABLE_STATES

from .compat import ON_OFF_DOMAINS
from .defaults import DEADBAND_K, FROZEN_AFTER_S, SELF_LIMIT_AFTER_S, SETTLE_AFTER_BYPASS_S
from .manifest import entities_by_key

UNKNOWN = "unknown"
HEATING = "heating"
FIXED = "fixed"
# Roles read as states (text or on/off) rather than numbers
STATE_ROLES = ("program", "activity", "strategy_select", "heating_block", "error")
# A fault: error codes starting with E (HPSU); W codes are warnings. A text the manifest does not
# list (newer firmware) counts by its beginning.
FAULT_KEY = re.compile(r"^err_E\d")
FAULT_TEXT = re.compile(r"^E\d")
_DURATION = re.compile(r"^(\d+)(s|min|h)$")
_UNITS = {"s": 1, "min": 60, "h": 3600}


def duration(text: Any, default_s: float) -> timedelta:
    """A profile duration ("10min", "60s", "1h"); the default when left out."""
    if not isinstance(text, str) or (match := _DURATION.match(text)) is None:
        return timedelta(seconds=default_s)
    return timedelta(seconds=int(match[1]) * _UNITS[match[2]])


@dataclass(frozen=True, kw_only=True)
class StateSettings:
    """Times of the profile's flow_control that decide valid heating."""

    settle_after_bypass: timedelta
    # Canonical activity -> how long heating stays frozen after it ended (after-run)
    frozen_after: dict[str, timedelta]
    deadband: float
    self_limit_after: timedelta

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "StateSettings":
        """The profile's values, Sunflow's defaults where it has none."""
        flow = profile.get("flow_control", {})
        frozen = flow.get("frozen_after") or {"hot_water": None, "defrost": None}
        return cls(
            settle_after_bypass=duration(flow.get("settle_after_bypass"), SETTLE_AFTER_BYPASS_S),
            frozen_after={activity: duration(text, FROZEN_AFTER_S) for activity, text in frozen.items()},
            deadband=float(flow.get("writes", {}).get("deadband", DEADBAND_K)),
            self_limit_after=timedelta(seconds=SELF_LIMIT_AFTER_S),
        )


def value_key(entity: dict[str, Any] | None, raw: str) -> str | None:
    """Language-neutral key of a shown state: translation key, else raw value; None if the manifest lacks it."""
    if entity is None:
        return None
    if entity["domain"] in ON_OFF_DOMAINS:
        return raw if raw in ("on", "off") else None
    for item in entity.get("values") or []:
        if isinstance(item, list) and len(item) > 1 and item[1] == raw:
            return item[2] if len(item) > 2 and isinstance(item[2], str) else str(item[0])
    return None


def canonical(profile: dict[str, Any], axis: str, key: str | None) -> str:
    """Canonical state of a key on an axis of the profile (program, activity); unknown if not mapped."""
    for name, keys in profile.get("states", {}).get(axis, {}).items():
        if key in keys:
            return name
    return UNKNOWN


def _since(state: dict[str, Any] | None) -> datetime | None:
    text = (state or {}).get("last_changed")
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class Reading:
    """The states of a heat pump at one moment."""

    program: str = UNKNOWN
    # Key of the program as the manifest names it (e.g. lowering), for the journal
    program_key: str | None = None
    activity: str = UNKNOWN
    strategy: str = UNKNOWN
    # Room thermostat block: True blocked, False released, None unknown
    blocked: bool | None = None
    # Key of the error (err_0, err_E9001) or a text the manifest does not list; None unreadable
    error: str | None = None
    fault: bool = False
    compressor: bool | None = None
    # Numeric values by role (normalized, e.g. W)
    values: dict[str, float | None] = field(default_factory=dict)
    # Since when the bypass valve reads 0 % (Home Assistant's last change), None if unknown or open
    bypass_closed_since: datetime | None = None
    # When the activity last changed (Home Assistant)
    activity_since: datetime | None = None
    # Role -> shown text Sunflow does not know
    unknown: dict[str, str] = field(default_factory=dict)

    @property
    def bypass(self) -> float | None:
        """Bypass valve in %: 0 = water into the heating circuit."""
        return self.values.get("bypass_valve")


def read(
    profile: dict[str, Any],
    manifest: dict[str, Any] | None,
    states: dict[str, dict[str, Any] | None],
    values: dict[str, float | None],
) -> Reading:
    """The states from the role entities' states (role -> Home Assistant state) and numeric values."""
    by_key = entities_by_key(manifest)
    roles = profile["roles"]
    keys: dict[str, str | None] = {}
    raws: dict[str, str | None] = {}
    unknown: dict[str, str] = {}
    for role in STATE_ROLES:
        if role not in roles:
            continue
        state = states.get(role)
        raw = state.get("state") if state else None
        if raw is None or raw in UNAVAILABLE_STATES:
            keys[role] = raws[role] = None
            continue
        raws[role] = raw
        keys[role] = value_key(by_key.get(roles[role]["key"]), raw)
        if keys[role] is None:
            unknown[role] = raw
    program = canonical(profile, "program", keys.get("program"))
    activity = canonical(profile, "activity", keys.get("activity"))
    for role, state in (("program", program), ("activity", activity)):
        # Known to the manifest but not to the profile (e.g. a new mode)
        if state == UNKNOWN and keys.get(role) is not None:
            unknown[role] = raws[role] or ""
    flow = profile.get("flow_control", {})
    strategy = next(
        (
            name
            for name, spec in flow.get("strategies", {}).items()
            if all(keys.get(role) == value for role, value in spec["when"].items())
        ),
        UNKNOWN,
    )
    block = flow.get("heating_block") or {}
    blocked = {block.get("on"): True, block.get("off"): False}.get(keys.get("heating_block")) if block else None
    error_key, error_raw = keys.get("error"), raws.get("error")
    fault = bool(error_key and FAULT_KEY.match(error_key)) or bool(error_key is None and error_raw and FAULT_TEXT.match(error_raw))
    if fault and error_key is None:
        # A fault code of a newer firmware: known enough
        unknown.pop("error", None)
    compressor_state = (states.get("compressor") or {}).get("state")
    bypass = values.get("bypass_valve")
    return Reading(
        program=program,
        program_key=keys.get("program"),
        activity=activity,
        strategy=strategy,
        blocked=blocked,
        error=error_key or error_raw,
        fault=fault,
        compressor={"on": True, "off": False}.get(compressor_state or ""),
        values=dict(values),
        bypass_closed_since=_since(states.get("bypass_valve")) if bypass == 0 else None,
        activity_since=_since(states.get("activity")),
        unknown=unknown,
    )


@dataclass(frozen=True, kw_only=True)
class Evaluation:
    """Whether the heat pump heats validly, and why not."""

    valid: bool
    # Why control and learning are frozen, sorted
    frozen: tuple[str, ...] = ()
    # After-runs going on (hot_water, defrost; unknown after a restart of Sunflow)
    after_run: tuple[str, ...] = ()
    # The heat pump limits the flow itself (effective setpoint below the set value)
    self_limited: bool = False
    # When heating becomes valid if only waiting times hold it (after-runs, bypass settling); else None
    valid_at: datetime | None = None


# Reasons of the frozen list that end by themselves at a known time
_TIMED = ("bypass_settling",)


class HeatingTracker:
    """Valid heating of one heat pump over time: bypass settling, after-runs, self-limiting."""

    def __init__(self, settings: StateSettings) -> None:
        """Initialize with the profile's times."""
        self.settings = settings
        self._bypass_closed_since: datetime | None = None
        # When the bypass settling ends (with bypass_settling)
        self._bypass_settled_at: datetime | None = None
        # End of the last hot water production / defrost by canonical activity
        self._ends: dict[str, datetime] = {}
        self._activity: str | None = None
        self._activity_at: datetime | None = None
        self._limited_since: datetime | None = None
        # Last write of Sunflow's set value (from step 3): the self-limit time counts from it
        self.written_at: datetime | None = None

    def activity_changed(self, old: str, new: str, at: datetime) -> None:
        """The activity changed (state_changed event): an after-run starts when hot water or defrost ends."""
        if old != new and old in self.settings.frozen_after:
            self._ends[old] = max(self._ends.get(old, at), at)

    def _after_runs(self, reading: Reading, now: datetime) -> tuple[str, ...]:
        activity, previous = reading.activity, self._activity
        changed = reading.activity_since
        if previous is not None and previous != activity and previous in self.settings.frozen_after:
            # Seen by sampling (an event may have been missed): ended at the last change, unless
            # that lies before the reading that still showed the old activity
            seen = self._activity_at
            end = changed if changed is not None and (seen is None or changed >= seen) and changed <= now else now
            self._ends[previous] = max(self._ends.get(previous, end), end)
        elif previous is None and activity not in self.settings.frozen_after and self.settings.frozen_after:
            # First reading (Sunflow started): what came before the last change is not known
            longest = max(self.settings.frozen_after.values())
            if reading.activity_since is not None and now - reading.activity_since < longest:
                self._ends[UNKNOWN] = reading.activity_since
        self._activity, self._activity_at = activity, now
        running = []
        for kind, end in self._ends.items():
            length = self.settings.frozen_after.get(kind) or max(self.settings.frozen_after.values())
            # During hot water or defrost itself the activity freezes; the after-run is what follows
            if kind != activity and end <= now < end + length:
                running.append(kind)
        return tuple(sorted(running))

    def _bypass_settled(self, reading: Reading, now: datetime) -> str | None:
        bypass = reading.bypass
        if bypass is None:
            self._bypass_closed_since = None
            return "bypass_missing"
        if bypass > 0:
            self._bypass_closed_since = None
            return "bypass"
        own = self._bypass_closed_since = self._bypass_closed_since or now
        since = reading.bypass_closed_since or own
        self._bypass_settled_at = since + self.settings.settle_after_bypass
        return None if now - since >= self.settings.settle_after_bypass else "bypass_settling"

    def _self_limited(self, reading: Reading, now: datetime) -> tuple[bool, str | None]:
        """Only with the strategy fixed: the effective setpoint stays below Sunflow's set value."""
        if reading.strategy != FIXED:
            self._limited_since = None
            return False, None
        effective, written = reading.values.get("flow_setpoint"), reading.values.get("max_flow_setpoint")
        if effective is None or written is None:
            self._limited_since = None
            return False, "setpoint_missing"
        if written - effective <= self.settings.deadband + 1e-9:
            # Ends without waiting
            self._limited_since = None
            return False, None
        self._limited_since = self._limited_since or now
        since = max(self._limited_since, self.written_at) if self.written_at else self._limited_since
        limited = now - since > self.settings.self_limit_after
        return limited, "self_limited" if limited else None

    def evaluate(self, reading: Reading, now: datetime, usable: bool) -> Evaluation:
        """Valid heating now: all conditions of section 4 at the same time."""
        frozen: list[str] = []
        if not usable:
            frozen.append("unusable")
        if reading.program != HEATING:
            frozen.append(f"program_{reading.program}")
        if reading.activity != HEATING:
            frozen.append(f"activity_{reading.activity}")
        if (bypass := self._bypass_settled(reading, now)) is not None:
            frozen.append(bypass)
        after_run = self._after_runs(reading, now)
        frozen.extend(f"after_{kind}" for kind in after_run)
        if reading.blocked is None:
            frozen.append("blocked_unknown")
        elif reading.blocked:
            frozen.append("blocked")
        if reading.fault:
            frozen.append("fault")
        elif reading.error is None:
            frozen.append("error_missing")
        limited, reason = self._self_limited(reading, now)
        if reason is not None:
            frozen.append(reason)
        return Evaluation(
            valid=not frozen, frozen=tuple(sorted(frozen)), after_run=after_run, self_limited=limited,
            valid_at=self._valid_at(frozen, after_run),
        )

    def _valid_at(self, frozen: list[str], after_run: tuple[str, ...]) -> datetime | None:
        """When heating becomes valid, if only after-runs and the bypass settling hold it (status: why the set
        value waits and until when); None when valid or something else holds it."""
        timed = {f"after_{kind}" for kind in after_run} | set(_TIMED)
        if not frozen or any(reason not in timed for reason in frozen):
            return None
        ends = []
        for kind in after_run:
            end = self._ends.get(kind)
            length = self.settings.frozen_after.get(kind) or max(self.settings.frozen_after.values())
            if end is None:
                return None
            ends.append(end + length)
        if "bypass_settling" in frozen:
            if self._bypass_settled_at is None:
                return None
            ends.append(self._bypass_settled_at)
        return max(ends) if ends else None
