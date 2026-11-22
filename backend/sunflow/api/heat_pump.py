"""Heat pumps: link status of devices recognized by their manifest, their current states, the house's
room setpoint (climate entity "Haus") and the shadow flow control."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sunflow.config.models import ClimateSettings, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.profiles import manifest_id
from sunflow.heat_pump.budget import BudgetLimits
from sunflow.heat_pump.defaults import FLOW_LIMIT_HIGH_C, FLOW_LIMIT_LOW_C, ROOM_SETPOINT_MAX_C, ROOM_SETPOINT_MIN_C
from sunflow.heat_pump.guard import daily_counts
from sunflow.heat_pump.limits import ControlSettings, setpoint_range, surface_range
from sunflow.heat_pump.samples import records_heat_pump
from sunflow.heat_pump.shadow import climate_status

from .common import _library, _local_tz

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


def heat_pump_notes(request: Request, config: SunflowConfig) -> list[str]:
    """Heat pumps whose set value someone else changes or the ESP regulates (8.5, for the setup summary)."""
    flow = getattr(_recorder(request), "flow", None)
    if flow is None or not hasattr(flow, "summary_notes"):
        return []
    return flow.summary_notes({d.id: d.name for d in config.devices}, datetime.now(UTC))


def _manifest(request: Request, device: Any) -> dict[str, Any] | None:
    """The manifest the device was last linked with (entity ranges); None before the first one."""
    recorder = _recorder(request)
    return recorder.manifest(device.id) if recorder is not None and hasattr(recorder, "manifest") else None


def flow_limits(config: SunflowConfig, device: Any, profile: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Range of the set value (8.3): the user's setting, the heating surface type's start limits, the
    entity's range from the manifest and what applies."""
    settings = ControlSettings.from_profile(profile, manifest, device.flow_min_c, device.flow_max_c)
    surface = config.house.heating_surface
    default_min, default_max = surface_range(surface)
    low, high = setpoint_range(surface, settings)
    return {
        "min_c": device.flow_min_c, "max_c": device.flow_max_c,
        "default_min_c": default_min, "default_max_c": default_max,
        "entity_min_c": settings.entity_min, "entity_max_c": settings.entity_max,
        "effective_min_c": low, "effective_max_c": high,
        "allowed_min_c": FLOW_LIMIT_LOW_C, "allowed_max_c": FLOW_LIMIT_HIGH_C,
    }


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
            | {"state": states.get(device.id), "flow_limits": flow_limits(config, device, profile, _manifest(request, device))}
        )
    return {"devices": devices, "notes": manifest_notes(request), "house": house_status(request, config)}


# Days the counts of planned writes cover at most
WRITES_DAYS_MAX = 31


@router.get("/heat_pump/{device_id}/shadow_writes")
async def shadow_writes(request: Request, device_id: str, days: int = Query(default=7, ge=1, le=WRITES_DAYS_MAX)) -> dict[str, Any]:
    """Per day (Home Assistant's time zone): writes Sunflow would have caused, by reason, and unwanted
    shutdowns (shadow, 8.4), with the budget of the profile (8.5). The writes are those that passed the
    minimum spacing and the budget; a block set instead of a protection step counts as block."""
    config = request.app.state.store.load()
    device = next((d for d in config.devices if d.id == device_id), None)
    profile = _library(request).profiles.get(device.profile) if device else None
    if device is None or profile is None or not records_heat_pump(profile):
        raise HTTPException(status_code=404, detail="No heat pump with this id")
    now = datetime.now(UTC)
    tz = await _local_tz(request)
    since = datetime.combine(now.astimezone(tz).date() - timedelta(days=days - 1), datetime.min.time(), tz)
    recorder = _recorder(request)
    store = getattr(getattr(request.app.state, "runner", None), "shadow", None)
    rows = await asyncio.to_thread(store.heat_pump_samples, device_id, since) if store is not None else []
    events = [e for row in rows for e in row.get("events") or []]
    if recorder is not None:
        events += recorder.pending_events(device_id)
    flow = getattr(recorder, "flow", None)
    return {
        "days": daily_counts(events, tz, days, now),
        # The budget of the profile (defaults where it names none); counted virtually in the shadow (8.5)
        "budget": BudgetLimits.from_writes((profile.get("flow_control") or {}).get("writes") or {}).as_dict(),
        "shadow": not flow.writing_active(device_id) if flow is not None else True,
    }


@router.post("/heat_pump/{device_id}/write_now", status_code=202)
async def write_now(request: Request, device_id: str) -> dict[str, Any]:
    """"Write now" (8.5): the next tick writes the pending set value without waiting for the minimum
    spacing (the budget still applies). 409 when nothing is pending or Sunflow does not write to the heat
    pump (shadow), with the reason; the wish lapses when the next tick cannot write it."""
    config = request.app.state.store.load()
    device = next((d for d in config.devices if d.id == device_id), None)
    profile = _library(request).profiles.get(device.profile) if device else None
    if device is None or profile is None or not records_heat_pump(profile):
        raise HTTPException(status_code=404, detail="No heat pump with this id")
    flow = getattr(_recorder(request), "flow", None)
    if flow is None:
        raise HTTPException(status_code=409, detail={"reason": "not_active"})
    accepted, reason, pending = flow.request_write_now(device_id, datetime.now(UTC))
    if not accepted:
        raise HTTPException(status_code=409, detail={"reason": reason})
    return {"accepted": True, "pending": pending}


class FlowLimits(BaseModel):
    """Range of the set value of one heat pump; None: the heating surface type's start limit."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    min_c: float | None = Field(default=None, ge=FLOW_LIMIT_LOW_C, le=FLOW_LIMIT_HIGH_C)
    max_c: float | None = Field(default=None, ge=FLOW_LIMIT_LOW_C, le=FLOW_LIMIT_HIGH_C)

    @model_validator(mode="after")
    def _ordered(self) -> "FlowLimits":
        if self.min_c is not None and self.max_c is not None and self.min_c >= self.max_c:
            raise ValueError("min_c must be below max_c")
        return self


@router.put("/heat_pump/{device_id}/flow_limits")
async def set_flow_limits(request: Request, device_id: str, body: FlowLimits) -> dict[str, Any]:
    """Set the range of the set value (Max T-Vorlauf) of one heat pump; rounded to a tenth. Together with
    the heating surface type's start limit for an empty side, the range must leave something."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = next((d for d in config.devices if d.id == device_id), None)
    profile = _library(request).profiles.get(device.profile) if device else None
    if device is None or profile is None or not records_heat_pump(profile):
        raise HTTPException(status_code=404, detail="No heat pump with this id")
    low = round(body.min_c, 1) if body.min_c is not None else None
    high = round(body.max_c, 1) if body.max_c is not None else None
    default_min, default_max = surface_range(config.house.heating_surface)
    if (low if low is not None else default_min) >= (high if high is not None else default_max):
        raise HTTPException(status_code=422, detail="The lower limit must be below the upper limit")
    device.flow_min_c, device.flow_max_c = low, high
    store.save(config)
    return flow_limits(config, device, profile, _manifest(request, device))


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
