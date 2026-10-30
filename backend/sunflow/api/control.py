"""The control tab: overrides, priorities, strategy and Sunflow's control of the devices."""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from sunflow.config.models import (
    BATTERY_PRIORITY_ID,
    DeviceOverride,
    PriorityCondition,
    StrategyOverride,
    SunflowConfig,
)
from sunflow.config.store import ConfigStore
from sunflow.control.controller import PlanController
from sunflow.devices.profiles import controls_battery
from sunflow.devices.resolver import preview
from sunflow.devices.site import device_category, device_kind
from sunflow.planning.vehicles import METHODS, vehicle_at
from sunflow.shadow.runner import ShadowRunner

from .common import _configured_device, _library, _replan, _snapshot
from .plan import _flex_windows

router = APIRouter()


@router.get("/control")
async def get_control(request: Request) -> dict[str, Any]:
    """Everything the control tab needs: strategies, overrides, live values, next windows, priorities."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    now = datetime.now(UTC)
    runner: ShadowRunner = request.app.state.runner
    latest = runner.latest
    infos = {f.device_id: f for f in latest.flex} if latest else {}
    names = {BATTERY_PRIORITY_ID: "Hausspeicher"}
    devices = []
    for device in config.devices:
        profile = profiles[device.profile]
        if "loadpoint" not in profile["capabilities"] and "load" not in profile["capabilities"]:
            continue
        names[device.id] = device.name
        roles = {
            r.role: r
            for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
        }
        effective, paused = config.effective_flex(device, now)
        recognized, vehicle_soc = vehicle_at(config, device, roles, snapshot.states)
        vehicle = recognized.vehicle
        override = config.overrides.get(device.id)
        info = infos.get(device.id)
        devices.append(
            {
                "id": device.id,
                "name": device.name,
                "kind": device_kind(profile, device, snapshot) or "load",
                "category": device_category(profile, device, snapshot),
                "default": config.flex_of(device.id).model_dump(),
                "override": override.model_dump(mode="json") if override and override.active(now) else None,
                "effective": effective.model_dump(),
                "paused": paused,
                "power_w": next((roles[r].value for r in ("charge_power", "power") if r in roles), None),
                "power_display": next(
                    (roles[r].display for r in ("charge_power", "power") if r in roles and roles[r].entity_id), None
                ),
                # The car's sensor now; the plan's value may be up to a slot old
                "level": vehicle_soc
                if vehicle_soc is not None
                else info.current
                if info and info.vehicle_id
                else roles["vehicle_soc"].value
                if "vehicle_soc" in roles
                else None,
                "target": info.target if info else None,
                "vehicles": [{"id": v.id, "name": v.name} for v in config.vehicles_at(device.id)],
                "vehicle": {"id": info.vehicle_id, "name": info.vehicle, "method": METHODS.get(info.vehicle_method or "")}
                if info and info.vehicle_id
                else {"id": vehicle.id, "name": vehicle.name, "method": METHODS.get(recognized.method or "")}
                if vehicle
                else None,
                "selected_vehicle": config.selected_vehicles.get(device.id),
                "manual_choice": config.detection_of(device.id).manual,
                "connected": roles["connected"].raw_state == "on" if "connected" in roles else None,
                "need_wh": info.need_wh if info else None,
                "reason": info.reason if info else None,
                "windows": _flex_windows(latest.plan.slots, device.name) if latest else [],
            }
        )
    has_battery = any(controls_battery(profiles[d.profile]) for d in config.devices)
    order = [i for i in config.priority_order() if i in names and (i != BATTERY_PRIORITY_ID or has_battery)]
    strategy_override = config.strategy_override
    return {
        "strategy": {
            "default": config.optimizer.strategy,
            "override": strategy_override.model_dump(mode="json")
            if strategy_override and strategy_override.active(now)
            else None,
            "effective": config.effective_strategy(now),
            "available": has_battery,
        },
        "devices": devices,
        "priorities": [
            {
                "id": i,
                "name": names[i],
                "conditions": [c.model_dump() for c in config.valid_conditions(i)],
                "hidden_conditions": [c.model_dump() for c in config.hidden_conditions(i)],
            }
            for i in order
        ],
        # Storages a consumer can wait for: home battery (%), vehicles (%) and hot water tanks (°C)
        "condition_sources": (
            [{"id": BATTERY_PRIORITY_ID, "name": names[BATTERY_PRIORITY_ID], "unit": "%", "kind": "battery"}]
            if has_battery
            else []
        )
        + [
            {"id": d["id"], "name": d["name"], "unit": "°C" if d["kind"] == "heater" else "%", "kind": d["kind"]}
            for d in devices
            if d["kind"] in ("vehicle", "heater")
        ],
        "plan_computed": runner.status.last_plan.isoformat() if runner.status.last_plan else None,
    }


@router.put("/control/devices/{device_id}")
async def set_device_override(request: Request, device_id: str, body: DeviceOverride) -> dict[str, Any]:
    """Temporarily change how a consumer is planned (strategy, target, pause)."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if config.device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    config.overrides[device_id] = body
    store.save(config)
    _replan(request)
    return body.model_dump(mode="json")


