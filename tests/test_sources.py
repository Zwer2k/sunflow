"""Tests for price and forecast sources."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from sunflow.sources import (
    detect_price_adapter,
    price_series,
    price_sources,
    solcast_points,
    solcast_sites,
    sum_forecasts,
)

BERLIN = ZoneInfo("Europe/Berlin")

# Shape of the Octopus Germany price sensor for an Octopus Go tariff
OCTOPUS_GO = {
    "state": "0.326179",
    "attributes": {
        "unit_of_measurement": "€/kWh",
        "friendly_name": "Octopus Electricity Price",
        "timeslots": [
            {"name": "GO", "rate": "22.6219", "activation_rules": [{"from_time": "00:00:00", "to_time": "05:00:00"}]},
            {"name": "STANDARD", "rate": "32.6179", "activation_rules": [{"from_time": "05:00:00", "to_time": "00:00:00"}]},
        ],
    },
}


def test_timeslot_prices() -> None:
    """Time-of-use slots expand to 15 minute prices in local time."""
    start = datetime(2026, 10, 24, 22, 0, tzinfo=BERLIN)

    points = price_series({"entity_id": "sensor.p", **OCTOPUS_GO}, start, 24, BERLIN)

    assert len(points) == 96
    by_hour = {p.start.hour: p.price for p in points if p.start.minute == 0}
    assert by_hour[23] == pytest.approx(0.326179)
    assert by_hour[0] == pytest.approx(0.226219)
    assert by_hour[4] == pytest.approx(0.226219)
    assert by_hour[5] == pytest.approx(0.326179)


def test_timeslot_prices_across_dst_change() -> None:
    """Local wall-clock rules stay correct when clocks go back (25 hour day)."""
    start = datetime(2026, 10, 25, 0, 0, tzinfo=BERLIN)

    points = price_series({"entity_id": "sensor.p", **OCTOPUS_GO}, start, 6, BERLIN)

    cheap = [p for p in points if p.price < 0.3]
    # 00:00-05:00 local is six real hours on the night the clock goes back
    assert len(cheap) == 6 * 4


def test_nordpool_and_epex() -> None:
    """Nord Pool and EPEX Spot attribute formats are read and clipped to the horizon."""
    start = datetime(2026, 10, 24, 12, 0, tzinfo=BERLIN)
    hour = timedelta(hours=1)
    nordpool = {
        "state": "25.0",
        "attributes": {
            "unit_of_measurement": "ct/kWh",
            "raw_today": [
                {"start": (start + i * hour).isoformat(), "end": (start + (i + 1) * hour).isoformat(), "value": 20 + i}
                for i in range(-2, 3)
            ],
            "raw_tomorrow": [],
        },
    }
    epex = {
        "state": "0.2",
        "attributes": {
            "unit_of_measurement": "€/kWh",
            "data": [{"start_time": start.isoformat(), "end_time": (start + hour).isoformat(), "price_per_kwh": 0.21}],
        },
    }

    nordpool_points = price_series(nordpool, start, 2, BERLIN)
    epex_points = price_series(epex, start, 2, BERLIN)

    assert [p.price for p in nordpool_points] == pytest.approx([0.20, 0.21])
    assert [p.price for p in epex_points] == pytest.approx([0.21])


def test_fixed_price() -> None:
    """A plain price sensor yields one interval with its current value."""
    start = datetime(2026, 10, 24, 12, 0, tzinfo=BERLIN)

    (point,) = price_series({"state": "30", "attributes": {"unit_of_measurement": "ct/kWh"}}, start, 48, BERLIN)

    assert point.price == pytest.approx(0.30)
    assert point.end - point.start == timedelta(hours=48)


def test_price_sources() -> None:
    """Only sensors with a price per kWh unit are offered."""
    states: list[dict[str, Any]] = [
        {"entity_id": "sensor.octopus", **OCTOPUS_GO},
        {"entity_id": "sensor.cost", "state": "3", "attributes": {"unit_of_measurement": "€"}},
        {"entity_id": "sensor.spot", "state": "unknown", "attributes": {"unit_of_measurement": "ct/kWh"}},
    ]

    sources = {s.entity_id: s for s in price_sources(states)}

    assert set(sources) == {"sensor.octopus", "sensor.spot"}
    assert sources["sensor.octopus"].adapter == "timeslots"
    assert sources["sensor.octopus"].current == pytest.approx(0.326179)
    assert sources["sensor.spot"].current is None
    assert detect_price_adapter({}) == "fixed"


def test_solcast() -> None:
    """Solcast sites are found and their kW periods summed per plant in W."""
    states = [
        {"entity_id": "sensor.site_a", "state": "10", "attributes": {"resource_id": "a", "name": "Nord-Ost", "capacity": 6}},
        {"entity_id": "sensor.other", "state": "1", "attributes": {"resource_id": "x"}},
    ]
    sites = solcast_sites(states, {"sensor.site_a"})
    assert [(s.ref, s.name, s.capacity_kw) for s in sites] == [("a", "Nord-Ost", 6)]

    period = timedelta(minutes=30)
    a = solcast_points([{"period_start": "2026-10-24T12:00:00+02:00", "pv_estimate": 1.5, "pv_estimate10": 1.0, "pv_estimate90": 2.0}], period)
    b = solcast_points([{"period_start": "2026-10-24T12:00:00+02:00", "pv_estimate": 0.5}], period)

    (total,) = sum_forecasts([a, b])
    assert total.power == pytest.approx(2000)
    assert total.power10 == pytest.approx(1000)
    assert total.end - total.start == period
