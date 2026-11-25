"""The house model (design/heat_pump.md, 14.1): heating surface and house, learned from the 5 minute rows of
heat_pump_samples; its test against "the temperature stays", the preview of the heating curve (16) and the
endpoints /heat_pump/<id>/house_model and /heat_pump/<id>/preview."""

from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
from pydantic import ValidationError
import pytest
from sunflow.api.heat_pump import router as heat_pump_router
from sunflow.api.house import router as house_router
from sunflow.config.models import CONFIG_VERSION, DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.heat_pump.house import (
    STEP,
    Learning,
    Params,
    Row,
    build,
    crossing,
    learn,
    outside_hours,
    predict,
    sections,
    simulate,
)
from sunflow.planning.house_plan import carnot_cop, heat_pump_device

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
ROOM = "sensor.wohnen"
WEIGHTS = {ROOM: 1.0}
TRUE = Params(u=0.15, kf=1.2, cs=6.0, cb=25.0, g=0.3)
KW = 0.8
PROFILE = "esphome/rotex_hpsu_can"


def _row(start: datetime, **values: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "start": start.isoformat(), "activity": "heating", "compressor": 1.0, "heat_w": 1000.0, "tv_c": 31.0,
        "tr_c": 29.0, "setpoint_c": 31.5, "outside_c": 2.0, "rooms": {ROOM: 21.0}, "flags": {},
    }
    return row | values


