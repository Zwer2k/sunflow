"""Tests for consumers that are only measured."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.planning.appliances import usage_from

START = datetime(2026, 9, 7, tzinfo=UTC)  # a Monday


def _hours(days: int, power: callable) -> list[tuple[datetime, float]]:
    return [(START + timedelta(hours=h), power(START + timedelta(hours=h))) for h in range(days * 24)]


def test_usual_hours_on_workdays_and_weekends() -> None:
    """A washing machine on Saturdays at 10, a stove on workdays at 18, standby otherwise."""

    def power(moment: datetime) -> float:
        if moment.weekday() == 5 and moment.hour == 10:
            return 1200.0
        if moment.weekday() < 5 and moment.hour == 18:
            return 900.0
        return 2.0

    usage = usage_from(_hours(28, power), UTC)

    assert usage is not None
    result = usage.as_dict()
    assert result["usual_workday_hour"] == 18
    assert result["usual_weekend_hour"] == 10
    # 5 × 900 + 1200 per week plus standby, on every day but Sunday
    assert result["daily_wh"] == pytest.approx((5 * 900 + 1200) / 7 + 2 * 24 - (5 + 1) * 2 / 7, abs=1)
    assert result["active_share"] == pytest.approx(6 / 7, abs=0.01)


def test_too_few_days() -> None:
    """Less than a week says nothing about the habits."""
    assert usage_from(_hours(5, lambda moment: 100.0), UTC) is None


def test_measured_consumer_needs_a_power_sensor() -> None:
    """Only measured: no switch needed, but a power sensor."""
    from fastapi import HTTPException
    from sunflow.api.devices import ManualDevice, _manual_setup
    from sunflow.devices.discovery import Snapshot

    snapshot = Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None)
    overrides, parameters, steps = _manual_setup(
        ManualDevice(name="Waschmaschine", type="meter", power_entity="sensor.waschmaschine_power"), snapshot
    )

    assert overrides["power"].entity_id == "sensor.waschmaschine_power"
    assert (parameters, steps) == ({}, None)
    with pytest.raises(HTTPException):
        _manual_setup(ManualDevice(name="Herd", type="meter"), snapshot)
