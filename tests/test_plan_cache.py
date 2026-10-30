"""Tests for the cache of database queries used by planning."""

from typing import Any

import pytest
from sunflow.ha import HACommandError, RecorderUnavailable
from sunflow.planning import plan


async def test_cached_reuses_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Within the ttl nothing is queried; after it a failure returns the last known value."""
    monkeypatch.setattr(plan, "_query_cache", {})
    calls: list[Any] = []

    async def fetch() -> list[int]:
        calls.append(1)
        return [1, 2]

    async def failing() -> list[int]:
        raise RecorderUnavailable("recorder_unavailable: paused")

    assert await plan.cached(("hourly", "sensor.load"), plan.HOURLY_TTL, fetch) == [1, 2]
    assert await plan.cached(("hourly", "sensor.load"), plan.HOURLY_TTL, fetch) == [1, 2]
    assert len(calls) == 1

    monkeypatch.setattr(plan, "HOURLY_TTL", plan.timedelta(0))
    assert await plan.cached(("hourly", "sensor.load"), plan.HOURLY_TTL, failing) == [1, 2]
    with pytest.raises(HACommandError):
        await plan.cached(("hourly", "sensor.other"), plan.HOURLY_TTL, failing)
