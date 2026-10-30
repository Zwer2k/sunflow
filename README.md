# Sunflow

Predictive energy management for Home Assistant – plans battery, EV and heat
pump around PV forecasts and dynamic prices.

## Repository layout

| Path | Content |
|---|---|
| `sunflow_app/` | Home Assistant App (config, Dockerfile) |
| `backend/` | Python backend: HA client, optimizer, forecasting, API |
| `frontend/` | Web UI: setup wizard, plan view (served by the backend / Ingress) |
| `custom_components/sunflow/` | Thin integration (HACS): entities, actions, watchdog |
| `profiles/` | Manufacturer profiles mapping roles/actions to HA entities |
| `schema/` | JSON schemas for profiles and configuration |
| `tests/` | Tests |

The same repository is added both to the App store (`repository.yaml`) and to
HACS (`hacs.json`).

### Backend (`backend/sunflow/`)

| Package | Content |
|---|---|
| `api/` | HTTP API, one router per area (devices, setup, sources, plan, control, …); `app.py` builds the app |
| `config/` | Configuration model (`models.py`) and its storage with migrations (`store.py`) |
| `devices/` | Profiles, discovery, entity roles, writing to devices, device test, site view |
| `planning/` | Forecast sources, load and PV learning, vehicles, the optimizer and the plan |
| `control/` | Carrying out the plan on batteries and loadpoints |
| `shadow/` | Background loop and the recorded plan vs. measurement |
| `analysis/` | Energy balance, costs, efficiency, history, recommendations |
| `ha/` | Home Assistant websocket client |

### Frontend (`frontend/src/`)

| Folder | Content |
|---|---|
| `app.js` | Shell: navigation, setup and settings |
| `views/` | Live, plan, control, analysis |
| `settings/` | Settings areas |
| `components/` | Charts, energy flows, dialog, entity picker |
| `lib/` | API client, formatting, polling |

Profiles are grouped by kind of device (`profiles/inverters/`, `meters/`,
`loadpoints/`, `loads/`); a profile is identified by its `profile` field, not
its path.

## Installation as Home Assistant App

1. Settings → Apps → App store → ⋮ → Repositories → add `https://github.com/Zwer2k/sunflow`
2. Install "Sunflow" (the Supervisor builds it on the device; this takes a few minutes)
3. Start it and open the Sunflow panel; set up devices in the wizard

Updates: the image fetches `main` at build time. A new version number in
`sunflow_app/config.yaml` triggers the update and a fresh clone; "Rebuild"
without a version change may reuse the cached clone.

Shadow mode data can be downloaded in the overview ("Daten exportieren") and
merged into another instance ("Export importieren").

## Development

The backend runs as a plain process during development and connects to a
Home Assistant instance with a long-lived access token:

```bash
cd backend
# In the HA devcontainer the HA venv is active and uv only accepts system interpreters
env -u VIRTUAL_ENV uv venv --python /home/vscode/.local/bin/python3.14 .venv
env -u VIRTUAL_ENV uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m sunflow
```

Settings are read from the environment or from `backend/.env` (git-ignored):

```
SUNFLOW_HA_TOKEN=<long-lived access token>
# optional
SUNFLOW_HA_URL=http://localhost:8123
SUNFLOW_PORT=8099
```

Inside the App it uses the Supervisor proxy and `SUPERVISOR_TOKEN` instead.
