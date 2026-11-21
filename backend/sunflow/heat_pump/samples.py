"""Recording heat pumps: every minute their values and states, stored as 5 minute means.

Design/heat_pump.md, sections 4, 9.1 and 10: one row per heat pump and 5 minutes in the table
heat_pump_samples of shadow.db. The peak of the flow temperature (tv_max, published every 30 s)
and compressor starts and stops come from state_changed events, so none is lost between two
samples. Rooms being aired (6.2) are taken from the shadow control each minute and noted per row
in flags.ventilation, changes of the flow target's source (8.1) in events. The heating curve of each
heat pump is learned from the table every hour (9.2), with the swing, start jumps and output range
of the protection layer (8.3, 8.4); the writes it would cause are noted in events. Unknown states, faults, the room thermostat block and the heat pump limiting the flow
itself are noted once per change in the journal. Reads only; nothing is written to a device.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
import math
from typing import Any

from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.control.journal import ControlJournal
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import manifest_id
from sunflow.devices.resolver import UNAVAILABLE_STATES, normalize, preview
from sunflow.i18n import t
from sunflow.planning.house_model import outside_entity
from sunflow.shadow.store import HEAT_PUMP_KEEP, ShadowStore

from .curve import Curve, learn as learn_curve
from .defaults import CURVE_HISTORY_DAYS, CURVE_REFRESH_S, FEED_HEAT_WINDOW_S, JUMP_LEARN_DAYS, POWER_LEARN_DAYS
from .learned import Learned, learn as learn_guard
from .limits import ControlSettings
from .shadow import FlowShadow
from .states import UNKNOWN, Evaluation, HeatingTracker, Reading, StateSettings, canonical, read, value_key

_LOGGER = logging.getLogger(__name__)

INTERVAL = timedelta(minutes=5)
PRUNE_EVERY = timedelta(days=1)
CURVE_REFRESH = timedelta(seconds=CURVE_REFRESH_S)
JOURNAL_ACTION = "heat_pump"
# Column -> role of the numeric 5 minute means
NUMERIC = {
    "tv_c": "flow_temperature",
    "tr_c": "return_temperature",
    "tvbh_c": "flow_temperature_bh",
    "setpoint_c": "flow_setpoint",
    "max_setpoint_c": "max_flow_setpoint",
    "bypass_pct": "bypass_valve",
}
PEAK_ROLE = "flow_temperature_max"
# Roles whose changes are followed between the samples
EVENT_ROLES = (PEAK_ROLE, "compressor", "activity", "flow_temperature", "flow_setpoint", "max_flow_setpoint")


def interval_start(moment: datetime) -> datetime:
    """Start of the 5 minute interval containing moment (UTC)."""
    moment = moment.astimezone(UTC)
    return moment.replace(minute=moment.minute - moment.minute % 5, second=0, microsecond=0)


def records_heat_pump(profile: dict[str, Any]) -> bool:
    """A heat pump described by a manifest with states (e.g. the Rotex HPSU)."""
    return "heat_pump" in profile.get("capabilities", []) and bool(manifest_id(profile)) and "states" in profile


def _number(state: dict[str, Any] | None, default_unit: str | None = None) -> float | None:
    if not state or state.get("state") in UNAVAILABLE_STATES:
        return None
    value, _ = normalize(state.get("state"), (state.get("attributes") or {}).get("unit_of_measurement") or default_unit, None)
    return value if value is not None and math.isfinite(value) else None


def _time(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class Minute:
    """One sample of a heat pump."""

    at: datetime
    reading: Reading
    evaluation: Evaluation
    heat_w: float | None
    heat_source: str | None
    outside_c: float | None
    rooms: dict[str, float]
    # Rooms being aired in this minute (6.2)
    ventilation: tuple[str, ...] = ()
    # Highest tv_max published since the previous minute (8.4)
    tv_peak_c: float | None = None
    # Parameters of the protection layer from the profile and the manifest (8.3, 8.4)
    control: ControlSettings | None = None


@dataclass(kw_only=True)
class Recording:
    """What is collected for one heat pump."""

    device_id: str
    name: str
    tracker: HeatingTracker
    profile: dict[str, Any]
    manifest: dict[str, Any] | None = None
    interval: datetime | None = None
    minutes: list[Minute] = field(default_factory=list)
    # By interval start: highest tv_max published in it, compressor starts and stops
    peaks: dict[datetime, float] = field(default_factory=dict)
    events: dict[datetime, list[dict[str, Any]]] = field(default_factory=dict)
    # Latest values of the followed roles, for the events
    current: dict[str, float] = field(default_factory=dict)
    last: Minute | None = None
    # Highest tv_max since the last sample
    minute_peak: float | None = None
    outside_entity: str | None = None
    # What the journal was told
    noted_unknown: set[tuple[str, str]] = field(default_factory=set)
    noted_fault: bool | None = None
    noted_block: bool | None = None
    noted_limit: bool = False


class HeatPumpRecorder:
    """Samples every configured heat pump each minute and stores 5 minute rows."""

    def __init__(
        self,
        store: ShadowStore,
        profiles: Callable[[], dict[str, dict[str, Any]]],
        usable: Callable[[str], bool],
        journal: ControlJournal | None = None,
    ) -> None:
        """Initialize; usable tells whether a device is reachable and compatible (ManifestManager.usable)."""
        self._store = store
        self._profiles = profiles
        self._usable = usable
        self.journal = journal
        self._recordings: dict[str, Recording] = {}
        # entity_id -> (device id, role) of the roles followed between the samples
        self._watched: dict[str, tuple[str, str]] = {}
        self._pruned_at: datetime | None = None
        # Shadow control (room correction, flow target) computed from each minute's samples
        self.flow: FlowShadow | None = None
        self._curve_task: asyncio.Task[None] | None = None

    # Events between the samples

    def on_state_changed(self, event: dict[str, Any]) -> None:
        """Peaks of tv_max, compressor starts and stops, the end of hot water and defrost."""
        data = event.get("data", {})
        if (watched := self._watched.get(data.get("entity_id"))) is None:
            return
        recording = self._recordings.get(watched[0])
        if recording is None:
            return
        role = watched[1]
        new, old = data.get("new_state") or {}, data.get("old_state") or {}
        at = _time(new.get("last_updated")) or _time(new.get("last_changed")) or datetime.now(UTC)
        if role == "activity":
            self._activity_changed(recording, old.get("state"), new.get("state"), _time(new.get("last_changed")) or at)
            return
        if role == "compressor":
            before, after = old.get("state"), new.get("state")
            if {before, after} == {"on", "off"}:
                recording.events.setdefault(interval_start(at), []).append(
                    {
                        "at": at.astimezone(UTC).isoformat(),
                        "kind": "start" if after == "on" else "stop",
                        "tv_c": recording.current.get("flow_temperature"),
                        "tv_max_c": recording.current.get(PEAK_ROLE),
                        "setpoint_c": recording.current.get("flow_setpoint"),
                        "max_setpoint_c": recording.current.get("max_flow_setpoint"),
                    }
                )
            return
        if (value := _number(new)) is None:
            return
        recording.current[role] = value
        if role == PEAK_ROLE:
            bucket = interval_start(at)
            recording.peaks[bucket] = max(recording.peaks.get(bucket, value), value)
            recording.minute_peak = max(recording.minute_peak if recording.minute_peak is not None else value, value)

    def _activity_changed(self, recording: Recording, old: str | None, new: str | None, at: datetime) -> None:
        if old is None or new is None or old in UNAVAILABLE_STATES or new in UNAVAILABLE_STATES:
            return
        entity = next((e for e in (recording.manifest or {}).get("entities", []) if e["key"] == recording.profile["roles"]["activity"]["key"]), None)
        before = canonical(recording.profile, "activity", value_key(entity, old))
        after = canonical(recording.profile, "activity", value_key(entity, new))
        if UNKNOWN not in (before, after):
            recording.tracker.activity_changed(before, after, at)

    # Sampling

    async def tick(self, config: SunflowConfig, snapshot: Snapshot, now: datetime) -> None:
        """Take a sample of every heat pump; store the intervals that ended; clear out old rows once a day."""
        profiles = self._profiles()
        devices = [d for d in config.devices if records_heat_pump(profiles.get(d.profile, {}))]
        for gone in set(self._recordings) - {d.id for d in devices}:
            del self._recordings[gone]
        rows: list[dict[str, Any]] = []
        watched: dict[str, tuple[str, str]] = {}
        aired: tuple[str, ...] = ()
        if self.flow is not None:
            try:
                aired = await self.flow.observe(config, snapshot.states, now)
            except Exception:
                # The recording goes on without it
                _LOGGER.exception("Ventilation detection failed")
        sampled: dict[str, tuple[DeviceConfig, Minute | None]] = {}
        for device in devices:
            profile = profiles[device.profile]
            recording = self._recordings.get(device.id)
            if recording is None:
                recording = self._recordings[device.id] = Recording(
                    device_id=device.id, name=device.name, tracker=HeatingTracker(StateSettings.from_profile(profile)), profile=profile
                )
            elif recording.profile is not profile:
                # Profiles loaded anew: same device, the times may have changed
                recording.profile, recording.tracker.settings = profile, StateSettings.from_profile(profile)
            recording.name = device.name
            interval = interval_start(now)
            if recording.interval is not None and recording.interval != interval:
                if (row := self._row(recording)) is not None:
                    rows.append(row)
                recording.minutes = []
                # Peaks and events of intervals that are over (also of one without samples)
                recording.peaks = {k: v for k, v in recording.peaks.items() if k >= interval}
                recording.events = {k: v for k, v in recording.events.items() if k >= interval}
            recording.interval = interval
            minute, entities = self._sample(config, device, profile, recording, snapshot, now, aired)
            watched |= {entity_id: (device.id, role) for role, entity_id in entities.items() if role in EVENT_ROLES and entity_id}
            sampled[device.id] = (device, minute)
            if self.flow is not None and not self.flow.recent(device.id).seeded:
                # After a start the heat of the last hours comes from the table, then from the minutes
                since = now - timedelta(seconds=FEED_HEAT_WINDOW_S) - INTERVAL
                try:
                    self.flow.recent(device.id).seed(await asyncio.to_thread(self._store.heat_pump_samples, device.id, since), now)
                except Exception:
                    _LOGGER.exception("Reading the recent heat failed")
            if minute is not None:
                recording.minutes.append(minute)
                recording.last = minute
                self._note(device, recording, minute, now)
        self._watched = watched
        if self.flow is not None:
            try:
                await self.flow.tick(config, snapshot.states, sampled, now)
            except Exception:
                # The recording goes on without the shadow control
                _LOGGER.exception("Shadow flow control failed")
            events, self.flow.source_events = self.flow.source_events, []
            for device_id, event in events:
                if (recording := self._recordings.get(device_id)) is not None:
                    recording.events.setdefault(interval_start(now), []).append(event)
            notes, self.flow.notes = self.flow.notes, []
            for device_id, message, warn in notes:
                if (device := next((d for d in devices if d.id == device_id), None)) is not None:
                    self._journal(device, message, now, warn=warn)
            if self._curve_task is None or self._curve_task.done():
                self._curve_task = asyncio.create_task(self.learn_curves(config, now))
        if rows:
            await asyncio.to_thread(self._store.store_heat_pump_samples, rows)
        if self._pruned_at is None or now - self._pruned_at >= PRUNE_EVERY:
            self._pruned_at = now
            removed = await asyncio.to_thread(self._store.prune_heat_pump_samples, now - HEAT_PUMP_KEEP)
            if removed:
                _LOGGER.info("Removed %s heat pump samples older than a year", removed)

    # Heating curve (9.2)

    @staticmethod
    def curve_key(config: SunflowConfig) -> str:
        """What a curve depends on besides the table: the heating surface type and the room weights."""
        rooms = ",".join(f"{r.entity_id}={r.weight}" for r in sorted(config.house.rooms, key=lambda r: r.entity_id))
        return f"{config.house.heating_surface}|{rooms}"

    async def learn_curves(self, config: SunflowConfig, now: datetime) -> None:
        """Learn the curve of every heat pump from the table, every CURVE_REFRESH or when the type or rooms changed."""
        if self.flow is None:
            return
        key = self.curve_key(config)
        weights = {room.entity_id: room.weight for room in config.house.rooms}
        days = max(CURVE_HISTORY_DAYS, JUMP_LEARN_DAYS, POWER_LEARN_DAYS)
        for device_id in list(self._recordings):
            current: Curve | None = self.flow.curves.get(device_id)
            curve_due = not (current is not None and current.key == key and timedelta(0) <= now - current.computed_at < CURVE_REFRESH)
            learned: Learned | None = self.flow.learned.get(device_id)
            guard_due = learned is None or not timedelta(0) <= now - learned.computed_at < CURVE_REFRESH
            if not curve_due and not guard_due:
                continue
            try:
                rows = await asyncio.to_thread(self._store.heat_pump_samples, device_id, now - timedelta(days=days))
                since = now - timedelta(days=CURVE_HISTORY_DAYS)
                curve_rows = [r for r in rows if isinstance(r.get("start"), str) and r["start"] >= since.astimezone(UTC).isoformat()]
                curve = await asyncio.to_thread(learn_curve, curve_rows, weights, config.house.heating_surface, now, key) if curve_due else None
                guard = await asyncio.to_thread(learn_guard, rows, now) if guard_due else None
            except Exception:
                # Tried again with the next tick
                _LOGGER.exception("Learning the heating curve failed")
                return
            recording = self._recordings.get(device_id)
            if curve is not None:
                if (current is None or current.state != curve.state) and recording:
                    _LOGGER.info("%s: heating curve %s (%s intervals)", recording.name, curve.state, curve.intervals)
                self.flow.set_curve(device_id, curve)
            if guard is not None:
                self.flow.set_learned(device_id, guard)

    def _sample(
        self,
        config: SunflowConfig,
        device: DeviceConfig,
        profile: dict[str, Any],
        recording: Recording,
        snapshot: Snapshot,
        now: datetime,
        aired: tuple[str, ...] = (),
    ) -> tuple[Minute | None, dict[str, str | None]]:
        found = (snapshot.manifests.get(device.ha_device_id) or {}).get(manifest_id(profile) or "")
        recording.manifest = found.manifest if found else None
        values = preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
        entities = {v.role: v.entity_id for v in values}
        if recording.manifest is None or all(v.raw_state is None or v.raw_state in UNAVAILABLE_STATES for v in values):
            # Nothing to read (node away, no manifest): no sample
            return None, entities
        numbers = {v.role: v.value for v in values}
        for role in EVENT_ROLES:
            if (number := numbers.get(role)) is not None:
                recording.current[role] = number
        reading = read(profile, recording.manifest, {role: snapshot.states.get(e) if e else None for role, e in entities.items()}, numbers)
        evaluation = recording.tracker.evaluate(reading, now, self._usable(device.id))
        heat_w, source = None, None
        if config.house.heat_entity:
            heat_w, source = _number(snapshot.states.get(config.house.heat_entity), "W"), "meter"
        elif numbers.get("heat_power") is not None:
            heat_w, source = numbers["heat_power"], "measured"
        recording.outside_entity = outside_entity(config, entities)
        rooms = {
            room.entity_id: value
            for room in config.house.rooms
            if (value := _number(snapshot.states.get(room.entity_id))) is not None
        }
        minute = Minute(
            at=now,
            reading=reading,
            evaluation=evaluation,
            heat_w=heat_w,
            heat_source=source,
            outside_c=_number(snapshot.states.get(recording.outside_entity)) if recording.outside_entity else None,
            rooms=rooms,
            ventilation=aired,
            tv_peak_c=recording.minute_peak,
            control=ControlSettings.from_profile(profile, recording.manifest, device.flow_min_c, device.flow_max_c),
        )
        recording.minute_peak = None
        return minute, entities

    def _row(self, recording: Recording) -> dict[str, Any] | None:
        """The 5 minute row of the interval that ended; None without samples."""
        minutes, interval = recording.minutes, recording.interval
        if not minutes or interval is None:
            return None

        def mean(values: list[float | None]) -> float | None:
            known = [v for v in values if v is not None]
            return sum(known) / len(known) if known else None

        def common(values: list[str]) -> str:
            # Most frequent; on a tie the later one
            return max(set(values), key=lambda v: (values.count(v), max(i for i, x in enumerate(values) if x == v)))

        peaks = [p for p in (recording.peaks.get(interval), *(m.reading.values.get(PEAK_ROLE) for m in minutes)) if p is not None]
        compressor = [m.reading.compressor for m in minutes if m.reading.compressor is not None]
        rooms = {
            entity_id: mean([m.rooms.get(entity_id) for m in minutes])
            for entity_id in sorted({e for m in minutes for e in m.rooms})
        }
        row: dict[str, Any] = {
            "device_id": recording.device_id,
            "start": interval,
            "samples": len(minutes),
            **{column: mean([m.reading.values.get(role) for m in minutes]) for column, role in NUMERIC.items()},
            "tv_max_c": max(peaks) if peaks else None,
            "heat_w": mean([m.heat_w for m in minutes]),
            "heat_source": next((m.heat_source for m in reversed(minutes) if m.heat_source), None),
            "outside_c": mean([m.outside_c for m in minutes]),
            # Sunflow writes nothing yet (shadow operation)
            "written_c": None,
            "compressor": sum(compressor) / len(compressor) if compressor else None,
            "program": common([m.reading.program for m in minutes]),
            "activity": common([m.reading.activity for m in minutes]),
            "strategy": common([m.reading.strategy for m in minutes]),
            "error": next((m.reading.error for m in reversed(minutes) if m.reading.error is not None), None),
            "rooms": rooms,
            "events": sorted(recording.events.get(interval, []), key=lambda e: e["at"]),
            "flags": {
                # Valid only when every minute was
                "valid": all(m.evaluation.valid for m in minutes),
                "frozen": sorted({r for m in minutes for r in m.evaluation.frozen}),
                "after_run": sorted({r for m in minutes for r in m.evaluation.after_run}),
                "blocked": any(m.reading.blocked for m in minutes),
                "fault": any(m.reading.fault for m in minutes),
                "self_limited": any(m.evaluation.self_limited for m in minutes),
                # Rooms aired in any minute (6.2); such intervals are left out of learning the heating curve
                "ventilation": sorted({r for m in minutes for r in m.ventilation}),
            },
        }
        return row

    # Journal

    def _note(self, device: DeviceConfig, recording: Recording, minute: Minute, now: datetime) -> None:
        reading, evaluation = minute.reading, minute.evaluation
        for role, text in sorted(reading.unknown.items()):
            if (role, text) not in recording.noted_unknown:
                recording.noted_unknown.add((role, text))
                self._journal(device, t("Unbekannter Zustand „{value}“ ({role}) – Sunflow greift dann nicht ein.", value=text, role=role), now, warn=True)
        if reading.error is not None and reading.fault != recording.noted_fault:
            if reading.fault:
                self._journal(device, t("Störung der Wärmepumpe: {error}", error=self._error_text(recording, reading)), now, warn=True)
            elif recording.noted_fault:
                self._journal(device, t("Störung der Wärmepumpe behoben"), now)
            recording.noted_fault = reading.fault
        if reading.blocked is not None and reading.blocked != recording.noted_block:
            if reading.blocked:
                self._journal(device, t("Raumthermostat-Sperre aktiv: die Wärmepumpe heizt nicht."), now)
            elif recording.noted_block:
                self._journal(device, t("Raumthermostat-Sperre aufgehoben"), now)
            recording.noted_block = reading.blocked
        if evaluation.self_limited and not recording.noted_limit:
            self._journal(device, t("Die Wärmepumpe begrenzt den Vorlauf selbst (Absenk-Zeit, T-Vorlauf Nacht)."), now)
        recording.noted_limit = evaluation.self_limited

    @staticmethod
    def _error_text(recording: Recording, reading: Reading) -> str:
        """The error as the device shows it (text of the manifest), else its key."""
        entity_key = recording.profile["roles"].get("error", {}).get("key")
        entity = next((e for e in (recording.manifest or {}).get("entities", []) if e["key"] == entity_key), None)
        for item in (entity or {}).get("values") or []:
            if isinstance(item, list) and len(item) > 2 and item[2] == reading.error:
                return str(item[1])
        return reading.error or "–"

    def _journal(self, device: DeviceConfig, message: str, now: datetime, warn: bool = False) -> None:
        (_LOGGER.warning if warn else _LOGGER.info)("%s: %s", device.name, message)
        if self.journal is not None:
            self.journal.note(device.id, device.name, JOURNAL_ACTION, message, now)

    # Status

    def pending_events(self, device_id: str) -> list[dict[str, Any]]:
        """Events of the intervals not stored yet (shown with the stored ones)."""
        recording = self._recordings.get(device_id)
        return [e for events in (recording.events.values() if recording else []) for e in events]

    def manifest(self, device_id: str) -> dict[str, Any] | None:
        """The manifest the heat pump was last sampled with; None before."""
        recording = self._recordings.get(device_id)
        return recording.manifest if recording is not None else None

    def status(self) -> dict[str, dict[str, Any]]:
        """Per heat pump: the latest states and whether it heats validly."""
        result = {}
        for device_id, recording in self._recordings.items():
            if (last := recording.last) is None:
                continue
            reading, evaluation = last.reading, last.evaluation
            result[device_id] = {
                "at": last.at.isoformat(),
                "program": reading.program,
                "program_key": reading.program_key,
                "activity": reading.activity,
                "strategy": reading.strategy,
                "blocked": reading.blocked,
                "error": reading.error,
                "fault": reading.fault,
                "compressor": reading.compressor,
                "bypass_pct": reading.bypass,
                "valid": evaluation.valid,
                "frozen": list(evaluation.frozen),
                "after_run": list(evaluation.after_run),
                "self_limited": evaluation.self_limited,
                "heat_source": last.heat_source,
                "outside_entity": recording.outside_entity,
                "unknown": reading.unknown,
                "values": {
                    role: reading.values.get(role)
                    for role in ("flow_temperature", "flow_temperature_max", "return_temperature", "flow_setpoint", "max_flow_setpoint")
                },
                # Base, correction and flow target (shadow, section 8)
                "flow": self.flow.flow_status(device_id) if self.flow is not None else None,
            }
        return result
