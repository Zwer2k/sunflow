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
import zipfile

from fastapi import APIRouter, HTTPException, Query, Request, Response, UploadFile

from sunflow import API_VERSION, __version__
from sunflow.devices.discovery import discover
from sunflow.ha import HAClient
from sunflow.integration_link import app_host
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