def house_rows(hours: int, params: Params = TRUE, quantum: float | None = 0.1, seed: int = 1, end: datetime = NOW) -> list[dict[str, Any]]:
    """5 minute rows of a house that follows the model: the compressor heats two hours of three with the water
    stepping between 30 and 33 °C, hot water half an hour a day and a defrost of 10 min every 6 h (both heat
    0 for the house, whatever the meter shows); outside a daily wave; the room sensor noisy and in 0.1 K steps."""
    rng = np.random.default_rng(seed)
    state = np.array([26.0, 21.5])
    rows = []
    start = end - timedelta(hours=hours)
    for k in range(hours * 12):
        at = start + k * STEP
        outside = 2 + 5 * math.sin(2 * math.pi * k / 288)
        activity = "hot_water" if 60 <= k % 288 < 66 else "defrost" if k % 72 in (10, 11) else "heating"
        on = (k // 12) % 3 != 2 and activity == "heating"
        water = 30.0 if (k // 96) % 2 else 33.0
        heat = KW * (water - state[0]) if on else 0.0
        room = state[1] + rng.normal(0, 0.02)
        if quantum:
            room = round(room / quantum) * quantum
        shown = {"heating": heat * 1000, "hot_water": 3000.0, "defrost": -2500.0}[activity]
        rows.append(_row(
            at, activity=activity, compressor=1.0 if on else 0.0, heat_w=shown, tv_c=water + 1, tr_c=water - 1,
            setpoint_c=water + 1.5, outside_c=outside, rooms={ROOM: float(room)},
        ))
        step = [Row(start=at, heat_kw=heat, outside_c=outside, room_c=None, share=0.0, water_c=None, offset_k=None, heating=True)]
        _, state = simulate(params, step, float(state[0]), float(state[1]))
    return rows


# Rows → sections


def test_heat_per_row_counts_only_heating() -> None:
    """Hot water, defrost and standby give no heat to the house; heating (also not valid) as measured."""
    rows = [
        _row(NOW, heat_w=1500.0, flags={"valid": False, "after_run": True}),
        _row(NOW + STEP, activity="hot_water", heat_w=3000.0),
        _row(NOW + 2 * STEP, activity="defrost", heat_w=-2500.0),
        _row(NOW + 3 * STEP, activity="idle", compressor=0.0, heat_w=-900.0),
    ]

    [section] = sections(rows, WEIGHTS)

    assert [r.heat_kw for r in section] == [1.5, 0.0, 0.0, 0.0]
    assert [r.heating for r in section] == [True, False, False, False]


def test_short_gaps_repeat_long_gaps_split() -> None:
    """A missing row up to 15 min repeats the inputs without a room value; a longer gap starts a new section."""
    rows = [_row(NOW), _row(NOW + 3 * STEP, heat_w=2000.0), _row(NOW + 3 * STEP + timedelta(minutes=20)), _row(NOW + 3 * STEP + timedelta(minutes=25))]

    first, second = sections(rows, WEIGHTS)

    assert [r.start for r in first] == [NOW + k * STEP for k in range(4)]
    assert [r.room_c for r in first] == [21.0, None, None, 21.0]
    assert [r.heat_kw for r in first] == [1.0, 1.0, 1.0, 2.0]
    assert len(second) == 2


def test_a_section_starts_with_a_room_value_and_a_fast_change_gives_none() -> None:
    rows = [_row(NOW, rooms={}), _row(NOW + STEP, flags={"ventilation": [ROOM]}), _row(NOW + 2 * STEP), _row(NOW + 3 * STEP)]

    [section] = sections(rows, WEIGHTS)

    assert [r.start for r in section] == [NOW + 2 * STEP, NOW + 3 * STEP]


def test_heating_without_heat_value_is_missing() -> None:
    rows = [_row(NOW), _row(NOW + STEP, heat_w=None), _row(NOW + 2 * STEP)]

    [section] = sections(rows, WEIGHTS)

    # The missing row is repeated from the one before (without a room value)
    assert [r.room_c for r in section] == [21.0, None, 21.0]


def test_simulate_holds_the_steady_state() -> None:
    """In equilibrium (heat = losses − gains, surface = room + heat / Kf) nothing moves."""
    heat = TRUE.u * (21.0 - 2.0) - TRUE.g
    rows = [Row(start=NOW + k * STEP, heat_kw=heat, outside_c=2.0, room_c=None, share=0.0, water_c=None, offset_k=None, heating=True) for k in range(50)]

    states, end = simulate(TRUE, rows, 21.0 + heat / TRUE.kf, 21.0)

    assert np.allclose(states[:, 1], 21.0) and end[1] == pytest.approx(21.0)


# Fit and test


def test_fit_finds_the_house_through_hot_water_and_defrost() -> None:
    model = build(house_rows(10 * 24), WEIGHTS, NOW)

    assert model is not None
    p, spread = model.params, model.fit.spread
    assert p.u == pytest.approx(TRUE.u, rel=0.1)
    assert p.cb == pytest.approx(TRUE.cb, rel=0.15)
    assert p.kf == pytest.approx(TRUE.kf, rel=0.3)
    assert p.cs == pytest.approx(TRUE.cs, rel=0.3)
    # Every value with its uncertainty; the true one within a few of them
    assert all(v > 0 for v in spread.values())
    assert abs(p.u - TRUE.u) < 4 * spread["u"] + 0.005
    assert model.fit.rms_k < 0.05 and model.plausible
    assert model.kw == pytest.approx(KW, rel=0.1)
    # Better than "it stays" on the last 24 h, fitted without them
    assert model.test_rms_k is not None and model.stay_rms_k is not None
    assert model.test_rms_k < model.stay_rms_k
    assert model.usable and model.reason() is None
    assert model.chart and len(model.chart) <= 7 * 24 + 1
    assert model.heating_hours > 0 and model.outside_min_c < model.outside_max_c


def test_uncertainty_shrinks_with_more_days() -> None:
    short = build(house_rows(60), WEIGHTS, NOW)
    long = build(house_rows(10 * 24), WEIGHTS, NOW)

    assert short is not None and long is not None
    assert long.fit.spread["u"] < short.fit.spread["u"]
    assert long.fit.spread["cb"] < short.fit.spread["cb"]


def test_shown_from_24_hours_usable_from_48() -> None:
    assert build(house_rows(20), WEIGHTS, NOW) is None
    early = build(house_rows(30), WEIGHTS, NOW)

    assert early is not None
    assert early.fit.room_hours < 48 and not early.usable and early.reason() == "few_hours"


def test_learn_states() -> None:
    no_heat = [_row(NOW + k * STEP, heat_w=None) for k in range(5)]

    assert learn(no_heat, WEIGHTS, NOW).state == "no_heat"
    few = learn(house_rows(10), WEIGHTS, NOW)
    assert (few.state, few.model) == ("learning", None) and 9 <= few.room_hours <= 11
    summer = learn([_row(NOW + k * STEP, activity="idle", compressor=0.0, heat_w=None) for k in range(5)], WEIGHTS, NOW)
    assert summer.state == "learning"
    ok = learn(house_rows(30), WEIGHTS, NOW)
    assert ok.state == "ok" and ok.model is not None and ok.room_hours >= 24


# Preview


def test_the_models_curve_holds_the_setpoint() -> None:
    """With the model's own heating curve the room stays on the setpoint (equilibrium)."""
    model = build(house_rows(10 * 24), WEIGHTS, NOW)
    assert model is not None and model.slope is not None and model.shift_k is not None
    setpoint = 21.0
    model.room_c = setpoint
    # Start in equilibrium at 2 °C outside
    heat = model.heat_to_hold(setpoint, 2.0)
    model.surface_c = setpoint + heat / model.params.kf

    result = predict(model, NOW, [2.0] * 48, setpoint, model.slope, model.shift_k, (20.0, 60.0))

    assert result["room_c"][-1] == pytest.approx(setpoint, abs=0.05)


def test_a_steeper_curve_reaches_the_setpoint_sooner() -> None:
    model = build(house_rows(5 * 24), WEIGHTS, NOW)
    assert model is not None
    outside = [0.0] * 72
    setpoint = model.room_c + 1.0

    flat = predict(model, NOW, outside, setpoint, 0.2, 0.0, (25.0, 40.0))
    steep = predict(model, NOW, outside, setpoint, 0.8, 2.0, (25.0, 45.0))
    capped = predict(model, NOW, outside, setpoint, 0.8, 2.0, (25.0, 28.0))

    assert steep["room_c"][-1] > flat["room_c"][-1]
    assert steep["reach_at"] is not None
    assert flat["reach_at"] is None or flat["reach_at"] > steep["reach_at"]
    assert max(capped["flow_c"]) == 28.0 and steep["flow_c"][0] > 28.0


def test_crossing_between_the_hours() -> None:
    assert crossing(NOW, [21.0, 21.6, 22.2], 20.4, 22.0) == NOW + timedelta(hours=2 + 4 / 6)
    assert crossing(NOW, [23.0, 22.4], 23.5, 22.5) == NOW + timedelta(hours=1 + 5 / 6)
    assert crossing(NOW, [21.0, 21.1], 20.9, 23.0) is None


def test_outside_from_the_hourly_then_the_daily_forecast() -> None:
    hourly_forecast = [{"datetime": (NOW + timedelta(hours=i)).isoformat(), "temperature": 1.0 + i} for i in range(3)]
    daily = [{"datetime": (NOW + timedelta(hours=3)).isoformat(), "temperature": 8.0, "templow": 2.0}]

    values, source = outside_hours(NOW, 5, hourly_forecast, daily, measured=-5.0)
    none, measured = outside_hours(NOW, 3, [], [], measured=-5.0)

    assert values == [1.0, 2.0, 3.0, 5.0, 5.0]
    assert source == "forecast"
    assert (none, measured) == ([-5.0] * 3, "measured")


# Helpers of the planning


@pytest.mark.parametrize(("outside", "cop"), [(-10.0, 2.74), (7.0, 4.40), (40.0, 6.0)])
def test_carnot_cop(outside: float, cop: float) -> None:
    """A rough COP from the Carnot limit when nothing measures it (14.3)."""
    assert carnot_cop(outside) == pytest.approx(cop, abs=0.01)


@pytest.mark.parametrize(
    ("profile", "found"),
    [
        pytest.param("x/hp", True, id="capability heat_pump"),
        pytest.param("x/meter", False, id="other device"),
        pytest.param("x/gone", False, id="unknown profile"),
    ],
)
def test_heat_pump_device(profile: str, found: bool) -> None:
    """The house model learns from the first device whose profile has the capability heat_pump."""
    profiles = {"x/hp": {"capabilities": ["heat_pump"]}, "x/meter": {"capabilities": ["appliance"]}}
    device = DeviceConfig(id="d", ha_device_id="d", profile=profile, profile_version=1, name="D")

    assert (heat_pump_device(SunflowConfig(devices=[device]), profiles) is device) is found


# Endpoints


def _api(tmp_path: Path, library: Any, learning: Learning | None, devices: int = 1) -> TestClient:
    app = FastAPI()
    app.include_router(heat_pump_router, prefix="/api/v1")
    app.include_router(house_router, prefix="/api/v1")
    app.state.profiles = library
    app.state.store = ConfigStore(tmp_path / "config", lambda: library.profiles)
    config = app.state.store.load()
    config.devices = [
        DeviceConfig(id=f"hp{i}", ha_device_id=f"x{i}", profile=PROFILE, profile_version=3, name=f"HPSU {i}") for i in range(devices)
    ]
    config.house = HouseSettings(rooms=[Room(entity_id=ROOM)])
    app.state.store.save(config)
    relearned: list[int] = []
    app.state.runner = SimpleNamespace(
        heat_pump=None, shadow=None, house=learning, relearn_house=lambda: relearned.append(1), request_replan=lambda: None,
    )
    app.state.relearned = relearned
    return TestClient(app)


@pytest.fixture(scope="module")
def learned() -> Learning:
    return learn(house_rows(5 * 24), WEIGHTS, NOW)


def test_house_model_endpoint(tmp_path: Path, library: Any, learned: Learning) -> None:
    client = _api(tmp_path, library, learned, devices=2)

    answer = client.get("/api/v1/heat_pump/hp0/house_model").json()
    other = client.get("/api/v1/heat_pump/hp1/house_model").json()

    assert answer["state"] == "ok" and answer["plan_model"] == "auto"
    assert (answer["show_hours"], answer["usable_hours"]) == (24, 48)
    assert answer["used"] is True and answer["reason"] is None
    model = answer["model"]
    assert model["loss_w_per_k"] == pytest.approx(150, rel=0.15) and model["loss_spread"] > 0
    assert model["house_kwh_per_k"] > 0 and model["surface_kwh_per_k"] > 0 and model["water_w_per_k"] > 0
    assert model["test_rms_k"] < model["stay_rms_k"] and model["usable"]
    assert model["data"]["room_hours"] >= 100 and model["data"]["days"] == pytest.approx(5, abs=0.1)
    assert answer["chart"]
    assert (other["state"], other["first"]) == ("other_heat_pump", "HPSU 0")
    assert client.get("/api/v1/heat_pump/nope/house_model").status_code == 404


def test_house_model_endpoint_while_learning(tmp_path: Path, library: Any) -> None:
    learning = _api(tmp_path / "a", library, Learning(state="learning", room_hours=10, computed_at=NOW))
    pending = _api(tmp_path / "b", library, None)

    answer = learning.get("/api/v1/heat_pump/hp0/house_model").json()

    assert (answer["state"], answer["room_hours"], answer["used"]) == ("learning", 10, False)
    assert pending.get("/api/v1/heat_pump/hp0/house_model").json()["state"] == "pending"


def test_the_setting_off_keeps_the_model_out(tmp_path: Path, library: Any, learned: Learning) -> None:
    client = _api(tmp_path, library, learned)

    assert client.put("/api/v1/house/climate", json={"plan_model": "off"}).json()["plan_model"] == "off"
    answer = client.get("/api/v1/heat_pump/hp0/house_model").json()

    assert (answer["plan_model"], answer["used"]) == ("off", False)
    assert client.put("/api/v1/house/climate", json={"plan_model": "sometimes"}).status_code == 422


def test_preview_endpoint(tmp_path: Path, library: Any, learned: Learning, monkeypatch: pytest.MonkeyPatch) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW.astimezone(tz)

    monkeypatch.setattr("sunflow.api.heat_pump.datetime", Clock)
    client = _api(tmp_path, library, learned, devices=2)

    answer = client.get("/api/v1/heat_pump/hp0/preview", params={"days": 3, "slope": 0.5, "shift_k": 1.0, "setpoint_c": 23.0}).json()
    other = client.get("/api/v1/heat_pump/hp1/preview").json()
    too_long = client.get("/api/v1/heat_pump/hp0/preview", params={"days": 8})

    assert answer["state"] == "ok"
    assert len(answer["times"]) == len(answer["outside_c"]) == len(answer["entered"]["room_c"]) == 72
    assert answer["entered"]["slope"] == 0.5 and answer["entered"]["shift_k"] == 1.0 and answer["setpoint_c"] == 23.0
    assert answer["outside_source"] == "measured"
    assert answer["model"]["rms_k"] < 0.05 and answer["past"] and len(answer["past"]) <= 49
    assert answer["model"]["slope"] > 0
    assert other["state"] == "not_enough_data"
    assert too_long.status_code == 422


def test_preview_without_a_model(tmp_path: Path, library: Any) -> None:
    answer = _api(tmp_path, library, Learning(state="learning", computed_at=NOW)).get("/api/v1/heat_pump/hp0/preview").json()

    assert answer["state"] == "not_enough_data"


# House settings


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"rooms": [{"entity_id": "sensor.a", "weight": 0}]}, id="weight zero"),
        pytest.param({"rooms": [{"weight": 1}]}, id="no sensor"),
    ],
)
def test_house_settings_refused(body: dict[str, object]) -> None:
    """A room without a sensor or weight is refused."""
    with pytest.raises(ValidationError):
        HouseSettings.model_validate(body)


