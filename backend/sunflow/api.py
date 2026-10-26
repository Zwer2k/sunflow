"""HTTP API of the backend."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
import io
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import tempfile
from typing import Annotated, Any
import sqlite3
import uuid
import zipfile
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, FiniteFloat

from . import API_VERSION, __version__
from .config_store import (
    BATTERY_PRIORITY_ID,
    ConfigError,
    ConfigStore,
    DeviceOverride,
    PriorityCondition,
    StrategyOverride,
    SunflowConfig,
    DeviceConfig,
    FeedIn,
    FlexSettings,
    ForecastPlane,
    MeasuredBy,
    OptimizerConfig,
    RoleOverride,
    TariffConfig,
)
from .device_test import DeviceTestState, ModeControl, RestoreMarker, run_device_test, state_response
from .discovery import Snapshot, SnapshotCache, discover, fetch_snapshot
from .executor import (
    ActionResult,
    EepromGuard,
    ExecutorError,
    PlannedCall,
    execute,
    find_entity,
    plan_action,
    plan_setup,
)
from .ha import HAClient, HAError
from .efficiency import EfficiencyEstimate, estimate_from_counters, estimate_from_history
from .energy import battery_counters, efficiency_history, energy_balance, energy_sources
from .history import fetch_history, history_series
from .profiles import ProfileLibrary, load_profiles, load_schema
from .resolver import CANONICAL_SIGNS, check_balance, device_entities, preview
from .settings import Settings
from .costs import cost_history
from .plan import cloud_coverage, feed_in_price, weather_entity
from .recommendations import build as build_recommendations
from .runner import ShadowRunner, current_plan
from .shadow import ShadowStore, actual_action, evaluate as evaluate_shadow
from .site import MEASUREMENTS, PV_OUTPUT_ROLES, device_kind, device_parameters, measurement_candidates, measurement_device, site_status
from .summary import SetupSummary, build_summary
from .sources import (
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

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAYS = (1, 2, 5, 10, 30)
DEFAULT_MAX_EEPROM_WRITES = 10

router = APIRouter(prefix=f"/api/{API_VERSION}")


class AddDevice(BaseModel):
    """Request body to add a discovered device."""

    ha_device_id: str
    profile: str | None = None
    role_overrides: dict[str, RoleOverride] = {}


async def _snapshot(request: Request, fresh: bool = False) -> Snapshot:
    try:
        return await request.app.state.snapshots.get(fresh=fresh)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


def _library(request: Request) -> ProfileLibrary:
    return request.app.state.profiles


def _configured_device(request: Request, device_id: str) -> DeviceConfig:
    config = request.app.state.store.load()
    if (device := config.device(device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    return device


def display_name(device: Any, profile: dict[str, Any]) -> str:
    """Name for a new device: one-word names like "Inverter" are ambiguous, so use
    manufacturer and model there; profiles may strip noise from names."""
    name = device.name
    for part in profile.get("name_strip", []):
        name = name.replace(part, "")
    name = name.strip()
    if len(name.split()) <= 1:
        name = " ".join(filter(None, (device.manufacturer, device.model))) or name
    return name


def _guard(request: Request, profile: dict[str, Any]) -> EepromGuard:
    limit = profile.get("eeprom_guard", {}).get(
        "max_writes_per_day", DEFAULT_MAX_EEPROM_WRITES
    )
    return EepromGuard(request.app.state.settings.data_dir, limit)


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Return backend status and versions."""
    client: HAClient | None = getattr(request.app.state, "ha", None)
    return {
        "status": "ok",
        "version": __version__,
        "api": API_VERSION,
        "ha_connected": client is not None and client.connected,
        "ha_version": client.ha_version if client else None,
    }


@router.get("/profiles")
async def profiles(request: Request) -> dict[str, Any]:
    """Return loaded profiles and load errors."""
    library = _library(request)
    return {
        "profiles": sorted(library.profiles),
        "errors": [{"path": str(e.path), "message": e.message} for e in library.errors],
    }


@router.get("/discovery")
async def discovery(request: Request) -> list[dict[str, Any]]:
    """Return integrations and devices found in Home Assistant."""
    # Newly installed integrations must show up right away
    snapshot = await _snapshot(request, fresh=True)
    return [asdict(i) for i in discover(snapshot, _library(request))]


class PreviewRequest(BaseModel):
    """Preview with unsaved role overrides."""

    profile: str | None = None
    role_overrides: dict[str, RoleOverride] = {}


@router.get("/devices/{device_id}/entities")
async def device_entity_list(request: Request, device_id: str) -> list[dict[str, Any]]:
    """Return the entities of a device and its sub-devices for manual mapping."""
    snapshot = await _snapshot(request)
    result = []
    for entity in device_entities(snapshot, device_id):
        state = snapshot.states.get(entity["entity_id"], {})
        attributes = state.get("attributes", {})
        result.append(
            {
                "entity_id": entity["entity_id"],
                "name": attributes.get("friendly_name") or entity["entity_id"],
                "state": state.get("state"),
                "unit": attributes.get("unit_of_measurement"),
                "device_class": attributes.get("device_class"),
            }
        )
    return sorted(result, key=lambda e: e["entity_id"])


@router.get("/devices/{device_id}/preview")
async def device_preview(
    request: Request, device_id: str, profile: str | None = None
) -> dict[str, Any]:
    """Return the live, normalized role values of a Home Assistant device.

    Without a profile parameter the profile found by discovery is used.
    Role overrides of a configured device are applied.
    """
    configured = next(
        (
            d
            for d in request.app.state.store.load().devices
            if d.ha_device_id == device_id
        ),
        None,
    )
    overrides = configured.role_overrides if configured else {}
    return await _preview(request, device_id, profile, overrides)


@router.post("/devices/{device_id}/preview")
async def device_preview_with_overrides(
    request: Request, device_id: str, body: PreviewRequest
) -> dict[str, Any]:
    """Return the preview with unsaved role overrides (advanced mode)."""
    return await _preview(request, device_id, body.profile, body.role_overrides)


