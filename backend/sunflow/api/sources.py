"""Tariff, PV forecast planes, weather and what was learned about the PV forecast."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from sunflow.config.models import ForecastPlane, TariffConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.discovery import Snapshot
from sunflow.devices.resolver import preview
from sunflow.devices.site import pv_strings
from sunflow.ha import HAClient, HAError
from sunflow.planning.forecast_export import as_response, export_forecasts
from sunflow.planning.plan import cloud_coverage, weather_entity
from sunflow.planning.sources import (
    ForecastPoint,
    PricePoint,
    detect_price_adapter,
    forecast_solar_forecast,
    forecast_solar_planes,
    price_series,
    price_sources,
    solcast_forecast,
    solcast_sites,
    sum_forecasts,
)
from sunflow.shadow.runner import ShadowRunner

from .common import FORECAST_HOURS, _library, _local_tz, _replan, _slot_start, _snapshot

router = APIRouter()


@router.get("/sources")
async def get_sources(request: Request) -> dict[str, Any]:
    """Return price entities and PV forecast planes found in Home Assistant."""
    config = request.app.state.store.load()
    snapshot = await _snapshot(request)
    states = list(snapshot.states.values())
    solcast_entities = {
        e["entity_id"] for e in snapshot.entities if e.get("platform") == "solcast_solar"
    }
    stored = {(p.source, p.ref): p for p in config.forecast_planes}
    planes = []
    for site in [
        *solcast_sites(states, solcast_entities),
        *forecast_solar_planes(snapshot.entries, snapshot.devices, snapshot.entities, snapshot.states),
    ]:
        plane = stored.get((site.source, site.ref))
        planes.append(
            asdict(site)
            | {
                "device_id": plane.device_id if plane else None,
                "custom_name": plane.custom_name if plane else None,
                "strings": plane.strings if plane else [],
                "detected_name": site.name,
                "name": (plane.custom_name if plane else None) or site.name,
            }
        )
    # Strings a plane can be measured by, per plant
    strings = []
    for device in config.devices:
        profile = _library(request).profiles[device.profile]
        if "pv" not in profile["capabilities"]:
            continue
        roles = {
            r.role: r
            for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
        }
        strings += [
            {"id": f"{device.id}:{s['index']}", "device_id": device.id, "name": s["name"]} for s in pv_strings(roles)
        ]
    return {
        "prices": [asdict(p) for p in price_sources(states)],
        "forecast_planes": planes,
        "pv_strings": strings,
        "tariff": config.tariff.model_dump(),
    }


@router.put("/config/tariff")
async def set_tariff(request: Request, tariff: TariffConfig) -> dict[str, Any]:
    """Store the import price source."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.tariff = tariff
    store.save(config)
    _replan(request)
    return config.tariff.model_dump()


@router.put("/config/forecast")
async def set_forecast(
    request: Request, planes: list[ForecastPlane]
) -> list[dict[str, Any]]:
    """Store which forecast plane belongs to which plant."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    known = {d.id for d in config.devices}
    if any(p.device_id is not None and p.device_id not in known for p in planes):
        raise HTTPException(status_code=422, detail="Plant not configured")
    # The setup does not know the learning restart; it stays with its plane
    since = {p.key: p.learn_since for p in config.forecast_planes}
    config.forecast_planes = [p if p.learn_since else p.model_copy(update={"learn_since": since.get(p.key)}) for p in planes]
    store.save(config)
    _replan(request)
    return [p.model_dump(mode="json") for p in config.forecast_planes]


@router.post("/pv-learning/planes/{key}/restart")
async def restart_plane_learning(request: Request, key: str) -> dict[str, Any]:
    """Learn the correction of one plane anew from now on (after cleaning, new shading)."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    plane = next((p for p in config.forecast_planes if p.key == key), None)
    if plane is None:
        raise HTTPException(status_code=404, detail="Unknown forecast plane")
    plane.learn_since = datetime.now(UTC)
    store.save(config)
    runner: ShadowRunner = request.app.state.runner
    runner.relearn()
    _replan(request)
    return {"key": key, "learn_since": plane.learn_since.isoformat()}


@router.get("/tariff/series")
async def get_tariff_series(
    request: Request, entity_id: str | None = None, hours: Annotated[int, Query(ge=1, le=7 * 24)] = FORECAST_HOURS
) -> dict[str, Any]:
    """Return the import price schedule (€/kWh) of the given or configured source."""
    tariff = request.app.state.store.load().tariff
    entity_id = entity_id or tariff.import_entity_id
    if entity_id is None:
        if tariff.fixed_price_ct is None:
            raise HTTPException(status_code=404, detail="No price source configured")
        start = _slot_start()
        point = PricePoint(start=start, end=start + timedelta(hours=hours), price=tariff.fixed_price_ct / 100)
        return {"entity_id": None, "adapter": "fixed", "points": [asdict(point)]}
    snapshot = await _snapshot(request)
    if (state := snapshot.states.get(entity_id)) is None:
        raise HTTPException(status_code=404, detail=f"Unknown entity {entity_id}")
    points = price_series(state, _slot_start(), hours, await _local_tz(request))
    return {
        "entity_id": entity_id,
        "adapter": detect_price_adapter(state.get("attributes", {})),
        "points": [asdict(p) for p in points],
    }


