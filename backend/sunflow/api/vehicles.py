"""Vehicles, their charging profiles at wallboxes and how wallboxes recognize them."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from sunflow.config.models import ChargingProfile, Vehicle, VehicleDetection
from sunflow.config.store import ConfigStore
from sunflow.devices.site import device_kind
from sunflow.planning.vehicles import soc_of

from .common import _library, _replan, _snapshot

router = APIRouter()


@router.get("/config/vehicles")
async def get_vehicles(request: Request) -> dict[str, Any]:
    """Vehicles, the wallboxes, which vehicle may charge where (charging profiles) and the recognition."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    wallboxes = [
        {
            "id": d.id,
            "name": d.name,
            "detection": config.detection_of(d.id).model_dump(),
            "cards": "transaction" in profiles[d.profile]["roles"],
        }
        for d in config.devices
        if "loadpoint" in profiles[d.profile]["capabilities"] and device_kind(profiles[d.profile], d, snapshot) != "heater"
    ]
    return {
        "vehicles": [v.model_dump() for v in config.vehicles],
        "wallboxes": wallboxes,
        "charging": [c.model_dump() for c in config.charging],
        "live": {v.id: vehicle_live(v, snapshot.states) for v in config.vehicles},
    }


# States of a presence or plug sensor that mean "plugged in / at home"
PRESENT_STATES = {"on", "home", "true", "plugged", "connected"}
ABSENT_STATES = {"off", "not_home", "away", "false", "unplugged", "disconnected"}


def vehicle_live(vehicle: Vehicle, states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Current values of a vehicle's own sensors: SoC, energy in the battery, plugged in, as of when."""
    soc = soc_of(vehicle, states)
    soc_state = states.get(vehicle.soc_entity or "")
    plugged_state = states.get(vehicle.plugged_entity or "", {}).get("state")
    plugged = (
        True if plugged_state in PRESENT_STATES else False if plugged_state in ABSENT_STATES else None
    )
    return {
        "soc_pct": soc,
        "energy_wh": soc / 100 * vehicle.capacity_wh if soc is not None else None,
        "soc_updated": soc_state.get("last_updated") if soc_state else None,
        "soc_unavailable": vehicle.soc_entity is not None and soc is None,
        "plugged": plugged,
        "plugged_state": plugged_state,
    }


@router.put("/config/vehicles")
async def set_vehicles(request: Request, vehicles: list[Vehicle]) -> dict[str, Any]:
    """Store all vehicles; charging profiles and choices of removed ones go with them."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.vehicles = vehicles
    ids = {v.id for v in vehicles}
    config.charging = [c for c in config.charging if c.vehicle_id in ids]
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
    config.vehicle_detection[device.id] = body
    store.save(config)
    _replan(request)
    return body.model_dump()


@router.put("/config/charging")
async def set_charging(request: Request, profiles: list[ChargingProfile]) -> dict[str, Any]:
    """Which vehicle may charge at which wallbox, with its card there, target and ready-by time."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    wallboxes, vehicles = {d.id for d in config.devices}, {v.id for v in config.vehicles}
    if any(p.wallbox_id not in wallboxes or p.vehicle_id not in vehicles for p in profiles):
        raise HTTPException(status_code=422, detail="Unknown wallbox or vehicle")
    pairs = [(p.wallbox_id, p.vehicle_id) for p in profiles]
    cards = [(p.wallbox_id, p.card) for p in profiles if p.card]
    if len(pairs) != len(set(pairs)) or len(cards) != len(set(cards)):
        raise HTTPException(status_code=422, detail="Eine Karte ist an einer Wallbox mehreren Fahrzeugen zugeordnet")
    config.charging = profiles
    store.save(config)
    _replan(request)
    return await get_vehicles(request)


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
