"""Heat pumps: link status of devices recognized by their manifest."""

from typing import Any

from fastapi import APIRouter, Request

from sunflow.devices.profiles import manifest_id

from .common import _library

router = APIRouter()


def manifest_status(request: Request) -> dict[str, dict[str, Any]]:
    """Per configured device: link status (compatible, incompatible, unavailable, ...)."""
    manifests = getattr(request.app.state, "manifests", None)
    return manifests.status() if manifests is not None else {}


def manifest_notes(request: Request) -> list[str]:
    """Manifests no Home Assistant device could be assigned to."""
    manifests = getattr(request.app.state, "manifests", None)
    return manifests.notes() if manifests is not None else []


@router.get("/heat_pump/status")
async def heat_pump_status(request: Request) -> dict[str, Any]:
    """Devices recognized by their manifest with compatibility, manifest and profile version."""
    library = _library(request)
    status = manifest_status(request)
    devices = []
    for device in request.app.state.store.load().devices:
        profile = library.profiles.get(device.profile)
        if profile is None or not manifest_id(profile):
            continue
        devices.append(
            {
                "id": device.id,
                "name": device.name,
                "ha_device_id": device.ha_device_id,
                "mac": device.mac,
                "component": device.component,
                "profile": device.profile,
                "profile_version": {"configured": device.profile_version, "current": profile["version"]},
            }
            | status.get(device.id, {"status": "pending", "usable": False, "problems": [], "manifest": None})
        )
    return {"devices": devices, "notes": manifest_notes(request)}
