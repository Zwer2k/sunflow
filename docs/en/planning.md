# How Sunflow plans

[Deutsch](../planung.md) · **English**

## The plan

For every quarter hour of the next up to 96 hours Sunflow works out what the home battery and the
controllable loads should do. It solves a (mixed-integer) linear optimisation problem: the plan
with the lowest cost that meets all constraints.

**Inputs**

- Import price per quarter hour (see [Electricity prices](prices.md)), feed-in tariff per system.
- PV forecast per plane with the learned correction.
- Load forecast for house consumption without controllable loads.
- State of charge, capacity, power limits and efficiencies of the batteries.
- Targets and deadlines of the loads, e.g. car at 80 % by 7:00, hot water at 55 °C by evening.

**Costs in the plan**

- Grid import at the price of the quarter hour, minus feed-in tariff.
- Losses when charging and discharging (efficiency of battery and inverter).
- Wear per discharged kWh (Settings → Planning → Battery wear cost).
- Optionally, aging at high state of charge.
- Strategy surcharges and soft goals such as "grid-friendly". They steer the plan but do not show
  up in the displayed costs.

Sunflow replans when new prices or forecasts arrive, every quarter hour, and after every change
under Control or in the settings.

## Home battery strategies

| | Balanced | Absorb as much PV as possible | As self-sufficient as possible |
|---|---|---|---|
| Goal | lowest electricity cost | like Balanced, room for PV | as little grid import as possible |
| Charging from the grid | when the price difference exceeds losses and wear | rarely before sunny days | never (except an overdue full charge) |
| In the plan | cost | every exported kWh counts 5 ct less | every kWh from the grid counts €1 extra |
| Suits | dynamic and time-of-use tariffs | lots of PV, small battery | fixed price, independence |

- **Balanced** keeps energy for expensive hours and discharges there.
- **Absorb PV** only differs from "Balanced" when energy would be exported, so hardly in winter.
  It knows no export limit; use "grid-friendly" for that.
- **Self-sufficient** covers every demand from the battery immediately and gives loads PV surplus
  first. With time-of-use tariffs it costs more, because cheap night power is not stored.

The strategy is set under Settings → Planning, temporarily under Control, or with the action
`sunflow_ems.set_strategy`.

## Does charging from the grid pay off?

Grid charging only pays off if the later price exceeds today's by more than losses and wear. With
72 % round-trip efficiency and 6 ct wear per kWh, power bought at 22 ct must later cost more than
about 36.5 ct (22 / 0.72 + 6). That is why Sunflow does not charge from the grid for small price
differences. This is intended, not a bug.

## Protecting the battery

- **Everyday charge limit**: Sunflow only charges above it for a due full charge.
- **Full charge every … days**: to balance the cells, preferably with PV.
- **From the grid at the latest after … days**: if PV has not filled the battery for that long,
  Sunflow fills it from the grid at the cheapest time.
- **Aging at high SoC** (calendar aging): cells age faster the longer they sit
  full. Every kWh above the threshold costs a small amount per hour. Sunflow then charges as late
  as possible before the demand, e.g. at the end of the cheap night window instead of from
  midnight. Example: 4 kWh above 80 % for 10 hours cost about 4 ct at 0.1 ct.
- **Backup reserve**: in every strategy this state of charge stays free for power outages.

## Loads

Wallboxes, hot water and own loads are planned in the same calculation as the battery. Each load
has a mode:

- **Cost-optimal**: PV surplus or cheap hours, so that the target is reached in time.
- **PV surplus only**: never from the grid or the home battery.
- **As fast as possible** ("Now" under Control): now at full power.
- **Pause** (under Control only): not at all.

**Priority** (Control → Priority) decides who gets scarce surplus first. **Limits** make a load
wait until storages ranked higher reach a level.

**Cars**: Sunflow learns consumption, arrival and departure. If the only car of a wallbox is
away, Sunflow already plans it with learned consumption and arrival, but only with PV surplus or
cheap power and without obligation. If the car is at home but not plugged in and would get energy
soon, the hint "… plugging in at "*wallbox*" pays off" appears (in Home Assistant as the event
`sunflow_ems_plug_in_suggested`).

**One-off loads** such as a washing machine or dryer are scheduled at the cheapest time with the
action `sunflow_ems.plan_load` (see [Sunflow integration](integration.md)).

## Learning

- **PV forecast**: Sunflow compares forecast and measurement per hour and cloud cover and learns a
  correction factor, per roof plane when strings are assigned to it. Recent days count more
  (half-life 30 days). "Relearn from today" discards old values of a plane.
- **Uncertain forecast**: with "Cautious" Sunflow plans more with the bad case
  (Solcast p10), so it rather tops up cheaply at night than buys expensive power when the sun
  does not show.
- **House consumption**: until enough history exists Sunflow uses hourly averages, afterwards
  (from 14 days) a model of time of day, weekday and temperature. Controllable loads are
  subtracted, rare peaks above the set limit left out.
- **Cars and hot water**: usage per hour, weekdays and weekends separately.

## Prices beyond the published ones

Dynamic tariffs only know today and, from around noon, tomorrow. For the hours after that Sunflow
estimates the price from the last two weeks. These values only shape the end of the plan and are
replaced as soon as the real prices arrive (see [Electricity prices](prices.md)).

## Grid-friendly

With **Grid-friendly** Sunflow tries to avoid export and import peaks above the
set limits by shifting battery and load charging. Every kWh above the limit counts like 20 ct
extra in the plan. If a peak cannot be avoided, it is allowed. This is a soft goal and no
replacement for a limit in the inverter.
