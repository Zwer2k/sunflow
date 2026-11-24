"""Room correction (design/heat_pump.md, 9).

The correction is a PI controller in incremental form, added to the flow target:
    Δcorrection = Kp/Ti · e · Δt (only outside the deadband) − Kp · Δroom,   e = target − room
The proportional part acts on the room temperature itself, so a setpoint change causes no jump. Ti
depends on the heating surface type (defaults.correction_ti_h); in the incremental form a change of Ti
only changes the increments from then on, never the correction itself (no jump).
A step is taken when the room temperature changed, at the latest CORRECTION_MAX_STEP after the last
one (then only the integral part); Δt is never longer than that. While frozen (no valid heating, no
room data) nothing changes, and what the room did meanwhile is dropped, not made up later. The
correction is clamped to ±CORRECTION_LIMIT (an increment beyond is cut: anti-windup).
"""

from datetime import datetime, timedelta

from .defaults import (
    CORRECTION_KP,
    CORRECTION_LIMIT_K,
    CORRECTION_MAX_STEP_S,
    CORRECTION_TI_H,
    ROOM_DEADBAND_K,
)

MAX_STEP = timedelta(seconds=CORRECTION_MAX_STEP_S)
_EPS = 1e-9


class Correction:
    """The correction of one heat pump and strategy, in K added to the flow target."""

    def __init__(self, value_k: float = 0.0) -> None:
        """Start at a stored value; the first step after (re)start only takes the room as reference."""
        self.value_k = max(-CORRECTION_LIMIT_K, min(CORRECTION_LIMIT_K, value_k))
        self._room: float | None = None
        self._at: datetime | None = None

    def hold(self, room_c: float | None, now: datetime) -> None:
        """Frozen: the room's changes until now are dropped."""
        self._room, self._at = room_c, now

    def rebase(self, room_c: float | None) -> None:
        """The room temperature is made of other rooms now (one left out or back): new reference, no step."""
        if self._room is not None:
            self._room = room_c

    def shift(self, shift_k: float) -> None:
        """Move the correction by shift_k (the heating curve changed, 8: no jump of the flow target), within its limit.

        The room reference stays: the shift is no step of the controller.
        """
        self.value_k = max(-CORRECTION_LIMIT_K, min(CORRECTION_LIMIT_K, self.value_k + shift_k))

    def step(
        self, room_c: float, target_c: float, now: datetime, block_up: bool = False, block_down: bool = False,
        ti_h: float = CORRECTION_TI_H,
    ) -> bool:
        """One step if the room changed or the longest step passed; True if a step was taken.

        ti_h: integral time of the heating surface type (9); not positive counts as the default.

        block_up / block_down: the flow target is at a limit (10) in that direction - an increment
        towards it is dropped like a frozen step (anti-windup), one away from it counts.
        """
        if self._room is None or self._at is None:
            self.hold(room_c, now)
            return False
        elapsed = now - self._at
        changed = abs(room_c - self._room) > _EPS
        if not changed and elapsed < MAX_STEP:
            return False
        hours = max(min(elapsed, MAX_STEP), timedelta(0)).total_seconds() / 3600
        error = target_c - room_c
        ti = ti_h if ti_h > 0 else CORRECTION_TI_H
        integral = CORRECTION_KP / ti * error * hours if abs(error) > ROOM_DEADBAND_K + _EPS else 0.0
        proportional = -CORRECTION_KP * (room_c - self._room)
        increment = integral + proportional
        if (increment > _EPS and block_up) or (increment < -_EPS and block_down):
            self.hold(room_c, now)
            return False
        self.value_k = max(-CORRECTION_LIMIT_K, min(CORRECTION_LIMIT_K, self.value_k + integral + proportional))
        self._room, self._at = room_c, now
        return True
