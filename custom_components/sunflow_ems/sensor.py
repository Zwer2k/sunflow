"""Sensors: key figures of each PV forecast, energy and power of each PV string."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SunflowConfigEntry
from .const import CONF_KIND, CONF_TARGET, DOMAIN, KIND_FORECAST
from .coordinator import SunflowCoordinator
from .entity import SunflowEntity, add_per_item

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


@dataclass(frozen=True, kw_only=True)
class SunflowStateSensorDescription(SensorEntityDescription):
    """A value of the App's state."""

    value_fn: Callable[[dict[str, Any]], Any]


def _timestamp(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


PLAN_ACTIONS = [
    "auto",
    "hold",
    "force_charge",
    "force_discharge",
    "limit_charge",
    "limit_discharge",
]

# Battery actions, charge actions of loadpoints and "off" (not controlled)
DEVICE_ACTIONS = [*PLAN_ACTIONS, "charge", "stop", "follow", "release", "off"]

STATE_SENSORS = (
    SunflowStateSensorDescription(
        key="battery_action",
        translation_key="battery_action",
        device_class=SensorDeviceClass.ENUM,
        options=PLAN_ACTIONS,
        value_fn=lambda d: d["plan"]["action"],
    ),
    SunflowStateSensorDescription(
        key="price_now",
        translation_key="price_now",
        native_unit_of_measurement="ct/kWh",
        suggested_display_precision=1,
        value_fn=lambda d: d["plan"]["price_ct"],
    ),
    SunflowStateSensorDescription(
        key="cheapest_start",
        translation_key="cheapest_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _timestamp(d["plan"]["cheapest_start"]),
    ),
    SunflowStateSensorDescription(
        key="cheapest_price",
        translation_key="cheapest_price",
        native_unit_of_measurement="ct/kWh",
        suggested_display_precision=1,
        value_fn=lambda d: d["plan"]["cheapest_price_ct"],
    ),
    SunflowStateSensorDescription(
        key="soc_in_an_hour",
        translation_key="soc_in_an_hour",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda d: d["plan"]["soc_in_an_hour_pct"],
    ),
    SunflowStateSensorDescription(
        key="savings",
        translation_key="savings",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        value_fn=lambda d: d["plan"]["savings_eur"],
    ),
    SunflowStateSensorDescription(
        key="surplus",
        translation_key="surplus",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d["live"]["surplus_w"],
    ),
    SunflowStateSensorDescription(
        key="next_load",
        translation_key="next_load",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _timestamp(
            min(
                (
                    load["start"]
                    for load in d.get("loads", [])
                    if load["start"] and not load["started"]
                ),
                default=None,
            )
        ),
    ),
    SunflowStateSensorDescription(
        key="plan_updated",
        translation_key="plan_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _timestamp(d["plan"]["updated"]),
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

    async_add_entities(SunflowStateSensor(data.state, entry, d) for d in STATE_SENSORS)
    add_per_item(
        entry,
        data.state,
        "devices",
        lambda d: [SunflowDeviceActionSensor(data.state, entry, d)],
        async_add_entities,
    )
    add_per_item(
        entry,
        data.state,
        "consumers",
        lambda c: [
            SunflowConsumerSensor(data.state, entry, c, "next_start"),
            SunflowConsumerSensor(data.state, entry, c, "planned_energy"),
        ],
        async_add_entities,
    )


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


class SunflowStateSensor(SunflowEntity, SensorEntity):
    """A value of the plan or the live state."""

    entity_description: SunflowStateSensorDescription

    def __init__(
        self,
        coordinator: SunflowCoordinator,
        entry: SunflowConfigEntry,
        description: SunflowStateSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """The value from the App."""
        return self.entity_description.value_fn(self.state_data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The one-time loads with their starts, for the next-load sensor."""
        if self.entity_description.key != "next_load":
            return None
        return {"loads": self.state_data.get("loads", [])}


class SunflowDeviceActionSensor(SunflowEntity, SensorEntity):
    """What Sunflow does with a controllable device, and why."""

    _attr_translation_key = "device_action"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = DEVICE_ACTIONS

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
            f"device_{device['id']}_action",
            {"name": device["name"]},
        )
        self._id = device["id"]

    @property
    def available(self) -> bool:
        """Unavailable once the device is no longer controllable."""
        return super().available and self.item("devices", self._id) is not None

    @property
    def native_value(self) -> str | None:
        """The action, "nicht gesteuert" while control is off."""
        device = self.item("devices", self._id) or {}
        if not device.get("controlled"):
            return "off"
        return device.get("action") if device.get("action") in DEVICE_ACTIONS else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Reason, power and what else the control reports."""
        device = self.item("devices", self._id) or {}
        return {
            k: device.get(k)
            for k in ("reason", "power_w", "phases", "limit_w", "error")
        }


class SunflowConsumerSensor(SunflowEntity, SensorEntity):
    """When a consumer is planned to start, and how much energy it gets."""

    def __init__(
        self,
        coordinator: SunflowCoordinator,
        entry: SunflowConfigEntry,
        consumer: dict[str, Any],
        key: str,
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            entry,
            f"consumer_{consumer['id']}_{key}",
            {"name": consumer["name"]},
        )
        self._id = consumer["id"]
        self._key = key
        self._attr_translation_key = f"consumer_{key}"
        if key == "next_start":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        else:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_suggested_display_precision = 1

    @property
    def available(self) -> bool:
        """Unavailable once the consumer is gone."""
        return super().available and self.item("consumers", self._id) is not None

    @property
    def native_value(self) -> Any:
        """The planned start or energy."""
        consumer = self.item("consumers", self._id) or {}
        if self._key == "next_start":
            return _timestamp(consumer.get("next_start"))
        return round(consumer.get("planned_wh", 0.0) / 1000, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Level, target, need and why it is not planned."""
        consumer = self.item("consumers", self._id) or {}
        return {
            k: consumer.get(k)
            for k in ("level", "target", "unit", "vehicle", "need_wh", "reason")
        }
