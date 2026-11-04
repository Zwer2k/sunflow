"""What Home Assistant still knows from the past: how far back learning can reach.

Only short queries: whether an entity has a state at a moment (the state at the start
of a window is the last one recorded before it), probed on a grid of days and refined
by bisection. Home Assistant keeps the states for its purge period, statistics of
sensors with a state_class for good; Solcast keeps its past forecasts itself.
"""

from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, tzinfo
from statistics import median
from typing import Any

from sunflow.config.models import SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.site import site_status
from sunflow.ha import HAClient, HACommandError, HAError
from sunflow.planning.plan import weather_entity
from sunflow.planning.sources import solcast_sites

# Days back probed before bisecting; dense where purge periods usually end
PROBE_DAYS = (1, 3, 7, 10, 14, 21, 30, 45, 60, 90, 100, 120, 180, 365, 730, 1095, 1825, 3650)
STATISTICS_DAYS = 3650
# Solcast keeps at most 730 days of past forecasts
SOLCAST_DAYS = 731
DENSITY_WINDOW = timedelta(hours=1)
WEATHER_ATTRIBUTES = ("cloud_coverage", "temperature")
RECORDED_DOMAINS = ("sensor.", "binary_sensor.")

Recorded = Callable[[list[str], int], Awaitable[set[str]]]


async def earliest_days(keys: list[str], recorded: Recorded, max_days: int) -> dict[str, int | None]:
    """Days back to the oldest data per key, at day precision; None: no data at all.

    recorded(keys, days) returns the keys with data at that many days back. Data is
    assumed to be continuous from its oldest day on (older data is purged, not newer).
    """
    newest: dict[str, int] = dict.fromkeys(await recorded(keys, 0), 0)
    empty: dict[str, int] = {}
    remaining = list(newest)
    for days in (d for d in PROBE_DAYS if d < max_days):
        if not remaining:
            break
        found = await recorded(remaining, days)
        for key in remaining:
            if key in found:
                newest[key] = days
            else:
                empty[key] = days
        remaining = [k for k in remaining if k in found]
    # Most keys end at the same purge, so they are bisected together
    brackets: dict[tuple[int, int], list[str]] = defaultdict(list)
    for key, days in empty.items():
        brackets[newest[key], days].append(key)
    pending = list(brackets.items())
    while pending:
        (new, old), group = pending.pop()
        if old - new <= 1:
            continue
        middle = (new + old) // 2
        found = await recorded(group, middle)
        if with_data := [k for k in group if k in found]:
            newest.update(dict.fromkeys(with_data, middle))
            pending.append(((middle, old), with_data))
        if without := [k for k in group if k not in found]:
            pending.append(((new, middle), without))
    return {key: newest.get(key) for key in keys}


async def _states_at(client: HAClient, entity_ids: list[str], moment: datetime) -> dict[str, list[dict[str, Any]]]:
    return await client.command(
        "history/history_during_period",
        start_time=moment.isoformat(),
        end_time=(moment + timedelta(seconds=1)).isoformat(),
        entity_ids=entity_ids,
        minimal_response=True,
        no_attributes=True,
        significant_changes_only=False,
    )


async def states_days(client: HAClient, entity_ids: list[str], now: datetime) -> dict[str, int | None]:
    """Days back to each entity's oldest recorded state."""

    async def recorded(keys: list[str], days: int) -> set[str]:
        result = await _states_at(client, keys, now - timedelta(days=days, minutes=1))
        return {k for k, rows in result.items() if rows}

    return await earliest_days(entity_ids, recorded, max(PROBE_DAYS) + 1)


async def changes_per_hour(client: HAClient, entity_ids: list[str], now: datetime) -> dict[str, float]:
    """Recorded state changes in the last hour: how finely the history resolves."""
    result = await client.command(
        "history/history_during_period",
        start_time=(now - DENSITY_WINDOW).isoformat(),
        end_time=now.isoformat(),
        entity_ids=entity_ids,
        minimal_response=True,
        no_attributes=True,
        significant_changes_only=False,
        # Only changes inside the window count
        include_start_time_state=False,
    )
    hours = DENSITY_WINDOW / timedelta(hours=1)
    return {entity_id: len(result.get(entity_id, [])) / hours for entity_id in entity_ids}


async def statistics_since(client: HAClient, entity_ids: list[str], now: datetime) -> dict[str, datetime]:
    """First month with long-term statistics, for entities that have them."""
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=(now - timedelta(days=STATISTICS_DAYS)).isoformat(),
        end_time=now.isoformat(),
        statistic_ids=entity_ids,
        period="month",
        types=["mean", "sum"],
    )
    return {
        entity_id: datetime.fromtimestamp(min(row["start"] for row in rows) / 1000, UTC)
        for entity_id, rows in result.items()
        if rows
    }


