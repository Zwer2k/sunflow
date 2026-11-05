"""Loads that charge a battery of their own (e-bike, tool batteries) without a SoC or plug sensor.

Readiness comes from a "plugged in" entity, else from a short test charge after a trigger
(e.g. the shed door locked): the switch goes on for a moment; drawing power means ready, and the
load is planned (PV surplus or cheap hours) and off until then. Switched on, power below a
threshold for some minutes means full or unplugged: not ready until the next test.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from statistics import fmean
from typing import Any

from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.devices.resolver import UNAVAILABLE_STATES
from sunflow.i18n import t

_LOGGER = logging.getLogger(__name__)

STATE_FILE = "chargers.json"
# Chargers take a moment to start: low power right after switching on does not mean full
SWITCH_ON_GRACE = timedelta(minutes=2)
# Learned energy per charge: the mean of the last ones
MAX_CHARGES = 5
# A charge below this is a test or an almost full battery, not a typical charge
MIN_CHARGE_WH = 20.0
# A charge that takes longer than usual keeps this share of the usual energy planned
MIN_REMAINING_SHARE = 0.2
# Gaps longer than this (App or Home Assistant away) are not counted as charged energy
MAX_STEP = timedelta(minutes=5)


@dataclass(frozen=True, kw_only=True)
class ChargerReady:
    """What planning needs to know about a charger."""

    ready: bool
    energy_wh: float | None


@dataclass(kw_only=True)
class _State:
    ready: bool = False
    since: str | None = None
    note: str | None = None
    trigger: str | None = None
    test_due: str | None = None
    testing_since: str | None = None
    on_since: str | None = None
    low_since: str | None = None
    last_step: str | None = None
    charge_wh: float = 0.0
    charges: list[float] = field(default_factory=list)
    # Full while its plug entity still says plugged in; cleared when it is unplugged
    full: bool = False


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _on(states: dict[str, dict[str, Any]], entity_id: str | None) -> bool | None:
    """On/off of an entity; None while it has no usable state."""
    state = states.get(entity_id or "", {}).get("state")
    if state is None or state in UNAVAILABLE_STATES:
        return None
    return state in ("on", "home", "true", "plugged", "connected")


class ChargerWatch:
    """Readiness, test charges and charged energy of the loads with a battery of their own."""

    def __init__(self, data_dir: Path) -> None:
        """Load the states kept across restarts."""
        self._path = data_dir / STATE_FILE
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError, ValueError:
            data = {}
        self._states = {device_id: _State(**state) for device_id, state in data.items()}
        # Set when readiness changed: the plan has to follow
        self.changed = False

    def _save(self) -> None:
        self._path.write_text(json.dumps({k: asdict(s) for k, s in self._states.items()}))

    def _state(self, device_id: str) -> _State:
        return self._states.setdefault(device_id, _State())

    def energy_wh(self, device: DeviceConfig) -> float | None:
        """Energy of a charge: learned from the last charges, else the configured value."""
        charges = self._state(device.id).charges
        return fmean(charges) if charges else device.charger.energy_wh if device.charger else None

    def remaining_wh(self, device: DeviceConfig) -> float | None:
        """Energy still to charge: a charge interrupted by the plan goes on with the rest, not anew.

        A charge that takes more than usual keeps a part of the usual energy planned until the
        load is found full.
        """
        if (energy := self.energy_wh(device)) is None:
            return None
        charged = self._state(device.id).charge_wh
        return max(energy - charged, MIN_REMAINING_SHARE * energy)

    def ready(self, device: DeviceConfig, states: dict[str, dict[str, Any]]) -> bool:
        """A plug entity decides where there is one, else the last test or charge."""
        if device.charger is None:
            return False
        if device.charger.connected_entity:
            return bool(_on(states, device.charger.connected_entity)) and not self._state(device.id).full
        return self._state(device.id).ready

    def planning(self, config: SunflowConfig, states: dict[str, dict[str, Any]]) -> dict[str, ChargerReady]:
        """Readiness and energy per charger for the plan."""
        return {
            d.id: ChargerReady(ready=self.ready(d, states), energy_wh=self.remaining_wh(d))
            for d in config.devices
            if d.charger is not None
        }

    def status(self, device: DeviceConfig, states: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """For the UI: ready, since when, the last result and a test that is due."""
        state = self._state(device.id)
        return {
            "ready": self.ready(device, states),
            "since": state.since,
            "note": state.note,
            "test_due": state.test_due,
            "testing": state.testing_since is not None,
            "energy_wh": self.energy_wh(device),
            "charges": len(state.charges),
        }

    def set_plugged(self, device: DeviceConfig, plugged: bool, now: datetime) -> None:
        """Plugged in (or not) as the user says; a load that draws no power is found full or unplugged as usual."""
        state = self._state(device.id)
        state.test_due = state.testing_since = None
        if plugged and not state.ready:
            state.charge_wh = 0.0
        self._set_ready(state, plugged, t("Als eingesteckt gemeldet") if plugged else t("Als nicht eingesteckt gemeldet"), now)
        self._save()

    def _set_ready(self, state: _State, ready: bool, note: str, now: datetime) -> None:
        if ready != state.ready:
            self.changed = True
        state.ready, state.since, state.note = ready, now.isoformat(), note

    def step(
        self, device: DeviceConfig, switch_on: bool | None, power_w: float | None, states: dict[str, dict[str, Any]], now: datetime
    ) -> tuple[str, str] | None:
        """One minute: the action it forces ("charge" for a test, "stop" when not ready) with its reason; None follows the plan."""
        settings = device.charger
        if settings is None:
            return None
        state = self._state(device.id)
        try:
            return self._step(device, settings, state, switch_on, power_w, states, now)
        finally:
            state.last_step = now.isoformat()
            self._save()

    def _step(
        self, device: DeviceConfig, settings: Any, state: _State, switch_on: bool | None, power_w: float | None,
        states: dict[str, dict[str, Any]], now: datetime,
    ) -> tuple[str, str] | None:
        threshold = settings.done_below_w
        drawing = power_w is not None and power_w >= threshold
        self._count_energy(state, switch_on, power_w, now)
        self._watch_trigger(settings, state, states, now)

        if (testing := _time(state.testing_since)) is not None:
            if now - testing < timedelta(seconds=settings.test_seconds):
                return "charge", t("Prüft, ob {name} ladebereit ist", name=device.name)
            state.testing_since = None
            if drawing:
                self._set_ready(state, True, t("Ladebereit (Prüfung)"), now)
            else:
                self._set_ready(state, False, t("Nicht ladebereit (Prüfung ohne Leistung)"), now)
                return "stop", t("Nicht ladebereit")

        if (due := _time(state.test_due)) is not None and now >= due:
            state.test_due = None
            # Charging already: it is plugged in, no test needed
            if not (switch_on and drawing):
                if not state.ready:
                    # The test is no charge of its own
                    state.charge_wh = 0.0
                state.testing_since = now.isoformat()
                return "charge", t("Prüft, ob {name} ladebereit ist", name=device.name)

        if settings.connected_entity:
            if not _on(states, settings.connected_entity):
                state.full = False
                return "stop", t("Nicht angesteckt")
            if state.full:
                return "stop", t("Voll – wieder eingeplant, wenn neu angesteckt")
        elif not state.ready:
            return "stop", t("Nicht ladebereit")

        # Switched on long enough and hardly any power: full or unplugged
        on_since = _time(state.on_since)
        if switch_on and on_since is not None and now - on_since >= SWITCH_ON_GRACE and not drawing:
            low_since = _time(state.low_since) or now
            state.low_since = low_since.isoformat()
            if now - low_since >= timedelta(minutes=settings.done_after_min):
                self._finish(state, now)
                return "stop", t("Voll oder abgesteckt")
        else:
            state.low_since = None
        return None

    def _count_energy(self, state: _State, switch_on: bool | None, power_w: float | None, now: datetime) -> None:
        if switch_on:
            state.on_since = state.on_since or now.isoformat()
        else:
            state.on_since = state.low_since = None
        last = _time(state.last_step)
        if switch_on and power_w and last is not None and timedelta(0) < now - last <= MAX_STEP:
            state.charge_wh += power_w * (now - last).total_seconds() / 3600

    def _watch_trigger(self, settings: Any, state: _State, states: dict[str, dict[str, Any]], now: datetime) -> None:
        if not settings.trigger_entity:
            return
        current = states.get(settings.trigger_entity, {}).get("state")
        if current is None or current in UNAVAILABLE_STATES:
            return
        if state.trigger is not None and current != state.trigger and settings.trigger_state in (None, "", current):
            state.test_due = (now + timedelta(minutes=settings.trigger_delay_min)).isoformat()
        state.trigger = current

    def _finish(self, state: _State, now: datetime) -> None:
        if state.charge_wh >= MIN_CHARGE_WH:
            state.charges = [*state.charges, round(state.charge_wh)][-MAX_CHARGES:]
        state.charge_wh = 0.0
        state.low_since = None
        state.full = True
        self.changed = True
        self._set_ready(state, False, t("Voll oder abgesteckt"), now)
