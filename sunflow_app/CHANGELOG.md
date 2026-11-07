# Changelog

## 0.78.17

- Settings → Loads: loads added by hand as "not controllable – measure only" (washing machine, dryer, stove …) no longer show up there with planning settings; they stay under Devices.

## 0.78.16

- Live, 3D view: the inverter callouts sit further left, next to the appliances.

## 0.78.15

- Live, 3D view: the e-bike stands in the back (north) corner with its callout above it; the cars are labelled on the south side below the carport.

## 0.78.14

- Live, 3D view: a smaller house door and a wider shop window, so up to four appliances fit side by side. Each inverter's callout lists its PV power and its batteries by name with level and power, the two side by side below the annex with short leaders.

## 0.78.13

- Live, 3D view: up to two cars under the carport, parked with the rear towards the viewer and the level bar on it, the second wallbox on the back post. The heat pump stands beside the grid pole, the e-bike beside the carport. Values of the roof faces and the house sit on them; the other callouts are placed close to their part, so leaders are short and hardly cross.

## 0.78.12

- Live: a "3D" view of the energy flow – an isometric drawing with callouts: up to four roof faces (south, the north side hinted behind the ridge, annex, carport) placed by the name of their forecast plane, up to two inverters with up to two batteries each, grid, wallbox and car, and – only where measured – e-bike, heat pump, hot water tank, washing machine, dryer and stove. Animated flows from the roof faces to their inverter and on to the house.
- Live API: per string the forecast plane, the batteries with their device, and the device id of each load.

## 0.78.11

- Charge battery (loads without an SoC, e.g. an e-bike): new "Needs charging from" power. A full battery left plugged in only draws a top-up for its self-discharge; above "full or unplugged below" but under this power the test charge no longer counts as ready, and a switched-on battery that never draws more counts as full.

## 0.78.10

- Priority limits (Control → Priority → Limits) hold in operation: control checks them at every step against the measured battery, car and tank levels, not only against the forecast the plan was made with. A load whose limit is not reached gets no planned PV or battery power; power planned from the grid may still run, "Immediately" ignores the limits as before.
- A car plugged in but not recognized yet (level unknown) holds back the loads waiting for it – before, hot water could start with the car below its limit right after plugging in.

## 0.78.9

- Control: surplus the forecast missed is used. When power goes to the grid that the plan did not expect, the wallbox and loads with a current or power steps charge from it once it lasts two minutes – in priority order and with their conditions. A load whose battery condition is met (e.g. the car from 50 %) or that ranks above the battery also takes the battery's charging power, so the car charges first. Through a short dip the smallest current is kept for three minutes, so charging no longer starts and stops every minute when plans differ.
- Planning: a clear PV measurement (from 1 kW) lifts a dull forecast up to ten times instead of three, never above what is measured now, and the measurement counts two minutes after a start instead of four.

## 0.78.8

- Plan a battery: instead of one "price optimized" run, each scenario is run as Sunflow does with the strategies picked above the table – Balanced, Absorb PV, Self-sufficiency and Battery use, with their grid charging, surcharges and buffers – next to plain self-consumption.

## 0.78.7

- Plan a battery: the measured row shows the payback of today's battery against no battery at all, from the purchase price entered for its wear.

## 0.78.6

- Live view, tiles: a "Losses" tile with the inverter and battery losses – the average of the last minutes with a consumption meter, else what the balance leaves right now (including reading differences, as in the bars).

## 0.78.5

- Live view: a fourth view "House" shows the site as an animated scene – sun (brighter and turning faster with more PV) or moon and stars at night, clouds from the weather, panels shimmering on the roof, the grid pole, the battery with its level, the hot water tank coloured by temperature, the carport with wallbox and car with its level. Glowing flows run from the inverter to each part, thicker and faster with more power; lights go on in the window when the house draws power. Without motion when the system asks for reduced motion.

## 0.78.4

- Live view, bars: what comes in but is not measured going out shows as a hatched "Losses / reading difference" segment with its value and an explanation, instead of an unlabeled grey rest; more measured out than in shows as "Reading difference" in the "From" bar. The house has its own colour instead of grey.

## 0.78.3

- Live view: the switch between diagram, bars and tiles, the quick controls and today's figures sit together at the bottom of the screen, without the "Today from energy meters" heading (the source shows as tooltip).

## 0.78.2

- Plan a battery: with an existing battery the table starts with "No battery" – the house without any battery – and a column shows per year what today's battery and each scenario save against none. The saving against today is now labelled as such.

## 0.78.1

- Live: the energy flow has three views, switched below it on the right and remembered per browser – the diagram as before, two flat bars "From" and "To", and small tiles per component and load. Bars and tiles leave room for the quick controls.

## 0.78.0

- Analysis → "Plan a battery": scenarios of a battery one might buy – capacity, usable share, power, battery and inverter efficiency, price and cycles – simulated over the measured grid and battery values and the price history of the last 30 days, 90 days or a year, in addition to the existing battery or instead of it. Each as plain self-consumption and price optimized (with prices and consumption known in advance), with import, export, cost, saving against today, per year, cycles per year and payback. "Use the existing battery" adds the existing one with its values to work out other settings in hindsight.

## 0.77.18

- Planning: of the running quarter hour only the rest counts. Planned at 17:28, the 17:15 slot moves two minutes' worth of energy instead of 15 – before, e.g. refilling the backup reserve looked done within minutes.
- Planning chart: the planned state of charge starts at the level measured now and each planned value sits at the end of its slot, so the line no longer jumps between measured and planned.

## 0.77.17

- Backup reserve first: a battery below the reserve (at the start, after an outage, after a change of the setting) is refilled right away – from the grid if charging from the grid is allowed, whatever the price – and never planned below it again. Before, it was only not discharged further and stayed below the reserve as long as grid charging did not pay.
- "Battery use": cost comes second. Grid power in expensive periods counts 10 ct/kWh extra in the plan, so the battery is charged in cheap hours and covers the day even when losses and wear eat up the price advantage (e.g. 72 % round trip and 6 ct wear with 22.6/32.6 ct). The buffer is sized with this extra too.

## 0.77.16

- Devices → parameters: after "reset" the empty field showed the value stored so far (e.g. 10000 W) instead of what applies once saved – the value from the sensors or the profile's default (e.g. 20000 W).

## 0.77.15

- Fix: when Sunflow stopped, handed a battery back or restored it after the device test, a Sofar inverter kept "Passive: Desired Grid Power" at the profile's buffer of -200 W instead of 0. In passive mode the battery then fed 200 W into the grid around the clock, also at night. Releasing now regulates the grid to zero, as the fallback stored in the integration already did.

## 0.77.14

- Today's energy balance is recomputed in the background shortly after Home Assistant compiles its 5 minute statistics (newer it cannot be), so the live view never waits for it.

## 0.77.13

- Faster pages: Sunflow follows Home Assistant's state changes instead of fetching all states again for a request a few seconds after the last one (several MB on large installations). The day's energy balance is kept for a minute; an older one is answered at once and refreshed in the background.
- Live view: reopening it in the same browser tab shows the last values (with their time) at once while fresh ones load.
- Quick controls: a temporary change shows as a small clock (its end as tooltip) instead of "changed until …", so a row no longer wraps on phones.

## 0.77.12

- Quick controls in the live view: a pin on each load under Control puts it below the energy flow as one compact row with power, state of charge, next window and the modes as small buttons (cost-optimal, PV only, now, pause; a change applies until midnight), plus "plugged in now" for a charge battery without a plug sensor. Stored in the configuration, so it applies on every device.

## 0.77.11

- Settings → Planning: numbers are saved like the switches, as soon as you leave the field or press Enter, instead of with a separate "Save and replan" button; a short "Saved" confirms it. An invalid value stays in the field with a message.

## 0.77.10

- Planning view right after a change in the settings or the control tab: it waits for the new plan (at most 20 seconds) instead of showing the plan from before the change for a few seconds.

## 0.77.9

- Fix: the buffer for a day above the forecast now lies above the backup reserve. While the battery was below the reserve, the buffer was measured from the discharge limit instead, so part of it disappeared in the reserve and the battery was charged too little. Its maximum size also counts from the reserve.

## 0.77.8

- Settings: a search box above the list of areas finds a setting by its name, by other words for it (e.g. "Akku", "Notstrom", "Tibber"), despite typos, in German and English, and devices by their name. A result opens the area, scrolls to the setting, opens it if folded and highlights it briefly.

## 0.77.7

- Buffer for a day above the forecast: with a time-of-use or dynamic tariff the battery is charged in cheap hours for a day somewhat above the forecast instead of exactly for the expected one, so it no longer runs empty in the evening whenever consumption is a little higher or the sun weaker. The size comes from how much the daily consumption varies and from the range of the PV forecast (p10); a missing buffer only counts half of what cheap charging saves, so the battery still uses it on such a day and Sunflow never buys expensive power for it. The planning view shows how much should be left before the next cheap period.
- New home battery strategy "Battery use": like "Balanced" with a buffer for about 19 days in 20 instead of 4 in 5. Also available in the integration's strategy select and in `sunflow_ems.set_strategy`.

## 0.77.6

- On/off loads: a load that is switched on goes on at an equal price instead of being stopped and restarted later; before, replanning every 15 minutes could cut a charge at equal night prices into pieces and push the rest into a more expensive tariff.
- Charge battery (e-bike …): starting a charger costs more in planning, so a charge is one block if at all possible – easier on the battery, especially in the cold.

## 0.77.5

- PV by sun position: Sunflow also learns per plane how far the measurement lies towards Solcast's cautious case (p10) when Solcast itself is uncertain (p10 far below the mean, e.g. low stratus in November), and plans with it where this is better in the backtest. The soiling check uses the same expectation, so a grey week is not taken for leaves.
- Planning chart: the PV forecast has a band from p10 to p90, corrected like the forecast.

## 0.77.4

- Charge battery (e-bike …): a charge the plan switches off before the charger is full goes on with the rest of the energy instead of a whole charge, so later plans no longer push it further back. Without an SoC entity the planned time has a 25 % reserve in case a charge takes longer than the learned mean; the charger is switched off anyway once it draws no more power.

## 0.77.3

- Live view: with several batteries each one is drawn as a small battery icon with its fill level next to the battery node in the energy flow diagram, with short name, state of charge and power (↑ charging, ↓ discharging); hovering shows the full name and the planned action. The rows below the diagram are gone, so "Today from energy meters" stays on screen.
- Batteries get a short name (up to 8 characters) under Settings → Devices → Battery; without one the first word of the name is used.

## 0.77.2