async def _preview(
    request: Request,
    device_id: str,
    profile: str | None,
    overrides: dict[str, RoleOverride],
) -> dict[str, Any]:
    library = _library(request)
    snapshot = await _snapshot(request)

    if profile is None:
        devices = {
            d.device_id: d for i in discover(snapshot, library) for d in i.devices
        }
        if device_id not in devices:
            raise HTTPException(status_code=404, detail="Device not found")
        profile = devices[device_id].profile
        if profile is None:
            raise HTTPException(status_code=422, detail="No matching profile")
    if profile not in library.profiles:
        raise HTTPException(status_code=404, detail=f"Unknown profile {profile}")

    roles = preview(
        library.profiles[profile],
        snapshot,
        device_id,
        {role: o.model_dump() for role, o in overrides.items()},
    )
    balance = check_balance(roles)
    return {
        "device_id": device_id,
        "profile": profile,
        "complete": not any(r.missing and not r.optional for r in roles),
        "roles": [asdict(r) | {"missing": r.missing} for r in roles],
        "balance": asdict(balance) if balance else None,
    }


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    """Return the stored configuration."""
    return request.app.state.store.load().model_dump(mode="json")


@router.post("/config/devices")
async def add_device(request: Request, body: AddDevice) -> dict[str, Any]:
    """Add a discovered device to the configuration."""
    library = _library(request)
    store: ConfigStore = request.app.state.store
    snapshot = await _snapshot(request)
    # Load after the await: a parallel add in the meantime must not be overwritten
    config = store.load()
    if any(d.ha_device_id == body.ha_device_id for d in config.devices):
        raise HTTPException(status_code=409, detail="Device already configured")
    found = {d.device_id: d for i in discover(snapshot, library) for d in i.devices}
    if (discovered := found.get(body.ha_device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    profile_id = body.profile or discovered.profile
    if profile_id not in library.profiles:
        raise HTTPException(status_code=422, detail="No matching profile")

    device = DeviceConfig(
        ha_device_id=body.ha_device_id,
        profile=profile_id,
        profile_version=library.profiles[profile_id]["version"],
        name=display_name(discovered, library.profiles[profile_id]),
        role_overrides=body.role_overrides,
    )
    config.devices.append(device)
    store.save(config)
    _replan(request)
    return device.model_dump()


class ManualDevice(BaseModel):
    """A switchable consumer added by hand (Shelly, ESPHome, smart plug ...)."""

    name: str
    switch_entity: str
    power_entity: str | None = None
    max_power: FiniteFloat = Field(default=500, gt=0, le=100_000)
    daily_energy_wh: FiniteFloat | None = Field(default=None, ge=0)


MANUAL_PROFILE = "generic/switch"
SWITCH_DOMAINS = ("switch.", "input_boolean.")


@router.get("/entities/candidates")
async def entity_candidates(request: Request) -> dict[str, list[dict[str, Any]]]:
    """Entities that can switch a consumer, and power sensors to measure it."""
    snapshot = await _snapshot(request)
    switches, power = [], []
    for entity_id, state in sorted(snapshot.states.items()):
        attributes = state.get("attributes", {})
        item = {
            "entity_id": entity_id,
            "name": attributes.get("friendly_name") or entity_id,
            "state": state.get("state"),
            "unit": attributes.get("unit_of_measurement"),
        }
        if entity_id.startswith(SWITCH_DOMAINS):
            switches.append(item)
        elif entity_id.startswith("sensor.") and attributes.get("device_class") == "power":
            power.append(item)
    return {"switches": switches, "power": power}


@router.post("/config/devices/manual")
async def add_manual_device(request: Request, body: ManualDevice) -> dict[str, Any]:
    """Add a switchable consumer that is not found by discovery."""
    library = _library(request)
    if MANUAL_PROFILE not in library.profiles:
        raise HTTPException(status_code=500, detail="Generic profile missing")
    if not body.switch_entity.startswith(SWITCH_DOMAINS):
        raise HTTPException(status_code=422, detail="Switch entity must be a switch or input_boolean")
    store: ConfigStore = request.app.state.store
    config = store.load()
    overrides = {"switch": RoleOverride(entity_id=body.switch_entity)}
    if body.power_entity:
        overrides["power"] = RoleOverride(entity_id=body.power_entity)
    parameters = {"max_power": body.max_power}
    if body.daily_energy_wh is not None:
        parameters["daily_energy_wh"] = body.daily_energy_wh
    device = DeviceConfig(
        ha_device_id=f"manual:{uuid.uuid4().hex}",
        profile=MANUAL_PROFILE,
        profile_version=library.profiles[MANUAL_PROFILE]["version"],
        name=body.name.strip() or body.switch_entity,
        parameters=parameters,
        role_overrides=overrides,
    )
    config.devices.append(device)
    store.save(config)
    _replan(request)
    return device.model_dump(mode="json")


@router.put("/config/devices/manual/{device_id}")
async def update_manual_device(request: Request, device_id: str, body: ManualDevice) -> dict[str, Any]:
    """Change name, entities, power and daily energy of a consumer added by hand."""
    if not body.switch_entity.startswith(SWITCH_DOMAINS):
        raise HTTPException(status_code=422, detail="Switch entity must be a switch or input_boolean")
    store: ConfigStore = request.app.state.store
    config = store.load()
    if (device := config.device(device_id)) is None or device.profile != MANUAL_PROFILE:
        raise HTTPException(status_code=404, detail="Manual device not configured")
    device.name = body.name.strip() or body.switch_entity
    device.role_overrides = {"switch": RoleOverride(entity_id=body.switch_entity)}
    if body.power_entity:
        device.role_overrides["power"] = RoleOverride(entity_id=body.power_entity)
    device.parameters["max_power"] = body.max_power
    if body.daily_energy_wh is None:
        device.parameters.pop("daily_energy_wh", None)
    else:
        device.parameters["daily_energy_wh"] = body.daily_energy_wh
    store.save(config)
    _replan(request)
    return device.model_dump(mode="json")


@router.put("/config/devices/{device_id}/role_overrides")
async def set_role_overrides(
    request: Request, device_id: str, overrides: dict[str, RoleOverride] = Body(...)
) -> dict[str, Any]:
    """Replace the role overrides of a configured device."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if (device := config.device(device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    device.role_overrides = overrides
    store.save(config)
    return device.model_dump()


class DeviceUpdate(BaseModel):
    """Partial update of a configured device; omitted fields stay unchanged."""

    name: str | None = None
    # NaN or Infinity would make config.json unreadable for the UI and break the plan
    parameters: dict[str, FiniteFloat | None] | None = None
    feed_in: FeedIn | None = None
    measured_by: MeasuredBy | None = None
    clear_measured_by: bool = False
    flex: FlexSettings | None = None


@router.patch("/config/devices/{device_id}")
async def update_device(
    request: Request, device_id: str, body: DeviceUpdate
) -> dict[str, Any]:
    """Update parameters (None removes a value), feed-in and measurement link."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if (device := config.device(device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    if body.name is not None and body.name.strip():
        device.name = body.name.strip()
    if body.parameters is not None:
        for name, value in body.parameters.items():
            if value is None:
                device.parameters.pop(name, None)
            else:
                device.parameters[name] = value
    if body.feed_in is not None:
        device.feed_in = body.feed_in
    if body.flex is not None:
        device.flex = body.flex
    if body.clear_measured_by:
        device.measured_by = None
    elif body.measured_by is not None:
        if config.device(body.measured_by.device_id) is None:
            raise HTTPException(status_code=422, detail="Measuring device not configured")
        device.measured_by = body.measured_by
    store.save(config)
    _replan(request)
    return device.model_dump()


@router.get("/config/devices/{device_id}/parameters")
async def get_parameters(request: Request, device_id: str) -> list[dict[str, Any]]:
    """Return profile parameters with stored, derived and default values."""
    device = _configured_device(request, device_id)
    snapshot = await _snapshot(request)
    return device_parameters(_library(request).profiles[device.profile], device, snapshot)


FORECAST_HOURS = 48


async def _local_tz(request: Request) -> ZoneInfo:
    config = await request.app.state.ha.command("get_config")
    return ZoneInfo(config["time_zone"])


def _slot_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)


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
                "detected_name": site.name,
                "name": (plane.custom_name if plane else None) or site.name,
            }
        )
    return {
        "prices": [asdict(p) for p in price_sources(states)],
        "forecast_planes": planes,
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
    config.forecast_planes = planes
    store.save(config)
    _replan(request)
    return [p.model_dump() for p in planes]


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


def _tests(request: Request) -> dict[str, tuple[DeviceTestState, asyncio.Task[None]]]:
    return request.app.state.device_tests


def _device_control(
    request: Request, device: DeviceConfig, profile: dict[str, Any], calls: list[PlannedCall]
) -> tuple[Any, Any, ModeControl | None]:
    """Read battery values, run profile actions and switch the control mode (real writes)."""
    client: HAClient = request.app.state.ha
    overrides = {r: o.model_dump() for r, o in device.role_overrides.items()}
    guard = _guard(request, profile)

    async def read() -> tuple[float | None, float | None]:
        snapshot = await fetch_snapshot(client)
        roles = {r.role: r.value for r in preview(profile, snapshot, device.ha_device_id, overrides)}
        return roles.get("battery_power"), roles.get("battery_soc")

    async def apply_action(action: str, runtime: dict[str, float]) -> list[PlannedCall]:
        snapshot = await fetch_snapshot(client)
        _, planned = plan_action(
            profile, action, device.parameters, runtime, device_entities(snapshot, device.ha_device_id)
        )
        await execute(client, planned, snapshot.states, guard, dry_run=False)
        return planned

    if not (control_mode := profile.get("control_mode")):
        return read, apply_action, None

    async def set_mode(option: str) -> str | None:
        """Select option if needed; return the previous option when switched."""
        snapshot = await fetch_snapshot(client)
        entities = device_entities(snapshot, device.ha_device_id)
        if (entity_id := find_entity(entities, "select", control_mode["key"])) is None:
            raise ExecutorError(f"No select entity for key {control_mode['key']}")
        current = snapshot.states.get(entity_id, {}).get("state")
        if current == option:
            return None
        call = PlannedCall(
            domain="select",
            service="select_option",
            data={"option": option},
            entity_id=entity_id,
            storage="eeprom",
            write="always",
        )
        await execute(client, [call], snapshot.states, guard, dry_run=False)
        calls.append(call)
        if call.status != "done":
            raise ExecutorError(f"Mode switch {call.status}: {call.reason}")
        return current

    async def ensure_mode() -> str | None:
        return await set_mode(control_mode["option"])

    async def restore_mode(option: str) -> None:
        await set_mode(option)

    return read, apply_action, ModeControl(ensure=ensure_mode, set=restore_mode)


@router.get("/device-test/pending-restore")
async def get_pending_restore(request: Request) -> dict[str, Any] | None:
    """A device test that wrote to the device and was not undone (e.g. app crash, lost connection)."""
    marker = request.app.state.restore_marker.get()
    if marker is None:
        return None
    device = request.app.state.store.load().device(marker.get("device_id") or "")
    return marker | {"device_name": device.name if device else None}


@router.post("/device-test/restore")
async def restore_after_test(request: Request, confirm: bool = False) -> dict[str, Any]:
    """Return the device of an interrupted test to self-consumption and its previous mode."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Restoring writes to the device; pass confirm=true")
    marker_store: RestoreMarker = request.app.state.restore_marker
    if (marker := marker_store.get()) is None:
        return {"restored": True, "calls": []}
    config = request.app.state.store.load()
    device = config.device(marker.get("device_id") or "") or next(
        (d for d in config.devices if "battery_window" in _library(request).profiles[d.profile]["capabilities"]), None
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Kein steuerbarer Speicher konfiguriert")
    if (running := _tests(request).get(device.id)) and running[0].running:
        raise HTTPException(status_code=409, detail="Der Gerätetest läuft noch und setzt selbst zurück")
    profile = _library(request).profiles[device.profile]
    calls: list[PlannedCall] = []
    _, apply_action, mode = _device_control(request, device, profile, calls)
    try:
        calls += await apply_action("auto", {})
        if mode is not None and marker.get("previous_mode"):
            await mode.set(marker["previous_mode"])
    except ExecutorError as err:
        raise HTTPException(status_code=502, detail=f"Zurücksetzen fehlgeschlagen: {err}") from err
    restored = all(c.status == "done" for c in calls)
    if restored:
        marker_store.set(None)
    return {"restored": restored, "calls": [asdict(c) for c in calls]}


@router.post("/config/devices/{device_id}/test")
async def start_device_test(
    request: Request, device_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Start the device test. Writes to the device, so confirm=true is required."""
    if not confirm:
        raise HTTPException(status_code=400, detail="The device test writes to the device; pass confirm=true")
    device = _configured_device(request, device_id)
    profile = _library(request).profiles[device.profile]
    if "battery_window" not in profile["capabilities"]:
        raise HTTPException(status_code=422, detail="Device has no battery control")
    if (running := _tests(request).get(device_id)) and running[0].running:
        raise HTTPException(status_code=409, detail="Test already running")

    state = DeviceTestState(device_id=device_id)
    read, apply_action, mode = _device_control(request, device, profile, state.calls)
    marker: RestoreMarker = request.app.state.restore_marker
    store: ConfigStore = request.app.state.store

    async def run() -> None:
        await run_device_test(state, read, apply_action, mode=mode, mark=marker.set)
        if state.phase == "done" and state.control_works and state.sign_correct and state.restored:
            config = store.load()
            if (stored := config.device(device_id)) is not None:
                stored.test_passed_at = state.finished
                store.save(config)

    _tests(request)[device_id] = (state, asyncio.create_task(run()))
    return state_response(state)


@router.get("/config/devices/{device_id}/test")
async def get_device_test(request: Request, device_id: str) -> dict[str, Any]:
    """Return progress and result of the last device test."""
    if (entry := _tests(request).get(device_id)) is None:
        raise HTTPException(status_code=404, detail="No test run")
    return state_response(entry[0])


@router.post("/config/devices/{device_id}/test/abort")
async def abort_device_test(request: Request, device_id: str) -> dict[str, Any]:
    """Abort a running test; the device is restored to self-consumption."""
    if (entry := _tests(request).get(device_id)) is None:
        raise HTTPException(status_code=404, detail="No test run")
    state, task = entry
    if state.running:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return state_response(state)


def _summary_response(summary: SetupSummary) -> dict[str, Any]:
    return asdict(summary) | {"can_complete": summary.can_complete}


@router.get("/setup/summary")
async def get_setup_summary(request: Request) -> dict[str, Any]:
    """Return configured items and open issues of the setup."""
    config = request.app.state.store.load()
    return _summary_response(build_summary(config, _library(request).profiles))


@router.post("/setup/complete")
async def complete_setup(request: Request) -> dict[str, Any]:
    """Mark the setup as completed if nothing blocks it."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    summary = build_summary(config, _library(request).profiles)
    if not summary.can_complete:
        raise HTTPException(status_code=409, detail="Setup has open errors")
    config.setup_completed = True
    store.save(config)
    summary.completed = True
    return _summary_response(summary)


@router.put("/config/optimizer")
async def set_optimizer(request: Request, body: OptimizerConfig) -> dict[str, Any]:
    """Store planning settings."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.optimizer = body
    store.save(config)
    _replan(request)
    return body.model_dump()


MEASUREMENT_LABELS = {"grid": "Netz (Bezug und Einspeisung)", "load": "Hausverbrauch"}


def _measurements(config: SunflowConfig, profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for quantity in MEASUREMENTS:
        candidates = measurement_candidates(config.devices, profiles, quantity)
        effective = measurement_device(config, profiles, quantity)
        result[quantity] = {
            "label": MEASUREMENT_LABELS.get(quantity, quantity),
            # None: automatic
            "selected": config.measurement_sources.get(quantity),
            "effective": effective.id if effective else None,
            "options": [{"id": d.id, "name": d.name} for d in candidates],
        }
    return result


@router.get("/config/measurements")
async def get_measurements(request: Request) -> dict[str, Any]:
    """Which device measures grid and load, with the choices per quantity."""
    return _measurements(request.app.state.store.load(), _library(request).profiles)


@router.put("/config/measurements")
async def set_measurements(request: Request, body: dict[str, str | None]) -> dict[str, Any]:
    """Choose the device per quantity; null returns to the automatic choice."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    profiles = _library(request).profiles
    for quantity, device_id in body.items():
        if quantity not in MEASUREMENTS:
            raise HTTPException(status_code=422, detail=f"Unknown quantity: {quantity}")
        if device_id is None:
            config.measurement_sources.pop(quantity, None)
        elif device_id in {d.id for d in measurement_candidates(config.devices, profiles, quantity)}:
            config.measurement_sources[quantity] = device_id
        else:
            raise HTTPException(status_code=422, detail=f"{device_id} cannot measure {quantity}")
    store.save(config)
    _replan(request)
    return _measurements(config, profiles)


def plan_response(result: Any, config: SunflowConfig) -> dict[str, Any]:
    """Serialize a plan result for the UI."""
    plan = result.plan
    return {
        "status": plan.status,
        "warnings": result.warnings,
        "price_source": result.price_source,
        "pv_planes": result.pv_planes,
        "load_quality": result.load.quality if result.load else None,
        "load_history_hours": result.load.history_hours if result.load else 0,
        "load_scaled_to_kwh": result.load.scaled_to_kwh if result.load else None,
        "load_history_daily_kwh": result.load.history_daily_kwh if result.load else None,
        "load_excluded_hours": result.load.excluded_hours if result.load else 0,
        "load_separated_hours": result.load.separated_hours if result.load else 0,
        "settings": config.optimizer.model_dump(),
        "strategy": config.effective_strategy(datetime.now(UTC)),
        "cost": plan.cost,
        "baseline_cost": plan.baseline_cost,
        "alternative_cost": plan.alternative_cost,
        "pv_correction": result.pv_correction.as_dict() if result.pv_correction and result.pv_correction.slots else None,
        "load_model": result.load_model.as_dict() if result.load_model else None,
        "usages": {device_id: asdict(usage) for device_id, usage in result.usages.items()},
        "baseline_alternative_cost": plan.baseline_alternative_cost,
        "baseline_slot_costs": plan.baseline_slot_costs,
        "terminal_value": plan.terminal_value,
        "baseline_terminal_value": plan.baseline_terminal_value,
        "savings": plan.savings if plan.slots else None,
        "battery": {
            "capacity_wh": result.battery.capacity_wh,
            "min_soc_wh": result.battery.min_soc_wh,
            "reserve_wh": result.reserve_wh,
            "limit_wh": result.care.limit_wh if result.care else None,
            "full_charge_at": result.care.full_charge_at.isoformat() if result.care and result.care.full_charge_at else None,
            "last_full": result.care.last_full.isoformat() if result.care and result.care.last_full else None,
        }
        if result.battery
        else None,
        "flex": [
            asdict(info) | next(
                ({"planned_wh": r.planned_wh, "unmet_wh": r.unmet_wh} for r in plan.flex if r.name == info.name),
                {"planned_wh": 0.0, "unmet_wh": 0.0},
            )
            for info in result.flex
        ],
        "slots": [asdict(slot) for slot in plan.slots],
    }


@router.get("/plan")
async def get_plan(request: Request) -> dict[str, Any]:
    """Optimize the battery schedule for the next hours (read only, nothing is controlled)."""
    try:
        runner = getattr(request.app.state, "runner", None)
        result = await current_plan(
            request.app.state.ha,
            request.app.state.store,
            _library(request),
            request.app.state.snapshots,
            await runner.pv_correction() if runner else None,
            runner.load_model if runner else None,
        )
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    # "Neu berechnen" must not be replaced by an older background plan on the next poll
    if (runner := getattr(request.app.state, "runner", None)) is not None:
        runner.latest = result
    return plan_response(result, request.app.state.store.load())


@router.get("/plan/latest")
async def get_latest_plan(request: Request) -> dict[str, Any]:
    """Return the plan of the background runner (computed at most 15 minutes ago)."""
    runner: ShadowRunner = request.app.state.runner
    # Without a plan yet, all open tabs wait for one shared computation
    if (latest := await runner.ensure_plan()) is None:
        raise HTTPException(status_code=503, detail=runner.status.last_error or "Plan wird noch berechnet")
    sample = runner.last_sample
    return plan_response(latest, request.app.state.store.load()) | {
        "computed": runner.status.last_plan.isoformat() if runner.status.last_plan else None,
        # What the battery does right now (Sunflow does not control yet; another system may)
        "actual": asdict(sample)
        | {"time": runner.last_sample_at.isoformat() if runner.last_sample_at else None}
        | {"action": actual_action(sample.grid_w, sample.battery_w)}
        if sample
        else None,
    }


def _flex_windows(slots: list[Any], name: str) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for slot in slots:
        power = slot.flex_w.get(name, 0.0)
        if power < 50:
            if windows and not windows[-1].get("closed"):
                windows[-1]["closed"] = True
            continue
        if windows and not windows[-1].get("closed"):
            windows[-1]["end"] = slot.end.isoformat()
            windows[-1]["energy_wh"] += power / 4
        else:
            windows.append({"start": slot.start.isoformat(), "end": slot.end.isoformat(), "energy_wh": power / 4})
    return [{k: v for k, v in w.items() if k != "closed"} for w in windows]


@router.get("/control")
async def get_control(request: Request) -> dict[str, Any]:
    """Everything the control tab needs: strategies, overrides, live values, next windows, priorities."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    now = datetime.now(UTC)
    runner: ShadowRunner = request.app.state.runner
    latest = runner.latest
    infos = {f.device_id: f for f in latest.flex} if latest else {}
    names = {BATTERY_PRIORITY_ID: "Hausspeicher"}
    devices = []
    for device in config.devices:
        profile = profiles[device.profile]
        if "loadpoint" not in profile["capabilities"] and "load" not in profile["capabilities"]:
            continue
        names[device.id] = device.name
        roles = {
            r.role: r
            for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
        }
        effective, paused = config.effective_flex(device, now)
        override = config.overrides.get(device.id)
        info = infos.get(device.id)
        devices.append(
            {
                "id": device.id,
                "name": device.name,
                "kind": device_kind(profile, device, snapshot) or "load",
                "default": device.flex.model_dump(),
                "override": override.model_dump(mode="json") if override and override.active(now) else None,
                "effective": effective.model_dump(),
                "paused": paused,
                "power_w": next((roles[r].value for r in ("charge_power", "power") if r in roles), None),
                "power_display": next(
                    (roles[r].display for r in ("charge_power", "power") if r in roles and roles[r].entity_id), None
                ),
                "level": roles["vehicle_soc"].value if "vehicle_soc" in roles else None,
                "target": info.target if info else None,
                "connected": roles["connected"].raw_state == "on" if "connected" in roles else None,
                "need_wh": info.need_wh if info else None,
                "reason": info.reason if info else None,
                "windows": _flex_windows(latest.plan.slots, device.name) if latest else [],
            }
        )
    has_battery = any("battery_window" in profiles[d.profile]["capabilities"] for d in config.devices)
    order = [i for i in config.priority_order() if i in names and (i != BATTERY_PRIORITY_ID or has_battery)]
    strategy_override = config.strategy_override
    return {
        "strategy": {
            "default": config.optimizer.strategy,
            "override": strategy_override.model_dump(mode="json")
            if strategy_override and strategy_override.active(now)
            else None,
            "effective": config.effective_strategy(now),
            "available": has_battery,
        },
        "devices": devices,
        "priorities": [
            {
                "id": i,
                "name": names[i],
                "conditions": [c.model_dump() for c in config.valid_conditions(i)],
                "hidden_conditions": [c.model_dump() for c in config.hidden_conditions(i)],
            }
            for i in order
        ],
        # Storages a consumer can wait for: home battery (%), vehicles (%) and hot water tanks (°C)
        "condition_sources": (
            [{"id": BATTERY_PRIORITY_ID, "name": names[BATTERY_PRIORITY_ID], "unit": "%", "kind": "battery"}]
            if has_battery
            else []
        )
        + [
            {"id": d["id"], "name": d["name"], "unit": "°C" if d["kind"] == "heater" else "%", "kind": d["kind"]}
            for d in devices
            if d["kind"] in ("vehicle", "heater")
        ],
        "plan_computed": runner.status.last_plan.isoformat() if runner.status.last_plan else None,
    }


def _replan(request: Request) -> None:
    # Every change that affects planning shows up in Plan and Steuerung right away
    if (runner := getattr(request.app.state, "runner", None)) is not None:
        runner.request_replan()


@router.put("/control/devices/{device_id}")
async def set_device_override(request: Request, device_id: str, body: DeviceOverride) -> dict[str, Any]:
    """Temporarily change how a consumer is planned (strategy, target, pause)."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if config.device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    config.overrides[device_id] = body
    store.save(config)
    _replan(request)
    return body.model_dump(mode="json")


@router.delete("/control/devices/{device_id}")
async def clear_device_override(request: Request, device_id: str) -> None:
    """Return a consumer to its default planning."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.overrides.pop(device_id, None)
    store.save(config)
    _replan(request)


@router.put("/control/strategy")
async def set_strategy_override(request: Request, body: StrategyOverride) -> dict[str, Any]:
    """Temporarily change the battery strategy."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.strategy_override = body
    store.save(config)
    _replan(request)
    return body.model_dump(mode="json")


@router.delete("/control/strategy")
async def clear_strategy_override(request: Request) -> None:
    """Return to the default battery strategy."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.strategy_override = None
    store.save(config)
    _replan(request)


@router.put("/control/priorities")
async def set_priorities(request: Request, order: list[str]) -> list[str]:
    """Store the priority order (device ids and "battery"), highest first."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    known = {d.id for d in config.devices} | {BATTERY_PRIORITY_ID}
    if unknown := [i for i in order if i not in known]:
        raise HTTPException(status_code=422, detail=f"Unknown ids: {unknown}")
    config.priorities = order
    store.save(config)
    _replan(request)
    return config.priority_order()


@router.put("/control/priorities/{device_id}/conditions")
async def set_priority_conditions(
    request: Request, device_id: str, conditions: list[PriorityCondition]
) -> list[dict[str, Any]]:
    """Store the levels other storages must reach before a consumer gets PV or battery energy."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if device_id != BATTERY_PRIORITY_ID and config.device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    # Only entries ranked higher can hold a consumer back
    above = config.ranked_above(device_id)
    if invalid := [c.device_id for c in conditions if c.device_id not in above]:
        raise HTTPException(status_code=422, detail=f"Conditions must refer to higher entries: {invalid}")
    # Hidden conditions (entry currently ranked lower) are kept for when it moves up again
    stored = conditions + config.hidden_conditions(device_id)
    if stored:
        config.priority_conditions[device_id] = stored
    else:
        config.priority_conditions.pop(device_id, None)
    store.save(config)
    _replan(request)
    return [c.model_dump() for c in conditions]


@router.get("/history")
async def get_history(request: Request, hours: Annotated[int, Query(ge=1, le=31 * 24)] = 24) -> dict[str, Any]:
    """Return measured SoC, PV, load and grid of the last hours from HA statistics."""
    hours = max(1, min(hours, 7 * 24))
    snapshot = await _snapshot(request)
    config = request.app.state.store.load()
    series = history_series(config, _library(request).profiles, snapshot)
    end = _slot_start()
    # 15 minute resolution up to two days, hourly above to keep charts readable
    bucket = timedelta(minutes=15) if hours <= 48 else timedelta(hours=1)
    start = end - timedelta(hours=hours)
    if bucket == timedelta(hours=1):
        start = start.replace(minute=0)
    try:
        points = await fetch_history(request.app.state.ha, series, start, end, bucket)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return {"start": start.isoformat(), "end": end.isoformat(), "points": points}


@router.get("/export")
async def export_data(request: Request) -> Response:
    """Download shadow mode data and configuration as a zip file."""
    runner: ShadowRunner = request.app.state.runner
    data_dir: Path = request.app.state.settings.data_dir
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "shadow.db"
        await asyncio.to_thread(runner.shadow.backup_to, copy)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(copy, "shadow.db")
            for name in ("config.json", "eeprom_writes.json"):
                if (data_dir / name).exists():
                    archive.write(data_dir / name, name)
            if (latest := runner.latest) is not None:
                archive.writestr(
                    "plan.json",
                    json.dumps(
                        {
                            "computed": runner.status.last_plan,
                            "warnings": latest.warnings,
                            "flex": [asdict(f) for f in latest.flex],
                            "plan": asdict(latest.plan),
                        },
                        default=str,
                        indent=1,
                    ),
                )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="sunflow-export-{stamp}.zip"'},
    )


@router.post("/import")
async def import_data(request: Request, file: UploadFile) -> dict[str, Any]:
    """Merge the shadow mode data of an export; the configuration is not imported."""
    runner: ShadowRunner = request.app.state.runner
    content = await file.read()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if "shadow.db" not in archive.namelist():
                    raise HTTPException(status_code=422, detail="Export enthält keine shadow.db")
                archive.extract("shadow.db", tmp)
        except zipfile.BadZipFile as err:
            raise HTTPException(status_code=422, detail="Keine gültige Export-Datei") from err
        try:
            count = await asyncio.to_thread(runner.shadow.merge_from, Path(tmp) / "shadow.db")
        except sqlite3.Error as err:
            raise HTTPException(status_code=422, detail=f"Export-Datei nicht lesbar: {err}") from err
    return {"imported_slots": count}


@router.get("/shadow")
async def get_shadow(request: Request, hours: Annotated[int, Query(ge=1, le=90 * 24)] = 48) -> dict[str, Any]:
    """Return recorded planned vs actual slots and their evaluation."""
    runner: ShadowRunner = request.app.state.runner
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = await asyncio.to_thread(runner.shadow.rows, since)
    return {
        "status": asdict(runner.status),
        "evaluation": evaluate_shadow(rows),
        "rows": rows,
    }


@router.get("/config/devices/{device_id}/efficiency")
async def get_efficiency(request: Request, device_id: str, days: int = 10) -> dict[str, Any] | None:
    """Estimate the battery efficiency from the measured history (suggestion only)."""
    result = await _efficiency_estimate(request, _configured_device(request, device_id), days)
    return asdict(result) if result else None


async def _efficiency_estimate(request: Request, device: DeviceConfig, days: int = 10) -> EfficiencyEstimate | None:
    """From the battery energy counters if there are some, else from the power history."""
    profile = _library(request).profiles[device.profile]
    snapshot = await _snapshot(request)
    roles = {
        r.role: r
        for r in preview(
            profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()}
        )
    }
    power, soc = roles.get("battery_power"), roles.get("battery_soc")
    params = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot)}
    if soc is None or not soc.entity_id or not params.get("capacity_wh"):
        return None
    charge, discharge = roles.get("battery_charge_energy"), roles.get("battery_discharge_energy")
    if charge and discharge and charge.entity_id and discharge.entity_id:
        # The inverter's counters cover more days and miss no peaks
        try:
            result = await estimate_from_counters(
                request.app.state.ha, charge.entity_id, discharge.entity_id, soc.entity_id, params["capacity_wh"]
            )
        except HAError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        if result:
            return result
    if power is None or not power.entity_id:
        return None
    # The sensor's sign convention applies to its statistics as well
    sign = -1.0 if power.sign is not None and power.sign not in CANONICAL_SIGNS else 1.0
    try:
        return await estimate_from_history(
            request.app.state.ha,
            power.entity_id,
            soc.entity_id,
            params["capacity_wh"],
            min(max(days, 1), 10),
            sign,
        )
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


