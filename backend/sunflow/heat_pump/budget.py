"""Write budget of the set value (design/heat_pump.md, 8.5, step 3 part 4.3), after EepromGuard.

Every write of the set value (Max T-Vorlauf, stored in the heat pump's EEPROM) counts, whatever its
reason; the room thermostat block does not (the HPSU does not store that input, 15 point 7).
- Normal writes: at most per_day (48) per day.
- Protection steps (start, preparing, tracking): on top the emergency quota (24), so at most
  per_day + emergency_per_day per day; they use the normal budget first.
- At most per_hour (6) writes in any 60 minutes, protection steps included.
The day is the local day in Home Assistant's time zone; while that is not known the last 24 hours count
(never more permissive than a calendar day). When the budget is used up, a normal value is not written;
a protection step beyond the emergency quota or the hourly limit is replaced by the block (writer.py,
guard.py).

Where the writes are counted: in the shadow virtually (VirtualLog, from the write events in
heat_pump_samples and those of this run), never in eeprom_writes.json; once Sunflow writes for real
(step 3 part 4.7) the writes EepromGuard records (EepromLog).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, Protocol

from .defaults import WRITE_EMERGENCY_PER_DAY, WRITE_PER_DAY, WRITE_PER_HOUR, WRITE_TIMES_KEEP_S

if TYPE_CHECKING:
    from sunflow.devices.executor import EepromGuard

# Why the budget holds a value back
DAY = "day"
EMERGENCY = "emergency"
HOUR = "hour"
# Reasons of a write event that wrote the set value (guard.py); block_on/block_off do not count
SETPOINT_REASONS = ("normal", "start", "track")
WRITE_EVENT = "write"

KEEP = timedelta(seconds=WRITE_TIMES_KEEP_S)
HOUR_WINDOW = timedelta(hours=1)
DAY_WINDOW = timedelta(days=1)


def _time(text: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def _count(value: Any, default: int) -> int:
    """A count from the profile: a whole number ≥ 0, else the default."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


@dataclass(frozen=True, kw_only=True)
class BudgetLimits:
    """The budget of the profile (flow_control.writes)."""

    per_day: int = WRITE_PER_DAY
    emergency_per_day: int = WRITE_EMERGENCY_PER_DAY
    per_hour: int = WRITE_PER_HOUR

    @classmethod
    def from_writes(cls, writes: dict[str, Any]) -> "BudgetLimits":
        """From flow_control.writes; defaults where missing or not a count."""
        return cls(
            per_day=_count(writes.get("per_day"), WRITE_PER_DAY),
            emergency_per_day=_count(writes.get("emergency_per_day"), WRITE_EMERGENCY_PER_DAY),
            per_hour=_count(writes.get("per_hour"), WRITE_PER_HOUR),
        )

    def as_dict(self) -> dict[str, int]:
        """For the status and the API."""
        return {"per_day": self.per_day, "emergency_per_day": self.emergency_per_day, "per_hour": self.per_hour}


def counts(event: Any) -> bool:
    """A write event of the set value (real or virtual); the block does not count."""
    return isinstance(event, dict) and event.get("kind") == WRITE_EVENT and event.get("reason") in SETPOINT_REASONS


class WriteLog(Protocol):
    """Times the set value was written."""

    def times(self) -> list[datetime]:
        """Writes of the last KEEP, oldest first."""

    def add(self, at: datetime) -> None:
        """A write that just happened."""


class VirtualLog:
    """Shadow: the writes Sunflow would have made, from the events and those of this run (in memory)."""

    def __init__(self) -> None:
        """Nothing counted; seeded from heat_pump_samples with the first tick."""
        self._times: set[datetime] = set()
        self.seeded = False

    def seed(self, events: Iterable[Any], now: datetime) -> None:
        """The write events stored so far (and not stored yet); a write seen twice counts once."""
        for event in events:
            if counts(event) and (at := _time(event.get("at"))) is not None:
                self._times.add(at.astimezone(UTC))
        self._prune(now)
        self.seeded = True

    def add(self, at: datetime) -> None:
        """A virtual write."""
        self._times.add(at.astimezone(UTC))
        self._prune(at)

    def times(self) -> list[datetime]:
        """Writes kept, oldest first."""
        return sorted(self._times)

    def _prune(self, now: datetime) -> None:
        self._times = {at for at in self._times if now - at <= KEEP}


