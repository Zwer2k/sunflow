"""Fixed values of the flow temperature control (design/heat_pump.md, 3.6, 6.2, 6.3, 8.2 and 8.5)."""

# Deadband of writing (8.5): a new set value is written only when it differs by more than this from the
# written one. A profile may override it in flow_control.writes.deadband.
DEADBAND_K = 0.5
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

# Room correction (8.2) without a heating curve, fixed for underfloor heating until step 3:
# proportional part on the room temperature, integral time, deadband of the integral part (also of
# the setpoint change), limit of the correction and the longest time step counted after a gap
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
