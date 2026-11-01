# Optimizers

[Deutsch](../optimierer.md) · **English**

Sunflow shows module optimizers the way the modules sit on the roof: in the Live view a click on
a PV string (the number at the circle = number of optimizers) opens its modules with their
current values. Which values appear (power, voltage and current at module input and output,
temperature, PWM) can be toggled there; the browser remembers the choice. Lighter modules deliver
less than the best one of the string.

## Sources

| Manufacturer | Integration | Values | Assignment to the string |
|---|---|---|---|
| Huawei (SUN2000-…-P) | [Huawei Solar](https://github.com/wlcrs/huawei_solar) | power, input and output voltage/current, temperature | Optimizers are sub-devices of the inverter. With only one string in operation automatically; otherwise choose in the settings (the integration does not pass on the string). |
| Tigo TS4 | [Tigo RS485](https://github.com/Zwer2k/ha-tigo-rs485) | power, input/output voltage, current, temperature, PWM | By the label from the Tigo integration: "A1" = string A (string 1 of the inverter), module 1. The inverter is suggested (the only one without optimizers of its own) or chosen. |

## Adding optimizers

As with inverters and wallboxes you decide what Sunflow uses:

1. **Settings → Integrationen:** select the integration. Its card shows how many optimizers it
   has; an optimizer-only integration such as Tigo RS485 appears as "Unterstützt · Optimierer"
   (supported · optimizers).
2. **Settings → Geräte (devices):** under the inverters there is an "Optimierer" card per
   integration with "von Sunflow nutzen" (use in Sunflow). Only then do the modules appear on the
   roof and in the Live view. When deselected they disappear again; assigned places stay stored.

Anyone who had arranged optimizers on the roof before Sunflow 0.72 keeps their integration as used.

## Settings → Optimizers

- **Quellen** (sources): how many optimizers are used per integration; for Tigo the inverter they
  are connected to.
- **Roof per string:** drag modules onto free places, or tap a module and then the place (phone);
  dropped on another module, the two swap. A selected module can be turned sideways ("quer legen" /
  "hochkant stellen"). "Vorschlag wiederherstellen" (restore suggestion) resets string and places.
- **Separators:** "+ Trenner" between two rows, or the narrow "+" between two places (applies to
  all rows), sets the following modules apart, e.g. another part of the roof.
- **Assignment:** string and name per optimizer; "automatisch" follows the suggestion.
