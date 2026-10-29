"""Carry out the plan on the devices (only when the user switched control on).

Every minute the action of the current plan slot is translated into the device's
primitive (e.g. the Sofar's passive mode window). Values are written only when the
device shows something else, so a rebooted inverter (window reset to 0/0/0) gets
them again within a minute. Without a current plan the battery falls back to
self-consumption ("auto").

Loadpoints (wallbox, heating rod) charge in the slots the plan gives them energy
and are stopped otherwise; without a current plan they are released to their own
logic (e.g. the go-e charges when plugged in, evcc follows its previous mode).

Switching control off (main switch or per device) restores self-consumption and
the operating mode the device had before Sunflow took over. That previous mode is
kept in a file, so it survives a restart of the app.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
import math
from pathlib import Path
from typing import Any

from .config_store import DeviceConfig, SunflowConfig
from .discovery import Snapshot, fetch_snapshot
from .executor import (
    EepromGuard,
    ExecutorError,
    PlannedCall,
    current_matches,
    execute,
    find_entity,
    plan_action,
    plan_charge,
)
from .ha import HAClient
from .resolver import device_entities

_LOGGER = logging.getLogger(__name__)

# An older plan is not followed any more; the battery returns to self-consumption
MAX_PLAN_AGE = timedelta(minutes=30)
# Planned consumer power from which a loadpoint is switched on
MIN_CHARGE_W = 100.0
# Share of the maximum power below which a loadpoint without current setpoint follows the surplus
FOLLOW_BELOW = 0.9
VOLTS = 230.0
# evcc states that mean evcc itself controls the home battery
EVCC_BATTERY_CONTROL = {"hold", "charge", "holdcharge"}


@dataclass(kw_only=True)
class DeviceControl:
    """What happened on one device, for the UI."""

    device_id: str
    name: str
    active: bool = False
    action: str | None = None
    power_w: float | None = None
    reason: str | None = None
    last_write: datetime | None = None
    last_check: datetime | None = None
    error: str | None = None
    note: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)


def desired_action(plan: Any, plan_at: datetime | None, now: datetime) -> tuple[str, float | None, str]:
    """The action of the slot running now, or self-consumption with the reason why not."""
    if plan is None or plan_at is None or not plan.plan.slots:
        return "auto", None, "Kein Plan – Eigenverbrauch"
    if now - plan_at > MAX_PLAN_AGE:
        return "auto", None, f"Plan älter als {int(MAX_PLAN_AGE.total_seconds() // 60)} min – Eigenverbrauch"
    for slot in plan.plan.slots:
        if datetime.fromisoformat(str(slot.start)) <= now < datetime.fromisoformat(str(slot.end)):
            return slot.action, slot.action_power_w, "nach Plan"
    return "auto", None, "Plan deckt die aktuelle Zeit nicht ab – Eigenverbrauch"


def desired_charge(
    plan: Any, plan_at: datetime | None, now: datetime, name: str
) -> tuple[str, float | None, str]:
    """charge (with power), stop, or release when there is no current plan."""
    _, _, reason = desired_action(plan, plan_at, now)
    if reason != "nach Plan":
        return "release", None, reason.replace("Eigenverbrauch", "Gerät regelt selbst")
    slot = next(s for s in plan.plan.slots if datetime.fromisoformat(str(s.start)) <= now < datetime.fromisoformat(str(s.end)))
    power = (getattr(slot, "flex_w", None) or {}).get(name, 0.0)
    if power >= MIN_CHARGE_W:
        return "charge", power, "nach Plan"
    return "stop", None, "nach Plan: jetzt nicht"


def charge_current(power: float, params: dict[str, float]) -> float:
    """Whole amperes for a power on the configured phases, within the allowed range."""
    phases = params.get("phases") or 3
    amps = math.ceil(power / (VOLTS * phases))
    return float(min(max(amps, params.get("min_current_a") or 6), params.get("max_current_a") or 16))


def evcc_controls_battery(snapshot: Snapshot) -> bool:
    """True while evcc holds or charges the home battery itself (two controllers would fight)."""
    for entity_id, state in snapshot.states.items():
        value = str(state.get("state", "")).lower()
        if entity_id.startswith("sensor.evcc") and entity_id.endswith(("battery_mode", "battery_mode_value")):
            if value in EVCC_BATTERY_CONTROL:
                return True
        if entity_id.startswith("binary_sensor.evcc") and entity_id.endswith("battery_grid_charge_active") and value == "on":
            return True
    return False


class TakeoverMarker:
    """Devices under Sunflow's control and the mode each had before, in the data directory."""

    def __init__(self, data_dir: Path) -> None:
        """Initialize."""
        self._path = data_dir / "control_active.json"

    def get(self) -> dict[str, str | None]:
        """Device id -> previous mode option (None: the mode was already right)."""
        try:
            return json.loads(self._path.read_text())
        except FileNotFoundError, ValueError:
            return {}

    def set(self, device_id: str, previous_mode: str | None) -> None:
        """Remember a device Sunflow took over."""
        data = self.get()
        data[device_id] = previous_mode
        self._write(data)

    def clear(self, device_id: str) -> None:
        """Forget a device after it was handed back."""
        data = self.get()
        data.pop(device_id, None)
        self._write(data)

    def _write(self, data: dict[str, str | None]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self._path)


