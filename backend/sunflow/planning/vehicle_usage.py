"""How much charge a car usually uses per hour of the day, learned from its SoC history.

Every drop of the SoC from one hour to the next is consumption (driving, standby); a
rise is charging and counts as none. Averaged per hour and day type, this tells the
SoC a car will come back with. Only days the sensor covered well count, and a profile
per day type needs enough of those days; otherwise both use one profile, and without
enough days nothing is learned, so a few odd days do not distort the plan.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from statistics import fmean

from .hot_water import DAY_TYPES, day_type

# Days the SoC sensor covered well enough, in total and per day type of its own profile
MIN_DAYS = 14
MIN_DAY_TYPE_DAYS = 4
# A day counts with this many hourly values (a sleeping car's sensor still reports its last value)
MIN_HOURS_PER_DAY = 20
# A larger drop within an hour is a sensor error (e.g. 0 % while the car is offline)
MAX_HOURLY_DROP_PCT = 25.0


@dataclass(frozen=True, kw_only=True)
class VehicleUsage:
    """SoC used per local hour of the day (%) per day type."""

    hourly_pct: dict[str, list[float]]
    days: int

    @property
    def daily_pct(self) -> float:
        """Used on an average day of the week."""
        return (5 * sum(self.hourly_pct["workday"]) + 2 * sum(self.hourly_pct["weekend"])) / 7

    def used_pct(self, start: datetime, end: datetime, tz: tzinfo) -> float:
        """Expected use between two moments, hour by hour."""
        used, moment = 0.0, start
        while moment < end:
            step = min(end, moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            local = moment.astimezone(tz)
            used += self.hourly_pct[day_type(local)][local.hour] * (step - moment).total_seconds() / 3600
            moment = step
        return used


def usage_from(socs: list[tuple[datetime, float]], tz: tzinfo) -> VehicleUsage | None:
    """From hourly mean SoCs (%); None while too few days are covered."""
    soc = dict(socs)
    per_day: dict[object, list[tuple[str, int, float]]] = {}
    for hour, value in socs:
        later = soc.get(hour + timedelta(hours=1))
        if later is None:
            continue
        drop = value - later
        if drop > MAX_HOURLY_DROP_PCT:
            continue
        local = hour.astimezone(tz)
        per_day.setdefault(local.date(), []).append((day_type(local), local.hour, max(drop, 0.0)))
    covered = [hours for hours in per_day.values() if len(hours) >= MIN_HOURS_PER_DAY]
    if len(covered) < MIN_DAYS:
        return None
    drops: dict[str, list[list[float]]] = {kind: [[] for _ in range(24)] for kind in (*DAY_TYPES, "all")}
    days = dict.fromkeys(DAY_TYPES, 0)
    for hours in covered:
        days[hours[0][0]] += 1
        for kind, hour, drop in hours:
            drops[kind][hour].append(drop)
            drops["all"][hour].append(drop)

    def profile(kind: str) -> list[float]:
        return [fmean(values) if values else 0.0 for values in drops[kind]]

    pooled = profile("all")
    # A day type with too few days of its own takes the profile of all days
    return VehicleUsage(
        hourly_pct={kind: profile(kind) if days[kind] >= MIN_DAY_TYPE_DAYS else pooled for kind in DAY_TYPES},
        days=len(covered),
    )
