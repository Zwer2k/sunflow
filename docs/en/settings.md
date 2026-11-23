# Settings

[Deutsch](../einstellungen.md) · **English**

The settings are split into areas; on first start Sunflow walks through them in order (see
[Installation and setup](installation.md)). Unsaved changes in one area are kept when you switch
to another.

**Search** (box above the list) finds a setting by its name, by other words for it ("battery",
"backup", "Tibber", "export limit"), despite typos, and your devices by name. A result opens the
area, scrolls to the setting and highlights it briefly. Arrow keys and Enter choose, Esc clears
the box.

**Expert mode** (switch below the list) additionally shows entity, unit, factor,
sign, decimals, polling interval and RAM/flash per role. These values are stored as deviations
from the profile. The browser remembers the switch.

## Integrations, devices, device test, balance check

Described in [Installation and setup](installation.md).

## Optimizers

![Optimizers](../images/en/settings-optimizers.png)

Sources, arrangement on the roof and assignment of module optimizers: see [Optimizers](optimizers.md).

## Vehicles

![Vehicles](../images/en/settings-vehicles.png)

What the car is, independent of the wallbox:

- **Name**, **Battery capacity (usable)** and **Charging efficiency**.
- **State of charge**: the sensor from the car's integration. **Estimate between reports** (on by
  default): many cars send their state of charge only now and then, e.g. a Fiat 500e through its
  cloud. In between, Sunflow adds what the wallbox charged into this car since. With every new
  report it compares the estimate with the real value and learns how much of the wallbox's energy
  reaches the battery (first the charging efficiency, then learned, over roughly the last 60 kWh).
  It only learns from reports after at least 1.5 kWh, not at 100 % and not when the car was driven
  in between. Estimated values show with "≈"; the vehicle's card shows the last report, what was
  charged since and what was learned.
- **Plugged in / home** (optional, e.g. a `device_tracker`): Sunflow
  learns when the car usually comes home and leaves, and plans it as soon as it is at home.
  Without it, the plug-in times at the wallbox are used.
- **Maximum charging power single-phase** and **three-phase** (optional, e.g. Fiat 500e:
  7.4 kW / 11 kW). Sunflow never asks the wallbox for more current than the car takes, and plans
  with this power.
- **Protect 12 V battery**: with the car's voltage sensor the wallbox
  charges briefly at minimum current when the auxiliary battery drops below the limit (default
  12.2 V, charge for 5 min, then at least 60 min pause). Details in [Supported devices](devices.md).

From the state-of-charge history of the last four weeks Sunflow learns how much the car uses per
hour (weekdays and weekends separately, from 14 days of data).

## Charging

![Charging](../images/en/settings-charging.png)

Per wallbox:

- **Which vehicles** may charge here.
- Per vehicle **Target SoC**, **Ready by (daily)**, **Charging** (Cost-optimal, PV surplus only or
  As fast as possible) and whether the **home battery may discharge into the car** (when off, the
  car charges from PV surplus or the grid only). These apply when the car is recognized without a
  card (sensor, selection under Control, the only vehicle).
- **Cards** (wallboxes with RFID): a vehicle can have several cards, each with its own number,
  name and values as above. When a card is scanned its values apply, e.g. a card "Holiday" with
  100 % by 7:00 and a card "Everyday" with 80 %. Its name is shown wherever "Card 3" would be. A
  number belongs to at most one card at a wallbox. When Sunflow recognizes the car without a card
  and releases the wallbox, it uses the vehicle's first card for that.