class EepromLog:
    """Writing for real (step 3 part 4.7): the writes EepromGuard recorded for the written entity.

    execute() records each EEPROM write itself, so add() records nothing more."""

    def __init__(self, guard: "EepromGuard", entity_id: str) -> None:
        """The guard of the data directory and the entity of the set value."""
        self._guard = guard
        self.entity_id = entity_id
        self.seeded = True

    def times(self) -> list[datetime]:
        """Recorded writes, oldest first."""
        return self._guard.times(self.entity_id)

    def add(self, at: datetime) -> None:
        """Recorded by execute() already."""


def day_start(now: datetime, tz: tzinfo | None) -> datetime:
    """Start of the local day in Home Assistant's time zone; without it 24 hours back."""
    if tz is None:
        return now - DAY_WINDOW
    local = now.astimezone(tz)
    return datetime.combine(local.date(), time.min, tz)


class WriteBudget:
    """The budget of one heat pump; limits and time zone are set every tick (profile, Home Assistant)."""

    def __init__(self, log: WriteLog | None = None, limits: BudgetLimits | None = None, tz: tzinfo | None = None) -> None:
        """Counted in log (virtual by default)."""
        self.log: WriteLog = log if log is not None else VirtualLog()
        self.limits = limits or BudgetLimits()
        self.tz = tz
        # Kinds of exhaustion already noted in the journal, per local day
        self._noticed: set[tuple[str, str]] = set()

    @property
    def seeded(self) -> bool:
        """The log knows the writes made before this run."""
        return bool(getattr(self.log, "seeded", True))

    def used(self, now: datetime) -> tuple[int, int]:
        """Writes today and in the last 60 minutes."""
        start, hour = day_start(now, self.tz), now - HOUR_WINDOW
        times = [at for at in self.log.times() if at <= now]
        return sum(1 for at in times if at >= start), sum(1 for at in times if at > hour)

    def check(self, protect: bool, now: datetime) -> str | None:
        """None when a write may go ahead now, else why not (day, emergency, hour)."""
        today, hour = self.used(now)
        limits = self.limits
        if protect:
            if today >= limits.per_day + limits.emergency_per_day:
                return EMERGENCY
        elif today >= limits.per_day:
            return DAY
        if hour >= limits.per_hour:
            return HOUR
        return None

    def __call__(self, request: Any, now: datetime) -> str | None:
        """The hook of the write layer (writer.Budget)."""
        return self.check(bool(getattr(request, "protect", False)), now)

    def wrote(self, at: datetime) -> None:
        """The set value was written (in the shadow virtually)."""
        self.log.add(at)

    def notice(self, kind: str, now: datetime) -> bool:
        """True the first time this kind of exhaustion shows on the local day (journal once)."""
        key = (day_start(now, self.tz).date().isoformat(), kind)
        if key in self._noticed:
            return False
        today = key[0]
        self._noticed = {k for k in self._noticed if k[0] == today} | {key}
        return True

    def status(self, now: datetime, *, virtual: bool) -> dict[str, Any]:
        """Writes used today and in the last hour, what is left and what is held back."""
        today, hour = self.used(now)
        limits = self.limits
        emergency_used = max(0, today - limits.per_day)
        return {
            **limits.as_dict(),
            "used_today": today,
            "used_hour": hour,
            "normal_left": max(0, limits.per_day - today),
            "emergency_left": max(0, limits.emergency_per_day - emergency_used),
            "hour_left": max(0, limits.per_hour - hour),
            "normal": self.check(False, now),
            "protect": self.check(True, now),
            "day_start": day_start(now, self.tz).astimezone(UTC).isoformat(),
            "local_day": self.tz is not None,
            "virtual": virtual,
            "seeded": self.seeded,
        }
