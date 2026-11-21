"""Fixed values of the flow temperature control (design/heat_pump.md, 3.6, 6.2, 6.3, 8.2 and 8.5)."""

# Deadband of writing (8.5): a new set value is written only when it differs by more than this from the
# written one. A profile may override it in flow_control.writes.deadband.
DEADBAND_K = 0.5
# Minimum spacing of two writes of the set value (8.5), fallbacks when the profile leaves them out
# (flow_control.writes.min_interval, protect_interval): normal writes and protection steps. A profile
# value below WRITE_INTERVAL_FLOOR_S counts as that: the heat pump takes a new value over only after
# 30-60 s, a shorter spacing would write before the effect of the last value shows.
WRITE_MIN_INTERVAL_S = 5 * 60
WRITE_PROTECT_INTERVAL_S = 2 * 60
WRITE_INTERVAL_FLOOR_S = 60
# Write budget of the set value (8.5, EepromGuard), fallbacks when the profile leaves them out
# (flow_control.writes.per_day, emergency_per_day, per_hour): normal writes per day, an emergency
# quota on top only for protection steps, and at most this many writes in any 60 minutes.
WRITE_PER_DAY = 48
WRITE_EMERGENCY_PER_DAY = 24
WRITE_PER_HOUR = 6
# Writes kept for counting: a local day (up to 25 h at a change of daylight saving time) and the last hour
WRITE_TIMES_KEEP_S = 26 * 3600
# Coarsest step of the written set value (3.6): with a coarser one the rounding alone exceeds the
# deadband (e.g. step 1.0 of a firmware without tenths). Firmware property, so not the profile's deadband.
MAX_SETPOINT_STEP_K = DEADBAND_K
# The heat pump limits the flow itself (section 4, e.g. T-Vorlauf Nacht in a setback time): the
# effective setpoint stays more than the deadband below the set value for longer than this. Longer
# than taking over a new set value (30-60 s), counted from Sunflow's own write.
SELF_LIMIT_AFTER_S = 5 * 60
# Fallbacks when a profile leaves them out (section 11)
SETTLE_AFTER_BYPASS_S = 10 * 60
FROZEN_AFTER_S = 10 * 60

# Room setpoint of the climate entity "Haus" (6.3): range, default, step of the entity and the
# resolution Sunflow stores. Not heating is mode off, not a low setpoint; values outside are refused.
ROOM_SETPOINT_MIN_C = 18.0
ROOM_SETPOINT_MAX_C = 27.0
ROOM_SETPOINT_DEFAULT_C = 21.0
ROOM_SETPOINT_STEP_C = 0.5
ROOM_SETPOINT_RESOLUTION_C = 0.1

# Room correction (8.2): proportional part on the room temperature (the same for every heating surface
# type), integral time of the default type (each type's own in HEATING_SURFACES, ti_h), deadband of the
# integral part (also of the setpoint change), limit of the correction and the longest time step counted
# after a gap
CORRECTION_KP = 1.0
CORRECTION_TI_H = 6.0
ROOM_DEADBAND_K = 0.2
CORRECTION_LIMIT_K = 3.0
CORRECTION_MAX_STEP_S = 30 * 60
# Setpoint change (8.2): frozen until the room reached the new target, at most this long; smaller
# changes do not count
SETPOINT_SETTLE_MAX_S = 24 * 3600
SETPOINT_CHANGE_MIN_K = 0.1

# Ventilation (6.2), per room: an event starts when the room falls by at least VENT_DROP_K within
# VENT_DROP_WINDOW (the value before counts as long as the sensor showed it; 0.3 K in 30 min is the
# slope of -0.6 K/h). It ends when the room is flat again - changed by at most VENT_FLAT_K over
# VENT_FLAT_WINDOW - at the latest after VENT_MAX. Meanwhile the room does not count for the room
# temperature and the correction is frozen. Ended events are shown for VENT_RECENT_KEEP, at most
# VENT_RECENT_MAX of them.
VENT_DROP_K = 0.3
VENT_DROP_WINDOW_S = 30 * 60
VENT_FLAT_K = 0.1
VENT_FLAT_WINDOW_S = 30 * 60
VENT_MAX_S = 2 * 3600
VENT_RECENT_KEEP_S = 24 * 3600
VENT_RECENT_MAX = 20

# Type of the heating surfaces (8.1, setting house.heating_surface): start value of the exponent n of
# the heating curve Q = K·(Tw − T_room)^n, the start limits of the set value (8.3, used from step 3
# part 3 on) and the integral time of the room correction (8.2, ti_h; slower surfaces react later, so
# the correction integrates more slowly). n is not learned (9.2: optional; kept at the start value).
HEATING_SURFACE_DEFAULT = "underfloor"
HEATING_SURFACES: dict[str, dict[str, float]] = {
    "underfloor": {"n": 1.1, "min_c": 25.0, "max_c": 40.0, "ti_h": CORRECTION_TI_H},
    "mixed": {"n": 1.2, "min_c": 25.0, "max_c": 50.0, "ti_h": 4.0},
    "radiator": {"n": 1.3, "min_c": 30.0, "max_c": 55.0, "ti_h": 2.0},
}


def _surface(surface: str | None) -> dict[str, float]:
    return HEATING_SURFACES.get(surface or "", HEATING_SURFACES[HEATING_SURFACE_DEFAULT])


def curve_exponent(surface: str | None) -> float:
    """Start value of n for a heating surface type; the default type for an unknown one."""
    return _surface(surface)["n"]


def correction_ti_h(surface: str | None) -> float:
    """Integral time of the room correction (h) for a heating surface type; the default type for an unknown one."""
    return _surface(surface)["ti_h"]


