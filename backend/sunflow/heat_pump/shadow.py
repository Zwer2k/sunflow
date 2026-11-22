"""Shadow operation of the flow temperature control (design/heat_pump.md, 8.1, 8.2, 16 step 2c part 2b).

Without a usable heating curve the flow target is the base plus the room correction:
    V_Ziel = base + correction
The base is the effective flow setpoint of the heat pump, taken over only while it heats validly
(section 4); outside that the last such value counts. Without one there is no flow target ("no_base").
With a usable curve (step 3 part 2, feedforward.py) the flow target is the curve's flow for the heat
the house needs plus the same correction:
    V_Ziel = target + (Q*/K)^(1/n) + s(Q*)/2 + correction
The correction belongs to the strategy, not to the source: in the shadow a change of the source leaves
it as it is and the flow target jumps once (noted in heat_pump_samples). While Sunflow writes for real
(step 3 part 4.5, switched on in part 4.7) the correction is shifted by old − new start value at a source
change, within its limit, so the flow target does not jump; a rest is noted in the journal. The target
room temperature is the room setpoint (planning follows in step 4).

Shadow: everything is computed, stored and shown, nothing is written to a heat pump. As Sunflow does
not act, the correction has no effect back and may run to its limit; it is marked as shadow and starts
at 0 when Sunflow takes over (step 3), with the base frozen then.

Protection layer (8.3, 8.4, step 3 part 3, guard.py): from the flow target the set value Sunflow would
write and the writes it would cause (start, tracking, block, ...), still only computed; at a limit the
correction does not move further beyond it.

Write layer (8.5, step 3 part 4.2, writer.py): when the wanted set value is written - leading edge,
minimum spacing, deadband except for protection steps, "write now". In the shadow it acts on the
virtual writes.
Budget (8.5, step 3 part 4.3, budget.py): per day in Home Assistant's time zone, counted virtually in the
shadow (seeded from the write events in heat_pump_samples), never in eeprom_writes.json; writing for real
the writes EepromGuard records.
Writing for real (step 3 part 4.7, active.py): only when the user switched it on for the heat pump
(DeviceConfig.flow_active, default off). Taking over (switched on in mode heat): the base is frozen, the
correction starts at 0, the protection layer starts from the real Max T-Vorlauf and block - nothing is
written until the rules ask for it. Then set value (EEPROM, budget) and block go to the heat pump; a write
that fails is taken back (journal), after adopt_delay Sunflow checks that the heat pump took the value
over. Switching off, mode off and failures reset nothing (8.6); switched off it is the shadow again.
Foreign writers (8.5, step 3 part 4.4, foreign.py): a change of the real Max T-Vorlauf Sunflow did not
write is noted (journal, status, setup summary) and the written value follows it; while the ESP regulation
is active nothing is written.

Ventilation (6.2): a room being aired does not count for the room temperature, and the correction is
frozen meanwhile. When rooms leave or join the room temperature (aired, stale, missing), the
correction takes the new value as reference instead of a step, so that alone causes no jump.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.devices.executor import EepromGuard, execute
from sunflow.i18n import number, t

from .active import MISSING, AdoptCheck, WriteTargets
from .budget import SETPOINT_REASONS, EepromLog, VirtualLog, WriteBudget
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
    SOURCE_JUMP_NOTE_K,
    SOURCE_SHIFT_MAX_AGE_S,
    correction_ti_h,
)
from .feedforward import BASE, HEAT_RECENT, HOUSE_MODEL, Feed, HouseContext, RecentHeat, feedforward, house_heat_w, source_shift
from .guard import BLOCK_OFF, BLOCK_ON, Guard, GuardInput, GuardResult
from .learned import Learned, values as guard_values
from .limits import ControlSettings
from .rooms import RoomTemperature, StateReader, device_sensors, reported_times, room_temperature
from .state_store import HeatPumpStateStore
from .states import UNKNOWN
from .ventilation import VentilationWatch
from .writer import NOT_ACTIVE, NOTHING_PENDING, Writer, write_step

if TYPE_CHECKING:
    from .samples import Minute

_LOGGER = logging.getLogger(__name__)

# last_reported of the room sensors is read again at least this often (and when a room changed)
REPORTED_EVERY = timedelta(seconds=CORRECTION_MAX_STEP_S)
# Home Assistant's time zone (local day of the write budget) is read again this often
TIMEZONE_EVERY = timedelta(hours=1)
# The start value of the old source counts for the jump protection only this long (8.1)
SOURCE_SHIFT_MAX_AGE = timedelta(seconds=SOURCE_SHIFT_MAX_AGE_S)
# Whether Sunflow writes to a heat pump for real or computes in the shadow (HA sensors, climate attribute mode)
SHADOW = "shadow"
ACTIVE = "active"
# Why nothing is written although Sunflow would (setpoint.held): besides the write layer's spacing and
# budget, the ESP regulation and the room thermostat block
HELD_ESP = "esp_regulation"
HELD_BLOCK = "block"


def source_text(source: str) -> str:
    """The feedforward source for the journal."""
    if source == HOUSE_MODEL:
        return t("Hausmodell")
    if source == HEAT_RECENT:
        return t("Wärme der letzten 3 h")
    if source == BASE:
        return t("Basis")
    return source


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

    def __init__(self, store: HeatPumpStateStore, client: StateReader | None = None, data_dir: Path | None = None) -> None:
        """Start from the stored state; client reads last_reported of the room sensors and writes to the heat
        pump once that is switched on; data_dir holds eeprom_writes.json (default: the state file's)."""
        self._store = store
        self._client = client
        self._data_dir = data_dir or store.data_dir
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
        # Write layer per heat pump (8.5) and the heat pumps Sunflow writes to for real: the user's switch
        # (DeviceConfig.flow_active, step 3 part 4.7), set every tick; off: shadow
        self.writers: dict[str, Writer] = {}
        self.active: dict[str, bool] = {}
        # Whether the heat pump took the last real write over (step 3 part 4.7), the last real write of the
        # set value (the self-limit time of the states counts from it)
        self.adopt_checks: dict[str, AdoptCheck] = {}
        self.real_written_at: dict[str, datetime] = {}
        # Real writes failing (device id, set value or block): noted once until one succeeds again
        self._failing: set[tuple[str, bool]] = set()
        # Write budget per heat pump (8.5), counted virtually in the shadow; the local day needs Home
        # Assistant's time zone (None until read: then the last 24 hours count)
        self.budgets: dict[str, WriteBudget] = {}
        self.tz: tzinfo | None = None
        self._tz_at: datetime | None = None
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

    def writer(self, device_id: str) -> Writer:
        """The write layer of one heat pump."""
        return self.writers.setdefault(device_id, Writer())

    def budget(self, device_id: str) -> WriteBudget:
        """The write budget of one heat pump (virtual in the shadow, EepromGuard while writing for real)."""
        return self.budgets.setdefault(device_id, WriteBudget())

    def adopt_check(self, device_id: str) -> AdoptCheck:
        """The check whether the heat pump took the last real write over."""
        return self.adopt_checks.setdefault(device_id, AdoptCheck())

    async def _timezone(self, now: datetime) -> None:
        """Home Assistant's time zone, read again every TIMEZONE_EVERY; the last one stays on a failure."""
        command = getattr(self._client, "command", None)
        if command is None or (self._tz_at is not None and timedelta(0) <= now - self._tz_at < TIMEZONE_EVERY):
            return
        self._tz_at = now
        try:
            self.tz = ZoneInfo((await command("get_config"))["time_zone"])
        except Exception:
            # The last one (or the last 24 hours) keeps counting
            _LOGGER.debug("Time zone of Home Assistant not read", exc_info=True)

    def writing_active(self, device_id: str) -> bool:
        """Sunflow writes to this heat pump for real: the user switched it on (step 3 part 4.7)."""
        return self.active.get(device_id, False)

    def request_write_now(self, device_id: str, now: datetime) -> tuple[bool, str | None, dict[str, Any] | None]:
        """"Write now": accepted (written with the next tick) or why not, and the value pending."""
        if not self.writing_active(device_id):
            return False, NOT_ACTIVE, None
        writer = self.writers.get(device_id)
        pending = writer.pending if writer is not None else None
        if pending is None:
            return False, NOTHING_PENDING, None
        writer.request_now(now)
        return True, None, pending.as_dict()

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
            self._reported = await reported_times(self._client, ids, await self._device_sensors(ids))
            self._reported_at, self._reported_key = now, key
        result: dict[str, datetime | None] = {}
        for entity_id in ids:
            fetched = self._reported.get(entity_id)
            # A change seen since is a report as well; without last_reported there is no staleness check
            updated = _time((states.get(entity_id) or {}).get("last_updated"))
            result[entity_id] = max(fetched, updated) if fetched is not None and updated is not None else fetched
        return result

    async def _device_sensors(self, ids: list[str]) -> dict[str, list[str]]:
        """The other sensors of each room sensor's device (6.1), from the entity registry; none if it cannot be read."""
        registry = getattr(self._client, "get_entity_registry", None)
        if registry is None:
            return {}
        try:
            return device_sensors(await registry(), ids)
        except Exception:
            # Then only the room sensor's own last_reported counts
            _LOGGER.debug("Entity registry not read for the room sensors", exc_info=True)
            return {}

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
        await self._timezone(now)
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
            # The user's switch; taking over freezes the base and starts the correction at 0
            writing = self._switch(config, device_id, device, minute, stored, room.value_c, now)
            self.recent(device_id).add(minute, bool(self.aired))
            feed, recent_s = self._feed(config, device_id, minute, states, now)
            previous = stored.get("source")
            source_changed = isinstance(previous, str) and previous != feed.source
            stored["source"] = feed.source
            effective = reading.values.get("flow_setpoint")
            # The base follows the effective setpoint only while Sunflow does not write (shadow, mode off):
            # writing for real the effective setpoint is Sunflow's own value
            if evaluation.valid and effective is not None and not writing:
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
            if source_changed:
                assert isinstance(previous, str)
                shift_k = self._source_change(device_id, device, previous, feed.source, start, strategy, now)
                if correction_k is not None and shift_k:
                    correction_k = self._correction(device_id, strategy).value_k
                    stored.setdefault("corrections", {})[strategy] = round(correction_k, 4)
            if start is not None:
                stored["start_c"], stored["start_at"] = start, now.isoformat()
            target = start + correction_k if start is not None and correction_k is not None else None
            reason = "no_base" if start is None else "strategy_unknown" if correction_k is None else None
            result = await self._guard_step(config, device_id, device, minute, target, feed, room.value_c, now)
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
            self.writers.pop(gone, None)
            self.budgets.pop(gone, None)
            self.active.pop(gone, None)
            self.adopt_checks.pop(gone, None)
            self.real_written_at.pop(gone, None)
            self._failing = {f for f in self._failing if f[0] != gone}
            self.learned.pop(gone, None)
            self.settings.pop(gone, None)
        self._store.house = {
            "setpoint_c": self.watch.last_c,
            "settling": self.watch.settling.as_dict() if self.watch.settling else None,
            "ventilation": self.ventilation.as_dict(),
            "model_source": self.house_source,
        }
        self._store.save()

    def _switch(
        self, config: SunflowConfig, device_id: str, device: DeviceConfig, minute: "Minute",
        stored: dict[str, Any], room_c: float | None, now: datetime,
    ) -> bool:
        """The user's switch (step 3 part 4.7); True while Sunflow takes the heat pump over (switched on, mode heat).

        Switched on: journal; once in mode heat Sunflow takes over (base frozen, correction 0, protection
        layer from the real values). Switched off: nothing is reset (8.6), journal; the shadow again.
        Mode off while switched on writes nothing (8.4, inactive) and lets the base follow again; back in
        mode heat Sunflow takes over anew."""
        on = device.flow_active
        self.active[device_id] = on
        was_on = stored.get("active") is True
        if on != was_on:
            stored["active"] = on
            if on:
                self.notes.append((device_id, t(
                    "Aktives Schreiben eingeschaltet: Sunflow schreibt ab jetzt Max T-Vorlauf und die Raumthermostat-Sperre an die Wärmepumpe."
                ), True))
            else:
                kept = self.guard(device_id).release()
                self.adopt_check(device_id).reset()
                self.writer(device_id).pending = None
                message = t("Aktives Schreiben ausgeschaltet: Sunflow rechnet wieder nur im Schatten. Max T-Vorlauf bleibt, wie zuletzt geschrieben (nichts zurückgestellt).")
                if kept:
                    message = t("{message} Die Raumthermostat-Sperre bleibt gesetzt: die Wärmepumpe heizt nicht, bis sie aufgehoben wird.", message=message)
                self.notes.append((device_id, message, True))
        writing = on and config.house.climate.mode == "heat"
        if writing and stored.get("writing") is not True:
            self._adopt(device_id, minute, stored, room_c, now)
        stored["writing"] = writing
        return writing

    def _adopt(self, device_id: str, minute: "Minute", stored: dict[str, Any], room_c: float | None, now: datetime) -> None:
        """Taking over (8.1, 8.2): the base frozen as it is, the correction of every strategy at 0 (new
        reference, no step), the protection layer from the real Max T-Vorlauf and block. The flow target is
        then the effective setpoint the heat pump has (without a curve), so nothing jumps; written is only
        what the rules ask for."""
        reading = minute.reading
        actual = reading.values.get("max_flow_setpoint")
        self.guard(device_id).adopt(actual, reading.blocked, now)
        strategies = set(stored.get("corrections") or {}) | {s for d, s in self._corrections if d == device_id}
        for strategy in strategies:
            correction = self._correction(device_id, strategy)
            correction.value_k = 0.0
            correction.hold(room_c, now)
        stored["corrections"] = dict.fromkeys(sorted(strategies), 0.0)
        writer = self.writer(device_id)
        writer.pending, writer.manual_at = None, None
        self.adopt_check(device_id).reset()
        base = _float(stored.get("base_c"))
        message = t(
            "Sunflow übernimmt die Wärmepumpe: Basis {base} °C eingefroren, Korrektur 0, Max T-Vorlauf {value} °C wie vorgefunden. Geschrieben wird erst, wenn die Regeln es verlangen.",
            base=number(base) if base is not None else "–", value=number(actual) if actual is not None else "–",
        )
        if reading.blocked:
            message = t("{message} Die Raumthermostat-Sperre ist gesetzt, nicht von Sunflow: Sunflow hebt sie nicht auf.", message=message)
        self.notes.append((device_id, message, False))

    def _source_change(
        self, device_id: str, device: DeviceConfig, old: str, new: str, start: float | None, strategy: str, now: datetime,
    ) -> float:
        """A change of the feedforward source (8.1): event in heat_pump_samples, jump protection when active.

        In the shadow the flow target jumps once, by design (no shift). While Sunflow writes for real
        (step 3 part 4.5, switched on in 4.7) the correction of the strategy is shifted by old − new start
        value, within its limit; the rest of the jump is noted in the journal, as is a change without
        protection (old start value unknown or too old). Returns the shift of the correction (K).
        """
        stored = self._store.devices.setdefault(device_id, {})
        active = self.writing_active(device_id)
        old_c, old_at = _float(stored.get("start_c")), _time(stored.get("start_at"))
        known = old_c is not None and old_at is not None and timedelta(0) <= now - old_at <= SOURCE_SHIFT_MAX_AGE
        event: dict[str, Any] = {"at": now.astimezone(UTC).isoformat(), "kind": "flow_source", "from": old, "to": new, "shadow": not active}
        if known and start is not None:
            assert old_c is not None
            event["jump_k"] = round(start - old_c, 3)
        _LOGGER.info("%s: flow target now from %s (was %s)", device.name, new, old)
        shift = 0.0
        if active:
            correction = self._correction(device_id, strategy) if strategy != UNKNOWN else None
            if known and start is not None and correction is not None:
                assert old_c is not None
                shift, rest = source_shift(old_c, start, correction.value_k)
                correction.shift(shift)
                event["shift_k"], event["rest_k"] = round(shift, 3), round(rest, 3)
                if abs(rest) > SOURCE_JUMP_NOTE_K:
                    self.notes.append((device_id, t(
                        "Quelle des Vorlauf-Ziels gewechselt ({old} → {new}): Die Korrektur reicht nicht ganz für den Ausgleich, das Vorlauf-Ziel springt um {rest} K.",
                        old=source_text(old), new=source_text(new), rest=("+" if rest > 0 else "") + number(rest),
                    ), False))
                event["protected"] = True
            elif start is not None and correction is not None:
                event["protected"] = False
                self.notes.append((device_id, t(
                    "Quelle des Vorlauf-Ziels gewechselt ({old} → {new}) ohne Sprungschutz: kein aktueller Wert der bisherigen Quelle.",
                    old=source_text(old), new=source_text(new),
                ), False))
            else:
                event["protected"] = False
        stored["source_change"] = event
        self.source_events.append((device_id, event))
        return shift

    async def _guard_step(
        self, config: SunflowConfig, device_id: str, device: DeviceConfig, minute: "Minute",
        target: float | None, feed: Feed, room_c: float | None, now: datetime,
    ) -> GuardResult | None:
        """The protection layer for this minute: set value and writes Sunflow causes (in the shadow virtually)."""
        guard = self.guard(device_id)
        settings = minute.control or ControlSettings()
        self.settings[device_id] = settings
        active = self.writing_active(device_id)
        targets = minute.targets or WriteTargets()
        budget = self.budget(device_id)
        budget.limits, budget.tz = settings.budget, self.tz
        self._budget_log(budget, active, targets, settings)
        if active:
            self._check_adopted(device_id, guard, minute, settings, now)
        before = guard.snapshot()
        try:
            result = write_step(guard, self.writer(device_id), GuardInput(
                at=now, reading=minute.reading, evaluation=minute.evaluation, settings=settings,
                values=guard_values(settings, self.learned.get(device_id), guard.bumps, now),
                tv_peak_c=minute.tv_peak_c, heat_w=minute.heat_w, target_c=target,
                curve_c=feed.curve_c if feed.source != BASE else None, heat_needed_w=feed.heat_w,
                room_c=room_c, plan_c=config.house.climate.setpoint_c, mode=config.house.climate.mode,
                surface=config.house.heating_surface,
            ), active=active, budget=budget)
        except Exception:
            # The flow target goes on without it
            _LOGGER.exception("%s: protection layer failed", device.name)
            return None
        if active:
            await self._write(device_id, device, guard, result, targets, settings, before, now)
        for event in (*result.events, *result.writes):
            self.source_events.append((device_id, event))
        for message, warn in result.notes:
            self.notes.append((device_id, message, warn))
        return result

    def _budget_log(self, budget: WriteBudget, active: bool, targets: WriteTargets, settings: ControlSettings) -> None:
        """Writing for real the writes EepromGuard records count; in the shadow the virtual ones (seeded anew
        from the events by the recorder after switching back)."""
        if active and targets.setpoint_entity is not None:
            if not isinstance(budget.log, EepromLog) or budget.log.entity_id != targets.setpoint_entity:
                limit = settings.budget.per_day + settings.budget.emergency_per_day
                budget.log = EepromLog(EepromGuard(self._data_dir, limit), targets.setpoint_entity)
        elif not active and not isinstance(budget.log, VirtualLog):
            budget.log = VirtualLog()

    def _check_adopted(self, device_id: str, guard: Guard, minute: "Minute", settings: ControlSettings, now: datetime) -> None:
        """After adopt_delay: did the heat pump take the last real write over (journal when not)."""
        check = self.adopt_check(device_id)
        outcome = check.step(now, minute.reading, settings.adopt_delay_s, settings.step, settings.deadband)
        if check.state == MISSING and outcome.actual_c is not None:
            guard.not_adopted(outcome.actual_c)
        for message, warn in outcome.notes or []:
            self.notes.append((device_id, message, warn))

    async def _write(
        self, device_id: str, device: DeviceConfig, guard: Guard, result: GuardResult, targets: WriteTargets,
        settings: ControlSettings, before: dict[str, Any], now: datetime,
    ) -> None:
        """The writes of this minute go to the heat pump: the set value (EEPROM, EepromGuard) and the block.

        A write that cannot be made (no entity, no connection, refused, EEPROM limit) is taken back - the
        state stays as before, so the rules ask for it again - and noted in the journal."""
        for event in [w for w in result.writes if w.get("shadow") is False]:
            block = event.get("reason") in (BLOCK_ON, BLOCK_OFF)
            if block:
                call = targets.block_call(bool(event.get("block")))
                what = t("Raumthermostat-Sperre")
            elif event.get("reason") in SETPOINT_REASONS:
                call = targets.setpoint_call(float(event["value_c"]))
                what = t("Max T-Vorlauf")
            else:
                continue
            error: str | None = None
            if call is None:
                error = t("keine Entität zum Schreiben")
            elif self._client is None or not hasattr(self._client, "call_service"):
                error = t("keine Verbindung zu Home Assistant")
            else:
                guard_eeprom = EepromGuard(self._data_dir, settings.budget.per_day + settings.budget.emergency_per_day)
                try:
                    await execute(self._client, [call], {}, guard_eeprom, dry_run=False, at=now)  # type: ignore[arg-type]
                except Exception as err:  # noqa: BLE001 - any failure takes the write back
                    _LOGGER.warning("%s: writing %s failed: %s", device.name, call.entity_id, err)
                    error = str(err) or type(err).__name__
                else:
                    if call.status != "done":
                        error = call.reason or call.status
            if error is not None:
                guard.undo(result, event, before)
                _LOGGER.info("%s: %s not written: %s", device.name, call.entity_id if call else what, error)
                if (device_id, block) not in self._failing:
                    self._failing.add((device_id, block))
                    self.notes.append((device_id, t("{what} nicht geschrieben ({error}); Sunflow versucht es wieder, wenn die Regeln es verlangen.", what=what, error=error), True))
                continue
            self._failing.discard((device_id, block))
            if not block:
                self.adopt_check(device_id).wrote(float(event["value_c"]), now)
                self.real_written_at[device_id] = now

    # Status

    def summary_notes(self, names: dict[str, str], now: datetime) -> list[str]:
        """For the setup summary: heat pumps whose set value someone else changes, or the ESP regulates."""
        notes = []
        for device_id, guard in self.guards.items():
            name = names.get(device_id)
            if name is None:
                continue
            status = guard.foreign.status(now)
            episode = guard.foreign.episode
            if status["episode"] is not None and episode is not None:
                minutes = max(0, round((now - episode.last_at).total_seconds() / 60))
                ago = t("{n} min", n=minutes) if minutes < 120 else t("{n} h", n=round(minutes / 60))
                notes.append(t(
                    "{name}: Max T-Vorlauf wird von außen geändert, nicht von Sunflow ({n}× geändert, zuletzt vor {ago}) – andere Regler abschalten, die ihn schreiben.",
                    name=name, n=episode.count, ago=ago,
                ))
            if status["regulation"]["active"]:
                notes.append(t(
                    "{name}: ESP-Regelung des Vorlaufs aktiv – Sunflow schreibt nicht, solange sie aktiv ist (in ESPHome auf 0 setzen).", name=name,
                ))
        return notes

    def house_status(self, config: SunflowConfig | None = None) -> dict[str, Any]:
        """Room setpoint, mode and room temperature for the climate entity and the UI."""
        return climate_status(config or self._config, self.room, self.watch.settling, self.ventilation, shadow=not any(self.active.values()))

    def flow_status(self, device_id: str) -> dict[str, Any] | None:
        """Base, correction and flow target of one heat pump (shadow: nothing is written)."""
        flow = self.flows.get(device_id)
        if flow is None:
            return None
        surface = self._config.house.heating_surface if self._config else None
        return {
            "shadow": not self.writing_active(device_id),
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
            # The last change of the source (8.1): from, to, jump of the start value, shift of the correction
            # and rest of the jump (only while writing for real), shadow
            "source_change": (self._store.devices.get(device_id) or {}).get("source_change"),
            "curve": curve.status() if (curve := self.curves.get(device_id)) is not None else None,
            "surface": surface,
            "parameters": {
                "kp": CORRECTION_KP, "ti_h": correction_ti_h(surface), "deadband_k": ROOM_DEADBAND_K, "limit_k": CORRECTION_LIMIT_K,
            },
            # Limits the correction does not move beyond (8.3); the direction in guard.freeze_up/down
            "limited": list(flow.limited),
            "guard": self.guard_status(device_id),
            "setpoint": self.setpoint_status(device_id),
        }

    def mode(self, device_id: str) -> str:
        """Shadow or active: the one mark of the HA sensors and the climate attributes (step 3 part 4.6)."""
        return ACTIVE if self.writing_active(device_id) else SHADOW

    def setpoint_status(self, device_id: str) -> dict[str, Any]:
        """The set value for the HA sensors "Stellwert" and "Schreibvorgänge heute" (13, step 3 part 4.6).

        written: the last written Max T-Vorlauf (in the shadow the virtual one), a foreign change taken over
        with reason foreign; None until a value is known. pending: a value waiting, with when it is due and
        why it is wanted. held: why it waits (spacing, budget) or why nothing is written although Sunflow
        would (esp_regulation, block). writes_today: writes of the set value on the local day, None until the
        writes before this run are counted and Home Assistant's time zone is known (never a premature 0).
        """
        guard = self.guards.get(device_id)
        flow = self.flows.get(device_id)
        result = flow.guard if flow is not None else None
        pending = result.pending if result is not None else None
        held: str | None = pending.held if pending is not None else None
        if held is None and guard is not None and result is not None:
            if HELD_ESP in result.inactive:
                held = HELD_ESP
            elif guard.block:
                held = HELD_BLOCK
        writes_today: int | None = None
        budget = self.budgets.get(device_id)
        # The budget's own time zone (set each tick from Home Assistant's): without it the last 24 hours count
        if budget is not None and budget.seeded and budget.tz is not None:
            writes_today = budget.used(self._observed_at or datetime.now(UTC))[0]
        return {
            "mode": self.mode(device_id),
            "written_c": guard.written_c if guard is not None else None,
            "written_at": guard.written_at.astimezone(UTC).isoformat() if guard is not None and guard.written_at else None,
            "written_reason": guard.written_reason if guard is not None else None,
            "pending_c": pending.value_c if pending is not None else None,
            "pending_at": pending.due_at.astimezone(UTC).isoformat() if pending is not None and pending.due_at else None,
            "pending_reason": pending.reason if pending is not None else None,
            "held": held,
            "held_budget": pending.budget if pending is not None else None,
            "writes_today": writes_today,
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
        writer = self.writers.get(device_id)
        return {
            "shadow": not self.writing_active(device_id),
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
            # Write layer (8.5): a value waiting for the spacing or the budget, the last "write now"
            "pending": result.pending.as_dict() if result.pending else None,
            "write_now": {
                "requested_at": writer.manual_at.isoformat() if writer.manual_at else None,
                "last": writer.manual_result,
            } if writer is not None else None,
            # Budget (8.5): used today and in the last hour, left, what it holds back (virtual in the shadow)
            "budget": self.budget(device_id).status(now, virtual=not self.writing_active(device_id)),
            # Foreign writers (8.5): the running episode, the latest foreign changes, the ESP regulation
            "foreign": guard.foreign.status(now),
            # Writing for real (step 3 part 4.7): whether the heat pump took the last write over
            "adopt": self.adopt_checks[device_id].status() if device_id in self.adopt_checks else None,
            "writes": list(reversed(guard.recent_writes)),
            "unwanted_stops": list(reversed(guard.recent_stops)),
            "values": guard_values(settings, learned, guard.bumps, now).as_dict(),
            "learned": learned.as_dict() if learned else None,
            "parameters": {
                "start_margin_k": settings.start_margin, "stop_margin_k": settings.stop_margin,
                "deadband_k": settings.deadband, "step_k": settings.step, "block_min_s": settings.block_min_s,
                "min_interval_s": settings.min_interval_s, "protect_interval_s": settings.protect_interval_s,
            },
        }


def climate_status(
    config: SunflowConfig | None,
    room: RoomTemperature | None,
    settling: Settling | None,
    ventilation: VentilationWatch | None = None,
    shadow: bool = True,
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
        # No heat pump is written to for real (the mark per heat pump is in its flow status, setpoint.mode)
        "shadow": shadow,
    }
