"""Room correction and setpoint changes (design/heat_pump.md, 8.2).

The correction is a PI controller in incremental form, added to the flow target:
    Δcorrection = Kp/Ti · e · Δt (only outside the deadband) − Kp · Δroom,   e = target − room
The proportional part acts on the room temperature itself, so a setpoint change causes no jump. Ti
depends on the heating surface type (defaults.correction_ti_h); in the incremental form a change of Ti
only changes the increments from then on, never the correction itself (no jump).
A step is taken when the room temperature changed, at the latest CORRECTION_MAX_STEP after the last
one (then only the integral part); Δt is never longer than that. While frozen (no valid heating,
setpoint change, no room data) nothing changes, and what the room did meanwhile is dropped, not made
up later. The correction is clamped to ±CORRECTION_LIMIT (an increment beyond is cut: anti-windup).

After a setpoint change the correction stays frozen until the room reached the new target within the
deadband (raising: room ≥ setpoint − deadband, lowering: room ≤ setpoint + deadband), at most
SETPOINT_SETTLE_MAX. A new change meanwhile starts anew with its own target, direction and time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from .defaults import (
    CORRECTION_KP,
    CORRECTION_LIMIT_K,
    CORRECTION_MAX_STEP_S,
    CORRECTION_TI_H,
    ROOM_DEADBAND_K,
    SETPOINT_CHANGE_MIN_K,
    SETPOINT_SETTLE_MAX_S,
)

_LOGGER = logging.getLogger(__name__)

MAX_STEP = timedelta(seconds=CORRECTION_MAX_STEP_S)
SETTLE_MAX = timedelta(seconds=SETPOINT_SETTLE_MAX_S)
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
        """Move the correction by shift_k (jump protection at a source change, 8.1), within its limit.

        The room reference stays: the shift is no step of the controller.
        """
        self.value_k = max(-CORRECTION_LIMIT_K, min(CORRECTION_LIMIT_K, self.value_k + shift_k))

    def step(
        self, room_c: float, target_c: float, now: datetime, block_up: bool = False, block_down: bool = False,
        ti_h: float = CORRECTION_TI_H,
    ) -> bool:
        """One step if the room changed or the longest step passed; True if a step was taken.

        ti_h: integral time of the heating surface type (8.2); not positive counts as the default.

        block_up / block_down: the flow target is at a limit (8.3) in that direction - an increment
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


@dataclass(frozen=True, kw_only=True)
class Settling:
    """A setpoint change the room has not followed yet."""

    target_c: float
    # +1 raised, -1 lowered
    direction: int
    since: datetime

    def reached(self, room_c: float) -> bool:
        """The room is at the new target within the deadband, coming from the old side."""
        if self.direction > 0:
            return room_c >= self.target_c - ROOM_DEADBAND_K - _EPS
        return room_c <= self.target_c + ROOM_DEADBAND_K + _EPS

    def as_dict(self) -> dict[str, Any]:
        """For the state file and the status."""
        return {
            "target_c": self.target_c,
            "direction": "up" if self.direction > 0 else "down",
            "since": self.since.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Settling | None":
        """From the state file; None if unreadable."""
        try:
            return cls(
                target_c=float(data["target_c"]),
                direction=1 if data["direction"] == "up" else -1,
                since=datetime.fromisoformat(data["since"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


class SetpointWatch:
    """Notices setpoint changes (from Home Assistant, the UI or the API) and how long the room follows them."""

    def __init__(self, last_c: float | None = None, settling: Settling | None = None) -> None:
        """Start from the stored last setpoint; None: the first one seen is no change."""
        self.last_c = last_c
        self.settling = settling

    def update(self, setpoint_c: float, room_c: float | None, now: datetime) -> Settling | None:
        """The change being followed now, None if the correction need not wait for the room."""
        if self.last_c is None:
            self.last_c = setpoint_c
        elif abs(setpoint_c - self.last_c) >= SETPOINT_CHANGE_MIN_K - _EPS:
            self.settling = Settling(target_c=setpoint_c, direction=1 if setpoint_c > self.last_c else -1, since=now)
            self.last_c = setpoint_c
        if self.settling is not None:
            if room_c is not None and self.settling.reached(room_c):
                self.settling = None
            elif now - self.settling.since >= SETTLE_MAX:
                _LOGGER.info(
                    "Room did not reach the setpoint %.1f °C within %s; the correction goes on",
                    self.settling.target_c,
                    SETTLE_MAX,
                )
                self.settling = None
        return self.settling
