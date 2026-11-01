"""When and how much hot water is drawn, learned from the tank temperature and the heating power.

Per hour the tank gets the heating energy and loses what was drawn (and its standing
loss): drawn = heating - temperature rise x tank content. Averaged per hour of the day
and day type (workday / weekend), this gives the usual usage profile, e.g. showers in
the morning and baths in the evening.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from statistics import fmean

from sunflow.ha import HAClient

from .load_forecast import fetch_hourly_means

# Fewer days say nothing about habits
MIN_DAYS = 7
DAY_TYPES = ("workday", "weekend")


def day_type(moment: datetime) -> str:
    """Workday or weekend of a local time."""
    return "weekend" if moment.weekday() >= 5 else "workday"


@dataclass(frozen=True, kw_only=True)
class HotWaterUsage:
    """Hot water drawn per local hour of the day (Wh) per day type."""

    hourly_wh: dict[str, list[float]]
    days: int

    @property
    def daily_wh(self) -> float:
        """Drawn on an average day of the week."""
        return (5 * sum(self.hourly_wh["workday"]) + 2 * sum(self.hourly_wh["weekend"])) / 7

    def drawn_wh(self, start: datetime, end: datetime, tz: tzinfo) -> float:
        """Expected draw between two moments (e.g. a plan slot)."""
        local = start.astimezone(tz)
        return self.hourly_wh[day_type(local)][local.hour] * (end - start).total_seconds() / 3600

    def next_peak(self, after: datetime, tz: tzinfo) -> datetime:
        """Start of the next hour with the most usage of its day type: the tank should be hot by then."""
        local = after.astimezone(tz)
        for offset in range(3):
            day = (local + timedelta(days=offset)).date()
            hours = self.hourly_wh[day_type(datetime.combine(day, datetime.min.time()))]
            peak = max(range(24), key=lambda h: hours[h])
            candidate = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=peak)
            if candidate > local:
                return candidate
        return after + timedelta(hours=24)


def usage_from(
    temperatures: list[tuple[datetime, float]], heating: list[tuple[datetime, float]], wh_per_kelvin: float, tz: tzinfo
) -> HotWaterUsage | None:
    """From hourly mean tank temperatures (°C) and heating powers (W); None with too little history."""
    temperature = dict(temperatures)
    heated = dict(heating)
    drawn: dict[str, list[list[float]]] = {kind: [[] for _ in range(24)] for kind in DAY_TYPES}
    days = set()
    for hour, value in temperatures:
        later = temperature.get(hour + timedelta(hours=1))
        if later is None:
            continue
        local = hour.astimezone(tz)
        # Heating that did not warm the tank was drawn (or lost); a rise beyond it is measuring noise
        drawn[day_type(local)][local.hour].append(max(heated.get(hour, 0.0) - (later - value) * wh_per_kelvin, 0.0))
        days.add(local.date())
    if len(days) < MIN_DAYS or not heating:
        return None
    return HotWaterUsage(
        hourly_wh={kind: [fmean(values) if values else 0.0 for values in drawn[kind]] for kind in DAY_TYPES},
        days=len(days),
    )


async def learn_hot_water(
    client: HAClient, temperature_entity: str, power_entity: str, wh_per_kelvin: float, now: datetime, tz: tzinfo
) -> HotWaterUsage | None:
    """The usage profile from Home Assistant's hourly statistics of the tank."""
    temperatures = await fetch_hourly_means(client, temperature_entity, now)
    heating = await fetch_hourly_means(client, power_entity, now)
    return usage_from(temperatures, heating, wh_per_kelvin, tz)
