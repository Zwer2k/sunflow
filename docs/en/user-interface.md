# User interface

[Deutsch](../bedienung.md) · **English**

The top bar switches between the five views: **Live**, **Steuerung** (control), **Planung**
(planning), **Analyse** (analysis) and **Einstellungen** (settings). On phones they are icons at
the bottom. A number on "Analyse" shows open recommendations.

## Live

![Live](../images/live.png)

The energy flow shows what is flowing right now: PV (top, with one circle per string), grid,
home battery, house and the controllable loads such as wallbox and hot water. Animated lines show
where energy is going.

- **Haus** (house) shows consumption without the controllable loads; "gesamt" (total) below
  includes them.
- **Batterie** (battery) shows power, direction and state of charge; the ring is the state of charge.
- **Wallbox** shows charging power and the state of charge of the detected car.
- **PV strings**: a click opens the module optimizers of the string with their current values
  (see [Optimizers](optimizers.md)).
- **Weather**: current conditions and forecast, switchable between hours ("Stunden") and days
  ("Tage"); swipe or use the arrows. It can be hidden under [Settings → Display](settings.md#display-anzeige).
- **Heute** (today) sums up the energy meters: PV produced, consumed, grid import, exported,
  self-sufficiency and self-consumption.

<img src="../images/live-mobile.png" alt="Live on a phone" width="300">

## Control ("Steuerung")

![Control](../images/control.png)

Temporary overrides of the plan; afterwards the defaults from the settings apply again.
**Eingriffe gelten** (overrides apply) sets for how long: until midnight, 4 hours or until
further notice. Sunflow replans immediately after every change.

- **Sunflow steuert** (Sunflow controls): main switch and one switch per device, with each
  device's state. Collapsed, it shows how many devices are controlled and whether one has a
  problem. Details in [Handing over control](control.md).
- **Hausspeicher** (home battery): strategy for the period (Balanced, Absorb PV, Self-sufficiency).
  "Was macht …?" (what does … do?) expands the explanation of the strategy.
- **Verbraucher** (loads): per wallbox, hot water tank or own load the mode (Kostenoptimal =
  cost-optimal, Nur PV = PV only, Sofort = now, Pause) and the target (state of charge or
  temperature). For the wallbox you choose the vehicle or let it be detected; the scanned card is
  shown in brackets, e.g. "Fiat 500e (Karte 1)". With a sensor set up you also see the voltage of
  the car's 12 V battery. Below it says what the plan intends, or why nothing is planned.
- **Priorität** (priority): if PV surplus is not enough for everyone, the top entry gets it
  first. With **Grenzen** (limits) a load waits until storages ranked higher reach a level,
  e.g. hot water only above 90 % home battery.
- **Protokoll** (log): what Sunflow wrote to devices, with reason and errors, and changes made by
  others with their trigger. Filters by device, period, "nur fremde Änderungen" (only changes by
  others) and "nur Fehler und Warnungen" (only errors and warnings).

<img src="../images/control-mobile.png" alt="Control on a phone" width="300">

## Planning ("Planung")

![Planning](../images/plan.png)

Left of "now" are measured values, right of it forecast and plan.

- **Header**: planned action now (and what actually happens), expected savings compared to plain
  self-consumption, expected electricity cost and expected state of charge at the end of the
  plan. **Neu berechnen** (recalculate) replans immediately.
- **Notices** in yellow, e.g. when a PV forecast does not reach as far as the plan.
- **Geplante Steuerung** (planned control): the sections of the plan with their action
  (self-consumption, hold battery, charge from grid, limited discharge …) and state of charge
  from → to.
- **Steuerbare Verbraucher** (controllable loads): target, deadline and what is planned per load,
  or why not.
- **Verlauf und Prognose** (history and forecast): charts for state of charge, PV production and
  house consumption, grid import and export, cost over time and import price.
  - Period 24 h, 48 h or 7 days; page with ‹ ›, **Jetzt** (now) jumps back.
  - Dragging sideways with mouse or finger moves all charts together.
  - Clicking a number on the power axis zooms to it; clicking again shows everything.
  - Clicking an entry in the legend of the state-of-charge chart shows only that one in its unit
    (kWh or °C) with its target.
  - Dashed lines are forecast or plan, solid lines are measured. Estimated prices beyond the
    published ones are dashed as well.
  - Checkboxes show the uncorrected forecast of the source (Solcast, Forecast.Solar) and total
    consumption including controllable loads.

## Analysis ("Analyse")

![Analysis](../images/analysis.png)

How well do forecasts and decisions match reality?

- **Empfehlungen** (recommendations): hints where a setting does not fit the measurements, e.g.
  a hot water demand set too high, or a measured battery efficiency. **Öffnen** (open) goes to the
  setting, **Ausblenden** (dismiss) hides the hint.
- **Energiebilanz** (energy balance): self-sufficiency, self-consumption, PV, consumption, grid,
  battery and efficiency for today, 7 or 30 days, from the devices' energy meters.
- **Kosten pro Tag** (cost per day): net cost (import minus export) compared to "without PV and
  battery", and the savings, over 30 or 90 days.
- **Wirkungsgrade** (efficiencies): battery and inverter over 30 days, 90 days or a year.
- **Lernen** (learning): how much of the PV forecast actually arrives (overall, per cloud cover,
  per hour, per plane) and how far the house consumption model is. Per roof plane, "Neu lernen ab
  heute" (relearn from today) resets learning, e.g. after trimming a tree.
- **Schattenbetrieb** (shadow mode): every 15 minutes Sunflow records what it would do and what
  actually happened. The tiles compare PV and load forecast with the measurement, the chart the
  planned with the actual state of charge. The table lists the sections where the decision
  differed. **Daten exportieren** (export data) and **Export importieren** (import export) are here.
