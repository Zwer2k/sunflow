"""Position of the sun (NOAA approximation, about 0.5° accurate) and the hours of daylight."""

from datetime import UTC, datetime, timedelta
import math


def sun_position(moment: datetime, latitude: float, longitude: float) -> tuple[float, float]:
    """Elevation and azimuth in degrees; azimuth clockwise from north (90 = east)."""
    moment = moment.astimezone(UTC)
    start = moment.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    hours = moment.hour + moment.minute / 60 + moment.second / 3600
    gamma = 2 * math.pi / 365 * ((moment - start).days + (hours - 12) / 24)
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    solar_minutes = hours * 60 + equation_of_time + 4 * longitude
    hour_angle = math.radians(solar_minutes / 4 - 180)
    lat = math.radians(latitude)
    cos_zenith = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(hour_angle)
    zenith = math.acos(max(-1.0, min(1.0, cos_zenith)))
    azimuth = math.degrees(
        math.atan2(
            math.sin(hour_angle),
            math.cos(hour_angle) * math.sin(lat) - math.tan(declination) * math.cos(lat),
        )
    )
    return 90 - math.degrees(zenith), (azimuth + 180) % 360


def nights(
    start: datetime, end: datetime, latitude: float, longitude: float, min_elevation: float = 0.0
) -> list[tuple[datetime, datetime]]:
    """Intervals with the sun below min_elevation, to the nearest 10 minutes."""
    step = timedelta(minutes=10)
    result: list[tuple[datetime, datetime]] = []
    begin = None
    moment = start
    while moment < end:
        dark = sun_position(moment, latitude, longitude)[0] < min_elevation
        if dark and begin is None:
            begin = moment
        elif not dark and begin is not None:
            result.append((begin, moment))
            begin = None
        moment += step
    if begin is not None:
        result.append((begin, end))
    return result
