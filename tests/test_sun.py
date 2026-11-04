"""Tests for the sun position."""

from datetime import UTC, datetime

import pytest
from sunflow.planning.sun import nights, sun_position

# Kassel, Germany
LAT, LON = 51.3, 9.5


@pytest.mark.parametrize(
    ("moment", "elevation", "azimuth"),
    [
        # Summer solstice, solar noon at about 11:20 UTC
        pytest.param(datetime(2026, 6, 21, 11, 20, tzinfo=UTC), 62.1, 180, id="summer-noon"),
        # Winter solstice, solar noon
        pytest.param(datetime(2026, 12, 21, 11, 20, tzinfo=UTC), 15.2, 180, id="winter-noon"),
        # Equinox, solar time about 7:30: low in the east-south-east
        pytest.param(datetime(2026, 3, 20, 7, 0, tzinfo=UTC), 13.8, 108, id="equinox-morning"),
    ],
)
def test_sun_position(moment: datetime, elevation: float, azimuth: float) -> None:
    """Within a degree of published solar tables."""
    found_elevation, found_azimuth = sun_position(moment, LAT, LON)

    assert found_elevation == pytest.approx(elevation, abs=1.0)
    assert found_azimuth == pytest.approx(azimuth, abs=2.0)


def test_nights_of_a_winter_day() -> None:
    """Sunrise near 7:20 and sunset near 15:10 UTC in late December."""
    start = datetime(2026, 12, 21, tzinfo=UTC)

    dark = nights(start, start.replace(hour=23, minute=59), LAT, LON)

    assert dark[0][0] == start
    assert dark[0][1].hour == 7
    assert dark[1][0].hour == 15
