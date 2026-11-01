"""Optimizers of the PV strings: values for the roof view, assignment and layout in the setup."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from sunflow.config.models import OptimizerSettings
from sunflow.devices.optimizers import (
    SOURCES,
    VALUE_UNITS,
    discover,
    optimizers,
    tigo_plant,
    used_entries,
)

from .common import _library, _snapshot

router = APIRouter()


@router.get("/optimizers")
async def get_optimizers(request: Request) -> dict[str, Any]:
    """Every optimizer with string, roof position and current values, and the strings to choose from."""
    config = request.app.state.store.load()
    snapshot = await _snapshot(request)
    found, strings = optimizers(config, _library(request).profiles, snapshot)
    installed = {e["domain"] for e in snapshot.entries}
    return {
        "optimizers": [asdict(o) for o in found],
        "strings": strings,
        "sources": [
            {"key": s.key, "name": s.name, "domain": s.domain, "installed": s.domain in installed,
             "count": sum(o.source == s.key for o in found)}
            for s in SOURCES
        ],
        "units": VALUE_UNITS,
        "tigo_plant": tigo_plant(config, strings, found),
        "tigo_plant_chosen": config.optimizer_settings.tigo_plant,
        "layouts": {k: v.model_dump() for k, v in config.optimizer_settings.layouts.items()},
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
    config = store.load()
    entries = used_entries(config, discover(await _snapshot(request)))
    entries = entries | {entry_id} if body.used else entries - {entry_id}
    config.optimizer_settings.entries = sorted(entries)
    store.save(config)
    return {"entries": config.optimizer_settings.entries}
