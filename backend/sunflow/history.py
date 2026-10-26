"""Measured history of the site from Home Assistant's statistics."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .config_store import SunflowConfig
from .discovery import Snapshot
from .ha import HAClient
from .resolver import CANONICAL_SIGNS, preview
from .site import PV_OUTPUT_ROLES, device_parameters, measurement_device

# Short-term statistics are kept 10 days by default; above that use hourly
FIVE_MINUTE_LIMIT = timedelta(days=10)


@dataclass(frozen=True, kw_only=True)
class Series:
    """One statistic and how to turn it into a site value."""

    key: str  # soc_wh, pv_w, load_w, grid_w, loadpoint_w
    statistic_id: str
    factor: float  # sign flip and unit conversion (e.g. % -> Wh)
    # Controllable consumers are also returned one by one
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

        if "battery_window" in profile["capabilities"]:
            params = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot)}
            if capacity := params.get("capacity_wh"):
                add("soc_wh", "battery_soc", capacity / 100)
        if load_device is not None and device.id == load_device.id:
            add("load_w", "load_power")
        if grid_device is not None and device.id == grid_device.id:
            add("grid_w", "grid_power")
        if "loadpoint" in profile["capabilities"]:
            add("loadpoint_w", "charge_power", device_id=device.id)
        elif "load" in profile["capabilities"]:
            add("loadpoint_w", "power", device_id=device.id)
        if "pv" in profile["capabilities"]:
            role = next((r for r in PV_OUTPUT_ROLES if r in roles and roles[r].entity_id), None)
            if role:
                add("pv_w", role)
    return series


async def fetch_history(
    client: HAClient, series: list[Series], start: datetime, end: datetime, bucket: timedelta
) -> list[dict[str, Any]]:
    """Return bucketed means per key; PV of several plants is summed."""
    if not series:
        return []
    period = "5minute" if end - start <= FIVE_MINUTE_LIMIT else "hour"
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=sorted({s.statistic_id for s in series}),
        period=period,
        types=["mean"],
        units={"power": "W"},
    )
    # bucket start -> key -> statistic -> values
    buckets: dict[datetime, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    consumers: dict[datetime, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for s in series:
        for row in result.get(s.statistic_id, []):
            if row.get("mean") is None:
                continue
            moment = datetime.fromtimestamp(row["start"] / 1000, UTC)
            offset = (moment - start) // bucket
            buckets[start + offset * bucket][s.key][s.statistic_id].append(row["mean"] * s.factor)
            if s.device_id is not None:
                consumers[start + offset * bucket][s.device_id].append(max(row["mean"] * s.factor, 0.0))

    points = []
    for bucket_start in sorted(buckets):
        values = buckets[bucket_start]
        point: dict[str, Any] = {"start": bucket_start.isoformat(), "end": (bucket_start + bucket).isoformat()}
        for key in ("soc_wh", "pv_w", "load_w", "grid_w", "loadpoint_w"):
            per_statistic = values.get(key)
            # Mean per statistic, summed over statistics (several PV plants)
            point[key] = (
                sum(sum(v) / len(v) for v in per_statistic.values()) if per_statistic else None
            )
        point["consumers"] = {device_id: sum(v) / len(v) for device_id, v in consumers[bucket_start].items()}
        points.append(point)
    return points
