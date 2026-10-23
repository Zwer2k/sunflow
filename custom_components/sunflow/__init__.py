"""The Sunflow integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

type SunflowConfigEntry = ConfigEntry[None]


async def async_setup_entry(hass: HomeAssistant, entry: SunflowConfigEntry) -> bool:
    """Set up Sunflow from a config entry."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SunflowConfigEntry) -> bool:
    """Unload a config entry."""
    return True
