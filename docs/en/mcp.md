# MCP server for AI assistants

[Deutsch](../mcp.md) · **English**

Sunflow has a built-in MCP server ([Model Context Protocol](https://modelcontextprotocol.io)).
An AI assistant such as Claude Code can use it to read the data of your Sunflow installation,
e.g. to explain why the plan looks the way it does, why the wallbox did not charge, or where a
forecast was off.

**It only reads.** No tool can change the configuration, control devices or send anything to Home
Assistant. The SQL query opens the recording read-only and only allows reading statements.

## Setup

1. In Home Assistant under **Settings → Apps → Sunflow → Configuration → Network** enter a port
   (e.g. `8099`) for "MCP-Server für KI-Assistenten (nur lesend)" (MCP server for AI assistants,
   read only) and restart the App. Without a port the server cannot be reached from outside.
2. In Sunflow under **Settings → Integrations**, section "MCP server for AI assistants",
   click **Generate** next to the key. Key and ready-made command are shown **only this
   once**; Sunflow only stores a checksum.
3. Run the command where the assistant runs, e.g.:

   ```bash
   claude mcp add --transport http sunflow http://homeassistant.local:8099/mcp \
     --header "Authorization: Bearer <key>"
   ```

Without a key the server is closed. **Revoke …** invalidates the key immediately;
**New …** replaces it (e.g. if it was lost). The key is only valid for `/mcp`, not for the
rest of Sunflow's interface, and is not part of the data export.

The port is reachable in the home network without encryption (HTTP). Do not expose it to the internet.

## Tools

| Tool | Content |
|---|---|
| `get_status` | version, connection to Home Assistant |
| `get_live` | current power values |
| `get_plan` | current plan with slots, prices, forecasts, loads, warnings |
| `get_control`, `get_control_log` | control and what Sunflow wrote to devices (with errors) |
| `get_history` | measured values from Home Assistant's statistics |
| `get_shadow`, `query_shadow_db` | recording planned vs. actual; free SQL query on it |
| `get_energy`, `get_costs`, `get_efficiency` | balances, costs, efficiency |
| `get_data_inventory` | how far back the HA history and past Solcast forecasts go |
| `get_learning_sources`, `get_learning_source_quality` | chosen learning sources and how plausible their values are |
| `get_pv_sun` | PV correction by sun position per plane with backtest, soiling in the history, expectation by season with comparison, snow on all planes and in the weather forecast, direction from Solcast |
| `get_appliances` | consumers that are only measured: energy per day, active days, usual hours |
| `get_config`, `get_vehicles`, `get_batteries`, `get_site` | configuration |
| `get_sources`, `get_tariff_series`, `get_forecast_series`, `get_pv_learning` | prices and PV forecast |
| `get_device_parameters`, `get_device_efficiency`, `get_direct_status` | single devices |
| `get_recommendations`, `get_integration_state` | recommendations, data for the integration |
| `get_log` | errors, warnings and notices of the App since the last start, with traceback |

## Permissions

Under "May read" you decide per area what the assistant may see: live, plan, prices
and forecasts · history, recording and analysis (with SQL) · configuration and devices · control
and control log · errors and warnings of the App. Version and connection are always readable. A
disabled area is missing from the tool list and refused; some assistants only reload the list on
their next start.

The plan over 96 hours is large. If Claude Code truncates the answer, the environment variable
`MAX_MCP_OUTPUT_TOKENS=50000` helps, or targeted queries via `query_shadow_db`.
