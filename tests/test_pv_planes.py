"""Tests for the comparison of roof planes with their raw forecast."""

from datetime import UTC, datetime, timedelta

import pytest
from sunflow.analysis.pv_planes import plane_comparison
from sunflow.config.models import ForecastPlane

START = datetime(2026, 11, 8, 11, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)
PLANE = ForecastPlane(source="solcast", ref="south", name="Süd", device_id="inv", strings=["inv:1", "inv:2"])


def _row(i: int, raw: float, strings: dict[str, float]) -> dict:
    return {"start": (START + i * SLOT).isoformat(), "plane_forecasts": {PLANE.key: raw}, "actual_strings": strings}


def test_history_and_forecast_per_hour() -> None:
    """Strings add up per plane, slots average per hour; an hour missing a string has no measurement."""
    rows = [
        *(_row(i, 2000.0, {"inv:1": 1000.0, "inv:2": 600.0 + 100 * i}) for i in range(4)),
        *(_row(4 + i, 1000.0, {"inv:1": 800.0}) for i in range(4)),
    ]
    starts = [START + timedelta(hours=2) + i * SLOT for i in range(4)]
    ahead = {PLANE.key: {"raw": [3000.0] * 4, "low": [1000.0] * 4, "high": [3500.0] * 4, "expected": [2400.0] * 4}}

    (plane,) = plane_comparison([PLANE], rows, starts, ahead)

    assert [(p["raw"], p["measured"]) for p in plane["history"]] == [(2000.0, 1750.0), (1000.0, None)]
    assert plane["forecast"] == [{
        "start": (START + timedelta(hours=2)).isoformat(), "end": (START + timedelta(hours=3)).isoformat(),
        "raw": 3000.0, "low": 1000.0, "high": 3500.0, "expected": 2400.0,
    }]
    # Only the hour with both values counts
    assert plane["measured_share"] == pytest.approx(0.875)
    assert plane["expected_share"] == pytest.approx(0.8)
