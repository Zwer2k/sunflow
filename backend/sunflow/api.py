"""HTTP API of the backend."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
import logging
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException, Request
from pydantic import BaseModel

from . import API_VERSION, __version__
from .config_store import ConfigStore, DeviceConfig
from .discovery import Snapshot, discover, fetch_snapshot
from .executor import (
    ActionResult,
    EepromGuard,
    ExecutorError,
    PlannedCall,
    execute,
    plan_action,
    plan_setup,
)
from .ha import HAClient, HAError
from .profiles import ProfileLibrary, load_profiles, load_schema
from .resolver import check_balance, device_entities, preview
from .settings import Settings

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAYS = (1, 2, 5, 10, 30)
DEFAULT_MAX_EEPROM_WRITES = 10

router = APIRouter(prefix=f"/api/{API_VERSION}")


class AddDevice(BaseModel):
    """Request body to add a discovered device."""

    ha_device_id: str
    profile: str | None = None


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


@router.get("/devices/{device_id}/preview")
async def device_preview(
    request: Request, device_id: str, profile: str | None = None
) -> dict[str, Any]:
    """Return the live, normalized role values of a Home Assistant device.

    Without a profile parameter the profile found by discovery is used.
    """
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

    roles = preview(library.profiles[profile], snapshot, device_id)
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
    return request.app.state.store.load().model_dump()


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
        name=discovered.name,
    )
    config.devices.append(device)
    store.save(config)
    return device.model_dump()


@router.patch("/config/devices/{device_id}/parameters")
async def update_parameters(
    request: Request, device_id: str, parameters: dict[str, float] = Body(...)
) -> dict[str, Any]:
    """Merge device parameters such as max_charge or grid_offset."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if (device := config.device(device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    device.parameters.update(parameters)
    store.save(config)
    return device.model_dump()


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
        keeper = asyncio.create_task(_keep_connected(app.state.ha))
        try:
            yield
        finally:
            keeper.cancel()
            with suppress(asyncio.CancelledError):
                await keeper
            await app.state.ha.close()

    app = FastAPI(title="Sunflow", version=__version__, lifespan=lifespan)
    app.include_router(router)
    return app
