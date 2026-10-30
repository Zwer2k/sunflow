"""Configured devices: discovery preview, entities, parameters, batteries, categories."""

from contextlib import suppress
from dataclasses import asdict
from typing import Any, Literal
import uuid

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field, FiniteFloat

from sunflow.analysis.efficiency import (
    EfficiencyEstimate,
    estimate_from_counters,
    estimate_from_history,
)
from sunflow.config.models import (
    DeviceConfig,
    FeedIn,
    FlexSettings,
    MeasuredBy,
    RoleOverride,
)
from sunflow.config.store import ConfigStore
from sunflow.control.controller import PlanController
from sunflow.devices.discovery import Snapshot, discover, entity_has_key
from sunflow.devices.executor import (
    ActionResult,
    ExecutorError,
    plan_action,
    plan_setup,
)
from sunflow.devices.resolver import (
    CANONICAL_SIGNS,
    check_balance,
    device_entities,
    preview,
)
from sunflow.devices.site import battery_module_ids, device_category, device_parameters
from sunflow.ha import HAError

from .common import _configured_device, _library, _replan, _snapshot, display_name

router = APIRouter()


class AddDevice(BaseModel):
    """Request body to add a discovered device."""

    ha_device_id: str
    profile: str | None = None
    role_overrides: dict[str, RoleOverride] = {}


class PreviewRequest(BaseModel):
    """Preview with unsaved role overrides."""

    profile: str | None = None
    role_overrides: dict[str, RoleOverride] = {}


@router.get("/devices/{device_id}/entities")
async def device_entity_list(request: Request, device_id: str) -> list[dict[str, Any]]:
    """Return the entities of a device and its sub-devices for manual mapping."""
    snapshot = await _snapshot(request)
    result = []
    for entity in device_entities(snapshot, device_id):
        state = snapshot.states.get(entity["entity_id"], {})
        attributes = state.get("attributes", {})
        result.append(
            {
                "entity_id": entity["entity_id"],
                "name": attributes.get("friendly_name") or entity["entity_id"],
                "state": state.get("state"),
                "unit": attributes.get("unit_of_measurement"),
                "device_class": attributes.get("device_class"),
            }
        )
    return sorted(result, key=lambda e: e["entity_id"])


@router.get("/devices/{device_id}/preview")
async def device_preview(
    request: Request, device_id: str, profile: str | None = None
) -> dict[str, Any]:
    """Return the live, normalized role values of a Home Assistant device.

    Without a profile parameter the profile found by discovery is used.
    Role overrides of a configured device are applied.
    """
    configured = next(
        (
            d
            for d in request.app.state.store.load().devices
            if d.ha_device_id == device_id
        ),
        None,
    )
    overrides = configured.role_overrides if configured else {}
    return await _preview(request, device_id, profile, overrides)


@router.post("/devices/{device_id}/preview")
async def device_preview_with_overrides(
    request: Request, device_id: str, body: PreviewRequest
) -> dict[str, Any]:
    """Return the preview with unsaved role overrides (advanced mode)."""
    return await _preview(request, device_id, body.profile, body.role_overrides)


async def _preview(
    request: Request,
    device_id: str,
    profile: str | None,
    overrides: dict[str, RoleOverride],
) -> dict[str, Any]:
    library = _library(request)
    snapshot = await _snapshot(request)

    if profile is None:
        devices = {
            d.device_id: d for i in discover(snapshot, library) for d in i.devices
        }
        if device_id not in devices:
            raise HTTPException(status_code=404, detail="Device not found")
        profile = devices[device_id].profile
        if profile is None:
            raise HTTPException(status_code=422, detail="No matching profile")
    if profile not in library.profiles:
        raise HTTPException(status_code=404, detail=f"Unknown profile {profile}")

    roles = preview(
        library.profiles[profile],
        snapshot,
        device_id,
        {role: o.model_dump() for role, o in overrides.items()},
    )
    balance = check_balance(roles)
    return {
        "device_id": device_id,
        "profile": profile,
        "complete": not any(r.missing and not r.optional for r in roles),
        "roles": [asdict(r) | {"missing": r.missing} for r in roles],
        "balance": asdict(balance) if balance else None,
    }


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    """Return the stored configuration."""
    return request.app.state.store.load().model_dump(mode="json")


