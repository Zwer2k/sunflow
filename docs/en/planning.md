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
- Wear per discharged kWh, per battery (Settings → Devices → Battery → Wear cost).
- Optionally, aging at high state of charge.
- Strategy surcharges and soft goals such as "grid-friendly". They steer the plan but do not show
  up in the displayed costs.

Sunflow replans when new prices or forecasts arrive, every quarter hour, and after every change
under Control or in the settings.

## Home battery strategies

| | Balanced | Absorb as much PV as possible | As self-sufficient as possible | Battery use |
|---|---|---|---|---|
| Goal | lowest electricity cost | like Balanced, room for PV | as little grid import as possible | like Balanced, battery lasts on heavy days too |
| Charging from the grid | when the price difference exceeds losses and wear | rarely before sunny days | never (except an overdue full charge) | like Balanced, with a larger buffer |
| In the plan | cost, buffer for 4 days in 5 | every exported kWh counts 5 ct less | every kWh from the grid counts €1 extra | grid power in expensive periods counts 10 ct extra, buffer for 19 days in 20 |
| Suits | dynamic and time-of-use tariffs | lots of PV, small battery | fixed price, independence | time-of-use tariffs, varying consumption |

- **Balanced** keeps energy for expensive hours and discharges there.
- **Absorb PV** only differs from "Balanced" when energy would be exported, so hardly in winter.
  It knows no export limit; use "grid-friendly" for that.
- **Self-sufficient** covers every demand from the battery immediately and gives loads PV surplus
  first. With time-of-use tariffs it costs more, because cheap night power is not stored.
- **Battery use** charges enough in cheap hours that the battery lasts until the next cheap charge
  even on a day with clearly more consumption or less sun. Cost comes second: grid power in
  expensive periods counts 10 ct extra, so Sunflow charges at night and covers the day from the
  battery even when losses and wear eat up the price advantage.

### Buffer for a day above the forecast

Consumption and PV forecast are never exact. If Sunflow charged at night only for the expected
day, the battery would be empty in the evening on every day with a little more consumption, and
the rest would come from the grid at the expensive price. A kWh charged too much costs almost
nothing: it stays in the battery and replaces part of the next cheap charge.

So with "Balanced", "Absorb PV" and "Battery use" the battery should still hold a buffer above
the discharge limit at the end of each expensive period (before the next cheap one):

- **Size**: from how much your daily consumption varies (per weekday/weekend, learned from the
  history; 15 % without history) and the range of the PV forecast down to p10, as far as that PV
  would cover the house. "Balanced" is then enough on about 4 days in 5, "Battery use" on about
  19 in 20. At most half the usable capacity. The buffer lies above the backup reserve, not in it.
- **Soft**: a missing buffer counts half of what cheap charging saves per kWh in the plan. On a
  day above the forecast the battery uses the buffer rather than buying expensive power, and
  Sunflow never buys at the expensive price for the buffer.
- **Only with a time-of-use or dynamic tariff** and charging from the grid allowed. With a fixed
  price or too small a price difference there is no buffer.

The plan says under "State of charge" how much should be left at what time.

### Room for more sun than planned

The plan uses the middle PV forecast, depending on "PV risk" a bit towards the cautious one
(p10). With more sun, an already full battery is the problem: the surplus would go to the grid for
the feed-in tariff and be bought back later at the cheap price. With cheap power every night,
holding the battery overnight and supplying the house from the grid gains little when the battery
would be full the next day anyway.

So at the end of each sunny period the plan keeps room for part of the extra PV up to the
optimistic forecast (p90):

- **Share**: the same as "PV risk" in the other direction (expected 0 %, balanced 30 %, cautious
  70 %); with "Absorb PV" and "Battery use" at least 70 %. "Battery use" charges for a clearly
  worse day; without as much room the other way the battery would be full by noon on every sunnier
  day.
- **Soft**: each kWh that would then be too much in the battery counts what that PV would replace
  (the cheapest price after it less the feed-in tariff, with "Absorb PV" plus 5 ct). The battery
  rather covers the house at night instead of holding.

The plan says under "State of charge" how much the battery should hold at most then.

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
- **Backup reserve**: in every strategy this state of charge stays free for power outages. If the
  battery is below it, Sunflow refills it first, from the grid if allowed.
  With **"Go below it on a safe forecast"** the plan may go below the reserve during the day's PV
  phase, down to a lowest level (default 10 %). Meant for mornings when the battery is at the
  reserve and large loads run: no grid import right away. Only as much is allowed as the pessimistic
  PV forecast (p10), with 25 % more consumption than expected, surely brings back into the battery
  on the same day. Where PV does not cover the house the plan holds the battery, so the surplus is
  enough. In the evening, at night and without a p10 forecast the reserve stays fixed. The plan shows
  a note while it uses part of the reserve.

## Several batteries

With several controllable batteries, say a hybrid inverter and a plug-in storage, Sunflow plans
them together. Each battery has its own state of charge, power limits, efficiencies and wear cost.

