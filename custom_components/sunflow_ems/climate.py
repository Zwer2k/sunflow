"""Climate entity "Haus": the room setpoint Sunflow keeps the house at, the room temperature and the mode.

Sunflow stores setpoint and mode; this entity mirrors them. Changes in quick succession (e.g. an
automation stepping the setpoint) are sent once, with the last value. Mode off: Sunflow writes
nothing to the heat pump but keeps computing. For now Sunflow runs in shadow operation: the flow
target is computed and shown in the attributes, nothing is written.
"""

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SunflowConfigEntry
from .api import SunflowError
from .const import CONF_KIND, DOMAIN, KIND_FORECAST
from .coordinator import SunflowCoordinator
from .entity import SunflowEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1
# Changes within this time are sent together, with the last value
SEND_AFTER_S = 2.0
HVAC_MODES = [HVACMode.HEAT, HVACMode.OFF]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunflowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The climate entity of the App entry, once the App has rooms or a heat pump."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        return
    coordinator = entry.runtime_data.state
    added = False

    def add() -> None:
        nonlocal added
        if not added and (coordinator.data or {}).get("house") is not None:
            added = True
            async_add_entities([SunflowHouseClimate(hass, coordinator, entry)])

    add()
    entry.async_on_unload(coordinator.async_add_listener(add))


class SunflowHouseClimate(SunflowEntity, ClimateEntity):
    """Room setpoint and mode of the house."""

    _attr_translation_key = "house"
    _attr_hvac_modes = HVAC_MODES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS

    def __init__(
        self, hass: HomeAssistant, coordinator: SunflowCoordinator, entry: SunflowConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "house")
        # Sent with the next request; shown meanwhile
        self._pending: dict[str, Any] = {}
        self._debouncer = Debouncer(
            hass, _LOGGER, cooldown=SEND_AFTER_S, immediate=False, function=self._async_send
        )

    @property
    def _house(self) -> dict[str, Any]:
        return self.state_data.get("house") or {}

    @property
    def available(self) -> bool:
        """Available while the App reports the house."""
        return super().available and bool(self._house)

    @property
    def current_temperature(self) -> float | None:
        """Weighted room temperature."""
        return self._house.get("room_c")

    @property
    def target_temperature(self) -> float | None:
        """Room setpoint."""
        return self._pending.get("setpoint_c", self._house.get("setpoint_c"))

    @property
    def min_temp(self) -> float:
        """Lowest room setpoint."""
        return self._house.get("min_c", 18.0)

    @property
    def max_temp(self) -> float:
        """Highest room setpoint."""
        return self._house.get("max_c", 27.0)

    @property
    def target_temperature_step(self) -> float:
        """Step of the setpoint in the thermostat card."""
        return self._house.get("step_c", 0.5)

    @property
    def hvac_mode(self) -> HVACMode:
        """Heat or off (Sunflow writes nothing to the heat pump)."""
        return HVACMode.OFF if self._pending.get("mode", self._house.get("mode")) == "off" else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        """What the heat pumps do: heating while a compressor heats the house."""
        pumps = self._house.get("heat_pumps") or []
        if not pumps:
            return None
        if any(p.get("activity") == "heating" and p.get("compressor") for p in pumps):
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Plan target, flow target of each heat pump (shadow: computed, not written)."""
        house = self._house
        pumps = house.get("heat_pumps") or []
        flows = [p for p in pumps if p.get("flow")]
        first = flows[0]["flow"] if flows else {}
        return {
            "plan_target_temperature": house.get("plan_target_c"),
            # Planning follows: the plan target is the setpoint
            "reason": "setpoint",
            "shadow": house.get("shadow", True),
            "setpoint_change": house.get("settling"),
            "flow_target_temperature": first.get("target_c"),
            "flow_base_temperature": first.get("base_c"),
            "flow_correction": first.get("correction_k"),
            "flow_frozen": first.get("frozen"),
            "heat_pumps": {
                p["name"]: {k: p["flow"].get(k) for k in ("target_c", "base_c", "correction_k", "frozen", "reason")}
                for p in flows
            }
            if len(flows) > 1
            else None,
            "rooms": house.get("rooms_used"),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the room setpoint (to 0.1 °C); HA has checked the range already."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        if not self.min_temp <= temperature <= self.max_temp:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="setpoint_out_of_range",
                translation_placeholders={"min": str(self.min_temp), "max": str(self.max_temp)},
            )
        self._pending["setpoint_c"] = round(float(temperature), 1)
        if (mode := kwargs.get("hvac_mode")) is not None:
            self._pending["mode"] = "off" if mode == HVACMode.OFF else "heat"
        self.async_write_ha_state()
        await self._debouncer.async_call()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Heat or off."""
        self._pending["mode"] = "off" if hvac_mode == HVACMode.OFF else "heat"
        self.async_write_ha_state()
        await self._debouncer.async_call()

    async def async_turn_on(self) -> None:
        """Mode heat."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Mode off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _async_send(self) -> None:
        """Send the latest changes; on failure the App's values are shown again."""
        pending, self._pending = self._pending, {}
        if not pending:
            return
        try:
            stored = await self.coordinator.config_entry.runtime_data.client.set_climate(pending)
        except SunflowError as err:
            _LOGGER.warning("Room setpoint not saved in Sunflow: %s", err)
            self.async_write_ha_state()
            return
        data = self.coordinator.data or {}
        # What Sunflow stored, without waiting for the next poll
        self.coordinator.async_set_updated_data({**data, "house": {**(data.get("house") or {}), **stored}})

    async def async_will_remove_from_hass(self) -> None:
        """Send what is pending before going."""
        self._debouncer.async_shutdown()
        await self._async_send()
        await super().async_will_remove_from_hass()
