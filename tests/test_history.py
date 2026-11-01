"""Tests for the measured history."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sunflow.analysis.history import Series, fetch_history

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


def test_efficiency_estimate() -> None:
    """A battery cycling with 95 % per direction is recognized; implausible values are flagged."""
    from sunflow.analysis.efficiency import estimate

    eta = 0.95
    capacity = 20000.0
    # Charge 200 kWh (10 cycles), discharge 180 kWh measured at the terminals over 1 h steps
    charged, discharged = 200000.0, 180000.0
    stored = eta * charged - discharged / eta
    power = [charged] + [-discharged]
    result = estimate(power, 1.0, 50, 50 + stored / capacity * 100, capacity)
    # Two cycles are not enough: standby losses and SoC errors still dominate
    assert not estimate([40000.0, -36000.0], 1.0, 50, 50, capacity).reliable

    assert result.efficiency == pytest.approx(eta)
    assert result.round_trip == pytest.approx(eta * eta)
    assert result.reliable and result.plausible

    lossy = estimate([25700.0, -13800.0], 1.0, 40, 40 + 4700 / capacity * 100, capacity)
    # Measured on Sofar BTS modules: high but real DC/DC losses
    assert lossy.efficiency == pytest.approx(0.829, abs=0.002)
    assert lossy.plausible
    assert not lossy.reliable

    assert not estimate([10000.0, -2000.0], 1.0, 40, 40, capacity).plausible


async def test_consumers_one_by_one_and_summed() -> None:
    """Each controllable consumer gets its own line; loadpoint_w stays their sum."""
    client = FakeClient({"sensor.wallbox": [(0, 3000), (5, 3000)], "sensor.ebike": [(0, 100), (5, 200)]})
    series = [
        Series(key="loadpoint_w", statistic_id="sensor.wallbox", factor=1, device_id="wallbox"),
        Series(key="loadpoint_w", statistic_id="sensor.ebike", factor=1, device_id="ebike"),
    ]

    (point,) = await fetch_history(client, series, START, START + timedelta(minutes=15), timedelta(minutes=15))

    assert point["consumers"] == {"wallbox": pytest.approx(3000), "ebike": pytest.approx(150)}
    assert point["loadpoint_w"] == pytest.approx(3150)


def _point(minutes: int, loadpoint: float | None, consumers: dict[str, float]) -> dict[str, Any]:
    start = START + timedelta(minutes=minutes)
    return {"start": start.isoformat(), "end": (start + timedelta(minutes=15)).isoformat(), "load_w": 4400.0, "loadpoint_w": loadpoint, "consumers": consumers}


def _row(minutes: int, total: float, consumers: dict[str, float] | None = None) -> dict[str, Any]:
    return {"start": (START + timedelta(minutes=minutes)).isoformat(), "actual_loadpoint_w": total, "actual_consumers": consumers or {}}


def test_consumers_without_statistics_come_from_own_samples() -> None:
    """A wallbox excluded from the recorder: Sunflow's samples keep it out of the house consumption."""
    from sunflow.analysis.history import fill_from_shadow

    points = [
        # Statistics of the heating rod only; the old slot has just the sum
        _point(0, 0.0, {"ww": 0.0, "bike": 0.0}),
        _point(15, None, {}),
        _point(30, 0.0, {"ww": 0.0}),
    ]
    rows = [_row(0, 4000.0), _row(15, 4100.0, {"wb": 4000.0, "bike": 100.0}), _row(30, 0.0, {})]

    fill_from_shadow(points, rows, {"wb", "ww", "bike"})

    assert (points[0]["loadpoint_w"], points[0]["consumers"]["wb"]) == (4000.0, 4000.0)
    assert (points[1]["loadpoint_w"], points[1]["consumers"]) == (4100.0, {"wb": 4000.0, "bike": 100.0, "ww": 0.0})
    # Two consumers without values: the sum cannot be split
    assert "wb" not in points[2]["consumers"]


def test_unmeasured_consumers_leave_the_house_load() -> None:
    """Hours the own samples saw more consumer power than the statistics are separated."""
    from sunflow.planning.load_forecast import subtract_loads, unmeasured_consumers

    hours = [START + timedelta(hours=h) for h in range(3)]
    history = [(h, 4500.0) for h in hours]
    statistics = [[(hours[0], 100.0), (hours[1], 100.0)]]
    own = [(hours[0], 4100.0), (hours[1], 100.0)]

    base, _ = subtract_loads(history, [*statistics, unmeasured_consumers(own, statistics)])

    assert [round(w) for _, w in base] == [400, 4400, 4500]
