"""Heating curve, feedforward of the flow target and the house model from heat_pump_samples
(design/heat_pump.md, 8.1, 9.2, 9.3, 16 step 3 part 2), with synthetic data over several days."""

from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
import random
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sunflow.api.house import router as house_router
from sunflow.config.models import DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.heat_pump.curve import Curve, learn, select_points
from sunflow.heat_pump.defaults import CURVE_MIN_DAYS, HEATING_SURFACES, curve_exponent
from sunflow.heat_pump.feedforward import (
    BASE,
    HEAT_RECENT,
    HOUSE_MODEL,
    HouseContext,
    RecentHeat,
    feedforward,
    house_heat_w,
)
from sunflow.heat_pump.samples import Minute
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from sunflow.heat_pump.states import Evaluation, Reading
from sunflow.planning.house_model import (
    HA_HOURLY,
    MIN_DAYS,
    SAMPLES,
    HouseData,
    HouseModel,
    choose_samples,
    fit,
    learn_house,
    sample_hours,
    samples_house_data,
)
from sunflow.shadow.store import ShadowStore

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
FIVE = timedelta(minutes=5)
MIN = timedelta(minutes=1)
ROOM = "sensor.wohnzimmer"
WEIGHTS = {ROOM: 1.0}
PROFILE = "esphome/rotex_hpsu_can"
# The heating surfaces of the synthetic house: underfloor, K = 180 W/K^1.1, spread 2 K + 1 K per kW
K_TRUE, N_TRUE, S0, S1 = 180.0, 1.1, 2.0, 1.0
# Heat loss of the house (W/K) and its room temperature
LOSS_W_PER_K, ROOM_C = 150.0, 21.0


def _outside(moment: datetime) -> float:
    """Outside swings over the day and from day to day."""
    hours = moment.timestamp() / 3600
    return 3 + 4 * math.sin(hours / 24 * 2 * math.pi) + 2 * math.sin(hours / 77 * 2 * math.pi)


def _row(start: datetime, rng: random.Random | None = None, noise: float = 0.0, **changes: Any) -> dict[str, Any]:
    """A 5 minute row of valid heating: the heat the house loses, flow and return from the true curve."""
    outside = _outside(start)
    room = ROOM_C + (rng.gauss(0, 0.05) if rng and noise else 0.0)
    heat = LOSS_W_PER_K * (room - outside)
    water = room + (heat / K_TRUE) ** (1 / N_TRUE)
    spread = S0 + S1 * heat / 1000
    tv, tr = water + spread / 2, water - spread / 2
    if rng and noise:
        tv += rng.gauss(0, noise)
        tr += rng.gauss(0, noise)
        heat *= 1 + rng.gauss(0, noise / 4)
    row = {
        "device_id": "hp", "start": start, "samples": 5, "tv_c": tv, "tr_c": tr, "tvbh_c": tv, "tv_max_c": tv + 0.2,
        "heat_w": heat, "heat_source": "measured", "outside_c": outside, "bypass_pct": 0.0, "setpoint_c": tv + 1,
        "max_setpoint_c": tv + 1, "written_c": None, "compressor": 1.0, "program": "heating", "activity": "heating",
        "strategy": "fixed", "error": "err_0", "rooms": {ROOM: room}, "events": [],
        "flags": {"valid": True, "frozen": [], "after_run": [], "blocked": False, "fault": False, "self_limited": False, "ventilation": []},
    }
    flags = changes.pop("flags", {})
    row.update(changes)
    row["flags"] = row["flags"] | flags
    return row


def _days(days: float, noise: float = 0.0, seed: int = 1, end: datetime = NOW) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    count = int(days * 24 * 12)
    return [_row(end - (count - i) * FIVE, rng, noise) for i in range(count)]


# Learning the curve (9.2)


def test_curve_is_found() -> None:
    """Ten days without noise: K and the spread model come back, and it beats "the flow stays"."""
    curve = learn(_days(10), WEIGHTS, "underfloor", NOW)

    assert curve.k == pytest.approx(K_TRUE, rel=0.01)
    # The ridge holds the slope back a little
    assert (curve.spread0_k, curve.spread_k_per_kw) == (pytest.approx(S0, abs=0.1), pytest.approx(S1, abs=0.05))
    assert curve.n == N_TRUE
    assert curve.state == "ready" and curve.usable
    assert curve.error_k < curve.baseline_k
    assert curve.days >= 10 and curve.intervals > 2800
    # The curve gives back the flow for a heat at a room temperature
    heat = 3000.0
    assert curve.flow_c(21.0, heat) == pytest.approx(21.0 + (heat / K_TRUE) ** (1 / N_TRUE) + (S0 + S1 * 3) / 2, abs=0.05)