# Heating curve (9.2), learned from heat_pump_samples every CURVE_REFRESH: rows of the last
# CURVE_HISTORY_DAYS weighted with a half-life; the last CURVE_HOLDOUT_DAYS are held out for the
# quality. An interval counts when it is valid heating (section 4, no after-run), nobody aired, at
# least CURVE_MIN_ROW_SAMPLES minutes, the compressor ran through it and CURVE_RUN_BEFORE_S before,
# the room temperature is known and the heat plausible (CURVE_HEAT_*_W, mean water temperature at
# least CURVE_DT_MIN_K above the room).
CURVE_REFRESH_S = 3600
CURVE_HISTORY_DAYS = 28
CURVE_HALF_LIFE_DAYS = 14
CURVE_HOLDOUT_DAYS = 2
CURVE_RIDGE = 0.001
CURVE_MIN_ROW_SAMPLES = 4
CURVE_COMPRESSOR_FULL = 0.99
CURVE_RUN_BEFORE_S = 15 * 60
CURVE_HEAT_MIN_W = 200.0
CURVE_HEAT_MAX_W = 30_000.0
CURVE_DT_MIN_K = 0.5
# Too little data: fewer training intervals or days, or fewer held out intervals
CURVE_MIN_INTERVALS = 144
CURVE_MIN_DAYS = 3
CURVE_MIN_HELD = 24
# Plausible: mean water temperature above the room at the median heat, spread at the median heat
CURVE_OVERTEMP_RANGE_K = (1.0, 40.0)
CURVE_SPREAD_RANGE_K = (0.0, 15.0)

# Heat needed now without a house model (8.1): mean heat of the last FEED_HEAT_WINDOW from the minutes
# of valid heating, compressor pauses as 0; with fewer than FEED_HEAT_MIN counted minutes, or a mean
# not above FEED_HEAT_MIN_W, there is no feedforward (source base).
FEED_HEAT_WINDOW_S = 3 * 3600
FEED_HEAT_MIN_S = 2 * 3600
FEED_HEAT_MIN_W = CURVE_HEAT_MIN_W

# Limits (8.3). The set value stays within the start limits of the heating surface type (HEATING_SURFACES)
# and the entity's own range (manifest min/max). Without a step in the manifest the coarsest allowed one
# counts (rounding then still goes the safe way). A flow target further than PLAUSIBLE_BAND_K from the
# effective setpoint (and from the curve's value, when the curve is used) is not plausible: not written.
PLAUSIBLE_BAND_K = 8.0
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

# Protection layer (8.4). Fallbacks when the profile leaves them out (compressor_rules, learned_defaults,
# heating_block.min_duration).
START_MARGIN_K = 3.0
STOP_MARGIN_K = 3.0
STEADY_SWING_K = 0.5
START_JUMP_K = 6.0
START_SETTLE_S = 5 * 60
BLOCK_MIN_S = 20 * 60
# Temperatures have one decimal: a lower bound against the shutdown keeps this below it ("− 2.9")
SHUTDOWN_RESOLUTION_K = 0.1
# Tv is calm ("flat") when it moved by at most steady_swing over this window
TV_FLAT_WINDOW_S = 5 * 60
# Tracking in running operation: the peak of the last TRACK_LOOKBACK counts; a raise goes TRACK_HEADROOM
# above the bound, so a slowly rising Tv does not cause a write every minute
TRACK_LOOKBACK_S = 5 * 60
TRACK_HEADROOM_K = DEADBAND_K
# A start within START_AFTER of the end of hot water or defrost is a start after it; the jump and
# settling time of a start are measured for at most START_OBSERVE_MAX
START_AFTER_S = 30 * 60
START_OBSERVE_MAX_S = 30 * 60
# Unwanted shutdown: the compressor stops in heating while Tv peaked (within STOP_LOOKBACK before) at
# least effective setpoint + stop margin − STOP_TOLERANCE, and neither the block nor hot water or
# defrost stopped it
STOP_LOOKBACK_S = 3 * 60
STOP_TOLERANCE_K = 0.2
# Wanted off (block): the room is BLOCK_ROOM_ABOVE_K above its target; released when back within
# ROOM_DEADBAND_K. Minimum output above the need: released when the room is ROOM_DEADBAND_K below.
BLOCK_ROOM_ABOVE_K = 0.5
# Learned jump and swing values (from heat_pump_samples): starts of the last JUMP_LEARN_DAYS, at least
# JUMP_MIN_EVENTS per kind of start, their JUMP_QUANTILE; steady swing from rows of steady running
# (the row and SWING_STEADY_ROWS before with the compressor on, no start), at least SWING_MIN_ROWS,
# their SWING_QUANTILE of tv_max − tv.
JUMP_LEARN_DAYS = 28
JUMP_MIN_EVENTS = 5
JUMP_QUANTILE = 0.9
SWING_MIN_ROWS = 288
SWING_QUANTILE = 0.95
SWING_STEADY_ROWS = 3
# After an unwanted shutdown the value concerned is raised at once to what was seen plus BUMP_EXTRA_K;
# without further incidents the raise fades by BUMP_DECAY_K_PER_DAY (weeks)
BUMP_EXTRA_K = 0.5
BUMP_DECAY_K_PER_DAY = 0.1
# Shown in the status: the latest planned writes and unwanted shutdowns
GUARD_RECENT_MAX = 20

# House model from heat_pump_samples (9.3): a day counts for the coverage when it holds at least
# HOUSE_SAMPLES_DAY_MIN_S of valid heating without ventilation; an hour is used with at least
# HOUSE_SAMPLES_HOUR_MIN_ROWS of its 5 minute rows.
HOUSE_SAMPLES_DAY_MIN_S = 3600
HOUSE_SAMPLES_HOUR_MIN_ROWS = 10
