"""Config flow for Sunflow: the App (found by the Supervisor or by hand) and its forecasts."""

from collections.abc import Mapping
from typing import Any

import voluptuous as vol  # noqa: TID251 - custom integration, also for older Home Assistant

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio
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

    async def _announced(self) -> dict[str, Any] | None:
        """Address and token of an App that announced itself to the Supervisor and answers."""
        if not is_hassio(self.hass):
            return None
        # Only with the Supervisor; imported here so other installations never load it
        from aiohasupervisor import SupervisorError  # noqa: PLC0415

        from homeassistant.components.hassio import get_supervisor_client  # noqa: PLC0415

        try:
            messages = await get_supervisor_client(self.hass).discovery.list()
        except SupervisorError:
            return None
        for message in messages:
            if message.service != DOMAIN:
                continue
            config = message.config
            found = {
                CONF_URL: f"http://{config['host']}:{config['port']}",
                CONF_TOKEN: config["token"],
            }
            # Sunflow and Sunflow Edge may both be installed; only one of them runs
            if await self._check(found[CONF_URL], found[CONF_TOKEN]) is None:
                return found
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The App found through the Supervisor; otherwise ask for its address and token."""
        errors: dict[str, str] = {}
        if user_input is None:
            await self.async_set_unique_id(APP_UNIQUE_ID)
            self._abort_if_unique_id_configured()
            if (found := await self._announced()) is not None:
                self._discovered = found
                return await self.async_step_hassio_confirm()
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

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The App rejected the token, e.g. after it was reinstalled."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the current token of the App."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            if (
                error := await self._check(entry.data[CONF_URL], user_input[CONF_TOKEN])
            ) is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
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