@router.get("/forecast/series")
async def get_forecast_series(
    request: Request, hours: Annotated[int, Query(ge=1, le=7 * 24)] = FORECAST_HOURS
) -> dict[str, Any]:
    """Return the PV forecast per used plane (on a 30 minute grid) and its plant."""
    config = request.app.state.store.load()
    client: HAClient = request.app.state.ha
    start = _slot_start().replace(minute=0)
    names = {d.id: d.name for d in config.devices}
    planes: list[dict[str, Any]] = []
    try:
        for plane in config.forecast_planes:
            if plane.device_id is None:
                continue
            match plane.source:
                case "solcast":
                    points = await solcast_forecast(client, plane.ref, start, hours)
                case "forecast_solar":
                    points = await forecast_solar_forecast(client, plane.ref, start, hours)
                case _:
                    continue
            planes.append(
                {
                    "source": plane.source,
                    "ref": plane.ref,
                    "name": plane.display_name,
                    "device_id": plane.device_id,
                    "plant": names.get(plane.device_id, plane.device_id),
                    "points": [asdict(p) for p in sum_forecasts([points])],
                }
            )
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return {"planes": planes}


@router.get("/strings/energy")
async def get_string_energy(request: Request) -> dict[str, Any]:
    """Energy (Wh, counted by Sunflow) and power of every PV string, with plant and plane."""
    config = request.app.state.store.load()
    runner: ShadowRunner = request.app.state.runner
    counters = runner.counters.wh if runner.counters is not None else {}
    powers = runner.last_sample.strings if runner.last_sample is not None else {}
    names = {d.id: d.name for d in config.devices}
    planes = {s: p.display_name for p in config.forecast_planes for s in p.strings}
    strings = []
    for key in sorted(counters):
        device_id, _, index = key.partition(":")
        if device_id not in names:
            continue
        strings.append(
            {
                "id": key,
                "device_id": device_id,
                "plant": names[device_id],
                "name": f"{names[device_id]} String {index}",
                "plane": planes.get(key),
                "energy_wh": round(counters[key], 1),
                "power_w": powers.get(key),
            }
        )
    return {"strings": strings}


# The energy dashboard shows today and the days ahead
EXPORT_DAYS = 7


@router.get("/forecast/export")
async def get_forecast_export(request: Request) -> dict[str, Any]:
    """The learned PV forecast by hour from the start of today: per plane, per plant and in total."""
    config = request.app.state.store.load()
    client: HAClient = request.app.state.ha
    runner: ShadowRunner = request.app.state.runner
    tz = await _local_tz(request)
    now = datetime.now(UTC)
    midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = int((now - midnight).total_seconds() // 3600) + 1 + EXPORT_DAYS * 24

    async def fetch(plane: ForecastPlane) -> list[ForecastPoint]:
        match plane.source:
            case "solcast":
                return await solcast_forecast(client, plane.ref, midnight, hours)
            case "forecast_solar":
                return await forecast_solar_forecast(client, plane.ref, midnight, hours)
        return []

    try:
        site = await runner.pv_correction()
        rows = await asyncio.to_thread(runner.shadow.rows, midnight)
        recorded: dict[str, list[ForecastPoint]] = {}
        for row in rows:
            begin = datetime.fromisoformat(row["start"])
            for key, power in (row.get("plane_forecasts") or {}).items():
                if power is not None:
                    recorded.setdefault(key, []).append(
                        ForecastPoint(start=begin, end=begin + timedelta(minutes=15), power=power)
                    )
        targets = await export_forecasts(config, fetch, site, runner.plane_corrections, tz, recorded)
        targets = [t for t in targets if t.kind in config.integration.forecast_kinds]
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return as_response(targets, now, tz)


@router.get("/pv-learning")
async def get_pv_learning(request: Request) -> dict[str, Any]:
    """What was learned about the PV forecast of this site."""
    config = request.app.state.store.load()
    runner: ShadowRunner = request.app.state.runner
    correction = await runner.pv_correction()
    snapshot = await _snapshot(request)
    return correction.as_dict() | {
        "load_model": runner.load_model.as_dict() if runner.load_model else None,
        "planes": [
            {
                "key": plane.key,
                "name": plane.display_name,
                "strings": len(plane.strings),
                "learn_since": plane.learn_since.isoformat() if plane.learn_since else None,
            }
            | runner.plane_corrections[plane.key].as_dict()
            for plane in config.forecast_planes
            if plane.strings and plane.key in runner.plane_corrections
        ],
        "enabled": config.optimizer.pv_learning,
        "weather_entity": weather_entity(config, snapshot),
    }


@router.get("/weather/entities")
async def get_weather_entities(request: Request) -> list[dict[str, Any]]:
    """Weather entities for the cloud coverage, with whether their forecast has it."""
    snapshot = await _snapshot(request)
    client: HAClient = request.app.state.ha
    result = []
    for entity_id, state in sorted(snapshot.states.items()):
        if not entity_id.startswith("weather."):
            continue
        # Sunflow uses the hourly forecast; some providers (e.g. Open-Meteo) have the cloud
        # coverage only there, not in the current state
        clouds = await cloud_coverage(client, entity_id, [datetime.now(UTC) + timedelta(hours=h) for h in range(1, 7)])
        result.append(
            {
                "entity_id": entity_id,
                "name": state["attributes"].get("friendly_name", entity_id),
                "provider": weather_provider(entity_id, snapshot),
                "cloud_coverage": any(c is not None for c in clouds),
            }
        )
    return result


def weather_provider(entity_id: str, snapshot: Snapshot) -> str | None:
    """Name of the integration behind a weather entity (e.g. "Open-Meteo", "Meteorologisk institutt")."""
    entry = next((e for e in snapshot.entities if e["entity_id"] == entity_id), None)
    if entry is None:
        return None
    return snapshot.integration_names.get(entry.get("platform"), entry.get("platform"))
