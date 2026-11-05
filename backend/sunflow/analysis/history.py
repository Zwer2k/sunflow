"""Measured history of the site from Home Assistant's statistics."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import fmean
from typing import Any

from sunflow.config.models import SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import controls_battery
from sunflow.devices.resolver import CANONICAL_SIGNS, preview
from sunflow.devices.site import PV_OUTPUT_ROLES, device_parameters, measurement_device
from sunflow.devices.state_history import state_means
from sunflow.ha import HAClient, HAError

# Short-term statistics are kept 10 days by default; above that use hourly
# Home Assistant keeps 5 minute statistics this long (recorder default), then hourly ones only
FIVE_MINUTE_LIMIT = timedelta(days=10)
# Sunflow's own recording: one row per quarter hour
RECORDED_SLOT = timedelta(minutes=15)


@dataclass(frozen=True, kw_only=True)
class Series:
    """One statistic and how to turn it into a site value."""

    key: str  # soc_wh, battery_w, pv_w, load_w, grid_w, loadpoint_w, level (vehicle %, tank °C)
    statistic_id: str
    factor: float  # sign flip and unit conversion (e.g. % -> Wh)
    # Controllable consumers and batteries are also returned one by one
    device_id: str | None = None


def history_series(config: SunflowConfig, profiles: dict[str, dict[str, Any]], snapshot: Snapshot) -> list[Series]:
    """Select the statistics for SoC, load and grid of the battery inverter and PV of all plants."""
    series: list[Series] = []
    grid_device = measurement_device(config, profiles, "grid")
    load_device = measurement_device(config, profiles, "load")
    for device in config.devices:
        profile = profiles[device.profile]
        roles = {
            r.role: r
            for r in preview(
                profile,
                snapshot,
                device.ha_device_id,
                {k: o.model_dump() for k, o in device.role_overrides.items()},
            )
        }

        def add(key: str, role: str, factor: float = 1.0, device_id: str | None = None) -> None:
            value = roles.get(role)
            if value is None or value.entity_id is None:
                return
            sign = -1.0 if value.sign is not None and value.sign not in CANONICAL_SIGNS else 1.0
            series.append(Series(key=key, statistic_id=value.entity_id, factor=factor * sign, device_id=device_id))

        if controls_battery(profile):
            params = {
                p["name"]: p["effective"]
                for p in device_parameters(profile, device, snapshot, config.battery_modules.get(device.id))
            }
            # Summed over all batteries like the plan; each one also by itself
            if capacity := params.get("capacity_wh"):
                add("soc_wh", "battery_soc", capacity / 100, device_id=device.id)
            add("battery_w", "battery_power")
        if load_device is not None and device.id == load_device.id:
            add("load_w", "load_power")
        if grid_device is not None and device.id == grid_device.id:
            add("grid_w", "grid_power")
        if "loadpoint" in profile["capabilities"]:
            add("loadpoint_w", "charge_power", device_id=device.id)
        elif "load" in profile["capabilities"]:
            add("loadpoint_w", "power", device_id=device.id)
        # Cars have their own line (below); a wallbox without assigned cars shows the SoC it knows
        if ("loadpoint" in profile["capabilities"] or "load" in profile["capabilities"]) and not config.vehicles_at(device.id):
            add("level", "vehicle_soc", device_id=device.id)
            add("level", "temperature", device_id=device.id)
        if "pv" in profile["capabilities"]:
            role = next((r for r in PV_OUTPUT_ROLES if r in roles and roles[r].entity_id), None)
            if role:
                add("pv_w", role)
    series.extend(
        Series(key="level", statistic_id=v.soc_entity, factor=1.0, device_id=level_key(v.id)) for v in config.vehicles if v.soc_entity
    )
    return series


async def fetch_history(
    client: HAClient, series: list[Series], start: datetime, end: datetime, bucket: timedelta
) -> list[dict[str, Any]]:
    """Return bucketed means per key; PV of several plants is summed."""
    if not series:
        return []
    # By age: older 5 minute statistics are purged, a window scrolled back gets hourly ones
    period = "5minute" if bucket < timedelta(hours=1) and start >= datetime.now(UTC) - FIVE_MINUTE_LIMIT else "hour"
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=sorted({s.statistic_id for s in series}),
        period=period,
        types=["mean"],
        units={"power": "W"},
    )
    for statistic_id in sorted({s.statistic_id for s in series} - {k for k, rows in result.items() if rows}):
        # A sensor without statistics (no state_class): means from its recorded states
        try:
            means = await state_means(client, statistic_id, start, end, bucket)
        except HAError:
            continue
        result[statistic_id] = [{"start": moment.timestamp() * 1000, "mean": value} for moment, value in means]
    # bucket start -> key -> statistic -> values
    buckets: dict[datetime, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    consumers: dict[datetime, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    levels: dict[datetime, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    batteries: dict[datetime, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for s in series:
        for row in result.get(s.statistic_id, []):
            if row.get("mean") is None:
                continue
            moment = datetime.fromtimestamp(row["start"] / 1000, UTC)
            offset = (moment - start) // bucket
            if s.key == "level":
                levels[start + offset * bucket][s.device_id or ""].append(row["mean"])
                continue
            buckets[start + offset * bucket][s.key][s.statistic_id].append(row["mean"] * s.factor)
            if s.key == "soc_wh" and s.device_id is not None:
                batteries[start + offset * bucket][s.device_id].append(row["mean"] * s.factor)
            elif s.device_id is not None:
                consumers[start + offset * bucket][s.device_id].append(max(row["mean"] * s.factor, 0.0))

    points = []
    for bucket_start in sorted(buckets):
        values = buckets[bucket_start]
        point: dict[str, Any] = {"start": bucket_start.isoformat(), "end": (bucket_start + bucket).isoformat()}
        for key in ("soc_wh", "battery_w", "pv_w", "load_w", "grid_w", "loadpoint_w"):
            per_statistic = values.get(key)
            # Mean per statistic, summed over statistics (several PV plants)
            point[key] = (
                sum(sum(v) / len(v) for v in per_statistic.values()) if per_statistic else None
            )
        point["consumers"] = {device_id: sum(v) / len(v) for device_id, v in consumers[bucket_start].items()}
        point["levels"] = {device_id: sum(v) / len(v) for device_id, v in levels.get(bucket_start, {}).items()}
        point["batteries"] = {device_id: sum(v) / len(v) for device_id, v in batteries.get(bucket_start, {}).items()}
        points.append(point)
    return points


def level_key(vehicle_id: str) -> str:
    """Key of a car's level in the history (devices use their id)."""
    return f"vehicle:{vehicle_id}"


