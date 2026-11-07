# Handing over control

[Deutsch](../steuerung.md) · **English**

After setup Sunflow only plans. The devices keep regulating themselves, and in shadow mode
Sunflow records what it would have done differently (Analysis → Shadow mode). Once the plan
looks right to you, you hand over control.

## Switching on

Under **Control → Sunflow controls**:

1. Expand the section. Every controllable device has its own switch: batteries, wallboxes, hot
   water, own loads and inverters that can curtail.
2. Switch on the devices Sunflow should control.
3. Switch on the main switch at the top right and confirm.

Sunflow only writes when the main switch **and** the device switch are on. A battery also needs a
passed [device test](installation.md#3-device-test). Below every device its state is
shown: controlled, not controlled, controlled elsewhere, error. For the battery the current grid
target is shown as well.

The main switch also exists in Home Assistant (entity "Sunflow steuert"), as well as one switch
per device. See [Sunflow integration](integration.md).

## What Sunflow does

- **Battery**: every minute Sunflow applies the planned action, e.g. self-consumption, hold,
  charge from grid, or limited charging or discharging. On the Sofar this is power-exact via the
  passive mode window. Only what the device can do is planned (see [Supported devices](devices.md)).
  A planned partial discharge is energy for the quarter hour: the battery covers short load peaks
  in full until that energy is used, then it holds. This applies only to devices that keep the
  discharge limit in RAM (Sofar passive mode); devices that store it in flash keep the plan's
  fixed limit.
- **Wallbox**: charges in the planned quarter hours and is locked otherwise. On PV surplus the
  charging current follows the surplus. Sunflow switches the go-e between 1 and 3 phases when needed.
- **Surplus the plan did not expect**: when the PV forecast is off and power goes to the grid,
  the wallbox and loads with a current or power steps charge anyway, once the surplus lasts two
  minutes and they still need energy. The priority order and its conditions apply here too. A
  load with a battery condition (e.g. the car from 50 % home battery) that is met, or ranked above
  the battery, also counts the battery's charging power as surplus: the car then charges before
  the battery. After a cloud the smallest current stays for three minutes before stopping; grid
  or battery are not used beyond that.
- **Hot water and own loads**: switched on and off, or power level or current set, as planned.
- **PV curtailment**: only for released systems and only when exporting costs money (negative
  feed-in tariff or above the grid-friendly limit).

Values the device stores permanently in flash (e.g. operating mode) Sunflow only writes when they
change, and at most as often per day as the profile allows.

## Handing back and safeguards

- **Switching off**: when a device or the main switch is switched off, the device gets its
  previous state back: battery to self-consumption, wallbox with its previous current and phase mode.
- **No current plan**: if the plan is older than 30 minutes or the App stops, Sunflow sets
  self-consumption.
- **Watchdog**: if the App does not respond for ten minutes, the Sunflow integration in Home
  Assistant hands the devices back itself and reports it under "Repairs".
- **Other controllers**: while evcc controls the home battery itself, Sunflow pauses. A loadpoint
  controlled elsewhere is marked as such.
- **Inverter restart**: on the Sofar the control values live in RAM only. After a restart the
  battery idles until Sunflow writes the values again in the next minute.

## Log

**Control → Log** shows every command Sunflow sends to controlled
devices: time, action, power, reason, the values written and errors reported by Home Assistant
(e.g. a Modbus exception). An error that repeats every minute appears only once.

If someone else changes a setting of a controlled device, e.g. in the manufacturer's app, in an
automation or in Home Assistant, the log shows that too, with what Home Assistant knows about the
trigger. The log covers 14 days and is part of the data export.

## Temporary overrides

Strategy, mode and target of loads can be changed for a while under
[Control](user-interface.md#control) without touching the settings. From Home Assistant
use the actions `sunflow_ems.set_strategy` and `sunflow_ems.set_consumer`.

There, each battery can be **held** for the chosen time (it gives nothing, PV may still charge it)
or left to run **on its own** (Sunflow only sends "auto", the battery runs its own
self-consumption). "As planned" ends that again.
