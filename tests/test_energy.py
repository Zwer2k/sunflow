"""Tests for the energy balance from energy counters."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sunflow.energy import EnergySources, _dashboard_statistics, energy_balance

START = datetime(2026, 10, 20, tzinfo=UTC)


class FakeClient:
    """Answers statistics requests with fixed hourly changes per statistic."""

    def __init__(self, changes: dict[str, list[float]]) -> None:
        self.changes = changes
        self.requests: list[dict[str, Any]] = []

    async def command(self, msg_type: str, **data: Any) -> dict[str, list[dict[str, Any]]]:
        self.requests.append(data)
        return {
            statistic: [{"start": i, "change": v} for i, v in enumerate(values)]
            for statistic, values in self.changes.items()
            if statistic in data["statistic_ids"]
        }


@pytest.mark.parametrize(
    "grid",
    [
        pytest.param(
            {"type": "grid", "flow_from": [{"stat_energy_from": "sensor.import"}], "flow_to": [{"stat_energy_to": "sensor.export"}]},
            id="flow-lists",
        ),
        pytest.param({"type": "grid", "stat_energy_from": "sensor.import", "stat_energy_to": "sensor.export"}, id="flat"),
    ],
)
def test_dashboard_statistics(grid: dict[str, Any]) -> None:
    """Grid, solar and battery statistics are read from both energy dashboard formats."""
    prefs = {
        "energy_sources": [
            grid,
            {"type": "solar", "stat_energy_from": "sensor.pv"},
            {"type": "battery", "stat_energy_from": "sensor.out", "stat_energy_to": "sensor.in"},
        ]
    }

    assert _dashboard_statistics(prefs) == {
        "grid_import": ["sensor.import"],
        "grid_export": ["sensor.export"],
        "pv": ["sensor.pv"],
        "battery_discharge": ["sensor.out"],
        "battery_charge": ["sensor.in"],
    }


async def test_energy_balance_from_counters() -> None:
    """Totals sum the counters; autarky and self-consumption follow from them."""
    sources = EnergySources(
        statistics={
            "pv": ["sensor.pv_a", "sensor.pv_b"],
            "load": ["sensor.load"],
            "grid_import": ["sensor.import"],
            "grid_export": ["sensor.export"],
            "battery_charge": ["sensor.in"],
            "battery_discharge": ["sensor.out"],
        },
        inverters={"Huawei": ("sensor.pv_b", "sensor.dc")},
    )
    client = FakeClient(
        {
            "sensor.pv_a": [6000, 4000],
            "sensor.pv_b": [1800],
            "sensor.dc": [2000],
            "sensor.load": [8000, 4000],
            "sensor.import": [3000],
            "sensor.export": [1180],
            "sensor.in": [5000],
            "sensor.out": [3600],
        }
    )

    balance = await energy_balance(client, sources, START, START + timedelta(days=1))

    assert balance["totals"]["pv"] == 11800
    assert balance["consumption"] == 12000
    assert balance["consumption_origin"] == "counter"
    assert balance["autarky"] == pytest.approx(0.75)
    assert balance["self_consumption"] == pytest.approx(0.9)
    assert balance["battery_ratio"] == pytest.approx(0.72)
    assert balance["inverters"] == [{"name": "Huawei", "ac_wh": 1800, "dc_wh": 2000, "efficiency": pytest.approx(0.9)}]
    assert client.requests[0]["types"] == ["change"]


async def test_energy_balance_without_load_counter() -> None:
    """Without a consumption counter the house is what is left in the balance."""
    sources = EnergySources(statistics={"pv": ["sensor.pv"], "grid_import": ["sensor.import"], "grid_export": ["sensor.export"]})
    client = FakeClient({"sensor.pv": [10000], "sensor.import": [2000], "sensor.export": [4000]})

    balance = await energy_balance(client, sources, START, START + timedelta(days=1))

    assert balance["consumption"] == 8000
    assert balance["consumption_origin"] == "balance"
    assert balance["battery_ratio"] is None


async def test_battery_efficiency_from_counters() -> None:
    """Charge and discharge counters plus the SoC change give the round trip."""
    from sunflow.efficiency import estimate_from_counters

    hour = 3_600_000

    class Client:
        async def command(self, msg_type: str, **data: Any) -> dict[str, list[dict[str, Any]]]:
            return {
                "sensor.in": [{"start": i * hour, "change": 1000} for i in range(48)],
                "sensor.out": [{"start": i * hour, "change": 720} for i in range(48)],
                "sensor.soc": [{"start": i * hour, "mean": 50} for i in range(48)],
            }

    estimate = await estimate_from_counters(Client(), "sensor.in", "sensor.out", "sensor.soc", 10000)

    assert estimate.source == "counters"
    assert estimate.round_trip == pytest.approx(0.72)
    assert estimate.hours == 48
