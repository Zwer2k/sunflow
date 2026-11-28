"""Prices beyond those published, so the plan can look further ahead than the day-ahead market.

Dynamic tariffs publish tomorrow's prices around noon. Further on, the price is
estimated per local hour of the day: from the mean of the price sensor over the
last weeks, else by repeating the last known day. Estimated prices only shape the
far end of the plan; it is replanned as soon as the real prices are known.
"""

from datetime import datetime, timedelta, tzinfo
from statistics import fmean

from .sources import SLOT, PricePoint

# Days of price history from which their mean per hour is used
MIN_HISTORY_DAYS = 7
# A source with only its current price: from this spread over the day (€/kWh) its usual
# course is planned with instead of a constant price
VARYING_SPREAD = 0.01


def hourly_profile(history: list[tuple[datetime, float]], tz: tzinfo) -> list[float] | None:
    """Mean price per local hour of the day; None with too few days or hours missing."""
    by_hour: list[list[float]] = [[] for _ in range(24)]
    days = set()
    for moment, price in history:
        local = moment.astimezone(tz)
        by_hour[local.hour].append(price)
        days.add(local.date())
    if len(days) < MIN_HISTORY_DAYS or not all(by_hour):
        return None
    return [fmean(prices) for prices in by_hour]


def last_day_profile(known: list[PricePoint], tz: tzinfo) -> list[float] | None:
    """The last 24 known hours per local hour; None if they do not cover a whole day."""
    if not known:
        return None
    end = max(p.end for p in known)
    by_hour: list[list[float]] = [[] for _ in range(24)]
    for point in known:
        if point.start >= end - timedelta(hours=24):
            by_hour[point.start.astimezone(tz).hour].append(point.price)
    return [fmean(prices) for prices in by_hour] if all(by_hour) else None


def extend_prices(
    known: list[PricePoint], history: list[tuple[datetime, float]], until: datetime, tz: tzinfo
) -> list[PricePoint]:
    """The known prices, followed by estimated ones per 15 minutes until the end of the horizon."""
    if not known:
        return known
    end = max(p.end for p in known)
    profile = hourly_profile(history, tz) or last_day_profile(known, tz)
    if profile is None or end >= until:
        return known
    estimated = []
    moment = end
    while moment < until:
        price = profile[moment.astimezone(tz).hour]
        estimated.append(PricePoint(start=moment, end=moment + SLOT, price=price, estimated=True))
        moment += SLOT
    return [*known, *estimated]


def from_history(
    current: float | None, history: list[tuple[datetime, float]], start: datetime, until: datetime, tz: tzinfo
) -> list[PricePoint]:
    """A source without a schedule: the current price now, then the usual course of each hour.

    Constant when the price does not vary over the day (a fixed tariff) or too little history
    is there; current None: the source has no reliable price now (all from the history).
    """
    profile = hourly_profile(history, tz)
    if profile is None or max(profile) - min(profile) < VARYING_SPREAD:
        if current is None:
            return []
        return [PricePoint(start=start, end=until, price=current)]
    known = [PricePoint(start=start, end=start + SLOT, price=current)] if current is not None else []
    estimated = []
    moment = start + SLOT if known else start
    while moment < until:
        estimated.append(PricePoint(start=moment, end=moment + SLOT, price=profile[moment.astimezone(tz).hour], estimated=True))
        moment += SLOT
    return [*known, *estimated]
