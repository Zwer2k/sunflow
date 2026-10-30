"""Tests for learning the typical departure of the car."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sunflow.planning.habits import departures_from

BERLIN = ZoneInfo("Europe/Berlin")


def _session(plug: datetime, unplug: datetime) -> list[dict[str, object]]:
    return [{"s": "on", "lu": plug.timestamp()}, {"s": "off", "lu": unplug.timestamp()}]


def test_typical_departure_per_day_type() -> None:
    """Overnight stays on workdays end around 07:30; short re-plugging does not count."""
    states = []
    monday = datetime(2026, 10, 12, 18, 0, tzinfo=BERLIN)
    for day, minute in zip(range(4), (20, 30, 35, 40), strict=True):  # Mon-Thu evening to next morning
        evening = monday + timedelta(days=day)
        states += _session(evening, evening.replace(hour=7, minute=minute) + timedelta(days=1))
    # A short top-up in the afternoon
    states += _session(monday.replace(hour=14), monday.replace(hour=14, minute=30))

    departure = departures_from(states, BERLIN)

    assert departure.workday == time(7, 32)
    assert departure.weekend is None  # no weekend sessions
    assert departure.sessions == 4
    # Friday evening: next workday departure is Monday morning, the weekend has none learned
    friday = datetime(2026, 10, 23, 20, 0, tzinfo=BERLIN)
    assert departure.next_after(friday, BERLIN) == datetime(2026, 10, 26, 7, 32, tzinfo=BERLIN)


def test_too_few_sessions_learn_nothing() -> None:
    """Two sessions are no habit."""
    monday = datetime(2026, 10, 12, 18, 0, tzinfo=BERLIN)
    states = _session(monday, monday + timedelta(hours=13)) + _session(monday + timedelta(days=1), monday + timedelta(days=1, hours=13))

    departure = departures_from(states, BERLIN)

    assert departure.workday is None
    assert departure.next_after(monday, BERLIN) is None


def test_consumer_usage_from_power_means() -> None:
    """An e-bike charger: 2 h at 250 W each day, 1 W standby otherwise."""
    from sunflow.planning.habits import usage_from

    start = datetime(2026, 10, 1, tzinfo=BERLIN)
    means = [
        (start + timedelta(minutes=5 * i), 250.0 if (i % 288) < 24 else 1.0)
        for i in range(288 * 5)
    ]

    usage = usage_from(means)

    assert usage.days == 5
    assert usage.running_w == 250
    # 2 h x 250 W plus 22 h x 1 W standby
    assert round(usage.daily_wh) == 500 + 22
    assert usage_from(means[:288 * 2]) is None  # two days are too few
