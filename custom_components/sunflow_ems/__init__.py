"""The Sunflow integration: the Sunflow App's forecast and state in Home Assistant.

An "app" entry connects to the App. Every PV forecast of it (total, per plant, per
plane) gets an entry of its own, created by the App entry, because the energy
dashboard chooses forecasts by config entry.
"""

from homeassistant.config_entries import (
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, discovery_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import SunflowClient
from .const import (
    CONF_APP_ENTRY,
    CONF_KIND,
    CONF_KNOWN_TARGETS,
    CONF_TARGET,
    CONF_TOKEN,
    DOMAIN,
    KIND_FORECAST,
)
from .coordinator import SunflowData, create_coordinators
from .services import async_setup_services
from .watchdog import Watchdog

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type SunflowConfigEntry = ConfigEntry[SunflowData]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SunflowConfigEntry) -> bool:
    """Set up the App connection or one of its forecasts."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        app = hass.config_entries.async_get_entry(entry.data[CONF_APP_ENTRY])
        # Created while the App entry finishes its setup; its data is there already
        if (
            app is None
            or app.state
            not in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
            or not hasattr(app, "runtime_data")
        ):
            raise ConfigEntryNotReady("Sunflow App entry not loaded yet")
        entry.runtime_data = app.runtime_data
    else:
        client = SunflowClient(
            async_get_clientsession(hass),
            entry.data[CONF_URL],
            entry.data.get(CONF_TOKEN),
        )
        data = create_coordinators(hass, entry, client)
        await data.forecasts.async_config_entry_first_refresh()
        await data.strings.async_config_entry_first_refresh()
        await data.state.async_config_entry_first_refresh()
        entry.runtime_data = data
        _sync_forecasts(hass, entry)
        entry.async_on_unload(
            data.forecasts.async_add_listener(lambda: _sync_forecasts(hass, entry))
        )
        entry.async_on_unload(data.state.async_add_listener(StateEvents(hass, data)))
        entry.async_on_unload(Watchdog(hass, data).start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _sync_forecasts(hass: HomeAssistant, entry: SunflowConfigEntry) -> None:
    """Follow the forecasts the App offers.

    A new one gets an entry; one the App no longer offers (deselected in Sunflow, plane
    removed) loses its entry and may come again later. One the user deleted while still
    offered stays deleted.
    """
    data = entry.runtime_data.forecasts.data or {}
    if "targets" not in data:
        return
    offered = {t["id"]: t for t in data["targets"]}
    known = set(entry.data.get(CONF_KNOWN_TARGETS, []))
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.data.get(CONF_APP_ENTRY) == entry.entry_id and other.data.get(CONF_TARGET) not in offered:
            hass.async_create_task(hass.config_entries.async_remove(other.entry_id))
    for target_id, target in offered.items():
        if target_id in known:
            continue
        discovery_flow.async_create_flow(
            hass,
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={
                CONF_APP_ENTRY: entry.entry_id,
                CONF_TARGET: target_id,
                "name": target["name"],
                "kind": target["kind"],
            },
        )
    if known != set(offered):
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_KNOWN_TARGETS: sorted(offered)})


class StateEvents:
    """Events for automations when the App's state changes: plan, consumers, one-time loads."""

    def __init__(self, hass: HomeAssistant, data: SunflowData) -> None:
        """Start from the current state, so a restart fires nothing."""
        self._hass = hass
        self._data = data
        self._plan, self._running, self._started = self._seen()

    def _seen(self) -> tuple[str | None, set[str], set[str]]:
        state = self._data.state.data or {}
        return (
            state.get("plan", {}).get("updated"),
            {c["id"] for c in state.get("consumers", []) if c.get("running")},
            {load["id"] for load in state.get("loads", []) if load.get("started")},
        )

    @callback
    def __call__(self) -> None:
        """Fire for what changed since the last update."""
        plan, running, started = self._seen()
        state = self._data.state.data or {}
        if plan is not None and plan != self._plan:
            self._hass.bus.async_fire(f"{DOMAIN}_plan_updated", {"updated": plan})
        for consumer in state.get("consumers", []):
            if consumer["id"] in running - self._running:
                self._hass.bus.async_fire(
                    f"{DOMAIN}_consumer_start",
                    {"id": consumer["id"], "name": consumer["name"]},
                )
        for load in state.get("loads", []):
            if load["id"] in started - self._started:
                self._hass.bus.async_fire(
                    f"{DOMAIN}_load_start",
                    {k: load[k] for k in ("id", "name", "start", "end")},
                )
        self._plan, self._running, self._started = plan, running, started


async def async_unload_entry(hass: HomeAssistant, entry: SunflowConfigEntry) -> bool:
    """Unload an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SunflowConfigEntry) -> None:
    """Without the App entry its forecasts have no source any more."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        return
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.data.get(CONF_APP_ENTRY) == entry.entry_id:
            await hass.config_entries.async_remove(other.entry_id)
