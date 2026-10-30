"""Hand the devices back when the Sunflow App stops answering while it controls them.

The App would otherwise leave them in its last command, e.g. a battery charging from
the grid. The App keeps the integration supplied with the Home Assistant calls that
hand each device back; after a while without answer they are run once.
"""

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SunflowData

_LOGGER = logging.getLogger(__name__)

UNREACHABLE_AFTER = timedelta(minutes=10)
CHECK_EVERY = timedelta(minutes=1)
ISSUE_ID = "app_unreachable"


class Watchdog:
    """Watches the state coordinator of the App entry."""

    def __init__(self, hass: HomeAssistant, data: SunflowData) -> None:
        """Initialize."""
        self._hass = hass
        self._data = data
        self._handed_back = False

    def start(self) -> Any:
        """Check every minute; returns the function that stops it."""
        return async_track_time_interval(self._hass, self._check, CHECK_EVERY)

    @property
    def _fallback(self) -> list[dict[str, Any]]:
        return (self._data.state.data or {}).get("fallback", [])

    async def _check(self, now: datetime) -> None:
        state = self._data.state
        last = state.last_update_success_time
        if state.last_update_success or last is None:
            self._recovered()
            return
        if dt_util.utcnow() - last < UNREACHABLE_AFTER or self._handed_back:
            return
        self._handed_back = True
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            ISSUE_ID,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_ID,
            translation_placeholders={
                "minutes": str(int(UNREACHABLE_AFTER.total_seconds() // 60))
            },
        )
        for call in self._fallback:
            try:
                await self._hass.services.async_call(
                    call["domain"],
                    call["service"],
                    call["data"],
                    target=call["target"],
                    blocking=True,
                )
            except Exception:
                _LOGGER.exception(
                    "Handing back with %s.%s failed", call["domain"], call["service"]
                )
        if self._fallback:
            _LOGGER.warning(
                "Sunflow App unreachable: devices handed back (%d calls)",
                len(self._fallback),
            )

    @callback
    def _recovered(self) -> None:
        if self._handed_back:
            self._handed_back = False
        ir.async_delete_issue(self._hass, DOMAIN, ISSUE_ID)