def test_curve_with_noise() -> None:
    """Flow and return ±0.2 K, heat ±5 %, room ±0.05 K: still found within a few percent and usable."""
    curve = learn(_days(14, noise=0.2), WEIGHTS, "underfloor", NOW)

    assert curve.k == pytest.approx(K_TRUE, rel=0.05)
    assert curve.spread0_k == pytest.approx(S0, abs=0.3)
    assert curve.spread_k_per_kw == pytest.approx(S1, abs=0.15)
    assert curve.usable
    # 5 % heat noise alone is about 0.5 K of flow
    assert curve.error_k < 1.0 < curve.baseline_k


def test_too_little_data_is_not_used() -> None:
    """Two days: computed and shown, but too little data; the flow target keeps the base."""
    curve = learn(_days(2), WEIGHTS, "underfloor", NOW)

    assert curve.k is not None
    assert curve.state == "learning" and not curve.usable
    assert curve.train_days < CURVE_MIN_DAYS
    feed = feedforward(curve, 21.0, 3000.0, None, "underfloor")
    assert (feed.source, feed.reason, feed.curve_c) == (BASE, "curve_learning", None)


def test_no_data_no_curve() -> None:
    curve = learn([], WEIGHTS, "underfloor", NOW)

    assert (curve.k, curve.intervals, curve.state) == (None, 0, "learning")


def test_left_out_intervals_count_nowhere() -> None:
    """Hot water, after-run, ventilation, not valid, compressor not long enough on: left out, even with nonsense values."""
    rows = _days(10)
    by_start = {r["start"]: r for r in rows}
    bad = 0
    for i, row in enumerate(rows):
        day_minute = i % 288
        if day_minute == 100:
            row.update(activity="hot_water", tv_c=52.0, tr_c=45.0, heat_w=8000.0)
            row["flags"] = row["flags"] | {"valid": False, "frozen": ["activity_hot_water"]}
        elif day_minute == 101:
            row.update(tv_c=45.0, heat_w=200.0)
            row["flags"] = row["flags"] | {"valid": False, "after_run": ["hot_water"], "frozen": ["after_hot_water"]}
        elif day_minute == 150:
            row.update(rooms={ROOM: 15.0}, tv_c=60.0)
            row["flags"] = row["flags"] | {"ventilation": [ROOM]}
        elif day_minute == 200:
            # Compressor off for one interval: this one and the next three are left out
            row.update(compressor=0.0, heat_w=0.0, tv_c=24.0, tr_c=23.5)
        elif day_minute in (201, 202, 203):
            row.update(tv_c=row["tv_c"] + 6.0)
        else:
            continue
        bad += 1
    assert len(by_start) == len(rows)

    points = select_points(rows, WEIGHTS)
    curve = learn(rows, WEIGHTS, "underfloor", NOW)

    # The first three intervals have no run before them either
    assert len(points) == len(rows) - bad - 3
    assert curve.k == pytest.approx(K_TRUE, rel=0.01)
    assert curve.usable


def test_rooms_not_configured_do_not_count() -> None:
    rows = _days(1)

    assert select_points(rows, {"sensor.other": 1.0}) == []


def test_implausible_spread() -> None:
    """A spread of 20 K is not believable: the curve is not used."""
    rows = _days(10)
    for row in rows:
        row["tr_c"] = row["tv_c"] - 20.0

    curve = learn(rows, WEIGHTS, "underfloor", NOW)

    assert curve.state == "implausible"
    assert not curve.usable


def test_not_better_than_the_flow_stays() -> None:
    """The flow stays at 30 °C while the heat varies at random: the curve predicts worse than "it stays"."""
    rng = random.Random(3)
    rows = _days(10)
    for row in rows:
        row.update(tv_c=30.0 + rng.gauss(0, 0.1), tr_c=26.0 + rng.gauss(0, 0.1), heat_w=rng.uniform(1000, 5000))

    curve = learn(rows, WEIGHTS, "underfloor", NOW)

    assert curve.state == "unsure"
    assert curve.error_k >= curve.baseline_k


def test_cache_round_trip() -> None:
    curve = learn(_days(5), WEIGHTS, "mixed", NOW, key="k")

    again = Curve.from_dict(json.loads(json.dumps(curve.as_dict())))

    assert again == curve
    assert Curve.from_dict({"format": 99}) is None
    assert Curve.from_dict("nonsense") is None


