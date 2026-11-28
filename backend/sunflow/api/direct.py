"""Devices Sunflow talks to directly (Modbus TCP, HTTP): profiles, adding, address, status."""

import re
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from sunflow.config.models import Connection, DeviceConfig
from sunflow.config.store import ConfigStore
from sunflow.direct.hub import DirectHub, direct_device_id
from sunflow.i18n import t

from .common import _library, _replan
from .devices import device_with_relations

router = APIRouter()
# The Sunflow EEBUS App's API: in the host network, reached over the Supervisor's network
EEBUS_HOST = "172.30.32.1"
EEBUS_PORT = 4713


class DirectDevice(BaseModel):
    """A device to add: its profile and where to reach it."""

    profile: str
    name: str = ""
    host: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    unit_id: int | None = Field(default=None, ge=0, le=255)
    # Profiles with a remote: the device behind the gateway (an EEBUS wallbox's SKI)
    remote: str | None = Field(default=None, max_length=64)


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
            "host": p["connection"].get("host"),
            "remote": p["connection"].get("remote"),
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


def _remote(remote: str | None) -> str | None:
    """An EEBUS SKI as devices show it (groups, upper case) in the form the EEBUS App uses."""
    if not remote:
        return None
    ski = re.sub(r"[\s:-]", "", remote).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", ski):
        raise HTTPException(status_code=422, detail=t("Die SKI hat 40 Hexadezimalzeichen"))
    return ski


def _eebus_url(host: str, port: int | None) -> str:
    return f"http://{host}:{port or EEBUS_PORT}"


@router.get("/eebus/status")
async def eebus_status(host: str = EEBUS_HOST, port: int | None = None) -> dict[str, Any]:
    """The Sunflow EEBUS App: its SKI (to enter at the wallbox) and the EEBUS devices it sees."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(f"{_eebus_url(host, port)}/status") as response:
                response.raise_for_status()
                return await response.json()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise HTTPException(status_code=502, detail=t("Die App „Sunflow EEBUS“ antwortet nicht ({error})", error=err)) from err


@router.post("/eebus/trust/{ski}")
async def eebus_trust(ski: str, host: str = EEBUS_HOST, port: int | None = None) -> dict[str, Any]:
    """Trust an EEBUS wallbox: the EEBUS App connects to it from then on."""
    remote = _remote(ski)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.post(f"{_eebus_url(host, port)}/trust/{remote}") as response:
                response.raise_for_status()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise HTTPException(status_code=502, detail=t("Die App „Sunflow EEBUS“ antwortet nicht ({error})", error=err)) from err
    return {"ski": remote}


@router.post("/config/devices/direct")
async def add_direct_device(request: Request, body: DirectDevice) -> dict[str, Any]:
    """Add a device by its profile and address."""
    library = _library(request)
    if (profile := library.profiles.get(body.profile)) is None or "connection" not in profile:
        raise HTTPException(status_code=422, detail=t("Unbekanntes Profil für eine Direktanbindung"))
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = DeviceConfig(
        ha_device_id="",
        profile=body.profile,
        profile_version=profile["version"],
        name=body.name.strip() or profile["connection"].get("model") or body.profile,
        connection=Connection(host=body.host.strip(), port=body.port, unit_id=body.unit_id, remote=_remote(body.remote)),
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
    device.connection = Connection(host=body.host.strip(), port=body.port, unit_id=body.unit_id, remote=_remote(body.remote))
    device.name = body.name.strip() or device.name
    store.save(config)
    await _connect(request)
    return device_with_relations(config, device)
