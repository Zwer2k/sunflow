"""Optimizers of the PV strings: values for the roof view, assignment and layout in the setup."""

from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from sunflow.analysis.day_series import day_window
from sunflow.analysis.optimizer_history import optimizer_day
from sunflow.config.models import OptimizerSettings
from sunflow.devices.optimizers import (
    VALUE_UNITS,
    discover,
    optimizers,
    sources,
    standalone_plant,
    used_entries,
)
from sunflow.ha import HAError
from sunflow.i18n import t

from .common import _library, _local_tz, _snapshot

router = APIRouter()


@router.get("/optimizers")
async def get_optimizers(request: Request) -> dict[str, Any]:
    """Every optimizer with string, roof position and current values, and the strings to choose from."""
    config = request.app.state.store.load()
    snapshot = await _snapshot(request)
    library = _library(request)
    found, strings = optimizers(config, library, snapshot)
    units = sources(library)
    installed = {e["domain"] for e in snapshot.entries}
    return {
        "optimizers": [asdict(o) for o in found],
        "strings": strings,
        "sources": [
            {
                "key": key, "name": u["name"], "domain": u["match"]["integration"], "url": u.get("url"),
                "sub_device": bool(u["match"].get("sub_device")), "label_position": "label_position" in u,
                "installed": u["match"]["integration"] in installed, "count": sum(o.source == key for o in found),
            }
            for key, u in units.items()
        ],
        "units": VALUE_UNITS,
        "standalone_plant": standalone_plant(config, strings, found, units),
        "standalone_plant_chosen": config.optimizer_settings.tigo_plant,
        "layouts": {k: v.model_dump() for k, v in config.optimizer_settings.layouts.items()},
    }


@router.get("/optimizers/history")
async def get_optimizer_history(
    request: Request, string: Annotated[str, Query(min_length=1)], day: date | None = None
) -> dict[str, Any]:
    """Power of the optimizers of one string over a local day (default today), up to now."""
    tz = await _local_tz(request)
    now = datetime.now(UTC)
    day = day or now.astimezone(tz).date()
    if (window := day_window(day, tz, now)) is None:
        raise HTTPException(status_code=422, detail=t("Dieser Tag liegt in der Zukunft"))
    start, end, step = window
    config = request.app.state.store.load()
    found, _ = optimizers(config, _library(request), await _snapshot(request))
    try:
        days = await optimizer_day(request.app.state.ha, [o for o in found if o.string == string], start, end, step)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return {
        "day": day.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "step_minutes": int(step.total_seconds() // 60),
        "optimizers": days,
    }


@router.put("/config/optimizers")
async def set_optimizers(request: Request, body: OptimizerSettings) -> dict[str, Any]:
    """Store assignment and layout; placements without anything set are left out."""
    store = request.app.state.store
    config = store.load()
    body.placements = [
        p for p in body.placements if p.string is not None or p.row is not None or p.col is not None or p.name or p.rotated
    ]
    # Which integrations are taken over is chosen under "Geräte"
    body.entries = config.optimizer_settings.entries
    config.optimizer_settings = body
    store.save(config)
    return body.model_dump()


class OptimizerEntry(BaseModel):
    """Whether Sunflow uses the optimizers of one integration."""

    used: bool


@router.put("/config/optimizers/entries/{entry_id}")
async def use_optimizer_entry(request: Request, entry_id: str, body: OptimizerEntry) -> dict[str, Any]:
    """Take over the optimizers of an integration, or drop them (their placements stay)."""
    store = request.app.state.store
    # The snapshot first: a configuration loaded before waiting for it would overwrite what was saved meanwhile
    found = discover(await _snapshot(request), _library(request))
    config = store.load()
    entries = used_entries(config, found)
    entries = entries | {entry_id} if body.used else entries - {entry_id}
    config.optimizer_settings.entries = sorted(entries)
    store.save(config)
    return {"entries": config.optimizer_settings.entries}
