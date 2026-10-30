"""Device test: verify that battery control reaches the inverter.

Reads the current state, picks a harmless command (limit charging, limit
discharging or charge 500 W from the grid), holds it, checks the battery
follows in the expected direction and restores self-consumption. Any
failure or abort restores self-consumption first.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from statistics import median
from typing import Any, Literal

from .executor import ExecutorError, PlannedCall

_LOGGER = logging.getLogger(__name__)

LIMIT_W = 300
FORCE_CHARGE_W = 500
ACTIVE_THRESHOLD_W = 300

type Phase = Literal[
    "idle", "baseline", "command", "restore", "verify_restore", "done", "failed", "aborted"
]


@dataclass(frozen=True, kw_only=True)
class Timings:
    """Durations in seconds; shortened in unit tests."""

    baseline: float = 20
    hold: float = 60
    settle: float = 15
    verify_restore: float = 30
    sample: float = 5


@dataclass(kw_only=True)
class Sample:
    """One battery power reading during the test."""

    at: datetime
    phase: str
    battery_power: float | None
    soc: float | None


@dataclass(kw_only=True)
class DeviceTestState:
    """Progress and result of a device test."""

    device_id: str
    phase: Phase = "idle"
    message: str = ""
    test_action: str | None = None
    test_power: float | None = None
    expectation: str | None = None
    samples: list[Sample] = field(default_factory=list)
    control_works: bool | None = None
    sign_correct: bool | None = None
    restored: bool | None = None
    previous_mode: str | None = None
    previous_mode_restored: bool | None = None
    calls: list[PlannedCall] = field(default_factory=list)
    error: str | None = None
    started: datetime | None = None
    finished: datetime | None = None

    @property
    def running(self) -> bool:
        """Return True while the test is in progress."""
        return self.phase in ("baseline", "command", "restore", "verify_restore")


ALL_ACTIONS = frozenset({"hold", "force_charge", "force_discharge", "limit_charge", "limit_discharge"})


def choose_test(baseline_power: float, supported: frozenset[str] | set[str] = ALL_ACTIONS) -> tuple[str, float, str]:
    """Pick action, power and expectation from the median baseline power (+ = charge).

    The action must be one the device supports; a visible change of the battery power
    proves control and sign.
    """
    if baseline_power > ACTIVE_THRESHOLD_W and "limit_charge" in supported:
        return "limit_charge", LIMIT_W, f"Ladeleistung sinkt auf höchstens ~{LIMIT_W} W"
    if baseline_power < -ACTIVE_THRESHOLD_W:
        if "limit_discharge" in supported:
            return "limit_discharge", LIMIT_W, f"Entladeleistung sinkt auf höchstens ~{LIMIT_W} W"
        if "force_charge" not in supported and "hold" in supported:
            return "hold", 0.0, "Batterie entlädt nicht mehr"
    if baseline_power > ACTIVE_THRESHOLD_W and "force_discharge" in supported:
        return "force_discharge", FORCE_CHARGE_W, f"Batterie entlädt mit mindestens ~{FORCE_CHARGE_W} W"
    if "force_charge" in supported:
        return "force_charge", FORCE_CHARGE_W, f"Batterie lädt mit mindestens ~{FORCE_CHARGE_W} W"
    if "force_discharge" in supported:
        return "force_discharge", FORCE_CHARGE_W, f"Batterie entlädt mit mindestens ~{FORCE_CHARGE_W} W"
    raise ExecutorError("Das Gerät kann keine prüfbare Aktion – Test nicht möglich")


def evaluate(action: str, power: float, settled: list[float]) -> tuple[bool, bool]:
    """Return (control works, sign correct) from readings after settling."""
    if not settled:
        return False, False
    level = median(settled)
    tolerance = max(power * 0.3, 150)
    match action:
        case "limit_charge":
            return level <= power + tolerance, level >= -tolerance
        case "limit_discharge":
            return level >= -(power + tolerance), level <= tolerance
        case "hold":
            # Chosen while discharging: stopping proves control and the sign alike
            stopped = level >= -tolerance
            return stopped, stopped
        case "force_discharge":
            return abs(level) >= power - tolerance, level < 0
        case _:
            # force_charge: charging means positive with the configured sign
            works = abs(level) >= power - tolerance
            return works, level > 0


ReadFn = Callable[[], Awaitable[tuple[float | None, float | None]]]
RunFn = Callable[[str, dict[str, float]], Awaitable[list[PlannedCall]]]
# Called with what is needed to undo the test once it has written to the device,
# and with None once self-consumption (and the previous mode) is restored
MarkFn = Callable[[dict[str, Any] | None], None]


@dataclass(frozen=True, kw_only=True)
class ModeControl:
    """Switch the device into its control mode and back.

    ensure() returns the previous option if it had to switch, else None.
    """

    ensure: Callable[[], Awaitable[str | None]]
    set: Callable[[str], Awaitable[None]]


async def run_device_test(
    state: DeviceTestState,
    read: ReadFn,
    run_action: RunFn,
    timings: Timings = Timings(),
    mode: ModeControl | None = None,
    mark: MarkFn | None = None,
    supported: frozenset[str] | set[str] = ALL_ACTIONS,
) -> None:
    """Run the test, updating state in place. Cancelling aborts and restores."""
    state.started = datetime.now(UTC)
    commanded = False

    def remember() -> None:
        if mark is not None:
            mark({"device_id": state.device_id, "started": state.started.isoformat(), "previous_mode": state.previous_mode})

    async def sample_for(phase: Phase, seconds: float) -> list[float]:
        state.phase = phase
        values = []
        end = asyncio.get_running_loop().time() + seconds
        while True:
            power, soc = await read()
            state.samples.append(Sample(at=datetime.now(UTC), phase=phase, battery_power=power, soc=soc))
            if power is not None:
                values.append(power)
            if asyncio.get_running_loop().time() + timings.sample > end:
                return values
            await asyncio.sleep(timings.sample)

    async def restore() -> None:
        state.phase = "restore"
        state.message = "Setze auf Eigenverbrauch zurück …"
        calls = await run_action("auto", {})
        state.calls += calls
        # A value the device already holds is skipped, which restores as well
        state.restored = all(c.status in ("done", "skipped") for c in calls)
        if mode is not None and state.previous_mode is not None:
            state.message = f"Schalte zurück in „{state.previous_mode}“ …"
            await mode.set(state.previous_mode)
            state.previous_mode_restored = True
        if state.restored and mark is not None:
            mark(None)

    try:
        state.message = "Messe Ausgangszustand …"
        baseline = await sample_for("baseline", timings.baseline)
        if not baseline:
            raise ExecutorError("Keine Batterieleistung lesbar – Test nicht möglich")
        action, power, expectation = choose_test(median(baseline), supported)
        state.test_action, state.test_power, state.expectation = action, power, expectation

        # From here on every exit path restores window and mode; the marker survives a
        # crash of the app so the user can restore later
        commanded = True
        remember()
        if mode is not None:
            state.message = "Prüfe Betriebsmodus …"
            state.previous_mode = await mode.ensure()
            remember()
        state.message = f"Testbefehl aktiv: {expectation}"
        state.calls += await run_action(action, {"P": power})
        await sample_for("command", timings.settle)
        settled = await sample_for("command", timings.hold - timings.settle)
        state.control_works, state.sign_correct = evaluate(action, power, settled)

        await restore()
        commanded = False
        # Readings after the restore are shown; self-consumption has no single expected value
        state.message = "Prüfe Verhalten nach dem Zurücksetzen …"
        await sample_for("verify_restore", timings.verify_restore)
        state.phase = "done"
        state.message = "Test abgeschlossen"
    except asyncio.CancelledError:
        state.phase = "aborted"
        state.message = "Abgebrochen"
        if commanded:
            await _restore_safely(state, restore)
        raise
    except Exception as err:  # noqa: BLE001 - any failure must end in a restore attempt
        _LOGGER.exception("Device test failed")
        state.phase = "failed"
        state.error = str(err)
        state.message = f"Fehler: {err}"
        if commanded:
            await _restore_safely(state, restore)
    finally:
        state.finished = datetime.now(UTC)


async def _restore_safely(state: DeviceTestState, restore: Callable[[], Awaitable[None]]) -> None:
    phase = state.phase
    try:
        await restore()
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Restore after device test failed")
        state.restored = False
        state.error = f"Zurücksetzen fehlgeschlagen: {err}"
    state.phase = phase


def state_response(state: DeviceTestState) -> dict[str, Any]:
    """Serialize the test state for the API."""
    return {
        "device_id": state.device_id,
        "phase": state.phase,
        "running": state.running,
        "message": state.message,
        "test_action": state.test_action,
        "test_power": state.test_power,
        "expectation": state.expectation,
        "samples": [
            {"at": s.at.isoformat(), "phase": s.phase, "battery_power": s.battery_power, "soc": s.soc}
            for s in state.samples
        ],
        "control_works": state.control_works,
        "sign_correct": state.sign_correct,
        "restored": state.restored,
        "previous_mode": state.previous_mode,
        "previous_mode_restored": state.previous_mode_restored,
        "calls": [
            {"entity_id": c.entity_id, "service": f"{c.domain}.{c.service}", "data": c.data, "status": c.status}
            for c in state.calls
        ],
        "error": state.error,
    }


class RestoreMarker:
    """File in the data directory that exists while a device test may have left the device changed."""

    def __init__(self, data_dir: Path) -> None:
        """Initialize the marker."""
        self._path = data_dir / "device_test_active.json"

    def get(self) -> dict[str, Any] | None:
        """Return the pending restore, None if nothing is left to undo."""
        try:
            return json.loads(self._path.read_text())
        except FileNotFoundError:
            return None
        except ValueError:
            # A half written marker still means: restore
            return {"device_id": None, "started": None, "previous_mode": None}

    def set(self, marker: dict[str, Any] | None) -> None:
        """Store or clear the marker (written atomically)."""
        if marker is None:
            self._path.unlink(missing_ok=True)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(marker))
        tmp.replace(self._path)
