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
import logging
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
    calls: list[PlannedCall] = field(default_factory=list)
    error: str | None = None
    started: datetime | None = None
    finished: datetime | None = None

    @property
    def running(self) -> bool:
        """Return True while the test is in progress."""
        return self.phase in ("baseline", "command", "restore", "verify_restore")


def choose_test(baseline_power: float) -> tuple[str, float, str]:
    """Pick action, power and expectation from the median baseline power (+ = charge)."""
    if baseline_power > ACTIVE_THRESHOLD_W:
        return "limit_charge", LIMIT_W, f"Ladeleistung sinkt auf höchstens ~{LIMIT_W} W"
    if baseline_power < -ACTIVE_THRESHOLD_W:
        return "limit_discharge", LIMIT_W, f"Entladeleistung sinkt auf höchstens ~{LIMIT_W} W"
    return "force_charge", FORCE_CHARGE_W, f"Batterie lädt mit mindestens ~{FORCE_CHARGE_W} W"


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
        case _:
            # force_charge: charging means positive with the configured sign
            works = abs(level) >= power - tolerance
            return works, level > 0


ReadFn = Callable[[], Awaitable[tuple[float | None, float | None]]]
RunFn = Callable[[str, dict[str, float]], Awaitable[list[PlannedCall]]]


async def run_device_test(
    state: DeviceTestState,
    read: ReadFn,
    run_action: RunFn,
    timings: Timings = Timings(),
) -> None:
    """Run the test, updating state in place. Cancelling aborts and restores."""
    state.started = datetime.now(UTC)
    commanded = False

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
        state.restored = all(c.status == "done" for c in calls)

    try:
        state.message = "Messe Ausgangszustand …"
        baseline = await sample_for("baseline", timings.baseline)
        if not baseline:
            raise ExecutorError("Keine Batterieleistung lesbar – Test nicht möglich")
        action, power, expectation = choose_test(median(baseline))
        state.test_action, state.test_power, state.expectation = action, power, expectation

        state.message = f"Testbefehl aktiv: {expectation}"
        commanded = True
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
        "calls": [
            {"entity_id": c.entity_id, "service": f"{c.domain}.{c.service}", "data": c.data, "status": c.status}
            for c in state.calls
        ],
        "error": state.error,
    }
