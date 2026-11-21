"""Ventilation detection per room (design/heat_pump.md, 6.2).

Airing a room makes it fall fast (around 1 K in half an hour), recover partly as soon as the window
is shut, the rest slowly over hours. While that fast part lasts, the room must not drive the
correction: it does not count for the room temperature and the correction is frozen.

Sensors report rarely (some every 15 min) and in steps of their resolution. So the series of a room
is the value it showed at any time (held until the next change, from last_changed of the state):
- An event starts when the room fell by at least VENT_DROP_K within VENT_DROP_WINDOW, i.e. the
  highest value shown in the window is that much above the current one (two reports at least: the
  one before and the current). 0.3 K in 30 min is the slope of -0.6 K/h; a slope over a shorter
  span would react to single steps of the sensor's resolution.
- It ends when the room is flat again: over the last VENT_FLAT_WINDOW (fully known) the values
  shown differ by at most VENT_FLAT_K. At the latest after VENT_MAX.
- After an event only values from its end on count for detecting the next one, else the same drop
  would start it again.
Active and recently ended events are kept in heat_pump_state.json; the series is not, it starts
afresh from the sensors' last_changed after a restart.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from .defaults import (
    VENT_DROP_K,
    VENT_DROP_WINDOW_S,
    VENT_FLAT_K,
    VENT_FLAT_WINDOW_S,
    VENT_MAX_S,
    VENT_RECENT_KEEP_S,
    VENT_RECENT_MAX,
)
from .rooms import celsius

_LOGGER = logging.getLogger(__name__)

DROP_WINDOW = timedelta(seconds=VENT_DROP_WINDOW_S)
FLAT_WINDOW = timedelta(seconds=VENT_FLAT_WINDOW_S)
MAX_DURATION = timedelta(seconds=VENT_MAX_S)
RECENT_KEEP = timedelta(seconds=VENT_RECENT_KEEP_S)
# History kept per room: the longest window plus the value shown at its start
HISTORY = max(DROP_WINDOW, FLAT_WINDOW)
_EPS = 1e-9


def _time(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


@dataclass(kw_only=True)
class Event:
    """A ventilation of one room: when it was noticed, the value before, the lowest so far, its end."""

    entity_id: str
    since: datetime
    start_c: float
    low_c: float
    until: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """For the state file and the status."""
        return {
            "entity_id": self.entity_id,
            "since": self.since.isoformat(),
            "until": self.until.isoformat() if self.until else None,
            "start_c": round(self.start_c, 2),
            "low_c": round(self.low_c, 2),
            "drop_k": round(self.start_c - self.low_c, 2),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Event | None":
        """From the state file; None if unreadable."""
        try:
            since = datetime.fromisoformat(data["since"])
            until = data.get("until")
            return cls(
                entity_id=str(data["entity_id"]),
                since=since,
                start_c=float(data["start_c"]),
                low_c=float(data["low_c"]),
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

    def add(self, value: float, changed: datetime | None, now: datetime) -> None:
        if self.points and abs(self.points[-1][1] - value) <= _EPS:
            return
        at = changed if changed is not None and changed <= now else now
        if self.points and at <= self.points[-1][0]:
            at = now
        self.points.append((at, value))
        # Keep the window and the value shown at its start
        start = now - HISTORY
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

    def fell(self, now: datetime) -> float | None:
        """The highest value shown in the drop window if the room fell by at least VENT_DROP_K since."""
        if not self.points:
            return None
        start = now - DROP_WINDOW
        if self.quiet_from is not None and self.quiet_from > start:
            start = self.quiet_from
        current = self.points[-1][1]
        before = self.shown(start)
        shown = [v for t, v in self.points if start < t <= now] + ([before] if before is not None else [])
        # The current value and at least one before it
        if len(shown) < 2:
            return None
        highest = max(shown)
        return highest if highest - current >= VENT_DROP_K - _EPS else None

    def flat(self, now: datetime) -> bool:
        """Changed by at most VENT_FLAT_K over the whole flat window."""
        values = self.values(now - FLAT_WINDOW, now)
        return values is not None and max(values) - min(values) <= VENT_FLAT_K + _EPS


class VentilationWatch:
    """Notices ventilation per room and when it is over."""

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

    def update(self, rooms: Iterable[str], states: dict[str, dict[str, Any]], now: datetime) -> tuple[str, ...]:
        """Take the rooms' current values; the rooms being aired now."""
        ids = list(dict.fromkeys(rooms))
        for gone in set(self._rooms) - set(ids):
            del self._rooms[gone]
        for entity_id in ids:
            room = self._rooms.setdefault(entity_id, _Room())
            state = states.get(entity_id)
            if (value := celsius(state)) is not None:
                room.add(value, _time((state or {}).get("last_changed")), now)
            self._follow(entity_id, room, now)
        self.recent = [
            e for e in self.recent if e.until is not None and now - e.until < RECENT_KEEP and e.entity_id in self._rooms
        ][-VENT_RECENT_MAX:]
        return tuple(entity_id for entity_id in ids if self._rooms[entity_id].event is not None)

    def _follow(self, entity_id: str, room: _Room, now: datetime) -> None:
        event = room.event
        if event is None:
            if (highest := room.fell(now)) is not None:
                current = room.points[-1][1]
                room.event = Event(entity_id=entity_id, since=now, start_c=highest, low_c=current)
                _LOGGER.info("Ventilation of %s: fell from %.1f to %.1f °C", entity_id, highest, current)
            return
        if room.points:
            event.low_c = min(event.low_c, room.points[-1][1])
        elapsed = now - event.since
        if elapsed >= MAX_DURATION or (elapsed >= FLAT_WINDOW and room.flat(now)):
            event.until = now
            room.event, room.quiet_from = None, now
            self.recent.append(event)
            _LOGGER.info("Ventilation of %s over after %s", entity_id, elapsed)

    def as_dict(self) -> dict[str, Any]:
        """For the state file."""
        return {
            "active": [e.as_dict() for e in self.active.values()],
            "recent": [e.as_dict() for e in self.recent],
        }
