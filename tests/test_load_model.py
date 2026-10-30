"""Tests for the learned load model."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sunflow.planning.load_model import fit

BERLIN = ZoneInfo("Europe/Berlin")


def _rows(days: int, heating_w_per_k: float = 40.0) -> list[dict[str, Any]]:
    """Base load: 300 W at night, 600 W in the evening, +40 W per degree below 15 °C; weekends +200 W."""
    rows = []
    start = datetime(2026, 10, 1, tzinfo=BERLIN)
    for day in range(days):
        temperature_day = 5.0 + 10.0 * (day % 7) / 6  # 5..15 °C over a week
        for quarter in range(96):
            local = start + timedelta(days=day, minutes=15 * quarter)
            base = 600.0 if 17 <= local.hour < 22 else 300.0
            if local.weekday() >= 5:
                base += 200.0
            base += heating_w_per_k * max(15.0 - temperature_day, 0.0)
            rows.append({
                "start": local.astimezone(UTC).isoformat(),
                "actual_load_w": base + 100.0,
                "actual_loadpoint_w": 100.0,  # a consumer that is not base load
                "weather_temp": temperature_day,
            })
    return rows


def test_learns_temperature_and_profile() -> None:
    """The model finds the heating share and beats the plain hourly average."""
    model = fit(_rows(28), BERLIN)

    assert model is not None and model.useful
    assert model.heating_w_per_k == pytest.approx(40, abs=5)
    monday_evening = datetime(2026, 11, 2, 19, 0, tzinfo=BERLIN)
    assert model.predict(monday_evening, 5.0) == pytest.approx(600 + 400, abs=40)
    saturday_night = datetime(2026, 11, 7, 3, 0, tzinfo=BERLIN)
    assert model.predict(saturday_night, 15.0) == pytest.approx(500, abs=40)


def test_needs_two_weeks_with_temperature() -> None:
    """Too few days, or rows without temperature, give no model."""
    assert fit(_rows(10), BERLIN) is None
    rows = _rows(28)
    for row in rows:
        row["weather_temp"] = None
    assert fit(rows, BERLIN) is None
