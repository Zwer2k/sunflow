"""Select: the strategy of the home battery."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SunflowConfigEntry
from .api import SunflowError
from .const import CONF_KIND, KIND_FORECAST
from .coordinator import SunflowCoordinator
from .entity import SunflowEntity

PARALLEL_UPDATES = 1

STRATEGIES = ["balanced", "pv_absorption", "autarky"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunflowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Select of the App entry."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        return
    async_add_entities([SunflowStrategySelect(entry.runtime_data.state, entry)])


class SunflowStrategySelect(SunflowEntity, SelectEntity):
    """Strategy in force; choosing the default ends a change, any other applies until changed again."""

    _attr_translation_key = "strategy"
    _attr_options = STRATEGIES

    def __init__(
        self, coordinator: SunflowCoordinator, entry: SunflowConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "strategy")

    @property
    def current_option(self) -> str | None:
        """The strategy in force now."""
        return self.state_data["strategy"]["effective"]

    async def async_select_option(self, option: str) -> None:
        """Change the strategy (the default strategy itself stays as set in Sunflow)."""
        default = self.state_data["strategy"]["default"]
        try:
            await self.coordinator.config_entry.runtime_data.client.set_strategy(
                None if option == default else option
            )
        except SunflowError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
