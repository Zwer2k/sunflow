"""Habits learned from the history: when the car usually leaves.

From the "connected" history of a loadpoint, charging sessions (plug in until
unplug) are found. The typical departure is the median unplug time per day type
(workday / weekend) of sessions that lasted long enough to be a real stay.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from statistics import median
from typing import Any

from sunflow.ha import HAClient

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


# A consumer counts as running above this power (standby of smart plugs stays below)
RUNNING_ABOVE_W = 20.0
# Five minute statistics are kept 10 days by default
CONSUMER_DAYS = 10
MIN_CONSUMER_DAYS = 3


@dataclass(frozen=True, kw_only=True)
class ConsumerUsage:
    """What a switchable consumer with a power sensor typically needs."""

    daily_wh: float
    running_w: float | None
    days: int


def usage_from(means: list[tuple[datetime, float]]) -> ConsumerUsage | None:
    """From five minute power means (W); None with less than MIN_CONSUMER_DAYS days."""
    days = len({moment.date() for moment, _ in means})
    if days < MIN_CONSUMER_DAYS:
        return None
    energy_wh = sum(max(power, 0.0) for _, power in means) * 5 / 60
    running = sorted(power for _, power in means if power > RUNNING_ABOVE_W)
    # A high quantile: the power when it runs, not the average with switching on and off
    running_w = running[int(0.9 * (len(running) - 1))] if running else None
    return ConsumerUsage(daily_wh=energy_wh / days, running_w=running_w, days=days)


def usage_from_energy(changes: list[tuple[datetime, float]]) -> ConsumerUsage | None:
    """From hourly energy counter changes (Wh); the running power stays unknown."""
    days = len({moment.date() for moment, _ in changes})
    if days < MIN_CONSUMER_DAYS:
        return None
    return ConsumerUsage(daily_wh=sum(max(c, 0.0) for _, c in changes) / days, running_w=None, days=days)


async def learn_usage_from_energy(client: HAClient, entity_id: str) -> ConsumerUsage | None:
    """Typical daily energy from an energy counter, for consumers without a power sensor."""
    end = datetime.now(UTC)
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=(end - timedelta(days=CONSUMER_DAYS)).isoformat(),
        end_time=end.isoformat(),
        statistic_ids=[entity_id],
        period="hour",
        types=["change"],
        units={"energy": "Wh"},
    )
    changes = [
        (datetime.fromtimestamp(row["start"] / 1000, UTC), row["change"])
        for row in result.get(entity_id, [])
        if row.get("change") is not None
    ]
    return usage_from_energy(changes)


async def learn_usage(client: HAClient, entity_id: str) -> ConsumerUsage | None:
    """Typical daily energy and running power from the power sensor's statistics."""
    end = datetime.now(UTC)
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=(end - timedelta(days=CONSUMER_DAYS)).isoformat(),
        end_time=end.isoformat(),
        statistic_ids=[entity_id],
        period="5minute",
        types=["mean"],
        units={"power": "W"},
    )
    means = [
        (datetime.fromtimestamp(row["start"] / 1000, UTC), row["mean"])
        for row in result.get(entity_id, [])
        if row.get("mean") is not None
    ]
    return usage_from(means)
