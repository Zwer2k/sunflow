# Supported devices

[Deutsch](../geraete.md) · **English**

Sunflow works with profiles (`profiles/`): a profile says which entities of a Home Assistant
integration provide which values and how a device is controlled. Devices without an integration
are connected directly via Modbus TCP.

## Legend

**Test status**

| Symbol | Meaning |
|---|---|
| ✅ | tested on real hardware |
| 🧪 | tested against a simulated device with the integration's entity keys, not yet on hardware |
| – | not applicable |

**Battery actions**

| Action | Meaning |
|---|---|
| Auto | self-consumption, the device regulates itself |
| Hold | do not discharge, PV may still charge |
| Charge | force charging, also from the grid |
| Discharge | force discharging, also into the grid |
| Limit charging / limit discharging | at most the planned power |

If a device cannot perform an action, Sunflow does not plan it or uses the closest one, e.g.
"Hold" instead of "Limit discharging". Without "Charge" there is no charging from the grid.

## Inverters and batteries

| Device | Connection | Profile | Measures | Battery actions | PV curtailment | Requirements | Measuring tested | Control tested |
|---|---|---|---|---|---|---|---|---|
| Sofar HYD-KTL (2nd gen.) | solax_modbus (Sofar plugin) | `solax_modbus/sofar` | PV per string, external PV, battery, grid, consumption, meters | all, power-exact (passive mode window) | – | Passive mode (set on takeover) | ✅ | ✅ |
| SolaX X1/X3 Hybrid Gen4/Gen5 | solax_modbus (SolaX plugin) | `solax_modbus/solax_gen4_5` | PV, battery, grid, consumption, meters | Auto, Hold, Charge, Discharge | – | Remote Control Mode 1 of the integration | 🧪 | 🧪 |
| Huawei SUN2000 with LUNA2000 | huawei_solar | `huawei_solar/sun2000_luna2000` | PV per string, battery, grid (with smart meter), meters | Auto, Hold, Charge, Discharge | yes | Installer login in the integration | 🧪 | 🧪 |
| Huawei SUN2000 without battery | huawei_solar | `huawei_solar/pv_only` | PV, PV per string, meters | – | yes | Curtailment: installer login | ✅ | 🧪 (curtailment) |
| SolarEdge StorEdge / Home Hub | solaredge_modbus (Core) | `solaredge_modbus/storedge` | PV, battery, grid (with meter), meters | all | – | Battery remote control (set on takeover) | 🧪 | 🧪 |
| Fronius Symo/Primo GEN24 Plus | fronius (Core) | `fronius/gen24` | PV, battery, grid, consumption, meters | Auto, Hold, Limit charging, Limit discharging | yes | Enable Modbus TCP with battery control in the inverter | 🧪 | 🧪 |
| GoodWe ET/EH/BT | goodwe (Core) | `goodwe/hybrid` | PV, battery, grid, consumption, meters | Auto, Charge, Discharge (full power only) | – | – | 🧪 | 🧪 |
| Kostal Plenticore plus / G3 | kostal_plenticore (Core) | `kostal_plenticore/plenticore` | PV per string, battery, grid, consumption | Auto, Hold | yes | – | 🧪 | 🧪 |
| Victron ESS (GX with Multi/Quattro) | direct, Modbus TCP | `modbus/victron_gx` | PV (MPPT and AC-coupled), battery, grid | Auto, Hold, Charge (full power only) | – | Enable Modbus TCP in the GX | 🧪 | 🧪 |

Notes:

- **Flash values:** Huawei, GoodWe, Kostal, Victron and the operating mode of the Sofar store
  their settings permanently in flash. Sunflow only writes them when they change, and at most as
  often per day as the profile allows.
- **Full power only:** GoodWe and Victron always charge or discharge at full power on command.
  Planning still uses the planned power; the next replan absorbs the difference.
- **Curtailment:** Sunflow only curtails systems released under "Control", and only
  when exporting costs money (negative feed-in tariff, above the grid-friendly limit).

## Meters

| Device | Connection | Profile | Measures | Measuring tested |
|---|---|---|---|---|
| Tibber Pulse (local) | tibber_local | `tibber_local/pulse` | grid import and export at the electricity meter, meter readings | ✅ |

## Wallboxes and loadpoints

