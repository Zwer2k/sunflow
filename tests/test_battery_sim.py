"""Tests for the simulation of a battery one might buy."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.analysis.battery_sim import (
    Hour,
    optimized,
    self_consumption,
    simulate,
    without_battery,
)
from sunflow.config.models import BatteryScenario

START = datetime(2026, 10, 1, tzinfo=UTC)


def _days(days: int, charged: float = 0.0, discharged: float = 0.0) -> list[Hour]:
    """3 kWh surplus at noon, 3 kWh need in the evening and 1 kWh at night; cheap from 0 to 5."""
    hours = []
    for index in range(days * 24):
        hour = index % 24
        surplus = 1000.0 if 11 <= hour < 14 else 0.0
        need = 1000.0 if 18 <= hour < 21 else 200.0 if hour < 5 else 0.0
        hours.append(
            Hour(
                start=START + timedelta(hours=index), imported=need, exported=surplus,
                charged=charged if 11 <= hour < 14 else 0.0, discharged=discharged if 18 <= hour < 21 else 0.0,
                price=0.22 if hour < 5 else 0.32, feed_in=0.08,
            )
        )
    return hours


def _scenario(**changes: object) -> BatteryScenario:
    values = {
        "id": "s", "name": "10 kWh", "capacity_kwh": 10, "usable_pct": 100, "max_charge_kw": 5,
        "max_discharge_kw": 5, "efficiency_pct": 100, "inverter_efficiency_pct": 100,
    }
    return BatteryScenario(**(values | changes))


def test_surplus_covers_the_evening() -> None:
    """Without losses the noon surplus covers the evening need; night and the rest still come from the grid."""
    run = self_consumption(_days(7), _scenario())

    # Per day 3 kWh exported less and 3 kWh imported less; the first day starts empty
    assert run.exported_wh == pytest.approx(0)
    assert run.imported_wh == pytest.approx(7 * 1000)


def test_losses_reduce_what_arrives() -> None:
    """With 81 % round trip in total, 3 kWh stored give about 2.4 kWh in the evening."""
    run = self_consumption(_days(1), _scenario(efficiency_pct=90, inverter_efficiency_pct=95))

    assert run.imported_wh == pytest.approx(1000 + 3000 - 3000 * 0.9 * 0.95**2, rel=0.01)


def test_replace_takes_out_the_existing_battery_first() -> None:
    """The measured grid values already contain the existing battery: replacing it starts from what it did."""
    measured = _days(3, charged=1000, discharged=900)
    small = _scenario(capacity_kwh=1)

    added = self_consumption(measured, small)
    replaced = self_consumption(measured, small.model_copy(update={"mode": "replace"}))

    # Without the existing battery the house needs more from the grid than with both
    assert replaced.imported_wh > added.imported_wh


@pytest.mark.parametrize(("grid_charge", "charges_at_night"), [pytest.param(True, True, id="grid-charge"), pytest.param(False, False, id="pv-only")])
def test_optimized_charges_at_night_where_it_pays(grid_charge: bool, charges_at_night: bool) -> None:
    """Without the noon surplus, cheap night power is stored for the evening if allowed."""
    hours = [h if not 11 <= h.start.hour < 14 else Hour(**{**h.__dict__, "exported": 0.0}) for h in _days(4)]

    run = optimized(hours, _scenario(grid_charge=grid_charge))
    plain = sum(h.imported for h in hours)

    assert (run.cost < sum(h.imported * h.price for h in hours) / 1000 - 0.1) is charges_at_night
    assert run.imported_wh >= plain - 1


def test_simulation_reports_savings_per_year_and_payback() -> None:
    """Savings against the measured operation, scaled to a year; payback from the price."""
    result = simulate(_days(14), [_scenario(price_eur=5000)])

    scenario = result["scenarios"][0]["self_consumption"]
    assert result["days"] == 14
    assert scenario["saving"] > 0
    assert scenario["saving_per_year"] == pytest.approx(scenario["saving"] * 365 / 14)
    assert scenario["payback_years"] == pytest.approx(5000 / scenario["saving_per_year"])


def test_without_battery_takes_out_the_existing_one() -> None:
    """Without any battery its charging goes to the grid and its discharging comes from it."""
    measured = _days(2, charged=1000, discharged=900)

    result = simulate(measured, [_scenario(price_eur=5000)])
    none = without_battery(measured)

    assert none.exported_wh == pytest.approx(2 * (3000 + 3000))
    assert none.imported_wh == pytest.approx(2 * (4000 + 2700))
    assert result["has_battery"] is True
    assert result["measured"]["saving_vs_none_per_year"] == pytest.approx((none.cost - result["measured"]["cost"]) * 365 / 2)
    run = result["scenarios"][0]["self_consumption"]
    assert run["saving_vs_none_per_year"] == pytest.approx((none.cost - run["cost"]) * 365 / 2)
