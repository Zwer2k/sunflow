"""Fixed values of the flow temperature control (design/heat_pump.md and heat_pump_device.md)."""

# Deadband of writing (12): a new set value is written only when it differs by more than this from the
# written one. A profile may override it in flow_control.writes.deadband.
DEADBAND_K = 0.5
# Minimum spacing of two writes of the set value (12), fallbacks when the profile leaves them out
# (flow_control.writes.min_interval, protect_interval): normal writes and protection steps. A profile
# value below WRITE_INTERVAL_FLOOR_S counts as that: the heat pump takes a new value over only after
# 30-60 s, a shorter spacing would write before the effect of the last value shows.
WRITE_MIN_INTERVAL_S = 5 * 60
WRITE_PROTECT_INTERVAL_S = 2 * 60
WRITE_INTERVAL_FLOOR_S = 60
# Write budget of the set value (12, EepromGuard), fallbacks when the profile leaves them out
# (flow_control.writes.per_day, emergency_per_day, per_hour): normal writes per day, an emergency
# quota on top only for protection steps, and at most this many writes in any 60 minutes.
WRITE_PER_DAY = 48
WRITE_EMERGENCY_PER_DAY = 24
WRITE_PER_HOUR = 6
# Writes kept for counting: a local day (up to 25 h at a change of daylight saving time) and the last hour
WRITE_TIMES_KEEP_S = 26 * 3600
# Coarsest step of the written set value (heat_pump_device.md 1.6): with a coarser one the rounding alone exceeds the
# deadband (e.g. step 1.0 of a firmware without tenths). Firmware property, so not the profile's deadband.
MAX_SETPOINT_STEP_K = DEADBAND_K
# The heat pump limits the flow itself (section 4, e.g. T-Vorlauf Nacht in a setback time): the
# effective setpoint stays more than the deadband below the set value for longer than this. Longer
# than taking over a new set value (30-60 s), counted from Sunflow's own write.
SELF_LIMIT_AFTER_S = 5 * 60
# Fallbacks when a profile leaves them out (heat_pump_device.md 2)
SETTLE_AFTER_BYPASS_S = 10 * 60
FROZEN_AFTER_S = 10 * 60

# Room setpoint of the climate entity "Haus" (6): range, default, step of the entity and the
# resolution Sunflow stores. Not heating is mode off, not a low setpoint; values outside are refused.
ROOM_SETPOINT_MIN_C = 18.0
ROOM_SETPOINT_MAX_C = 27.0
ROOM_SETPOINT_DEFAULT_C = 21.0
ROOM_SETPOINT_STEP_C = 0.5
ROOM_SETPOINT_RESOLUTION_C = 0.1
# Margin of the planning around the room setpoint (6, 14): preheat up to +PREHEAT, lower down to
# -LOWER; each in 0..ROOM_MARGIN_MAX_K, stored to ROOM_SETPOINT_RESOLUTION_C. A missing or broken value
# counts as its start value (no error on loading); the API refuses values outside.
ROOM_PREHEAT_DEFAULT_K = 1.0
ROOM_LOWER_DEFAULT_K = 0.0
ROOM_MARGIN_MAX_K = 3.0
ROOM_MARGIN_STEP_K = 0.1
# Planning with the house (14): € per K and hour the planned room temperature lies away
# from the setpoint within the margin, up or down alike - small against a real saving, so the plan stays
# on the setpoint without one. Not a setting.
PLAN_DEVIATION_EUR_PER_KH = 0.05
# Below the margin: far above what heating a kelvin costs (practically hard); above it: dear, but the
# sun may push the house there
PLAN_COLD_EUR_PER_KH = 50.0
PLAN_WARM_EUR_PER_KH = 0.2
# What the heat pump draws to hold the setpoint is taken out of the house load forecast (which learned
# it), at most this share of the forecast per slot
PLAN_MAX_LOAD_SHARE = 0.7
# The heat pump's last sample counts for the planning this long (room temperature, outside, program)
PLAN_SAMPLE_MAX_AGE_S = 10 * 60

# Room correction (9): proportional part on the room temperature (the same for every heating surface
# type), integral time of the default type (each type's own in HEATING_SURFACES, ti_h), deadband of the
# integral part (also of the setpoint change), limit of the correction and the longest time step counted
# after a gap
CORRECTION_KP = 1.0
CORRECTION_TI_H = 6.0
ROOM_DEADBAND_K = 0.2
CORRECTION_LIMIT_K = 3.0
CORRECTION_MAX_STEP_S = 30 * 60
# Outside temperature for the control (7): the measured value is averaged over OUTSIDE_MEAN_S
OUTSIDE_MEAN_S = 15 * 60

