"""Preview of the heating curve (design/heat_pump.md, 16): hourly means of the samples, the two-store house
model fitted to a synthetic house, the prediction with a heating curve, the outside temperature from the
forecast and the endpoint /heat_pump/<id>/preview."""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sunflow.api.heat_pump import router
from sunflow.config.models import DeviceConfig, HouseSettings, Room
from sunflow.config.store import ConfigStore
from sunflow.heat_pump.preview import (
    KF,
    KW,
    Params,
    Plant,
    PreviewCache,
    build,
    crossing,
    hourly,
    outside_hours,
    predict,
)

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
ROOM = "sensor.wohnen"
TRUE = Params(u=0.3, tau_s=4.0, tau_b=60.0, g=0.5)
PROFILE = "esphome/rotex_hpsu_can"


def synthetic_rows(hours: int = 96, params: Params = TRUE) -> list[dict[str, Any]]:
    """5 minute rows of a house that follows the model exactly: water steps between 27 and 31 °C every 8 h,
    outside a daily wave, the compressor off in two rows of an hour, a hot water row once a day."""
    plant = Plant(params)
    state = np.array([25.0, 22.0])
    rows = []
    start = NOW - timedelta(hours=hours)
    for h in range(hours):
        water = 27.0 if (h // 8) % 2 == 0 else 31.0
        outside = 2.0 + 4.0 * math.sin(2 * math.pi * h / 24)
        room_before = state[1]
        share = (9 if h % 24 == 5 else 10) / 12
        state = plant.step(state, water, share, outside)
        for k in range(12):
            on = k % 5 != 4
            rows.append({
                "start": (start + timedelta(hours=h, minutes=5 * k)).isoformat(),
                "activity": "hot_water" if (h % 24 == 5 and k == 0) else "heating",
                "compressor": 1.0 if on else 0.0,
                "tv_c": water + 1.0, "tr_c": water - 1.0, "setpoint_c": water + 1.5, "outside_c": outside,
                # Linear within the hour: its mean is the middle of start and end
                "rooms": {ROOM: float(room_before + (state[1] - room_before) * (k + 0.5) / 12)},
                "flags": {"ventilation": [ROOM] if h == 30 and k < 6 else []},
            })
    return rows


def test_hourly_means() -> None:
    rows = synthetic_rows(48)

    hours = hourly(rows, {ROOM: 1.0})

    assert len(hours) == 48
    first = hours[0]
    assert first.water_c == pytest.approx(27.0)
    assert first.share == pytest.approx(10 / 12)
    assert hours[5].share == pytest.approx(9 / 12)
    assert first.offset_k == pytest.approx(1.5)
    # Rows with a fast change do not count for the room
    assert hours[30].room_c is not None


def test_a_gap_is_an_hour_without_values() -> None:
    rows = [r for r in synthetic_rows(30) if not r["start"].startswith((NOW - timedelta(hours=20)).isoformat()[:13])]

    hours = hourly(rows, {ROOM: 1.0})

    gap = next(h for h in hours if h.start == (NOW - timedelta(hours=20)).replace(minute=0))
    assert (gap.water_c, gap.room_c, gap.share) == (None, None, 0.0)


def test_fit_reproduces_the_house_and_predicts_it() -> None:
    hours = hourly(synthetic_rows(96), {ROOM: 1.0})

    model = build(hours, NOW)

    assert model is not None
    assert model.fit.rms_k < 0.05
    assert model.test_rms_k is not None and model.test_rms_k < 0.1
    # The model's own curve: slope u / k, shift = offset − g / k (setpoint − water 1.5 K in the data), k the
    # conductance water → house at the mean compressor share
    k = 1 / (1 / (KW * model.share) + 1 / KF)
    assert model.slope == pytest.approx(TRUE.u / k, abs=0.06)
    assert model.shift_k == pytest.approx(1.5 - TRUE.g / k, abs=0.5)
    assert [p["model_c"] for p in model.past] and len(model.past) <= 49


def test_too_few_hours_give_no_model() -> None:
    assert build(hourly(synthetic_rows(20), {ROOM: 1.0}), NOW) is None


def test_a_steeper_curve_reaches_the_setpoint_sooner() -> None:
    model = build(hourly(synthetic_rows(96), {ROOM: 1.0}), NOW)
    assert model is not None
    outside = [0.0] * 72
    setpoint = model.room_c + 1.0

    flat = predict(model, NOW, outside, setpoint, 0.2, 0.0, (25.0, 40.0))
    steep = predict(model, NOW, outside, setpoint, 0.6, 1.0, (25.0, 40.0))
    capped = predict(model, NOW, outside, setpoint, 0.6, 1.0, (25.0, 28.0))

    assert steep["room_c"][-1] > flat["room_c"][-1]
    assert steep["reach_at"] is not None
    assert flat["reach_at"] is None or flat["reach_at"] > steep["reach_at"]
    # The limits of the set value cap the flow
    assert max(capped["flow_c"]) == 28.0 and steep["flow_c"][0] > 28.0


def test_crossing_between_the_hours() -> None:
    assert crossing(NOW, [21.0, 21.6, 22.2], 20.4, 22.0) == NOW + timedelta(hours=2 + 4 / 6)
    assert crossing(NOW, [23.0, 22.4], 23.5, 22.5) == NOW + timedelta(hours=1 + 5 / 6)
    assert crossing(NOW, [21.0, 21.1], 20.9, 23.0) is None


def test_outside_from_the_hourly_then_the_daily_forecast() -> None:
    start = NOW
    hourly_forecast = [{"datetime": (start + timedelta(hours=i)).isoformat(), "temperature": 1.0 + i} for i in range(3)]
    daily = [{"datetime": (start + timedelta(hours=3)).isoformat(), "temperature": 8.0, "templow": 2.0}]

    values, source = outside_hours(start, 5, hourly_forecast, daily, measured=-5.0)
    none, measured = outside_hours(start, 3, [], [], measured=-5.0)

    assert values == [1.0, 2.0, 3.0, 5.0, 5.0]
    assert source == "forecast"
    assert (none, measured) == ([-5.0] * 3, "measured")


def test_cache_builds_at_most_hourly() -> None:
    cache = PreviewCache()
    calls = []

    def make() -> None:
        calls.append(1)

    cache.get("hp", NOW, make)
    cache.get("hp", NOW + timedelta(minutes=30), make)
    cache.get("hp", NOW + timedelta(minutes=61), make)

    assert len(calls) == 2


def _api(tmp_path: Path, library: Any, rows: list[dict[str, Any]]) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.profiles = library
    app.state.store = ConfigStore(tmp_path / "config", lambda: library.profiles)
    config = app.state.store.load()
    config.devices = [DeviceConfig(id="hp", ha_device_id="x", profile=PROFILE, profile_version=3, name="HPSU")]
    config.house = HouseSettings(rooms=[Room(entity_id=ROOM)])
    app.state.store.save(config)
    store = SimpleNamespace(heat_pump_samples=lambda device_id, since: [r for r in rows if r["start"] >= since.isoformat()])
    app.state.runner = SimpleNamespace(heat_pump=None, shadow=store)
    return TestClient(app)


def test_endpoint(tmp_path: Path, library: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW.astimezone(tz)

    monkeypatch.setattr("sunflow.api.heat_pump.datetime", Clock)
    client = _api(tmp_path, library, synthetic_rows(96))

    answer = client.get("/api/v1/heat_pump/hp/preview", params={"days": 3, "slope": 0.5, "shift_k": 1.0, "setpoint_c": 23.0}).json()
    too_long = client.get("/api/v1/heat_pump/hp/preview", params={"days": 8})
    missing = client.get("/api/v1/heat_pump/nope/preview")

    assert answer["state"] == "ok"
    assert len(answer["times"]) == len(answer["outside_c"]) == len(answer["entered"]["room_c"]) == 72
    assert answer["entered"]["slope"] == 0.5 and answer["entered"]["shift_k"] == 1.0 and answer["setpoint_c"] == 23.0
    # Without a forecast and without a live value: the last measured outside temperature
    assert answer["outside_source"] == "measured"
    assert answer["model"]["rms_k"] < 0.05 and answer["past"]
    assert (too_long.status_code, missing.status_code) == (422, 404)


def test_endpoint_without_enough_data(tmp_path: Path, library: Any) -> None:
    answer = _api(tmp_path, library, synthetic_rows(10)).get("/api/v1/heat_pump/hp/preview").json()

    assert answer["state"] == "not_enough_data"
