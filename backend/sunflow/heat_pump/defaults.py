"""Fixed values of the flow temperature control (design/heat_pump.md, 3.6 and 8.5)."""

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
