# Changelog

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