@dataclass(frozen=True, kw_only=True)
class DeviceAccess:
    """Writes to one device, provided by the API layer (Home Assistant calls)."""

    # Plan the calls of an action without writing: (calls, current states)
    plan: Callable[[str, dict[str, float]], Awaitable[tuple[list[PlannedCall], dict[str, dict[str, Any]]]]]
    run: Callable[[list[PlannedCall], dict[str, dict[str, Any]]], Awaitable[None]]
    # Switch into the control mode; returns the previous option to restore later
    ensure_mode: Callable[[], Awaitable[str | None]] | None = None
    set_mode: Callable[[str], Awaitable[None]] | None = None
    # Written when handing back ("auto" for batteries); None: only the mode is restored
    release: str | None = "auto"


async def apply(
    access: DeviceAccess,
    status: DeviceControl,
    action: str,
    power: float | None,
    now: datetime,
    runtime: dict[str, float] | None = None,
) -> None:
    """Write the action if the device shows other values; status is updated in place."""
    if runtime is None:
        runtime = {"P": abs(power)} if power is not None else {}
    calls, states = await access.plan(action, runtime)
    status.last_check = now
    # Numbers and selects that already hold the value need no write; a press commits them
    if all(current_matches(c, states) for c in calls if c.service != "press"):
        return
    await access.run(calls, states)
    status.calls = [{"entity_id": c.entity_id, "service": c.service, "data": c.data, "status": c.status} for c in calls]
    # The daily write limit of a flash value keeps the last value; the rest still applies
    failed = [c for c in calls if c.status not in ("done", "skipped", "blocked")]
    if failed:
        raise RuntimeError(f"{failed[0].entity_id}: {failed[0].status} ({failed[0].reason})")
    status.note = (
        f"{blocked[0].entity_id}: Schreiblimit für heute erreicht, bleibt beim letzten Wert"
        if (blocked := [c for c in calls if c.status == "blocked"])
        else None
    )
    status.last_write = now
    _LOGGER.info("%s: %s %s", status.name, action, f"{power:.0f} W" if power is not None else "")


async def take_over(access: DeviceAccess, marker: TakeoverMarker, device_id: str) -> None:
    """Switch into the control mode once and remember the previous one."""
    if device_id in marker.get():
        return
    previous = await access.ensure_mode() if access.ensure_mode else None
    marker.set(device_id, previous)


async def hand_back(access: DeviceAccess, marker: TakeoverMarker, device_id: str, status: DeviceControl) -> None:
    """Self-consumption and the previous mode again; the marker is cleared only on success."""
    taken = marker.get()
    if device_id not in taken:
        return
    if access.release is not None:
        await apply(access, status, access.release, None, datetime.now(UTC))
    if (previous := taken[device_id]) and access.set_mode:
        await access.set_mode(previous)
    marker.clear(device_id)
    status.active = False
    status.action = None
    status.reason = "An das Gerät zurückgegeben"