@router.post("/config/devices")
async def add_device(request: Request, body: AddDevice) -> dict[str, Any]:
    """Add a discovered device to the configuration."""
    library = _library(request)
    store: ConfigStore = request.app.state.store
    snapshot = await _snapshot(request)
    # Load after the await: a parallel add in the meantime must not be overwritten
    config = store.load()
    if any(d.ha_device_id == body.ha_device_id for d in [*config.devices, *config.disabled_devices]):
        raise HTTPException(status_code=409, detail="Device already configured")
    found = {d.device_id: d for i in discover(snapshot, library) for d in i.devices}
    if (discovered := found.get(body.ha_device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    profile_id = body.profile or discovered.profile
    if profile_id not in library.profiles:
        raise HTTPException(status_code=422, detail="No matching profile")

    device = DeviceConfig(
        ha_device_id=body.ha_device_id,
        profile=profile_id,
        profile_version=library.profiles[profile_id]["version"],
        name=display_name(discovered, library.profiles[profile_id]),
        role_overrides=body.role_overrides,
    )
    profile = library.profiles[profile_id]
    if "loadpoint" in profile["capabilities"]:
        # Fixed on adding while the "SoC" is readable, so the category does not depend on it later
        roles = {r.role: r for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in body.role_overrides.items()})}
        if (soc := roles.get("vehicle_soc")) is not None and soc.raw_unit:
            device.kind = "heater" if soc.raw_unit in ("°C", "°F", "K") else "vehicle"
    config.devices.append(device)
    store.save(config)
    _replan(request)
    return device.model_dump()


class ManualDevice(BaseModel):
    """A switchable consumer added by hand (Shelly, ESPHome, smart plug ...)."""

    name: str
    switch_entity: str
    power_entity: str | None = None
    max_power: FiniteFloat = Field(default=500, gt=0, le=100_000)
    daily_energy_wh: FiniteFloat | None = Field(default=None, ge=0)


MANUAL_PROFILE = "generic/switch"


SWITCH_DOMAINS = ("switch.", "input_boolean.")


@router.get("/entities/candidates")
async def entity_candidates(request: Request) -> dict[str, list[dict[str, Any]]]:
    """Entities that can switch a consumer, power sensors to measure it and % sensors (vehicle SoC)."""
    snapshot = await _snapshot(request)
    switches, power, percent, presence = [], [], [], []
    for entity_id, state in sorted(snapshot.states.items()):
        attributes = state.get("attributes", {})
        item = {
            "entity_id": entity_id,
            "name": attributes.get("friendly_name") or entity_id,
            "state": state.get("state"),
            "unit": attributes.get("unit_of_measurement"),
        }
        if entity_id.startswith(SWITCH_DOMAINS):
            switches.append(item)
        elif entity_id.startswith("sensor.") and attributes.get("device_class") == "power":
            power.append(item)
        elif entity_id.startswith("sensor.") and attributes.get("unit_of_measurement") == "%":
            percent.append(item)
        if entity_id.startswith(("binary_sensor.", "device_tracker.")):
            # "Plugged in" or "at home" of a car
            presence.append(item)
    return {"switches": switches, "power": power, "percent": percent, "presence": presence}


@router.post("/config/devices/manual")
async def add_manual_device(request: Request, body: ManualDevice) -> dict[str, Any]:
    """Add a switchable consumer that is not found by discovery."""
    library = _library(request)
    if MANUAL_PROFILE not in library.profiles:
        raise HTTPException(status_code=500, detail="Generic profile missing")
    if not body.switch_entity.startswith(SWITCH_DOMAINS):
        raise HTTPException(status_code=422, detail="Switch entity must be a switch or input_boolean")
    store: ConfigStore = request.app.state.store
    config = store.load()
    overrides = {"switch": RoleOverride(entity_id=body.switch_entity)}
    if body.power_entity:
        overrides["power"] = RoleOverride(entity_id=body.power_entity)
    parameters = {"max_power": body.max_power}
    if body.daily_energy_wh is not None:
        parameters["daily_energy_wh"] = body.daily_energy_wh
    device = DeviceConfig(
        ha_device_id=f"manual:{uuid.uuid4().hex}",
        profile=MANUAL_PROFILE,
        profile_version=library.profiles[MANUAL_PROFILE]["version"],
        name=body.name.strip() or body.switch_entity,
        parameters=parameters,
        role_overrides=overrides,
    )
    config.devices.append(device)
    store.save(config)
    _replan(request)
    return device_with_relations(config, device)