- Live view: the rows of several batteries share one grid, so their state of charge bars line up, and each battery takes a single compact line (on phones the planned action moves into the name's tooltip). The view no longer needs to scroll because of them.

## 0.77.1

- Fix: devices whose integration disables its control entities by default (e.g. Homevolt's battery power, Marstek's RS485 control, force mode and power settings) were not offered under Devices. Profiles now match on disabled entities as well; the device card says how many needed entities are disabled, and Sunflow enables exactly those when the device is taken over (the integration reloads briefly afterwards).

## 0.77.0

- New device profiles for plug-in and home storage: `marstek_modbus/venus` (Marstek Venus E/D/A via the marstek_modbus custom integration, RS485 control mode with force mode and charge/discharge power) and `zendure_ha/manager` (Zendure SolarFlow/Hyper/AIO through the official integration's manager device: manual power, store solar, smart). Both are tested against simulated devices or entities, not yet on hardware. A Modbus simulator for the Marstek Venus E is in `tools/sim/marstek.py`.
- Plan: with several batteries the battery power chart shows each battery, measured and planned, in its own colour.
- Analysis: "Plan against actual" also shows each battery's state of charge with its mean deviation; the shadow recording keeps planned and measured values per battery.
- Control: each battery can be held (gives nothing, PV may still charge it) or left to run on its own for the chosen time; "As planned" ends it.

## 0.76.1

- Fix: the Indevolt capacity was taken 1000 times too large (the kWh sensor is already converted to Wh), which also made the state of charge in the plan chart far too high.
- Batteries: only sub-devices of batteries of the same kind are offered as modules, and only those with the module capacity sensor. A plug-in storage no longer lists the hybrid inverter's modules, and the Sofar BDU and PCU devices are no longer counted as modules.
- Wear cost: the inputs stay in line when a label wraps.
- New device profile `homevolt/homevolt` (core integration homevolt, local): state of charge and power; charging and discharging at a set power and holding through the integration's local mode, which is not stored on the device. Tested against a simulated device (`tools/sim/homevolt.py`), not yet on hardware.

## 0.76.0

- Several batteries are planned together, e.g. a hybrid inverter and a plug-in storage. Each battery has its own state of charge, power limits, efficiencies and wear cost; the one whose discharged kWh costs least covers the demand first, and energy is never moved from one battery into the other. Every battery gets its own action: when only one of them should cover the house, the other holds, so the two do not regulate against each other. The everyday charge limit and calendar aging apply to all batteries, the full charge and the backup reserve to the first one.
- Plan: the state of charge chart shows all batteries together and each one (a legend click shows one in kWh); "Planned control now" and the list of planned control name each battery's action.
- Live view: below the battery node every battery has a row with its state of charge, power and current planned action.
- Profiles can declare `coupling: ac` for storage on the house's AC side, which also converts PV power; the Indevolt profile does.
- Live values, history and the Home Assistant integration's state of charge are the sum over all batteries, matching the plan.

## 0.75.38

- Fix: with a second controllable battery (e.g. a plug-in storage next to the hybrid inverter) the measured state of charge in the plan chart added up both batteries and divided by the planned one's capacity, showing values far above 100 %. History, live values and the plan now all use the battery the plan is made for; the plan warns that the other battery runs on its own (planning several batteries is not supported yet).

## 0.75.37

- New device profile `indevolt/indevolt` for Indevolt SolidFlex/PowerFlex storage (core integration indevolt, local): PV, battery, AC output and energy counters; charging and discharging at a set power through the integration's actions, holding and limiting the discharge through the maximum AC output. Tested against a simulated device (`tools/sim/indevolt.py`), not yet on hardware.

## 0.75.36

- Setup → Integrations: integrations Sunflow does not use are hidden by default; "Show unused" lists them. An integration chosen or unchosen in this visit stays visible.

## 0.75.35

- Battery wear is set per battery: under Settings → Devices → Battery each battery has its own wear cost (directly in ct/kWh or from purchase price and rated cycles) and a switch "Include in planning". Switched off, the battery's cycles count as free and it is used even for small price differences. Batteries without their own entry keep the value set so far under Planning, which no longer shows the wear editor.

## 0.75.34

- PV by sun position: the current state of each plane (e.g. autumn leaves that come and go) is taken from the last 3 days, the latest counting most, and checked every 3 hours. Below 90 % of the learned yield planning uses this lower yield and goes back by itself once the plane delivers more. The soiling hint appears once per episode and no longer asks to clean the modules.

## 0.75.33

- Custom loads: new type "not controllable – only measure" for consumers with a power sensor of their own (stove, washing machine, dryer …). Sunflow never switches them; from the hourly values of the last 8 weeks it learns their energy per day, on how many days they run and their usual hour on workdays and at weekends (Analysis → Learning, MCP tool `get_appliances`). Planning does not use this yet.

## 0.75.32

- Leaves, snow or dirt on the modules are recognised: when a plane delivers far less than usual at this sun position for at least 4 days in a row (without a history of its own: less than half of the forecast), a recommendation says since when and how much, and the PV by sun position section shows it. These days are left out of learning by sun position.

## 0.75.31

- PV correction by sun position: planning uses a plane's model only when it was learned from at least 60 days and beats the correction by time of day in the backtest. Before, a plane with only a few days of forecast history (e.g. a new Solcast site) was corrected by sun position as well. Analysis shows per plane whether planning uses it.

## 0.75.30

- Planning: new switch "correct by sun position (test)" under Settings → Planning → PV forecast (off by default). For planes with Solcast history the forecast is then corrected per sun elevation and direction, telling clear from cloudy slots by the forecast itself; other planes keep the correction by time of day. The model is learned once a day in the background and is also shown under Analysis.

## 0.75.29

- Analysis: new section "PV by sun position (test)". Per roof plane, Sunflow learns from Solcast's past forecasts and the long-term statistics of the plane's strings how much of the forecast arrives per sun elevation and direction (clear and cloudy hours separately), shows it as a shading map and backtests it on the last 3 weeks against the raw forecast and the correction by time of day. Runs in shadow mode; planning is unchanged. Also available to the MCP server as `get_pv_sun`.
- Consumers added by hand and vehicles: choosing an entity suggests the name from its Home Assistant device (else the entity's name), as long as the name was not typed.

## 0.75.28

- Plausibility of learning sources: much faster with many sensors (e.g. ten optimizers over 100 days), and computed without blocking the App, so the check no longer runs into a timeout on a Raspberry Pi.

## 0.75.27

- Plausibility of learning sources: optimizers are judged only while the sun is at least 5° above the horizon, so their nights no longer count as outages. Sunflow now computes the sun's position from the location of Home Assistant (groundwork for learning PV by sun position).

## 0.75.26

- Learning sources: a roof plane with module optimizers (found through the plane's strings) takes the mean of their temperatures as module temperature unless a sensor is chosen. Its plausibility is shown as the median over the optimizers.

## 0.75.25

- Learning sources: the temperatures of module optimizers (e.g. Huawei, which are sub-devices of the inverter) are offered as module temperature; only the inverter's and battery's own temperatures stay out.
- Optimizers: the string and installation sums of Tigo RS485 1.1.0 are no longer taken for optimizers.

## 0.75.24

- Filterable choices open upwards when there is not enough room below (e.g. at the bottom of the page or above the tab bar on phones), and their list is no higher than the room there is.
- Learning sources: current values in the list are rounded to two decimals.

## 0.75.23

- Learning sources are checked for plausibility over up to 100 days: values outside the physical range, short spikes, hanging sensors and outages longer than 30 minutes are left out of learning; times Home Assistant did not record do not count. The plausible share is shown below each source (highlighted below 80 %), and the MCP server has `get_learning_sources` and `get_learning_source_quality`.
- Learning sources: module temperatures are offered only for roof planes in use, not for planes set to "do not use" (e.g. a second forecast provider for the same roof).

## 0.75.22

- All choices from lists that come from Home Assistant or the configuration (entities, devices, plants, roof planes, vehicles, price source, weather, learning sources, optimizer strings, device filter of the control log) can now be filtered by typing. Short fixed choices such as language or strategy stay plain dropdowns.

## 0.75.21

- Optimizers: the roof view can also show each module's yield: today and total with Tigo RS485 from version 1.1.0, total with Huawei.

## 0.75.20

- Data inventory: the power and temperature of each module optimizer (Huawei, Tigo) are checked too, with a tile for how far back their history goes. Shading and heat can then be learned module by module.

## 0.75.19

- Settings → Planning: new optional "Learning sources": outdoor temperature and humidity, illuminance or irradiance, precipitation and a module temperature per roof plane. Sensors are suggested by device class or unit (also DIY sensors without a device class); the inverter's and battery's own temperatures are left out. Each choice shows how far back Home Assistant keeps its values. The learned models will use them step by step; nothing changes in the planning yet.

## 0.75.18

- Data inventory: further entities can be checked as well (`entities`, comma separated; also in the MCP tool `get_data_inventory`), e.g. a weather station before using it for learning. Each entity also shows its `state_class`, without which Home Assistant keeps no long-term statistics.

## 0.75.17

- Analysis: new section "Data for learning". It shows how far back Home Assistant keeps the measurements (recorder history and long-term statistics per entity), whether the weather history includes cloud coverage, and how many days of past forecasts Solcast keeps. This is the groundwork for learning from the past instead of collecting for weeks. Also available to the MCP server as `get_data_inventory`.

## 0.75.16

- Sunflow is in the HACS default list: the documentation now installs the integration by searching for "Sunflow" in HACS (with a link that opens it in HACS) instead of adding a custom repository.

## 0.75.15

- Development: with `SUNFLOW_REQUIRE_TOKEN=1` the backend outside the Supervisor refuses a wrong integration token like the App does, so the integration's reauth can be tried out. The App itself is unchanged.

## 0.75.14

- Integration: when the App rejects the token (e.g. after reinstalling it), Home Assistant asks for the current token under Settings → Devices & services. The entry keeps its address and reloads with the new token; before, it had to be removed and added again.

## 0.75.13

- Live: the losses of inverter and battery are shown below the energy flow (average of the last minutes) and as a tile under "Today". They are the balance of PV, grid and battery minus the measured house consumption: conversion losses and the devices' own consumption. Home Assistant's energy dashboard counts them as house consumption, which is why it shows more consumption and more self-sufficiency.

## 0.75.12

- Energy balance: a PV plant measured by the Sofar as external PV (PV ext) is no longer counted twice. The Sofar's yield counter already contains it, so PV generated was too high by that plant's yield (e.g. 7.0 instead of 6.4 kWh today). New profile flag `includes_pv_ext` for yield counters that count the external PV.

## 0.75.11

- The integration carries the Sunflow version (before: its own 0.4.0), so HACS shows the same version as the App; a release checks that both match.
- The repository is checked by the HACS action and hassfest on every push. The documentation describes installing the integration with HACS or by hand.

## 0.75.10

- Planning: the grid import and export chart also shows the battery power, measured and planned (+ charging, − discharging). Measured values come from the battery power sensor's statistics, else from Sunflow's own samples.

## 0.75.9

- Hot water: a tank that warms up again without its own heating (district heating, gas) has another heat source. Sunflow learns the temperature at which it starts, and the temperature forecast no longer falls below it. Before, the forecast assumed the tank kept cooling down until the heating rod was switched on. Planning shows the learned temperature below the chart.
- Planning: with a battery whose discharge limit lives in RAM (Sofar), a planned partial discharge is shown as energy per quarter hour ("up to … per quarter hour, covering peaks in full"). Discharging more than the planned power during a peak, or holding once that energy is used, is no longer reported as a deviation.

## 0.75.8

- Battery: a planned partial discharge now counts as energy for the quarter hour. Short load peaks (cooking, kettle) are covered by the battery in full until that energy is used, then it holds. Before, the fixed limit let part of a peak come from the grid although the plan had energy left for it. Only for inverters that keep the discharge limit in RAM (Sofar passive mode); those that store it in flash (e.g. Huawei) keep the fixed limit, so they are not written every minute.
- After a replan within the quarter hour, the new power applies to the minutes left.

## 0.75.7

- Planning charts: the measured history of 24 h and 48 h has 5 minute resolution (before: 15 minutes) and reaches up to the last full 5 minutes instead of the last full quarter hour; the line between measured and forecast moves with it. Sunflow's own quarter-hour samples and the past price apply to every 5 minute point of their quarter hour.
- A window scrolled back more than 10 days now gets hourly values. Before, it asked Home Assistant for 5 minute statistics it had already purged, and the charts stayed empty.

## 0.75.6

- Planning shows what is carried out: the intro says whether Sunflow controls nothing, everything, or which devices; "Planned control" and every controllable load are marked **carried out** or **plan only**. Before, it always said Sunflow controls nothing yet, also while it did.

## 0.75.5

- Planning: an on/off load (e.g. an e-bike charger) is charged in one piece where it costs the same, instead of being switched on several times within a cheap window. Each switching on counts 1 ct in the plan, so a clearly cheaper or sunnier time still wins.

## 0.75.4

- Planning: loads that can only be switched on and off (a socket without power steps or current, e.g. an e-bike charger) are planned per quarter hour either off or with at least half their power. Before, the plan could spread their energy thinly (e.g. 67 W of a 143 W charger); Control only switches such a load on from half its power, so it was never charged.

## 0.75.3

- Control: a load with a battery of its own (Charge battery) shows whether it is ready to charge and offers **Plugged in now** / **No longer plugged in**, so it is planned without waiting for a test charge.
- Settings → Devices → Custom loads: the Charge battery block can be collapsed like the device cards; collapsed it shows its current state, and the browser remembers the choice.

## 0.75.2

- Custom loads: "Charge battery" is a separate block below the load's name with its own Save and Cancel; Edit and Remove of the load stay next to its name.

## 0.75.1

- Custom loads: **Charge battery** for loads with a battery of their own that are only plugged in at times, such as an e-bike charger on a switchable socket (Settings → Devices → Custom loads). Ready to charge is decided by an optional "plugged in" entity, or by a short test charge after a trigger (e.g. the shed door locked) and a waiting time: if the load draws power, it is planned with PV surplus or cheap power and switched off until then. The energy comes from an optional state-of-charge entity, else from the energy per charge, learned from the last charges. Power below a threshold while charging means full or unplugged and switches it off. Switching only happens while Sunflow controls the load.
- Control: a test charge without a current plan no longer fails.

## 0.75.0

- First release of the "Sunflow" App: it installs a prebuilt image and only gets updates with a release. The current development state remains available as "Sunflow Edge". These notes cover all changes since 0.73.1.
- Planning → State of charge: the hint about learning the car's consumption now says exactly what counts: 14 days on which the SoC was recorded for at least 20 hours.

## 0.74.8

- Planning → Import price: when part of the recording is missing (e.g. because no plan could be computed), the chart joined the prices before and after with a slope. The gap now stays empty; adjoining prices remain vertical steps.

## 0.74.7

- Collapsible cards remember in the browser whether they are open or closed instead of starting collapsed: "Sunflow controls" (Control), "Planned control" (Planning), the device cards under Settings → Devices, the battery cards and "Vehicle detection" (Charging). They are open on the first visit; deactivated devices stay collapsed. Device cards that need attention (not added yet, a value missing, being edited) are always open, as before.

## 0.74.6

- Planning: "Discharge limited" wrongly reported "deviates from the plan" when the house needed less than the limit. The battery then covers everything and the grid stays near 0 – this looks like self-consumption but keeps to the plan. Discharging at most the limit (with 10 % or 100 W tolerance) now counts as planned, also for "Same decision" in shadow mode.
- Planning: the explanation of the planned control names the power for limits, e.g. "Battery discharges at most 982 W; any demand beyond that comes from the grid", instead of "covers the demand only partly". The same for "Charge limited".

## 0.74.5

- Planning → Import price: the chart also shows the past – the price each quarter hour was planned with (from the recording), like the other charts when paging back.
- Planning → Planned control can be collapsed (click the heading). Collapsed, it shows how many sections there are and what comes next.
- Recommendations: "limit 'from home battery … %' costs PV" came back after hiding with every new plan, because the uncovered energy was part of what identifies it. It now only returns when the limit changes. Also steadier: "plan costs … more" (only again at €5 more) and "PV forecast is … % of the measurement" (in steps of 10 %). A recommendation hidden before has to be hidden once more.

## 0.74.4

- Planning → State of charge: for a car that is not plugged in and whose consumption is not learned yet, the expected line was missing. It now continues at the current SoC until the car is plugged in and planned; the note below the chart says so. With learned consumption it drops on the way as before.

## 0.74.3

- Reloading the integration, to be allowed explicitly per device under Settings → Devices → "Reload integration" (off by default). If the device delivers not a single value for that many minutes (default 10), Sunflow reloads its integration entry in Home Assistant – at most every 30 minutes and 6 times a day, never during a device test. Nothing is written to the device. Every reload appears in the control log; the device card shows since when values are missing and when it was last reloaded.
- Control mode: if a controlled inverter is not (or no longer) in its control mode – e.g. because the switch got lost while its integration was disconnected, or because it was changed in its app – Sunflow switches it back and notes this in the log. Before, it stayed in the wrong mode and refused the control values with Modbus errors. A mode remembered as "unknown" when taking over is replaced by the real one, so handing back restores it.

## 0.74.2

- 0.74.1 did not start (missing file); fixed.
- Sunflow only takes over a device once its mode is readable, and never writes back a remembered mode "unknown"/"unavailable" (e.g. after a Home Assistant restart while the inverter delivers no values yet).

## 0.74.1

- Documentation: images of the optimizer view in the Live view (computer and phone, German and English) under "Optimizers" and "User interface".

## 0.74.0

- Two Apps in the repository: **Sunflow** only gets updates with a GitHub release and installs a prebuilt image (faster, no build on the device). **Sunflow Edge** is the development state with an update for every change and builds on the device as before.
- If both Apps run at the same time, neither controls anything: taken-over devices are handed back, Control shows the reason, and the integration stays with the App that announced itself last. As soon as only one runs, it controls again and announces itself to the integration.
- Moving between the Apps: "Import export" asks whether to take over the configuration too. With configuration it replaces the current one; control stays off afterwards until it is switched on.
- For this the App needs read access to the Supervisor info of the other Sunflow App (`hassio_api`).

## 0.73.1

- Dokumentation: Screenshots aus einer echten Installation (Kennungen unkenntlich), auf Deutsch und – für die englische Dokumentation – auf Englisch, dazu Beispiele für Wechselrichter- und Wallbox-Einstellungen. Die englischen Seiten nennen die englischen Beschriftungen der Oberfläche.
- Live: Der Umschalter der Wettervorhersage heißt auf Englisch „Days“ statt „days“.

## 0.73.0

- Sunflow spricht Deutsch und Englisch. Neue Einstellung unter Einstellungen → Anzeige → Sprache: „Wie Home Assistant“ (Standard; Deutsch, wenn Home Assistant auf Deutsch steht, sonst Englisch), „Deutsch“ oder „English“. Sie gilt für die Oberfläche und für Meldungen wie Planhinweise, Empfehlungen, Gerätetest und Steuerungsprotokoll; die Seite lädt nach dem Wechsel neu.
- Zahlen, Uhrzeiten und Eingabefelder folgen der Sprache (1,5 kW bzw. 1.5 kW).
- Einträge im Steuerungsprotokoll und Planhinweise entstehen in der Sprache, die beim Schreiben galt.
- Der Hinweis in der Planung verweist auf „Einstellungen → Planung“ statt auf den alten Namen „Standards“.

## 0.72.2

- Geräte: Rollen und Kenndaten ohne deutsche Bezeichnung erscheinen nicht mehr mit ihrem internen Namen, z. B. bei der go-e „Eingestellter Ladestrom“, „Ladekarte“, „Karte nötig“, „Phasenmodus“ und „Ladestrom ändern höchstens alle“, bei Wechselrichtern „Max. AC-Leistung“; die neuen Kenndaten haben eine kurze Erklärung.

## 0.72.1

- Dokumentation: vollständig auf Deutsch (`docs/`) und Englisch (`docs/en/`) mit Screenshots – Installation und Einrichtung, Bedienung, Steuerung übernehmen, Einstellungen, So plant Sunflow, Fehlersuche; die bisherigen Seiten (Geräte, Strompreise, Optimierer, Integration, MCP) auch auf Englisch. Die README zeigt Funktionen, Screenshots und die Dokumentation in beiden Sprachen.
- Einstellungen → Integrationen: Die Bereiche „Darf lesen“ des MCP-Servers und die Prognosearten brechen um, statt über den Rand hinauszulaufen.
- Einstellungen → Planung: Die Felder von „Alterung bei hohem Ladestand“ überlappten die Notstrom-Reserve; sie stehen jetzt untereinander.
- Einstellungen → Verbraucher: „Hausspeicher darf in diesen Verbraucher entladen“ bricht um, statt über den Rand hinauszulaufen.
- Steuerung → Priorität: Eine Grenze, die sich auf ein deaktiviertes Gerät bezieht, erscheint nicht mehr mit dessen interner Kennung. Sie bleibt gespeichert und gilt wieder, wenn das Gerät aktiviert wird.

## 0.72.0

- Optimierer werden wie andere Geräte eingebunden: Integration unter Einstellungen → Integrationen auswählen (die Karte zeigt die Zahl der Optimierer; Tigo RS485 erscheint jetzt als „Unterstützt · Optimierer“), dann unter Geräte → Wechselrichter bei „Optimierer“ „von Sunflow nutzen“ anhaken. Vorher erscheinen sie weder auf dem Dach noch in der Live-Ansicht. Bereits angeordnete Optimierer bleiben genutzt.

## 0.71.0

- Planung: Die Diagramme lassen sich mit der Maus (oder dem Finger) seitlich ziehen; alle verschieben sich gemeinsam. Losgelassen lädt Sunflow den gemessenen Verlauf des neuen Fensters nach. Die Knöpfe ‹ Jetzt › bleiben; „Jetzt“ springt zurück. Auf dem Handy scrollt senkrechtes Wischen weiter die Seite.

## 0.70.3

- Plan: Der Hinweis zu ausgeklammerten Großverbrauchern war veraltet („z. B. Wallbox … plant Sunflow noch nicht ein“). Wallbox, Heizstab und andere Verbraucher rechnet Sunflow längst aus dem Hausverbrauch heraus und plant sie selbst. Ausgeklammert werden nur noch seltene Spitzen wie Backofen oder Sauna – darauf weist der Plan erst hin, wenn es viele Stunden sind (mehr als 5 und mehr als 2 % des Verlaufs), mit dem Tipp, einen regelmäßigen Großverbraucher als Verbraucher anzulegen.

## 0.70.2

- Plan-Diagramm: Die Leistung von Wallbox und anderen Verbrauchern kommt jetzt bevorzugt aus Sunflows eigenen Minutenwerten. Ist ein Sensor vom Recorder ausgeschlossen, lieferte der Verlauf von Home Assistant seinen letzten gespeicherten Zustand (z. B. 0 W) stundenlang weiter – die Wallbox fehlte dann in der Grafik, obwohl das Auto lud.

## 0.70.1

- Strategien des Hausspeichers: Unter jeder Strategie (Einstellungen → Planung) und unter der gewählten in der Steuerung lässt sich aufklappen, was der Plan damit anders macht. „Möglichst viel PV aufnehmen“ ist genauer beschrieben: Es hält vor sonnigen Tagen Platz frei, kennt aber keine Einspeisebegrenzung.

## 0.70.0

- MCP-Server: Neues Werkzeug `get_log` liest Fehler, Warnungen und Hinweise aus dem Log der App seit dem letzten Start, bei Ausnahmen mit Traceback.
- MCP-Server: Berechtigungen je Bereich unter Einstellungen → Geräte → MCP-Server („Darf lesen“): Live/Plan/Preise, Verlauf/Aufzeichnung (mit SQL), Konfiguration, Steuerung, Fehlerlog. Nicht freigegebene Bereiche fehlen in der Werkzeugliste und werden abgewiesen. Standardmäßig ist alles freigegeben, Schreiben gibt es weiterhin nicht.
- Steuerung: Beim Auto steht die gescannte Karte direkt in der Klammer, z. B. „Fiat 500e (Karte 1)“.
- Steuerung: Der Abschnitt „Sunflow steuert“ ist standardmäßig zugeklappt. Zugeklappt zeigt er, wie viele Geräte gesteuert werden und ob eines einen Fehler oder eine Warnung hat; der Hauptschalter bleibt sichtbar.

## 0.69.1

- Steuerung → Verbraucher: Beim Auto steht die an der Wallbox gewählte Karte (auch wenn sie keinem Fahrzeug zugeordnet ist) und – wenn eingerichtet – die Spannung der 12-V-Batterie, rot unter der eingestellten Schwelle, mit dem Ende einer laufenden Nachladung.

## 0.69.0

- Planung: Alterung bei hohem Ladestand (kalendarische Alterung). Jede kWh über einer Schwelle (Standard 80 %) kostet im Plan je Stunde einen kleinen Betrag (Standard 0,1 ct). Sunflow lädt den Speicher dadurch möglichst kurz vor dem Bedarf – aus dem Netz am Ende des günstigen Fensters statt ab Mitternacht, und lässt ihn nicht unnötig lange voll stehen. Einstellbar und abschaltbar unter Einstellungen → Planung. Standardmäßig an.

## 0.68.3

- MCP-Server: Schlüssel und die Knöpfe „Erzeugen“ / „Neu …“ / „Zurückziehen …“ stehen in einer Zeile.

## 0.68.2

- MCP-Server: Die angezeigte Adresse und der Einrichtungsbefehl nutzen die interne Adresse von Home Assistant (Einstellungen → System → Netzwerk, sonst seine lokale IP) statt der Adresse, über die der Browser HA gerade öffnet – die kann die externe sein, an der der freigegebene Port nicht erreichbar ist.

## 0.68.1

- MCP-Server: Der Schlüssel entsteht erst auf Knopfdruck (Einstellungen → Geräte → „Schlüssel erzeugen“) und wird nur einmal angezeigt; Sunflow speichert nur eine Prüfsumme. Er lässt sich zurückziehen (Server geschlossen) oder durch einen neuen ersetzen. Der Schlüssel aus 0.68.0 wird gelöscht.

## 0.68.0

- MCP-Server: Ein KI-Assistent (z. B. Claude Code) kann über `/mcp` Plan, Verlauf, Aufzeichnung, Steuerungsprotokoll, Preise, Prognosen und Konfiguration lesen – nur lesend, mit eigenem Schlüssel, der für nichts anderes gilt. Von außen erreichbar erst, wenn in der App unter „Konfiguration → Netzwerk“ ein Port eingetragen ist. Zugangsdaten und Einrichtungsbefehl stehen unter Einstellungen → Geräte. Anleitung in `docs/mcp.md`.

## 0.67.0

- Autos: Mit einem Anwesenheitssensor (Einstellungen → Fahrzeuge, „Angesteckt / zuhause“, z. B. ein device_tracker) lernt Sunflow je Auto, wann es nach Hause kommt und wegfährt – das gilt dann statt der Steckzeiten an der Wallbox. Ist das Auto zu Hause, aber nicht angesteckt, plant Sunflow es ab sofort ein (ohne Pflicht, nur mit PV-Überschuss oder günstigem Strom).
- Hinweis „Einstecken lohnt sich“: Bekommt ein Auto, das zu Hause, aber nicht angesteckt ist, in den nächsten sechs Stunden mindestens 1 kWh, zeigen Plan und Steuerung einen Hinweis mit Startzeit und Anteil PV-Überschuss. Die Integration (0.4.0) löst dazu einmal das Ereignis `sunflow_ems_plug_in_suggested` aus – Beispiel für eine Push-Nachricht in `docs/integration.md`.

## 0.66.1

- Autos unterwegs: Der Bedarf eines Autos, das nur erwartet wird, ist keine Pflicht mehr. Sunflow plant es in Viertelstunden ein, die günstiger als der mittlere Preis im Plan sind, oder mit PV-Überschuss – erzwingt aber kein teures Netzladen. Bisher konnte eine kurze gelernte Standzeit (z. B. Ankunft 13:33, Abfahrt 15:48) volle 11 kW aus dem Netz einplanen. Steckt das Auto an, gilt wieder der volle Bedarf bis zur Abfahrt.

## 0.66.0

- Plan: bis zu 96 statt 48 Stunden voraus. Bei Zeittarifen und festem Preis über den ganzen Zeitraum; bei dynamischen Tarifen schätzt Sunflow die noch nicht veröffentlichten Preise je Uhrzeit aus dem Preisverlauf der letzten zwei Wochen (sonst wiederholt es die letzten 24 bekannten Stunden). Geschätzte Preise sind im Plan gestrichelt und markiert. Geschätzt wird nur so weit, wie die PV-Prognose reicht. Ein Auto ohne Abfahrtszeit wird weiterhin innerhalb von 48 Stunden geladen.

## 0.65.0

- Strompreise: Tibber (Core-Integration, über die Aktion `tibber.get_prices`), Nord Pool (Core-Integration, über `nordpool.get_prices_for_date`) und ENTSO-e (HACS) werden mit ihrem Preisverlauf gelesen. EPEX Spot (HACS) deckt aWATTar, smartENERGY, Energy-Charts, SMARD u. a. ab; dessen Sensor „Market Price“ wird als Börsenpreis erkannt.
- Für reine Börsenpreise gibt es unter Tarif & Prognose einen Aufschlag (ct/kWh) und die Mehrwertsteuer; ohne Aufschlag warnt der Plan. Übersicht in `docs/strompreise.md`.

## 0.64.1

- Ladestand-Diagramm: Die %-Achse endet bei 100 %. Nur wenn ein Wert darüber liegt (z. B. Warmwasser heißer als sein Ziel), reicht sie weiter, und 100 % ist als Linie eingezeichnet.

## 0.64.0

- Sensoren ohne Statistik (ohne `state_class`, z. B. der Ladestand-Sensor eines Autos per MQTT): Sunflow nimmt ihre gespeicherten Zustände aus dem Verlauf von Home Assistant (so lange, wie der Recorder sie behält) und bildet daraus zeitgewichtete Mittel je Stunde bzw. Viertelstunde. Das gilt für das Lernen (Autoverbrauch, Warmwasser, Abzug steuerbarer Verbraucher vom Hausverbrauch) und für die gemessenen Linien im Plan-Diagramm. Damit lernt Sunflow sofort aus der vorhandenen Historie, statt erst Wochen zu sammeln. Stunden, in denen der Sensor weniger als die Hälfte der Zeit einen Wert hatte, zählen nicht.

## 0.63.1

- Plan-Diagramm: Gemessene PV-Erzeugung fehlte seit 0.61.1 (nur die letzte Anlage wurde abgefragt), und der Wallbox-Verbrauch war nur stückweise zu sehen (das Auto zählte beim Auffüllen aus Sunflows eigenen Werten als weiterer Verbraucher). Beides ist behoben.

## 0.63.0

- Autos: Sunflow lernt je Auto, wie viel Ladestand es je Stunde verbraucht (getrennt nach Werktag und Wochenende), aus dem Verlauf seines Ladestand-Sensors der letzten vier Wochen – aus der Statistik von Home Assistant, sonst aus Sunflows eigenen Messwerten. Damit nichts verfälscht wird, zählen nur Tage, an denen der Sensor mindestens 20 Stunden Werte hat; gelernt wird erst ab 14 solchen Tagen, ein eigenes Wochenend-Profil erst ab 4 Wochenendtagen, und Sprünge über 25 % in einer Stunde gelten als Messfehler.
- Wallbox: Die übliche Ankunftszeit wird gelernt (ab 5 Aufenthalten je Tagestyp, aus vier Wochen statt zwei). Ist das einzige Auto einer Wallbox unterwegs, plant Sunflow es mit gelerntem Verbrauch und Ankunft schon jetzt ein: ab der Ankunft, mit dem Ladestand, mit dem es üblicherweise zurückkommt. Bei mehreren Autos an einer Wallbox wird nicht geraten. Das Ladestand-Diagramm zeigt den erwarteten Verlauf unterwegs und darunter, wie weit das Lernen je Auto ist.

## 0.62.0

- Warmwasser: Sunflow lernt, wann und wie viel Warmwasser verbraucht wird – je Stunde, getrennt nach Werktag und Wochenende, aus Speichertemperatur und Heizleistung der letzten vier Wochen (ab einer Woche Daten). Der gelernte Tagesbedarf ersetzt die pauschalen 10 K pro Tag; ohne eingestellte Bereitzeit plant Sunflow den Speicher bis zur gewohnten Hauptverbrauchszeit heiß (z. B. vor dem Duschen am Morgen, mindestens drei Stunden voraus). Die erwartete Temperatur im Ladestand-Diagramm fällt zu den gelernten Zeiten statt gleichmäßig.

## 0.61.1

- Ladestand-Diagramm: eine Linie je Auto statt je Wallbox – mit dem Verlauf seines eigenen Ladestand-Sensors, auch wenn es unterwegs ist; bei mehreren Autos jedes für sich. Der erwartete Verlauf erscheint beim Auto, das gerade an der Wallbox erkannt ist.
- Ladegrenze, Notstromreserve und Entladegrenze erscheinen nur noch, wenn der Hausspeicher allein angezeigt wird; die Ziele von Auto und Warmwasser ebenso nur bei ihnen.

## 0.61.0

- Plan: Das Diagramm „Ladestand“ zeigt neben dem Hausspeicher auch Autos (Ladestand) und Warmwasserspeicher (Temperatur), gemessen und erwartet. Standardmäßig alle in % – beim Warmwasser 0 % bei 15 °C bis 100 % beim Ziel. Ein Klick auf einen Eintrag der Legende zeigt nur diesen in seiner Einheit (Speicher und Auto in kWh, Warmwasser in °C) mit seinem Ziel; erneuter Klick zeigt wieder alle in %.
- Der erwartete Verlauf kommt aus der geplanten Leistung (beim Warmwasser abzüglich der täglichen Abkühlung). Gemessen wird aus der Statistik von Home Assistant; fehlt sie (z. B. ein MQTT-Sensor des Autos ohne Statistik), aus Sunflows eigenen Minutenwerten, die ab jetzt auch die Ladestände speichern.

## 0.60.0

- Neues Profil für ältere go-e Charger mit der lokalen HTTP-API v1 (Integration „goecharger“ aus HACS): Laden erlauben/sperren und Ladestrom als temporärer Strom (`amx`, ohne Flash-Schreiben, wie evcc bei API v1) über die Aktion `goecharger.set_max_current`; den Höchststrom übernimmt Sunflow aus der Wallbox. Diese Wallboxen schalten keine Phasen – die Phasen des Anschlusses stehen in den Kenndaten.
- Profile: Textzustände (z. B. „charging“) können als an/aus gelesen werden (`on_states`).

## 0.59.0

- go-e: Sunflow setzt den Ladestrom jetzt als „Requested current“ (`amp`), wie evcc es bei aktuellen go-e tut. Den temporären Strom `amx` übernimmt die Firmware 56.x nicht – darum blieb die Wallbox bisher beim eingestellten Strom (z. B. 6 A), egal was der Plan wollte. Weil die go-e den Wert behält, ändert Sunflow ihn höchstens alle 3 Minuten (Kenndaten, „current_interval_min“) und stellt den vorher eingestellten Strom wieder her, sobald die Wallbox selbst regelt oder zurückgegeben wird.

## 0.58.1

- go-e: Der Ladestrom geht jetzt über die Aktion „set_config_key“ der go-e-Integration an die Wallbox statt über ein direktes `mqtt.publish`. Bisher kam er nicht an: Die Wallbox blieb beim gespeicherten Strom (z. B. 6 A), egal was Sunflow vorgab – darum lud der Fiat langsamer als geplant.

## 0.58.0

- Plan-Diagramm: Der Verbrauch von Wallbox und anderen steuerbaren Verbrauchern erscheint auch dann als eigene Kurve, wenn Home Assistant für ihren Leistungssensor keine Statistik führt (z. B. im Recorder ausgeschlossen). Sunflow nimmt dafür seine eigenen Minutenwerte; bisher zählte deren Verbrauch dann zum Hausverbrauch. Auch die Verbrauchsprognose zieht ihn ab, statt nächtliches Autoladen als Hausverbrauch zu lernen. Die Leistung wird jetzt je Verbraucher gespeichert; ältere Viertelstunden ordnen die Summe dem einen Verbraucher ohne Statistik zu.
- Wallbox: Sunflow prüft, ob der vorgegebene Ladestrom ankommt (go-e: „Maximum allowed current“). Erlaubt die Wallbox nach zwei Minuten weniger, zeigen Steuerung und Protokoll eine Warnung. Filter „nur Fehler und Warnungen“ im Protokoll.
- Wallbox: Bei unbekanntem Fahrzeug („Wallbox regelt selbst“) entsteht nicht mehr jede Minute ein Protokolleintrag.

## 0.57.0

- Wallbox: Der höchste Ladestrom der go-e kommt jetzt aus ihrem eingestellten Maximalstrom (`ama`, z. B. 32 A bei 22 kW) statt fest 16 A; damit lädt sie auch einphasig mit mehr als 3,7 kW. Ein in den Kenndaten gespeicherter Wert gilt weiter.
- Fahrzeuge: höchste Ladeleistung einphasig und dreiphasig (z. B. Fiat 500e: 7,4 kW / 11 kW). Sunflow gibt der Wallbox nicht mehr Strom vor, als das Auto auf den jeweiligen Phasen nimmt, und plant mit der Leistung des Autos statt der Wallbox.

## 0.56.1

- Wallbox: Laden, das der Plan aus dem Netz vorsieht (z. B. nachts bei günstigem Preis), hält jetzt die geplante Leistung. Bisher wurde es wie PV-Überschussladen behandelt („folgt dem PV-Überschuss“): die Phasen wurden nur nach PV-Regeln umgeschaltet, so blieb die go-e z. B. einphasig bei höchstens 3,7 kW statt der geplanten 4,8 kW; ein evcc-Ladepunkt wartete im PV-Modus. Jetzt wählt Sunflow die Phasen sofort nach der geplanten Leistung.
- Steuerung und Protokoll zeigen die Leistung, die der Strom auf den aktuellen Phasen tatsächlich ergibt, statt der geplanten.

## 0.56.0

- Protokoll (Steuerung → Protokoll): Schreibfehler, die Home Assistant meldet (z. B. eine Modbus-Exception des Wechselrichters), erscheinen jetzt mit der Fehlermeldung. Ein Fehler, der jede Minute wiederkommt, steht nur einmal da – erneut erst, nachdem das Gerät zwischendurch wieder in Ordnung war. Neuer Filter „nur Fehler“.
- Lehnt der Wechselrichter die Werte ab und steht er nicht im Steuermodus (z. B. in seiner App auf „Self Use“ umgestellt), nennt die Fehlermeldung den Modus, auf dem er steht.

## 0.55.2

- Verständlichere Meldungen ohne Verbindung zu Home Assistant: Das Log sagt „Home Assistant not connected (restarting?) – planning again once it is back“ statt einer Warnung, Fehler nennen „Not connected to Home Assistant“.

## 0.55.1

- Keine Warnung „Replan failed: Not connected“ mehr, wenn während eines Neustarts von Home Assistant eine Einstellung gespeichert oder der Plan geöffnet wird. Sunflow plant dann neu, sobald die Verbindung wieder steht, statt die Änderung bis zur nächsten Viertelstunde liegen zu lassen.

## 0.55.0

- Fahrzeuge: 12-V-Batterie schützen. Mit dem Spannungssensor des Autos (Einstellungen → Fahrzeuge) überwacht Sunflow die Bordnetzbatterie, solange das Auto an der Wallbox steckt. Unter der Grenze (Vorgabe 12,2 V) lädt die Wallbox 5 min mit kleinster Leistung und einphasig, danach frühestens nach 60 min wieder – alles einstellbar. Hilft bei Autos wie dem Fiat 500e, die ihre 12-V-Batterie entladen, wenn sie lange angesteckt sind, ohne zu laden. Die Spannung erscheint bei den Live-Werten des Fahrzeugs, der Grund in Steuerung und Protokoll.

## 0.54.0

- Optimierer: Trenner zwischen Reihen und zwischen Plätzen deuten an, dass Module abseits liegen (z. B. zwei Reihen auf einem anderen Dachstück). Im Editor „+ Trenner“ zwischen zwei Reihen bzw. das schmale „+“ zwischen zwei Plätzen (gilt für alle Reihen); die Dach- und Live-Ansicht zeigen sie als gestrichelte Linie mit Abstand.

## 0.53.3

- Optimierer: Ein quer gelegtes Modul schiebt nur die Module seiner eigenen Reihe zur Seite; die anderen Reihen behalten ihre Abstände (bisher wurde die ganze Spalte breiter).

## 0.53.2

- Optimierer: Einzelne Module lassen sich quer legen (Editor: Modul antippen → „quer legen“, zurück mit „hochkant stellen“). Spalten und Reihen passen sich dem breitesten Modul an; Dach- und Live-Ansicht zeigen sie ebenso.

## 0.53.1

- Optimierer: Lange Namen (z. B. Huawei-Seriennummern) brechen im Modul auf zwei Zeilen um statt abgeschnitten zu werden. Im Editor steht unter dem Dach der volle Name des ausgewählten Moduls.

## 0.53.0

- Planung: Der Verbrauch der nächsten Stunde richtet sich nach dem, was das Haus gerade braucht (Mittel der letzten Minuten, ohne steuerbare Verbraucher). Die erste Viertelstunde beginnt beim gemessenen Wert, die Abweichung zur Prognose halbiert sich alle 30 Minuten – wie es die PV-Prognose schon mit der gemessenen PV tut, nur schneller abklingend.
- Planung: Alle Grafiken zeigen denselben Zeitraum. Kostenverlauf und Bezugspreis folgen jetzt auch dem gewählten Fenster (24 h, 48 h, 7 Tage, verschoben) statt eigener Zeiträume.

## 0.52.0

- Optimierer: In der Live-Ansicht öffnet ein Klick auf einen PV-String (Zahl am Kreis = Anzahl Optimierer) seine Module, angeordnet wie auf dem Dach, mit aktueller Leistung und je nach Quelle Spannung, Strom, Temperatur und PWM. Die Werte lassen sich einzeln ein- und ausblenden.
- Einstellungen → Optimierer (neu): gefundene Optimierer von Huawei (Integration Huawei Solar) und Tigo (Integration Tigo RS485), Zuordnung zu den Strings – automatisch (Huawei: der String in Betrieb; Tigo: Label „A1“ = String A, Modul 1) oder von Hand – und ein Editor für die Anordnung auf dem Dach (ziehen oder antippen).

## 0.51.0

- Einstellungen → Anzeige (neu): Das Wetter in der Live-Ansicht lässt sich für Handy und für Tablet/Computer getrennt ausblenden. Ist es überall aus, wird die Wettervorhersage dafür nicht mehr abgefragt.
- Live: Netz, PV/Speicher und Haus rücken im Energiefluss enger zusammen. Das Diagramm ist schmaler und wird bei gleicher Breite (Handy) höher und größer dargestellt.

## 0.50.3

- Einstellungen auf dem Handy: Pfeile am Rand der Bereichszeile zeigen, dass es links bzw. rechts weitere Bereiche gibt. Antippen scrollt weiter.

## 0.50.2

- Einstellungen auf dem Handy: Die Bereiche sind wieder auswählbar. Nach abgeschlossener Einrichtung (ohne Schrittnummern) waren alle Einträge außer dem aktuellen leer und damit unsichtbar. Jetzt stehen alle Namen in einer seitlich scrollbaren Zeile, der gewählte Bereich wird ins Bild geholt.

## 0.50.1

- Empfehlung „… läuft mit etwa … W statt … W“ entfällt für Geräte mit Leistungsstufen (z. B. Heizstab): Der gemessene Mittelwert mischt die Stufen und die Ein-/Aus-Phasen und ist keine Leistung, die man einstellen sollte. Die Stufen selbst legen die Leistung fest.

## 0.50.0

- Steuerung → Protokoll: jeder Schaltbefehl, den Sunflow an gesteuerte Geräte schickt – Zeit, Aktion, Leistung, Grund und die geschriebenen Werte (bei Fehlern mit Ursache), dazu Übernahme und Rückgabe.
- Ändert sich an einem gesteuerten Gerät ein Stellwert (Auswahl, Schalter, Zahl), ohne dass Sunflow ihn geschrieben hat, steht das ebenfalls im Protokoll – mit dem, was Home Assistant über den Auslöser weiß: ein Benutzer (oder ein Programm mit dessen Zugangstoken, z. B. evcc), eine bestimmte Automation bzw. ein Skript, oder das Gerät/seine Integration selbst (eigene Regelung des Geräts oder ein System, das direkt aufs Gerät schreibt). Geprüft wird jede Minute; Filter nach Gerät, Zeitraum (bis 14 Tage) und „nur fremde Änderungen“.
- Das Protokoll (`control_log.jsonl`, 14 Tage) ist im Datenexport enthalten.

## 0.49.4

- Planung: Weicht die PV 3 Minuten lang deutlich vom Plan ab, wird neu geplant (bisher 5 Minuten, und frühestens 5 statt jetzt 3 Minuten nach dem letzten Plan). Die Seite holt den Plan jede Minute statt alle 2 Minuten – mehr Sonne als vorhergesagt zeigt sich damit nach etwa 4 statt bis zu 12 Minuten in der Prognose.
- „Tatsächlich: Speicher lädt … aus dem Netz“: Ob aus dem Netz oder aus PV, entscheidet jetzt auch das Mittel der letzten Minuten (bisher eine einzelne Messung). Direkt nach einem Neustart gibt es erst nach 3 Messungen ein Urteil. Bei einer Abweichung stehen die gemittelten Werte von Netz und Speicher dabei.

## 0.49.3

- Planung → „Tatsächlich: … weicht vom Plan ab“: beurteilt jetzt das Mittel der letzten 5 Minuten statt einer einzelnen Messung. Eine Messung verbindet Zähler, die zu verschiedenen Zeitpunkten gelesen werden (Netz alle paar Sekunden, Wechselrichter etwa alle halbe Minute), und fängt Lastsprünge ab, die der Wechselrichter gerade ausregelt – das sah oft nach Laden aus dem Netz aus, das es nicht gab.

## 0.49.2

- PV-Prognose für Home Assistant: Auch die vergangenen 31 Tage werden geliefert – aus der Prognose, die Sunflow damals je Fläche aufgezeichnet hat, mit der gelernten Korrektur (ähnlich wie Solcast seine Vergangenheit liefert). Wochen- und Monatsansicht im Energie-Dashboard zeigen damit Prognose und Ertrag auch für zurückliegende Tage.

## 0.49.1

- PV-Prognose für Home Assistant: Der letzte Tag, den die Quelle nur anreißt (Solcast reicht bis kurz nach Mitternacht des 7. Tages), wird nicht mehr geliefert – er erschien im Energie-Dashboard als Tag ohne Sonne.
- Liefert die Quelle die schon vergangenen Stunden von heute nicht mehr, kommen sie aus der Prognose, die Sunflow damals aufgezeichnet hat (mit der gelernten Korrektur). So hat der heutige Tag im Energie-Dashboard eine durchgehende Prognose.

## 0.49.0

- Integrationen → Sunflow-Integration: wählbar, welche PV-Prognosen Home Assistant bekommt – gesamt, je Wechselrichter, je Fläche/String (Standard: alle). Abgewählte verschwinden in Home Assistant samt Gerät beim nächsten Abgleich (bis zu 15 Minuten), wieder angewählte kommen zurück. Eine in Home Assistant gelöschte, aber weiter angebotene Prognose bleibt gelöscht (Integration 0.3.1).

## 0.48.2

- Die Integration heißt jetzt `sunflow_ems` (Integration 0.3.0). Unter `sunflow` gibt es bereits eine fremde Integration, deren Icon Home Assistant anzeigte. Mit der neuen Domain erscheint überall das Sunflow-Icon der App. Aktionen und Ereignisse heißen entsprechend `sunflow_ems.plan_load`, `sunflow_ems_load_start` usw.
- Umstellung: Die alte Integration „Sunflow“ unter „Einstellungen → Geräte & Dienste“ löschen, in HACS aktualisieren und Home Assistant neu starten; den Ordner `custom_components/sunflow` entfernen, falls er bleibt. Danach die Integration neu einrichten (sie wird wieder automatisch gefunden).

## 0.48.1

- Vorrang „erst ab Hausspeicher …%“: Sunflow prüft den Ladestand jetzt am Ende der Viertelstunde statt am Anfang. In der Viertelstunde, in der der Speicher die Schwelle erreicht, bekommt der Verbraucher (z. B. der Heizstab) den restlichen Überschuss. Vorher wurde dieser Rest eingespeist – besonders sichtbar, wenn die Schwelle gleich der Ladegrenze im Alltag ist.

## 0.48.0

Sunflow-Integration für Home Assistant (Integration 0.2.0, siehe docs/integration.md):

- Einrichtung automatisch über den Supervisor oder von Hand mit Adresse und Schlüssel (in Sunflow unter „Integrationen“).
- Gelernte PV-Prognose fürs Energie-Dashboard, je Fläche, je Anlage und gesamt als eigene Geräte, mit Prognose heute, Rest heute, morgen und Leistung jetzt.
- Energiezähler je PV-String (aus der minütlich gemessenen Leistung), damit Strings auch ohne eigenen Zähler eine Solarquelle haben.
- Gerät „Sunflow“: Aktion des Speichers, Preis, günstigstes Fenster, Speicher in einer Stunde, Ersparnis, PV-Überschuss, Planstatus, Aktion je gesteuertem Gerät, nächster Start und geplante Energie je Verbraucher; Hauptschalter, Schalter je Gerät, Strategie, „Neu planen“; Kalender mit den Fenstern des Plans.
- Aktionen: Strategie setzen, Verbraucher-Planung ändern, einmalige Last einplanen (günstigste zusammenhängende Startzeit, z. B. Waschmaschine) und abbrechen, neu planen. Ereignisse für neuen Plan, Verbraucherstart und fällige einmalige Last.
- Watchdog: Antwortet die App zehn Minuten nicht, während sie steuert, gibt die Integration die Geräte zurück und meldet das unter „Reparaturen“.

## 0.47.2

- Phasenumschaltung: „Hochschalten nach“ und „Herunterschalten nach“ sind mindestens 1 Minute, der „Mindestabstand zwischen Umschaltungen“ mindestens 5 Minuten, „Höchstens Umschaltungen pro Tag“ mindestens 1. Kleinere Werte werden beim Speichern abgelehnt, früher gespeicherte wirken wie die Untergrenze.
- Kenndaten: Werte außerhalb des erlaubten Bereichs eines Geräts werden beim Speichern mit Hinweis auf den Bereich abgelehnt statt übernommen.

## 0.47.1

- Planung → Steuerbare Verbraucher: Eine Wallbox mit zugeordnetem Fahrzeug zeigt Ladestand und Ziel des erkannten Fahrzeugs und dessen Namen auch, wenn es gerade nicht angesteckt ist (eingeplant wird es erst angesteckt). Vorher fehlte der Ladestand bei Wallboxen ohne eigenen Ladestand (go-e).

## 0.47.0

Phasenumschaltung der Wallbox (go-e):

- Kenndaten der Wallbox → „Phasenumschaltung“: automatisch, immer 3 Phasen oder immer 1 Phase. Automatisch lädt das Auto schon bei wenig PV-Überschuss auf 1 Phase (ab ≈ 1,4 kW) und schaltet bei genug Sonne auf 3 Phasen.
- Einstellbar: „Auf 3 Phasen ab“ (Standard 4,5 kW) und „Auf 1 Phase unter“ (4,0 kW) als Hysterese, Verzögerung fürs Hoch- (5 min) und Herunterschalten (2 min), Mindestabstand zwischen Umschaltungen (15 min) und höchstens 12 Umschaltungen am Tag.
- Strategie: Bei geplanter fester Leistung (z. B. günstiges Netzladen) wählt Sunflow die Phasen sofort. Beim PV-Überschuss entscheidet das Mittel der letzten Minuten, und hochgeschaltet wird nur, wenn der Plan auch für die nächste Viertelstunde genug Überschuss erwartet – kurze Sonnenlücken schalten nicht.
- Die Steuerung zeigt die aktuelle Phasenzahl. Beim Zurückgeben bekommt die Wallbox ihren vorherigen Phasenmodus wieder.
- evcc-Ladepunkte schalten im PV-Modus weiterhin selbst um.

## 0.46.3

- Einstellungen → Fahrzeuge zeigte nach dem Öffnen bis zu 30 Sekunden „Lade …“: Die Fahrzeuge werden jetzt sofort geladen, danach alle 30 Sekunden aktualisiert.

## 0.46.2

- Wallboxen mit zugeordneten Fahrzeugen zeigen in den Übersichten das erkannte Fahrzeug und dessen Ladestand aus dem Fahrzeug-Sensor: im Live-Energiefluss am Wallbox-Knoten (z. B. „Fiat 500e 62 %“) und in der Steuerung. Das gilt auch für Wallboxen, die selbst keinen Ladestand kennen (go-e). In der Steuerung ist der Wert jetzt aktuell statt aus dem letzten Plan; ein erkanntes Fahrzeug erscheint dort auch, bevor der Plan es kennt.

## 0.46.1

- Einstellungen → Fahrzeuge: Die Karte zeigt wie bei den Geräten die aktuellen Werte der Fahrzeug-Sensoren – zugeklappt in einer Zeile, aufgeklappt als Kacheln: Ladestand (mit Stand der Messung), Energie im Akku und ob das Auto angesteckt bzw. zuhause ist. Die Werte aktualisieren sich alle 30 Sekunden.

## 0.46.0

- Planung → Vorgaben → „Ladegrenze gilt“: „immer“ (wie bisher) oder „außer vor Schlechtwetter-Tagen“. Sagt die PV-Prognose für morgen bzw. die nächsten Tage (einstellbar 1–5) weniger Ertrag als den erwarteten Tagesverbrauch voraus, setzt Sunflow die Ladegrenze aus und lässt den Speicher mit der PV vorher voll laden. Der Plan nennt den betreffenden Tag. Gezählt werden nur Tage, die die Prognose ganz abdeckt; der heutige Tag zählt nicht.

## 0.45.0

Einstellungen neu geordnet:

- Nach der Ersteinrichtung zeigen die Einstellungen benannte Bereiche statt nummerierter Schritte: Geräte (mit ihren Kenndaten in der Gerätekarte), Fahrzeuge, Laden, Verbraucher, Tarif und weitere. Die Ersteinrichtung führt weiter Schritt für Schritt durch. Hinweise und Empfehlungen verweisen auf die Bereiche.
- Fahrzeuge enthalten nur noch, was das Auto ist (Akku, Sensoren). Unter „Laden“ steht pro Wallbox, welches Fahrzeug mit welcher Karte und welchem Ziel lädt, zusammen mit der Fahrzeugerkennung; beides wird gemeinsam gespeichert.
- Die Konfiguration wird beim ersten Start automatisch auf das neue Format (Version 3) umgestellt; Einstellungen bleiben erhalten.

Eigene Verbraucher:

- Neben Ein/Aus-Schaltern (Shelly, Steckdose) gibt es Verbraucher in Leistungsstufen (z. B. ESPHome-Heizstab mit Auswahl, die Leistung wird aus den Optionsnamen gelesen) und mit Ziel-Stromstärke, jeweils mit oder ohne Leistungs- und Verbrauchsmessung.
- Ein Heizstab im Speicher wird als Wärme nach Temperatur und Volumen geplant wie der evcc-Heizstab. Der Tagesverbrauch wird auch allein aus einem Energiezähler gelernt.

Wechselrichter:

- Neue Profile, aus den Entitäten der Integrationen gebaut und gegen nachgebildete Geräte getestet, noch nicht an echter Hardware:
  - SolaX Gen4/5 (solax_modbus, Remote Control Mode 1)
  - Huawei SUN2000 mit LUNA2000 (huawei_solar, braucht den Installateur-Zugang), Huawei ohne Speicher jetzt mit Abregelung
  - SolarEdge StorEdge (solaredge_modbus)
  - Fronius GEN24 (fronius mit Modbus-Steuerung)
  - GoodWe ET/EH (goodwe)
  - Kostal Plenticore (kostal_plenticore)
  - Victron ESS direkt per Modbus TCP am GX-Gerät
- Speicher ohne Leistungsfenster werden über Aktionen gesteuert (Automatik, Halten, Laden/Entladen erzwingen, Laden/Entladen begrenzen). Geplant wird nur, was das Gerät kann: ohne erzwungenes Laden z. B. kein Laden aus dem Netz. Eine fehlende Aktion ersetzt Sunflow durch die nächstliegende und zeigt das in der Steuerung an. Befehle, die das Gerät nach einiger Zeit vergisst, werden regelmäßig erneuert; im Flash gespeicherte Werte nur bei Änderung.
- PV-Abregelung: Ist eine Anlage zur Steuerung freigegeben und kann sie abregeln, darf der Plan ihre PV drosseln, wenn Einspeisen kostet (negative Vergütung, über der netzdienlichen Grenze). Die Steuerung führt das Limit minütlich anhand der gemessenen Einspeisung nach und hebt es ohne Abregelung, beim Zurückgeben und beim Beenden der App wieder auf. Die Planung zeigt die abgeregelte PV.
- Solarspitzengesetz: Zu negativen Preisen gilt die Einspeisung als unvergütet.
- Geräte ohne Home-Assistant-Integration lassen sich unter „Integrationen“ direkt anbinden (Modbus TCP, vorbereitet auch HTTP/JSON). Sie erscheinen danach wie jedes andere Gerät mit Live-Werten, Kenndaten, Gerätetest und Steuerung.

## 0.44.1

- Planung → PV-Erzeugung: Per Häkchen lässt sich die unkorrigierte Prognose der gewählten Quelle (Solcast, Forecast.Solar oder beide) als dünne Linie einblenden – in der Vergangenheit, wie der Schattenbetrieb sie aufgezeichnet hat, im Planzeitraum und danach. So ist sichtbar, wie weit die gelernte Korrektur und die Kurzfristprognose von der Rohprognose abweichen. Die Auswahl wird im Browser gemerkt; der Legendeneintrag hebt die Linie hervor.

## 0.44.0

Planung → Verlauf und Prognose:

- 24 h, 48 h und 7 Tage wählen die Breite des Zeitfensters (ein Drittel Vergangenheit, zwei Drittel Zukunft; Standard 48 h).
- Mit ‹ und › springt das Fenster um eine halbe Breite in die Vergangenheit (bis 31 Tage) oder Zukunft (bis 7 Tage), „Jetzt“ springt zurück.
- Hinter dem Planende (der Plan reicht so weit wie die Strompreise, höchstens 48 h) zeigt das PV-Diagramm die längere Solcast-Prognose, mit der gelernten Korrektur.

## 0.43.1

- PV-Prognose: Der Sicherheitsabschlag von „Unsichere PV-Prognose“ (Richtung pessimistische Prognose) wirkt nicht mehr dort, wo gerade gemessen wird – in den nächsten Stunden im Maß der Kurzfristprognose – und bei klarem Himmel laut Wetter nur noch zu einem Viertel. Vorher plante Sunflow z. B. bei wolkenlosem Himmel mittags weniger PV, als schon vormittags gemessen wurde.
- Die Kurzfristprognose startet erst mit vier Minutenwerten (nach einem Neustart der App war einer zu zufällig).

## 0.43.0

Planung reagiert schneller auf die tatsächlichen Werte:

- Kurzfristprognose: Weicht die gemessene PV gerade von der Prognose ab (Mittel der letzten Minuten), wird das auf die nächsten Stunden übertragen – jetzt voll, nach einer Stunde zur Hälfte, danach immer weniger. Mehr Sonne als vorhergesagt fließt so sofort in den Plan ein (z. B. Auto oder Heizstab früher), weniger ebenso.
- Neuberechnung bei Abweichung: Liegen PV (mehr als 800 W bzw. 30 %), Verbrauch (mehr als 1,5 kW) oder Ladestand (mehr als 5 % der Kapazität) fünf Minuten lang in dieselbe Richtung neben dem Plan, rechnet Sunflow sofort neu statt bis zur nächsten Viertelstunde zu warten – höchstens alle 5 Minuten. Kurze Wolken lösen nichts aus.
- Gelernt wird weiter aus der unveränderten Prognose; die Kurzfristkorrektur verfälscht das Lernen nicht.

## 0.42.0

- Netz-Zielwert („Einspeise-Puffer“) wird jetzt nach Plan gesetzt: Ein negativer Puffer (z. B. −200 W) gilt nur in Viertelstunden, in denen der Plan ohnehin mehr einspeist (PV-Überschuss). Bei wenig PV, beim Halten, bei Autarkie und ohne aktuellen Plan regelt der Wechselrichter auf 0 W – so geht kein gespeicherter Strom ins Netz, der später teurer zurückgekauft wird. Ein positiver Wert (bewusst leichter Bezug) gilt immer.
- „Sunflow steuert“ zeigt beim Speicher das aktuelle Netz-Ziel.

## 0.41.8

- Kenndaten → Sofar: Der Netz-Zielwert („Passive Desired Grid Power“, bisher „Einspeise-Puffer“ und nur im Expertenmodus) ist jetzt immer sichtbar, mit Erklärung. Vorgabe −200 W.

## 0.41.7

- Fahrzeuge: Jedes Fahrzeug und die Erkennung je Wallbox erscheinen kompakt und klappen sich wie die übrigen Karten auf; aufgeklappt sind die Felder gleich bearbeitbar (Speichern, Abbrechen, Entfernen).

## 0.41.6

- Kenndaten: Die Geräte erscheinen kompakt mit den wichtigsten Werten und lassen sich aufklappen wie unter Geräte. Offen bleiben Geräte mit ungespeicherten Änderungen oder einer fehlenden Pflichtangabe (Speicherkapazität, Volumen des Warmwasserspeichers).

## 0.41.5

- Kenndaten und Geräte: Die Gruppen-Überschriften (Wechselrichter, Speicher, Wallboxen …) sind deutlicher. Kann die Kategorie nicht vom Server geladen werden, ordnet die Oberfläche die Geräte selbst nach ihren Fähigkeiten ein, statt alles unter „Verbraucher“ zu zeigen.

## 0.41.4

- Rückfragen (Steuerung einschalten, Gerät entfernen oder deaktivieren, Wechselrichter zurücksetzen, PV neu lernen, Fahrzeug entfernen) erscheinen als eigener Sunflow-Dialog statt als Browser-Meldung.

## 0.41.3

- Steuerung → „Sunflow steuert“: Der Zustand jedes Geräts steht direkt unter seinem Namen und ist besser lesbar; der Hinweis zu anderen Steuerungen erscheint bei Ladepunkten nur noch, wenn Sunflow sie tatsächlich steuert.

## 0.41.2

- Steuerung: Der Hinweis bei Ladepunkten nennt allgemein „an anderer Stelle gesteuert“ statt evcc.

## 0.41.1

- Steuerung → „Sunflow steuert“: Ist ein Gerät eingeschaltet, der Hauptschalter aber aus, steht das jetzt dabei (vorher nur „nicht gesteuert“). Wer ein Gerät einschaltet, während der Hauptschalter aus ist, schaltet ihn nach Rückfrage gleich mit ein. Eine Minute nach dem Einschalten aktualisiert sich die Anzeige von selbst mit dem, was Sunflow geschrieben hat.

## 0.41.0

- Die Kategorie eines Geräts (Wechselrichter, Speicher, Zähler, Wallbox, Wärme, Verbraucher) wird beim Übernehmen festgehalten und überall verwendet: Geräte und Kenndaten sind danach gegliedert, Steuerung und „Sunflow steuert“ zeigen sie bei jedem Gerät.
- evcc-Ladepunkte: Unter Kenndaten lässt sich die Art festlegen (Wallbox oder Wärme). Ohne Angabe gilt wie bisher die Einheit des Ladestands (°C = Wärme); sie wird jetzt beim Übernehmen gespeichert, so dass ein gerade nicht lesbarer Sensor die Einordnung nicht mehr ändert.
- Deaktivierte Geräte erscheinen in Kenndaten, Gerätetest, Bilanzprüfung und Prognose nicht mehr.

## 0.40.0

- Einstellungen → Geräte → Speicher: Jeder Wechselrichter mit Speicher hat eine eigene Speicher-Karte mit Ladestand, Leistung, Kapazität und seinen Modulen (z. B. 4× BTS mit der jeweiligen Kapazität). Die Module werden automatisch zugeordnet (Untergeräte des Wechselrichters in Home Assistant) oder lassen sich von Hand zuordnen, auch Module, die in Home Assistant an einem anderen Wechselrichter hängen. Die vorgeschlagene Kapazität ergibt sich aus den zugeordneten Modulen.

## 0.39.0

- Einstellungen → Geräte: Geräte lassen sich mit „aktiv“ ausschalten. Ein deaktiviertes Gerät behält alle Einstellungen, wird aber weder geplant, gemessen noch gesteuert – z. B. um zwischen der Wallbox über evcc und direkt am go-e zu wechseln. Stand es unter Sunflow-Steuerung, wird es vorher zurückgegeben.
- Die Geräteseite ist nach Kategorien gegliedert (Wechselrichter, Speicher, Zähler, Wallboxen und Fahrzeuge, Wärme, Verbraucher). Übernommene Geräte erscheinen kompakt mit den wichtigsten Live-Werten und lassen sich bei Bedarf aufklappen; Geräte, die noch etwas brauchen, sind offen.

## 0.38.0

- go-e: Der Ladestrom wird als vorübergehender Strom („amx“) per MQTT gesetzt, wie evcc es macht – er landet nicht im Flash der Wallbox. Das Thema ist „go-eCharger/<Seriennummer>“ (Standard der Integration). Beim Zurückgeben gilt wieder der gespeicherte Strom.
- In Viertelstunden, in denen nur ein Teil der Ladeleistung (aus PV) geplant ist, folgt der Strom jede Minute dem tatsächlichen PV-Überschuss: mehr PV als vorhergesagt geht ins Auto, weniger wird abgezogen, ebenso Netzbezug. Reicht es nicht für den Mindeststrom, wird das Laden gesperrt (mit Hysterese, damit es nicht ständig schaltet).

## 0.37.0

Mehrere Fahrzeuge an einer Wallbox:

- Unter Geräte gibt es den Bereich „Fahrzeuge“: Name, Akku, Ladestand-Sensor (z. B. MQTT des Autos), optional „angesteckt/zuhause“-Sensor, Karte an der Wallbox, Ziel-Ladestand, „fertig bis“, und an welchen Wallboxen es lädt. Eine Karte gehört je Wallbox zu höchstens einem Fahrzeug.
- Erkennung des angesteckten Fahrzeugs, je Wallbox einzeln abschaltbar und in dieser Reihenfolge: gescannte Karte, Auswahl unter Steuerung (gilt bis zum Abstecken), Sensor des Fahrzeugs, einziges zugeordnetes Fahrzeug. Sunflow plant dann mit Ladestand, Akku und Zielen dieses Fahrzeugs.
- Ist eine Karte nötig und das Fahrzeug über Sensor oder Auswahl sicher erkannt, startet Sunflow den Ladevorgang selbst mit der Karte des Fahrzeugs (je Wallbox abschaltbar). „Einziges Fahrzeug“ allein genügt dafür nicht.
- Fremde Karte oder nichts erkannt: Sunflow hält sich raus, die Wallbox regelt selbst.

## 0.36.0

Steuerung von Ladepunkten (abschaltbar, standardmäßig aus):

- Wallbox und Heizstab lassen sich wahlweise über evcc oder direkt steuern. Neues Geräteprofil für den go-e Charger über die Integration „go-eCharger (MQTT)“.
- Den Ladestand des Fahrzeugs wählt man beim go-e frei aus (z. B. ein MQTT-Sensor des Autos), unter Geräte direkt bei der Wallbox.
- Unter „Steuerung → Sunflow steuert“ erscheinen auch die Ladepunkte mit eigenem Schalter. In den geplanten Viertelstunden wird geladen, sonst gesperrt; ohne aktuellen Plan oder beim Beenden der App regelt das Gerät wieder selbst.
- go-e: Ladestrom aus der geplanten Leistung (Phasen, 6–16 A einstellbar). Der Strom wird im Flash der Wallbox gespeichert und deshalb erst ab 2 A Unterschied geändert, höchstens 20-mal am Tag.
- evcc: „Schnell“ bei voller geplanter Leistung, „PV“ bei weniger (evcc folgt dem Überschuss), „Aus“ sonst; beim Zurückgeben gilt wieder der vorherige Modus.

## 0.35.0

Steuerung des Hausspeichers (abschaltbar, standardmäßig aus):

- Unter „Steuerung“ gibt es „Sunflow steuert“ mit Hauptschalter und einem Schalter je Speicher. Erst wenn beide an sind und der Gerätetest bestanden ist, setzt Sunflow den Plan am Wechselrichter um; das Einschalten muss bestätigt werden.
- Jede Minute wird die Aktion der laufenden Viertelstunde übertragen (beim Sofar das Fenster im Passiv-Modus). Geschrieben wird nur, wenn der Wechselrichter andere Werte zeigt – nach einem Neustart des Wechselrichters stehen sie so spätestens nach einer Minute wieder.
- Beim ersten Einschalten wird der Betriebsmodus einmal umgestellt (beim Sofar „Passive Mode“, EEPROM-Schutz bleibt aktiv). Ausschalten stellt Eigenverbrauch und den vorherigen Modus wieder her; der vorherige Modus wird in einer Datei gemerkt und übersteht einen Neustart der App.
- Absicherung: Ohne aktuellen Plan (älter als 30 Minuten) oder beim Beenden der App wird auf Eigenverbrauch gestellt. Steuert evcc gerade selbst den Hausspeicher (Halten/Laden), pausiert Sunflow.
- Die Planung unterscheidet jetzt, ob eine Abweichung vom Plan von einer anderen Steuerung kommt oder trotz Sunflow-Steuerung auftritt.

## 0.34.0

- PV-Lernen: Unter Analyse → Lernen gibt es je Dachfläche „Neu lernen ab heute“. Ältere Messwerte dieser Fläche (z. B. mit Laub bedeckt, vor einem Baumschnitt oder Modultausch) zählen dann nicht mehr; bis genug neue Tage da sind, gilt für sie die Korrektur der ganzen Anlage.
- PV-Lernen: Neuere Tage zählen mehr (Halbwertszeit 30 Tage). Die Korrektur folgt dadurch Verschmutzung, Laub und dem Lauf der Jahreszeit, statt alle 120 Tage gleich zu gewichten.

## 0.33.5

- PV-Lernen: Die Korrektur der Prognose kann jetzt bis auf 10 % statt nur 30 % sinken. Eine im Winter stark verschattete Fläche (z. B. der Carport mit etwa 20 % der Solcast-Prognose) wurde sonst dauerhaft zu hoch eingeplant.

## 0.33.4

- Planung: Bei gewählter Skalierung steigt eine Linie bis zum oberen Rand, wenn der Wert darüber liegt, und wird dort abgeschnitten; oberhalb wird nichts gezeichnet.

## 0.33.3

- Planung: Bei gewählter Skalierung werden Werte über dem gewählten Maximum nicht gezeichnet; die Linie setzt dort aus. Beim Darüberfahren erscheint weiterhin der tatsächliche Wert.

## 0.33.2

- Planung: Bei gewählter Skalierung laufen Spitzen darüber als waagerechte Linie am oberen Rand entlang, statt zu verschwinden. Ein erneuter Klick auf eine Zahl der Achse stellt die ganze Höhe wieder her.

## 0.33.1

Empfehlung „Wirkungsgrad Speicher“ springt nicht mehr:

- Die Tagesbilanzen für die Aufteilung in Umwandlungsverlust und Eigenverbrauch beginnen immer um Mitternacht (UTC). Vorher verschoben sich alle Tagesfenster mit jeder Stunde des gleitenden 90-Tage-Zeitraums, und der Wert änderte sich stündlich deutlich.
- Einzelne Ausreißertage (z. B. ein Sprung im Ladestand durch Kalibrierung) werden erkannt und fließen nicht ein.
- Empfohlen wird nur noch der Wert ohne Eigenverbrauch. Vorher wechselte die Empfehlung zum Gesamtwert mit Eigenverbrauch, wenn die Aufteilung gerade nicht möglich war – mehrere Prozentpunkte Unterschied.
- Der Vorschlag ist auf halbe Prozent gerundet.

## 0.33.0

- Planung: In „PV-Erzeugung und Hausverbrauch“ und „Netzbezug und Einspeisung“ skaliert ein Klick auf eine Zahl der senkrechten Achse das Diagramm bis zu diesem Wert; Leistungsspitzen darüber werden abgeschnitten, damit der Rest lesbar bleibt. „↺ ganze Höhe“ oben rechts stellt die automatische Skalierung wieder her.

## 0.32.11

- Der Schutz der Home-Assistant-Datenbank löst seltener aus: Ein einzelner „unknown_error“ sperrt nicht mehr alle Abfragen, sondern Sunflow fragt zuerst den Recorder, ob er wirklich hinterherhängt. Nur dann – oder wenn eine Abfrage 60 s ohne Antwort bleibt – wird pausiert, jetzt für 2 statt 5 Minuten.
- Die Meldung „Abfragen pausiert“ nennt die verbleibende Zeit und den Auslöser.

## 0.32.10

- Planung: Antwortet die Datenbank von Home Assistant nicht, wird der Plan trotzdem angezeigt. Vorher meldete die Seite „Plan konnte nicht berechnet werden“, obwohl nur der gemessene Verlauf für die Diagramme fehlte. Jetzt erscheint ein Hinweis beim Verlauf, der zuletzt geladene bleibt stehen.

## 0.32.9

- Live: Die Tageskennzahlen verschwinden nicht mehr, wenn die Datenbank von Home Assistant nicht antwortet. Die zuletzt geladenen Werte bleiben stehen, mit dem Hinweis „nicht aktuell“; ohne frühere Werte erscheint der Grund.

## 0.32.8

- Live: „Aktuelle Energieflüsse“ und „Stand …“ linksbündig.

## 0.32.7

- Live: „Aktuelle Energieflüsse“ und „Stand …“ rechtsbündig, ohne Einzug vor „Stand“.

## 0.32.6

- Live: „Stand …“ ohne vorangestellten Punkt und etwas heller.

## 0.32.5

- Live: „Aktuelle Energieflüsse · Stand …“ bricht neben dem Wetter höchstens in zwei Zeilen um statt Wort für Wort.

## 0.32.4

- Live: Die Wetteranzeige ist kompakter und bleibt auf breiten Bildschirmen rechts neben dem Titel; die Vorhersage wird dafür schmaler und lässt sich weiterhin wischen oder per Pfeil blättern.

## 0.32.3

- Live: Pfeile links und rechts an der Wettervorhersage blättern weiter; sie erscheinen nur, wenn in die Richtung noch Stunden oder Tage verborgen sind.

## 0.32.2

- Live: Die Wettervorhersage scrollt ohne sichtbaren Scrollbalken.

## 0.32.1

- Live: Durch die Wettervorhersage lässt sich wischen (am PC mit gedrückter Maustaste ziehen): bis zu 48 Stunden bzw. 16 Tage, so weit die Wetter-Entität sie liefert.

## 0.32.0

- Live: Die Wetteranzeige lässt sich zwischen Stunden und Tagen umschalten. Die Tagesansicht zeigt bis zu 7 Tage mit Höchst- und Tiefsttemperatur (Regenmenge beim Darüberfahren). Die Auswahl wird im Browser gemerkt; liefert die Wetter-Entität keine Tagesvorhersage, bleibt es bei den Stunden.

## 0.31.2

- Die Änderungen aus 0.31.0 und 0.31.1 (Schonung der Home-Assistant-Datenbank, kein Protokoll pro Anfrage) waren mit 0.30.7 versehentlich zurückgenommen worden und sind wieder enthalten.
- Einstellungen: Das Prozentfeld der Notstrom-Reserve war auf Häkchengröße geschrumpft und ließ sich nicht bedienen.
- Einstellungen: Die Notstrom-Reserve steht jetzt nach „Spätestens nach … aus dem Netz“.

## 0.31.1

- Keine Protokollzeile mehr pro Anfrage der Oberfläche (die Live-Ansicht fragt alle 5 Sekunden ab); das Protokoll der App bleibt klein und zeigt nur noch Hinweise und Fehler.

## 0.31.0

Schonung der Home-Assistant-Datenbank:

- Vor Abfragen von Statistik und Verlauf prüft Sunflow, ob die Datenbank von Home Assistant hinterherkommt (Warteschlange, Recorder läuft). Ist sie ausgelastet oder antwortet sie nicht (Zeitüberschreitung, „unknown_error“), pausiert Sunflow alle Datenbankabfragen für 5 Minuten, statt weitere hinterherzuschicken.
- Verläufe für die Planung (Hausverbrauch, Verbraucher, letzte Vollladung) werden höchstens einmal pro Stunde abgefragt, gelernte Gewohnheiten (Abfahrtszeit, Verbrauch eigener Geräte) alle 6 Stunden. Bei einem Fehler rechnet Sunflow mit den zuletzt bekannten Werten weiter.
- Eine fehlgeschlagene Neuplanung wird mit wachsendem Abstand wiederholt: 5, 10, 20, höchstens 30 Minuten.

## 0.30.6

- Schattenbetrieb: Schlägt die Neuplanung fehl, wird trotzdem weiter gemessen. Vorher fehlten dann alle Messwerte, bis wieder ein Plan gelang.
- Eine fehlgeschlagene Neuplanung wird nach 5 Minuten wiederholt statt jede Minute; Home Assistant wird bei einem Problem nicht mehr minütlich mit allen Abfragen belastet.
- Planung: Ist die Neuberechnung fehlgeschlagen, sagt eine Warnung, von wann der angezeigte Plan stammt und warum es keinen neueren gibt.

## 0.30.5

- Planung: Schlägt in Home Assistant eine einzelne Abfrage fehl (z. B. die PV-Prognose einer Fläche, der Verlauf des Hausverbrauchs oder eines Verbrauchers, die Statistik zur letzten Vollladung), wird der Plan trotzdem berechnet. Eine Warnung nennt, welche Quelle fehlte und mit welcher Fehlermeldung. Vorher brach der Plan mit „unknown_error: Unknown error“ ohne Hinweis auf die Ursache ab.

## 0.30.4

- Planung: Der eingeblendete Gesamtverbrauch lässt sich über seinen Legendeneintrag hervorheben wie die anderen Linien; ein- und ausgeblendet wird nur über das Häkchen.

## 0.30.3

- Planung: Im Diagramm „PV-Erzeugung und Hausverbrauch“ lässt sich zusätzlich der Gesamtverbrauch einblenden (Hausverbrauch mit steuerbaren Verbrauchern, gemessen und geplant). Die Auswahl wird im Browser gemerkt.

## 0.30.2

- Planung: Die gemessene Linie „Hausverbrauch“ enthält die steuerbaren Verbraucher (Wallbox, Heizstab, E-Bike) nicht mehr doppelt – sie haben eigene Linien, und die Prognose rechnet ebenfalls ohne sie.
- Analyse → Schattenbetrieb: Die Lastprognose wird mit dem gemessenen Verbrauch ohne steuerbare Verbraucher verglichen; vorher ließ ein Ladevorgang die Prognose zu niedrig aussehen.

## 0.30.1

- Analyse → Wirkungsgrade: Tage mit wenig Energie (im Herbst und Winter häufig) bleiben nicht mehr leer, sondern werden mit den folgenden Tagen zu einem Wert zusammengefasst. Die Linien reichen dadurch wieder bis an die letzten Tage.
- Diagramme: Einzelne Werte ohne Nachbarn werden als Punkt gezeichnet statt unsichtbar zu bleiben.

## 0.30.0

- Notstrom-Reserve gilt jetzt in jeder Strategie (Ausgewogen, PV aufnehmen, Autarkie): eigener Schalter mit Prozentwert unter Einstellungen → Planung. Im Alltag wird der Speicher nie unter die Reserve geplant; sie bleibt dem Notstrom bei Stromausfall vorbehalten.
- Die Strategie „Notstrom sicherstellen“ entfällt. Bestehende Einstellungen werden automatisch umgestellt (Strategie „Ausgewogen“, Reserve eingeschaltet mit dem bisherigen Wert); das Original bleibt als `config.v1.json` erhalten.

## 0.29.1

- Einstellungen: „Netzdienlich“ erklärt genauer, wie die Grenzen wirken (weiches Ziel, keine Abregelung, kein Ersatz für die Begrenzung im Wechselrichter).

## 0.29.0

- Datenmigration zwischen Versionen: Die Konfiguration wird bei einem Update schrittweise auf den neuen Stand gebracht; das Original bleibt als `config.v<n>.json` erhalten.
- Nach einem Downgrade startet Sunflow auch mit einer Konfiguration der neueren Version: Werte, die die ältere Version nicht kennt, nehmen den Standardwert an (Hinweis im Log).
- Die Schattendaten-Datenbank hat eine Schema-Version und wird ebenfalls schrittweise aktualisiert. Exporte älterer und neuerer Versionen lassen sich importieren.

## 0.28.2

- Analyse: Das Diagramm „Kosten pro Tag“ hat eine Legende.

## 0.28.1

- Planung: Jeder steuerbare Verbraucher hat im Diagramm „PV-Erzeugung und Hausverbrauch“ eine eigene Farbe (gemessen und geplant), passend zur Karte unter „Steuerbare Verbraucher“.
- Ein Klick auf einen Legendeneintrag hebt dessen Linien hervor und blendet die anderen ab; ein zweiter Klick zeigt wieder alle.

## 0.28.0

- PV-Prognose lernt je Dachfläche: Unter Einstellungen → Tarif & Prognose lässt sich jeder Prognose-Fläche zuordnen, von welchen PV-Strings sie gemessen wird. Sunflow lernt dann die Abweichung jeder Fläche einzeln (z. B. Verschattung am Vormittag) und korrigiert die Planung flächengenau. Anzeige unter Analyse → Lernen.

## 0.27.1

- Energy values entered in kWh keep three decimals: small daily needs such as 0,04 kWh
  were shown as 0 and could be overwritten with 0 when saving again

## 0.27.0

- Analyse → "Kosten pro Tag" (30/90 days): net electricity cost per day (import minus
  feed-in) next to what it would have cost without PV and battery, from the energy
  counters and the price history of the tariff entity; totals for net cost, savings
  and feed-in revenue
- Planung → "Kostenverlauf": accumulated electricity cost over the plan with Sunflow
  against plain self-consumption

## 0.26.0

- Grid friendly option (Standards), combinable with every strategy: export and import
  above set powers cost 20 ct/kWh extra in the plan. The battery then charges in the
  midday PV peak instead of filling up in the morning, and large consumers run one
  after another instead of together, as long as that costs little

## 0.25.0

- New battery strategy "Autarkie": as little grid import as possible instead of the
  lowest cost. Every imported kWh counts far above price differences, no grid
  charging, the battery covers demand right away, loads go to the PV surplus.
  Selectable in Standards and Steuerung

## 0.24.0

- Own consumers with a power sensor learn their use from the last 10 days: daily
  energy (mean) and running power (90th percentile while on). Without a set daily
  energy the plan uses the learned one; when the settings differ by more than 30 %, a
  recommendation offers the measured values. The consumer list shows them

## 0.23.1

- "Hausverbrauch pro Tag" is a start value: it scales the load forecast only while less
  than 7 days of consumption history exist; afterwards the measured hourly means count
  (and from 14 days the learned model). Standards shows a suggestion from the history
  (base load without large loads) to take over. The recommendation to change the daily
  consumption is dropped

## 0.23.0

- Learned departure of the car: from the last 14 days of the loadpoint's "connected"
  history, the typical unplug time per workday/weekend (median of stays of at least
  2 h, from 3 sessions on) becomes the deadline when the car is plugged in without an
  evcc plan or a "ready by" time. Planung marks it as "übliche Abfahrt, gelernt"

## 0.22.0

- PV forecast uncertainty: the plan can lean towards the pessimistic forecast (Solcast
  p10). Standards → "Unsichere PV-Prognose": expected value, balanced (default, 30 %
  of the way to p10) or cautious (70 %). Forecast.Solar planes take the relative
  range of Solcast in the same interval instead of counting as 0 in p10. The learned
  PV correction applies to both values

## 0.21.0

- Learned load forecast: shadow mode now stores the forecast outside temperature per
  slot. From 14 days of such data a regression model learns the base load per hour,
  the weekend difference and the heating (and cooling) share of the temperature;
  recent days weigh more. The plan uses it once it beats the hourly average on held
  out days. Analyse → "Lernen" shows its state and accuracy

## 0.20.1

- Shadow mode records the power of the controllable consumers; the load recommendation
  compares the forecast with the measured base load without them (a heating rod below
  the large-load threshold no longer counts as household consumption). It appears once
  three days of such data exist

## 0.20.0

- Recommendations (Analyse, count as badge on the menu): Sunflow checks settings, plan,
  shadow mode and energy counters and suggests changes, where possible with one click:
  battery efficiency from the counters, missing daily energy or heating price, priority
  limits that keep PV from hot water, plan more expensive than self-consumption, base
  load forecast off by more than 10 % (large loads left out), implausible hot water
  demand, PV learning off or no weather entity with cloud coverage. Dismissed
  recommendations return when their numbers change

## 0.19.5

- Entity assignment: German names for all roles (PV strings, energy counters, plan
  values); values rounding to zero show as "0 W" instead of "−0 W"

## 0.19.4

- Sofar battery and PV power in 10 W steps: the live value is the sum of the per input
  values (0.01 kW) instead of the totals (0.1 kW); statistics keep using the totals
- Units as before: W below 1 kW, kW above with two decimals (never finer than the
  source entity resolves)

## 0.19.3

- Powers in W are always shown without decimals (e.g. Tibber Pulse "75 W" instead
  of "75,00 W"); kW keeps the entity's precision

## 0.19.2

- Live: weather on the right in the title line (wraps below on narrow screens)

## 0.19.1

- Live and Steuerung show powers like their source entity in Home Assistant: in the
  entity's unit (W or kW) with its display precision (the user's setting, else the
  integration's). Values coming from a coarse entity (e.g. Sofar battery power total
  in 0.1 kW steps) therefore look exactly like in the integration

## 0.19.0

- Live: weather strip below the title with the current condition, temperature and
  cloud coverage and the next 8 hours (symbol, temperature, cloud coverage;
  precipitation as tooltip), from the weather entity chosen in Standards. The forecast
  is cached for 10 minutes

## 0.18.1

- Weather choice: shows the provider ("Home (Open-Meteo)") and checks the cloud
  coverage in the hourly forecast that Sunflow uses. Open-Meteo was wrongly marked
  "ohne Bewölkungsangabe" because its current state has no cloud coverage

## 0.18.0

- PV forecast learns from this site: shadow mode now also stores the uncorrected
  forecast and the forecast cloud coverage of a weather entity (e.g. met.no). From
  forecast vs. measurement, correction factors are learned per hour and cloud class
  (clear, mixed, overcast), cautiously with little data, and applied to the plan
- Standards: switch "PV-Prognose an die Anlage anpassen" and choice of the weather
  entity; Analyse: learned factors and mean deviation before/after; Planung: hint
  how much the forecast was adapted

## 0.17.2

- Fix "401: Unauthorized" after a while inside Home Assistant: the Supervisor expires
  the Ingress session when HA's panel is in the background. Sunflow now renews it
  through HA's connection in the surrounding page and repeats the request, and renews
  it right away when the tab comes back after more than 5 minutes. If renewing is not
  possible, a clear message asks to reload

## 0.17.1

- Battery care: "Spätestens nach … Tagen aus dem Netz". When the battery was not full
  for that long (e.g. in winter), the full charge is planned from the grid by the end of
  the cheapest price window of the next 24 h, also when grid charging is otherwise off;
  PV available before is still used first

## 0.17.0

- Battery care (Standards): everyday charge limit (e.g. 90 %) and a full charge every
  N days for balancing and calibration. The last full charge is read from the SoC
  statistics; when due, the limit is lifted and the full charge is aimed at the end of
  the PV surplus of that day (soft target, no forced grid charging). At the limit the
  plan limits charging instead of self-consumption. The limit appears in the SoC chart
- Weekdays in plan hints in German ("Mo 16:30" instead of "Mon 16:30")

## 0.16.0

- Standards: battery wear either directly in ct/kWh or calculated from purchase price,
  rated cycles, capacity, depth of discharge and remaining capacity at end of life
  (price / (cycles x mean capacity x depth of discharge)); the inputs are kept

## 0.15.3

- Planung: the measured lines (SoC, PV, consumption, grid import and export) reach up
  to "now": the latest live reading closes the gap left by Home Assistant's statistics,
  which lag up to a quarter of an hour. The view refreshes every 2 minutes

## 0.15.2

- Planung: below "Geplante Steuerung jetzt" the tile shows what the battery really does
  ("lädt 2,9 kW aus dem Netz"); when that differs from the plan, a hint says that
  another control is active (Sunflow only plans so far)
- Live: no "0 W + 0 W" in the PV node when the string row is shown

## 0.15.1

- Planning no longer stops with "Ladestand nicht lesbar" when the SoC sensor is
  briefly "unknown" right after an app start: the last valid value from the Home
  Assistant history (up to 6 h old) is used; a hint appears only when it is older
  than 30 minutes
- Live: details inside the circles ("kein Auto", "ausgeglichen", sums) shrink to fit
  instead of overlapping the circle

## 0.15.0

- Live: PV and battery stay in the diagram at 0 W (inverters reporting "unavailable"
  at night count as 0 W)
- Live: PV per string as a row of small nodes flowing into the PV total (Sofar: power
  per MPPT; Huawei: voltage x current per string; DC values)

## 0.14.2

- Own consumers can be edited after adding them ("Bearbeiten": name, switch, power
  sensor, power, daily energy) and removed directly in the list; planning settings
  stay unchanged. Adding or editing them replans right away

## 0.14.1

- Short "unknown"/"unavailable" phases of sensors (e.g. single failed Modbus reads of
  the Sofar SoC, every ~20 minutes) are bridged with the last valid value for up to
  15 minutes, so the plan no longer fails with "Ladestand nicht lesbar"
- Live: the battery stays in the diagram when only its SoC is missing; consumers
  always fit into the diagram (it grows without a battery row); today's figures sit
  at the bottom of the screen
- PC: main menu centred

## 0.14.0

- Analyse: new chart "Wirkungsgrade" (30 days, 90 days, 1 year) with one value per
  day for every battery (round trip incl. standby, corrected by the SoC change) and
  every inverter with AC and DC counters (e.g. Huawei); energy-weighted average in the
  legend. Days with little energy or gaps in the statistics stay empty
- Charts support an axis not starting at zero and label long ranges with dates

## 0.13.0

- New parameter "Wirkungsgrad Wechselrichter" (per direction, default 96,5 %). Grid
  charging and discharging to the house pass the inverter's AC/DC conversion, PV
  charging on the DC side of a hybrid inverter does not, so the plan now tells PV
  charging and grid charging apart
- "Wirkungsgrad Speicher" now means the battery at its terminals (cells and DC/DC),
  exactly what the battery energy counters measure; the suggestion from the counters
  can be taken over as is. Check the value after the update: a value entered as
  overall round trip before now counts the inverter twice

## 0.12.1

- Fix: the "Expertenmodus" switch text was cut off when the step list needed much
  room; the step column has a fixed width and its styles no longer leak into the
  main menu

## 0.12.0

- Main menu with icons: on the PC at the top with icon and text, stays in view while
  scrolling; on phones a fixed icon bar at the bottom (names as tooltip and for
  screen readers)

## 0.11.4

- Expertenmodus only for technical details: battery efficiency, vehicle charging
  efficiency and daily hot water demand are always visible in Kenndaten. Manual entity
  assignment ("Entitäten zuordnen"), role table, sub-devices, the grid offset and the
  device test's service calls stay in expert mode; the assignment is offered anyway
  when a required value was not found

## 0.11.3

- "Expertenmodus" switch in its own row below the setup steps, always visible on
  phones (was at the end of the scrolling step row)

## 0.11.2

- Charts: value labels sit on their grid lines inside the plot, so there is no empty
  margin on the left; all charts keep a common time axis
- Phone: outer margins halved (8 px)

## 0.11.1

- Inside Home Assistant (Ingress panel) the own logo and name are hidden: HA's toolbar
  already shows them. A lost connection is then shown as a hint below the tabs
- Phone: smaller tabs so all five fit better

## 0.11.0

- Fix: the Forecast.Solar forecast was one hour late. Its watt_hours_period is the
  energy of the interval ending at the timestamp; Home Assistant's hourly aggregation
  files it under the following hour. Sunflow now reads the raw series and spreads each
  interval onto its 30 minute grid (independent of daylight saving time)
- Battery efficiency: with at least 7 days of energy counters, the losses are split
  into conversion (per kWh, used for planning) and the battery's constant standby
  draw (BMS, DC/DC converters), shown in W
- Phone layout: no frame around the content, compact header and step row, charts sized
  to the screen with readable labels, larger text in the Live diagram
- "Expertenmodus" moved from the header to the end of the setup steps and is
  remembered in the browser

## 0.10.2

- "Selbstverbrauch wiederherstellen": the device test stores a marker in /data from
  its first write until the restore succeeded. If the app crashed, the connection to
  Home Assistant was lost or the restore failed, every view shows a hint with a button
  that sets self-consumption and the previous operating mode again (after
  confirmation; nothing is written automatically)

## 0.10.1

- Kenndaten: devices can be removed ("Entfernen", with confirmation); overrides,
  priority entry, limits and measurement choice referring to them are cleaned up
- Wording: "Steuerbare Verbraucher" instead of "Ladepunkte" in Planung; the finished
  setup points to Live, Steuerung and Planung; German decimals for plane capacity and
  minimum SoC

## 0.10.0

Robustness and safety
- Commands to Home Assistant time out after 60 s; a malformed message no longer stops
  the connection reader; a dead connection is detected and re-established; caches are
  refreshed after a reconnect. Before, one unanswered command could block the whole UI
- The background runner never stops on unexpected errors, retries a failed replan on
  the next minute and replans after every settings change
- Runner, samples and /plan use the shared snapshot cache; without a plan yet, all
  tabs wait for one shared computation instead of each computing one
- As a Home Assistant App the API only answers the Ingress proxy (health excepted)
- The action and setup endpoints only show service calls; real writes remain
  exclusively in the confirmed device test
- config.json is written with fsync and keeps config.json.bak; fields of newer versions
  survive; a broken file gives a clear message; NaN/Infinity are rejected
- Shadow database access is serialized; unreadable imports give a clear error

Planning
- New actions "Entladen begrenzt" and "Laden begrenzt": partial discharges were
  shown as "Speicher halten", which would have left the planned discharge unused
- Calmer export: no feed-in before the battery is full when that costs nothing
- Priority limits no longer block the battery for the whole horizon when a car cannot
  reach its level (e.g. unplugged before); planning with limits takes ~1 s instead of
  up to 11 s
- Fair comparison "ohne Sunflow": optional loads (Nur PV, or with an alternative heat
  source) only use PV surplus there; costs include energy covered by the alternative
- Warning when the hot water tank has no cost of its usual heating
- Without grid charging / battery export, limits apply to the surplus over the house load
- Load forecast scales each calendar day to the daily consumption

## 0.9.2

- Priority limits can refer to the hot water tank (in °C), e.g. wallbox only from
  55 °C hot water, home battery only from 60 °C hot water and 80 % car
- Setup opens even when Home Assistant is unreachable, with "Erneut versuchen"
- Fixed: a failed fixed-price save lost the typed price and "Weiter" moved on;
  renaming a forecast plane could be overwritten by a quick assignment; typed numbers
  in Standards were reverted when changing the strategy; the device step could fail
  to render after changing a role; older previews could overwrite newer ones
- Analyse: period switch keeps the selected period, errors are shown, import is a
  keyboard reachable button with progress
- Steuerung: cards fit phone width, unknown power shows "–", errors are kept
- Dark mode: text on filled buttons and step dots has sufficient contrast
- Wizard timers pause in hidden tabs like the views

## 0.9.1

- New icon: the sun with three energy flows to storage, house and car. Used for the
  app, the integration (local brand folder, no brands repository entry needed) and
  the header of the UI

## 0.9.0

- New profile: Tibber Pulse via the tibber_local integration (grid power 16.7.0,
  meter readings 1.8.0 / 2.8.0)
- Messquellen (setup step "Bilanzprüfung"): choose which device measures the grid and
  the house consumption. "Automatisch" prefers a dedicated meter over the inverter.
  Applies to Live, history, energy balance and shadow mode. Before, grid counters of
  several devices would have been added up in the energy balance

## 0.8.0

- The home battery can have priority limits too: ranked below a vehicle, it only
  charges from PV once the car reached the set level (grid charging stays possible)
- Settings: "Weiter", "Zurück", the step list and leaving the tab save unsaved
  edits of the current step (Geräte, Kenndaten, fixed price, Standards). Invalid
  values keep you on the step with an error message

## 0.7.7

- Much less load on Home Assistant: registries (devices, entities, integrations) are
  cached for 2 minutes and all states for 4 seconds, shared by all requests. Before,
  every poll of every open tab fetched several MB from Home Assistant (every 5 s in Live)
- Views stop polling while the browser tab is hidden and never overlap requests;
  requests time out after 60 s instead of hanging

## 0.7.6

- Priority limits pointing to a lower entry are no longer deleted when reordering:
  they are hidden, not used, and apply again once the entry moves up

## 0.7.5

- Priority limits only refer to higher entries: the top entry has none, the second
  can wait for the first, and so on. Reordering drops limits that would point to a
  lower entry

## 0.7.4

- Steuerung → Priorität: buttons in the limits form ("Übernehmen", "Abbrechen") were
  cut off to the width of the arrow buttons

## 0.7.3

- Planung: the price chart uses the same time axis as the charts above (history
  plus plan) and marks "now"; all charts share the same left margin so the time
  axes line up

## 0.7.2

- Fix: planning failed with an internal error as soon as a priority limit on the
  home battery was set (since 0.6.0)
- Server errors show the HTTP status instead of a JSON parse error

## 0.7.1

- Battery efficiency suggestion counts as reliable only from 10 full cycles (was 2);
  the counter based estimate looks back 90 days instead of 30

## 0.7.0

- Energy counters (kWh) are used where available: Sofar PV, consumption, import,
  export, battery charge/discharge; Huawei AC yield and DC input. Grid counters of
  the HA energy dashboard (utility meter) take precedence over inverter counters
- Live "Heute" and the new Analyse section "Energiebilanz" (today, 7 days, 30 days)
  come from the counters: autarky, self-consumption, battery in/out, inverter
  efficiency (AC/DC). Integrated power means remain the fallback
- Battery efficiency suggestion uses the charge/discharge counters of the last 30 days

## 0.6.0

- Priority limits: a consumer in the priority list can wait until the home battery
  and/or a vehicle reached a level (e.g. hot water only from 80 % home battery and
  90 % car). Until then it gets no PV or battery energy and only runs on grid power
  if its target requires it. A vehicle only holds others back while it is plugged in
  and charging; a limit above its target counts as met at the target. "Sofort"
  ignores the limits

## 0.5.4

- Steuerung: consumers offer Kostenoptimal, Nur PV, Sofort and Pause; the default from
  the settings is marked "Standard". Before, a device set to "Nur PV" by default could
  not be switched to cost optimal

## 0.5.3

- Hot water and daily consumers are planned for every day of the horizon: after the
  first need, each further 24 h window gets its own daily need. Before, a tank heated
  on the first day got no PV surplus on the second day although energy was exported
- New advanced heater parameter "Energiebedarf pro Tag" (default: tank cools by 10 K
  per day, about 3.5 kWh for 300 l)

## 0.5.2

- Live view fits the window without scrolling: the flow diagram scales to the
  available height, title and today's tiles are more compact
- More compact layout everywhere: smaller header, spacing and headings, flatter charts

## 0.5.1

- Ladestand chart shows the limits: full capacity, discharge limit (min SoC)
  and the backup reserve while the backup strategy is active

## 0.5.0

- New tab layout: Live | Steuerung | Planung | Analyse | Einstellungen
- Steuerung: switch the battery strategy and each consumer (Standard, Nur PV,
  Sofort, Pause) or its target with one click, for today, 4 hours or until
  further notice; the plan is recomputed right away
- Priorities: order consumers and the home battery; consumers below the battery
  only get surplus the battery cannot take
- Defaults for strategy, wear, grid charging, backup reserve and consumption moved
  to the new setup step "Standards"
- Analyse holds the shadow mode comparison and data export/import
- Planung shows the last computed plan instantly instead of recomputing on open

## 0.4.3

- evcc loadpoints are recognized when Home Assistant runs in German: the evcc
  integration names them "Ladepunkt …" instead of "Loadpoint …"

## 0.4.2

- Live view adapts to the configuration: components that are not configured are
  hidden with their lines (no PV, no battery, no grid measurement)
- Several batteries are shown as one node (summed power, capacity-weighted SoC)
  with the single values below
- Grid and house consumption come from any device that measures them

## 0.4.1

- Plans fill the battery first and export afterwards (export early only paid off if
  the PV forecast held; a small risk premium now prefers late export)
- Export is limited to the current PV: a fixed feed-in tariff does not pay for
  stored grid energy. This also removes a slow solver case with cheap night prices
- Solver stops at 0.1 % from the optimum and keeps the best plan on its time limit

## 0.4.0

- New "Live" tab: animated power flows between PV, grid, battery (with SoC ring),
  house and consumers (wallbox, hot water, own consumers), refreshed every 5 s
- Today: PV, consumption, grid import, export, autarky and self-consumption

## 0.3.6

- Battery efficiency is now the round trip (charge + discharge), as usually stated;
  the planner splits it evenly. Stored values are read as round trip (default 90 %)
- The history estimate shows and applies the round-trip value

## 0.3.5

- Shadow mode classifies what the battery did from the grid meter and battery power;
  coarse DC PV readings caused false "Entladen ins Netz" entries (also for old data)
- The data export contains the current plan (plan.json) for analysis

## 0.3.4

- Clearer wording for whether the home battery may discharge into a consumer

## 0.3.3

- Chart axes: round steps (1, 2 or 5 x 10^n, up to six intervals), no decimals for
  whole numbers and values from 10 on

## 0.3.2

- Searchable entity choice (type to filter by name or entity id, keyboard support)
  for own consumers and for adjusting role mappings

## 0.3.1

- Dropdowns no longer close right after opening (selects were wrapped in labels)

## 0.3.0

- Planning strategy per loadpoint and consumer: cost-optimal, PV surplus only or
  as fast as possible; own target, daily ready-by time, home battery allowed or not
- Alternative heat source (district heating, gas) for hot water: electric heating
  only when cheaper than the alternative
- Own consumers (Shelly, ESPHome, any switch) with daily energy need, planned like
  loadpoints; their measured power is separated from the base load

## 0.2.6

- Calmer plans: when several schedules cost the same (e.g. full battery, PV surplus
  split between export and the heater), the smooth one is chosen instead of a zigzag

## 0.2.5

- Shadow mode: deviation is shown as actual relative to the forecast
  (e.g. "−40 % ggü. Prognose" when less was produced than forecast)

## 0.2.4

- Chart y-axis labels are no longer cut off on the left

## 0.2.3

- PV forecast chart in the setup wizard: one color per forecast plane (stacked),
  legend with provider, plane, plant and energy

## 0.2.2

- Forecast.Solar planes without a specific device name get numbered names
- Each forecast plane shows today's forecast to tell planes apart
- Forecast planes can be renamed in the setup wizard

## 0.2.1

- Fix "Connection lost" on larger installations: websocket messages above 4 MB
  (states, registries) are now accepted
- Log why the connection to Home Assistant closes

## 0.2.0

- Installable as Home Assistant App (code is fetched from GitHub at build time)
- Setup wizard, overview with forecasts, planning and shadow mode
- Data export for analysis on another machine

## 0.1.0

- Initial project structure
