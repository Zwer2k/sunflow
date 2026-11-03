# User interface

[Deutsch](../bedienung.md) · **English**

The top bar switches between the five views: **Live**, **Control**, **Planning**, **Analysis**
and **Settings**. On phones they are icons at the bottom. A number on "Analysis" shows open
recommendations.

## Live

![Live](../images/en/live.png)

The energy flow shows what is flowing right now: PV (top, with one circle per string), grid,
home battery, house and the controllable loads such as wallbox and hot water. Animated lines show
where energy is going.

- **House** shows consumption without the controllable loads; "total" below includes them.
- **Battery** shows power, direction and state of charge; the ring is the state of charge.
- **Wallbox** shows charging power and the state of charge of the detected car.
- **PV strings**: the number at the circle is the number of optimizers. A click opens the module
  optimizers of the string with their current values (see [Optimizers](optimizers.md)).

  <details><summary>Example: optimizers of a Huawei string</summary>

  ![Optimizers in the Live view](../images/en/live-optimizers.png)

  </details>
- **Weather**: current conditions and forecast, switchable between **Hours** and **Days**;
  swipe or use the arrows. It can be hidden under [Settings → Display](settings.md#display).
- **Today** sums up the energy meters: PV generated, consumed, grid import, exported,
  inverter/battery losses, self-sufficiency and self-consumption.
- **Inverter/battery losses** (below the energy flow, average of the last minutes): the balance
  of PV, grid and battery minus the measured house consumption, i.e. conversion losses and the
  devices' own consumption. Home Assistant's energy dashboard counts them as house consumption,
  so it shows more consumption and more self-sufficiency than Sunflow. Only with a consumption
  counter or measurement.

<img src="../images/en/live-mobile.png" alt="Live on a phone" width="300">

## Control

![Control](../images/en/control.png)

Temporary overrides of the plan; afterwards the defaults from the settings apply again.
**Overrides apply** sets for how long: until midnight, 4 hours or until
further notice. Sunflow replans immediately after every change.

- **Sunflow controls**: main switch and one switch per device, with each
  device's state. Collapsed, it shows how many devices are controlled and whether one has a
  problem. Details in [Handing over control](control.md).
- **Home battery**: strategy for the period (Balanced, Absorb PV, Self-sufficiency).
  "What does … do?" expands the explanation of the strategy.
- **Loads**: per wallbox, hot water tank or own load the mode (Cost-optimal, PV only, Now,
  Pause) and the target (state of charge or
  temperature). For the wallbox you choose the vehicle or let it be detected; the scanned card is
  shown in brackets, e.g. "Fiat 500e (Card 1)". With a sensor set up you also see the voltage of
  the car's 12 V battery. Below it says what the plan intends, or why nothing is planned.
- **Priority**: if PV surplus is not enough for everyone, the top entry gets it
  first. With **Limits** a load waits until storages ranked higher reach a level,
  e.g. hot water only above 90 % home battery.
- **Log**: what Sunflow wrote to devices, with reason and errors, and changes made by
  others with their trigger. Filters by device, period, "only external changes" and "only errors
  and warnings".

<img src="../images/en/control-mobile.png" alt="Control on a phone" width="300">

## Planning

![Planning](../images/en/plan.png)

Left of "now" are measured values, right of it forecast and plan.

- **Header**: planned action now (and what actually happens), expected savings compared to plain
  self-consumption, expected electricity cost and expected state of charge at the end of the
  plan. **Recalculate** replans immediately.
- **Notices** in yellow, e.g. when a PV forecast does not reach as far as the plan.
- **Planned control**: the sections of the plan with their action
  (self-consumption, hold battery, charge from grid, limited discharge …) and state of charge
  from → to.
- **Controllable loads**: target, deadline and what is planned per load,
  or why not.
- **History and forecast**: charts for state of charge, PV production and
  house consumption, grid import and export, cost over time and import price.
  - Period 24 h, 48 h or 7 days; page with ‹ ›, **Now** jumps back.
  - Dragging sideways with mouse or finger moves all charts together.
  - Clicking a number on the power axis zooms to it; clicking again shows everything.
  - Clicking an entry in the legend of the state-of-charge chart shows only that one in its unit
    (kWh or °C) with its target.
  - Dashed lines are forecast or plan, solid lines are measured. Estimated prices beyond the
    published ones are dashed as well.
  - Checkboxes show the uncorrected forecast of the source (Solcast, Forecast.Solar) and total
    consumption including controllable loads.

## Analysis

![Analysis](../images/en/analysis.png)

How well do forecasts and decisions match reality?

- **Recommendations**: hints where a setting does not fit the measurements, e.g.
  a hot water demand set too high, or a measured battery efficiency. **Open** goes to the
  setting, **Hide** hides the hint.
- **Energy balance**: self-sufficiency, self-consumption, PV, consumption, grid,
  battery and efficiency for today, 7 or 30 days, from the devices' energy meters.
- **Cost per day**: net cost (import minus export) compared to "without PV and
  battery", and the savings, over 30 or 90 days.
- **Efficiencies**: battery and inverter over 30 days, 90 days or a year.
- **Learning**: how much of the PV forecast actually arrives (overall, per cloud cover,
  per hour, per plane) and how far the house consumption model is. Per roof plane, "Relearn from
  today" resets learning, e.g. after trimming a tree.
- **Shadow mode**: every 15 minutes Sunflow records what it would do and what
  actually happened. The tiles compare PV and load forecast with the measurement, the chart the
  planned with the actual state of charge. The table lists the sections where the decision
  differed. **Export data** and **Import export** are here.
