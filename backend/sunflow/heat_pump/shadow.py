"""Shadow operation of the flow temperature control (design/heat_pump.md, 8.1, 8.2, 16 step 2c part 2b).

Without a usable heating curve the flow target is the base plus the room correction:
    V_Ziel = base + correction
The base is the effective flow setpoint of the heat pump, taken over only while it heats validly
(section 4); outside that the last such value counts. Without one there is no flow target ("no_base").
With a usable curve (step 3 part 2, feedforward.py) the flow target is the curve's flow for the heat
the house needs plus the same correction:
    V_Ziel = target + (Q*/K)^(1/n) + s(Q*)/2 + correction
The correction belongs to the strategy, not to the source: a change of the source leaves it as it is
(in shadow the flow target jumps once; noted in heat_pump_samples). The target room temperature is
the room setpoint (planning follows in step 4).

Shadow: everything is computed, stored and shown, nothing is written to a heat pump. As Sunflow does
not act, the correction has no effect back and may run to its limit; it is marked as shadow and starts
at 0 when Sunflow takes over (step 3), with the base frozen then.

Protection layer (8.3, 8.4, step 3 part 3, guard.py): from the flow target the set value Sunflow would
write and the writes it would cause (start, tracking, block, ...), still only computed; at a limit the
correction does not move further beyond it.

Ventilation (6.2): a room being aired does not count for the room temperature, and the correction is
frozen meanwhile. When rooms leave or join the room temperature (aired, stale, missing), the
correction takes the new value as reference instead of a step, so that alone causes no jump.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import math
from typing import TYPE_CHECKING, Any

from sunflow.config.models import DeviceConfig, SunflowConfig

from .correction import Correction, SetpointWatch, Settling
from .curve import Curve
from .defaults import (
    CORRECTION_KP,
    CORRECTION_LIMIT_K,
    CORRECTION_MAX_STEP_S,
    ROOM_DEADBAND_K,
    ROOM_SETPOINT_MAX_C,
    ROOM_SETPOINT_MIN_C,
    ROOM_SETPOINT_STEP_C,
    correction_ti_h,
)
from .feedforward import BASE, Feed, HouseContext, RecentHeat, feedforward, house_heat_w
from .guard import Guard, GuardInput, GuardResult
from .learned import Learned, values as guard_values
from .limits import ControlSettings
from .rooms import RoomTemperature, StateReader, reported_times, room_temperature
from .state_store import HeatPumpStateStore
from .states import UNKNOWN
from .ventilation import VentilationWatch

if TYPE_CHECKING:
    from .samples import Minute

_LOGGER = logging.getLogger(__name__)

# last_reported of the room sensors is read again at least this often (and when a room changed)
REPORTED_EVERY = timedelta(seconds=CORRECTION_MAX_STEP_S)


def _time(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _number(text: Any) -> float | None:
    """A state as a number (sun sensor); None when unavailable or not a number."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass(kw_only=True)
class FlowState:
    """What the shadow control computed for one heat pump."""

    strategy: str
    base_c: float | None
    base_at: datetime | None
    correction_k: float | None
    target_c: float | None
    frozen: tuple[str, ...]
    reason: str | None
    feed: Feed | None = None
    recent_s: float = 0.0
    # Protection layer (8.3, 8.4): set value and writes Sunflow would cause, limits reached
    guard: GuardResult | None = None
    limited: tuple[str, ...] = ()