# Heating surface type (8.1)


def test_surface_type_missing_unknown_and_its_exponent() -> None:
    assert HouseSettings().heating_surface == "underfloor"
    # A type of a newer version: the default, without an error
    assert HouseSettings.model_validate({"heating_surface": "floor_and_wall"}).heating_surface == "underfloor"
    assert HouseSettings.model_validate({"heating_surface": None}).heating_surface == "underfloor"
    assert [curve_exponent(s) for s in ("underfloor", "mixed", "radiator", "unknown")] == [1.1, 1.2, 1.3, 1.1]
    assert set(HEATING_SURFACES) == {"underfloor", "mixed", "radiator"}


def test_surface_type_change_changes_the_start_value_and_stales_the_curve() -> None:
    rows = _days(10)
    floor = learn(rows, WEIGHTS, "underfloor", NOW)
    radiators = learn(rows, WEIGHTS, "radiator", NOW)

    assert (floor.n, radiators.n) == (1.1, 1.3)
    # Until learned anew with the new type the curve is not used
    feed = feedforward(floor, 21.0, 3000.0, None, "radiator")
    assert (feed.source, feed.reason) == (BASE, "curve_stale")


def test_surface_type_saved_and_loaded_through_the_api(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(house_router, prefix="/api/v1")
    app.state.store = ConfigStore(tmp_path / "config.json")
    client = TestClient(app)

    body = HouseSettings(rooms=[Room(entity_id=ROOM)], heating_surface="radiator").model_dump()
    assert client.put("/api/v1/config/house", json=body).json()["heating_surface"] == "radiator"
    assert client.get("/api/v1/config/house").json()["heating_surface"] == "radiator"
    assert ConfigStore(tmp_path / "config.json").load().house.heating_surface == "radiator"
    assert client.put("/api/v1/config/house", json=body | {"heating_surface": "wall"}).json()["heating_surface"] == "underfloor"


# Heat of the last 3 h and the order of the sources (8.1)


def _minute(at: datetime, heat: float | None = 2000.0, valid: bool = True, frozen: tuple[str, ...] = (), setpoint: float = 31.0, outside: float | None = 5.0) -> Minute:
    activity = "heating" if valid else "idle" if frozen == ("activity_idle",) else "hot_water"
    reading = Reading(program="heating", activity=activity, strategy="fixed", values={"flow_setpoint": setpoint})
    return Minute(at=at, reading=reading, evaluation=Evaluation(valid=valid, frozen=() if valid else frozen),
                  heat_w=heat, heat_source="measured", outside_c=outside, rooms={})


def test_recent_heat_with_pauses() -> None:
    """1 h at 3 kW, 1 h compressor pause, 1 h at 3 kW: 2 kW over 3 h."""
    recent = RecentHeat()
    for i in range(180):
        pause = 60 <= i < 120
        recent.add(_minute(NOW + i * MIN, heat=None if pause else 3000.0, valid=not pause, frozen=("activity_idle",)), aired=False)

    mean, seconds = recent.mean(NOW + 179 * MIN)

    assert mean == pytest.approx(2000.0)
    assert seconds == 180 * 60


def test_recent_heat_too_few_valid_minutes() -> None:
    recent = RecentHeat()
    for i in range(119):
        recent.add(_minute(NOW + i * MIN), aired=False)

    assert recent.mean(NOW + 119 * MIN) == (None, 119 * 60)
    recent.add(_minute(NOW + 119 * MIN), aired=False)
    assert recent.mean(NOW + 119 * MIN)[0] == pytest.approx(2000.0)
    # Older than 3 h drops out
    assert recent.mean(NOW + 4 * 60 * MIN)[0] is None


def test_recent_heat_leaves_out_hot_water_ventilation_and_block() -> None:
    recent = RecentHeat()
    for i in range(120):
        recent.add(_minute(NOW + i * MIN), aired=False)
    for i in range(120, 150):
        recent.add(_minute(NOW + i * MIN, heat=8000.0, valid=False, frozen=("activity_hot_water",)), aired=False)
    for i in range(150, 160):
        recent.add(_minute(NOW + i * MIN, heat=0.0, valid=False, frozen=("activity_idle", "blocked")), aired=False)
    for i in range(160, 170):
        recent.add(_minute(NOW + i * MIN, heat=9000.0), aired=True)

    assert recent.mean(NOW + 170 * MIN) == (pytest.approx(2000.0), 120 * 60)


def test_recent_heat_seeded_from_the_table() -> None:
    rows = [_row(NOW - (36 - i) * FIVE, heat_w=1800.0) for i in range(36)]
    rows[0]["flags"]["ventilation"] = [ROOM]
    rows[1].update(heat_w=None, compressor=0.0)
    rows[1]["flags"] = rows[1]["flags"] | {"valid": False, "frozen": ["activity_idle"]}
    recent = RecentHeat()

    recent.seed(rows, NOW)

    mean, seconds = recent.mean(NOW)
    assert seconds == 35 * 5 * 60
    assert mean == pytest.approx(1800.0 * 34 / 35)
    assert recent.seeded


def _ready_curve(surface: str = "underfloor", **changes: Any) -> Curve:
    values: dict[str, Any] = {
        "surface": surface, "n": curve_exponent(surface), "k": K_TRUE, "spread0_k": S0, "spread_k_per_kw": S1,
        "intervals": 2000, "days": 10, "held": 500, "error_k": 0.1, "baseline_k": 0.8, "first": NOW - timedelta(days=10),
        "last": NOW, "computed_at": NOW, "train_days": 8, "typical_w": 2500.0,
    } | changes
    return Curve(**values)


def _house_model(**changes: Any) -> HouseModel:
    values: dict[str, Any] = {"a": 0.02, "b": 0.04, "c": 0.3, "d": 0.02, "days": 14, "samples": 300, "error_k": 0.2, "baseline_k": 0.5} | changes
    return HouseModel(**values)


def test_order_of_the_sources() -> None:
    curve = _ready_curve()

    assert feedforward(curve, 21.0, 3000.0, 2000.0).source == HOUSE_MODEL
    assert feedforward(curve, 21.0, None, 2000.0).source == HEAT_RECENT
    assert (feedforward(curve, 21.0, None, None).source, feedforward(curve, 21.0, None, None).reason) == (BASE, "no_heat")
    assert feedforward(None, 21.0, 3000.0, 2000.0).reason == "no_curve"
    assert feedforward(_ready_curve(error_k=0.9), 21.0, 3000.0, 2000.0).reason == "curve_unsure"
    feed = feedforward(curve, 21.0, None, 2000.0)
    assert feed.curve_c == pytest.approx(curve.flow_c(21.0, 2000.0))


@pytest.mark.parametrize("heat", [-500.0, 0.0, 150.0])
def test_no_heat_needed_means_base(heat: float) -> None:
    feed = feedforward(_ready_curve(), 21.0, heat, 2000.0)

    assert (feed.source, feed.reason, feed.curve_c) == (BASE, "heat_low", None)


def test_heat_from_the_house_model() -> None:
    """Q* = (a·(target − outside) − d − c·S)/b: 0.02·16 − 0.02 = 0.3 K/h → 7.5 kW; sun half 0.15 K/h less."""
    house = HouseContext(model=_house_model(), sun_entity="sensor.sun", sun_bright=800.0)

    assert house_heat_w(house, 21.0, 5.0, None) == pytest.approx(7500.0)
    assert house_heat_w(house, 21.0, 5.0, 400.0) == pytest.approx(3750.0)
    assert house_heat_w(house, 21.0, None, None) is None
    # Not better than "it stays": not used
    assert house_heat_w(HouseContext(model=_house_model(error_k=0.9)), 21.0, 5.0, None) is None
    assert house_heat_w(None, 21.0, 5.0, None) is None


# Shadow control with the curve


class Shadow:
    def __init__(self, tmp_path: Path) -> None:
        self.device = DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=2, name="HPSU CAN")
        self.config = SunflowConfig(devices=[self.device], house=HouseSettings(rooms=[Room(entity_id=ROOM)]))
        self.shadow = FlowShadow(HeatPumpStateStore(tmp_path))
        self.room = "21.0"

    async def tick(self, at: datetime, minute: Minute) -> dict[str, Any]:
        states = {ROOM: {"state": self.room, "last_updated": NOW.isoformat(), "last_changed": NOW.isoformat()}}
        await self.shadow.tick(self.config, states, {"hp": (self.device, minute)}, at)
        return self.shadow.flow_status("hp")


