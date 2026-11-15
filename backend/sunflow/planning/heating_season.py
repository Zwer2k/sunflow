"""Whether it is heating season: by outside temperature, by date or from an entity."""

from collections import defaultdict
from datetime import date, datetime, tzinfo
from statistics import fmean

from sunflow.config.models import HeatingSeason, SunflowConfig
from sunflow.ha import HAClient

from .load_forecast import fetch_hourly_means

# A day counts with most of its hours measured
MIN_HOURS = 18


def by_temperature(daily_means: list[float], season: HeatingSeason) -> bool:
    """Heating season after `days` days in a row below the threshold, summer after as many above.

    daily_means: the last full days, oldest first. In between the season stays as it is.
    """
    last = daily_means[-season.days :]
    if len(last) < season.days:
        return season.active
    if all(mean < season.threshold_c for mean in last):
        return True
    if all(mean >= season.threshold_c for mean in last):
        return False
    return season.active


def by_dates(today: date, season: HeatingSeason) -> bool:
    """Heating season from the start to the end date, over the turn of the year."""
    day = today.strftime("%m-%d")
    if season.start <= season.end:
        return season.start <= day <= season.end
    return day >= season.start or day <= season.end


def daily_means(hourly: list[tuple[datetime, float]], today: date, tz: tzinfo) -> list[float]:
    """Mean of each full day before today with enough hours, oldest first."""
    days: dict[date, list[float]] = defaultdict(list)
    for moment, value in hourly:
        days[moment.astimezone(tz).date()].append(value)
    return [fmean(values) for day, values in sorted(days.items()) if day < today and len(values) >= MIN_HOURS]


def uses_seasons(config: SunflowConfig) -> bool:
    """Whether any consumer has a mode per season."""
    return any(f.heating_mode is not None or f.summer_mode is not None for f in config.planning.values())


async def current_season(
    client: HAClient, season: HeatingSeason, states: dict[str, dict], now: datetime, tz: tzinfo
) -> bool:
    """Heating season now; the stored state where nothing tells otherwise (no sensor, no history)."""
    today = now.astimezone(tz).date()
    match season.source:
        case "dates":
            return by_dates(today, season)
        case "entity":
            state = states.get(season.entity_id or "", {}).get("state")
            if state is None or state in ("unavailable", "unknown"):
                return season.active
            return state.lower() in {s.lower() for s in season.entity_states}
        case _:
            if season.temperature_entity is None:
                return season.active
            hourly = await fetch_hourly_means(client, season.temperature_entity, now, days=season.days + 1)
            return by_temperature(daily_means(hourly, today, tz), season)


def with_season(config: SunflowConfig, active: bool, now: datetime) -> bool:
    """Store a changed season in the config; True when it changed."""
    if config.heating_season.active == active:
        return False
    config.heating_season.active = active
    config.heating_season.changed_at = now
    return True