# Fast change (6, airing, cooking ...), per room; start values of the setting house.fast_change: an event
# starts when the room changed by at least FAST_START_K within FAST_START_MIN, down or up (the value before
# counts as long as the sensor showed it). In kelvin, not in steps of the sensor, so it works the same for
# sensors reporting in 0.1, 0.3 or 0.5 K. It ends once the fast part is over - the room moved by at most
# FAST_SETTLE_K over the last FAST_SETTLE_MIN - at the latest after FAST_MAX_H; the slow rest counts again.
# Meanwhile the room does not count for the room temperature and the correction is frozen. Ended events are
# shown for VENT_RECENT_KEEP, at most VENT_RECENT_MAX of them.
FAST_START_K = 0.8
FAST_START_MIN = 20
FAST_SETTLE_K = 0.5
FAST_SETTLE_MIN = 30
FAST_MAX_H = 2.0
# Allowed ranges of the setting
FAST_START_K_RANGE = (0.2, 5.0)
FAST_START_MIN_RANGE = (5, 120)
FAST_SETTLE_K_RANGE = (0.1, 5.0)
FAST_SETTLE_MIN_RANGE = (10, 180)
FAST_MAX_H_RANGE = (0.5, 12.0)
VENT_RECENT_KEEP_S = 24 * 3600
VENT_RECENT_MAX = 20

# Type of the heating surfaces (setting house.heating_surface): start slope of the heating curve (8) when the
# heat pump does not tell its own, start limits of the set value (10) and integral time of the correction (9;
# slower surfaces react later, so the correction integrates more slowly).
HEATING_SURFACE_DEFAULT = "underfloor"
HEATING_SURFACES: dict[str, dict[str, float]] = {
    "underfloor": {"slope": 0.6, "min_c": 25.0, "max_c": 40.0, "ti_h": CORRECTION_TI_H},
    "mixed": {"slope": 1.0, "min_c": 25.0, "max_c": 50.0, "ti_h": 4.0},
    "radiator": {"slope": 1.4, "min_c": 30.0, "max_c": 55.0, "ti_h": 2.0},
}


def _surface(surface: str | None) -> dict[str, float]:
    return HEATING_SURFACES.get(surface or "", HEATING_SURFACES[HEATING_SURFACE_DEFAULT])


def curve_slope(surface: str | None) -> float:
    """Start slope of the heating curve for a heating surface type; the default type for an unknown one."""
    return _surface(surface)["slope"]


def correction_ti_h(surface: str | None) -> float:
    """Integral time of the room correction (h) for a heating surface type; the default type for an unknown one."""
    return _surface(surface)["ti_h"]


# Heating curve (8), learned from heat_pump_samples every CURVE_REFRESH from the last CURVE_HISTORY_DAYS
# (half-life CURVE_HALF_LIFE_DAYS). A point is a 5 minute row (worth 1/12 hour) of valid heating, no fast
# change, room within CURVE_ROOM_BAND_K of its setpoint. The fit is pulled to the start curve as if
# CURVE_PRIOR_HOURS hours on it said so, spread over CURVE_PRIOR_X_K of R − A; slope within the range of the
# heat pump's own slope entity (manifest), else CURVE_SLOPE_RANGE; shift within ±CURVE_SHIFT_RANGE_K.
# CURVE_SLOPE_LIMIT only bounds what config.json may hold.
CURVE_REFRESH_S = 3600
CURVE_HISTORY_DAYS = 28
CURVE_HALF_LIFE_DAYS = 14
CURVE_ROOM_BAND_K = 0.3
CURVE_PRIOR_HOURS = 24
CURVE_PRIOR_X_K = (5.0, 15.0, 25.0)
CURVE_SLOPE_RANGE = (0.0, 3.0)
CURVE_SLOPE_LIMIT = 10.0
CURVE_SLOPE_STEP = 0.01
CURVE_SHIFT_RANGE_K = 10.0

