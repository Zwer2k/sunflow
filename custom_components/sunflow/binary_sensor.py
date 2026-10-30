"""Binary sensors: PV surplus, grid charging, curtailing, plan current, consumers running."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SunflowConfigEntry
from .const import CONF_KIND, KIND_FORECAST
from .coordinator import SunflowCoordinator
from .entity import SunflowEntity, add_per_item

PARALLEL_UPDATES = 0

# The App replans at least every 15 minutes; older means it is stuck
PLAN_MAX_AGE = timedelta(minutes=30)


def _plan_current(data: dict[str, Any]) -> bool:
    updated = data["plan"]["updated"]
    if updated is None or data["plan"]["error"]:
        return False
    parsed = dt_util.parse_datetime(updated)
    return parsed is not None and dt_util.utcnow() - parsed <= PLAN_MAX_AGE


@dataclass(frozen=True, kw_only=True)
class SunflowBinaryDescription(BinarySensorEntityDescription):
    """A yes/no of the App's state."""

    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSORS = (
    SunflowBinaryDescription(
        key="surplus",
        translation_key="surplus",
        value_fn=lambda d: d["live"]["surplus"],
    ),
    SunflowBinaryDescription(
        key="grid_charging",
        translation_key="grid_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda d: d["plan"]["grid_charging"],
    ),
    SunflowBinaryDescription(
        key="curtailing",
        translation_key="curtailing",
        value_fn=lambda d: d["plan"]["curtailing"],
    ),
    SunflowBinaryDescription(
        key="plan_problem",
        translation_key="plan_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: not _plan_current(d),
    ),
    SunflowBinaryDescription(
        key="controlling",
        translation_key="controlling",
        value_fn=lambda d: d["control"]["enabled"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunflowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Binary sensors of the App entry."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        return
    state = entry.runtime_data.state
    async_add_entities(SunflowBinarySensor(state, entry, d) for d in BINARY_SENSORS)
    add_per_item(
        entry,
        state,
        "consumers",
        lambda c: [SunflowConsumerRunning(state, entry, c)],
        async_add_entities,
    )


class SunflowBinarySensor(SunflowEntity, BinarySensorEntity):
    """A yes/no of the plan or the live state."""

    entity_description: SunflowBinaryDescription

    def __init__(
        self,
        coordinator: SunflowCoordinator,
        entry: SunflowConfigEntry,
        description: SunflowBinaryDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """The value from the App."""
        return self.entity_description.value_fn(self.state_data)


class SunflowConsumerRunning(SunflowEntity, BinarySensorEntity):
    """Whether the plan runs a consumer now."""

    _attr_translation_key = "consumer_running"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        coordinator: SunflowCoordinator,
        entry: SunflowConfigEntry,
        consumer: dict[str, Any],
    ) -> None:
        """Initialize."""
        super().__init__(
            coordinator,
            entry,
            f"consumer_{consumer['id']}_running",
            {"name": consumer["name"]},
        )
        self._id = consumer["id"]

    @property
    def available(self) -> bool:
        """Unavailable once the consumer is gone."""
        return super().available and self.item("consumers", self._id) is not None

    @property
    def is_on(self) -> bool | None:
        """Planned to run in the current slot."""
        consumer = self.item("consumers", self._id)
        return consumer["running"] if consumer else None
