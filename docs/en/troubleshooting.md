# Troubleshooting

[Deutsch](../fehlersuche.md) · **English**

## The battery does not charge from the grid at cheap prices

- Is the strategy set to **As self-sufficient as possible**? It never charges
  from the grid.
- Is **Charge from grid** allowed under Settings → Planning?
- Is the price difference large enough? Grid charging has to cover losses and wear (see
  [Does charging from the grid pay off?](planning.md#does-charging-from-the-grid-pay-off)). High
  wear cost or low efficiency often prevent it.

## The wallbox does not charge as planned

- Control shows **"Card 1 belongs to no vehicle of this wallbox – wallbox controls itself"**:
  assign the card to
  the vehicle under Settings → Charging, or choose the vehicle under Control.
- Does Sunflow control the wallbox? See [Handing over control](control.md). evcc must not control
  the same loadpoint at the same time.
- If the wallbox allows less current than requested, Control and the log show a warning after
  two minutes.

## "Not scheduled: Temperature or target not readable"

A sensor of the load is currently
`unavailable` or `unknown`. Check it in Home Assistant; Sunflow plans the load again as soon as
the sensor delivers values.

## "PV forecast … only reaches until …"

A forecast source (e.g. Forecast.Solar) reaches less far
than the plan; after that its yield is missing from the plan. Use a source with a longer
horizon (Solcast) where possible, or accept the notice: it only affects the end of the plan.

## Control cannot be switched on

A battery needs a passed [device test](installation.md#3-device-test). If the test
fails it names the reason, e.g. swapped signs (fixable in expert mode under "Assign
entities") or an inverter that is not in its control mode.

## The log shows write errors

The message comes from Home Assistant or the device's integration. Often the inverter is not in
its control mode, e.g. because it was switched to "Self Use" in the manufacturer's app; the
message then names the current mode. An error that repeats every minute appears only once.

## Measurements are missing in the chart

Sunflow takes measurements from Home Assistant's statistics. If a sensor has no statistics (no
`state_class`) or is excluded from the recorder, Sunflow uses its own minute samples. These only
exist from the moment the device was set up in Sunflow.

## "No connection to Home Assistant"

Home Assistant is restarting or the App lost the connection.
Sunflow reconnects by itself and replans right away. While controlling, Sunflow sets
self-consumption after 30 minutes without a current plan.

## Logs and help

- **App log**: Home Assistant → Settings → Apps → Sunflow → Log.
- **AI assistant**: with the [MCP server](mcp.md) e.g. Claude Code can read and explain plan,
  control log and App log.
- **Report a problem**: open an issue on [GitHub](https://github.com/Zwer2k/sunflow/issues) with
  version, device and, if possible, the data export (Analysis → Shadow mode → Export
  data). The export contains your configuration and measurements; only share it if you
  are fine with that.
