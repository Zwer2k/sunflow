"""Setup summary, measurement sources and planning settings."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from sunflow.analysis.summary import SetupSummary, build_summary
from sunflow.config.models import OptimizerConfig, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.site import (
    MEASUREMENTS,
    measurement_candidates,
    measurement_device,
)

from .common import _library, _replan

router = APIRouter()


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
