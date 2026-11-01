"""Tests for means from the recorded states of sensors without statistics."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.devices.state_history import means_from_states

START = datetime(2026, 11, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _row(minutes: float, state: str, unit: str | None = None) -> dict:
    row = {"s": state, "lu": (START + timedelta(minutes=minutes)).timestamp()}
    return row | ({"a": {"unit_of_measurement": unit}} if unit else {})


def test_states_are_weighted_by_how_long_they_held() -> None:
    """A state before the window holds into it; kW become W; each hour is its own mean."""
    rows = [_row(-30, "1", "kW"), _row(15, "3"), _row(90, "2")]

    means = means_from_states(rows, START, START + 2 * HOUR, HOUR)

    # 15 min 1 kW + 45 min 3 kW; then 30 min 3 kW + 30 min 2 kW
    assert means == [(START, pytest.approx(2500)), (START + HOUR, pytest.approx(2500))]


def test_gaps_leave_hours_out() -> None:
    """An hour mostly unavailable says nothing; a sensor holding its last value covers the hour."""
    # 40 % for 20 min, then 40 min nothing; the second hour has 38 % for 40 min
    rows = [_row(0, "40", "%"), _row(20, "unavailable"), _row(80, "38")]

    means = means_from_states(rows, START, START + 2 * HOUR, HOUR)

    assert means == [(START + HOUR, pytest.approx(38))]
    assert means_from_states([], START, START + HOUR, HOUR) == []


async def test_hourly_means_fall_back_to_states() -> None:
    """A sensor without statistics: its recorded states give the hourly means."""
    from typing import Any

    from sunflow.planning.load_forecast import fetch_hourly_means

    class Client:
        """No statistics, a state history."""

        def __init__(self) -> None:
            self.types: list[str] = []

        async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
            self.types.append(msg_type)
            if msg_type == "recorder/statistics_during_period":
                return {}
            return {"sensor.fiat_soc": [_row(-60, "50", "%"), _row(30, "40")]}

    client = Client()

    means = await fetch_hourly_means(client, "sensor.fiat_soc", START + HOUR, days=1)

    assert client.types == ["recorder/statistics_during_period", "history/history_during_period"]
    assert means[-1] == (START, pytest.approx(45))
