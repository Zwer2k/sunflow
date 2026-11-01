# Sunflow integration in Home Assistant

[Deutsch](../integration.md) · **English**

The integration connects Home Assistant with the Sunflow App. It brings the learned PV forecast
into the Energy dashboard, exposes plan and control as entities and lets automations operate
Sunflow. Calculation and control remain in the App.

## Setup

- **Automatic:** the App announces itself to the Supervisor. Home Assistant shows the
  integration as discovered under "Settings → Devices & services"; one click connects it.
- **Manual:** in Sunflow under "Integrationen → Sunflow-Integration in Home Assistant →
  Zugangsdaten anzeigen" (show credentials) you find address and key. Enter both when adding the
  integration.

## Devices

| Device | Content |
|---|---|
| Sunflow | plan, control, loads, calendar |
| Sunflow PV-Prognose · Gesamt | forecast of all PV |
| Sunflow PV-Prognose · *system* | one per inverter |
| Sunflow PV-Prognose · *plane* | one per forecast plane (e.g. roof south-east, carport) |
| Sunflow PV-Strings | energy and power per PV string |

Every forecast is a separate integration entry, because the Energy dashboard selects forecasts by
entry. Which kinds exist (total, per inverter, per plane/string) is set in Sunflow under
"Integrationen → Sunflow-Integration in Home Assistant". The integration creates new planes and
systems itself and removes deselected ones; a forecast entry deleted in Home Assistant is not
created again as long as it stays offered.

## Forecast in the Energy dashboard

1. Under "Settings → Dashboards → Energy → Solar panels" edit the solar source.
2. Under "Solar production forecast" choose the matching Sunflow entry: "Gesamt" (total) for a
   single source, the system for one inverter, or the plane for one string.
3. For strings without their own energy meter use the "*string* Energie" sensors of the
   "Sunflow PV-Strings" device as solar source.

The forecast is the one of the chosen source (Solcast, Forecast.Solar) with the learned
correction per plane, hourly over the full horizon of the source. Past hours (including the past
31 days) come from the forecast Sunflow recorded at the time. Each forecast additionally has the
sensors "Prognose heute" (today), "Prognose Rest heute" (rest of today), "Prognose morgen"
(tomorrow) and "Prognose Leistung jetzt" (power now).

## Entities of the "Sunflow" device

| Entity | Meaning |
|---|---|
| Aktion Speicher (battery action) | what the plan is doing with the home battery right now |
| Strompreis jetzt, Günstigstes Fenster, Günstigster Preis (price now, cheapest window, cheapest price) | from the plan's tariff |
| Speicher in einer Stunde (battery in one hour) | planned state of charge |
| Ersparnis des Plans (plan savings) | compared to plain self-consumption, over the plan period |
| PV-Überschuss (PV surplus, sensor and binary sensor) | PV minus consumption; on from 300 W, for your own automations |
| Laden aus dem Netz, PV begrenzt (charging from grid, PV limited) | what the plan is doing right now |
| Plan | problem if the plan is older than 30 minutes or failed |
| Plan aktualisiert, Nächste einmalige Last (plan updated, next one-off load) | timestamps |
| *device* Aktion | what Sunflow does with a controllable device (reason, power, phases as attributes) |
| *load* nächster Start, geplante Energie, jetzt eingeplant (next start, planned energy, planned now) | per wallbox, heating rod, own load |
| Sunflow steuert, *device* steuern (Sunflow controls, control *device*) | main switch and one switch per device |
| Strategie (strategy) | Balanced, Absorb PV, Self-sufficiency |
| Neu planen (replan) | button |
| Plan (calendar) | charging, heating, grid charging and curtailment windows, one-off loads |

## Actions

| Action | Purpose |
|---|---|
| `sunflow_ems.set_strategy` | strategy until a time; `default` returns to the configured one |
| `sunflow_ems.set_consumer` | target, strategy or pause of a load for a while; `reset` cancels it |
| `sunflow_ems.plan_load` | schedule a one-off load; responds with start, end and cost |
| `sunflow_ems.cancel_load` | cancel a one-off load |
| `sunflow_ems.replan` | replan immediately |

## Events

| Event | When |
|---|---|
| `sunflow_ems_plan_updated` | a new plan is available |
| `sunflow_ems_consumer_start` | a load is planned from now on |
| `sunflow_ems_load_start` | a one-off load should start now |
| `sunflow_ems_plug_in_suggested` | a car is at home but not plugged in and would get energy soon (PV surplus or cheap power): worth plugging in. Data: `vehicle`, `wallbox`, `start`, `energy_kwh`, `pv_kwh` |

### Example: plug-in reminder

```yaml
- alias: Plug in the car
  triggers:
    - trigger: event
      event_type: sunflow_ems_plug_in_suggested
  actions:
    - action: notify.mobile_app_phone
      data:
        title: "Plug in {{ trigger.event.data.vehicle }}"
        message: >-
          From {{ as_timestamp(trigger.event.data.start) | timestamp_custom('%H:%M') }}
          {{ trigger.event.data.energy_kwh }} kWh are planned,
          {{ trigger.event.data.pv_kwh }} kWh of it PV surplus.
```

### Example: washing machine at the cheapest time

```yaml
# Schedule when the ready switch is turned on, done by 18:00
- alias: Schedule washing machine
  triggers:
    - trigger: state
      entity_id: input_boolean.washing_machine_ready
      to: "on"
  actions:
    - action: sunflow_ems.plan_load
      data:
        name: Washing machine
        energy_kwh: 1.2
        power_kw: 2
        finish_by: "{{ today_at('18:00') }}"

# Switch on at the planned start
- alias: Start washing machine
  triggers:
    - trigger: event
      event_type: sunflow_ems_load_start
      event_data:
        name: Washing machine
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.washing_machine
```

Sunflow looks for a continuous run time in which the energy costs least: planned export first
(then only the feed-in tariff is lost), otherwise the grid price.

## Watchdog

If Sunflow controls devices and the App does not respond for ten minutes, the integration hands
the devices back itself: battery to self-consumption, wallboxes and loads to their own logic, PV
limit removed. The App keeps the necessary Home Assistant calls ready for this. A notice appears
under "Repairs" until the App responds again; after that it continues controlling as before.
