"""Fast changes of a room (design/heat_pump.md, 6): airing, cooking, an oven ...

Airing makes a room fall fast (often 1-4 K in 10-20 min), cooking makes it rise fast. Both change mostly
the air: once the window is shut or the stove off, a fast part comes back within about an hour, a slow
rest over hours - that rest is real heat lost (or gained) by walls, furniture and floor and must count.
While the fast part lasts the room must not drive the correction: it does not count for the room
temperature and the correction is frozen. Afterwards it counts again with what is left.

Sensors report rarely (some every 15 min) and in steps of their resolution (0.1, 0.3, 0.5 K ...). So the
series of a room is the value it showed at any time (held until the next change, from last_changed of the
state), and the thresholds are in kelvin, not in steps of the sensor (setting house.fast_change):
- An event starts when the room changed by at least start_k within start_min, down or up: the highest
  (lowest) value shown in the window is that much above (below) the current one, with two reports at
  least (the one before and the current). One step of a coarse sensor, or the slow cooling after cooking,
  stays below it.
- It ends once the fast part is over: over the last settle_min (fully known) the values shown differ by at
  most settle_k. At the latest after max_h. A fall after a rise (cooking, then the room cools) belongs to
  the same event until it is over.
- After an event only values from its end on count for detecting the next one, else the same change
  would start it again.
Active and recently ended events are kept in heat_pump_state.json (key ventilation, as before); the series
is not, it starts afresh from the sensors' last_changed after a restart.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from .defaults import (
    FAST_MAX_H,
    FAST_SETTLE_K,
    FAST_SETTLE_MIN,
    FAST_START_K,
    FAST_START_MIN,
    VENT_RECENT_KEEP_S,
    VENT_RECENT_MAX,
)
from .rooms import celsius

_LOGGER = logging.getLogger(__name__)

RECENT_KEEP = timedelta(seconds=VENT_RECENT_KEEP_S)
DOWN = "down"
UP = "up"
_EPS = 1e-9


@dataclass(frozen=True, kw_only=True)
class Thresholds:
    """When a fast change starts and ends (setting house.fast_change; start values by default)."""

    start_k: float = FAST_START_K
    start_window: timedelta = timedelta(minutes=FAST_START_MIN)
    settle_k: float = FAST_SETTLE_K
    settle_window: timedelta = timedelta(minutes=FAST_SETTLE_MIN)
    max_duration: timedelta = timedelta(hours=FAST_MAX_H)

    @classmethod
    def from_settings(cls, settings: Any) -> "Thresholds":
        """From FastChangeSettings (or anything with its fields); the start values without."""
        if settings is None:
            return cls()
        return cls(
            start_k=float(settings.start_k), start_window=timedelta(minutes=settings.start_min),
            settle_k=float(settings.settle_k), settle_window=timedelta(minutes=settings.settle_min),
            max_duration=timedelta(hours=settings.max_h),
        )

    @property
    def history(self) -> timedelta:
        """History kept per room: the longest window plus the value shown at its start."""
        return max(self.start_window, self.settle_window)


def _time(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


@dataclass(kw_only=True)
class Event:
    """A fast change of one room: when it was noticed, the value before, direction, the extreme so far, its end."""

    entity_id: str
    since: datetime
    start_c: float
    # Lowest value so far for a fall, highest for a rise
    extreme_c: float
    direction: str = DOWN
    until: datetime | None = None

    @property
    def change_k(self) -> float:
        """Signed: negative for a fall, positive for a rise."""
        return self.extreme_c - self.start_c

    def follow(self, value: float) -> None:
        """A new value of the room during the event."""
        self.extreme_c = min(self.extreme_c, value) if self.direction == DOWN else max(self.extreme_c, value)

    def as_dict(self) -> dict[str, Any]:
        """For the state file and the status; low_c/drop_k (meaning as before) only for a fall."""
        data: dict[str, Any] = {
            "entity_id": self.entity_id,
            "since": self.since.isoformat(),
            "until": self.until.isoformat() if self.until else None,
            "start_c": round(self.start_c, 2),
            "direction": self.direction,
            "extreme_c": round(self.extreme_c, 2),
            "change_k": round(self.change_k, 2),
        }
        if self.direction == DOWN:
            data["low_c"], data["drop_k"] = round(self.extreme_c, 2), round(-self.change_k, 2)
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "Event | None":
        """From the state file (also of versions with falls only: low_c); None if unreadable."""
        try:
            since = datetime.fromisoformat(data["since"])
            until = data.get("until")
            direction = UP if data.get("direction") == UP else DOWN
            extreme = data.get("extreme_c", data.get("low_c"))
            return cls(
                entity_id=str(data["entity_id"]),
                since=since,
                start_c=float(data["start_c"]),
                extreme_c=float(extreme),
                direction=direction,
                until=datetime.fromisoformat(until) if until else None,
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return None


@dataclass(kw_only=True)
class _Room:
    # (time shown from, value), ascending, a new point only on a change of the value
    points: list[tuple[datetime, float]] = field(default_factory=list)
    # Detection looks at values from here on only (end of the last event)
    quiet_from: datetime | None = None
    event: Event | None = None

    def add(self, value: float, changed: datetime | None, now: datetime, history: timedelta) -> None:
        if self.points and abs(self.points[-1][1] - value) <= _EPS:
            return
        at = changed if changed is not None and changed <= now else now
        if self.points and at <= self.points[-1][0]:
            at = now
        self.points.append((at, value))
        # Keep the window and the value shown at its start
        start = now - history
        while len(self.points) > 1 and self.points[1][0] <= start:
            self.points.pop(0)

    def shown(self, at: datetime) -> float | None:
        """The value shown at that time; None before the first known one."""
        value = None
        for time, point in self.points:
            if time > at:
                break
            value = point
        return value

    def values(self, start: datetime, now: datetime) -> list[float] | None:
        """All values shown from start to now; None if the room is not known from start on."""
        before = self.shown(start)
        if before is None:
            return None
        return [before, *(v for t, v in self.points if start < t <= now)]

    def changed(self, now: datetime, limits: Thresholds) -> tuple[str, float] | None:
        """Direction and the value before when the room changed by at least start_k within the window."""
        if not self.points:
            return None
        start = now - limits.start_window
        if self.quiet_from is not None and self.quiet_from > start:
            start = self.quiet_from
        current = self.points[-1][1]
        before = self.shown(start)
        shown = [v for t, v in self.points if start < t <= now] + ([before] if before is not None else [])
        # The current value and at least one before it
        if len(shown) < 2:
            return None
        highest, lowest = max(shown), min(shown)
        fall, rise = highest - current, current - lowest
        if fall >= limits.start_k - _EPS and fall >= rise:
            return DOWN, highest
        if rise >= limits.start_k - _EPS:
            return UP, lowest
        return None

    def settled(self, now: datetime, limits: Thresholds) -> bool:
        """Moved by at most settle_k over the whole settle window."""
        values = self.values(now - limits.settle_window, now)
        return values is not None and max(values) - min(values) <= limits.settle_k + _EPS


class VentilationWatch:
    """Notices fast changes per room (airing, cooking ...) and when their fast part is over."""

    def __init__(self, stored: Any = None) -> None:
        """Start from the stored active and recent events (house["ventilation"] of the state file)."""
        self._rooms: dict[str, _Room] = {}
        self.recent: list[Event] = []
        stored = stored if isinstance(stored, dict) else {}
        for data in stored.get("active") or []:
            if (event := Event.from_dict(data)) is not None and event.until is None:
                self._rooms[event.entity_id] = _Room(event=event)
        for data in stored.get("recent") or []:
            if (event := Event.from_dict(data)) is not None and event.until is not None:
                self.recent.append(event)

    @property
    def active(self) -> dict[str, Event]:
        """Rooms being aired now."""
        return {entity_id: room.event for entity_id, room in self._rooms.items() if room.event is not None}

    def update(
        self, rooms: Iterable[str], states: dict[str, dict[str, Any]], now: datetime, settings: Any = None,
    ) -> tuple[str, ...]:
        """Take the rooms' current values; the rooms with a fast change now. settings: house.fast_change."""
        limits = Thresholds.from_settings(settings)
        ids = list(dict.fromkeys(rooms))
        for gone in set(self._rooms) - set(ids):
            del self._rooms[gone]
        for entity_id in ids:
            room = self._rooms.setdefault(entity_id, _Room())
            state = states.get(entity_id)
            if (value := celsius(state)) is not None:
                room.add(value, _time((state or {}).get("last_changed")), now, limits.history)
            self._follow(entity_id, room, now, limits)
        self.recent = [
            e for e in self.recent if e.until is not None and now - e.until < RECENT_KEEP and e.entity_id in self._rooms
        ][-VENT_RECENT_MAX:]
        return tuple(entity_id for entity_id in ids if self._rooms[entity_id].event is not None)

    def _follow(self, entity_id: str, room: _Room, now: datetime, limits: Thresholds) -> None:
        event = room.event
        if event is None:
            if (found := room.changed(now, limits)) is not None:
                direction, before = found
                current = room.points[-1][1]
                room.event = Event(entity_id=entity_id, since=now, start_c=before, extreme_c=current, direction=direction)
                _LOGGER.info("Fast change of %s: %.1f to %.1f °C", entity_id, before, current)
            return
        if room.points:
            event.follow(room.points[-1][1])
        elapsed = now - event.since
        if elapsed >= limits.max_duration or (elapsed >= limits.settle_window and room.settled(now, limits)):
            event.until = now
            room.event, room.quiet_from = None, now
            self.recent.append(event)
            _LOGGER.info("Fast change of %s over after %s", entity_id, elapsed)

    def as_dict(self) -> dict[str, Any]:
        """For the state file."""
        return {
            "active": [e.as_dict() for e in self.active.values()],
            "recent": [e.as_dict() for e in self.recent],
        }
