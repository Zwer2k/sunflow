"""Devices Sunflow talks to directly (Modbus TCP, HTTP): profiles, adding, address, status."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from sunflow.config.models import Connection, DeviceConfig
from sunflow.config.store import ConfigStore
from sunflow.direct.hub import DirectHub, direct_device_id

from .common import _library, _replan
from .devices import device_with_relations

router = APIRouter()


class DirectDevice(BaseModel):
    """A device to add: its profile and where to reach it."""

    profile: str
    name: str = ""
    host: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    unit_id: int | None = Field(default=None, ge=0, le=255)


@router.get("/direct/profiles")
async def direct_profiles(request: Request) -> list[dict[str, Any]]:
    """Profiles of devices without an integration, with their default address parts."""
    return [
        {
            "profile": p["profile"],
            "manufacturer": p["connection"].get("manufacturer"),
            "model": p["connection"].get("model"),
            "type": p["connection"]["type"],
            "port": p["connection"].get("port"),
            "unit_id": p["connection"].get("unit_id"),
            "capabilities": p["capabilities"],
        }
        for p in _library(request).direct
    ]


@router.get("/direct/status")
async def direct_status(request: Request) -> dict[str, dict[str, Any]]:
    """Last read of each direct device: time and errors."""
    hub: DirectHub = request.app.state.direct
    return {
        d.id: {"read_at": d.read_at, "error": d.error, "values": d.values} for d in hub.devices.values()
    }


async def _connect(request: Request) -> None:
    """Take the configuration over and read once, so the device shows values right away."""
    hub: DirectHub = request.app.state.direct
    await hub.sync(request.app.state.store.load(), _library(request))
    await hub.poll()


@router.post("/config/devices/direct")
async def add_direct_device(request: Request, body: DirectDevice) -> dict[str, Any]:
    """Add a device by its profile and address."""
    library = _library(request)
    if (profile := library.profiles.get(body.profile)) is None or "connection" not in profile:
        raise HTTPException(status_code=422, detail="Unbekanntes Profil für eine Direktanbindung")
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = DeviceConfig(
        ha_device_id="",
        profile=body.profile,
        profile_version=profile["version"],
        name=body.name.strip() or profile["connection"].get("model") or body.profile,
        connection=Connection(host=body.host.strip(), port=body.port, unit_id=body.unit_id),
    )
    device.ha_device_id = direct_device_id(device.id)
    config.devices.append(device)
    store.save(config)
    await _connect(request)
    _replan(request)
    return device_with_relations(config, device)


@router.put("/config/devices/direct/{device_id}")
async def update_direct_device(request: Request, device_id: str, body: DirectDevice) -> dict[str, Any]:
    """Change the address (or name) of a direct device."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if (device := config.device(device_id)) is None or device.connection is None:
        raise HTTPException(status_code=404, detail="Direct device not configured")
    device.connection = Connection(host=body.host.strip(), port=body.port, unit_id=body.unit_id)
    device.name = body.name.strip() or device.name
    store.save(config)
    await _connect(request)
    return device_with_relations(config, device)
