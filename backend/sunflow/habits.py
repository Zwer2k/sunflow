"""Habits learned from the history: when the car usually leaves.

From the "connected" history of a loadpoint, charging sessions (plug in until
unplug) are found. The typical departure is the median unplug time per day type
(workday / weekend) of sessions that lasted long enough to be a real stay.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from statistics import median
from typing import Any

from .ha import HAClient

MIN_SESSIONS = 3
# Shorter stays are re-plugging or a quick top-up, not a parking period
MIN_STAY = timedelta(hours=2)
HISTORY_DAYS = 14


@dataclass(frozen=True, kw_only=True)
class Departure:
    """Typical unplug time (local) per day type; None without enough sessions."""

    workday: time | None
    weekend: time | None
    sessions: int

    def next_after(self, moment: datetime, tz: tzinfo) -> datetime | None:
        """The next typical departure after moment."""
        local = moment.astimezone(tz)
        for offset in range(8):
            day: date = (local + timedelta(days=offset)).date()
            typical = self.weekend if day.weekday() >= 5 else self.workday
            if typical is None:
                continue
            candidate = datetime.combine(day, typical, tzinfo=tz)
            if candidate > local:
                return candidate
        return None


def departures_from(states: list[dict[str, Any]], tz: tzinfo) -> Departure:
    """Learn from minimal history states ({"s": "on"/"off", "lu": timestamp})."""
    unplug: dict[str, list[int]] = {"workday": [], "weekend": []}
    plugged_at: datetime | None = None
    for row in states:
        moment = datetime.fromtimestamp(row.get("lu") or row.get("lc") or 0, UTC).astimezone(tz)
        if row.get("s") == "on":
            plugged_at = plugged_at or moment
        elif row.get("s") == "off" and plugged_at is not None:
            if moment - plugged_at >= MIN_STAY:
                kind = "weekend" if moment.weekday() >= 5 else "workday"
                unplug[kind].append(moment.hour * 60 + moment.minute)
            plugged_at = None

    def typical(minutes: list[int]) -> time | None:
        if len(minutes) < MIN_SESSIONS:
            return None
        value = int(median(minutes))
        return time(value // 60, value % 60)

    return Departure(
        workday=typical(unplug["workday"]),
        weekend=typical(unplug["weekend"]),
        sessions=len(unplug["workday"]) + len(unplug["weekend"]),
    )


async def learn_departure(client: HAClient, entity_id: str, tz: tzinfo) -> Departure:
    """Typical departure from the recorder history of the connected sensor."""
    end = datetime.now(UTC)
    result = await client.command(
        "history/history_during_period",
        start_time=(end - timedelta(days=HISTORY_DAYS)).isoformat(),
        end_time=end.isoformat(),
        entity_ids=[entity_id],
        minimal_response=True,
        no_attributes=True,
    )
    return departures_from(result.get(entity_id, []), tz)