# Limits (10). The set value stays within the start limits of the heating surface type (HEATING_SURFACES)
# and the entity's own range (manifest min/max). Without a step in the manifest the coarsest allowed one
# counts (rounding then still goes the safe way).
# Range a user setting of the set value limits (DeviceConfig.flow_min_c/flow_max_c) may name at all; within
# it the entity's range from the manifest still narrows the setting
FLOW_LIMIT_LOW_C = 15.0
FLOW_LIMIT_HIGH_C = 70.0
DEFAULT_SETPOINT_STEP_K = MAX_SETPOINT_STEP_K
# Heat output range of the heat pump (kW): the profile's start values until learned from steady running
# in heat_pump_samples (POWER_LEARN_DAYS, at least POWER_MIN_ROWS rows): the minimum is a low quantile of
# the heat; the maximum is only raised by a high quantile (mild days do not show it). A heat within
# POWER_AT_LIMIT_SHARE of a limit counts as at the limit.
POWER_MIN_KW_DEFAULT = 0.6
POWER_MAX_KW_DEFAULT = 6.0
POWER_LEARN_DAYS = 28
POWER_MIN_ROWS = 288
POWER_MIN_QUANTILE = 0.05
POWER_MAX_QUANTILE = 0.99
POWER_AT_LIMIT_SHARE = 0.05
# Heat counted for the output range (W)
POWER_HEAT_MIN_W = 200.0
POWER_HEAT_MAX_W = 30_000.0

# Protection layer (11). Fallbacks when the profile leaves them out (compressor_rules,
# learned_defaults, heating_block.min_duration).
START_MARGIN_K = 3.0
STOP_MARGIN_K = 3.0
STEADY_SWING_K = 0.5
BLOCK_MIN_S = 20 * 60
# Restart: the compressor off this long although heat is wanted (also after hot water or defrost) - Sunflow
# raises the set value to Tv + start margin. Heat is wanted while the room is below its target + RESTART_DEMAND_K.
RESTART_AFTER_S = 5 * 60
RESTART_DEMAND_K = 0.5
# After a start the set value is left alone this long, then lowered to the flow target in one step
LOWER_AFTER_S = 10 * 60
# The shutdown bound counts the peak of Tv over the last PEAK_LOOKBACK_S of running
PEAK_LOOKBACK_S = 5 * 60
# Wanted off (block): the room is BLOCK_ROOM_ABOVE_K above its target; released when back within ROOM_DEADBAND_K
BLOCK_ROOM_ABOVE_K = 0.5
# Steady swing learned from heat_pump_samples: rows of steady running (the row and SWING_STEADY_ROWS before
# with the compressor on in at least STEADY_COMPRESSOR_SHARE of their minutes, no start), at least SWING_MIN_ROWS,
# their SWING_QUANTILE of tv_max − tv.
SWING_MIN_ROWS = 288
SWING_QUANTILE = 0.95
SWING_STEADY_ROWS = 3
STEADY_COMPRESSOR_SHARE = 0.99
# Stop margin learned from the compressor stops of the last STOP_LEARN_DAYS: a stop is classified by what
# follows within STOP_CLASSIFY_S (defrost, hot water) or the block; the unexplained ones with Tv above the
# effective setpoint within STOP_MARGIN_RANGE_K count, their STOP_QUANTILE from STOP_MIN_EVENTS on (a margin
# above the profile's from STOP_MIN_EVENTS_ABOVE). The status shows the latest STOPS_SHOWN stops.
STOP_LEARN_DAYS = 14
STOP_CLASSIFY_S = 10 * 60
STOP_MARGIN_RANGE_K = (0.5, 8.0)
STOP_QUANTILE = 0.1
STOP_MIN_EVENTS = 5
STOP_MIN_EVENTS_ABOVE = 10
STOPS_SHOWN = 30
# Shown in the status: the latest planned writes
GUARD_RECENT_MAX = 20

# Foreign writers (12). A change of the set value by someone else starts an episode
# (journal once); it ends FOREIGN_EPISODE_S after the last such change. A change to the value Sunflow
# wrote itself counts as its own within OWN_WRITE_CONFIRM_S of that write (from part 4.7).
FOREIGN_EPISODE_S = 24 * 3600
OWN_WRITE_CONFIRM_S = 10 * 60

# Writing for real (12): the heat pump takes a written set value over within ADOPT_DELAY_S
# (fallback when the profile leaves out flow_control.adopt_delay); after it Sunflow checks the real
# Max T-Vorlauf and the effective setpoint. The room thermostat block Sunflow switched is its own while the
# real state follows within OWN_WRITE_CONFIRM_S.
ADOPT_DELAY_S = 60

# House model from heat_pump_samples (14): a day counts for the coverage when it holds at least
# HOUSE_SAMPLES_DAY_MIN_S of valid heating without ventilation; an hour is used with at least
# HOUSE_SAMPLES_HOUR_MIN_ROWS of its 5 minute rows.
HOUSE_SAMPLES_DAY_MIN_S = 3600
HOUSE_SAMPLES_HOUR_MIN_ROWS = 10
