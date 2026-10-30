"""Base entity and devices of the Sunflow integration."""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SunflowCoordinator


def app_device(entry: ConfigEntry) -> DeviceInfo:
    """The device of the Sunflow App: plan, control and consumers."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Sunflow",
        manufacturer="Sunflow",
        model="Energiemanagement",
        entry_type=DeviceEntryType.SERVICE,
    )


class SunflowEntity(CoordinatorEntity[SunflowCoordinator]):
    """An entity on the Sunflow device, fed by the state coordinator."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SunflowCoordinator,
        entry: ConfigEntry,
        key: str,
        placeholders: dict[str, str] | None = None,
    ) -> None:
        """Initialize; key also names the translation."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = app_device(entry)
        self._attr_translation_placeholders = placeholders or {}

    @property
    def state_data(self) -> dict[str, Any]:
        """The latest state of the App."""
        return self.coordinator.data or {}

    def item(self, collection: str, item_id: str) -> dict[str, Any] | None:
        """One device or consumer of the state."""
        return next(
            (i for i in self.state_data.get(collection, []) if i["id"] == item_id),
            None,
        )


def add_per_item(
    entry: ConfigEntry,
    coordinator: SunflowCoordinator,
    collection: str,
    factory: Any,
    async_add_entities: Any,
) -> None:
    """Add entities for every device or consumer of the state, also ones appearing later."""
    added: set[str] = set()

    def add_new() -> None:
        new = [
            i
            for i in (coordinator.data or {}).get(collection, [])
            if i["id"] not in added
        ]
        added.update(i["id"] for i in new)
        entities = [entity for item in new for entity in factory(item)]
        if entities:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
