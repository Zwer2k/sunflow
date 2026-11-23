"""Limits of the set value (design/heat_pump.md, 10) and the profile's parameters of the protection layer (11).

- Range of the set value: the user's setting per heat pump (flow_min_c/flow_max_c), else the start limits
  of the heating surface type (HEATING_SURFACES); never outside the written entity's own range
  (manifest min/max).
- Rounding to the entity's step (manifest), always the safe way where it protects (up).
The heat output range is learned in learned.py; the write budget is in budget.py.
"""

from dataclasses import dataclass, field
import math
from typing import Any

from .budget import BudgetLimits
from .defaults import (
    ADOPT_DELAY_S,
    CURVE_SLOPE_RANGE,
    CURVE_SLOPE_STEP,
    BLOCK_MIN_S,
    DEADBAND_K,
    DEFAULT_SETPOINT_STEP_K,
    HEATING_SURFACE_DEFAULT,
    HEATING_SURFACES,
    POWER_MAX_KW_DEFAULT,
    POWER_MIN_KW_DEFAULT,
    START_MARGIN_K,
    STEADY_SWING_K,
    STOP_MARGIN_K,
    WRITE_INTERVAL_FLOOR_S,
    WRITE_MIN_INTERVAL_S,
    WRITE_PROTECT_INTERVAL_S,
)
from .manifest import entities_by_key

# Role of the heat pump's own heating curve slope (its entity's range is the slope's range, 8)
SLOPE_ROLE = "heating_curve_slope"
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
    # Start value of the learned swing (11)
    steady_swing: float = STEADY_SWING_K
    # Heat output range (start values, W)
    power_min_w: float = POWER_MIN_KW_DEFAULT * 1000
    power_max_w: float = POWER_MAX_KW_DEFAULT * 1000
    block_min_s: float = BLOCK_MIN_S
    # The heat pump has the room thermostat input (role heating_block)
    has_block: bool = True
    deadband: float = DEADBAND_K
    # Minimum spacing of two writes of the set value (12): normal and for protection steps
    min_interval_s: float = WRITE_MIN_INTERVAL_S
    protect_interval_s: float = WRITE_PROTECT_INTERVAL_S
    # Write budget of the set value (12): per day, emergency quota for protection steps, per hour
    budget: BudgetLimits = field(default_factory=BudgetLimits)
    # Step and range of the written entity (manifest); None when it names none
    step: float = DEFAULT_SETPOINT_STEP_K
    entity_min: float | None = None
    entity_max: float | None = None
    # The user's range of the set value (device setting); None: the heating surface type's start limit
    user_min: float | None = None
    user_max: float | None = None
    # Time the heat pump needs to take a written set value over (profile adopt_delay, 12)
    adopt_delay_s: float = ADOPT_DELAY_S
    # Range and step of the heating curve's slope (8): those of the heat pump's own slope entity (manifest),
    # else the defaults
    slope_min: float = CURVE_SLOPE_RANGE[0]
    slope_max: float = CURVE_SLOPE_RANGE[1]
    slope_step: float = CURVE_SLOPE_STEP

    @property
    def slope_range(self) -> tuple[float, float]:
        return self.slope_min, self.slope_max

    @classmethod
    def from_profile(
        cls, profile: dict[str, Any], manifest: dict[str, Any] | None, user_min: float | None = None, user_max: float | None = None,
    ) -> "ControlSettings":
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
        writes = flow.get("writes") or {}
        slope_key = (profile.get("roles", {}).get(SLOPE_ROLE) or {}).get("key")
        slope = entities_by_key(manifest).get(slope_key) if slope_key else None
        slope_min, slope_max = _optional((slope or {}).get("min")), _optional((slope or {}).get("max"))
        if slope_min is None or slope_max is None or slope_min >= slope_max:
            slope_min, slope_max = CURVE_SLOPE_RANGE
        slope_step = _number((slope or {}).get("step"), 0.0)
        return cls(
            start_margin=_number(rules.get("start_margin"), START_MARGIN_K),
            stop_margin=_number(rules.get("stop_margin"), STOP_MARGIN_K),
            steady_swing=_number(learned.get("steady_swing"), STEADY_SWING_K),
            power_min_w=_number(power.get("min"), POWER_MIN_KW_DEFAULT) * 1000,
            power_max_w=_number(power.get("max"), POWER_MAX_KW_DEFAULT) * 1000,
            block_min_s=duration(block.get("min_duration"), BLOCK_MIN_S).total_seconds(),
            has_block="heating_block" in profile.get("roles", {}) and bool(block),
            deadband=_number(writes.get("deadband"), DEADBAND_K),
            # Never shorter than the heat pump needs to take a value over (12)
            min_interval_s=max(duration(writes.get("min_interval"), WRITE_MIN_INTERVAL_S).total_seconds(), WRITE_INTERVAL_FLOOR_S),
            protect_interval_s=max(duration(writes.get("protect_interval"), WRITE_PROTECT_INTERVAL_S).total_seconds(), WRITE_INTERVAL_FLOOR_S),
            budget=BudgetLimits.from_writes(writes),
            step=step if step > 0 else DEFAULT_SETPOINT_STEP_K,
            entity_min=_optional((entity or {}).get("min")),
            entity_max=_optional((entity or {}).get("max")),
            user_min=user_min,
            user_max=user_max,
            adopt_delay_s=duration(flow.get("adopt_delay"), ADOPT_DELAY_S).total_seconds(),
            slope_min=slope_min,
            slope_max=slope_max,
            slope_step=slope_step if slope_step > 0 else CURVE_SLOPE_STEP,
        )


def surface_range(surface: str | None) -> tuple[float, float]:
    """Start limits of the set value for a heating surface type (the default type for an unknown one)."""
    limits = HEATING_SURFACES.get(surface or "", HEATING_SURFACES[HEATING_SURFACE_DEFAULT])
    return limits["min_c"], limits["max_c"]


def setpoint_range(surface: str | None, settings: ControlSettings) -> tuple[float, float]:
    """[min, max] of the set value: the user's setting, else the start limits of the heating surface type;
    within the entity's range. The upper bound protects the heating surfaces (screed): when the lower one
    lies above it (e.g. a user maximum below the type's start minimum), the range collapses to the upper."""
    low, high = surface_range(surface)
    if settings.user_min is not None:
        low = settings.user_min
    if settings.user_max is not None:
        high = settings.user_max
    low = min(low, high)
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