async def test_switch_from_base_to_curve_keeps_the_correction(tmp_path: Path) -> None:
    """After 2 h of valid heating the curve takes over with the heat of the last hours; the correction stays."""
    shadow = Shadow(tmp_path)
    shadow.shadow.set_curve("hp", _ready_curve())
    # A correction from before (e.g. run to its limit in shadow)
    shadow.shadow._correction("hp", "fixed").value_k = 1.5
    flows = [await shadow.tick(NOW + i * MIN, _minute(NOW + i * MIN, heat=2500.0)) for i in range(120)]

    # The 120th minute completes 2 h of valid heating
    before, after = flows[118], flows[119]
    assert (before["source"], before["source_reason"]) == (BASE, "no_heat")
    assert before["target_c"] == pytest.approx(31.0 + 1.5)
    assert after["source"] == HEAT_RECENT
    assert after["correction_k"] == before["correction_k"] == 1.5
    assert after["curve_c"] == pytest.approx(_ready_curve().flow_c(21.0, 2500.0), abs=0.01)
    assert after["target_c"] == pytest.approx(after["curve_c"] + after["correction_k"], abs=0.01)
    assert after["heat_needed_w"] == 2500
    assert after["curve"]["state"] == "ready" and after["curve"]["shadow"]
    events = shadow.shadow.source_events
    assert [(d, e["kind"], e["from"], e["to"]) for d, e in events] == [("hp", "flow_source", BASE, HEAT_RECENT)]


