"""Tests for the measured history."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sunflow.history import Series, fetch_history

START = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)


class FakeClient:
    """Returns 5 minute statistics."""

    def __init__(self, rows: dict[str, list[tuple[int, float]]]) -> None:
        """Rows per statistic: (minutes after START, mean)."""
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def command(self, msg_type: str, **data: Any) -> dict[str, list[dict[str, Any]]]:
        """Answer recorder/statistics_during_period."""
        self.calls.append(data)
        return {
            sid: [
                {"start": (START + timedelta(minutes=m)).timestamp() * 1000, "mean": v}
                for m, v in rows
            ]
            for sid, rows in self.rows.items()
        }


async def test_buckets_signs_and_pv_sum() -> None:
    """5 minute means are averaged per 15 minutes; grid sign flipped; PV plants summed."""
    client = FakeClient(
        {
            "sensor.soc": [(0, 50), (5, 51), (10, 52), (15, 60)],
            "sensor.pcc": [(0, -1000), (5, -1200), (10, -1400)],
            "sensor.pv_dc": [(0, 2000), (5, 2000), (10, 2000)],
            "sensor.huawei": [(0, 300), (5, 600), (10, 900)],
        }
    )
    series = [
        Series(key="soc_wh", statistic_id="sensor.soc", factor=200),
        Series(key="grid_w", statistic_id="sensor.pcc", factor=-1),
        Series(key="pv_w", statistic_id="sensor.pv_dc", factor=1),
        Series(key="pv_w", statistic_id="sensor.huawei", factor=1),
    ]

    points = await fetch_history(client, series, START, START + timedelta(minutes=30), timedelta(minutes=15))

    assert client.calls[0]["period"] == "5minute"
    first, second = points
    assert first["soc_wh"] == pytest.approx(51 * 200)
    assert first["grid_w"] == pytest.approx(1200)
    assert first["pv_w"] == pytest.approx(2000 + 600)
    assert first["load_w"] is None
    assert second["soc_wh"] == pytest.approx(60 * 200)
    assert second["pv_w"] is None


async def test_long_ranges_use_hourly_statistics() -> None:
    """Beyond the short-term retention the hourly statistics are queried."""
    client = FakeClient({})
    series = [Series(key="soc_wh", statistic_id="sensor.soc", factor=1)]

    assert await fetch_history(client, series, START - timedelta(days=11), START, timedelta(hours=1)) == []
    assert client.calls[0]["period"] == "hour"
