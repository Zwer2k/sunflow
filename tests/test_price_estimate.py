"""Tests for estimating prices beyond the published ones."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.planning.price_estimate import extend_prices
from sunflow.planning.sources import PricePoint

START = datetime(2026, 11, 2, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _known(hours: int, price: float = 0.30) -> list[PricePoint]:
    """Hourly prices, cheap at night (00-05)."""
    return [
        PricePoint(start=START + h * HOUR, end=START + (h + 1) * HOUR, price=0.20 if (h % 24) < 6 else price)
        for h in range(hours)
    ]


def test_estimates_from_the_usual_price_of_each_hour() -> None:
    """Two weeks of history: its mean per hour fills the hours after the published ones."""
    history = [(START - timedelta(days=14) + h * HOUR, 0.10 + (h % 24) / 100) for h in range(14 * 24)]

    prices = extend_prices(_known(36), history, START + 48 * HOUR, UTC)

    estimated = [p for p in prices if p.estimated]
    assert estimated[0].start == START + 36 * HOUR
    assert estimated[-1].end == START + 48 * HOUR
    # 12:00 next day: the usual 12 o'clock price
    assert estimated[0].price == pytest.approx(0.22)
    assert all(not p.estimated for p in prices[:36])


def test_without_history_the_last_day_repeats() -> None:
    """Too little history: the last 24 published hours are repeated."""
    prices = extend_prices(_known(36), [], START + 60 * HOUR, UTC)

    by_start = {p.start: p.price for p in prices if p.estimated}
    assert by_start[START + 48 * HOUR] == pytest.approx(0.20)
    assert by_start[START + 54 * HOUR] == pytest.approx(0.30)


@pytest.mark.parametrize(
    "known",
    [
        pytest.param([], id="no-prices"),
        # Less than a day published and no history: nothing to repeat
        pytest.param(_known(12), id="half-a-day"),
    ],
)
def test_nothing_estimated_without_a_basis(known: list[PricePoint]) -> None:
    """Without history or a whole known day the plan stays as long as the published prices."""
    assert extend_prices(known, [], START + 48 * HOUR, UTC) == known


def test_plan_slots_say_which_prices_are_estimated() -> None:
    """The flag reaches the slot inputs of the optimizer."""
    from sunflow.planning.load_forecast import LoadForecast
    from sunflow.planning.plan import build_inputs

    prices = extend_prices(_known(24), [], START + 26 * HOUR, UTC)
    inputs = build_inputs(START, prices, [], LoadForecast(points=[], history_hours=0, quality="default"), 0.08)

    assert len(inputs) == 26 * 4
    assert [i.price_estimated for i in inputs[95:97]] == [False, True]
