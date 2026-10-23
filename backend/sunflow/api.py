"""HTTP API of the backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request

from . import API_VERSION, __version__
from .discovery import discover, fetch_snapshot
from .ha import HAClient, HAError
from .profiles import load_profiles, load_schema
from .settings import Settings

router = APIRouter(prefix=f"/api/{API_VERSION}")


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
    library = request.app.state.profiles
    return {
        "profiles": sorted(library.profiles),
        "errors": [{"path": str(e.path), "message": e.message} for e in library.errors],
    }


@router.get("/discovery")
async def discovery(request: Request) -> list[dict[str, Any]]:
    """Return integrations and devices found in Home Assistant."""
    client: HAClient = request.app.state.ha
    try:
        snapshot = await fetch_snapshot(client)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return [asdict(i) for i in discover(snapshot, request.app.state.profiles)]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings is None:
            yield
            return
        app.state.settings = settings
        app.state.profiles = load_profiles(
            settings.profiles_dir, load_schema(settings.schema_dir)
        )
        app.state.ha = HAClient(settings.ha_websocket_url, settings.ha_token)
        await app.state.ha.connect()
        try:
            yield
        finally:
            await app.state.ha.close()

    app = FastAPI(title="Sunflow", version=__version__, lifespan=lifespan)
    app.include_router(router)
    return app
