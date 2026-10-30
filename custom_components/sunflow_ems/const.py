"""Constants for the Sunflow integration."""

from datetime import timedelta

DOMAIN = "sunflow_ems"
API_VERSION = "v1"
DEFAULT_URL = "http://localhost:8099"

CONF_TOKEN = "token"
# "app": the connection to the Sunflow App; "forecast": one PV forecast of it
CONF_KIND = "kind"
CONF_APP_ENTRY = "app_entry_id"
CONF_TARGET = "target"
CONF_KNOWN_TARGETS = "known_targets"
KIND_APP = "app"
KIND_FORECAST = "forecast"

FORECAST_INTERVAL = timedelta(minutes=15)
STATE_INTERVAL = timedelta(minutes=1)