@router.put("/config/devices/manual/{device_id}")
async def update_manual_device(request: Request, device_id: str, body: ManualDevice) -> dict[str, Any]:
    """Change name, entities, power and daily energy of a consumer added by hand."""
    if not body.switch_entity.startswith(SWITCH_DOMAINS):
        raise HTTPException(status_code=422, detail="Switch entity must be a switch or input_boolean")
    store: ConfigStore = request.app.state.store
    config = store.load()
    if (device := config.device(device_id)) is None or device.profile != MANUAL_PROFILE:
        raise HTTPException(status_code=404, detail="Manual device not configured")
    device.name = body.name.strip() or body.switch_entity
    device.role_overrides = {"switch": RoleOverride(entity_id=body.switch_entity)}
    if body.power_entity:
        device.role_overrides["power"] = RoleOverride(entity_id=body.power_entity)
    device.parameters["max_power"] = body.max_power
    if body.daily_energy_wh is None:
        device.parameters.pop("daily_energy_wh", None)
    else:
        device.parameters["daily_energy_wh"] = body.daily_energy_wh
    store.save(config)
    _replan(request)
    return device_with_relations(config, device)


@router.put("/config/devices/{device_id}/role_overrides")
async def set_role_overrides(
    request: Request, device_id: str, overrides: dict[str, RoleOverride] = Body(...)
) -> dict[str, Any]:
    """Replace the role overrides of a configured device."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if (device := config.device(device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    device.role_overrides = overrides
    store.save(config)
    return device.model_dump()


def device_with_relations(config: SunflowConfig, device: DeviceConfig) -> dict[str, Any]:
    """The device with what belongs to it (planning, feed-in, measurement link), for the UI."""
    feed_in, measured = config.feed_in.get(device.id), config.measured_by.get(device.id)
    return device.model_dump(mode="json") | {
        "flex": config.flex_of(device.id).model_dump(),
        "feed_in": feed_in.model_dump() if feed_in else None,
        "measured_by": measured.model_dump() if measured else None,
    }


class DeviceUpdate(BaseModel):
    """Partial update of a configured device; omitted fields stay unchanged."""

    name: str | None = None
    # NaN or Infinity would make config.json unreadable for the UI and break the plan
    parameters: dict[str, FiniteFloat | None] | None = None
    feed_in: FeedIn | None = None
    measured_by: MeasuredBy | None = None
    clear_measured_by: bool = False
    flex: FlexSettings | None = None


@router.patch("/config/devices/{device_id}")
async def update_device(
    request: Request, device_id: str, body: DeviceUpdate
) -> dict[str, Any]:
    """Update parameters (None removes a value), feed-in and measurement link."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if (device := config.device(device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    if body.name is not None and body.name.strip():
        device.name = body.name.strip()
    if body.parameters is not None:
        for name, value in body.parameters.items():
            if value is None:
                device.parameters.pop(name, None)
            else:
                device.parameters[name] = value
    # Not the device itself but what belongs to it: kept by device id
    if body.feed_in is not None:
        config.feed_in[device.id] = body.feed_in
    if body.flex is not None:
        config.planning[device.id] = body.flex
    if body.clear_measured_by:
        config.measured_by.pop(device.id, None)
    elif body.measured_by is not None:
        if config.device(body.measured_by.device_id) is None:
            raise HTTPException(status_code=422, detail="Measuring device not configured")
        config.measured_by[device.id] = body.measured_by
    store.save(config)
    _replan(request)
    return device_with_relations(config, device)


@router.get("/config/devices/{device_id}/parameters")
async def get_parameters(request: Request, device_id: str) -> list[dict[str, Any]]:
    """Return profile parameters with stored, derived and default values."""
    device = _configured_device(request, device_id)
    snapshot = await _snapshot(request)
    modules = request.app.state.store.load().battery_modules.get(device.id)
    return device_parameters(_library(request).profiles[device.profile], device, snapshot, modules)


@router.get("/config/devices/{device_id}/efficiency")
async def get_efficiency(request: Request, device_id: str, days: int = 10) -> dict[str, Any] | None:
    """Estimate the battery efficiency from the measured history (suggestion only)."""
    result = await _efficiency_estimate(request, _configured_device(request, device_id), days)
    return asdict(result) if result else None


