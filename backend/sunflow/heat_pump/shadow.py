"""Shadow operation of the flow temperature control (design/heat_pump.md, 8.1, 8.2, 16 step 2c part 2b).

Without a heating curve the flow target is the base plus the room correction:
    V_Ziel = base + correction
The base is the effective flow setpoint of the heat pump, taken over only while it heats validly
(section 4); outside that the last such value counts. Without one there is no flow target ("no_base").
The target room temperature is the room setpoint (planning follows in step 4).

Shadow: everything is computed, stored and shown, nothing is written to a heat pump. As Sunflow does
not act, the correction has no effect back and may run to its limit; it is marked as shadow and starts
at 0 when Sunflow takes over (step 3), with the base frozen then.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from sunflow.config.models import DeviceConfig, SunflowConfig

from .correction import Correction, SetpointWatch, Settling
from .defaults import (
    CORRECTION_KP,
    CORRECTION_LIMIT_K,
    CORRECTION_MAX_STEP_S,
    CORRECTION_TI_H,
    ROOM_DEADBAND_K,
    ROOM_SETPOINT_MAX_C,
    ROOM_SETPOINT_MIN_C,
    ROOM_SETPOINT_STEP_C,
)
from .rooms import RoomTemperature, StateReader, reported_times, room_temperature
from .state_store import HeatPumpStateStore
from .states import UNKNOWN

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
        self.room: RoomTemperature | None = None
        self.flows: dict[str, FlowState] = {}
        self._config: SunflowConfig | None = None

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

    async def tick(
        self,
        config: SunflowConfig,
        states: dict[str, dict[str, Any]],
        minutes: dict[str, tuple[DeviceConfig, "Minute | None"]],
        now: datetime,
    ) -> None:
        """One step: room temperature, setpoint change, then base, correction and target per heat pump."""
        self._config = config
        reported = await self._reported_times(config, states, now)
        self.room = room = room_temperature(config.house.rooms, states, reported, now)
        setpoint = config.house.climate.setpoint_c
        settling = self.watch.update(setpoint, room.value_c, now)
        flows: dict[str, FlowState] = {}
        for device_id, (_device, minute) in minutes.items():
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
            effective = reading.values.get("flow_setpoint")
            if evaluation.valid and effective is not None:
                base, base_at = effective, now
                stored["base_c"], stored["base_at"] = base, now.isoformat()
            frozen = list(evaluation.frozen)
            if room.value_c is None:
                frozen.append("rooms_missing")
            if settling is not None:
                frozen.append("setpoint_change")
            strategy = reading.strategy
            if strategy == UNKNOWN:
                frozen.append("strategy_unknown")
                correction_k = None
            else:
                correction = self._correction(device_id, strategy)
                if self._active.get(device_id) != strategy:
                    correction.hold(room.value_c, now)
                    self._active[device_id] = strategy
                if frozen or room.value_c is None:
                    correction.hold(room.value_c, now)
                else:
                    correction.step(room.value_c, setpoint, now)
                correction_k = correction.value_k
                stored.setdefault("corrections", {})[strategy] = round(correction.value_k, 4)
            target = base + correction_k if base is not None and correction_k is not None else None
            reason = "no_base" if base is None else "strategy_unknown" if correction_k is None else None
            flows[device_id] = FlowState(
                strategy=strategy, base_c=base, base_at=base_at, correction_k=correction_k, target_c=target,
                frozen=tuple(sorted(set(frozen))), reason=reason,
            )
        self.flows = flows
        for gone in set(self._store.devices) - set(minutes):
            del self._store.devices[gone]
        self._store.house = {
            "setpoint_c": self.watch.last_c,
            "settling": self.watch.settling.as_dict() if self.watch.settling else None,
        }
        self._store.save()

    # Status

    def house_status(self, config: SunflowConfig | None = None) -> dict[str, Any]:
        """Room setpoint, mode and room temperature for the climate entity and the UI."""
        return climate_status(config or self._config, self.room, self.watch.settling)

    def flow_status(self, device_id: str) -> dict[str, Any] | None:
        """Base, correction and flow target of one heat pump (shadow: nothing is written)."""
        flow = self.flows.get(device_id)
        if flow is None:
            return None
        return {
            "shadow": True,
            "strategy": flow.strategy,
            "base_c": flow.base_c,
            "base_at": flow.base_at.isoformat() if flow.base_at else None,
            "correction_k": round(flow.correction_k, 3) if flow.correction_k is not None else None,
            "target_c": round(flow.target_c, 2) if flow.target_c is not None else None,
            "frozen": list(flow.frozen),
            "reason": flow.reason,
            "parameters": {
                "kp": CORRECTION_KP, "ti_h": CORRECTION_TI_H, "deadband_k": ROOM_DEADBAND_K, "limit_k": CORRECTION_LIMIT_K,
            },
        }


def climate_status(config: SunflowConfig | None, room: RoomTemperature | None, settling: Settling | None) -> dict[str, Any]:
    """Room setpoint, its range, mode, room temperature and a setpoint change being followed."""
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
        # Planning follows in step 4: the plan target is the setpoint
        "plan_target_c": climate.setpoint_c if climate else None,
        "settling": settling.as_dict() if settling else None,
        # Nothing is written to a heat pump yet
        "shadow": True,
    }