def fill_from_shadow(points: list[dict[str, Any]], rows: list[dict[str, Any]], consumers: set[str]) -> None:
    """Consumer power from Sunflow's own minute samples, ahead of Home Assistant's history; levels where it has none.

    A sensor excluded from the recorder (e.g. a chatty wallbox) has no history, or only its
    last recorded state, which then holds for hours (e.g. 0 W while the car charges). The
    own samples read the same sensors every minute. Older slots only hold the sum of all
    consumers: it goes to the one consumer without any value.
    """
    all_rows = [(datetime.fromisoformat(r["start"]), r) for r in rows]
    starts = [(start, r) for start, r in all_rows if r.get("actual_loadpoint_w") is not None]

    def overlapping(recorded: list[tuple[datetime, dict[str, Any]]], begin: datetime, end: datetime) -> list[dict[str, Any]]:
        # A recorded quarter hour counts for every point it overlaps (a 5 minute point lies inside one)
        return [r for start, r in recorded if start < end and start + RECORDED_SLOT > begin]

    for point in points:
        begin, end = datetime.fromisoformat(point["start"]), datetime.fromisoformat(point["end"])
        inside = overlapping(starts, begin, end)
        if not inside:
            continue
        total = fmean(r["actual_loadpoint_w"] for r in inside)
        point["loadpoint_w"] = max(point.get("loadpoint_w") or 0.0, total)
        known = point.setdefault("consumers", {})
        for device_id in consumers:
            values = [r["actual_consumers"][device_id] for r in inside if device_id in (r.get("actual_consumers") or {})]
            if values:
                known[device_id] = fmean(values)
        missing = consumers - known.keys()
        if len(missing) == 1:
            known[missing.pop()] = max(total - sum(known.values()), 0.0)
    for point in points:
        begin, end = datetime.fromisoformat(point["start"]), datetime.fromisoformat(point["end"])
        own: dict[str, list[float]] = {}
        prices: list[float] = []
        batteries: list[float] = []
        for r in overlapping(all_rows, begin, end):
            if r.get("actual_battery_w") is not None:
                batteries.append(r["actual_battery_w"])
            for device_id, value in (r.get("actual_levels") or {}).items():
                own.setdefault(device_id, []).append(value)
            # The import price the slot was planned with: the tariff that applied then
            if r.get("planned_price") is not None:
                prices.append(r["planned_price"])
        if prices:
            point["price"] = fmean(prices)
        # A battery power sensor without statistics: from the own samples
        if point.get("battery_w") is None and batteries:
            point["battery_w"] = fmean(batteries)
        levels = point.setdefault("levels", {})
        for device_id, values in own.items():
            # Sensors without statistics (e.g. a car's MQTT sensor) from the own samples
            levels.setdefault(device_id, fmean(values))