class FlowShadow:
    """Room correction and flow target of every heat pump, computed each minute, never written."""

    def __init__(self, store: HeatPumpStateStore, client: StateReader | None = None) -> None:
        """Start from the stored state; client reads last_reported of the room sensors."""
        self._store = store
        self._client = client
        house = store.house
        self.watch = SetpointWatch(_float(house.get("setpoint_c")), Settling.from_dict(house.get("settling")))
        self._corrections: dict[tuple[str, str], Correction] = {}
        # Strategy each heat pump was computed in last (a change starts the new one without a jump)
        self._active: dict[str, str] = {}
        self._reported: dict[str, datetime | None] = {}
        self._reported_at: datetime | None = None
        self._reported_key: tuple[Any, ...] | None = None
        self.ventilation = VentilationWatch(house.get("ventilation"))
        # Rooms being aired at the last observation
        self.aired: tuple[str, ...] = ()
        self.room: RoomTemperature | None = None
        self.flows: dict[str, FlowState] = {}
        self._config: SunflowConfig | None = None
        self._observed_at: datetime | None = None
        # Learned heating curves per heat pump (from the cache until learned anew) and the house model
        self.curves: dict[str, Curve] = {}
        for device_id, stored in store.devices.items():
            if (curve := Curve.from_dict(stored.get("curve"))) is not None:
                self.curves[device_id] = curve
        self.house: HouseContext | None = None
        # Protection layer per heat pump and its learned values (from the cache until learned anew)
        self.guards: dict[str, Guard] = {}
        self.learned: dict[str, Learned] = {}
        self.settings: dict[str, ControlSettings] = {}
        for device_id, stored in store.devices.items():
            if (learned := Learned.from_dict(stored.get("learned"))) is not None:
                self.learned[device_id] = learned
        # Where the house model learns from: heat_pump_samples once chosen (kept, no jump back)
        self.house_source: str | None = house.get("model_source") if isinstance(house.get("model_source"), str) else None
        self._recent: dict[str, RecentHeat] = {}
        # Source changes, planned writes, starts and unwanted shutdowns of this tick: (device id, event)
        # for heat_pump_samples; journal entries (device id, message, warn)
        self.source_events: list[tuple[str, dict[str, Any]]] = []
        self.notes: list[tuple[str, str, bool]] = []
        # The rooms the room temperature is made of changed with the last observation
        self._rooms_changed = False

    def set_curve(self, device_id: str, curve: Curve) -> None:
        """A newly learned curve; kept as cache with the next save."""
        self.curves[device_id] = curve
        self._store.devices.setdefault(device_id, {})["curve"] = curve.as_dict()

    def set_learned(self, device_id: str, learned: Learned) -> None:
        """Newly learned swing, jumps and heat output range; kept as cache with the next save."""
        self.learned[device_id] = learned
        self._store.devices.setdefault(device_id, {})["learned"] = learned.as_dict()

    def guard(self, device_id: str) -> Guard:
        """The protection layer of one heat pump (from the stored state)."""
        if device_id not in self.guards:
            self.guards[device_id] = Guard((self._store.devices.get(device_id) or {}).get("guard"))
        return self.guards[device_id]

    def recent(self, device_id: str) -> RecentHeat:
        """Heat of the last hours of one heat pump (source heat_recent)."""
        return self._recent.setdefault(device_id, RecentHeat())

    def _feed(self, config: SunflowConfig, device_id: str, minute: "Minute", states: dict[str, dict[str, Any]], now: datetime) -> tuple[Feed, float]:
        target = config.house.climate.setpoint_c
        sun = None
        if self.house is not None and self.house.sun_entity:
            sun = _number((states.get(self.house.sun_entity) or {}).get("state"))
        house_w = house_heat_w(self.house, target, minute.outside_c, sun)
        recent_w, seconds = self.recent(device_id).mean(now)
        return feedforward(self.curves.get(device_id), target, house_w, recent_w, config.house.heating_surface), seconds

    def _correction(self, device_id: str, strategy: str) -> Correction:
        key = (device_id, strategy)
        if key not in self._corrections:
            stored = ((self._store.devices.get(device_id) or {}).get("corrections") or {}).get(strategy)
            self._corrections[key] = Correction(_float(stored) or 0.0)
        return self._corrections[key]

    async def _reported_times(self, config: SunflowConfig, states: dict[str, dict[str, Any]], now: datetime) -> dict[str, datetime | None]:
        """last_reported per room sensor, read again when a room changed or REPORTED_EVERY passed."""
        ids = [room.entity_id for room in config.house.rooms]
        key = tuple((e, (states.get(e) or {}).get("state")) for e in ids)
        if self._client is not None and (
            key != self._reported_key or self._reported_at is None or now - self._reported_at >= REPORTED_EVERY
        ):
            self._reported = await reported_times(self._client, ids)
            self._reported_at, self._reported_key = now, key
        result: dict[str, datetime | None] = {}
        for entity_id in ids:
            fetched = self._reported.get(entity_id)
            # A change seen since is a report as well; without last_reported there is no staleness check
            updated = _time((states.get(entity_id) or {}).get("last_updated"))
            result[entity_id] = max(fetched, updated) if fetched is not None and updated is not None else fetched
        return result

    async def observe(self, config: SunflowConfig, states: dict[str, dict[str, Any]], now: datetime) -> tuple[str, ...]:
        """Ventilation and room temperature of this minute (once per minute); the rooms being aired."""
        if self._observed_at == now:
            return self.aired
        self._config = config
        reported = await self._reported_times(config, states, now)
        self.aired = self.ventilation.update((room.entity_id for room in config.house.rooms), states, now)
        before = self.room
        self.room = room_temperature(config.house.rooms, states, reported, now, self.aired)
        self._rooms_changed = before is not None and before.used != self.room.used
        self._observed_at = now
        return self.aired

    async def tick(
        self,
        config: SunflowConfig,
        states: dict[str, dict[str, Any]],
        minutes: dict[str, tuple[DeviceConfig, "Minute | None"]],
        now: datetime,
    ) -> None:
        """One step: room temperature, setpoint change, then base, correction and target per heat pump."""
        await self.observe(config, states, now)
        room = self.room
        assert room is not None
        setpoint = config.house.climate.setpoint_c
        settling = self.watch.update(setpoint, room.value_c, now)
        flows: dict[str, FlowState] = {}
        for device_id, (device, minute) in minutes.items():
            stored = self._store.devices.setdefault(device_id, {})
            base, base_at = _float(stored.get("base_c")), _time(stored.get("base_at"))
            if minute is None:
                # Nothing read (node away): what the room does meanwhile is dropped
                if (active := self._active.get(device_id)) is not None:
                    self._correction(device_id, active).hold(room.value_c, now)
                flows[device_id] = FlowState(
                    strategy=active or UNKNOWN, base_c=base, base_at=base_at,
                    correction_k=self._correction(device_id, active).value_k if active else None,
                    target_c=None, frozen=("no_sample",), reason="no_sample",
                )
                continue
            reading, evaluation = minute.reading, minute.evaluation
            self.recent(device_id).add(minute, bool(self.aired))
            feed, recent_s = self._feed(config, device_id, minute, states, now)
            previous = stored.get("source")
            if previous != feed.source:
                if isinstance(previous, str):
                    self.source_events.append(
                        (device_id, {"at": now.astimezone(UTC).isoformat(), "kind": "flow_source", "from": previous, "to": feed.source})
                    )
                    _LOGGER.info("%s: flow target now from %s (was %s)", device.name, feed.source, previous)
                stored["source"] = feed.source
            effective = reading.values.get("flow_setpoint")
            if evaluation.valid and effective is not None:
                base, base_at = effective, now
                stored["base_c"], stored["base_at"] = base, now.isoformat()
            frozen = list(evaluation.frozen)
            if self.aired:
                frozen.append("ventilation")
            elif room.value_c is None:
                frozen.append("rooms_missing")
            if settling is not None:
                frozen.append("setpoint_change")
            strategy = reading.strategy
            if strategy == UNKNOWN:
                frozen.append("strategy_unknown")
                correction_k = None
            else:
                correction = self._correction(device_id, strategy)
                guard = self.guard(device_id)
                # Limits reached in the previous minute (8.3): the correction does not move beyond them
                last = guard.last
                if self._active.get(device_id) != strategy:
                    correction.hold(room.value_c, now)
                    self._active[device_id] = strategy
                if frozen or room.value_c is None:
                    correction.hold(room.value_c, now)
                else:
                    if self._rooms_changed:
                        correction.rebase(room.value_c)
                    correction.step(
                        room.value_c, setpoint, now,
                        block_up=last is not None and last.freeze_up, block_down=last is not None and last.freeze_down,
                        ti_h=correction_ti_h(config.house.heating_surface),
                    )
                correction_k = correction.value_k
                stored.setdefault("corrections", {})[strategy] = round(correction.value_k, 4)
            start = feed.curve_c if feed.source != BASE else base
            target = start + correction_k if start is not None and correction_k is not None else None
            reason = "no_base" if start is None else "strategy_unknown" if correction_k is None else None
            result = self._guard_step(config, device_id, device, minute, target, feed, room.value_c, now)
            stored["guard"] = self.guard(device_id).as_dict()
            flows[device_id] = FlowState(
                strategy=strategy, base_c=base, base_at=base_at, correction_k=correction_k, target_c=target,
                frozen=tuple(sorted(set(frozen))), reason=reason, feed=feed, recent_s=recent_s,
                guard=result, limited=result.limited if result else (),
            )
        self.flows = flows
        for gone in set(self._store.devices) - set(minutes):
            del self._store.devices[gone]
            self.curves.pop(gone, None)
            self._recent.pop(gone, None)
            self.guards.pop(gone, None)
            self.learned.pop(gone, None)
            self.settings.pop(gone, None)
        self._store.house = {
            "setpoint_c": self.watch.last_c,
            "settling": self.watch.settling.as_dict() if self.watch.settling else None,
            "ventilation": self.ventilation.as_dict(),
            "model_source": self.house_source,
        }
        self._store.save()

    def _guard_step(
        self, config: SunflowConfig, device_id: str, device: DeviceConfig, minute: "Minute",
        target: float | None, feed: Feed, room_c: float | None, now: datetime,
    ) -> GuardResult | None:
        """The protection layer for this minute: set value and writes Sunflow would cause (shadow)."""
        guard = self.guard(device_id)
        settings = minute.control or ControlSettings()
        self.settings[device_id] = settings
        try:
            result = guard.step(GuardInput(
                at=now, reading=minute.reading, evaluation=minute.evaluation, settings=settings,
                values=guard_values(settings, self.learned.get(device_id), guard.bumps, now),
                tv_peak_c=minute.tv_peak_c, heat_w=minute.heat_w, target_c=target,
                curve_c=feed.curve_c if feed.source != BASE else None, heat_needed_w=feed.heat_w,
                room_c=room_c, plan_c=config.house.climate.setpoint_c, mode=config.house.climate.mode,
                surface=config.house.heating_surface,
            ))
        except Exception:
            # The flow target goes on without it
            _LOGGER.exception("%s: protection layer failed", device.name)
            return None
        for event in (*result.events, *result.writes):
            self.source_events.append((device_id, event))
        for message, warn in result.notes:
            self.notes.append((device_id, message, warn))
        return result

    # Status

    def house_status(self, config: SunflowConfig | None = None) -> dict[str, Any]:
        """Room setpoint, mode and room temperature for the climate entity and the UI."""
        return climate_status(config or self._config, self.room, self.watch.settling, self.ventilation)

    def flow_status(self, device_id: str) -> dict[str, Any] | None:
        """Base, correction and flow target of one heat pump (shadow: nothing is written)."""
        flow = self.flows.get(device_id)
        if flow is None:
            return None
        surface = self._config.house.heating_surface if self._config else None
        return {
            "shadow": True,
            "strategy": flow.strategy,
            "base_c": flow.base_c,
            "base_at": flow.base_at.isoformat() if flow.base_at else None,
            "correction_k": round(flow.correction_k, 3) if flow.correction_k is not None else None,
            "target_c": round(flow.target_c, 2) if flow.target_c is not None else None,
            "frozen": list(flow.frozen),
            "reason": flow.reason,
            # Feedforward (8.1): source of the flow target (base, house_model, heat_recent), why the base,
            # heat needed and the curve's flow for it; the learned curve (shadow)
            "source": flow.feed.source if flow.feed else BASE,
            "source_reason": flow.feed.reason if flow.feed else None,
            "heat_needed_w": round(flow.feed.heat_w) if flow.feed and flow.feed.heat_w is not None else None,
            "curve_c": round(flow.feed.curve_c, 2) if flow.feed and flow.feed.curve_c is not None else None,
            "heat_recent_s": round(flow.recent_s),
            "curve": curve.status() if (curve := self.curves.get(device_id)) is not None else None,
            "surface": surface,
            "parameters": {
                "kp": CORRECTION_KP, "ti_h": correction_ti_h(surface), "deadband_k": ROOM_DEADBAND_K, "limit_k": CORRECTION_LIMIT_K,
            },
            # Limits the correction does not move beyond (8.3); the direction in guard.freeze_up/down
            "limited": list(flow.limited),
            "guard": self.guard_status(device_id),
        }

    def guard_status(self, device_id: str) -> dict[str, Any] | None:
        """Set value and writes Sunflow would cause (8.3, 8.4, shadow), learned values, unwanted shutdowns."""
        flow = self.flows.get(device_id)
        guard = self.guards.get(device_id)
        result = flow.guard if flow else None
        if guard is None or result is None:
            return None
        settings = self.settings.get(device_id) or ControlSettings()
        learned = self.learned.get(device_id)
        now = self._observed_at or datetime.now(UTC)
        return {
            "shadow": True,
            "phase": result.phase,
            "inactive": list(result.inactive),
            "setpoint_c": result.setpoint_c,
            "desired_c": result.desired_c,
            "floor_c": result.floor_c,
            "min_c": result.limits[0] if result.limits else None,
            "max_c": result.limits[1] if result.limits else None,
            "limited": list(result.limited),
            "freeze_up": result.freeze_up,
            "freeze_down": result.freeze_down,
            "implausible_k": result.implausible_k,
            "calm": result.calm,
            "block": guard.block,
            "block_since": guard.block_at.isoformat() if guard.block_at else None,
            "block_reason": guard.block_reason,
            "start": guard.start.as_dict() if guard.start else None,
            "written_at": guard.written_at.isoformat() if guard.written_at else None,
            "writes": list(reversed(guard.recent_writes)),
            "unwanted_stops": list(reversed(guard.recent_stops)),
            "values": guard_values(settings, learned, guard.bumps, now).as_dict(),
            "learned": learned.as_dict() if learned else None,
            "parameters": {
                "start_margin_k": settings.start_margin, "stop_margin_k": settings.stop_margin,
                "deadband_k": settings.deadband, "step_k": settings.step, "block_min_s": settings.block_min_s,
            },
        }


