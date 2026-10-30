"""Switches: Sunflow controls the devices (main switch) and each device."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SunflowConfigEntry
from .api import SunflowError
from .const import CONF_KIND, KIND_FORECAST
from .coordinator import SunflowCoordinator
from .entity import SunflowEntity, add_per_item

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunflowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Switches of the App entry."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        return
    state = entry.runtime_data.state
    async_add_entities([SunflowControlSwitch(state, entry)])
    add_per_item(
        entry,
        state,
        "devices",
        lambda d: [SunflowDeviceSwitch(state, entry, d)],
        async_add_entities,
    )


class _SunflowSwitch(SunflowEntity, SwitchEntity):
    """Writes the control setting to the App and refreshes the state."""

    async def _set(self, **body: Any) -> None:
        try:
            await self.coordinator.config_entry.runtime_data.client.set_control(**body)
        except SunflowError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()


class SunflowControlSwitch(_SunflowSwitch):
    """Main switch: Sunflow carries out the plan on the devices switched on below."""

    _attr_translation_key = "control"

    def __init__(
        self, coordinator: SunflowCoordinator, entry: SunflowConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "control")

    @property
    def is_on(self) -> bool:
        """Whether Sunflow controls."""
        return self.state_data["control"]["enabled"]

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Let Sunflow control (writes to the devices from the next minute on)."""
        await self._set(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hand all devices back."""
        await self._set(enabled=False)


class SunflowDeviceSwitch(_SunflowSwitch):
    """Sunflow controls this device (with the main switch on)."""

    _attr_translation_key = "device_control"

    def __init__(
        self,
        coordinator: SunflowCoordinator,
        entry: SunflowConfigEntry,
        device: dict[str, Any],
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            entry,
            f"device_{device['id']}_control",
            {"name": device["name"]},
        )
        self._id = device["id"]

    @property
    def available(self) -> bool:
        """Unavailable once the device is no longer controllable."""
        return super().available and self.item("devices", self._id) is not None

    @property
    def is_on(self) -> bool | None:
        """Whether this device is switched on (controlled while the main switch is on)."""
        device = self.item("devices", self._id)
        return device["enabled"] if device else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Control this device while the main switch is on."""
        await self._set(device_id=self._id, device_enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hand this device back."""
        await self._set(device_id=self._id, device_enabled=False)
