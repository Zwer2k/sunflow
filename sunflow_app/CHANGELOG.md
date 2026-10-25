# Changelog

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
