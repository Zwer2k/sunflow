"""Calendar: what the plan does when (consumers, grid charging, PV limiting, one-time loads)."""

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SunflowConfigEntry
from .const import CONF_KIND, KIND_FORECAST
from .coordinator import SunflowCoordinator
from .entity import SunflowEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SunflowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Calendar of the App entry."""
    if entry.data.get(CONF_KIND) == KIND_FORECAST:
        return
    async_add_entities([SunflowPlanCalendar(entry.runtime_data.state, entry)])


class SunflowPlanCalendar(SunflowEntity, CalendarEntity):
    """The plan's windows ahead as events."""

    _attr_translation_key = "plan"

    def __init__(
        self, coordinator: SunflowCoordinator, entry: SunflowConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, "plan_calendar")

    def _events(self) -> list[CalendarEvent]:
        events = []
        for item in self.state_data.get("calendar", []):
            start, end = (
                dt_util.parse_datetime(item["start"]),
                dt_util.parse_datetime(item["end"]),
            )
            if start is None or end is None:
                continue
            events.append(
                CalendarEvent(
                    start=start,
                    end=end,
                    summary=item["summary"],
                    description=item["description"] or None,
                )
            )
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """The running or next event."""
        now = dt_util.utcnow()
        return next((e for e in self._events() if e.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events in the range (the plan reaches up to two days ahead)."""
        return [e for e in self._events() if e.end > start_date and e.start < end_date]