def device_access(client: HAClient, profile: dict[str, Any], device: DeviceConfig, guard: EepromGuard) -> DeviceAccess:
    """Home Assistant calls for one device; every write passes the executor and its EEPROM guard."""

    charging = profile.get("charge_control")

    async def plan(action: str, runtime: dict[str, float]) -> tuple[list[PlannedCall], dict[str, dict[str, Any]]]:
        snapshot = await fetch_snapshot(client)
        entities = device_entities(snapshot, device.ha_device_id)
        if charging:
            return plan_charge(profile, action, device.parameters, runtime, entities), snapshot.states
        _, calls = plan_action(profile, action, device.parameters, runtime, entities)
        return calls, snapshot.states

    async def run(calls: list[PlannedCall], states: dict[str, dict[str, Any]]) -> None:
        await execute(client, calls, states, guard, dry_run=False)

    if charging:
        release = "release" if "release" in charging else None
        if not (remember := charging.get("remember")):
            return DeviceAccess(plan=plan, run=run, release=release)

        async def remembered_entity() -> tuple[str | None, dict[str, dict[str, Any]]]:
            snapshot = await fetch_snapshot(client)
            return find_entity(device_entities(snapshot, device.ha_device_id), "select", remember["key"]), snapshot.states

        async def current_option() -> str | None:
            # Only remembered here; charge and stop switch it
            entity_id, states = await remembered_entity()
            return states.get(entity_id or "", {}).get("state")

        async def restore(option: str) -> None:
            entity_id, states = await remembered_entity()
            call = PlannedCall(
                domain="select", service="select_option", data={"option": option}, entity_id=entity_id,
                storage="volatile", write="if_differs",
            )
            await execute(client, [call], states, guard, dry_run=False)

        return DeviceAccess(plan=plan, run=run, ensure_mode=current_option, set_mode=restore, release=release)

    if not (control_mode := profile.get("control_mode")):
        return DeviceAccess(plan=plan, run=run)

    async def select(option: str) -> str | None:
        snapshot = await fetch_snapshot(client)
        entity_id = find_entity(device_entities(snapshot, device.ha_device_id), "select", control_mode["key"])
        if entity_id is None:
            raise ExecutorError(f"No select entity for key {control_mode['key']}")
        current = snapshot.states.get(entity_id, {}).get("state")
        if current == option:
            return None
        call = PlannedCall(
            domain="select", service="select_option", data={"option": option}, entity_id=entity_id, storage="eeprom", write="always"
        )
        await execute(client, [call], snapshot.states, guard, dry_run=False)
        if call.status != "done":
            raise ExecutorError(f"Mode switch {call.status}: {call.reason}")
        return current

    async def ensure_mode() -> str | None:
        return await select(control_mode["option"])

    async def set_mode(option: str) -> None:
        await select(option)

    return DeviceAccess(plan=plan, run=run, ensure_mode=ensure_mode, set_mode=set_mode)


