"""Config flow for Sunflow."""

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_VERSION, DEFAULT_URL, DOMAIN


class SunflowConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sunflow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the URL of the Sunflow backend."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            self._async_abort_entries_match({CONF_URL: url})
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(
                    f"{url}/api/{API_VERSION}/health", raise_for_status=True
                ) as resp:
                    await resp.json()
            except aiohttp.ClientError, TimeoutError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="Sunflow", data={CONF_URL: url})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=DEFAULT_URL): str}),
            errors=errors,
        )
