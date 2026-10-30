"""The FastAPI app: lifespan (HA connection, background loop, control) and the frontend."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
import hashlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from sunflow import __version__
from sunflow.config.models import DeviceConfig
from sunflow.config.store import ConfigError, ConfigStore
from sunflow.control.controller import (
    DeviceAccess,
    PlanController,
    TakeoverMarker,
    device_access,
)
from sunflow.devices.device_test import RestoreMarker
from sunflow.devices.discovery import SnapshotCache
from sunflow.devices.executor import EepromGuard
from sunflow.devices.profiles import load_profiles, load_schema
from sunflow.ha import HAClient, HAError
from sunflow.settings import Settings
from sunflow.shadow.runner import ShadowRunner
from sunflow.shadow.store import ShadowStore

from .common import _LOGGER, DEFAULT_MAX_EEPROM_WRITES, router

RECONNECT_DELAYS = (1, 2, 5, 10, 30)


def _plan_controller(app: FastAPI, settings: Settings) -> PlanController:
    def access_for(device: DeviceConfig) -> DeviceAccess:
        profile = app.state.profiles.profiles[device.profile]
        limit = profile.get("eeprom_guard", {}).get("max_writes_per_day", DEFAULT_MAX_EEPROM_WRITES)
        return device_access(app.state.ha, profile, device, EepromGuard(settings.data_dir, limit))

    return PlanController(TakeoverMarker(settings.data_dir), access_for, lambda: app.state.profiles.profiles)


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
        app.state.controller = app.state.runner.controller = _plan_controller(app, settings)
        runner_task = asyncio.create_task(app.state.runner.run())
        try:
            yield
        finally:
            runner_task.cancel()
            with suppress(asyncio.CancelledError):
                await runner_task
            # Without Sunflow running no plan is followed: leave the batteries in self-consumption
            with suppress(Exception):
                await asyncio.wait_for(app.state.controller.release_all(app.state.store.load()), 15)
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