async def test_house_model_ranks_first_and_curve_survives_a_restart(tmp_path: Path) -> None:
    shadow = Shadow(tmp_path)
    shadow.shadow.set_curve("hp", _ready_curve())
    shadow.shadow.house = HouseContext(model=_house_model())

    flow = await shadow.tick(NOW, _minute(NOW, outside=5.0))

    assert (flow["source"], flow["heat_needed_w"]) == (HOUSE_MODEL, 7500)
    restarted = FlowShadow(HeatPumpStateStore(tmp_path))
    assert restarted.curves["hp"] == _ready_curve()
    stored = json.loads((tmp_path / "heat_pump_state.json").read_text())
    assert stored["devices"]["hp"]["source"] == HOUSE_MODEL


async def test_recorder_notes_the_source_change_in_events(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    pump.recorder.flow.set_curve("hp", _ready_curve(key=pump.recorder.curve_key(pump.config)))

    async def keep(*_: Any) -> None:
        """The curve stays as set (the table holds only these minutes)."""

    pump.recorder.learn_curves = keep  # type: ignore[method-assign]
    try:
        await pump.minutes(NOW, 126)
        rows = pump.store.heat_pump_samples("hp", NOW)
    finally:
        pump.store.close()

    events = [e for row in rows for e in row["events"] if e["kind"] == "flow_source"]
    assert [(e["from"], e["to"]) for e in events] == [(BASE, HEAT_RECENT)]
    assert pump.recorder.status()["hp"]["flow"]["source"] == HEAT_RECENT


async def test_recorder_learns_the_curve_from_the_table(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        pump.store.store_heat_pump_samples(_days(10))
        # The tick learns in the background
        await pump.tick(NOW)
        await pump.recorder._curve_task
        first = pump.recorder.flow.curves["hp"]
        # Within the hour nothing is computed anew; a new type is evaluated at once
        await pump.recorder.learn_curves(pump.config, NOW + timedelta(minutes=30))
        assert pump.recorder.flow.curves["hp"] is first
        pump.config.house.heating_surface = "radiator"
        await pump.recorder.learn_curves(pump.config, NOW + timedelta(minutes=31))
    finally:
        pump.store.close()

    assert first.usable and first.k == pytest.approx(K_TRUE, rel=0.01)
    assert pump.recorder.flow.curves["hp"].n == 1.3
    # The data in the table stay
    assert pump.recorder.flow.curves["hp"].intervals == first.intervals


# House model from heat_pump_samples (9.3)


def _house_rows(days: int, end: datetime = NOW, seed: int = 2) -> list[dict[str, Any]]:
    """5 minute rows of a 1R1C house: τ 50 h, 25 kWh/K; heating 2 h on, 1 h pause; a hot water hour a day."""
    rng = random.Random(seed)
    rows = []
    inside = 21.0
    count = days * 288
    for i in range(count):
        start = end - (count - i) * FIVE
        outside = _outside(start)
        heating = (i // 12) % 3 != 2
        hot_water = (i // 12) % 24 == 5
        heat = 3600.0 if heating and not hot_water else 0.0
        changes: dict[str, Any] = {"rooms": {ROOM: inside + rng.gauss(0, 0.01)}, "outside_c": outside, "heat_w": heat,
                                   "compressor": 1.0 if heat else 0.0}
        if hot_water:
            changes |= {"activity": "hot_water", "heat_w": 7000.0, "flags": {"valid": False, "frozen": ["activity_hot_water"]}}
        elif not heating:
            changes |= {"activity": "idle", "flags": {"valid": False, "frozen": ["activity_idle"]}}
        rows.append(_row(start, **changes))
        # Per 5 minutes: a = 0.02/h, b = 0.04 K/kWh
        inside += (0.02 * (outside - inside) + 0.04 * heat / 1000) / 12
    return rows


def test_hours_from_samples_leave_out_hot_water_and_ventilation() -> None:
    rows = _house_rows(2)
    rows[30]["flags"] = rows[30]["flags"] | {"ventilation": [ROOM]}

    hours, excluded, source = sample_hours(rows, WEIGHTS)

    assert source == "measured"
    # A hot water hour a day plus the aired one
    assert excluded == 2 + 1
    assert len(hours) == 48 - excluded
    heating_hour = next(v for h, v in hours.items() if v["heat"] and v["heat"] > 3)
    assert heating_hour["heat"] == pytest.approx(3.6)


def test_house_model_learned_from_samples() -> None:
    rows = _house_rows(14)
    ha = HouseData(rows=[], per_room={}, inside={}, heat_source=None, outside_entity="sensor.out", sun_entity=None)
    config = SunflowConfig(house=HouseSettings(rooms=[Room(entity_id=ROOM)]))

    data = samples_house_data(rows, config, ha, coverage_days=12)
    model = fit(data.rows, NOW)

    assert (data.source, data.coverage_days, data.heat_source) == (SAMPLES, 12, "measured")
    assert model is not None and model.useful
    assert model.tau_h == pytest.approx(50, rel=0.1)
    assert model.capacity_kwh_per_k == pytest.approx(25, rel=0.1)


def test_valid_days_of_the_table(tmp_path: Path) -> None:
    store = ShadowStore(tmp_path)
    try:
        rows = _days(3, end=NOW.replace(hour=0))
        # One day aired, a broken row from another version
        for row in rows:
            if row["start"].date() == (NOW - timedelta(days=2)).date():
                row["flags"]["ventilation"] = [ROOM]
        store.store_heat_pump_samples(rows)
        store._db.execute("INSERT INTO heat_pump_samples (device_id, start, samples, flags) VALUES ('hp', '2026-01-01T00:00:00+00:00', 5, 'x')")
        assert store.heat_pump_valid_days("hp", 3600) == 2
        assert store.heat_pump_valid_days("other", 3600) == 0
    finally:
        store.close()


def test_choice_of_the_source() -> None:
    useful, unsure = _house_model(), _house_model(error_k=0.9)

    assert choose_samples(useful, useful, kept=False)
    assert choose_samples(unsure, None, kept=False)
    assert choose_samples(unsure, unsure, kept=False)
    # Not worse in use: a useful model from Home Assistant is not replaced by an unsure one
    assert not choose_samples(unsure, useful, kept=False)
    # Once chosen it stays, unless the samples give no model at all
    assert choose_samples(unsure, useful, kept=True)
    assert not choose_samples(None, useful, kept=True)


class _NoHistory:
    """Home Assistant without any history: the hourly means are empty."""

    async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
        return {}


async def test_house_model_switches_after_seven_days(tmp_path: Path) -> None:
    """Below MIN_DAYS of valid heating Home Assistant's hourly means count; from then on the samples."""
    store = ShadowStore(tmp_path)
    config = SunflowConfig(
        devices=[DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=2, name="HPSU CAN")],
        house=HouseSettings(rooms=[Room(entity_id=ROOM)]),
    )
    config.learning_sources.outdoor_temperature = "sensor.out"
    profiles = {PROFILE: {"capabilities": ["heat_pump"]}}
    try:
        store.store_heat_pump_samples(_house_rows(MIN_DAYS - 1, end=NOW.replace(hour=0)))
        before = await learn_house(_NoHistory(), config, profiles, NOW, None, store)  # type: ignore[arg-type]
        store.store_heat_pump_samples(_house_rows(14, end=NOW.replace(hour=0)))
        after = await learn_house(_NoHistory(), config, profiles, NOW, None, store)  # type: ignore[arg-type]
    finally:
        store.close()

    assert (before.data.source, before.data.coverage_days, before.model) == (HA_HOURLY, MIN_DAYS - 1, None)
    assert (after.data.source, after.data.coverage_days) == (SAMPLES, 14)
    assert after.model is not None and after.model.useful