async def _efficiency_estimate(request: Request, device: DeviceConfig, days: int = 10) -> EfficiencyEstimate | None:
    """From the battery energy counters if there are some, else from the power history."""
    profile = _library(request).profiles[device.profile]
    snapshot = await _snapshot(request)
    roles = {
        r.role: r
        for r in preview(
            profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()}
        )
    }
    power, soc = roles.get("battery_power"), roles.get("battery_soc")
    modules = request.app.state.store.load().battery_modules.get(device.id)
    params = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot, modules)}
    if soc is None or not soc.entity_id or not params.get("capacity_wh"):
        return None
    charge, discharge = roles.get("battery_charge_energy"), roles.get("battery_discharge_energy")
    if charge and discharge and charge.entity_id and discharge.entity_id:
        # The inverter's counters cover more days and miss no peaks
        try:
            result = await estimate_from_counters(
                request.app.state.ha, charge.entity_id, discharge.entity_id, soc.entity_id, params["capacity_wh"]
            )
        except HAError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        if result:
            return result
    if power is None or not power.entity_id:
        return None
    # The sensor's sign convention applies to its statistics as well
    sign = -1.0 if power.sign is not None and power.sign not in CANONICAL_SIGNS else 1.0
    try:
        return await estimate_from_history(
            request.app.state.ha,
            power.entity_id,
            soc.entity_id,
            params["capacity_wh"],
            min(max(days, 1), 10),
            sign,
        )
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


def _module_capacity(spec: dict[str, Any] | None, snapshot: Snapshot, ha_device_id: str) -> float | None:
    """Capacity of one battery module from the profile's derive key (e.g. full charge Ah x 51.2 V)."""
    if not spec or "derive" not in spec:
        return None
    values = []
    for entity in snapshot.entities:
        if entity.get("device_id") == ha_device_id and entity_has_key(entity, spec["derive"]["sum_key"]):
            with suppress(KeyError, TypeError, ValueError):
                values.append(float(snapshot.states[entity["entity_id"]]["state"]))
    return round(sum(values) * spec["derive"].get("factor", 1)) if values else None


@router.get("/config/batteries")
async def get_batteries(request: Request) -> list[dict[str, Any]]:
    """Each inverter's battery with its modules (sub-devices in HA or chosen in the setup)."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    names = {d["id"]: d.get("name_by_user") or d.get("name") or d["id"] for d in snapshot.devices}
    batteries = [d for d in config.devices if "battery" in profiles[d.profile]["capabilities"]]
    # Modules may move between inverters: the sub-devices of all of them are offered
    candidates = {d["id"]: d for d in snapshot.devices if d.get("via_device_id") in {b.ha_device_id for b in batteries}}
    result = []
    for device in batteries:
        profile = profiles[device.profile]
        roles = {r.role: r for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})}
        chosen = config.battery_modules.get(device.id)
        params = {p["name"]: p for p in device_parameters(profile, device, snapshot, chosen)}
        assigned = battery_module_ids(snapshot, device, chosen)
        spec = profile.get("parameters", {}).get("capacity_wh")
        result.append(
            {
                "device_id": device.id,
                "inverter": device.name,
                "auto": chosen is None,
                "soc_pct": roles["battery_soc"].value if "battery_soc" in roles else None,
                "power_w": roles["battery_power"].value if "battery_power" in roles else None,
                "capacity_wh": params.get("capacity_wh", {}).get("effective"),
                "modules": [
                    {
                        "ha_device_id": module_id,
                        "name": names.get(module_id, module_id),
                        "model": candidates[module_id].get("model"),
                        "capacity_wh": _module_capacity(spec, snapshot, module_id),
                        "assigned": module_id in assigned,
                        "native": candidates[module_id].get("via_device_id") == device.ha_device_id,
                    }
                    for module_id in sorted(candidates, key=lambda i: names.get(i, i))
                ],
            }
        )
    return result


class BatteryModules(BaseModel):
    """The modules of an inverter's battery; None: its sub-devices in HA."""

    modules: list[str] | None = None


@router.put("/config/devices/{device_id}/battery-modules")
async def set_battery_modules(request: Request, device_id: str, body: BatteryModules) -> list[dict[str, Any]]:
    """Assign battery modules to an inverter (the capacity follows from them)."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = config.device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    if body.modules is None:
        config.battery_modules.pop(device.id, None)
    else:
        taken = {m for owner, modules in config.battery_modules.items() if owner != device_id for m in modules}
        if taken & set(body.modules):
            raise HTTPException(status_code=422, detail="Ein Modul ist schon einem anderen Wechselrichter zugeordnet")
        config.battery_modules[device.id] = body.modules
    store.save(config)
    _replan(request)
    return await get_batteries(request)


@router.get("/config/categories")
async def get_categories(request: Request) -> dict[str, str]:
    """Category of every configured device (also switched off ones), for grouping in the UI."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    result = {}
    for device in [*config.devices, *config.disabled_devices]:
        if (profile := profiles.get(device.profile)) is None:
            # Profile no longer shipped: the UI groups it by itself
            continue
        result[device.id] = device_category(profile, device, snapshot)
    return result


