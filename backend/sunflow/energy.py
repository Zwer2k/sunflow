"""Energy balance of the site from energy counters (kWh) in Home Assistant's statistics.

Counters are exact where integrated power means miss short peaks. Grid and load
come from the device chosen under "Messquellen"; on "automatic" the grid counters
of the energy dashboard win, then a dedicated meter, then an inverter.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config_store import SunflowConfig
from .discovery import Snapshot
from .ha import HAClient
from .resolver import preview
from .site import measurement_device

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