- The battery whose discharged kWh costs least covers the demand first. With "Include in
  planning" switched off for a battery, its cycles count as free.
- Energy is not moved from one battery into the other; that would only lose energy.
- Each battery gets its own action. When only one of them should cover the demand, the other one
  holds, so the two do not get in each other's way.
- The everyday charge limit and aging at a high state of charge apply to all batteries. The full
  charge and the backup reserve apply to the first one (usually the battery on the backup output).
- A plug-in storage on the house's AC side converts PV power twice and loses more than a battery
  on a hybrid inverter. The profile knows the coupling.

The plan shows the state of charge of all batteries together and of each one; the live view shows
each battery's state of charge, power and current planned action.

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

## Room heating

With a heat pump and a learned house model the house itself becomes a heat storage: with PV surplus or
cheap electricity Sunflow preheats, with dear electricity it lets the house cool a little – within the
**margin** around the room setpoint (Settings → Heat pump, start: preheat up to +1 K, no lowering).

- Per 15 minutes Sunflow computes the room temperature ahead with the house model (heating surface and house
  as two heat stores, heat loss to outside, internal gains, heat of the heat pump); heat that goes into the
  screed thus reaches the room only hours later; the heat pump's power is a variable of the plan, the COP is
  estimated from the outside temperature (Carnot), the highest power comes from the highest heat output
  learned (else the profile's start value). The outside temperature comes from the weather forecast; without
  a forecast the measured one stays. The sun is left out – on sunny days Sunflow plans a little too much
  heat, the room correction makes up for it.
- Every kelvin above or below the room setpoint costs 0.05 € per hour: without a saving the plan target
  stays on the setpoint. Below the margin it is very expensive (practically ruled out), above it dear but
  possible (the sun may push the house there).
- What the heat pump needs to hold the room setpoint (heat loss at the setpoint minus internal gains, over the
  COP) Sunflow takes out of the load
  forecast (which learned it), at most 70 % of it per quarter hour, and plans it anew; in the comparison
  without Sunflow it counts again.
- **Lernen + Regeln** (learning + control): battery and loads are planned with the heat pump. **Nur Lernen**
  (learning only): the plan stays as without room heating (the heat pump is part of the load forecast); a
  second calculation only shows how Sunflow would preheat. **Aus** (off): no preheating plan.
- Only the first heat pump is planned, only in the heating program, with a house model the setting "House
  model in the planning" allows (automatic: at least 48 hours of data, plausible, better than "temperature
  stays" in the test; see Settings → Heat pump), with the room temperature and a current reading of the
  heat pump – otherwise the plan target is the room setpoint and the plan says why. Without a controllable
  battery Sunflow computes no plan and so no preheating plan either.
- A change of room setpoint, margin, mode, "House model in the planning" or "Lernen + Regeln" replans at
  once.
- Shown in the plan under **Room heating**: the planned room temperature within the margin and the heat
  pump's power draw, planned and without planning. The flow controller still works towards the room
  setpoint; it takes the plan target over in one of the next versions.

## Learning

- **PV forecast**: Sunflow compares forecast and measurement per hour and cloud cover and learns a
  correction factor, per roof plane when strings are assigned to it. Recent days count more
  (half-life 30 days). "Relearn from today" discards old values of a plane.
- **Snow, leaves and cleaning**: with two or more planes Sunflow compares every day how much of
  its forecast each plane delivers against the others; weather and forecast errors cancel out. A
  plane counts as soiled
  - when it falls back by more than 30 % against the days before on at least two days (e.g.
    snow), or
  - when, after its last cleaning, it delivers less than 80 % of what it delivered in the first
    week after it over three days (leaves gathering bit by bit).

  These days are not learned, and Sunflow plans it with the lower share until it delivers as much
  again: a drop also ends without a jump once the plane brings at least 80 % of the clean share
  for three days (snow melting, rain washing it off). If it rises suddenly (leaves cleared, snow melted), the days before drop out of learning.
  So what was learned for the clean plane stays, even with leaves falling again and again in
  autumn. A slow decline without a cleaning found before it is only noticed by the model by sun
  position. With only one other plane to compare with (always, or in that period, e.g. before a
  third one was added), the one whose own share of its forecast moved more counts as changed. With three or more planes
  the change must also show against each other plane alone on each of the days: a cleared carport
  lifts the sum of the others and must not make a roof look soiled. Planes whose correction by sun
  position has learned from at least 60 days enter the comparison with their learned yield instead
  of the raw forecast: otherwise a clear week in autumn lifts a south roof but not a north roof,
  and the north roof would look soiled. A plane is only compared with the planes measured on all
  the days looked at: a plane that joins later is no step of the others.
- **The day so far**: the PV measured in the last minutes carries on into the next hour. How the
  last three hours went against what Sunflow expected for exactly those hours carries the rest of
  the day, half of it after three hours: a clear day above what was learned lifts the afternoon
  too, a dull one lowers it. A roof shaded in the morning as learned does not lower the afternoon.
  Sunflow then also plans these hours less cautiously. From tomorrow on what was learned applies
  again.
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
