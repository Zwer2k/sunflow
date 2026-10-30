"""Tests for the forecast and string energy Sunflow hands to Home Assistant."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sunflow.config.models import DeviceConfig, ForecastPlane, SunflowConfig
from sunflow.planning.forecast_export import export_forecasts
from sunflow.planning.pv_learning import PvCorrection
from sunflow.planning.sources import ForecastPoint
from sunflow.shadow.counters import EnergyCounters

TZ = ZoneInfo("Europe/Berlin")
# 06:00 local on 30.10. (UTC+1)
MIDNIGHT = datetime(2026, 10, 30, 0, 0, tzinfo=TZ).astimezone(UTC)


def _points(watts: float, hours: int = 48) -> list[ForecastPoint]:
    """Half-hourly points with the same power from local midnight on."""
    return [
        ForecastPoint(start=MIDNIGHT + timedelta(minutes=30 * i), end=MIDNIGHT + timedelta(minutes=30 * (i + 1)), power=watts)
        for i in range(hours * 2)
    ]


def _config() -> SunflowConfig:
    sofar = DeviceConfig(id="sofar", ha_device_id="s", profile="p", profile_version=1, name="Sofar")
    huawei = DeviceConfig(id="huawei", ha_device_id="h", profile="p", profile_version=1, name="Huawei")
    return SunflowConfig(
        devices=[sofar, huawei],
        forecast_planes=[
            ForecastPlane(source="solcast", ref="ne", name="Nord-Ost", device_id="sofar"),
            ForecastPlane(source="solcast", ref="se", name="Süd-Ost", device_id="sofar"),
            ForecastPlane(source="solcast", ref="car", name="Carport", device_id="huawei"),
            ForecastPlane(source="solcast", ref="none", name="ohne Anlage"),
        ],
    )


async def test_targets_per_plane_plant_and_total() -> None:
    """Each plane with its own or the site's factor; plants and total add their planes up."""
    powers = {"ne": 1000.0, "se": 2000.0, "car": 500.0}
    site = PvCorrection(overall=0.5, by_hour={}, by_hour_cloud={}, days=10, slots=100)
    carport = PvCorrection(overall=2.0, by_hour={}, by_hour_cloud={}, days=10, slots=100)

    async def fetch(plane: ForecastPlane) -> list[ForecastPoint]:
        return _points(powers[plane.ref])

    targets = await export_forecasts(_config(), fetch, site, {"solcast:car": carport}, TZ)
    by_id = {t.id: t for t in targets}
    assert list(by_id) == ["total", "plant:sofar", "plant:huawei", "plane:solcast:ne", "plane:solcast:se", "plane:solcast:car"]
    first = MIDNIGHT.isoformat()
    # Nord-Ost 1 kW at the site's 0.5; Carport 0.5 kW at its own 2.0
    assert by_id["plane:solcast:ne"].wh_hours[first] == pytest.approx(500)
    assert by_id["plane:solcast:car"].wh_hours[first] == pytest.approx(1000)
    assert by_id["plant:sofar"].wh_hours[first] == pytest.approx(1500)
    assert by_id["total"].wh_hours[first] == pytest.approx(2500)
    assert by_id["plane:solcast:car"].plant == "Huawei"


async def test_summary_today_rest_and_tomorrow() -> None:
    """Key figures of a target at 12:30 local."""

    async def fetch(plane: ForecastPlane) -> list[ForecastPoint]:
        return _points(1000.0)

    config = _config().model_copy(update={"forecast_planes": _config().forecast_planes[:1]})
    (total, *_) = await export_forecasts(config, fetch, None, {}, TZ)
    now = MIDNIGHT + timedelta(hours=12, minutes=30)
    summary = total.summary(now, TZ)
    assert summary == {"today_wh": 24000.0, "remaining_today_wh": 11500.0, "tomorrow_wh": 24000.0, "power_now_w": 1000.0}


def test_string_energy_counts_and_survives_a_restart(tmp_path: Path) -> None:
    """Energy is the mean power times the time between measurements; long gaps are not bridged."""
    start = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    counters = EnergyCounters(tmp_path)
    counters.add({"sofar:1": 1000.0}, start)
    counters.add({"sofar:1": 2000.0}, start + timedelta(minutes=1))
    assert counters.wh["sofar:1"] == pytest.approx(1500 / 60)

    again = EnergyCounters(tmp_path)
    again.add({"sofar:1": 2000.0}, start + timedelta(minutes=20))
    assert again.wh["sofar:1"] == pytest.approx(1500 / 60)


def test_integration_state_from_plan_and_sample() -> None:
    """Current slot, cheapest window, SoC in an hour, surplus and when a consumer starts."""
    from types import SimpleNamespace

    from sunflow.api.integration import integration_state

    now = datetime(2026, 10, 30, 12, 5, tzinfo=UTC)

    def slot(minutes: int, price: float, wallbox: float = 0.0, action: str = "auto") -> SimpleNamespace:
        return SimpleNamespace(
            start=(now.replace(minute=0) + timedelta(minutes=minutes)).isoformat(), action=action, action_power_w=None,
            price=price, feed_in=0.08, soc_wh=10000 + minutes * 100, import_w=0.0, curtail_w=0.0,
            flex_w={"Wallbox": wallbox},
        )

    slots = [slot(0, 0.30), slot(15, 0.20), slot(30, 0.25, 4000), slot(45, 0.25, 4000), slot(60, 0.31), slot(75, 0.32)]
    info = SimpleNamespace(
        device_id="wb", name="Wallbox", kind="vehicle", current=40.0, target=80.0, unit="%", vehicle="Fiat",
        need_wh=12000.0, reason=None,
    )
    result = SimpleNamespace(
        plan=SimpleNamespace(slots=slots, savings=1.234), battery=SimpleNamespace(capacity_wh=20000), flex=[info]
    )
    sample = SimpleNamespace(pv_w=5000.0, load_w=800.0, grid_w=-3000.0, battery_w=1200.0, soc_wh=10000.0)

    state = integration_state(SunflowConfig(), result, now, sample, None, now)

    assert state["plan"]["price_ct"] == 30.0
    assert state["plan"]["cheapest_start"] == slots[1].start
    # The slot starting at 13:15 is the first a full hour ahead: 10000 + 75 * 100 Wh of 20000
    assert state["plan"]["soc_in_an_hour_pct"] == 87.5
    assert state["live"] == {
        "pv_w": 5000.0, "load_w": 800.0, "grid_w": -3000.0, "battery_w": 1200.0, "soc_pct": 50.0,
        "surplus_w": 4200.0, "surplus": True,
    }
    (consumer,) = state["consumers"]
    assert (consumer["next_start"], consumer["planned_wh"], consumer["running"]) == (slots[2].start, 2000.0, False)
