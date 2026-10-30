"""Sensors: key figures of each PV forecast, energy and power of each PV string."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SunflowConfigEntry
from .const import CONF_KIND, CONF_TARGET, DOMAIN, KIND_FORECAST
from .coordinator import SunflowCoordinator

PARALLEL_UPDATES = 0

KIND_MODELS = {
    "total": "PV-Prognose gesamt",
    "plant": "PV-Prognose Anlage",
    "plane": "PV-Prognose Fläche",
}


@dataclass(frozen=True, kw_only=True)
class SunflowSensorDescription(SensorEntityDescription):
    """A value of an item (forecast target or string) of a coordinator."""

    value_fn: Callable[[dict[str, Any]], float | None]


def _kwh(key: str) -> Callable[[dict[str, Any]], float | None]:
    return lambda item: (
        round(item[key] / 1000, 3) if item.get(key) is not None else None
    )


FORECAST_SENSORS = (
    SunflowSensorDescription(
        key="today",
        translation_key="forecast_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_kwh("today_wh"),
    ),
    SunflowSensorDescription(
        key="remaining_today",
        translation_key="forecast_remaining_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_kwh("remaining_today_wh"),
    ),
    SunflowSensorDescription(
        key="tomorrow",
        translation_key="forecast_tomorrow",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_kwh("tomorrow_wh"),
    ),
    SunflowSensorDescription(
        key="power_now",
        translation_key="forecast_power_now",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda item: item.get("power_now_w"),
    ),
)

STRING_SENSORS = (
    SunflowSensorDescription(
        key="energy",
        translation_key="string_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_kwh("energy_wh"),
    ),
    SunflowSensorDescription(
        key="power",
        translation_key="string_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda item: item.get("power_w"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunflowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Forecast entries get their forecast sensors, the App entry the string sensors."""
    data = entry.runtime_data
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        target = entry.data[CONF_TARGET]
        device = DeviceInfo(
            identifiers={(DOMAIN, f"forecast_{target}")},
            name=f"Sunflow {entry.title}",
            manufacturer="Sunflow",
            model=KIND_MODELS.get(entry.data.get("target_kind", ""), "PV-Prognose"),
            entry_type=DeviceEntryType.SERVICE,
        )
        async_add_entities(
            SunflowItemSensor(
                data.forecasts,
                "targets",
                target,
                f"{entry.entry_id}_{d.key}",
                device,
                d,
            )
            for d in FORECAST_SENSORS
        )
        return

    device = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_strings")},
        name="Sunflow PV-Strings",
        manufacturer="Sunflow",
        model="Zähler je PV-String",
        entry_type=DeviceEntryType.SERVICE,
    )
    added: set[str] = set()

    @callback
    def add_new_strings() -> None:
        new = [s for s in data.strings.data.get("strings", []) if s["id"] not in added]
        added.update(s["id"] for s in new)
        async_add_entities(
            SunflowItemSensor(
                data.strings,
                "strings",
                s["id"],
                f"{entry.entry_id}_string_{s['id']}_{d.key}",
                device,
                d,
                {"name": s["name"]},
            )
            for s in new
            for d in STRING_SENSORS
        )

    add_new_strings()
    entry.async_on_unload(data.strings.async_add_listener(add_new_strings))


class SunflowItemSensor(CoordinatorEntity[SunflowCoordinator], SensorEntity):
    """A value of one item in a list of the coordinator's data."""

    _attr_has_entity_name = True
    entity_description: SunflowSensorDescription

    def __init__(
        self,
        coordinator: SunflowCoordinator,
        collection: str,
        item_id: str,
        unique_id: str,
        device: DeviceInfo,
        description: SunflowSensorDescription,
        placeholders: dict[str, str] | None = None,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._collection = collection
        self._item_id = item_id
        self._attr_unique_id = unique_id
        self._attr_device_info = device
        self._attr_translation_placeholders = placeholders or {}

    @property
    def _item(self) -> dict[str, Any] | None:
        items = (self.coordinator.data or {}).get(self._collection, [])
        return next((i for i in items if i["id"] == self._item_id), None)

    @property
    def available(self) -> bool:
        """Unavailable when the App no longer has the item (e.g. a plane removed)."""
        return super().available and self._item is not None

    @property
    def native_value(self) -> float | None:
        """The value from the App."""
        item = self._item
        return self.entity_description.value_fn(item) if item is not None else None