ENERGY_PERIODS = {"today": 0, "7d": 7, "30d": 30}


@router.get("/energy")
async def get_energy(request: Request, period: str = "today") -> dict[str, Any]:
    """Energy balance from the energy counters: today (since local midnight) or the last days."""
    if period not in ENERGY_PERIODS:
        raise HTTPException(status_code=422, detail=f"period must be one of {list(ENERGY_PERIODS)}")
    config = request.app.state.store.load()
    snapshot = await _snapshot(request)
    sources = energy_sources(config, _library(request).profiles, snapshot)
    tz = await _local_tz(request)
    end = datetime.now(tz)
    midnight = end.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=ENERGY_PERIODS[period])
    try:
        return await energy_balance(request.app.state.ha, sources, start, end)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


@router.get("/efficiency/history")
async def get_efficiency_history(
    request: Request, days: Annotated[int, Query(ge=7, le=365)] = 60
) -> list[dict[str, Any]]:
    """Daily efficiency of batteries and inverters with energy counters, for the Analyse tab."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    return await efficiency_history(
        request.app.state.ha,
        battery_counters(config, profiles, snapshot),
        energy_sources(config, profiles, snapshot).inverters,
        days,
        await _local_tz(request),
    )


RECOMMENDATION_ESTIMATES_TTL = timedelta(hours=1)


@router.get("/recommendations")
async def get_recommendations(request: Request) -> list[dict[str, Any]]:
    """Settings the measured data or the plan suggest to change."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    runner: ShadowRunner = request.app.state.runner
    # The counter estimates query 90 days of statistics; an hour old is recent enough
    cache = getattr(request.app.state, "efficiency_cache", None)
    now = datetime.now(UTC)
    if cache is None or now - cache[0] > RECOMMENDATION_ESTIMATES_TTL:
        estimates = {}
        for device in config.devices:
            if "efficiency_pct" in device.parameters or "battery_window" in profiles[device.profile]["capabilities"]:
                try:
                    estimates[device.id] = await _efficiency_estimate(request, device)
                except HTTPException:
                    estimates[device.id] = None
        cache = (now, estimates)
        request.app.state.efficiency_cache = cache
    rows = await asyncio.to_thread(runner.shadow.rows, now - timedelta(days=14))
    days = len({row["start"][:10] for row in rows if row.get("actual_load_w") is not None})
    return build_recommendations(
        config,
        runner.latest,
        cache[1],
        rows,
        days,
        await runner.pv_correction(),
        await get_weather_entities(request),
    )