- **Vehicle detection**: in which order Sunflow detects the plugged-in
  car (card at the wallbox, selection under Control, the vehicle's sensor) and whether the wallbox
  releases charging without a card.

## Heat pump

**Layout of the page:** At the top one sentence says what is happening now (e.g. "Hot water running – room
control paused.") and what Sunflow wrote or would write, below it mode, room setpoint, room temperature,
outside temperature and flow. Then per heat pump collapsible sections (the browser remembers open or closed):

- **Control chain:** a diagram of how the set value comes about – room setpoint and room temperature into the
  correction, the heat needed into the feedforward, both to the flow target, then limits, protection, writing
  and the heat pump. Each block shows its value; held blocks are dashed, acting ones yellow. A click on a block
  shows its details below.
- **History:** the last 6 hours, 24 hours or 3 days as 5 minute means: flow, return, flow target, Max
  T-Vorlauf in the heat pump (a dot per write; with "Nur Lernen" dashed what Sunflow would have written), below
  the room temperature, the single rooms and the room setpoint; hot water, block, fast changes and compressor
  runs as bands. Flow target and room setpoint are recorded from this version on.
- **Plant:** outdoor unit, indoor unit, tank and rooms as a picture of where the heat flows now, with the
  states of the heat pump.
- **Writing and protection:** budget, pending value, latest writes and the counts per day.
- **Learned:** the heating curve as a chart (flow over outside, start curve dashed, with the learned hours), the learned share, the start values to set, the learned
  behaviour of the heat pump (stop margin, start margin, swing, output), the switch of the learning phase
  and the compressor stops.

At the end **House model** and **Settings** (margin, limits of the set value, rooms, fast change, house).
Explanations are help texts: the "?" in the menu bar shows all of them, the "?" after a heading or a field
only that one.

**Mode** (at the top with the room setpoint), three levels:

- **Off:** Sunflow does not control, does not learn and records no measurements of the heat pump (saves
  space, e.g. in summer). What it learned (heating curve, house model, stop margin, correction) is kept; afterwards
  Sunflow goes on learning with the older data.
- **Learning only** (default): Sunflow records, learns and shows what it would write, but writes nothing to
  the heat pump.
- **Learning + control:** Sunflow writes the set value (Max T-Vorlauf, stored in the heat pump's EEPROM,
  within the budget) and the room thermostat block to the heat pump.

The thermostat "Haus" in Home Assistant knows only heat/off: off there is **Off**, heat there returns to the
level that applied last (learning only or learning + control). **Learning + control** is switched on only in
Sunflow, with a confirmation, and only when the heat pump is compatible and reachable, values are there, the
heating circuit function is fixed and the ESP regulation is off; switch off other controllers writing these
values first. When switched on, Sunflow takes over without writing anything: the
correction starts at 0, set value and block count as the heat pump has them (a block set by someone else
stays someone else's). Sunflow writes only when the rules ask for it; every write is in the journal with its
reason. A write that fails is taken back (journal) and tried again when the rules ask for it. About a minute
after a write Sunflow checks that the heat pump took the value over: if it does not show it, Sunflow goes on
from the value shown; if the effective flow setpoint stays below it while heating, the heat pump limits it
itself (e.g. flow temperature day) – then set the upper limit of the set value to that value at most (both a
warning in the journal). After **Off** Sunflow takes over again as when switched on. **Switching back**
resets nothing: Max T-Vorlauf stays as last written, a block Sunflow set stays set (the heat pump then does
not heat until it is released).

**Room setpoint:** the room temperature Sunflow is to keep (18–27 °C) and the mode; the same as
the thermostat "Haus" in Home Assistant. Next to it the room temperature (weighted mean of the rooms; a
room does not count while its sensor is unavailable or its device has not reported for more than 2 hours –
another reading of the same device counts as well, e.g. the humidity, as some integrations do not report an
unchanged temperature again).

**Margin (preheat up to / lower down to):** how far the planning may depart from the room setpoint, each
0–3 K (start: preheat up to +1.0 K, lower down to 0 K). With PV surplus or cheap electricity Sunflow may heat
the house up to setpoint + preheat and let it fall to setpoint − lower when electricity is dear; every
departure costs something in the planning (0.05 € per kelvin and hour), so without a saving it stays at the
room setpoint. Stored with the room setpoint; a change replans at once. With **Lernen + Regeln** (learning +
control) the battery and consumer plan includes the heat pump, with **Nur Lernen** (learning only) the plan
only shows how Sunflow would preheat (see [Planning](planning.md#room-heating)). The controller still works
towards the room setpoint, not the plan target. Set only in Sunflow, not in the thermostat "Haus".

**Fast change (airing, cooking …):** when a room changes by at least 0.8 K within 20 minutes – down as
when airing or up as when cooking – it does not count for the room temperature for now and the correction
pauses. The thresholds are in kelvin, whether a sensor reports in steps of 0.1, 0.3 or 0.5 K; a single step
or slow cooling triggers nothing. The change is over once the room moves by at most 0.5 K for 30 minutes
(the fast part of the recovery is over), at the latest after 2 hours. Then the room counts again with what
it is still below or above: after airing, walls and furniture often lack heat for hours, which the heating
is to replace. All five values can be set with the rooms. Rooms with a fast change and the changes of the
last 24 hours are shown.

**Heat pump:** per device connection and compatibility, program, activity, strategy (HK function),
bypass valve, flow, return, effective flow setpoint and Max T-Vorlauf, an active room thermostat block or
a fault clearly highlighted, and whether the heat pump heats validly – otherwise why not (e.g. hot water,
after-run, bypass valve). Below it the **flow target** = heating curve + correction.

**Heating curve:** flow = room setpoint + slope × (room setpoint − outside) + shift; the formula with the
current numbers is shown next to the control chain. Outside is the measured outside temperature, averaged
over 15 minutes. You set the **start values** (slope and shift) per heat pump under "Learned". While none are
set, Sunflow reads the slope once from the heat pump (HPSU: "Heizkurve") and keeps it; the shift is then 0
(the HPSU has none). Without a value from the heat pump the start value of the **heating surface type**
applies (underfloor 0.6, mixed 1.0, radiators 1.4). "Read again from the heat pump" fetches the slope anew,
"Take over learned" makes the learned curve the start (e.g. before summer for next autumn). The curve
applies at once, without a waiting time. Every hour Sunflow learns slope and shift from the hours of the last
28 days in which the heat pump heated validly, the compressor ran and the room was within 0.3 K of that
hour's room setpoint: the effective flow setpoint of those hours was enough. The start values count like 24
hours; the **learned share** is shown: 0 % at first, then rising, at most about 94 % – the start values
always act a little. When the learned curve changes, Sunflow shifts the correction by the difference so the
flow target does not jump; when you change the start values, the new curve applies at once. A higher room
setpoint raises the flow at once by 1 + slope kelvin per kelvin.

**Correction:** slowly evens out the difference between room temperature and setpoint (at most ± 3 K; it
does not grow within ± 0.2 K); how slowly depends on the heating surface type (integral time: underfloor 6 h,
mixed 4 h, radiators 2 h). It pauses while the heat pump does not heat validly, room temperatures are
missing or a room changes fast (airing, cooking). In mode "learning only" it has no effect back and may run
to its limit.

**Set value and protection (with "learning only": would write):** From the flow target Sunflow computes the set value (Max
T-Vorlauf) it would write and the room thermostat block – shown as "would write", none of it goes to the
heat pump. The set value stays within the **limits of the set value**, which can be set per heat pump
(at least/at most; at most is also the highest flow for the screed or heating surfaces). Empty, the
start value of the heating surface type applies (underfloor 25–40 °C, mixed 25–50 °C, radiators
30–55 °C); the entity's range always narrows it further. When the flow
target reaches a limit or the heat pump its maximum or minimum output, the correction does not move
further beyond it.

**Protection against shutdowns (since 0.80.27):** Sunflow does not predict when the compressor switches,
it reacts to it. While the compressor runs in heating, the set value is the flow target. When the
compressor has been **off for 5 minutes** although the rooms need heat (room temperature below setpoint +
0.5 K, no block, no hot water and no defrosting right now) – whatever the reason, also after hot water or
defrosting –, Sunflow raises the set value in one step to flow + start margin (profile, 3 K for the HPSU):
**restart**. After a start it waits 10 minutes and then lowers to the flow target in one step – not below
what the **stop margin** allows (highest flow of the last 5 minutes − stop margin + swing); when the flow
rises above that, it follows. During hot water and defrosting Sunflow writes nothing.

Sunflow learns the stop margin from real stops: each compressor stop is recorded with the flow (also the
UART node's, if there is one), the flow setpoint and the context, and classified – defrosting or hot
water follows, block, other activity, else "shutdown rule". The flow − setpoint distances of the
shutdown-rule stops (from 5, of the last 14 days) give the margin; one larger than the profile's (3 K)
only from 10. Until then the profile value applies. In the **learning phase** (switch in the section
"Learned", on at first) Sunflow lowers to the flow target and lets the compressor stop, so the heat pump
shows when it switches off; the restart brings it back. You switch it off once enough stops are recorded.
The table **Compressor stops** shows the latest stops with their kind and distance.

Heat Sunflow does not want (room 0.5 K above the setpoint) it switches off with the room thermostat block –
each state for at least 20 minutes. A room thermostat block Sunflow did not set (by hand, an automation,
another controller) is not released by Sunflow: it is shown prominently in the heat pump section and noted
in the journal, and Sunflow controls again only once it is released. The swing of the flow while running
and the heat pump's output range are learned from the recording as well. The table **Writes per day**
counts for the last 7 days the writes (normal, restart, tracking, block on/off) and the compressor stops in
heating – to
compare with the budget of 48 normal writes and 24 for restart and tracking per day. The **minimum
spacing** between two writes of the set value already applies with "learning only" (from the device profile, for the
HPSU 5 minutes, for protection steps – restart and tracking – 2 minutes): the first change after a calm
spell goes out at once, further changes within that time are only kept, and once it has passed Sunflow
writes the latest value if it still differs (a normal value by more than 0.5 K, a protection step with any
raise). A waiting value is shown as "pending … would be written at …". The **budget** applies with "learning only"
as well (from the device profile, for the HPSU 48 writes per day, plus 24 only for protection steps, at
most 6 in any hour; the day follows Home Assistant's time zone): Sunflow counts virtually from the
recorded writes, the counter of real EEPROM writes stays untouched; writing for real, the real writes in
that counter count. When the daily budget is used up, a
normal value waits ("pending … waits: daily budget used up") until the next day; protection steps go on
from the emergency quota. When that is used up too or the hourly limit is reached, a protection step waits
as well (warning in the journal). The journal notes each kind of exhaustion
once a day. The tile **Budget today** (with "learning only" counting what Sunflow would write) shows the day's use, the emergency quota left and the
last hour. The button **Write now** skips the spacing for the pending value (not the budget) and is not
offered with "learning only". The room thermostat block counts neither against the
budget nor for the spacing: the HPSU does not store this input in its EEPROM; its minimum duration
prevents cycling.

**Foreign writers:** When Max T-Vorlauf changes at the heat pump without Sunflow having written it
(automation, script, ESP regulation, by hand), Sunflow reports it as a warning in the journal – once while
such changes follow each other; further ones it only counts. It counts as over only 24 hours after the
last change (journal entry); until then the setup summary names the heat pump as well. Sunflow assumes the
new value from then on (with "learning only" also for the writes it would make) and waits for the minimum spacing before
writing again itself; foreign changes do not count against its budget. While the **ESP regulation** of the
CAN component is active ("Vorlauf Soll Geregelt" above 0), the component regulates Max T-Vorlauf itself:
Sunflow then writes nothing (with "learning only" "Would write nothing: ESP regulation active"), reports it in the journal and the
summary, and controls again once the value is set to 0 in ESPHome.

**House:** room temperatures (one or more sensors, optionally weighted, e.g. by area) and
optionally the heat into the house (e.g. the heating's heat meter, otherwise the heat pump's heat).
The **heating surface type** (underfloor heating, mixed, radiators; default underfloor) sets the start
value of the heating curve (while no start values are set and the heat pump provides none) and the integral
time of the correction; the measured data are kept.
The **outside temperature** can be chosen: automatic (own sensor under learning sources, else the heat
pump's sensor), the heat pump's outside temperature sensor, the sensor of its outdoor unit (HPSU: TA2)
or only the own sensor. From the hourly values of the last four weeks Sunflow learns a simple thermal
model of the house. Hours in which the heat pump made hot water or defrosted are left out – their heat
did not go into the house. The hourly values first come from Home Assistant's hourly means; once the
heat pump's recording holds at least 7 days of valid heating (at least 1 hour each), from its 5 minute
values – then without hours with hot water, defrost, after-run or fast changes. The switch happens only when
the model from them is not worse; after that it stays. The source in use is shown under "Learned":

| Learned | Meaning |
|---|---|
| Time constant | hours until two thirds of the difference to outside are gone |
| Cooling at 0 °C outside | kelvin per hour lost at 20 °C inside without heating and sun |
| Heat loss | heating power per kelvin of difference (only with heat measurement) |
| Heat storage | heat for 1 K warmer in the whole house (only with heat measurement) |
| Sun | warming in full sun (with brightness under learning sources) |

Sunflow holds back the last two days and computes them anew from the measurement each day; the
model counts as reliable only when it beats "the temperature stays the same" there and its values
are plausible (from 7 days). A chart shows the last week measured and expected by the model. Rooms
whose temperature jumps far more than the others' (sun, radiator, window at the sensor) are named.

## Loads

![Loads](../images/en/settings-consumers.png)

Defaults for wallboxes without assigned vehicles, hot water and own loads:

- **Planning**: Cost-optimal (PV surplus or cheap hours), PV surplus only (never from the grid
  or the home battery) or As fast as possible (now at full power).
- **Target**: state of charge or temperature; empty takes the value from evcc. An active
  evcc charging plan takes precedence.
- **Highest target by hand**: the target can be chosen up to this value in Control and Live,
  also with the knob; Sunflow rejects higher values. Empty: 60 °C for hot water (above it there
  is a risk of scalding at the tap), 100 % for a car.
- **Ready by (daily)**: empty means by the end of the plan, or within 24 h.
- **Power from the home battery**: whether the battery may discharge
  into this load.
- **Alternative heat source (district heating, gas …)** (hot water): price per kWh of heat from
  district heating, gas or similar. Sunflow only heats electrically when that is cheaper.

Sunflow learns when and how much hot water is used (per hour, weekdays and weekends separately,
from one week of data) and, without a set ready time, heats up before the usual main usage time.
If the tank regularly warms up without its own heating rod (district heating, gas), Sunflow learns
the temperature at which that heat source starts; the temperature forecast does not fall below it.

## Tariff & forecast

![Tariff and forecast](../images/en/settings-sources.png)

Import price, PV forecast per plane and feed-in tariff per inverter. See
[Installation and setup](installation.md#6-tariff--forecast) and
[Electricity prices](prices.md).

## Planning

![Planning](../images/en/settings-standards.png)

How Sunflow plans the home battery. Background in [How Sunflow plans](planning.md).

| Setting | Meaning |
|---|---|
| Home battery strategy (default) | Balanced, Absorb as much PV as possible, As self-sufficient as possible or Battery use. "What does … do?" expands the explanation. |
| Everyday charge limit | Sunflow only charges above it for a due full charge. 100 % = no limit. |
| Charge limit applies | "always" or "except before bad-weather days": if the preview (tomorrow up to 5 days) has a day with less PV than consumption, the battery may become full to store the PV before it. |
| Full charge every … days | To balance and calibrate the cells, preferably with PV. Empty = never on purpose. |
| From the grid at the latest after … days | If the battery has not been full for that long (e.g. in winter), Sunflow fills it from the grid at the cheapest time. |
| Aging at high SoC | Every kWh above the threshold (default 80 %) costs a small amount per hour (default 0.1 ct). Sunflow then charges as late as possible before the demand. Can be switched off. |
| Backup reserve | Applies in every strategy: in everyday use Sunflow never plans the battery below this level. If it is below, Sunflow refills it first, from the grid if allowed, whatever the price. **Go below it on a safe forecast**: during the day's PV phase down to the lowest level, as far as the pessimistic PV forecast (p10) surely brings the battery back to the reserve on the same day (e.g. large loads in the morning without grid import right away). Consumption counts 25 % above the forecast; the lowest level defaults to 10 %. |
| Home consumption per day (initial value) | Used while less than 7 days of history exist. |
| Exclude large loads from | Hours with higher house consumption (e.g. oven, sauna) are left out of the load forecast. Controllable loads are subtracted anyway. |
| Battery short name | Per battery under Devices → Battery, up to 8 characters; shown with several batteries in Live and Plan. Without one: the first word of the name. |
| Battery wear cost | Per battery under Devices → Battery: directly in ct/kWh, or calculated from purchase price, cycles, depth of discharge and remaining capacity; plus the switch "Include in planning". Prevents cycling for tiny gains. |
| Charge from grid | "allowed when it pays off". |
| PV forecast | "adapt to the system (learn from measurements)": learns from measurements per hour and cloud cover; details under Analysis. |
| Correct by sun position (test) | For planes with Solcast history: a correction per sun elevation and direction, learned from up to two years (once a day); recognises shading that moves with the seasons. Replaces the correction by time of day for planes whose model was learned from at least 60 days and is better in the backtest. Backtest and shading map under Analysis. |
| Grid-friendly | Avoid export and import peaks above the set limits where it costs little. A soft goal, not a hard limit. |
| Uncertain PV forecast | "Cautious" plans more with the bad case of the forecast (Solcast p10). |
| Weather for cloud cover | Weather entity used for learning per cloud cover. |

All changes apply immediately, numbers once you leave the field or press Enter. Sunflow then replans; "Saved" confirms it briefly.

**Learning sources** (optional): additional sensors make the learned forecasts more precise. None
is required. Sunflow suggests sensors by their device class or unit; the inverter's and battery's
own temperatures are left out. Below each choice you see how far back Home Assistant keeps its
values. Sensors without a `state_class` have no long-term statistics, only the recorder history.

Each chosen source is checked for plausibility (up to 100 days). Learning leaves out values outside
the physically possible range, short spikes away from the neighbouring values and values that stay
the same implausibly long (a hanging sensor); outages up to 30 minutes are bridged, longer ones stay
gaps. Times Home Assistant itself did not record (recognised by the grid power) do not count, nor, for
optimizers, the time with the sun below 5° (they sleep without light). The
plausible share is shown below the choice and highlighted below 80 %. Nothing changes in Home
Assistant.

| Source | Used for |
|---|---|
| Outdoor temperature | Measured in the shade; heating share of the house consumption and module temperature. Without it: the weather entity's temperature. |
| Outdoor humidity | With the temperature: dew and frost on the modules. |
| Illuminance or irradiance | How bright it really was (lx or W/m²); tells clouds from shading, also retroactively. |
| Precipitation | Detecting snow on the modules. |
| Module temperature per roof plane | Warm modules yield less. If the plane has optimizers (assigned through its strings) and nothing is chosen, Sunflow takes the mean of their temperatures; otherwise estimated from outdoor temperature and irradiance. |

## Display

![Display](../images/en/settings-display.png)

- **Weather in the Live view**: can be hidden separately for phones and for tablets/computers. If
  it is off everywhere, Sunflow does not fetch the weather forecast for it.
- **Language**: "Same as Home Assistant" (default: German if Home Assistant is set to German, English
  otherwise), "Deutsch" or "English". Applies to the interface and to messages such as plan notices
  and recommendations. The page reloads afterwards.
- **House pictures in the live view**: where the "House" and "3D" views show strings, inverters and
  consumers. The default everywhere is "Automatic": strings go to the roof faces by the names of
  their forecast planes ("North", "Carport" …), consumers are recognized by kind and name (heat
  pump, hot water, washing machine …). You can choose instead the roof face per string (or "do not
  show"), which plant hangs on which of the two inverter places and the figure per consumer (or
  "only in the home consumption").

## Summary

Lists devices, tariff and forecast and marks what is still missing (see
[Installation and setup](installation.md#8-summary)).
