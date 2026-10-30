"""The learned PV forecast of Sunflow for the energy dashboard."""

from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_KIND, CONF_TARGET, DOMAIN, KIND_FORECAST


async def async_get_solar_forecast(
    hass: HomeAssistant, config_entry_id: str
) -> dict[str, Any] | None:
    """Wh per hour of the forecast this entry stands for."""
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.data.get(CONF_KIND) != KIND_FORECAST
    ):
        return None
    if not hasattr(entry, "runtime_data") or entry.runtime_data.forecasts.data is None:
        return None
    for target in entry.runtime_data.forecasts.data.get("targets", []):
        if target["id"] == entry.data[CONF_TARGET]:
            return {"wh_hours": target["wh_hours"]}
    return None
