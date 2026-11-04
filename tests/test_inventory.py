"""Tests for the inventory of past data in Home Assistant."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sunflow.analysis.inventory import earliest_days, solcast_days, states_days
from sunflow.ha import HACommandError

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)


class RecorderHA:
    """States recorded since a moment per entity, past forecasts per Solcast site."""

    def __init__(self, since: dict[str, datetime], forecasts: dict[str, datetime] | None = None) -> None:
        """Initialize."""
        self.since = since
        self.forecasts = forecasts or {}
        self.calls = 0

    async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
        """Answer a history query: the state at the start exists once recorded."""
        self.calls += 1
        moment = datetime.fromisoformat(data["start_time"])
        return {e: [{"s": "1"}] for e in data["entity_ids"] if self.since[e] <= moment}

    async def call_service(self, domain: str, service: str, data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Answer a Solcast query like the integration: a day before its history is an error."""
        self.calls += 1
        if self.forecasts[data["site"]] > datetime.fromisoformat(data["start_date_time"]):
            raise HACommandError("service_validation_error: The requested data range is not available")
        return {"response": {"data": [{"pv_estimate": 0.1}]}}


async def test_purge_period_found_to_the_day() -> None:
    """Entities recorded for the purge period, one added later, one never recorded."""
    ha = RecorderHA(
        {
            "sensor.pv": NOW - timedelta(days=100, hours=3),
            "sensor.load": NOW - timedelta(days=100, hours=3),
            "sensor.carport": NOW - timedelta(days=37, hours=3),
            "sensor.excluded": NOW + timedelta(days=1),
        }
    )

    days = await states_days(ha, list(ha.since), NOW)

    assert days == {"sensor.pv": 100, "sensor.load": 100, "sensor.carport": 37, "sensor.excluded": None}
    # Entities ending at the same purge are probed together
    assert ha.calls < 30


async def test_data_older_than_the_grid_is_at_least_its_end() -> None:
    """Statistics kept for good: the oldest probed day is the answer."""

    async def recorded(keys: list[str], days: int) -> set[str]:
        return set(keys)

    assert await earliest_days(["sensor.old"], recorded, 365) == {"sensor.old": 180}


@pytest.mark.parametrize(
    ("kept_days", "expected"),
    [
        pytest.param(40, 40, id="since-installed"),
        pytest.param(800, 730, id="history-limit"),
    ],
)
async def test_solcast_keeps_past_forecasts(kept_days: int, expected: int) -> None:
    """Past forecasts are queried day by day through the action."""
    midnight = NOW.replace(hour=0)
    ha = RecorderHA({}, {"site-a": midnight - timedelta(days=kept_days)})

    assert await solcast_days(ha, ["site-a"], NOW, UTC) == {"site-a": expected}
