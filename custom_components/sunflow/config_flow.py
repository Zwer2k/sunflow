"""Config flow for Sunflow: the App (found by the Supervisor or by hand) and its forecasts."""

from typing import Any

import voluptuous as vol  # noqa: TID251 - custom integration, also for older Home Assistant

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import SunflowAuthError, SunflowClient, SunflowError
from .const import (
    CONF_APP_ENTRY,
    CONF_KIND,
    CONF_TARGET,
    CONF_TOKEN,
    DEFAULT_URL,
    DOMAIN,
    KIND_APP,
    KIND_FORECAST,
)

APP_UNIQUE_ID = "app"
KIND_TITLES = {"total": "Gesamt", "plant": "Anlage", "plane": "Fläche"}


class SunflowConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sunflow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._discovered: dict[str, Any] = {}

    async def _check(self, url: str, token: str) -> str | None:
        """Error key, None when the App answers with this token."""
        client = SunflowClient(async_get_clientsession(self.hass), url, token)
        try:
            await client.health()
            await client.string_energy()
        except SunflowAuthError:
            return "invalid_auth"
        except SunflowError:
            return "cannot_connect"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for address and token of the App (shown in Sunflow under Integrationen)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            await self.async_set_unique_id(APP_UNIQUE_ID)
            self._abort_if_unique_id_configured()
            if (error := await self._check(url, user_input[CONF_TOKEN])) is None:
                return self.async_create_entry(
                    title="Sunflow",
                    data={
                        CONF_KIND: KIND_APP,
                        CONF_URL: url,
                        CONF_TOKEN: user_input[CONF_TOKEN],
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=DEFAULT_URL): str,
                    vol.Required(CONF_TOKEN): str,
                }
            ),
            errors=errors,
        )

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """The App announced itself through the Supervisor."""
        config = discovery_info.config
        url = f"http://{config['host']}:{config['port']}"
        await self.async_set_unique_id(APP_UNIQUE_ID)
        # A reinstalled App gets a new host or token: the entry follows it
        self._abort_if_unique_id_configured(
            updates={CONF_URL: url, CONF_TOKEN: config["token"]}
        )
        self._discovered = {CONF_URL: url, CONF_TOKEN: config["token"]}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the App found by the Supervisor."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if (
                error := await self._check(
                    self._discovered[CONF_URL], self._discovered[CONF_TOKEN]
                )
            ) is None:
                return self.async_create_entry(
                    title="Sunflow", data={CONF_KIND: KIND_APP, **self._discovered}
                )
            errors["base"] = error
        return self.async_show_form(step_id="hassio_confirm", errors=errors)

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """A forecast of the App: created without asking, it only reads."""
        await self.async_set_unique_id(
            f"forecast_{discovery_info[CONF_APP_ENTRY]}_{discovery_info[CONF_TARGET]}"
        )
        self._abort_if_unique_id_configured()
        kind = KIND_TITLES.get(discovery_info["kind"], "")
        title = f"PV-Prognose · {discovery_info['name']}" + (
            f" ({kind})" if kind and discovery_info["kind"] != "total" else ""
        )
        return self.async_create_entry(
            title=title,
            data={
                CONF_KIND: KIND_FORECAST,
                CONF_APP_ENTRY: discovery_info[CONF_APP_ENTRY],
                CONF_TARGET: discovery_info[CONF_TARGET],
                "name": discovery_info["name"],
                "target_kind": discovery_info["kind"],
            },
        )
