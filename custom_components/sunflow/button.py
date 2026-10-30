"""Button: plan again now."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SunflowConfigEntry
from .api import SunflowError
from .const import CONF_KIND, KIND_FORECAST
from .coordinator import SunflowCoordinator
from .entity import SunflowEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunflowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Button of the App entry."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        return
    async_add_entities([SunflowReplanButton(entry.runtime_data.state, entry)])


class SunflowReplanButton(SunflowEntity, ButtonEntity):
    """Plan again now, e.g. after changing something outside Sunflow."""

    _attr_translation_key = "replan"

    def __init__(
        self, coordinator: SunflowCoordinator, entry: SunflowConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "replan")

    async def async_press(self) -> None:
        """Ask the App for a new plan."""
        try:
            await self.coordinator.config_entry.runtime_data.client.replan()
        except SunflowError as err:
            raise HomeAssistantError(str(err)) from err