def climate_status(
    config: SunflowConfig | None,
    room: RoomTemperature | None,
    settling: Settling | None,
    ventilation: VentilationWatch | None = None,
) -> dict[str, Any]:
    """Room setpoint, its range, mode, room temperature, a setpoint change being followed, ventilation."""
    climate = config.house.climate if config is not None else None
    return {
        "setpoint_c": climate.setpoint_c if climate else None,
        "mode": climate.mode if climate else None,
        "min_c": ROOM_SETPOINT_MIN_C,
        "max_c": ROOM_SETPOINT_MAX_C,
        "step_c": ROOM_SETPOINT_STEP_C,
        "room_c": round(room.value_c, 2) if room and room.value_c is not None else None,
        "rooms_used": list(room.used) if room else [],
        "rooms_missing": list(room.missing) if room else [],
        "rooms_stale": list(room.stale) if room else [],
        "rooms_ventilated": list(room.ventilated) if room else [],
        # Ventilation (6.2): rooms being aired and events that ended within the last 24 h, newest first
        "ventilation": [e.as_dict() for e in ventilation.active.values()] if ventilation else [],
        "ventilation_recent": [e.as_dict() for e in reversed(ventilation.recent)] if ventilation else [],
        # Planning follows in step 4: the plan target is the setpoint
        "plan_target_c": climate.setpoint_c if climate else None,
        "settling": settling.as_dict() if settling else None,
        # Nothing is written to a heat pump yet
        "shadow": True,
    }