@router.delete("/control/devices/{device_id}")
async def clear_device_override(request: Request, device_id: str) -> None:
    """Return a consumer to its default planning."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.overrides.pop(device_id, None)
    store.save(config)
    _replan(request)


class ControlSwitch(BaseModel):
    """Main switch and/or one device switch of the control."""

    enabled: bool | None = None
    device_id: str | None = None
    device_enabled: bool | None = None


async def _control_status(request: Request, config: SunflowConfig) -> dict[str, Any]:
    controller: PlanController = request.app.state.controller
    snapshot = await _snapshot(request)
    devices = []
    for device in config.devices:
        if not controller.controllable(device):
            continue
        status = controller.statuses.get(device.id)
        devices.append(
            {
                "device_id": device.id,
                "name": device.name,
                "enabled": config.control.devices.get(device.id, False),
                "blocked": controller.eligible(config, device),
                "status": asdict(status) if status else None,
                "kind": "loadpoint" if "charge_control" in _library(request).profiles[device.profile] else "battery",
                "category": device_category(_library(request).profiles[device.profile], device, snapshot),
            }
        )
    return {"enabled": config.control.enabled, "paused": controller.paused, "devices": devices}


@router.get("/control/takeover")
async def get_takeover(request: Request) -> dict[str, Any]:
    """Whether Sunflow controls which device, and what it did last."""
    return await _control_status(request, request.app.state.store.load())


@router.put("/control/takeover")
async def set_takeover(request: Request, body: ControlSwitch, confirm: bool = False) -> dict[str, Any]:
    """Switch control on or off. Switching on writes to the devices, so confirm=true is required."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if body.device_id is not None:
        device = _configured_device(request, body.device_id)
        if body.device_enabled:
            if (blocked := request.app.state.controller.eligible(config, device)) is not None:
                raise HTTPException(status_code=422, detail=f"{device.name}: {blocked}")
        config.control.devices[device.id] = bool(body.device_enabled)
    if body.enabled is not None:
        config.control.enabled = body.enabled
    # Only switching on needs the confirmation; switching off hands the devices back
    switching_on = (body.enabled or body.device_enabled) and config.control.enabled and any(config.control.devices.values())
    if switching_on and not confirm:
        raise HTTPException(status_code=400, detail="Control writes to the devices; pass confirm=true")
    store.save(config)
    # Applied (or handed back) within the next minute by the background loop
    return await _control_status(request, config)


@router.put("/control/strategy")
async def set_strategy_override(request: Request, body: StrategyOverride) -> dict[str, Any]:
    """Temporarily change the battery strategy."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.strategy_override = body
    store.save(config)
    _replan(request)
    return body.model_dump(mode="json")


@router.delete("/control/strategy")
async def clear_strategy_override(request: Request) -> None:
    """Return to the default battery strategy."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.strategy_override = None
    store.save(config)
    _replan(request)


@router.put("/control/priorities")
async def set_priorities(request: Request, order: list[str]) -> list[str]:
    """Store the priority order (device ids and "battery"), highest first."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    known = {d.id for d in config.devices} | {BATTERY_PRIORITY_ID}
    if unknown := [i for i in order if i not in known]:
        raise HTTPException(status_code=422, detail=f"Unknown ids: {unknown}")
    config.priorities = order
    store.save(config)
    _replan(request)
    return config.priority_order()


@router.put("/control/priorities/{device_id}/conditions")
async def set_priority_conditions(
    request: Request, device_id: str, conditions: list[PriorityCondition]
) -> list[dict[str, Any]]:
    """Store the levels other storages must reach before a consumer gets PV or battery energy."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if device_id != BATTERY_PRIORITY_ID and config.device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    # Only entries ranked higher can hold a consumer back
    above = config.ranked_above(device_id)
    if invalid := [c.device_id for c in conditions if c.device_id not in above]:
        raise HTTPException(status_code=422, detail=f"Conditions must refer to higher entries: {invalid}")
    # Hidden conditions (entry currently ranked lower) are kept for when it moves up again
    stored = conditions + config.hidden_conditions(device_id)
    if stored:
        config.priority_conditions[device_id] = stored
    else:
        config.priority_conditions.pop(device_id, None)
    store.save(config)
    _replan(request)
    return [c.model_dump() for c in conditions]
