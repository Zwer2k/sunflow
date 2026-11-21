"""Fixed values of the flow temperature control (design/heat_pump.md, 3.6 and 8.5)."""

# Deadband of writing (8.5): a new set value is written only when it differs by more than this from the
# written one. A profile may override it in flow_control.writes.deadband.
DEADBAND_K = 0.5
# Coarsest step of the written set value (3.6): with a coarser one the rounding alone exceeds the
# deadband (e.g. step 1.0 of a firmware without tenths). Firmware property, so not the profile's deadband.
MAX_SETPOINT_STEP_K = DEADBAND_K
