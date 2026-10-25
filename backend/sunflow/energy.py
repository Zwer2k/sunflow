"""Energy balance of the site from energy counters (kWh) in Home Assistant's statistics.

Counters are exact where integrated power means miss short peaks. Grid and load
come from the device chosen under "Messquellen"; on "automatic" the grid counters
of the energy dashboard win, then a dedicated meter, then an inverter.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
import math
from typing import Any

from .config_store import SunflowConfig
from .discovery import Snapshot
from .ha import HAClient
from .resolver import preview
from .site import device_parameters, measurement_device

# Balance keys that belong to one measured quantity: only its device counts
QUANTITY_KEYS = {"grid_import": "grid", "grid_export": "grid", "load": "load"}

# Profile role -> balance key
ENERGY_ROLES = {
    "pv_energy": "pv",
    "load_energy": "load",
    "grid_import_energy": "grid_import",
    "grid_export_energy": "grid_export",
    "battery_charge_energy": "battery_charge",
    "battery_discharge_energy": "battery_discharge",
}
FIVE_MINUTE_LIMIT = timedelta(days=10)


@dataclass(kw_only=True)
class EnergySources:
    """Statistics per balance key and where they come from."""

    statistics: dict[str, list[str]] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)  # "devices" or "energy_dashboard"
    # Inverters with AC and DC counters: name -> (AC statistic, DC statistic)
    inverters: dict[str, tuple[str, str]] = field(default_factory=dict)


def _dashboard_statistics(prefs: dict[str, Any] | None) -> dict[str, list[str]]:
    """Grid, solar and battery statistics configured in the energy dashboard."""
    found: dict[str, list[str]] = {}

    def add(key: str, statistic: str | None) -> None:
        if statistic:
            found.setdefault(key, []).append(statistic)

    for source in (prefs or {}).get("energy_sources", []):
        kind = source.get("type")
        if kind == "grid":
            # Older format with flow lists, newer with the statistics on the source
            for flow in source.get("flow_from", []):
                add("grid_import", flow.get("stat_energy_from"))
            for flow in source.get("flow_to", []):
                add("grid_export", flow.get("stat_energy_to"))
            add("grid_import", source.get("stat_energy_from"))
            add("grid_export", source.get("stat_energy_to"))
        elif kind == "solar":
            add("pv", source.get("stat_energy_from"))
        elif kind == "battery":
            add("battery_discharge", source.get("stat_energy_from"))
            add("battery_charge", source.get("stat_energy_to"))
    return found


def energy_sources(config: SunflowConfig, profiles: dict[str, dict[str, Any]], snapshot: Snapshot) -> EnergySources:
    """Pick the counters for every balance key."""
    devices: dict[str, list[str]] = {}
    sources = EnergySources()
    chosen = {quantity: measurement_device(config, profiles, quantity) for quantity in set(QUANTITY_KEYS.values())}
    for device in config.devices:
        profile = profiles[device.profile]
        roles = {
            r.role: r
            for r in preview(
                profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()}
            )
        }
        for role, key in ENERGY_ROLES.items():
            quantity = QUANTITY_KEYS.get(key)
            if quantity and (chosen[quantity] is None or chosen[quantity].id != device.id):
                continue
            if role in roles and roles[role].entity_id:
                devices.setdefault(key, []).append(roles[role].entity_id)
        ac, dc = roles.get("pv_energy"), roles.get("pv_dc_energy")
        if ac and dc and ac.entity_id and dc.entity_id:
            sources.inverters[device.name] = (ac.entity_id, dc.entity_id)
    dashboard = _dashboard_statistics(snapshot.energy_prefs)
    for key in ENERGY_ROLES.values():
        # On automatic the energy dashboard's grid (usually the utility meter) is the reference
        prefer_dashboard = QUANTITY_KEYS.get(key) == "grid" and "grid" not in config.measurement_sources
        first, second = (dashboard, devices) if prefer_dashboard else (devices, dashboard)
        if key in first:
            sources.statistics[key] = first[key]
            sources.origin[key] = "energy_dashboard" if first is dashboard else "devices"
        elif key in second:
            sources.statistics[key] = second[key]
            sources.origin[key] = "energy_dashboard" if second is dashboard else "devices"
    return sources


async def fetch_changes(client: HAClient, statistic_ids: list[str], start: datetime, end: datetime) -> dict[str, float]:
    """Energy (Wh) counted by each statistic between start and end."""
    if not statistic_ids:
        return {}
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=sorted(set(statistic_ids)),
        period="5minute" if end - start <= FIVE_MINUTE_LIMIT else "hour",
        types=["change"],
        units={"energy": "Wh"},
    )
    return {
        statistic: sum(row["change"] for row in rows if row.get("change") is not None)
        for statistic, rows in result.items()
        if any(row.get("change") is not None for row in rows)
    }


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    """numerator / denominator, None when undefined."""
    if numerator is None or not denominator:
        return None
    return numerator / denominator


async def energy_balance(client: HAClient, sources: EnergySources, start: datetime, end: datetime) -> dict[str, Any]:
    """Totals (Wh), autarky, self-consumption and inverter efficiencies for a period."""
    ids = [s for statistics in sources.statistics.values() for s in statistics]
    ids += [s for pair in sources.inverters.values() for s in pair]
    changes = await fetch_changes(client, ids, start, end)
    totals: dict[str, float | None] = {}
    for key in ENERGY_ROLES.values():
        values = [changes[s] for s in sources.statistics.get(key, []) if s in changes]
        totals[key] = sum(values) if values else None
    consumption, consumption_origin = totals["load"], "counter"
    if consumption is None and totals["grid_import"] is not None and totals["pv"] is not None:
        # Without a consumption counter the house takes what is left in the balance
        consumption = (
            totals["pv"]
            + totals["grid_import"]
            - (totals["grid_export"] or 0)
            + (totals["battery_discharge"] or 0)
            - (totals["battery_charge"] or 0)
        )
        consumption_origin = "balance"
    autarky = ratio(totals["grid_import"], consumption)
    self_consumption = ratio(totals["grid_export"], totals["pv"])
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "totals": totals,
        "origin": sources.origin,
        "consumption": consumption,
        "consumption_origin": consumption_origin if consumption is not None else None,
        "autarky": min(max(1 - autarky, 0), 1) if autarky is not None else None,
        "self_consumption": min(max(1 - self_consumption, 0), 1) if self_consumption is not None else None,
        "battery_ratio": ratio(totals["battery_discharge"], totals["battery_charge"]),
        "inverters": [
            {
                "name": name,
                "ac_wh": changes.get(ac),
                "dc_wh": changes.get(dc),
                "efficiency": ratio(changes.get(ac), changes.get(dc)),
            }
            for name, (ac, dc) in sources.inverters.items()
        ],
    }


@dataclass(frozen=True, kw_only=True)
class BatteryCounters:
    """Statistics needed for a battery's daily efficiency."""

    name: str
    charge: str
    discharge: str
    soc: str
    capacity_wh: float


