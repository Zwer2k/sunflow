"""The control tab: overrides, priorities, strategy and Sunflow's control of the devices."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from sunflow.config.models import (
    BATTERY_PRIORITY_ID,
    BatteryOverride,
    DeviceOverride,
    PriorityCondition,
    StrategyOverride,
    SunflowConfig,
    Vehicle,
)
from sunflow.config.store import ConfigStore
from sunflow.control.aux_battery import aux_voltage
from sunflow.control.controller import PlanController
from sunflow.devices.profiles import controls_battery
from sunflow.devices.resolver import preview
from sunflow.devices.site import device_category, device_kind
from sunflow.i18n import t
from sunflow.planning.plan import FlexInfo
from sunflow.planning.vehicles import METHODS, scanned_card, vehicle_at
from sunflow.shadow.runner import ShadowRunner

from .common import _configured_device, _library, _replan, _snapshot
from .plan import _flex_windows

router = APIRouter()


def _method(method: str | None) -> str | None:
    """How the vehicle at a wallbox was recognized, for the UI."""
    return t(METHODS[method]) if method in METHODS else None


def _aux_battery(
    vehicle: Vehicle | None, states: dict[str, dict[str, Any]], controller: PlanController | None, device_id: str, now: datetime
) -> dict[str, Any] | None:
    """The 12 V battery of the recognized car, when watched: voltage, threshold and a running top-up."""
    if vehicle is None or not vehicle.aux_battery.voltage_entity:
        return None
    until = controller.aux_charging_until(device_id, now) if controller else None
    return {
        "voltage": aux_voltage(vehicle, states),
        "min_voltage": vehicle.aux_battery.min_voltage_v,
        "charging_until": until.isoformat() if until else None,
    }


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
    names = {BATTERY_PRIORITY_ID: t("Hausspeicher")}
    controller: PlanController | None = getattr(request.app.state, "controller", None)
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
                "default_target": _default_target(config, device.id, info, vehicle, override, now),
                "vehicles": [{"id": v.id, "name": v.name} for v in config.vehicles_at(device.id)],
                "vehicle": {"id": info.vehicle_id, "name": info.vehicle, "method": _method(info.vehicle_method)}
                if info and info.vehicle_id
                else {"id": vehicle.id, "name": vehicle.name, "method": _method(recognized.method)}
                if vehicle
                else None,
                "selected_vehicle": config.selected_vehicles.get(device.id),
                # The RFID card scanned at the wallbox, also when it belongs to no vehicle
                "card": scanned_card(roles["transaction"].raw_state) if "transaction" in roles else None,
                "aux_battery": _aux_battery(vehicle, snapshot.states, controller, device.id, now),
                "manual_choice": config.detection_of(device.id).manual,
                "connected": roles["connected"].raw_state == "on" if "connected" in roles else None,
                "need_wh": info.need_wh if info else None,
                "reason": info.reason if info else None,
                "windows": _flex_windows(latest.plan.slots, device.name) if latest else [],
                "live": device.id in config.display.live_controls,
                # A battery of its own without a plug entity: the user can say it is plugged in
                "charger": controller.chargers.status(device, snapshot.states) | {"manual": not device.charger.connected_entity}
                if controller is not None and device.charger is not None
                else None,
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
        # Each controllable battery with its temporary mode
        "batteries": [
            {
                "id": d.id,
                "name": d.name,
                "override": override.model_dump(mode="json")
                if (override := config.battery_overrides.get(d.id)) is not None and override.active(now)
                else None,
            }
            for d in config.devices
            if controls_battery(profiles[d.profile])
        ],
        "devices": devices,
        "priorities": [
            {
                "id": i,
                "name": names[i],
                # Conditions on switched off devices stay stored but are not shown
                "conditions": [c.model_dump() for c in config.valid_conditions(i) if c.device_id in names],
                "hidden_conditions": [c.model_dump() for c in config.hidden_conditions(i) if c.device_id in names],
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
        "plug_in_hints": latest.plug_in_hints if latest else [],
    }


@router.get("/control/log")
async def control_log(request: Request, hours: int = 24, device_id: str | None = None) -> dict[str, Any]:
    """What Sunflow wrote to the devices it controls and what others changed on them."""
    controller: PlanController | None = getattr(request.app.state, "controller", None)
    if controller is None or controller.journal is None:
        return {"entries": []}
    since = datetime.now(UTC) - timedelta(hours=min(max(hours, 1), 14 * 24))
    return {"entries": controller.journal.entries(since, device_id)}


def _default_target(
    config: SunflowConfig, device_id: str, info: FlexInfo | None, vehicle: Vehicle | None, override: DeviceOverride | None, now: datetime
) -> float | None:
    """The target without a change by hand: the vehicle's charging settings at this wallbox, the consumer's, else the plan's (evcc)."""
    vehicle_id = info.vehicle_id if info and info.vehicle_id else vehicle.id if vehicle else None
    profile = config.charging_profile(vehicle_id, device_id) if vehicle_id else None
    configured = (profile.flex if profile else config.flex_of(device_id)).target
    if configured is not None:
        return configured
    by_hand = override is not None and override.active(now) and override.target is not None
    return info.target if info and not by_hand else None


@router.put("/control/devices/{device_id}")
async def set_device_override(request: Request, device_id: str, body: DeviceOverride) -> dict[str, Any]:
    """Temporarily change how a consumer is planned (strategy, target, pause)."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if config.device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    limit = config.flex_of(device_id).target_max
    if body.target is not None and limit is not None and body.target > limit:
        raise HTTPException(status_code=422, detail=f"Target above the configured maximum of {limit:g}")
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


class LiveControl(BaseModel):
    """Whether a consumer has quick controls in the live view."""

    shown: bool


@router.put("/control/devices/{device_id}/live")
async def set_live_control(request: Request, device_id: str, body: LiveControl) -> dict[str, Any]:
    """Show or hide a consumer's quick controls in the live view."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if config.device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    shown = [d for d in config.display.live_controls if d != device_id]
    config.display.live_controls = [*shown, device_id] if body.shown else shown
    store.save(config)
    return {"live_controls": config.display.live_controls}


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


class Plugged(BaseModel):
    """A load with a battery of its own is plugged in now (or no longer)."""

    plugged: bool


@router.put("/control/chargers/{device_id}")
async def set_plugged(request: Request, device_id: str, body: Plugged) -> dict[str, Any]:
    """Mark a load with a battery of its own as plugged in, so it is planned without waiting for a test charge."""
    config = request.app.state.store.load()
    controller: PlanController | None = getattr(request.app.state, "controller", None)
    if (device := config.device(device_id)) is None or device.charger is None or controller is None:
        raise HTTPException(status_code=404, detail="No load with a battery of its own")
    controller.chargers.set_plugged(device, body.plugged, datetime.now(UTC))
    _replan(request)
    return {"device_id": device_id, "plugged": body.plugged}


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


@router.put("/control/batteries/{device_id}")
async def set_battery_override(request: Request, device_id: str, body: BatteryOverride) -> dict[str, Any]:
    """Temporarily hold a battery or let it run on its own."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if config.device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    config.battery_overrides[device_id] = body
    store.save(config)
    _replan(request)
    return body.model_dump(mode="json")


@router.delete("/control/batteries/{device_id}")
async def clear_battery_override(request: Request, device_id: str) -> None:
    """Let the plan use the battery again."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.battery_overrides.pop(device_id, None)
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
