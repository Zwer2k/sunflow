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
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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

PLATFORMS = [Platform.SENSOR]

type SunflowConfigEntry = ConfigEntry[SunflowData]


async def async_setup_entry(hass: HomeAssistant, entry: SunflowConfigEntry) -> bool:
    """Set up the App connection or one of its forecasts."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        app = hass.config_entries.async_get_entry(entry.data[CONF_APP_ENTRY])
        # Created while the App entry finishes its setup; its data is there already
        if app is None or app.state not in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS) or not hasattr(
            app, "runtime_data"
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
        entry.runtime_data = data
        _offer_new_forecasts(hass, entry)
        entry.async_on_unload(
            data.forecasts.async_add_listener(lambda: _offer_new_forecasts(hass, entry))
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _offer_new_forecasts(hass: HomeAssistant, entry: SunflowConfigEntry) -> None:
    """Create an entry for every forecast not seen before; one the user deleted stays deleted."""
    known = set(entry.data.get(CONF_KNOWN_TARGETS, []))
    targets = entry.runtime_data.forecasts.data.get("targets", [])
    new = [t for t in targets if t["id"] not in known]
    if not new:
        return
    for target in new:
        discovery_flow.async_create_flow(
            hass,
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={
                CONF_APP_ENTRY: entry.entry_id,
                CONF_TARGET: target["id"],
                "name": target["name"],
                "kind": target["kind"],
            },
        )
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_KNOWN_TARGETS: sorted(known | {t["id"] for t in new})},
    )


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
