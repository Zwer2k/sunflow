"""Live view: current flows, consumers and weather."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request

from sunflow.config.models import SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.site import (
    PV_OUTPUT_ROLES,
    device_kind,
    device_parameters,
    measurement_device,
    pv_strings,
    site_status,
)
from sunflow.ha import HAError
from sunflow.planning.plan import weather_entity
from sunflow.planning.vehicles import vehicle_at

from .common import _library, _local_tz, _snapshot

router = APIRouter()


@router.get("/live")
async def get_live(request: Request) -> dict[str, Any]:
    """Current power flows (W; battery + = charging, grid + = import).

    Components that are not configured are null or empty, so the view adapts:
    no PV, no battery, several batteries (combined), grid and load from any
    device that measures them.
    """
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    site = site_status(config.devices, profiles, snapshot, measured_by=config.measured_by)
    by_id = {d.id: d for d in config.devices}
    grid_device = measurement_device(config, profiles, "grid")
    load_device = measurement_device(config, profiles, "load")
    pv: list[dict[str, Any]] = []
    batteries: list[dict[str, Any]] = []
    consumers: list[dict[str, Any]] = []
    grid_w: float | None = None
    load_w: float | None = None
    # Unit and decimals of the source entities, so values look like in Home Assistant
    displays: dict[str, dict[str, Any]] = {}
    for status in site.devices:
        device = by_id[status.device_id]
        profile = profiles[device.profile]
        roles = {r.role: r for r in status.roles}

        def value(role: str) -> float | None:
            return roles[role].value if role in roles else None

        def display(role: str) -> dict[str, Any] | None:
            return roles[role].display if role in roles and roles[role].entity_id else None

        capabilities = status.capabilities
        if "pv" in capabilities:
            # Kept at night: inverters without sun often report "unavailable", that is 0 W
            pv.append(
                {
                    "name": device.name,
                    "power_w": status.pv_output or 0.0,
                    "available": status.pv_output is not None,
                    "display": next((display(r) for r in PV_OUTPUT_ROLES if value(r) is not None), None)
                    or next((display(r) for r in PV_OUTPUT_ROLES if display(r)), None),
                    "strings": pv_strings(roles),
                }
            )
        # The node stays without readings, e.g. while the inverter restarts
        if "battery" in capabilities:
            params = {
                p["name"]: p["effective"]
                for p in device_parameters(profile, device, snapshot, config.battery_modules.get(device.id))
            }
            batteries.append(
                {
                    "name": device.name,
                    "soc_pct": value("battery_soc"),
                    "capacity_wh": params.get("capacity_wh"),
                    "power_w": value("battery_power"),
                    "display": display("battery_power"),
                }
            )
        if grid_device is not None and device.id == grid_device.id:
            grid_w = value("grid_power")
            displays["grid"] = display("grid_power")
        if load_device is not None and device.id == load_device.id:
            load_w = value("load_power")
            displays["load"] = display("load_power")
        if "loadpoint" in capabilities or "load" in capabilities:
            kind = device_kind(profile, device, snapshot) or "load"
            # A wallbox with assigned vehicles shows the car it recognized, with the car's own SoC
            vehicle, vehicle_soc = None, None
            if kind == "vehicle":
                recognized, vehicle_soc = vehicle_at(config, device, roles, snapshot.states)
                vehicle = recognized.vehicle
            consumers.append(
                {
                    "name": device.name,
                    "kind": kind,
                    "power_w": value("charge_power") if "loadpoint" in capabilities else value("power"),
                    "display": display("charge_power" if "loadpoint" in capabilities else "power"),
                    "level": vehicle_soc if vehicle_soc is not None else value("vehicle_soc"),
                    "vehicle": vehicle.name if vehicle else None,
                    "level_unit": {"heater": "°C", "vehicle": "%"}.get(kind),
                    "charging": roles["charging"].raw_state == "on" if "charging" in roles else None,
                    "connected": roles["connected"].raw_state == "on" if "connected" in roles else None,
                }
            )
    return {
        "time": datetime.now(UTC).isoformat(),
        "pv": pv,
        "pv_w": sum(p["power_w"] for p in pv) if pv else None,
        "battery": combine_batteries(batteries),
        "grid_w": grid_w,
        "load_w": load_w,
        "consumers": consumers,
        "displays": displays,
        "weather": await _live_weather(request, config, snapshot),
    }


WEATHER_CACHE = timedelta(minutes=10)


# As far as the integration forecasts; the UI scrolls through them
WEATHER_HOURS = 48


WEATHER_DAYS = 16


async def _forecast(request: Request, entity_id: str, kind: str) -> list[dict[str, Any]]:
    """Hourly or daily forecast of a weather entity, cached; empty if the integration has none."""
    caches = getattr(request.app.state, "weather_cache", None)
    if not isinstance(caches, dict) or caches.get("entity") != entity_id:
        caches = {"entity": entity_id}
        request.app.state.weather_cache = caches
    now = datetime.now(UTC)
    cache = caches.get(kind)
    if cache is None or now - cache["at"] > WEATHER_CACHE:
        try:
            response = await request.app.state.ha.call_service(
                "weather", "get_forecasts", {"type": kind}, target={"entity_id": entity_id}, return_response=True
            )
            forecast = response["response"][entity_id]["forecast"]
        except (HAError, KeyError, TypeError):
            forecast = []
        cache = {"at": now, "forecast": forecast}
        caches[kind] = cache
    return cache["forecast"]


async def _live_weather(request: Request, config: SunflowConfig, snapshot: Snapshot) -> dict[str, Any] | None:
    """Current weather and the next hours of the configured weather entity (forecast cached)."""
    entity_id = weather_entity(config, snapshot)
    state = snapshot.states.get(entity_id or "")
    if not entity_id or state is None:
        return None
    now = datetime.now(UTC)
    hourly = await _forecast(request, entity_id, "hourly")
    upcoming = [f for f in hourly if "datetime" in f and datetime.fromisoformat(f["datetime"]) > now]
    tz = await _local_tz(request)
    today = now.astimezone(tz).date()
    # Daily entries are dated at local midnight (some integrations at noon); today included
    daily = [
        f for f in await _forecast(request, entity_id, "daily")
        if "datetime" in f and datetime.fromisoformat(f["datetime"]).astimezone(tz).date() >= today
    ]
    attributes = state["attributes"]
    return {
        "condition": state["state"],
        "temperature": attributes.get("temperature"),
        "temperature_unit": attributes.get("temperature_unit", "°C"),
        # Open-Meteo has no current cloud coverage; the next hour stands in
        "cloud": attributes.get("cloud_coverage", upcoming[0].get("cloud_coverage") if upcoming else None),
        "hours": [
            {
                "time": f["datetime"],
                "condition": f.get("condition"),
                "temperature": f.get("temperature"),
                "cloud": f.get("cloud_coverage"),
                "precipitation": f.get("precipitation"),
            }
            for f in upcoming[:WEATHER_HOURS]
        ],
        "days": [
            {
                "time": f["datetime"],
                "condition": f.get("condition"),
                "temperature": f.get("temperature"),
                "templow": f.get("templow"),
                "cloud": f.get("cloud_coverage"),
                "precipitation": f.get("precipitation"),
            }
            for f in daily[:WEATHER_DAYS]
        ],
    }


def combine_batteries(batteries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One battery node: summed power, SoC weighted by capacity (equal weights without)."""
    if not batteries:
        return None
    known = [b for b in batteries if b["soc_pct"] is not None]
    weights = [b["capacity_wh"] or 1.0 for b in known]
    soc = sum(b["soc_pct"] * w for b, w in zip(known, weights, strict=True)) / sum(weights) if known else None
    powers = [b["power_w"] for b in batteries if b["power_w"] is not None]
    return {
        "name": " + ".join(b["name"] for b in batteries),
        "count": len(batteries),
        "soc_pct": soc,
        "power_w": sum(powers) if powers else None,
        "parts": batteries if len(batteries) > 1 else [],
        "display": next((b["display"] for b in batteries if b.get("display")), None),
    }


@router.get("/site")
async def get_site(request: Request) -> dict[str, Any]:
    """Return live status, cross checks and PV total of all configured devices."""
    config = request.app.state.store.load()
    snapshot = await _snapshot(request)
    site = site_status(config.devices, _library(request).profiles, snapshot, measured_by=config.measured_by)
    result = asdict(site)
    for device in result["devices"]:
        for role in device["roles"]:
            role["missing"] = role["entity_id"] is None
    return result
