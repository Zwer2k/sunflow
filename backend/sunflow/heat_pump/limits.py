"""Limits of the set value (design/heat_pump.md, 8.3) and the profile's parameters of the protection layer (8.4).

- Range of the set value: the start limits of the heating surface type (HEATING_SURFACES), never
  outside the written entity's own range (manifest min/max).
- Rounding to the entity's step (manifest), always the safe way where it protects (up).
- Plausibility: a flow target further than PLAUSIBLE_BAND_K from the effective setpoint, and from the
  curve's value when the curve is used, is not written.
The heat output range is learned in learned.py.
"""

from dataclasses import dataclass
import math
from typing import Any

from .defaults import (
    BLOCK_MIN_S,
    DEADBAND_K,
    DEFAULT_SETPOINT_STEP_K,
    HEATING_SURFACE_DEFAULT,
    HEATING_SURFACES,
    PLAUSIBLE_BAND_K,
    SHUTDOWN_RESOLUTION_K,
    POWER_MAX_KW_DEFAULT,
    POWER_MIN_KW_DEFAULT,
    START_JUMP_K,
    START_MARGIN_K,
    START_SETTLE_S,
    STEADY_SWING_K,
    STOP_MARGIN_K,
)
from .manifest import entities_by_key
from .states import FIXED, duration

_EPS = 1e-9


def _number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _optional(value: Any) -> float | None:
    number = _number(value, math.nan)
    return number if math.isfinite(number) else None


@dataclass(frozen=True, kw_only=True)
class ControlSettings:
    """What the protection layer takes from the profile and the manifest of one heat pump."""

    start_margin: float = START_MARGIN_K
    stop_margin: float = STOP_MARGIN_K
    # Start values of the learned values (8.4)
    steady_swing: float = STEADY_SWING_K
    start_jump: float = START_JUMP_K
    start_settle_s: float = START_SETTLE_S
    # Heat output range (start values, W)
    power_min_w: float = POWER_MIN_KW_DEFAULT * 1000
    power_max_w: float = POWER_MAX_KW_DEFAULT * 1000
    block_min_s: float = BLOCK_MIN_S
    # The heat pump has the room thermostat input (role heating_block)
    has_block: bool = True
    deadband: float = DEADBAND_K
    # Step and range of the written entity (manifest); None when it names none
    step: float = DEFAULT_SETPOINT_STEP_K
    entity_min: float | None = None
    entity_max: float | None = None

    @classmethod
    def from_profile(cls, profile: dict[str, Any], manifest: dict[str, Any] | None) -> "ControlSettings":
        """The profile's flow_control and the written entity of the strategy fixed; defaults where missing."""
        flow = profile.get("flow_control") or {}
        rules = flow.get("compressor_rules") or {}
        learned = flow.get("learned_defaults") or {}
        power = flow.get("heat_power") or {}
        block = flow.get("heating_block") or {}
        role = ((flow.get("strategies") or {}).get(FIXED, {}).get("write") or {}).get("role")
        key = (profile.get("roles", {}).get(role) or {}).get("key") if role else None
        entity = entities_by_key(manifest).get(key) if key else None
        step = _number((entity or {}).get("step"), 0.0)
        return cls(
            start_margin=_number(rules.get("start_margin"), START_MARGIN_K),
            stop_margin=_number(rules.get("stop_margin"), STOP_MARGIN_K),
            steady_swing=_number(learned.get("steady_swing"), STEADY_SWING_K),
            start_jump=_number(learned.get("start_jump"), START_JUMP_K),
            start_settle_s=duration(learned.get("start_settle"), START_SETTLE_S).total_seconds(),
            power_min_w=_number(power.get("min"), POWER_MIN_KW_DEFAULT) * 1000,
            power_max_w=_number(power.get("max"), POWER_MAX_KW_DEFAULT) * 1000,
            block_min_s=duration(block.get("min_duration"), BLOCK_MIN_S).total_seconds(),
            has_block="heating_block" in profile.get("roles", {}) and bool(block),
            deadband=_number((flow.get("writes") or {}).get("deadband"), DEADBAND_K),
            step=step if step > 0 else DEFAULT_SETPOINT_STEP_K,
            entity_min=_optional((entity or {}).get("min")),
            entity_max=_optional((entity or {}).get("max")),
        )

    @property
    def shutdown_guard(self) -> float:
        """What a lower bound against the shutdown keeps below it: stop margin − one tenth (3.0 → 2.9)."""
        return self.stop_margin - SHUTDOWN_RESOLUTION_K


def setpoint_range(surface: str | None, settings: ControlSettings) -> tuple[float, float]:
    """[min, max] of the set value: start limits of the heating surface type within the entity's range."""
    limits = HEATING_SURFACES.get(surface or "", HEATING_SURFACES[HEATING_SURFACE_DEFAULT])
    low, high = limits["min_c"], limits["max_c"]
    if settings.entity_min is not None:
        low = max(low, settings.entity_min)
    if settings.entity_max is not None:
        high = min(high, settings.entity_max)
    return low, max(low, high)


def round_up(value: float, step: float) -> float:
    """Up to the step (safe against the shutdown); exact multiples stay."""
    return round(math.ceil(value / step - _EPS) * step, 3)


def round_down(value: float, step: float) -> float:
    """Down to the step (stays within an upper limit)."""
    return round(math.floor(value / step + _EPS) * step, 3)


def round_near(value: float, step: float) -> float:
    """To the nearest step."""
    return round(round(value / step) * step, 3)


def clamp(value: float, low: float, high: float, step: float) -> float:
    """Within [low, high] on the step grid: never above high, never below low."""
    return min(max(value, round_up(low, step)), round_down(high, step))


def implausible(target_c: float, effective_c: float | None, curve_c: float | None) -> float | None:
    """How far the flow target lies from the furthest reference when beyond PLAUSIBLE_BAND_K (signed), else None."""
    worst = None
    for reference in (effective_c, curve_c):
        if reference is None:
            continue
        distance = target_c - reference
        if abs(distance) > PLAUSIBLE_BAND_K + _EPS and (worst is None or abs(distance) > abs(worst)):
            worst = distance
    return worst