| Device | Connection | Profile | Control | Phase switching | Vehicle release | Measuring tested | Control tested |
|---|---|---|---|---|---|---|---|
| evcc loadpoint (wallbox or heating rod) | evcc_intg | `evcc_intg/loadpoint` | mode "Now", "PV", "Off"; evcc follows the PV surplus itself | by evcc itself (in PV mode) | – | ✅ | – (not confirmed yet) |
| go-e Charger with API v2 (Gemini, HOME+, HOMEfix) | goecharger_mqtt | `goecharger_mqtt/charger` | charge/stop, charging current following PV surplus (amp, at most every 3 min) | 1 ↔ 3 phases by Sunflow (thresholds, delays, minimum interval, daily limit in the parameters) | charging card of the detected vehicle | ✅ | 🧪 (phases), otherwise – (not confirmed yet) |
| go-e Charger with API v1 (HOME, older firmware) | goecharger (HTTP) | `goecharger/charger` | allow/lock charging, charging current every minute following PV surplus (amx, no flash writes) | – (fixed phases in the parameters) | – | – (not confirmed yet) | – (not confirmed yet) |

Phase switching (go-e, wallbox parameters): for a planned fixed power (e.g. from the grid)
Sunflow chooses the phases immediately. For PV surplus the average of the last minutes decides:
switching up to 3 phases only after the switch-up delay and only if the plan also expects enough
for the next quarter hour; switching down faster, so that no grid power flows. Minimum interval
and daily limit prevent flapping in changeable weather. When handed back, the wallbox gets its
previous phase mode again.

Charging current of the go-e: Sunflow sets the "Requested current" (`amp`), as evcc does with API
v2 – firmware 56.x does not take the temporary current `amx`. Because the go-e keeps this value,
Sunflow changes it at most every 3 minutes (parameter `current_interval_min`) and restores the
previously set current as soon as the wallbox regulates itself or is handed back. The maximum
current of the go-e is taken from its configured maximum (`ama`, e.g. 32 A for a 22 kW wallbox)
and can be overridden in the parameters. The car's maximum charging power single-phase and
three-phase (Settings → Vehicles, e.g. Fiat 500e: 7.4 kW / 11 kW) limits the current further and
is used for planning.

Protect the 12 V battery (Settings → Vehicles): some cars, e.g. the Fiat 500e, drain their 12 V
auxiliary battery when they stay plugged in for a long time without charging. With a voltage
sensor from the car's integration Sunflow monitors it while the car is plugged into the wallbox.
Below the configured limit (default 12.2 V) the wallbox charges for the configured time (default
5 min) at minimum current and – if it can switch – single-phase; the car then also tops up the
12 V battery. Afterwards the plan applies again. It starts again at the earliest after the pause
(default 60 min), so that a rarely updated sensor does not trigger constantly. If the wallbox is
charging according to plan anyway, that continues. An evcc loadpoint charges in "Min+PV" mode for
this. Only effective while Sunflow controls the wallbox.

## Own loads

Devices Sunflow does not detect by itself are added in the settings under "Devices → Custom
loads". Power, meter and temperature are optional.

| Type | Profile | Control | Typical devices | Control tested |
|---|---|---|---|---|
| On/off | `generic/switch` | switch (switch, input_boolean) | Shelly, smart plug, e-bike charger | 🧪 |
| Power levels | `generic/stepped` | select with levels in W/kW | ESPHome heating rod with levels | 🧪 |
| Target current | `generic/current` | number in A, optional switch | wallbox or heating rod with adjustable current | 🧪 |
| Only measure | `generic/meter` | none – power measurement (sensor), optional meter | stove, washing machine, dryer, dishwasher | – |

Sunflow never switches "Only measure". From the hourly values of the last 8 weeks it learns how
much the device uses per day, on how many days it runs and at which hour usually (workdays and
weekends separately); shown under Analysis → Learning. It does not affect planning yet.

### Charge battery (e-bike, tool batteries)

Some loads charge a battery of their own and are only plugged in at times, e.g. an e-bike charger
on a switchable socket. Under "Custom loads" you can switch on **Charge battery** for them. The load
needs a power measurement.

- **Ready to charge** when the optional "plugged in" entity is on. Without one, Sunflow checks with a
  **test charge**: after the trigger (e.g. the shed door is locked, state `locked`; empty means any
  change) and the set waiting time it switches the load on briefly. If it draws at least the
  threshold, it is ready. Under **Control** you can also report it by hand as **Plugged in now**
  (or "No longer plugged in").
- **Planned** only while it is ready, with PV surplus or cheap power and by "Ready by" as usual.
  Until then it is switched off.
- **How much**: from the state of charge with an SoC entity and the battery capacity, else with the
  energy per charge. Sunflow replaces the starting value with the mean of the last charges once it
  has measured them.
- **Full or unplugged**: if the power stays below the threshold for the set time while charging,
  Sunflow switches off. Only the next trigger or plugging in makes it ready again.
- Switching only happens while Sunflow controls the load (Control → Sunflow controls).

## A device is missing or has been tested?

If you have tested a device from the table on real hardware, please report it with integration
version and device model; the test status here will then be updated. A new device needs a
profile in `profiles/` (schema: `schema/profile.schema.json`). The existing profiles show how
roles, actions and limits are described.
