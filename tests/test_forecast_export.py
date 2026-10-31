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


def _slots(now: datetime, prices: list[float], exports: list[float]) -> list:
    from types import SimpleNamespace

    return [
        SimpleNamespace(start=(now + timedelta(minutes=15 * i)).isoformat(), price=p, feed_in=0.08, export_w=e)
        for i, (p, e) in enumerate(zip(prices, exports, strict=True))
    ]


@pytest.mark.parametrize(
    ("prices", "exports", "finish_by_slots", "expected_slot"),
    [
        pytest.param([0.3] * 8, [0, 0, 0, 2000, 2000, 2000, 2000, 0], None, 3, id="into-the-pv-surplus"),
        pytest.param([0.3, 0.3, 0.1, 0.1, 0.1, 0.1, 0.3, 0.3], [0] * 8, None, 2, id="into-the-cheap-hour"),
        pytest.param([0.3, 0.3, 0.1, 0.1, 0.1, 0.1, 0.3, 0.3], [0] * 8, 4, 0, id="done-in-time-beats-cheap"),
        pytest.param([0.3] * 8, [0] * 8, None, 0, id="equal-means-now"),
    ],
)
def test_one_time_load_starts_when_cheapest(
    prices: list[float], exports: list[float], finish_by_slots: int | None, expected_slot: int
) -> None:
    """A 2 kW run of an hour is placed contiguously where it costs least and is done in time."""
    from sunflow.planning.one_time import OneTimeLoad, best_start

    now = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    finish_by = now + timedelta(minutes=15 * finish_by_slots) if finish_by_slots else None
    load = OneTimeLoad(name="Waschmaschine", energy_wh=2000, power_w=2000, finish_by=finish_by)
    found = best_start(load, _slots(now, prices, exports), now)
    assert found is not None
    assert found[0] == now + timedelta(minutes=15 * expected_slot)


def test_one_time_load_is_fixed_once_started_and_dropped_when_done(tmp_path: Path) -> None:
    """The start stays once it came; after the end the load is gone, also after a restart."""
    from sunflow.planning.one_time import OneTimeLoad, OneTimeLoads

    now = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    loads = OneTimeLoads(tmp_path)
    loads.loads.append(OneTimeLoad(name="Spülmaschine", energy_wh=1000, power_w=2000))
    loads.update(_slots(now, [0.3] * 8, [0] * 8), now)
    (load,) = OneTimeLoads(tmp_path).as_list()
    assert (load["start"], load["started"]) == (now.isoformat(), True)

    later = now + timedelta(minutes=45)
    loads.update(_slots(later, [0.1] * 8, [0] * 8), later)
    assert OneTimeLoads(tmp_path).as_list() == []


def test_calendar_windows() -> None:
    """Consecutive planned slots become one event per consumer, grid charging and one-time load."""
    from types import SimpleNamespace

    from sunflow.api.integration import calendar

    now = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)

    def slot(i: int, wallbox: float, action: str = "auto", import_w: float = 0.0) -> SimpleNamespace:
        return SimpleNamespace(
            start=(now + timedelta(minutes=15 * i)).isoformat(), flex_w={"Wallbox": wallbox}, action=action,
            import_w=import_w, curtail_w=0.0,
        )

    slots = [slot(0, 0, "force_charge", 3000), slot(1, 4000), slot(2, 4000), slot(3, 0), slot(4, 2000)]
    info = SimpleNamespace(name="Wallbox", kind="vehicle")
    load = {"name": "Waschmaschine", "start": slots[3].start, "end": slots[4].start}
    events = calendar(slots, [info], [load])
    assert [(e["summary"], e["start"], e["end"]) for e in events] == [
        ("Speicher lädt aus dem Netz", slots[0].start, slots[1].start),
        ("Wallbox lädt", slots[1].start, slots[3].start),
        ("Waschmaschine", slots[3].start, slots[4].start),
        ("Wallbox lädt", slots[4].start, (now + timedelta(minutes=75)).isoformat()),
    ]


async def test_recorded_hours_fill_today_and_a_partial_last_day_is_left_out() -> None:
    """A source without today's past hours gets them from the recording; a day it only starts is dropped."""
    from sunflow.planning.forecast_export import export_forecasts

    config = _config().model_copy(update={"forecast_planes": _config().forecast_planes[:1]})
    now = MIDNIGHT + timedelta(hours=10)
    # The source delivers from 10:00 local on, until 03:00 two days later
    ahead = [p for p in _points(1000.0, 51) if p.start >= now]
    recorded = {"solcast:ne": [p for p in _points(800.0, 24) if p.start < now]}

    async def fetch(plane: ForecastPlane) -> list[ForecastPoint]:
        return ahead

    (total, *_) = await export_forecasts(config, fetch, None, {}, TZ, recorded)
    hours = total.wh_hours
    assert hours[MIDNIGHT.isoformat()] == pytest.approx(800)
    assert hours[now.isoformat()] == pytest.approx(1000)
    # 30.10. and 31.10. complete; the 1.11. reaches only to 03:00 and is left out
    days = {datetime.fromisoformat(k).astimezone(TZ).date().isoformat() for k in hours}
    assert days == {"2026-10-30", "2026-10-31"}


async def test_past_days_come_from_the_recording() -> None:
    """Days before today are delivered from the recorded forecast, with the plane's correction."""
    from sunflow.planning.forecast_export import export_forecasts

    config = _config().model_copy(update={"forecast_planes": _config().forecast_planes[:1]})
    yesterday = MIDNIGHT - timedelta(days=1)
    recorded = {
        "solcast:ne": [
            ForecastPoint(start=yesterday + timedelta(minutes=15 * i), end=yesterday + timedelta(minutes=15 * (i + 1)), power=400.0)
            for i in range(96)
        ]
    }

    async def fetch(plane: ForecastPlane) -> list[ForecastPoint]:
        return _points(1000.0)

    site = PvCorrection(overall=0.5, by_hour={}, by_hour_cloud={}, days=10, slots=100)
    (total, *_) = await export_forecasts(config, fetch, site, {}, TZ, recorded)
    assert total.wh_hours[yesterday.isoformat()] == pytest.approx(200)
    assert total.wh_hours[MIDNIGHT.isoformat()] == pytest.approx(500)