class Dismiss(BaseModel):
    """A recommendation to hide until its numbers change."""

    id: str
    fingerprint: str = ""


@router.post("/recommendations/dismiss")
async def dismiss_recommendation(request: Request, body: Dismiss) -> None:
    """Hide a recommendation."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    key = f"{body.id}#{body.fingerprint}"
    if key not in config.dismissed_recommendations:
        # Old entries of the same recommendation are no longer needed
        config.dismissed_recommendations = [
            k for k in config.dismissed_recommendations if not k.startswith(f"{body.id}#")
        ] + [key]
        store.save(config)


@router.get("/pv-learning")
async def get_pv_learning(request: Request) -> dict[str, Any]:
    """What was learned about the PV forecast of this site."""
    config = request.app.state.store.load()
    runner: ShadowRunner = request.app.state.runner
    correction = await runner.pv_correction()
    snapshot = await _snapshot(request)
    return correction.as_dict() | {
        "load_model": runner.load_model.as_dict() if runner.load_model else None,
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


@router.get("/costs/history")
async def get_cost_history(request: Request, days: Annotated[int, Query(ge=1, le=90)] = 30) -> list[dict[str, Any]]:
    """Daily electricity costs, feed-in revenue and the cost without PV and battery."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    battery_device = next(
        (d for d in config.devices if "battery_window" in profiles[d.profile]["capabilities"]), None
    )
    fixed = config.tariff.fixed_price_ct
    return await cost_history(
        request.app.state.ha,
        energy_sources(config, profiles, snapshot),
        config.tariff.import_entity_id,
        fixed / 100 if fixed is not None else None,
        feed_in_price(config, battery_device),
        days,
        await _local_tz(request),
    )


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
    site = site_status(config.devices, profiles, snapshot)
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
            params = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot)}
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
            consumers.append(
                {
                    "name": device.name,
                    "kind": kind,
                    "power_w": value("charge_power") if "loadpoint" in capabilities else value("power"),
                    "display": display("charge_power" if "loadpoint" in capabilities else "power"),
                    "level": value("vehicle_soc"),
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
WEATHER_HOURS = 8


async def _live_weather(request: Request, config: SunflowConfig, snapshot: Snapshot) -> dict[str, Any] | None:
    """Current weather and the next hours of the configured weather entity (forecast cached)."""
    entity_id = weather_entity(config, snapshot)
    state = snapshot.states.get(entity_id or "")
    if not entity_id or state is None:
        return None
    cache = getattr(request.app.state, "weather_cache", None)
    now = datetime.now(UTC)
    if cache is None or cache["entity"] != entity_id or now - cache["at"] > WEATHER_CACHE:
        try:
            response = await request.app.state.ha.call_service(
                "weather", "get_forecasts", {"type": "hourly"}, target={"entity_id": entity_id}, return_response=True
            )
            forecast = response["response"][entity_id]["forecast"]
        except (HAError, KeyError, TypeError):
            forecast = []
        cache = {"entity": entity_id, "at": now, "forecast": forecast}
        request.app.state.weather_cache = cache
    upcoming = [f for f in cache["forecast"] if "datetime" in f and datetime.fromisoformat(f["datetime"]) > now]
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
    }


MAX_STRINGS = 4


def pv_strings(roles: dict[str, Any]) -> list[dict[str, Any]]:
    """DC power per string: measured, or voltage times current; missing values count as 0 W."""
    strings = []
    for index in range(1, MAX_STRINGS + 1):
        # A role without entity means the device has no such string
        power, voltage, current = (
            role if (role := roles.get(f"pv{index}_{kind}")) is not None and role.entity_id else None
            for kind in ("power", "voltage", "current")
        )
        if power is None and (voltage is None or current is None):
            continue
        if power is not None:
            value = power.value
        else:
            value = voltage.value * current.value if voltage.value is not None and current.value is not None else None
        strings.append(
            {
                "name": f"String {index}",
                "power_w": max(value or 0.0, 0.0),
                "display": power.display if power is not None else {"unit": "W", "precision": 0},
            }
        )
    return strings


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
    site = site_status(config.devices, _library(request).profiles, snapshot)
    result = asdict(site)
    for device in result["devices"]:
        for role in device["roles"]:
            role["missing"] = role["entity_id"] is None
    return result


@router.delete("/config/devices/{device_id}")
async def remove_device(request: Request, device_id: str) -> None:
    """Remove a device from the configuration."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if config.device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    config.devices = [d for d in config.devices if d.id != device_id]
    # Nothing may keep referring to the removed device
    config.overrides.pop(device_id, None)
    config.priorities = [i for i in config.priorities if i != device_id]
    config.priority_conditions.pop(device_id, None)
    config.priority_conditions = {
        owner: kept
        for owner, conditions in config.priority_conditions.items()
        if (kept := [c for c in conditions if c.device_id != device_id])
    }
    config.measurement_sources = {q: d for q, d in config.measurement_sources.items() if d != device_id}
    store.save(config)
    _replan(request)


@router.post("/config/devices/{device_id}/actions/{action}")
async def run_action(
    request: Request,
    device_id: str,
    action: str,
    params: dict[str, float] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Plan a profile action and show the service calls (never executed).

    Runtime parameters such as P (power in W) are passed in the body. Writing to a
    device happens only in the device test, which requires an explicit confirmation.
    """
    device = _configured_device(request, device_id)
    profile = _library(request).profiles[device.profile]
    snapshot = await _snapshot(request)
    try:
        resolved, calls = plan_action(
            profile,
            action,
            device.parameters,
            params,
            device_entities(snapshot, device.ha_device_id),
        )
    except ExecutorError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return asdict(ActionResult(action=action, params=resolved, dry_run=True, calls=calls))


@router.post("/config/devices/{device_id}/setup")
async def run_setup(request: Request, device_id: str) -> dict[str, Any]:
    """Plan the profile's setup steps and show the service calls (never executed)."""
    device = _configured_device(request, device_id)
    profile = _library(request).profiles[device.profile]
    snapshot = await _snapshot(request)
    try:
        calls = plan_setup(profile, device_entities(snapshot, device.ha_device_id))
    except ExecutorError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return asdict(ActionResult(action="setup", params={}, dry_run=True, calls=calls))


async def _keep_connected(client: HAClient) -> None:
    """Reconnect to Home Assistant with increasing delays."""
    attempt = 0
    while True:
        if not client.connected:
            try:
                await client.connect()
                attempt = 0
            except Exception as err:  # noqa: BLE001 - the keeper must never die
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                _LOGGER.warning("%s, retrying in %ss", err, delay)
                attempt += 1
                await asyncio.sleep(delay)
                continue
        await asyncio.sleep(1)


# The Supervisor's Ingress proxy, and local calls inside the container
INGRESS_CLIENTS = {"172.30.32.2", "127.0.0.1"}


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings is None:
            yield
            return
        app.state.settings = settings
        app.state.store = ConfigStore(settings.data_dir)
        app.state.profiles = load_profiles(
            settings.profiles_dir, load_schema(settings.schema_dir)
        )
        app.state.ha = HAClient(settings.ha_websocket_url, settings.ha_token)
        app.state.snapshots = SnapshotCache(app.state.ha)
        app.state.device_tests = {}
        app.state.restore_marker = RestoreMarker(settings.data_dir)
        keeper = asyncio.create_task(_keep_connected(app.state.ha))
        shadow = ShadowStore(settings.data_dir)
        app.state.runner = ShadowRunner(app.state.ha, app.state.store, app.state.profiles, shadow, app.state.snapshots)
        runner_task = asyncio.create_task(app.state.runner.run())
        try:
            yield
        finally:
            runner_task.cancel()
            with suppress(asyncio.CancelledError):
                await runner_task
            shadow.close()
            # A running device test restores self-consumption when cancelled
            for _, task in app.state.device_tests.values():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            keeper.cancel()
            with suppress(asyncio.CancelledError):
                await keeper
            await app.state.ha.close()

    app = FastAPI(title="Sunflow", version=__version__, lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(HAError)
    async def ha_error(request: Request, err: HAError) -> JSONResponse:
        return JSONResponse({"detail": f"Home Assistant: {err}"}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, err: RequestValidationError) -> JSONResponse:
        # Leave out the rejected input: NaN or Infinity cannot be serialized as JSON
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in err.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, err: ConfigError) -> JSONResponse:
        return JSONResponse({"detail": str(err)}, status_code=503)

    if settings is not None and settings.ingress_only:

        @app.middleware("http")
        async def ingress_only(request: Request, call_next: Any) -> Any:
            # Port 8099 is reachable by every container on the hassio network; without
            # this, any of them could change the configuration or start the device test
            client = request.client.host if request.client else None
            if client not in INGRESS_CLIENTS and request.url.path != "/api/v1/health":
                return JSONResponse({"detail": "Only available through Home Assistant"}, status_code=403)
            return await call_next(request)

    if settings is not None and settings.frontend_dir.is_dir():
        add_frontend(app, settings.frontend_dir)
    return app


def frontend_version(frontend_dir: Path) -> str:
    """Return a hash over all frontend files; changes with every edit."""
    digest = hashlib.sha256()
    for path in sorted(frontend_dir.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(frontend_dir)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def add_frontend(app: FastAPI, frontend_dir: Path) -> None:
    """Serve the frontend below a versioned path.

    There is no build step producing hashed file names, so all assets are
    served under /assets/<version>/. Relative module imports stay inside
    that prefix, so a browser can never mix cached files of an old version.
    """
    root = frontend_dir.resolve()

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        html = (root / "index.html").read_text()
        html = html.replace("__ASSETS__", f"assets/{frontend_version(root)}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/assets/{version}/{path:path}", include_in_schema=False)
    async def asset(version: str, path: str) -> FileResponse:
        file = (root / path).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(
            file, headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )
