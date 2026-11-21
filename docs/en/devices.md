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
| Growatt SPH with battery (e.g. SPH 4000–10000TL3 BH-UP) | solax_modbus (Growatt plugin) | `solax_modbus/growatt_hybrid` | PV, PV per string, battery, grid (import minus export), consumption, meters | Auto, Charge, Discharge (VPP remote control) | – | VPP function (set by Sunflow on takeover and in the device test) | ✅ | ✅ (charge, auto) |
| Growatt MOD/MIN and SPH without battery | solax_modbus (Growatt plugin) | `solax_modbus/growatt_pv` | PV, PV per string, meters | – | – | – | ✅ | – |
| Deye micro inverters (e.g. SUN-M80G3) | solarman (HACS, davidrapan) | `solarman/deye_micro` | PV (AC), PV per input, meters | – | – | – | ✅ | – |
| Huawei SUN2000 with LUNA2000 | huawei_solar | `huawei_solar/sun2000_luna2000` | PV per string, battery, grid (with smart meter), meters | Auto, Hold, Charge, Discharge | yes | Installer login in the integration | 🧪 | 🧪 |
| Huawei SUN2000 without battery | huawei_solar | `huawei_solar/pv_only` | PV, PV per string, meters | – | yes | Curtailment: installer login | ✅ | 🧪 (curtailment) |
| SolarEdge StorEdge / Home Hub | solaredge_modbus (Core) | `solaredge_modbus/storedge` | PV, battery, grid (with meter), meters | all | – | Battery remote control (set on takeover) | 🧪 | 🧪 |
| Fronius Symo/Primo GEN24 Plus | fronius (Core) | `fronius/gen24` | PV, battery, grid, consumption, meters | Auto, Hold, Limit charging, Limit discharging | yes | Enable Modbus TCP with battery control in the inverter | 🧪 | 🧪 |
| GoodWe ET/EH/BT | goodwe (Core) | `goodwe/hybrid` | PV, battery, grid, consumption, meters | Auto, Charge, Discharge (full power only) | – | – | 🧪 | 🧪 |
| Kostal Plenticore plus / G3 | kostal_plenticore (Core) | `kostal_plenticore/plenticore` | PV per string, battery, grid, consumption | Auto, Hold | yes | – | 🧪 | 🧪 |
| Victron ESS (GX with Multi/Quattro) | direct, Modbus TCP | `modbus/victron_gx` | PV (MPPT and AC-coupled), battery, grid | Auto, Hold, Charge (full power only) | – | Enable Modbus TCP in the GX | 🧪 | 🧪 |
| Indevolt SolidFlex / PowerFlex (plug-in storage with PV inputs) | indevolt (Core) | `indevolt/indevolt` | PV, PV per input, battery, AC output, meters | Auto, Hold, Limit discharging, Charge, Discharge | – | – | 🧪 | 🧪 |
| Homevolt | homevolt (Core) | `homevolt/homevolt` | Battery | Auto, Hold, Charge, Discharge | – | Local Mode (Sunflow sets it, not stored) | 🧪 | 🧪 |
| Marstek Venus E / D / A (plug-in storage) | marstek_modbus (HACS, Modbus via RS485 gateway or Ethernet) | `marstek_modbus/venus` | Battery, AC power, meters | Auto, Hold, Charge, Discharge | – | RS485 control (Sunflow sets it) | 🧪 | 🧪 |
| Zendure SolarFlow / Hyper / AIO | zendure_ha (HACS, official) via the "Zendure Manager" device | `zendure_ha/manager` | Battery (all Zendure devices together) | Auto, Hold, Charge, Discharge | – | Home meter in the Zendure Manager for "Auto" (smart) | 🧪 | 🧪 |

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
| Electricity meter with IR reading head (EDL21/SML) | edl21 | `edl21/meter` | grid import and export at the electricity meter, meter readings; many meters send the power only after entering the PIN from the grid operator | 🧪 |
| HomeWizard P1 Meter | homewizard | `homewizard/p1` | grid import and export at the meter's P1 port, meter readings | 🧪 |
| powerfox poweropti | powerfox (cloud) | `powerfox/poweropti` | grid import and export at the electricity meter (about every minute), meter readings | 🧪 |
| inexogy (Discovergy) | discovergy (cloud) | `discovergy/meter` | grid import and export at the electricity meter, meter readings | 🧪 |
| Grid meter by hand | any sensors | `generic/grid_meter` | signed grid power or import and export apart, meter readings | 🧪 |

If Sunflow does not recognize your meter (e.g. Tibber via the cloud, DSMR, P1 Monitor, Shelly 3EM
or a template sensor), add it under "Devices → Meters → Grid meter by hand": either one signed grid
power (import positive; optionally export positive) or import and export as two sensors, then Sunflow
computes import minus export. A meter takes precedence over the inverter's grid measurement; the
source can be changed under "Balance check".

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

## Heat pumps

| Device | Connection | Profile | Measures | Control | Requirements | Measuring tested |
|---|---|---|---|---|---|---|
| Rotex/Daikin HPSU | ESPHome with the component `daikin_rotex_can` (CAN bus) | `esphome/rotex_hpsu_can` | Flow, flow peak, return, effective flow setpoint, heat output, outside temperature, operating mode, mode of operation, bypass valve, compressor, error code | shadow operation: flow target computed, nothing written | Firmware with manifest and `tv_max` | 🧪 |

Sunflow does not recognize the HPSU by entity names – they are free and translated in ESPHome –
but by the **manifest** the component delivers as an ESPHome action (`…_rotex_can_manifest`).
Other ESPHome devices therefore do not show up among the recognized devices. When adding the device,
Sunflow stores the Home Assistant device, its MAC address and the component (CAN).

- **Assignment:** Sunflow finds the node through the manifest's entities. If several ESPHome devices have
  them all, the device name decides: `HPSU.CAN` fits the action `hpsu_can_rotex_can_manifest`. If no
  device or more than one fits, Sunflow assigns none and reports this in the summary.
- **Compatibility:** On every load Sunflow checks each role of the profile: key present, matching
  type (e.g. select), unit, every option it reacts to, and for Max T-Vorlauf that the range covers the
  limits and the step is at most 0.5 K (older firmware with whole degrees: update the component). If a required role is missing or does not fit, the device is **not compatible**:
  Sunflow does not use it and names the reason in the summary and the log (e.g. "key
  max_target_flow_temp missing"). A missing optional role only disables that function.
- **Reload:** after flashing (new firmware version), when the node is reachable again after an
  outage, and hourly otherwise. A changed manifest is noted once in the log, as is every change of
  the compatibility.
- **Integration added again:** If Sunflow no longer finds the Home Assistant device, it looks for one
  with the same MAC address and assigns it anew (log entry).
- **Profile version:** If the profile is newer than when the device was added, the summary says so.
  Version 2 adds the outside sensor of the outdoor unit (TA2, optional).
- **States and recording:** Every minute Sunflow reads operating mode, mode of operation, HK function,
  bypass valve, room thermostat and error code and stores the means with the states every 5 minutes
  (table `heat_pump_samples`, kept for a year; part of the export). Only this counts as **valid
  heating**: mode heating/setback/automatic, mode of operation heating, bypass valve at 0 % for
  10 minutes, no after-run (10 minutes after hot water or defrosting), no room thermostat block, no
  fault (error code starting with E; warnings with W do not count), the HPSU does not limit the flow
  itself (with "fixed": flow setpoint more than 0.5 K below Max T-Vorlauf for longer than 5 minutes,
  e.g. T-Vorlauf Nacht) and the device is reachable and compatible. Only then will Sunflow control and
  learn later. The log names unknown states, the start and end of a fault, the block on and off and
  the start of the HPSU limiting the flow; `GET /api/v1/heat_pump/status` shows the current state.
  Sunflow writes nothing; Settings → Heat pump shows the state as well.
- **Shadow operation:** From the room setpoint (thermostat "Haus") and the room temperature Sunflow
  computes a flow target (effective flow setpoint + correction) and shows it (Settings → Heat pump,
  attributes of the thermostat); nothing is written to the heat pump yet.

The UART node (`daikin_rotex_uart`) as a companion and the flow control follow in a later step.

## Own loads

Devices Sunflow does not detect by itself are added in the settings under "Devices → Custom
loads". Power, meter and temperature are optional.

| Type | Profile | Control | Typical devices | Control tested |
|---|---|---|---|---|
| On/off | `generic/switch` | switch (switch, input_boolean) | Shelly, smart plug, e-bike charger | 🧪 |
| Power levels | `generic/stepped` | select with levels in W/kW | ESPHome heating rod with levels | 🧪 |
| Relay per phase | `generic/relays` | 1–3 switches, one per phase, with equal power | heating rod with 1–3 elements | 🧪 |
| Target current | `generic/current` | number in A, optional switch | wallbox or heating rod with adjustable current | 🧪 |
| Only measure | `generic/meter` | none – power measurement (sensor), optional meter | stove, washing machine, dryer, dishwasher | – |

Sunflow never switches "Only measure". From the hourly values of the last 8 weeks it learns how
much the device uses per day, on how many days it runs and at which hour usually (workdays and
weekends separately); shown under Analysis → Learning. It does not affect planning yet.

### Heating rod with a relay per phase

"Relay per phase (1–3)" is a heating rod with up to three elements, each switched by its own relay
(Shelly, contactor). Enter the relays for L1–L3 (one is enough) and the power of one element.
Sunflow switches on as many relays as fit into the planned power (with 3 × 2 kW: 2, 4 or 6 kW) and
changes every day which relay comes first, so they wear evenly.

### Charge battery (e-bike, tool batteries)

Some loads charge a battery of their own and are only plugged in at times, e.g. an e-bike charger
on a switchable socket. Under "Custom loads" you can switch on **Charge battery** for them. The load
needs a power measurement.

- **Ready to charge** when the optional "plugged in" entity is on. Without one, Sunflow checks with a
  **test charge**: after the trigger (e.g. the shed door is locked, state `locked`; empty means any
  change) and the set waiting time it switches the load on briefly. If it draws at least the
  threshold, it is ready. Without an SoC entity **Needs charging from** applies: a full battery left
  plugged in only draws a little for its self-discharge (a top-up) – that does not count as ready,
  and switched on it counts as full as long as it never draws more. A value just below the power at
  the start of a charge fits (for an e-bike charging at 110–145 W e.g. 80 W). Under **Control** you
  can also report it by hand as **Plugged in now**
  (or "No longer plugged in").
- **Planned** only while it is ready, with PV surplus or cheap power and by "Ready by" as usual.
  Until then it is switched off.
- **How much**: from the state of charge with an SoC entity and the battery capacity, else with the
  energy per charge. Sunflow replaces the starting value with the mean of the last charges once it
  has measured them. Without an SoC it plans 25 % more time as a reserve in case a charge takes longer.
- **In one go**: a battery charges in one block if at all possible, not in pieces (easier on it,
  especially in the cold). Once a charge runs, Sunflow simply goes on at an equal price.
- **Window over, but not full**: Sunflow switches off at the end of the planned window, but the load
  stays ready. Only the rest of the charge (at least a fifth) is planned then, by "Ready by" or in
  the next cheap window.
- **Full or unplugged**: if the power stays below the threshold for the set time while charging,
  Sunflow switches off. Only the next trigger or plugging in makes it ready again.
- Switching only happens while Sunflow controls the load (Control → Sunflow controls).

## A device is missing or has been tested?

If you have tested a device from the table on real hardware, please report it with integration
version and device model; the test status here will then be updated. A new device needs a
profile in `profiles/` (schema: `schema/profile.schema.json`). The existing profiles show how
roles, actions and limits are described.
