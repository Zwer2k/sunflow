"""Heat pumps: link status of devices recognized by their manifest, their current states, the house's
room setpoint (climate entity "Haus") and the shadow flow control."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from sunflow.config.models import ClimateSettings, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.profiles import manifest_id
from sunflow.heat_pump.budget import BudgetLimits
from sunflow.heat_pump.defaults import (
    CURVE_SHIFT_RANGE_K,
    CURVE_SLOPE_LIMIT,
    FLOW_LIMIT_HIGH_C,
    FLOW_LIMIT_LOW_C,
    ROOM_MARGIN_MAX_K,
    ROOM_SETPOINT_MAX_C,
    ROOM_SETPOINT_MIN_C,
)
from sunflow.heat_pump.guard import daily_counts
from sunflow.heat_pump.limits import ControlSettings, setpoint_range, surface_range
from sunflow.heat_pump.samples import records_heat_pump
from sunflow.heat_pump.shadow import climate_status

from .common import _library, _local_tz, _replan

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
    """Heat pumps whose set value someone else changes or the ESP regulates (12, for the setup summary)."""
    flow = getattr(_recorder(request), "flow", None)
    if flow is None or not hasattr(flow, "summary_notes"):
        return []
    return flow.summary_notes({d.id: d.name for d in config.devices}, datetime.now(UTC))


def _manifest(request: Request, device: Any) -> dict[str, Any] | None:
    """The manifest the device was last linked with (entity ranges); None before the first one."""
    recorder = _recorder(request)
    return recorder.manifest(device.id) if recorder is not None and hasattr(recorder, "manifest") else None


def flow_limits(config: SunflowConfig, device: Any, profile: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Range of the set value (10): the user's setting, the heating surface type's start limits, the
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
    status = flow.house_status(config) if flow is not None else climate_status(config, None)
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
                # The user's switch: Sunflow writes to the heat pump for real (4)
                "writing": {"active": device.flow_active, "refused": writing_refused(request, device.id) if not device.flow_active else None},
                # Learning phase of the stop margin (11): Sunflow lowers straight to the flow target
                "learn_stops": device.flow_learn_stops,
                # Start values of the heating curve as stored (8); None: not set yet
                "curve_start": curve_start(request, device),
            }
            | status.get(device.id, {"status": "pending", "usable": False, "problems": [], "manifest": None})
            # Program, activity, block, fault, whether it heats validly (section 4) and the
            # flow control (sections 8-12); None before the first sample
            | {"state": states.get(device.id), "flow_limits": flow_limits(config, device, profile, _manifest(request, device))}
        )
    return {"devices": devices, "notes": manifest_notes(request), "house": house_status(request, config)}


# Days the counts of planned writes cover at most
WRITES_DAYS_MAX = 31


@router.get("/heat_pump/{device_id}/shadow_writes")
async def shadow_writes(request: Request, device_id: str, days: int = Query(default=7, ge=1, le=WRITES_DAYS_MAX)) -> dict[str, Any]:
    """Per day (Home Assistant's time zone): writes Sunflow would have caused, by reason, and unwanted
    shutdowns (shadow, 11), with the budget of the profile (12). The writes are those that passed the
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
        # The budget of the profile (defaults where it names none); counted virtually in the shadow (12)
        "budget": BudgetLimits.from_writes((profile.get("flow_control") or {}).get("writes") or {}).as_dict(),
        "shadow": not flow.writing_active(device_id) if flow is not None else True,
    }


# Hours the history of a heat pump covers at most
HISTORY_HOURS_MAX = 7 * 24
# Columns of the 5 minute rows the history view shows
HISTORY_COLUMNS = (
    "tv_c", "tr_c", "tv_max_c", "setpoint_c", "max_setpoint_c", "written_c", "target_c", "room_setpoint_c",
    "outside_c", "heat_w", "compressor", "bypass_pct", "activity",
)  # fmt: skip
# Events the history view marks
HISTORY_EVENTS = ("write", "start", "stop", "foreign_write", "flow_source")


def _room_mean(rooms: dict[str, Any] | None, weights: dict[str, float], left_out: list[str]) -> float | None:
    """Weighted mean of the configured rooms of a row, without the rooms with a fast change (as the room temperature)."""
    known = [(value, weights[e]) for e, value in (rooms or {}).items() if e in weights and e not in left_out and isinstance(value, int | float)]
    total = sum(w for _, w in known)
    return sum(v * w for v, w in known) / total if total > 0 else None


