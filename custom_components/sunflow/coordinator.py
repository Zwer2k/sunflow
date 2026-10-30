"""Polling the Sunflow App."""

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SunflowAuthError, SunflowClient, SunflowError
from .const import DOMAIN, FORECAST_INTERVAL, STATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SunflowCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """One endpoint of the App, polled at its own interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SunflowClient,
        name: str,
        interval: timedelta,
        fetch: str,
    ) -> None:
        """Initialize; fetch names the client method."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {name}",
            update_interval=interval,
        )
        self._fetch = getattr(client, fetch)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch()
        except SunflowAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SunflowError as err:
            raise UpdateFailed(str(err)) from err


@dataclass(kw_only=True)
class SunflowData:
    """What the App entry shares with its forecast entries and platforms."""

    client: SunflowClient
    forecasts: SunflowCoordinator
    strings: SunflowCoordinator


def create_coordinators(
    hass: HomeAssistant, entry: ConfigEntry, client: SunflowClient
) -> SunflowData:
    """Coordinators of an App entry."""
    return SunflowData(
        client=client,
        forecasts=SunflowCoordinator(
            hass, entry, client, "forecast", FORECAST_INTERVAL, "forecasts"
        ),
        strings=SunflowCoordinator(
            hass, entry, client, "strings", STATE_INTERVAL, "string_energy"
        ),
    )