def battery_counters(
    config: SunflowConfig, profiles: dict[str, dict[str, Any]], snapshot: Snapshot
) -> list[BatteryCounters]:
    """Batteries with charge and discharge counters, SoC and capacity."""
    result = []
    for device in config.devices:
        profile = profiles[device.profile]
        roles = {
            r.role: r
            for r in preview(
                profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()}
            )
        }
        ids = [roles[r].entity_id if r in roles else None for r in ("battery_charge_energy", "battery_discharge_energy", "battery_soc")]
        capacity = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot)}.get("capacity_wh")
        if all(ids) and capacity:
            result.append(BatteryCounters(name=device.name, charge=ids[0], discharge=ids[1], soc=ids[2], capacity_wh=capacity))
    return result


# Days with less energy through the device give only measurement noise
MIN_BATTERY_CYCLE = 0.2
MIN_INVERTER_WH = 500


async def efficiency_history(
    client: HAClient,
    batteries: list[BatteryCounters],
    inverters: dict[str, tuple[str, str]],
    days: int,
    tz: tzinfo,
) -> list[dict[str, Any]]:
    """Daily efficiency per battery (round trip, SoC corrected) and per inverter (AC / DC)."""
    end = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    # One hour before for the SoC around the first midnight
    query_start = start - timedelta(hours=1)
    ids = [s for b in batteries for s in (b.charge, b.discharge, b.soc)] + [s for pair in inverters.values() for s in pair]
    if not ids:
        return []
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=query_start.isoformat(),
        # One hour more for the SoC at the end of the last day
        end_time=(end + timedelta(hours=1)).isoformat(),
        statistic_ids=sorted(set(ids)),
        period="hour",
        types=["change", "mean"],
        units={"energy": "Wh"},
    )

    def hours_in(day: date) -> int:
        """23, 24 or 25 hours, depending on daylight saving time changes."""
        midnight = datetime(day.year, day.month, day.day, tzinfo=tz)
        following = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        return round((following.astimezone(UTC) - midnight.astimezone(UTC)).total_seconds() / 3600)

    def per_day(statistic: str) -> dict[date, float]:
        """Daily totals of days without gaps (an hour without statistics would miss energy)."""
        totals: dict[date, float] = {}
        hours: dict[date, int] = {}
        for row in result.get(statistic, []):
            if row.get("change") is not None:
                day = datetime.fromtimestamp(row["start"] / 1000, tz).date()
                totals[day] = totals.get(day, 0.0) + row["change"]
                hours[day] = hours.get(day, 0) + 1
        return {day: total for day, total in totals.items() if hours[day] >= hours_in(day)}

    def soc_at_midnight(statistic: str) -> dict[date, float]:
        """SoC at each local midnight from the means of the hour before and after it."""
        means = {
            datetime.fromtimestamp(row["start"] / 1000, tz): row["mean"]
            for row in result.get(statistic, [])
            if row.get("mean") is not None
        }
        values = {}
        for moment, after in means.items():
            if moment.hour == 0 and (before := means.get(moment - timedelta(hours=1))) is not None:
                values[moment.date()] = (before + after) / 2
        return values

    dates = [(start + timedelta(days=i)).date() for i in range(days)]
    series = []
    for battery in batteries:
        charged, discharged, soc = per_day(battery.charge), per_day(battery.discharge), soc_at_midnight(battery.soc)
        points = []
        for day in dates:
            c, d = charged.get(day), discharged.get(day)
            begin, finish = soc.get(day), soc.get(day + timedelta(days=1))
            value = None
            if c and d is not None and begin is not None and finish is not None and c >= MIN_BATTERY_CYCLE * battery.capacity_wh:
                stored = battery.capacity_wh * (finish - begin) / 100
                root = stored * stored + 4 * c * d
                eta = (stored + math.sqrt(root)) / (2 * c) if root >= 0 else None
                value = eta * eta if eta else None
            points.append({"date": day.isoformat(), "value": value, "energy_wh": c})
        series.append({"name": battery.name, "kind": "battery", "points": points})
    for name, (ac_id, dc_id) in inverters.items():
        ac, dc = per_day(ac_id), per_day(dc_id)
        points = []
        for day in dates:
            a, d = ac.get(day), dc.get(day)
            value = a / d if a is not None and d and d >= MIN_INVERTER_WH else None
            points.append({"date": day.isoformat(), "value": value, "energy_wh": d})
        series.append({"name": name, "kind": "inverter", "points": points})
    return series