class DeviceKind(BaseModel):
    """What a loadpoint supplies."""

    kind: Literal["vehicle", "heater"] | None = None


@router.put("/config/devices/{device_id}/kind")
async def set_device_kind(request: Request, device_id: str, body: DeviceKind) -> dict[str, Any]:
    """Wallbox (vehicle) or heat (heater) for an evcc loadpoint; None: from the unit of its SoC."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    device = config.device(device_id)
    if device is None or "loadpoint" not in _library(request).profiles[device.profile]["capabilities"]:
        raise HTTPException(status_code=404, detail="No loadpoint")
    device.kind = body.kind
    store.save(config)
    _replan(request)
    return {"device_id": device_id, "kind": body.kind}


class DeviceEnabled(BaseModel):
    """Switch a configured device on or off."""

    enabled: bool


@router.put("/config/devices/{device_id}/enabled")
async def set_device_enabled(request: Request, device_id: str, body: DeviceEnabled) -> dict[str, Any]:
    """Keep a device with its settings but leave it out of planning, measuring and control."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    active = config.device(device_id)
    disabled = next((d for d in config.disabled_devices if d.id == device_id), None)
    if active is None and disabled is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    if body.enabled and disabled is not None:
        config.disabled_devices.remove(disabled)
        config.devices.append(disabled)
    elif not body.enabled and active is not None:
        controller: PlanController | None = getattr(request.app.state, "controller", None)
        if controller is not None:
            # A device under control is handed back first (e.g. the wallbox released)
            await controller.hand_back_device(active)
        config.devices.remove(active)
        config.disabled_devices.append(active)
    store.save(config)
    _replan(request)
    return {"device_id": device_id, "enabled": body.enabled}


@router.delete("/config/devices/{device_id}")
async def remove_device(request: Request, device_id: str) -> None:
    """Remove a device from the configuration."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    if config.device(device_id) is None and all(d.id != device_id for d in config.disabled_devices):
        raise HTTPException(status_code=404, detail="Device not configured")
    config.devices = [d for d in config.devices if d.id != device_id]
    config.disabled_devices = [d for d in config.disabled_devices if d.id != device_id]
    # Nothing may keep referring to the removed device
    config.overrides.pop(device_id, None)
    config.priorities = [i for i in config.priorities if i != device_id]
    config.priority_conditions.pop(device_id, None)
    config.priority_conditions = {
        owner: kept
        for owner, conditions in config.priority_conditions.items()
        if (kept := [c for c in conditions if c.device_id != device_id])
    }
    config.measurement_sources = {q: d for q, d in config.measurement_sources.items() if d != device_id}
    config.forget_device(device_id)
    store.save(config)
    _replan(request)


@router.post("/config/devices/{device_id}/actions/{action}")
async def run_action(
    request: Request,
    device_id: str,
    action: str,
    params: dict[str, float] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Plan a profile action and show the service calls (never executed).

    Runtime parameters such as P (power in W) are passed in the body. Writing to a
    device happens only in the device test, which requires an explicit confirmation.
    """
    device = _configured_device(request, device_id)
    profile = _library(request).profiles[device.profile]
    snapshot = await _snapshot(request)
    try:
        resolved, calls = plan_action(
            profile,
            action,
            device.parameters,
            params,
            device_entities(snapshot, device.ha_device_id),
        )
    except ExecutorError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return asdict(ActionResult(action=action, params=resolved, dry_run=True, calls=calls))


@router.post("/config/devices/{device_id}/setup")
async def run_setup(request: Request, device_id: str) -> dict[str, Any]:
    """Plan the profile's setup steps and show the service calls (never executed)."""
    device = _configured_device(request, device_id)
    profile = _library(request).profiles[device.profile]
    snapshot = await _snapshot(request)
    try:
        calls = plan_setup(profile, device_entities(snapshot, device.ha_device_id))
    except ExecutorError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return asdict(ActionResult(action="setup", params={}, dry_run=True, calls=calls))