def history_rows(rows: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    """The 5 minute rows reduced to what the history view draws, with the room temperature per row."""
    result = []
    for row in rows:
        flags = row.get("flags") or {}
        ventilation = list(flags.get("ventilation") or [])
        result.append(
            {"start": row["start"]}
            | {column: row.get(column) for column in HISTORY_COLUMNS}
            | {
                "room_c": _room_mean(row.get("rooms"), weights, ventilation),
                "rooms": {e: v for e, v in (row.get("rooms") or {}).items() if e in weights},
                "valid": bool(flags.get("valid")),
                "blocked": bool(flags.get("blocked")),
                "ventilation": ventilation,
            }
        )
    return result


@router.get("/heat_pump/{device_id}/history")
async def history(request: Request, device_id: str, hours: int = Query(default=24, ge=1, le=HISTORY_HOURS_MAX)) -> dict[str, Any]:
    """The last hours of a heat pump for the history view: 5 minute means of flow, return, set values, flow
    target, room setpoint and rooms (room temperature as weighted mean without rooms with a fast change),
    states, and the events written, compressor starts and stops, foreign writes and changes of the source."""
    config = request.app.state.store.load()
    device = next((d for d in config.devices if d.id == device_id), None)
    profile = _library(request).profiles.get(device.profile) if device else None
    if device is None or profile is None or not records_heat_pump(profile):
        raise HTTPException(status_code=404, detail="No heat pump with this id")
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    store = getattr(getattr(request.app.state, "runner", None), "shadow", None)
    rows = await asyncio.to_thread(store.heat_pump_samples, device_id, since) if store is not None else []
    events = [e for row in rows for e in row.get("events") or []]
    recorder = _recorder(request)
    if recorder is not None:
        events += recorder.pending_events(device_id)
    weights = {room.entity_id: room.weight for room in config.house.rooms}
    return {
        "since": since.isoformat(),
        "until": now.isoformat(),
        "rows": history_rows(rows, weights),
        "events": sorted((e for e in events if e.get("kind") in HISTORY_EVENTS and isinstance(e.get("at"), str)), key=lambda e: e["at"]),
        "rooms": [{"entity_id": room.entity_id, "weight": room.weight} for room in config.house.rooms],
    }


@router.post("/heat_pump/{device_id}/write_now", status_code=202)
async def write_now(request: Request, device_id: str) -> dict[str, Any]:
    """"Write now" (12): the next tick writes the pending set value without waiting for the minimum
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


class Writing(BaseModel):
    """The switch of one heat pump: Sunflow writes to it for real (true) or computes in the shadow (false)."""

    model_config = ConfigDict(extra="forbid")

    active: StrictBool


def writing_refused(request: Request, device_id: str) -> str | None:
    """Why writing for real cannot be switched on now (4), None when it can: the heat pump
    must be compatible and reachable, sampled, in the strategy fixed and without the ESP regulation."""
    manifests = getattr(request.app.state, "manifests", None)
    if manifests is None or not manifests.usable(device_id):
        return "incompatible"
    recorder = _recorder(request)
    state = (recorder.status() if recorder is not None else {}).get(device_id)
    if state is None:
        return "no_sample"
    if state.get("strategy") != "fixed":
        return "strategy"
    guard = getattr(getattr(recorder, "flow", None), "guards", {}).get(device_id)
    if guard is not None and guard.foreign.regulating:
        return "esp_regulation"
    return None


@router.put("/heat_pump/{device_id}/writing")
async def set_writing(request: Request, device_id: str, body: Writing) -> dict[str, Any]:
    """Switch writing to the heat pump for real on or off (4; default off).

    On: 409 with the reason when the heat pump is not compatible, not sampled yet, not in the strategy fixed
    or the ESP regulates; Sunflow takes over with the next tick (correction so that the flow target is the real Max T-Vorlauf) and writes only
    when the rules ask for it. Off: always; nothing at the heat pump is reset (4)."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = next((d for d in config.devices if d.id == device_id), None)
    profile = _library(request).profiles.get(device.profile) if device else None
    if device is None or profile is None or not records_heat_pump(profile):
        raise HTTPException(status_code=404, detail="No heat pump with this id")
    if body.active and not device.flow_active and (reason := writing_refused(request, device_id)) is not None:
        raise HTTPException(status_code=409, detail={"reason": reason})
    if device.flow_active != body.active:
        device.flow_active = body.active
        store.save(config)
        # "Lernen + Regeln" plans the battery with the heat pump, "Nur Lernen" without (14)
        _replan(request)
    return {"active": device.flow_active}


@router.put("/heat_pump/{device_id}/learn_stops")
async def set_learn_stops(request: Request, device_id: str, body: Writing) -> dict[str, Any]:
    """Switch the learning phase of the stop margin on or off (11): on, Sunflow lowers the set value straight
    to the flow target and lets the compressor stop, to learn where it does; off, it keeps above the learned
    (or the profile's) shutdown bound. The user decides when the phase ends."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = next((d for d in config.devices if d.id == device_id), None)
    profile = _library(request).profiles.get(device.profile) if device else None
    if device is None or profile is None or not records_heat_pump(profile):
        raise HTTPException(status_code=404, detail="No heat pump with this id")
    if device.flow_learn_stops != body.active:
        device.flow_learn_stops = body.active
        store.save(config)
    return {"active": device.flow_learn_stops}


class CurveStart(BaseModel):
    """Start values of the heating curve of one heat pump, set by the user."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    # Checked against the range of the heat pump's own slope entity (curve_range)
    slope: float = Field(ge=0, le=CURVE_SLOPE_LIMIT)
    shift_k: float = Field(ge=-CURVE_SHIFT_RANGE_K, le=CURVE_SHIFT_RANGE_K)


def _heat_pump(request: Request, device_id: str) -> tuple[ConfigStore, SunflowConfig, Any]:
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = next((d for d in config.devices if d.id == device_id), None)
    profile = _library(request).profiles.get(device.profile) if device else None
    if device is None or profile is None or not records_heat_pump(profile):
        raise HTTPException(status_code=404, detail="No heat pump with this id")
    return store, config, device


def curve_range(request: Request, device: Any) -> ControlSettings:
    """Range and step of the slope: those of the heat pump's own slope entity (manifest), else the defaults."""
    profile = _library(request).profiles.get(device.profile) or {}
    return ControlSettings.from_profile(profile, _manifest(request, device))


def curve_start(request: Request, device: Any) -> dict[str, Any]:
    """Start values of the heating curve as stored (None: not set yet) and the range of the slope (8)."""
    settings = curve_range(request, device)
    return {
        "slope": device.curve_slope_start, "shift_k": device.curve_shift_start, "source": device.curve_start_source,
        "slope_min": settings.slope_min, "slope_max": settings.slope_max, "slope_step": settings.slope_step,
        "shift_max_k": CURVE_SHIFT_RANGE_K,
    }


def _set_start(
    request: Request, store: ConfigStore, config: SunflowConfig, device: Any, slope: float, shift_k: float, source: str,
) -> dict[str, Any]:
    device.curve_slope_start = round(slope, 3)
    device.curve_shift_start = round(shift_k, 2)
    device.curve_start_source = source
    store.save(config)
    # The curve and the flow target at once, not only with the next tick
    flow = getattr(_recorder(request), "flow", None)
    if flow is not None and hasattr(flow, "refresh_curve"):
        flow.refresh_curve(device.id, config, datetime.now(UTC))
    return {"slope": device.curve_slope_start, "shift_k": device.curve_shift_start, "source": source}


@router.put("/heat_pump/{device_id}/curve_start")
async def set_curve_start(request: Request, device_id: str, body: CurveStart) -> dict[str, Any]:
    """Start values of the heating curve (8), set by the user: they apply at once, the learned hours stay and
    are fitted anew towards them."""
    store, config, device = _heat_pump(request, device_id)
    settings = curve_range(request, device)
    if not settings.slope_min <= body.slope <= settings.slope_max:
        raise HTTPException(status_code=422, detail={"reason": "slope_range", "min": settings.slope_min, "max": settings.slope_max})
    return _set_start(request, store, config, device, body.slope, body.shift_k, "user")


@router.post("/heat_pump/{device_id}/curve_start/{origin}")
async def take_curve_start(request: Request, device_id: str, origin: Literal["heat_pump", "learned"]) -> dict[str, Any]:
    """Start values of the heating curve (8) taken over: the slope of the heat pump's own curve as read now
    (shift 0; 409 no_value without one), or the learned curve (409 nothing_learned before any hour counted)."""
    store, config, device = _heat_pump(request, device_id)
    flow = getattr(_recorder(request), "flow", None)
    if origin == "heat_pump":
        slope = flow.heat_pump_slope(device_id) if flow is not None else None
        if slope is None:
            raise HTTPException(status_code=409, detail={"reason": "no_value"})
        return _set_start(request, store, config, device, slope, 0.0, "heat_pump")
    curve = flow.curve(device_id, config, datetime.now(UTC)) if flow is not None else None
    if curve is None or curve.hours == 0:
        raise HTTPException(status_code=409, detail={"reason": "nothing_learned"})
    return _set_start(request, store, config, device, curve.slope, curve.shift_k, "learned")


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
    """A change of the climate entity "Haus": setpoint, mode and/or the planning's margin around the setpoint
    (preheat_k up, lower_k down; design/heat_pump.md 6); values outside the range are refused."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    setpoint_c: float | None = Field(default=None, ge=ROOM_SETPOINT_MIN_C, le=ROOM_SETPOINT_MAX_C)
    mode: Literal["heat", "off"] | None = None
    preheat_k: float | None = Field(default=None, ge=0.0, le=ROOM_MARGIN_MAX_K)
    lower_k: float | None = Field(default=None, ge=0.0, le=ROOM_MARGIN_MAX_K)


@router.get("/house/climate")
async def get_climate(request: Request) -> dict[str, Any]:
    """Room setpoint, mode and the planning's margin around the setpoint."""
    return request.app.state.store.load().house.climate.model_dump()


@router.put("/house/climate")
async def set_climate(request: Request, body: ClimateUpdate) -> dict[str, Any]:
    """Change the room setpoint, the margin (both stored to 0.1 K) or the mode; saved at once, also across restarts."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    current = config.house.climate
    climate = ClimateSettings.model_validate(current.model_dump() | body.model_dump(exclude_none=True))
    if climate.model_dump() != current.model_dump():
        config.house.climate = climate
        store.save(config)
        # The planning's band and target follow at once (14), not with the next quarter hour
        _replan(request)
    return climate.model_dump()