def test_house_settings_api_learns_anew(tmp_path: Path, library: Any) -> None:
    """Rooms are stored; the house is learned anew, the setting of the planning kept."""
    client = _api(tmp_path, library, None)
    client.put("/api/v1/house/climate", json={"plan_model": "on"})
    body = HouseSettings(rooms=[Room(entity_id="sensor.wohnzimmer", weight=20)], heat_entity="sensor.heat").model_dump()

    stored = client.put("/api/v1/config/house", json=body).json()

    assert stored["rooms"] == body["rooms"] and stored["climate"]["plan_model"] == "on"
    assert client.app.state.relearned == [1]  # type: ignore[attr-defined]


def test_old_house_fields_survive(tmp_path: Path) -> None:
    """Fields of the former control stay in the file; rooms are kept; new fields get their default."""
    old_house = {
        "rooms": [{"entity_id": "sensor.a", "weight": 2.0, "reference": True}, {"entity_id": "sensor.b", "weight": 1.0}],
        "heat_entity": None,
        "comfort_min": 20.5, "comfort_max": 22.0, "night_min": 18.0, "night_start": "22:00", "night_end": "06:00",
    }
    store = ConfigStore(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"version": CONFIG_VERSION, "house": old_house}))

    config = store.load()
    store.save(config)

    assert [(r.entity_id, r.weight) for r in config.house.rooms] == [("sensor.a", 2.0), ("sensor.b", 1.0)]
    assert json.loads((tmp_path / "config.json").read_text())["house"] == old_house | {
        "outside_source": "auto",
        "climate": {"setpoint_c": 21.0, "mode": "heat", "preheat_k": 1.0, "lower_k": 0.0, "plan_model": "auto"},
        "heating_surface": "underfloor",
        "fast_change": {"start_k": 0.8, "start_min": 20, "settle_k": 0.5, "settle_min": 30, "max_h": 2.0},
    }


@pytest.mark.parametrize("stored", ["sometimes", None, 3])
def test_unknown_plan_model_counts_as_auto(stored: object) -> None:
    assert HouseSettings.model_validate({"climate": {"plan_model": stored}}).climate.plan_model == "auto"
