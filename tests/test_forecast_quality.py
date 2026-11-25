"""Tests for recording the forecasts known in advance and rating them."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sunflow.config.models import ForecastPlane, SunflowConfig
from sunflow.ha import HACommandError
from sunflow.planning import plan
from sunflow.planning.sources import ForecastPoint
from sunflow.shadow import runner as module
from sunflow.shadow.runner import ShadowRunner
from sunflow.shadow.store import ShadowStore, forecast_quality

START = datetime(2026, 11, 20, 11, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")
PLANES = {"solcast:a": 1200.0, "forecast_solar:b": 1600.0}


def _store_slot(store: ShadowStore, start: datetime, actual_pv: float) -> None:
    store.store_planned(
        start,
        {"action": "auto", "power": None, "soc": 5000, "pv": actual_pv, "load": 400, "price": 0.3,
         "planes": '{"solcast:a": 1000.0}', "unused_planes": '{"forecast_solar:b": 1400.0}'},
    )
    store.store_actual(
        start,
        {"actual_soc_wh": 5000, "actual_battery_w": 0, "actual_grid_w": 0, "actual_pv_w": actual_pv,
         "actual_load_w": 1500.0, "actual_loadpoint_w": 1000.0, "actual_action": "auto", "samples": 15},
    )


def test_dayahead_is_kept_and_rated(tmp_path: Path) -> None:
    """The first evening plan stays; quality uses it, the base load and each service's raw forecast."""
    store = ShadowStore(tmp_path)
    try:
        made = START - timedelta(hours=14)
        store.store_dayahead(made, [(START, 1300.0, 1200.0, 600.0, 5200.0, PLANES)])
        # A later plan of the same evening does not replace it
        store.store_dayahead(made + timedelta(hours=1), [(START, 9999.0, 9999.0, 9999.0, 9999.0, {})])
        store.store_ahead(START, 5100.0, {}, 1100.0, 450.0)
        _store_slot(store, START, 1000.0)
        rows = store.rows(START)
    finally:
        store.close()

    assert rows[0]["unused_plane_forecasts"] == {"forecast_solar:b": 1400.0}
    assert rows[0]["dayahead_planes"] == PLANES
    quality = forecast_quality(rows)

    assert quality["days"] == 1
    assert quality["pv"]["dayahead"] | {"slots": 1} == pytest.approx(
        {"slots": 1, "forecast_kwh": 0.325, "actual_kwh": 0.25, "mae_w": 300.0, "bias_w": 300.0}
    )
    assert quality["pv"]["ahead"]["bias_w"] == pytest.approx(100.0)
    # The wallbox is not part of the forecast: 1500 W measured minus 1000 W wallbox
    assert quality["load"]["dayahead"]["bias_w"] == pytest.approx(100.0)
    assert [(s["service"], s["planned_with"], s["raw"]["bias_w"]) for s in quality["services"]] == [
        ("forecast_solar", False, pytest.approx(600.0)),
        ("solcast", True, pytest.approx(200.0)),
    ]


def test_night_slots_do_not_dilute_the_pv_error() -> None:
    """Slots without PV in forecast and measurement are left out of the PV figures."""
    row = {
        "start": START.isoformat(), "actual_pv_w": 0.0, "actual_load_w": 300.0, "dayahead_pv_w": 0.0,
        "dayahead_load_w": 300.0, "dayahead_planes": {"solcast:a": 0.0}, "plane_forecasts": {},
    }

    quality = forecast_quality([row])

    assert quality["pv"]["dayahead"] is None
    assert quality["load"]["dayahead"]["mae_w"] == 0.0
    assert quality["services"] == []


@pytest.mark.parametrize(
    ("now", "stored"),
    [
        # 18:30 and 17:30 in Berlin
        pytest.param(datetime(2026, 11, 20, 17, 30, tzinfo=UTC), [datetime(2026, 11, 20, 23, 0, tzinfo=UTC)], id="evening"),
        pytest.param(datetime(2026, 11, 20, 16, 30, tzinfo=UTC), [], id="before_the_evening"),
    ],
)
def test_dayahead_is_stored_from_the_evening_on(
    monkeypatch: pytest.MonkeyPatch, now: datetime, stored: list[datetime]
) -> None:
    """Only slots of the next local day, only from DAYAHEAD_HOUR on, with every plane's raw forecast."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr(module, "datetime", Clock)
    slots = [SimpleNamespace(start=now.replace(hour=22, minute=45) + timedelta(minutes=15) * i, pv_w=0.0, load_w=300.0, soc_wh=5000.0) for i in range(2)]
    result = SimpleNamespace(
        plan=SimpleNamespace(slots=slots), raw_pv=[0.0, 0.0],
        plane_pv={"solcast:a": {"raw": [0.0, 10.0]}}, unused_pv={"forecast_solar:b": [0.0, 20.0]},
    )
    calls: list[list[tuple]] = []
    runner = SimpleNamespace(shadow=SimpleNamespace(store_dayahead=lambda made, rows: calls.append(rows)))

    ShadowRunner._store_dayahead(runner, result, BERLIN)  # type: ignore[arg-type]

    assert [row[0] for rows in calls for row in rows] == stored
    assert [row[5] for rows in calls for row in rows] == [{"solcast:a": 10.0, "forecast_solar:b": 20.0}] * len(stored)


async def test_unused_planes_are_fetched_for_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    """Planes without an inverter are fetched per slot; planned ones and failing ones are left out."""
    async def forecast_solar(client: object, ref: str, start: datetime, hours: int) -> list[ForecastPoint]:
        if ref == "broken":
            raise HACommandError("unavailable")
        return [ForecastPoint(start=start, end=start + timedelta(minutes=30), power=800.0)]

    monkeypatch.setattr(plan, "forecast_solar_forecast", forecast_solar)
    config = SunflowConfig(forecast_planes=[
        ForecastPlane(source="forecast_solar", ref="cmp", name="Süd"),
        ForecastPlane(source="forecast_solar", ref="broken", name="Nord"),
        ForecastPlane(source="forecast_solar", ref="used", name="Carport", device_id="inverter"),
    ])
    inputs = [SimpleNamespace(start=START + timedelta(minutes=15) * i) for i in range(3)]

    result = await plan.unused_plane_forecasts(object(), config, inputs, START)  # type: ignore[arg-type]

    assert result == {"forecast_solar:cmp": [800.0, 800.0, 0.0]}
