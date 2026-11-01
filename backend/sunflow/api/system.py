"""Health, profiles, discovery and data export/import."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Annotated, Any
from urllib.parse import urlsplit
import zipfile

from fastapi import APIRouter, HTTPException, Query, Request, Response, UploadFile

from sunflow import API_VERSION, __version__
from sunflow.config.models import McpSettings
from sunflow.control.journal import JOURNAL_FILE
from sunflow.devices.discovery import discover
from sunflow.ha import HAClient, HAError
from sunflow.integration_link import app_host, host_port
from sunflow.mcp_key import McpKey
from sunflow.shadow.runner import ShadowRunner
from sunflow.shadow.store import evaluate as evaluate_shadow

from .common import _library, _snapshot

router = APIRouter()


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


@router.get("/integration/link")
async def integration_link(request: Request) -> dict[str, Any]:
    """URL and token for setting up the Sunflow integration by hand (only through Ingress)."""
    settings = request.app.state.settings
    return {
        "url": f"http://{app_host()}:{settings.port}",
        "token": request.app.state.integration_token,
        "discovery": settings.supervisor_token is not None,
    }


@router.get("/mcp/link")
async def mcp_link(request: Request) -> dict[str, Any]:
    """Port of the MCP server and whether it has a key (only through Ingress); without a mapped port it is not reachable."""
    settings = request.app.state.settings
    port = await host_port(settings.supervisor_token, settings.port) if settings.supervisor_token else settings.port
    return {
        "host": await internal_host(request),
        "port": port,
        "path": "/mcp",
        "key_created": request.app.state.mcp_key.created,
        "areas": request.app.state.store.load().mcp.areas,
    }


@router.put("/config/mcp")
async def set_mcp_settings(request: Request, body: McpSettings) -> dict[str, Any]:
    """Choose what the MCP server may read; applies to the next tool call."""
    store = request.app.state.store
    config = store.load()
    config.mcp = body
    store.save(config)
    return body.model_dump()


async def internal_host(request: Request) -> str | None:
    """Host of Home Assistant in the home network; the browser may know it only by its external address."""
    try:
        urls = await request.app.state.ha.command("network/url")
    except HAError:
        return None
    return urlsplit(urls["internal"]).hostname if urls.get("internal") else None


@router.post("/mcp/key")
async def create_mcp_key(request: Request) -> dict[str, Any]:
    """A new key for the MCP server; the old one stops working. It is shown this once only."""
    key: McpKey = request.app.state.mcp_key
    return {"key": key.create(), "key_created": key.created}


@router.delete("/mcp/key")
async def revoke_mcp_key(request: Request) -> dict[str, Any]:
    """Close the MCP server until a new key is created."""
    request.app.state.mcp_key.revoke()
    return {"key_created": None}


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
            for name in ("config.json", "eeprom_writes.json", JOURNAL_FILE):
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
