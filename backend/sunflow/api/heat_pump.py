"""Heat pumps: link status of devices recognized by their manifest, their current states, the house's
room setpoint (climate entity "Haus") and the shadow flow control."""

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from sunflow.config.models import ClimateSettings, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.profiles import manifest_id
from sunflow.heat_pump.defaults import ROOM_SETPOINT_MAX_C, ROOM_SETPOINT_MIN_C
from sunflow.heat_pump.samples import records_heat_pump
from sunflow.heat_pump.shadow import climate_status

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


def _recorder(request: Request) -> Any:
    return getattr(getattr(request.app.state, "runner", None), "heat_pump", None)


def house_status(request: Request, config: SunflowConfig) -> dict[str, Any] | None:
    """Room setpoint, mode and room temperature; None without rooms and heat pump (no climate entity then)."""
    profiles = _library(request).profiles
    if not config.house.rooms and not any(records_heat_pump(profiles.get(d.profile, {})) for d in config.devices):
        return None
    recorder = _recorder(request)
    flow = getattr(recorder, "flow", None)
    status = flow.house_status(config) if flow is not None else climate_status(config, None, None)
    # The heat pumps' flow targets, for the climate entity's attributes
    states = recorder.status() if recorder is not None else {}
    status["heat_pumps"] = [
        {"id": d.id, "name": d.name, "activity": (states.get(d.id) or {}).get("activity"),
         "compressor": (states.get(d.id) or {}).get("compressor"), "flow": (states.get(d.id) or {}).get("flow")}
        for d in config.devices
        if records_heat_pump(profiles.get(d.profile, {}))
    ]
    return status


@router.get("/heat_pump/status")
async def heat_pump_status(request: Request) -> dict[str, Any]:
    """Devices recognized by their manifest with compatibility, manifest and profile version."""
    library = _library(request)
    status = manifest_status(request)
    recorder = _recorder(request)
    states = recorder.status() if recorder is not None else {}
    config = request.app.state.store.load()
    devices = []
    for device in config.devices:
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
            # Program, activity, block, fault, whether it heats validly (section 4) and the shadow
            # flow control (section 8); None before the first sample
            | {"state": states.get(device.id)}
        )
    return {"devices": devices, "notes": manifest_notes(request), "house": house_status(request, config)}


class ClimateUpdate(BaseModel):
    """A change of the climate entity "Haus": setpoint and/or mode; values outside the range are refused."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    setpoint_c: float | None = Field(default=None, ge=ROOM_SETPOINT_MIN_C, le=ROOM_SETPOINT_MAX_C)
    mode: Literal["heat", "off"] | None = None


@router.get("/house/climate")
async def get_climate(request: Request) -> dict[str, Any]:
    """Room setpoint and mode."""
    return request.app.state.store.load().house.climate.model_dump()


@router.put("/house/climate")
async def set_climate(request: Request, body: ClimateUpdate) -> dict[str, Any]:
    """Change the room setpoint (stored to 0.1 K) or the mode; saved at once, also across restarts."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    current = config.house.climate
    climate = ClimateSettings.model_validate(current.model_dump() | body.model_dump(exclude_none=True))
    if climate.model_dump() != current.model_dump():
        config.house.climate = climate
        store.save(config)
    return climate.model_dump()
