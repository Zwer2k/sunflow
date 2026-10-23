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