class PlanController:
    """Applies the plan to the batteries and loadpoints the user switched control on for."""

    def __init__(
        self,
        marker: TakeoverMarker,
        access_for: Callable[[DeviceConfig], DeviceAccess],
        profiles: Callable[[], dict[str, dict[str, Any]]],
    ) -> None:
        """Initialize; access_for builds the Home Assistant calls of a device."""
        self._marker = marker
        self._access_for = access_for
        self._profiles = profiles
        self.statuses: dict[str, DeviceControl] = {}
        self.paused: str | None = None
        self._vehicles: dict[str, Any] = {}

    def controllable(self, device: DeviceConfig) -> bool:
        """A battery with a control primitive or a loadpoint with charge control."""
        profile = self._profiles()[device.profile]
        return "battery_window" in profile["capabilities"] or "charge_control" in profile

    def eligible(self, config: SunflowConfig, device: DeviceConfig) -> str | None:
        """Why a device cannot be controlled, None if it can."""
        profile = self._profiles()[device.profile]
        if "charge_control" in profile:
            return None
        if "battery_window" not in profile["capabilities"]:
            return "nicht steuerbar"
        if device.test_passed_at is None:
            return "Gerätetest noch nicht bestanden"
        return None

    def wanted(self, config: SunflowConfig, device: DeviceConfig) -> bool:
        """Control is on for this device and it passed the checks."""
        return config.control.enabled and config.control.devices.get(device.id, False) and self.eligible(config, device) is None

    async def tick(self, config: SunflowConfig, plan: Any, plan_at: datetime | None, snapshot: Snapshot, now: datetime) -> None:
        """One control step; a failure on one device does not stop the others."""
        devices = {d.id: d for d in config.devices}
        self._vehicles = {v.id: v for v in config.vehicles}
        # Devices taken over earlier but no longer wanted (switched off, removed, test reset)
        for device_id in list(self._marker.get()):
            device = devices.get(device_id)
            if device is None or not self.wanted(config, device):
                await self._safely(device_id, device, lambda d, s: hand_back(self._access_for(d), self._marker, d.id, s))
        self.paused = "evcc steuert gerade den Hausspeicher – Sunflow wartet" if evcc_controls_battery(snapshot) else None
        for device in config.devices:
            if not self.wanted(config, device):
                continue
            if "charge_control" in self._profiles()[device.profile]:
                await self._safely(device.id, device, lambda d, s: self._charge(d, s, plan, plan_at, now))
            elif self.paused:
                self._status(device).reason = self.paused
            else:
                await self._safely(device.id, device, lambda d, s: self._battery(d, s, plan, plan_at, now))

    async def _battery(self, device: DeviceConfig, status: DeviceControl, plan: Any, plan_at: datetime | None, now: datetime) -> None:
        action, power, reason = desired_action(plan, plan_at, now)
        access = self._access_for(device)
        await take_over(access, self._marker, device.id)
        await apply(access, status, action, power, now)
        status.active, status.action, status.power_w, status.reason = True, action, power, reason

    async def _charge(self, device: DeviceConfig, status: DeviceControl, plan: Any, plan_at: datetime | None, now: datetime) -> None:
        action, power, reason = desired_charge(plan, plan_at, now, device.name)
        info = next((f for f in getattr(plan, "flex", None) or [] if f.device_id == device.id), None)
        if info is not None and info.unknown_vehicle:
            action, power, reason = "release", None, info.reason
        access = self._access_for(device)
        await take_over(access, self._marker, device.id)
        if (
            action == "charge"
            and info is not None
            # Only a sure recognition: "the only vehicle" would also let a foreign car charge
            and info.vehicle_method in ("manual", "sensor")
            and device.vehicle_detection.authorize
            and "authorize" in self._profiles()[device.profile]["charge_control"]
            and (vehicle := self._vehicles.get(info.vehicle_id or "")) is not None
        ):
            # Recognized without its card: start the transaction as if the card had been scanned
            transaction = f"card_{vehicle.card - 1}" if vehicle.card else "without_card"
            await apply(access, status, "authorize", None, now, {"transaction": transaction})
        if action == "release" and access.release is None:
            # Nothing of its own to fall back to (evcc): its previous mode until a plan is there again
            if (previous := self._marker.get().get(device.id)) and access.set_mode:
                await access.set_mode(previous)
        else:
            profile = self._profiles()[device.profile]
            defaults = {n: spec["default"] for n, spec in profile.get("parameters", {}).items() if "default" in spec}
            params = defaults | device.parameters
            # Without a current setpoint the device charges at full power: below that it follows the surplus
            if action == "charge" and "follow" in profile["charge_control"] and power < FOLLOW_BELOW * (params.get("max_power") or 0):
                action = "follow"
            runtime = {"current_a": charge_current(power, params)} if power else {}
            await apply(access, status, action, power, now, runtime)
        status.active, status.action, status.power_w, status.reason = True, action, power, reason

    async def release_all(self, config: SunflowConfig) -> None:
        """App shutdown: batteries to self-consumption (mode kept), loadpoints back to their own logic."""
        for device in config.devices:
            if device.id not in self._marker.get():
                continue
            if "charge_control" in self._profiles()[device.profile]:
                # Without Sunflow nobody would start or stop charging
                await self._safely(device.id, device, lambda d, s: hand_back(self._access_for(d), self._marker, d.id, s))
            else:
                await self._safely(device.id, device, lambda d, s: apply(self._access_for(d), s, "auto", None, datetime.now(UTC)))

    def _status(self, device: DeviceConfig) -> DeviceControl:
        status = self.statuses.setdefault(device.id, DeviceControl(device_id=device.id, name=device.name))
        status.name = device.name
        return status

    async def _safely(
        self,
        device_id: str,
        device: DeviceConfig | None,
        step: Callable[[DeviceConfig, DeviceControl], Awaitable[None]],
    ) -> None:
        if device is None:
            # Removed from the configuration: nothing left to write to
            self._marker.clear(device_id)
            return
        status = self._status(device)
        try:
            await step(device, status)
            status.error = None
        except Exception as err:  # noqa: BLE001 - reported in the UI, retried next minute
            _LOGGER.warning("Battery control of %s failed: %s", device.name, err)
            status.error = str(err)
