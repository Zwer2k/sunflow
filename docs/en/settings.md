# Settings

[Deutsch](../einstellungen.md) · **English**

The settings are split into areas; on first start Sunflow walks through them in order (see
[Installation and setup](installation.md)). Unsaved changes in one area are kept when you switch
to another.

**Expertenmodus** (expert mode, switch below the list) additionally shows entity, unit, factor,
sign, decimals, polling interval and RAM/flash per role. These values are stored as deviations
from the profile. The browser remembers the switch.

## Integrations, devices, device test, balance check

Described in [Installation and setup](installation.md).

## Optimizers ("Optimierer")

![Optimizers](../images/settings-optimizers.png)

Sources, arrangement on the roof and assignment of module optimizers: see [Optimizers](optimizers.md).

## Vehicles ("Fahrzeuge")

![Vehicles](../images/settings-vehicles.png)

What the car is, independent of the wallbox:

- **Name**, **Akkukapazität (nutzbar)** (usable battery capacity) and **Ladewirkungsgrad**
  (charging efficiency).
- **Ladestand** (state of charge): the sensor from the car's integration.
- **Angesteckt / zuhause** (plugged in / at home, optional, e.g. a `device_tracker`): Sunflow
  learns when the car usually comes home and leaves, and plans it as soon as it is at home.
  Without it, the plug-in times at the wallbox are used.
- **Höchste Ladeleistung** (maximum charging power) single-phase and three-phase (e.g. Fiat 500e:
  7.4 kW / 11 kW). Sunflow never asks the wallbox for more current than the car takes, and plans
  with this power.
- **12-V-Batterie schützen** (protect 12 V battery): with the car's voltage sensor the wallbox
  charges briefly at minimum current when the auxiliary battery drops below the limit (default
  12.2 V, charge for 5 min, then at least 60 min pause). Details in [Supported devices](devices.md).

From the state-of-charge history of the last four weeks Sunflow learns how much the car uses per
hour (weekdays and weekends separately, from 14 days of data).

## Charging ("Laden")

![Charging](../images/settings-charging.png)

Per wallbox:

- **Which vehicles** may charge here.
- **Karte an dieser Wallbox** (card at this wallbox): the RFID card that identifies the vehicle.
- **Ziel-Ladestand** (target state of charge) and **Fertig bis (täglich)** (ready by, daily).
- **Laden** (charging): cost-optimal, PV surplus only, or as fast as possible.
- **Hausspeicher darf ins Auto entladen** (home battery may discharge into the car): when off,
  the car charges from PV surplus or the grid only.
- **Erkennung des Fahrzeugs** (vehicle detection): in which order Sunflow detects the plugged-in
  car (card at the wallbox, selection under Control, the vehicle's sensor) and whether the wallbox
  releases charging without a card.

## Loads ("Verbraucher")

![Loads](../images/settings-consumers.png)

Defaults for wallboxes without assigned vehicles, hot water and own loads:

- **Planung** (planning): Kostenoptimal (cost-optimal: PV surplus or cheap hours), Nur
  PV-Überschuss (PV surplus only: never from the grid or the home battery) or Schnellstmöglich
  (as fast as possible: now at full power).
- **Ziel** (target): state of charge or temperature; empty takes the value from evcc. An active
  evcc charging plan takes precedence.
- **Fertig bis (täglich)** (ready by, daily): empty means by the end of the plan, or within 24 h.
- **Strom aus dem Hausspeicher** (power from the home battery): whether the battery may discharge
  into this load.
- **Alternative Wärmequelle** (alternative heat source, hot water): price per kWh of heat from
  district heating, gas or similar. Sunflow only heats electrically when that is cheaper.

Sunflow learns when and how much hot water is used (per hour, weekdays and weekends separately,
from one week of data) and, without a set ready time, heats up before the usual main usage time.

## Tariff & forecast ("Tarif & Prognose")

![Tariff and forecast](../images/settings-sources.png)

Import price, PV forecast per plane and feed-in tariff per inverter. See
[Installation and setup](installation.md#6-tariff--forecast-tarif--prognose) and
[Electricity prices](prices.md).

## Planning ("Planung")

![Planning](../images/settings-standards.png)

How Sunflow plans the home battery. Background in [How Sunflow plans](planning.md).

| Setting | Meaning |
|---|---|
| Strategie (strategy) | Ausgewogen (balanced), Möglichst viel PV aufnehmen (absorb as much PV as possible) or Möglichst autark (as self-sufficient as possible). "Was macht …?" expands the explanation. |
| Ladegrenze im Alltag (everyday charge limit) | Sunflow only charges above it for a due full charge. 100 % = no limit. |
| Voll laden alle … Tage (full charge every … days) | To balance and calibrate the cells, preferably with PV. Empty = never on purpose. |
| Spätestens nach … aus dem Netz (from the grid after … days at the latest) | If the battery has not been full for that long (e.g. in winter), Sunflow fills it from the grid at the cheapest time. |
| Alterung bei hohem Ladestand (ageing at high state of charge) | Every kWh above the threshold (default 80 %) costs a small amount per hour (default 0.1 ct). Sunflow then charges as late as possible before the demand. Can be switched off. |
| Notstrom-Reserve (backup reserve) | Applies in every strategy: in everyday use Sunflow never plans the battery below this level. |
| Hausverbrauch pro Tag (house consumption per day) | Starting value while less than 7 days of history exist. |
| Großverbraucher ausklammern ab (exclude large loads above) | Hours with higher house consumption (e.g. oven, sauna) are left out of the load forecast. Controllable loads are subtracted anyway. |
| Verschleißkosten Speicher (battery wear cost) | Directly in ct/kWh, or calculated from purchase price, cycles, depth of discharge and remaining capacity. Prevents cycling for tiny gains. |
| Laden aus dem Netz (charging from the grid) | Allowed when it pays off. |
| PV-Prognose anpassen (adapt PV forecast) | Learns from measurements per hour and cloud cover; details under Analysis. |
| Netzdienlich (grid-friendly) | Avoid export and import peaks above the set limits where it costs little. A soft goal, not a hard limit. |
| Unsichere PV-Prognose (uncertain PV forecast) | "Vorsichtig" (cautious) plans more with the bad case of the forecast (Solcast p10). |
| Wetter für die Bewölkung (weather for cloud cover) | Weather entity used for learning per cloud cover. |

Strategy and grid charging apply immediately. Numbers are applied with **Speichern und neu
planen** (save and replan).

## Display ("Anzeige")

![Display](../images/settings-display.png)

- **Weather in the Live view**: can be hidden separately for phones and for tablets/computers. If
  it is off everywhere, Sunflow does not fetch the weather forecast for it.
- **Language**: "Like Home Assistant" (default: German if Home Assistant is set to German, English
  otherwise), "Deutsch" or "English". Applies to the interface and to messages such as plan notices
  and recommendations. The page reloads afterwards.

## Summary ("Übersicht")

Lists devices, tariff and forecast and marks what is still missing (see
[Installation and setup](installation.md#8-summary-übersicht)).
