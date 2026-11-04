"""Tests for the plausibility of learning sources."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.analysis.plausibility import check, kind_of, to_canonical

START = datetime(2026, 11, 1, tzinfo=UTC)
END = START + timedelta(hours=24)


def _every(minutes: int, values: list[float | None], start: datetime = START) -> list[tuple[datetime, float | None]]:
    return [(start + timedelta(minutes=minutes * i), v) for i, v in enumerate(values)]


def _temperatures() -> list[float]:
    """A day of outdoor temperature, changing by 0.1 K every 10 minutes."""
    return [round(5 + 0.1 * (i % 40 if i % 80 < 40 else 40 - i % 40), 1) for i in range(144)]


def test_clean_series_is_kept_whole() -> None:
    """Every value plausible: the whole day counts."""
    quality = check(_every(10, _temperatures()), "temperature", START, END)

    assert quality.share == pytest.approx(1.0)
    assert (quality.spikes, quality.out_of_range, quality.gaps) == (0, 0, 0)


def test_spike_and_impossible_value_are_left_out() -> None:
    """A jump of 20 K for one reading and -100 °C are left out; the last good value holds meanwhile."""
    values = _temperatures()
    values[50] = 27.0
    values[90] = -100.0

    quality = check(_every(10, values), "temperature", START, END)

    assert (quality.spikes, quality.out_of_range) == (1, 1)
    assert quality.share == pytest.approx(1.0)
    assert 27.0 not in {s.value for s in quality.segments}


def test_long_unavailability_is_a_gap_short_one_is_bridged() -> None:
    """20 minutes unavailable keep the last value; 3 hours are missing, not filled."""
    values: list[float | None] = _temperatures()
    values[10:12] = [None, None]
    values[60:78] = [None] * 18

    quality = check(_every(10, values), "temperature", START, END)

    assert quality.gaps == 1
    assert quality.gap_hours == pytest.approx(3.0)
    assert quality.share == pytest.approx(21 / 24)


def test_hanging_sensor() -> None:
    """The same temperature for 12 hours is a hanging sensor."""
    changes = [(START, 5.0), (START + timedelta(hours=2), 5.3)] + [
        (START + timedelta(hours=14, minutes=30 * i), 5.4 + 0.1 * (i % 2)) for i in range(20)
    ]

    quality = check(changes, "temperature", START, END)

    assert quality.stuck_hours == pytest.approx(12.0)
    assert quality.share == pytest.approx(12 / 24)


@pytest.mark.parametrize(
    ("value", "stuck"),
    [
        pytest.param(0.0, 0.0, id="dark-night"),
        pytest.param(850.0, 14.0, id="brightness-hangs"),
    ],
)
def test_brightness_constant_only_in_the_dark(value: float, stuck: float) -> None:
    """Fourteen hours of darkness are normal; fourteen hours of the same daylight are not."""
    changes = [(START, value), (START + timedelta(hours=14), 1200.0), (START + timedelta(hours=14, minutes=5), 900.0)]

    assert check(changes, "illuminance", START, START + timedelta(hours=15)).stuck_hours == pytest.approx(stuck)


def test_units_and_kinds() -> None:
    """Fahrenheit and Kelvin become °C; brightness in W/m² is irradiance, else illuminance."""
    assert to_canonical(50.0, "°F") == pytest.approx(10.0)
    assert to_canonical(283.15, "K") == pytest.approx(10.0)
    assert kind_of("irradiance", "W/m²") == "irradiance"
    assert kind_of("irradiance", "lx") == "illuminance"
    assert kind_of("outdoor_temperature", "°C") == "temperature"


def test_home_assistant_offline_is_no_hanging_sensor() -> None:
    """While Home Assistant did not record, the last value seems to hold; that time is left out, not judged."""
    values = _temperatures()
    changes = _every(10, values[:60]) + _every(10, values[60:], start=START + timedelta(hours=14))
    offline = [(START + timedelta(minutes=590), START + timedelta(hours=14))]

    quality = check(changes, "temperature", START, START + timedelta(hours=28), offline)

    assert quality.stuck_hours == 0
    assert quality.offline_hours == pytest.approx(14 - 590 / 60)
    assert quality.share == pytest.approx(1.0)


def test_quiet_intervals_of_all_series() -> None:
    """Only where no series changed for an hour."""
    from sunflow.analysis.plausibility import quiet_intervals

    a = [(START, 1.0), (START + timedelta(hours=3), 2.0)]
    b = [(START + timedelta(minutes=30), 1.0), (START + timedelta(hours=1), 2.0)]

    assert quiet_intervals([a, b], START, START + timedelta(hours=4)) == [
        (START + timedelta(hours=1), START + timedelta(hours=3)),
        (START + timedelta(hours=3), START + timedelta(hours=4)),
    ]
