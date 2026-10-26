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


def test_split_losses_separates_standby() -> None:
    """Days with different throughput separate 85 % conversion per direction and 60 W standby."""
    from sunflow.efficiency import split_losses

    eta, standby = 0.85, 60.0
    days = []
    for charged in (5000, 12000, 8000, 15000, 3000, 10000, 7000, 14000):
        discharged = charged * 0.6
        days.append((charged, discharged, eta * charged - discharged / eta - standby * 24))

    found_eta, found_standby = split_losses(days)

    assert found_eta == pytest.approx(eta, abs=0.002)
    assert found_standby == pytest.approx(standby, abs=2)


def test_split_losses_needs_enough_varied_days() -> None:
    """Too few days or always the same cycle give no split."""
    from sunflow.efficiency import split_losses

    assert split_losses([(10000, 6000, -500)] * 3) is None
    assert split_losses([(10000, 6000, -500)] * 10) is None


def _hours_of(day: datetime) -> int:
    """23, 24 or 25: the tests run on any date, also around daylight saving time changes."""
    following = (day + timedelta(days=1)).replace(hour=0)
    return round((following.timestamp() - day.timestamp()) / 3600)


async def test_efficiency_history_per_day() -> None:
    """Daily battery round trip (SoC corrected) and inverter AC/DC; small or incomplete days stay empty."""
    from zoneinfo import ZoneInfo

    from sunflow.energy import BatteryCounters, efficiency_history

    tz = ZoneInfo("Europe/Berlin")
    end = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [end - timedelta(days=n) for n in (3, 2, 1)]

    def hourly(per_day: list[float], skip: set[tuple[int, int]] = frozenset()) -> list[dict[str, Any]]:
        """Spread daily energy over the hours; skip (day, hour) to simulate a gap."""
        rows = []
        for index, (day, total) in enumerate(zip(days, per_day, strict=True)):
            hours = _hours_of(day)
            for hour in range(hours):
                if (index, hour) not in skip:
                    rows.append({"start": day.timestamp() * 1000 + hour * 3_600_000, "change": total / hours})
        return rows

    soc = [
        {"start": (day + timedelta(hours=h)).timestamp() * 1000, "mean": 50}
        for day in [*days, end]
        for h in (-1, 0)
    ]

    class Client:
        async def command(self, msg_type: str, **data: Any) -> dict[str, list[dict[str, Any]]]:
            return {
                # Day 1: 10 kWh in, 7.2 out at unchanged SoC -> 72 %; day 2: only 1 kWh; day 3: a gap
                "in": hourly([10000, 1000, 10000], skip={(2, 17)}),
                "out": hourly([7200, 900, 7200]),
                "soc": soc,
                "ac": hourly([1800, 200, 1800]),
                "dc": hourly([2000, 250, 2000]),
            }

    battery = BatteryCounters(name="Sofar", charge="in", discharge="out", soc="soc", capacity_wh=20000)
    sofar, huawei = await efficiency_history(Client(), [battery], {"Huawei": ("ac", "dc")}, 3, tz)

    assert sofar["points"][0]["value"] == pytest.approx(0.72)
    # 1 kWh is below 0.2 cycles and the following day has a gap: both stay empty
    assert [p["value"] for p in sofar["points"][1:]] == [None, None]
    assert huawei["points"][0]["value"] == pytest.approx(0.9)
    # 250 Wh DC alone is too little; together with the next day it counts
    assert huawei["points"][1]["days"] == 2
    assert huawei["points"][1]["value"] == pytest.approx(2000 / 2250)


async def test_efficiency_history_merges_days_with_little_energy() -> None:
    """Winter days below the minimum are combined until there is enough energy for a value."""
    from zoneinfo import ZoneInfo

    from sunflow.energy import BatteryCounters, efficiency_history

    tz = ZoneInfo("Europe/Berlin")
    end = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [end - timedelta(days=n) for n in (3, 2, 1)]

    def hourly(per_day: list[float]) -> list[dict[str, Any]]:
        return [
            {"start": day.timestamp() * 1000 + hour * 3_600_000, "change": total / _hours_of(day)}
            for day, total in zip(days, per_day, strict=True)
            for hour in range(_hours_of(day))
        ]

    soc = [{"start": (day + timedelta(hours=h)).timestamp() * 1000, "mean": 50} for day in [*days, end] for h in (-1, 0)]

    class Client:
        async def command(self, msg_type: str, **data: Any) -> dict[str, list[dict[str, Any]]]:
            return {"in": hourly([2500, 2500, 1000]), "out": hourly([2000, 2000, 800]), "soc": soc}

    battery = BatteryCounters(name="Sofar", charge="in", discharge="out", soc="soc", capacity_wh=20000)
    (sofar,) = await efficiency_history(Client(), [battery], {}, 3, tz)

    merged, last = sofar["points"]
    assert merged["date"] == days[0].date().isoformat()
    assert merged["days"] == 2
    assert merged["value"] == pytest.approx(0.8)
    assert merged["energy_wh"] == pytest.approx(5000)
    # Still too little on its own; the window is not closed yet
    assert last["value"] is None