async def recorded_attributes(client: HAClient, entity_id: str, moment: datetime) -> list[str]:
    """Which of the weather attributes the recorder kept at that moment."""
    result = await client.command(
        "history/history_during_period",
        start_time=moment.isoformat(),
        end_time=(moment + timedelta(hours=1)).isoformat(),
        entity_ids=[entity_id],
        minimal_response=False,
        no_attributes=False,
        significant_changes_only=False,
    )
    rows = result.get(entity_id, [])
    return [a for a in WEATHER_ATTRIBUTES if any(a in (row.get("a") or row.get("attributes") or {}) for row in rows)]


async def solcast_days(client: HAClient, sites: list[str], now: datetime, tz: tzinfo) -> dict[str, int | None]:
    """Days back to each Solcast site's oldest kept forecast."""
    today = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    async def recorded(keys: list[str], days: int) -> set[str]:
        start = today - timedelta(days=days)
        found = set()
        for site in keys:
            try:
                response = await client.call_service(
                    "solcast_solar",
                    "query_forecast_data",
                    {"start_date_time": start.isoformat(), "end_date_time": (start + timedelta(days=1)).isoformat(), "site": site},
                    return_response=True,
                )
            except HACommandError:
                # A day before the kept history is a validation error; today it is a real one
                if days == 0:
                    raise
                continue
            if response["response"]["data"]:
                found.add(site)
        return found

    return await earliest_days(sites, recorded, SOLCAST_DAYS)


async def data_inventory(
    client: HAClient,
    config: SunflowConfig,
    profiles: dict[str, Any],
    snapshot: Snapshot,
    tz: tzinfo,
    extra: list[str] | None = None,
) -> dict[str, Any]:
    """How far back the measurements, the weather and the PV forecasts go; extra: further entities to check."""
    now = datetime.now(UTC)
    site = site_status(config.devices, profiles, snapshot, measured_by=config.measured_by)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for device in site.devices:
        for role in device.roles:
            if role.entity_id and role.entity_id.startswith(RECORDED_DOMAINS) and role.entity_id not in seen:
                seen.add(role.entity_id)
                rows.append({"device": device.name, "role": role.role, "entity_id": role.entity_id})
    for entity_id in extra or []:
        if entity_id not in seen:
            seen.add(entity_id)
            rows.append({"device": None, "role": None, "entity_id": entity_id})
    weather = weather_entity(config, snapshot)
    entity_ids = [r["entity_id"] for r in rows] + ([weather] if weather else [])
    since = await states_days(client, entity_ids, now) if entity_ids else {}
    density = await changes_per_hour(client, entity_ids, now) if entity_ids else {}
    statistics = await statistics_since(client, entity_ids, now) if entity_ids else {}
    for row in rows:
        row |= {
            "states_days": since[row["entity_id"]],
            "changes_per_hour": density[row["entity_id"]],
            "statistics_since": statistics[row["entity_id"]].isoformat() if row["entity_id"] in statistics else None,
            # Without one Home Assistant keeps no long-term statistics
            "state_class": snapshot.states.get(row["entity_id"], {}).get("attributes", {}).get("state_class"),
        }
    weather_info = None
    if weather:
        days = since[weather]
        weather_info = {
            "entity_id": weather,
            "states_days": days,
            # Checked at the oldest day: an attribute recorded then is recorded since
            "attributes": await recorded_attributes(client, weather, now - timedelta(days=max(days - 1, 0)))
            if days is not None
            else [],
        }
    sites = solcast_sites(
        list(snapshot.states.values()), {e["entity_id"] for e in snapshot.entities if e.get("platform") == "solcast_solar"}
    )
    forecast_error = None
    try:
        forecast_days = await solcast_days(client, [s.ref for s in sites], now, tz) if sites else {}
    except (HAError, KeyError, TypeError) as err:
        forecast_days, forecast_error = {}, str(err)
    names = {(p.source, p.ref): p.custom_name for p in config.forecast_planes}
    forecasts = [
        {"name": names.get((s.source, s.ref)) or s.name, "source": s.source, "days": forecast_days.get(s.ref), "error": forecast_error}
        for s in sites
    ]
    usable = [d for d in (r["states_days"] for r in rows if r["device"]) if d]
    return {
        "time": now.isoformat(),
        # The purge period is the same for all; the typical value stands for the recorder
        "states_days": round(median(usable)) if usable else None,
        "entities": rows,
        "weather": weather_info,
        "forecasts": forecasts,
    }
