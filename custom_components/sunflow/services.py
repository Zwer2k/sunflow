"""Actions of the Sunflow integration."""

from datetime import datetime
from typing import Any

import voluptuous as vol  # noqa: TID251 - custom integration, also for older Home Assistant

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import SunflowError
from .const import CONF_KIND, DOMAIN, KIND_FORECAST
from .coordinator import SunflowData

STRATEGIES = ["balanced", "pv_absorption", "autarky", "default"]
CONSUMER_STRATEGIES = ["cost", "pv_only", "fast"]

SET_STRATEGY_SCHEMA = vol.Schema(
    {vol.Required("strategy"): vol.In(STRATEGIES), vol.Optional("until"): cv.datetime}
)
SET_CONSUMER_SCHEMA = vol.Schema(
    {
        vol.Required("consumer"): cv.string,
        vol.Optional("strategy"): vol.In(CONSUMER_STRATEGIES),
        vol.Optional("target"): vol.Coerce(float),
        vol.Optional("paused"): cv.boolean,
        vol.Optional("until"): cv.datetime,
        vol.Optional("reset", default=False): cv.boolean,
    }
)
PLAN_LOAD_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("energy_kwh"): vol.All(
            vol.Coerce(float), vol.Range(min=0.01, max=100)
        ),
        vol.Required("power_kw"): vol.All(
            vol.Coerce(float), vol.Range(min=0.01, max=50)
        ),
        vol.Optional("earliest"): cv.datetime,
        vol.Optional("finish_by"): cv.datetime,
    }
)
CANCEL_LOAD_SCHEMA = vol.Schema({vol.Required("load_id"): cv.string})


def _app(hass: HomeAssistant) -> SunflowData:
    """The loaded App entry's data."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (
            entry.data.get(CONF_KIND) != KIND_FORECAST
            and entry.state is ConfigEntryState.LOADED
        ):
            return entry.runtime_data
    raise ServiceValidationError("Sunflow is not connected")


def _iso(value: datetime | None) -> str | None:
    """A time from the action as aware ISO string (local time if none given)."""
    if value is None:
        return None
    return (
        value if value.tzinfo else value.replace(tzinfo=dt_util.get_default_time_zone())
    ).isoformat()


async def _call(coroutine: Any) -> Any:
    try:
        return await coroutine
    except SunflowError as err:
        raise HomeAssistantError(str(err)) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""

    async def set_strategy(call: ServiceCall) -> None:
        data = _app(hass)
        strategy = call.data["strategy"]
        await _call(
            data.client.set_strategy(
                None if strategy == "default" else strategy,
                _iso(call.data.get("until")),
            )
        )
        await data.state.async_request_refresh()

    async def set_consumer(call: ServiceCall) -> None:
        data = _app(hass)
        wanted = call.data["consumer"]
        consumer = next(
            (
                c
                for c in (data.state.data or {}).get("consumers", [])
                if wanted in (c["id"], c["name"])
            ),
            None,
        )
        if consumer is None:
            raise ServiceValidationError(f"Unknown consumer {wanted}")
        if call.data["reset"]:
            await _call(data.client.set_consumer(consumer["id"], None))
        else:
            override = {
                k: call.data[k]
                for k in ("strategy", "target", "paused")
                if k in call.data
            }
            override["until"] = _iso(call.data.get("until"))
            await _call(data.client.set_consumer(consumer["id"], override))
        await data.state.async_request_refresh()

    async def plan_load(call: ServiceCall) -> ServiceResponse:
        data = _app(hass)
        load = await _call(
            data.client.add_load(
                {
                    "name": call.data["name"],
                    "energy_wh": call.data["energy_kwh"] * 1000,
                    "power_w": call.data["power_kw"] * 1000,
                    "earliest": _iso(call.data.get("earliest")),
                    "finish_by": _iso(call.data.get("finish_by")),
                }
            )
        )
        await data.state.async_request_refresh()
        return {k: load[k] for k in ("id", "start", "end", "cost_eur")}

    async def cancel_load(call: ServiceCall) -> None:
        data = _app(hass)
        await _call(data.client.remove_load(call.data["load_id"]))
        await data.state.async_request_refresh()

    async def replan(call: ServiceCall) -> None:
        await _call(_app(hass).client.replan())

    hass.services.async_register(
        DOMAIN, "set_strategy", set_strategy, schema=SET_STRATEGY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "set_consumer", set_consumer, schema=SET_CONSUMER_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "plan_load",
        plan_load,
        schema=PLAN_LOAD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "cancel_load", cancel_load, schema=CANCEL_LOAD_SCHEMA
    )
    hass.services.async_register(DOMAIN, "replan", replan)
