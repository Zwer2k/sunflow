"""Tests for the daily power of the optimizers."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest
from sunflow.analysis.optimizer_history import day_step, optimizer_day
from sunflow.api.optimizers import get_optimizer_history
from sunflow.devices.optimizers import Optimizer

START = datetime(2026, 11, 20, 23, tzinfo=UTC)
STEP = timedelta(minutes=5)


class FakeClient:
    """Answers statistics for some entities and recorded states for the others."""

    def __init__(self, statistics: dict[str, list[tuple[int, float | None]]], states: dict[str, list[tuple[int, str]]]) -> None:
        """Rows as (minutes after START, value)."""
        self.statistics = statistics
        self.states = states
        self.calls: list[str] = []

    async def command(self, msg_type: str, **data: Any) -> dict[str, list[dict[str, Any]]]:
        """Answer recorder/statistics_during_period and history/history_during_period."""
        self.calls.append(msg_type)
        if msg_type == "recorder/statistics_during_period":
            return {
                sid: [{"start": (START + timedelta(minutes=m)).timestamp() * 1000, "mean": v} for m, v in rows]
                for sid, rows in self.statistics.items()
            }
        return {
            eid: [{"s": v, "lu": (START + timedelta(minutes=m)).timestamp()} for m, v in rows]
            for eid, rows in self.states.items()
            if eid in data["entity_ids"]
        }


def _optimizer(name: str) -> Optimizer:
    return Optimizer(id=name, source="tigo", name=name, label=name, parent=None, entities={"power": f"sensor.{name}_power"})


async def test_power_per_slot_and_energy() -> None:
    """Slots without a row stay empty; negative means count as 0; energy from the measured slots."""
    client = FakeClient({"sensor.a1_power": [(0, 120.0), (10, -3.0), (15, None), (30, 999.0)]}, {})
    (day,) = await optimizer_day(client, [_optimizer("a1")], START, START + timedelta(minutes=30), STEP)
    assert day == {"id": "a1", "power": [120.0, None, 0.0, None, None, None], "energy_wh": 10.0}


async def test_states_without_statistics() -> None:
    """A sensor without state_class is averaged from its recorded states."""
    client = FakeClient({}, {"sensor.a2_power": [(0, "60"), (5, "unavailable")]})
    (day,) = await optimizer_day(client, [_optimizer("a2")], START, START + timedelta(minutes=10), STEP)
    assert day == {"id": "a2", "power": [60.0, None], "energy_wh": 5.0}
    assert client.calls == ["recorder/statistics_during_period", "history/history_during_period"]


@pytest.mark.parametrize(
    ("age", "step"),
    [
        pytest.param(timedelta(days=1), timedelta(minutes=5), id="recent"),
        pytest.param(timedelta(days=11), timedelta(hours=1), id="older_than_short_term_statistics"),
    ],
)
def test_day_step(age: timedelta, step: timedelta) -> None:
    """Hourly once the 5 minute statistics are purged."""
    now = datetime.now(UTC)
    assert day_step(now - age, now) == step


class ConfigClient:
    """Answers get_config only."""

    async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
        """The time zone of Home Assistant."""
        assert msg_type == "get_config"
        return {"time_zone": "Europe/Berlin"}


async def test_a_day_in_the_future_is_refused() -> None:
    """Nothing is measured there yet."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ha=ConfigClient())))
    with pytest.raises(HTTPException) as raised:
        await get_optimizer_history(request, "tigo:A", datetime.now(UTC).date() + timedelta(days=2))
    assert raised.value.status_code == 422
