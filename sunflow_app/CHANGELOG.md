# Changelog

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
