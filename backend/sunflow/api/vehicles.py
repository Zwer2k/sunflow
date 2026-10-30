"""Vehicles at wallboxes and how they are recognized."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from sunflow.config.models import Vehicle, VehicleDetection
from sunflow.config.store import ConfigStore
from sunflow.devices.site import device_kind

from .common import _library, _replan, _snapshot

router = APIRouter()


@router.get("/config/vehicles")
async def get_vehicles(request: Request) -> dict[str, Any]:
    """Vehicles, the wallboxes they may charge at and how each wallbox recognizes them."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    wallboxes = [
        {"id": d.id, "name": d.name, "detection": d.vehicle_detection.model_dump(), "cards": "transaction" in profiles[d.profile]["roles"]}
        for d in config.devices
        if "loadpoint" in profiles[d.profile]["capabilities"] and device_kind(profiles[d.profile], d, snapshot) != "heater"
    ]
    return {"vehicles": [v.model_dump() for v in config.vehicles], "wallboxes": wallboxes}


@router.put("/config/vehicles")
async def set_vehicles(request: Request, vehicles: list[Vehicle]) -> dict[str, Any]:
    """Store all vehicles."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    known = {d.id for d in config.devices}
    if any(w not in known for v in vehicles for w in v.wallboxes):
        raise HTTPException(status_code=422, detail="Unknown wallbox")
    cards = [(w, v.card) for v in vehicles if v.card for w in v.wallboxes]
    if len(cards) != len(set(cards)):
        raise HTTPException(status_code=422, detail="Eine Karte ist an einer Wallbox mehreren Fahrzeugen zugeordnet")
    config.vehicles = vehicles
    ids = {v.id for v in vehicles}
    config.selected_vehicles = {w: v for w, v in config.selected_vehicles.items() if v in ids}
    store.save(config)
    _replan(request)
    return await get_vehicles(request)


@router.put("/config/devices/{device_id}/vehicle-detection")
async def set_vehicle_detection(request: Request, device_id: str, body: VehicleDetection) -> dict[str, Any]:
    """How a wallbox recognizes the connected vehicle."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = config.device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Unknown device")
    device.vehicle_detection = body
    store.save(config)
    _replan(request)
    return body.model_dump()


class VehicleChoice(BaseModel):
    """The vehicle at a wallbox, chosen in the control tab; None: recognize it."""

    vehicle_id: str | None = None


@router.put("/control/vehicles/{wallbox_id}")
async def choose_vehicle(request: Request, wallbox_id: str, body: VehicleChoice) -> dict[str, Any]:
    """Which vehicle is plugged in; counts until it is unplugged."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if config.device(wallbox_id) is None:
        raise HTTPException(status_code=404, detail="Unknown wallbox")
    if body.vehicle_id is None:
        config.selected_vehicles.pop(wallbox_id, None)
    elif body.vehicle_id not in {v.id for v in config.vehicles_at(wallbox_id)}:
        raise HTTPException(status_code=422, detail="Vehicle not assigned to this wallbox")
    else:
        config.selected_vehicles[wallbox_id] = body.vehicle_id
    store.save(config)
    _replan(request)
    return {"wallbox_id": wallbox_id, "vehicle_id": body.vehicle_id}
