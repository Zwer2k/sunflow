"""HTTP API of the backend."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import API_VERSION, __version__
from .config_store import (
    ConfigStore,
    DeviceConfig,
    FeedIn,
    ForecastPlane,
    MeasuredBy,
    OptimizerConfig,
    RoleOverride,
    TariffConfig,
)
from .device_test import DeviceTestState, ModeControl, run_device_test, state_response
from .discovery import Snapshot, discover, fetch_snapshot
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
from .profiles import ProfileLibrary, load_profiles, load_schema
from .resolver import check_balance, device_entities, preview
from .settings import Settings
from .runner import ShadowRunner, current_plan
from .shadow import ShadowStore, evaluate as evaluate_shadow
from .site import device_parameters, site_status
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


async def _snapshot(request: Request) -> Snapshot:
    try:
        return await fetch_snapshot(request.app.state.ha)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


def _library(request: Request) -> ProfileLibrary:
    return request.app.state.profiles


def _configured_device(request: Request, device_id: str) -> DeviceConfig:
    config = request.app.state.store.load()
    if (device := config.device(device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    return device


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
    snapshot = await _snapshot(request)
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
    config = store.load()
    if any(d.ha_device_id == body.ha_device_id for d in config.devices):
        raise HTTPException(status_code=409, detail="Device already configured")

    snapshot = await _snapshot(request)
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
        # Device names like "Inverter" are ambiguous; manufacturer + model is not
        name=" ".join(filter(None, (discovered.manufacturer, discovered.model)))
        or discovered.name,
        role_overrides=body.role_overrides,
    )
    config.devices.append(device)
    store.save(config)
    return device.model_dump()


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
    parameters: dict[str, float | None] | None = None
    feed_in: FeedIn | None = None
    measured_by: MeasuredBy | None = None
    clear_measured_by: bool = False


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
    if body.clear_measured_by:
        device.measured_by = None
    elif body.measured_by is not None:
        if config.device(body.measured_by.device_id) is None:
            raise HTTPException(status_code=422, detail="Measuring device not configured")
        device.measured_by = body.measured_by
    store.save(config)
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
    assigned = {(p.source, p.ref): p.device_id for p in config.forecast_planes}
    planes = [
        asdict(site) | {"device_id": assigned.get((site.source, site.ref))}
        for site in [
            *solcast_sites(states, solcast_entities),
            *forecast_solar_planes(snapshot.entries, snapshot.devices),
        ]
    ]
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
    return [p.model_dump() for p in planes]


@router.get("/tariff/series")
async def get_tariff_series(
    request: Request, entity_id: str | None = None, hours: int = FORECAST_HOURS
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
    request: Request, hours: int = FORECAST_HOURS
) -> dict[str, Any]:
    """Return the PV forecast per plant, summed over its assigned planes."""
    config = request.app.state.store.load()
    client: HAClient = request.app.state.ha
    start = _slot_start().replace(minute=0)
    by_plant: dict[str, list[list[ForecastPoint]]] = {}
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
            by_plant.setdefault(plane.device_id, []).append(points)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    names = {d.id: d.name for d in config.devices}
    return {
        "plants": [
            {
                "device_id": device_id,
                "name": names.get(device_id, device_id),
                "points": [asdict(p) for p in sum_forecasts(series)],
            }
            for device_id, series in by_plant.items()
        ]
    }


def _tests(request: Request) -> dict[str, tuple[DeviceTestState, asyncio.Task[None]]]:
    return request.app.state.device_tests


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

    client: HAClient = request.app.state.ha
    overrides = {r: o.model_dump() for r, o in device.role_overrides.items()}
    guard = _guard(request, profile)

    async def read() -> tuple[float | None, float | None]:
        snapshot = await fetch_snapshot(client)
        roles = {r.role: r.value for r in preview(profile, snapshot, device.ha_device_id, overrides)}
        return roles.get("battery_power"), roles.get("battery_soc")

    async def apply_action(action: str, runtime: dict[str, float]) -> list[PlannedCall]:
        snapshot = await fetch_snapshot(client)
        _, calls = plan_action(
            profile, action, device.parameters, runtime, device_entities(snapshot, device.ha_device_id)
        )
        await execute(client, calls, snapshot.states, guard, dry_run=False)
        return calls

    mode = None
    if control_mode := profile.get("control_mode"):

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
            state.calls.append(call)
            if call.status != "done":
                raise ExecutorError(f"Mode switch {call.status}: {call.reason}")
            return current

        async def ensure_mode() -> str | None:
            return await set_mode(control_mode["option"])

        async def restore_mode(option: str) -> None:
            await set_mode(option)

        mode = ModeControl(ensure=ensure_mode, set=restore_mode)

    state = DeviceTestState(device_id=device_id)
    store: ConfigStore = request.app.state.store

    async def run() -> None:
        await run_device_test(state, read, apply_action, mode=mode)
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
    return body.model_dump()


@router.get("/plan")
async def get_plan(request: Request) -> dict[str, Any]:
    """Optimize the battery schedule for the next hours (read only, nothing is controlled)."""
    try:
        result = await current_plan(request.app.state.ha, request.app.state.store, _library(request))
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    config = request.app.state.store.load()
    plan = result.plan
    return {
        "status": plan.status,
        "warnings": result.warnings,
        "price_source": result.price_source,
        "pv_planes": result.pv_planes,
        "load_quality": result.load.quality if result.load else None,
        "load_history_hours": result.load.history_hours if result.load else 0,
        "settings": config.optimizer.model_dump(),
        "cost": plan.cost,
        "baseline_cost": plan.baseline_cost,
        "terminal_value": plan.terminal_value,
        "baseline_terminal_value": plan.baseline_terminal_value,
        "savings": plan.savings if plan.slots else None,
        "slots": [asdict(slot) for slot in plan.slots],
    }


@router.get("/shadow")
async def get_shadow(request: Request, hours: int = 48) -> dict[str, Any]:
    """Return recorded planned vs actual slots and their evaluation."""
    runner: ShadowRunner = request.app.state.runner
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = await asyncio.to_thread(runner.shadow.rows, since)
    return {
        "status": asdict(runner.status),
        "evaluation": evaluate_shadow(rows),
        "rows": rows,
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
    store.save(config)


async def _run(
    request: Request, calls: list[PlannedCall], profile: dict[str, Any], dry_run: bool
) -> None:
    snapshot = await _snapshot(request)
    try:
        await execute(
            request.app.state.ha, calls, snapshot.states, _guard(request, profile), dry_run
        )
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


@router.post("/config/devices/{device_id}/actions/{action}")
async def run_action(
    request: Request,
    device_id: str,
    action: str,
    dry_run: bool = True,
    params: dict[str, float] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Plan and (with dry_run=false) execute a profile action.

    Runtime parameters such as P (power in W) are passed in the body.
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
    await _run(request, calls, profile, dry_run)
    return asdict(
        ActionResult(action=action, params=resolved, dry_run=dry_run, calls=calls)
    )


@router.post("/config/devices/{device_id}/setup")
async def run_setup(
    request: Request, device_id: str, dry_run: bool = True
) -> dict[str, Any]:
    """Plan and (with dry_run=false) execute the profile's setup steps."""
    device = _configured_device(request, device_id)
    profile = _library(request).profiles[device.profile]
    snapshot = await _snapshot(request)
    try:
        calls = plan_setup(profile, device_entities(snapshot, device.ha_device_id))
    except ExecutorError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    await _run(request, calls, profile, dry_run)
    return asdict(ActionResult(action="setup", params={}, dry_run=dry_run, calls=calls))


async def _keep_connected(client: HAClient) -> None:
    """Reconnect to Home Assistant with increasing delays."""
    attempt = 0
    while True:
        if not client.connected:
            try:
                await client.connect()
                attempt = 0
            except HAError as err:
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                _LOGGER.warning("%s, retrying in %ss", err, delay)
                attempt += 1
                await asyncio.sleep(delay)
                continue
        await asyncio.sleep(1)


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
        app.state.device_tests = {}
        keeper = asyncio.create_task(_keep_connected(app.state.ha))
        shadow = ShadowStore(settings.data_dir)
        app.state.runner = ShadowRunner(app.state.ha, app.state.store, app.state.profiles, shadow)
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
