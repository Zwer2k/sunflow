"""The device test (real writes to the battery) and restoring after it."""

import asyncio
from contextlib import suppress
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from sunflow.config.models import DeviceConfig
from sunflow.config.store import ConfigStore
from sunflow.devices.device_test import (
    DeviceTestState,
    ModeControl,
    RestoreMarker,
    run_device_test,
    state_response,
)
from sunflow.devices.discovery import fetch_snapshot
from sunflow.devices.executor import (
    ExecutorError,
    PlannedCall,
    execute,
    find_entity,
    plan_battery,
)
from sunflow.devices.profiles import battery_actions, controls_battery
from sunflow.devices.resolver import device_entities, preview
from sunflow.ha import HAClient

from .common import _configured_device, _guard, _library

router = APIRouter()


def _tests(request: Request) -> dict[str, tuple[DeviceTestState, asyncio.Task[None]]]:
    return request.app.state.device_tests


def _device_control(
    request: Request, device: DeviceConfig, profile: dict[str, Any], calls: list[PlannedCall]
) -> tuple[Any, Any, ModeControl | None]:
    """Read battery values, run profile actions and switch the control mode (real writes)."""
    client: HAClient = request.app.state.ha
    overrides = {r: o.model_dump() for r, o in device.role_overrides.items()}
    guard = _guard(request, profile)

    async def read() -> tuple[float | None, float | None]:
        snapshot = await fetch_snapshot(client, request.app.state.direct)
        roles = {r.role: r.value for r in preview(profile, snapshot, device.ha_device_id, overrides)}
        return roles.get("battery_power"), roles.get("battery_soc")

    async def apply_action(action: str, runtime: dict[str, float]) -> list[PlannedCall]:
        snapshot = await fetch_snapshot(client, request.app.state.direct)
        planned = plan_battery(profile, action, device.parameters, runtime, device_entities(snapshot, device.ha_device_id))
        direct = request.app.state.direct.by_ha_device(device.ha_device_id)
        await execute(client, planned, snapshot.states, guard, dry_run=False, direct=direct)
        return planned

    if not (control_mode := profile.get("control_mode")):
        return read, apply_action, None

    async def set_mode(option: str) -> str | None:
        """Select option if needed; return the previous option when switched."""
        snapshot = await fetch_snapshot(client, request.app.state.direct)
        entities = device_entities(snapshot, device.ha_device_id)
        if (entity_id := find_entity(entities, "select", control_mode["key"])) is None:
            raise ExecutorError(f"No select entity for key {control_mode['key']}")
        current = snapshot.states.get(entity_id, {}).get("state")
        if current == option:
            return None
        call = PlannedCall(
            domain="select",
            service="select_option",
            data={"option": option},
            entity_id=entity_id,
            storage="eeprom",
            write="always",
        )
        await execute(client, [call], snapshot.states, guard, dry_run=False)
        calls.append(call)
        if call.status != "done":
            raise ExecutorError(f"Mode switch {call.status}: {call.reason}")
        return current

    async def ensure_mode() -> str | None:
        return await set_mode(control_mode["option"])

    async def restore_mode(option: str) -> None:
        await set_mode(option)

    return read, apply_action, ModeControl(ensure=ensure_mode, set=restore_mode)


@router.get("/device-test/pending-restore")
async def get_pending_restore(request: Request) -> dict[str, Any] | None:
    """A device test that wrote to the device and was not undone (e.g. app crash, lost connection)."""
    marker = request.app.state.restore_marker.get()
    if marker is None:
        return None
    device = request.app.state.store.load().device(marker.get("device_id") or "")
    return marker | {"device_name": device.name if device else None}


@router.post("/device-test/restore")
async def restore_after_test(request: Request, confirm: bool = False) -> dict[str, Any]:
    """Return the device of an interrupted test to self-consumption and its previous mode."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Restoring writes to the device; pass confirm=true")
    marker_store: RestoreMarker = request.app.state.restore_marker
    if (marker := marker_store.get()) is None:
        return {"restored": True, "calls": []}
    config = request.app.state.store.load()
    device = config.device(marker.get("device_id") or "") or next(
        (d for d in config.devices if controls_battery(_library(request).profiles[d.profile])), None
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Kein steuerbarer Speicher konfiguriert")
    if (running := _tests(request).get(device.id)) and running[0].running:
        raise HTTPException(status_code=409, detail="Der Gerätetest läuft noch und setzt selbst zurück")
    profile = _library(request).profiles[device.profile]
    calls: list[PlannedCall] = []
    _, apply_action, mode = _device_control(request, device, profile, calls)
    try:
        calls += await apply_action("auto", {})
        if mode is not None and marker.get("previous_mode"):
            await mode.set(marker["previous_mode"])
    except ExecutorError as err:
        raise HTTPException(status_code=502, detail=f"Zurücksetzen fehlgeschlagen: {err}") from err
    restored = all(c.status in ("done", "skipped") for c in calls)
    if restored:
        marker_store.set(None)
    return {"restored": restored, "calls": [asdict(c) for c in calls]}


@router.post("/config/devices/{device_id}/test")
async def start_device_test(
    request: Request, device_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Start the device test. Writes to the device, so confirm=true is required."""
    if not confirm:
        raise HTTPException(status_code=400, detail="The device test writes to the device; pass confirm=true")
    device = _configured_device(request, device_id)
    profile = _library(request).profiles[device.profile]
    if not controls_battery(profile):
        raise HTTPException(status_code=422, detail="Device has no battery control")
    if (running := _tests(request).get(device_id)) and running[0].running:
        raise HTTPException(status_code=409, detail="Test already running")

    state = DeviceTestState(device_id=device_id)
    read, apply_action, mode = _device_control(request, device, profile, state.calls)
    marker: RestoreMarker = request.app.state.restore_marker
    store: ConfigStore = request.app.state.store

    async def run() -> None:
        await run_device_test(state, read, apply_action, mode=mode, mark=marker.set, supported=battery_actions(profile))
        if state.phase == "done" and state.control_works and state.sign_correct and state.restored:
            config = store.load()
            if (stored := config.device(device_id)) is not None:
                stored.test_passed_at = state.finished
                store.save(config)

    _tests(request)[device_id] = (state, asyncio.create_task(run()))
    return state_response(state)


@router.get("/config/devices/{device_id}/test")
async def get_device_test(request: Request, device_id: str) -> dict[str, Any]:
    """Return progress and result of the last device test."""
    if (entry := _tests(request).get(device_id)) is None:
        raise HTTPException(status_code=404, detail="No test run")
    return state_response(entry[0])


@router.post("/config/devices/{device_id}/test/abort")
async def abort_device_test(request: Request, device_id: str) -> dict[str, Any]:
    """Abort a running test; the device is restored to self-consumption."""
    if (entry := _tests(request).get(device_id)) is None:
        raise HTTPException(status_code=404, detail="No test run")
    state, task = entry
    if state.running:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return state_response(state)
