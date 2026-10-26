"""Tests for price and forecast sources."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from sunflow.sources import (
    detect_price_adapter,
    forecast_solar_planes,
    price_series,
    price_sources,
    solcast_points,
    solcast_sites,
    sum_forecasts,
    wh_period_points,
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


def test_forecast_solar_planes() -> None:
    """Planes are named after their device without the generic prefix."""
    entries = [
        {"entry_id": "e1", "domain": "forecast_solar", "title": "", "state": "loaded"},
        {"entry_id": "e2", "domain": "forecast_solar", "title": "", "state": "loaded"},
        {"entry_id": "e3", "domain": "forecast_solar", "title": "", "state": "setup_error"},
        {"entry_id": "x", "domain": "met", "title": "Home", "state": "loaded"},
    ]
    devices = [
        {"config_entries": ["e1"], "name": "Solar production forecast Carport", "name_by_user": None},
        # Production instances often keep the generic device name
        {"config_entries": ["e2"], "name": "Solar production forecast", "name_by_user": None},
    ]
    entities = [
        {"entity_id": "sensor.energy_production_today", "unique_id": "e1_energy_production_today", "config_entry_id": "e1"},
        {"entity_id": "sensor.energy_production_today_remaining", "unique_id": "e1_energy_production_today_remaining", "config_entry_id": "e1"},
    ]
    states = {"sensor.energy_production_today": {"state": "4.675"}}

    planes = forecast_solar_planes(entries, devices, entities, states)

    assert [(p.source, p.ref, p.name, p.today_kwh) for p in planes] == [
        ("forecast_solar", "e1", "Carport", 4.675),
        ("forecast_solar", "e2", "Forecast.Solar 2", None),
    ]


def test_mixed_resolutions_are_summed() -> None:
    """Hourly Forecast.Solar energy and 30 minute Solcast power add up on a 30 minute grid."""
    hourly = wh_period_points({"2026-10-24T12:00:00+02:00": 1000}, timedelta(hours=1))
    half_hourly = solcast_points(
        [
            {"period_start": "2026-10-24T12:00:00+02:00", "pv_estimate": 0.5},
            {"period_start": "2026-10-24T12:30:00+02:00", "pv_estimate": 1.5},
        ],
        timedelta(minutes=30),
    )

    total = sum_forecasts([hourly, half_hourly])

    assert [p.power for p in total] == pytest.approx([1500, 2500])
    assert all(p.end - p.start == timedelta(minutes=30) for p in total)


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
    # The plane without a range takes the relative range of the other: 1000 + 500 x 1.0/1.5
    assert total.power10 == pytest.approx(1000 + 500 * 1.0 / 1.5)
    assert total.power90 == pytest.approx(2000 + 500 * 2.0 / 1.5)
    assert total.end - total.start == period


def test_forecast_solar_energy_belongs_to_interval_before_timestamp() -> None:
    """watt_hours_period ends at its timestamp: 160 Wh at 09:00 were produced 08:00-09:00."""
    from sunflow.sources import period_end_points

    points = period_end_points(
        {
            "2026-10-25T07:00:00+01:00": 0,
            "2026-10-25T08:00:00+01:00": 49,
            "2026-10-25T09:00:00+01:00": 160,
            "2026-10-25T09:15:20+01:00": 30,
        }
    )

    by_start = {p.start.strftime("%H:%M"): round(p.power) for p in points}
    assert by_start["07:00"] == 49 and by_start["07:30"] == 49
    assert by_start["08:00"] == 160 and by_start["08:30"] == 160
    # The 15 min 20 s tail lies within the 09:00 cell: 30 Wh in half an hour
    assert by_start["09:00"] == 60
    assert sum(p.power for p in points) / 2 == pytest.approx(239)
