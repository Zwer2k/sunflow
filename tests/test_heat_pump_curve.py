"""Heating curve flow = R + S·(R − A) + P (design/heat_pump.md, 8): start values (stored, the heat pump's slope,
the heating surface type's), learning pulled to them, share of the learned hours, the flow target from curve
and correction, no jump when the curve changes, the API for the start values; with synthetic data."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import random
from types import SimpleNamespace
from typing import Any

import pytest
from sunflow.config.models import DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.heat_pump.curve import FROM_HEAT_PUMP, FROM_SURFACE, FROM_USER, Curve, Start, fit, learn, select_points, start_curve, start_values
from sunflow.heat_pump.defaults import CURVE_PRIOR_HOURS, HEATING_SURFACES, curve_slope
from sunflow.heat_pump.samples import Minute
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore
from sunflow.heat_pump.states import Evaluation, Reading

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
FIVE = timedelta(minutes=5)
MIN = timedelta(minutes=1)
ROOM = "sensor.wohnzimmer"
WEIGHTS = {ROOM: 1.0}
PROFILE = "esphome/rotex_hpsu_can"
# The house the rows come from: this curve keeps the room at its setpoint
S_TRUE, P_TRUE, SETPOINT = 0.8, 1.5, 21.0
START = Start(slope=0.5, shift_k=0.0, source=FROM_HEAT_PUMP)


def _curve(slope: float, shift: float, start_slope: float = 0.5, start_shift: float = 0.0, hours: int = 50) -> Curve:
    return Curve(slope=slope, shift_k=shift, start_slope=start_slope, start_shift_k=start_shift, start_source=FROM_HEAT_PUMP,
                 hours=hours, learned_share=0.5, computed_at=NOW)


def _rows(days: float, noise: float = 0.0, seed: int = 1, room_offset: float = 0.0, **changes: Any) -> list[dict[str, Any]]:
    """5 minute rows of valid heating at the setpoint, outside swinging between −4 and 10 °C."""
    rng = random.Random(seed)
    count = int(days * 288)
    rows = []
    for i in range(count):
        start = NOW - (count - i) * FIVE
        outside = 3 + 7 * ((i // 36) % 5 - 2) / 2
        flow = SETPOINT + S_TRUE * (SETPOINT - outside) + P_TRUE + (rng.gauss(0, noise) if noise else 0.0)
        row = {
            "start": start.isoformat(), "samples": 5, "setpoint_c": flow, "outside_c": outside, "compressor": 1.0,
            "room_setpoint_c": SETPOINT, "rooms": {ROOM: SETPOINT + room_offset},
            "flags": {"valid": True, "after_run": [], "ventilation": []},
        }
        rows.append(row | changes)
    return rows


# Start values


def test_start_values_stored_else_the_heat_pump_else_the_surface_type() -> None:
    assert start_values(0.3, -0.5, "user", "underfloor", 0.5) == Start(slope=0.3, shift_k=-0.5, source=FROM_USER)
    assert start_values(None, None, None, "underfloor", 0.5) == START
    # Not usable (missing, 0 = off, out of range): the heating surface type's
    assert start_values(None, None, None, "underfloor", None) == Start(slope=0.6, shift_k=0.0, source=FROM_SURFACE)
    assert start_values(None, None, None, "radiator", 0.0).slope == 1.4
    assert [curve_slope(s) for s in ("underfloor", "mixed", "radiator", "unknown")] == [0.6, 1.0, 1.4, 0.6]
    assert set(HEATING_SURFACES) == {"underfloor", "mixed", "radiator"}


def test_curve_formula() -> None:
    curve = start_curve(START, NOW)

    # 23 + 0.5 · (23 − 2): a setpoint 1 K higher raises the flow by 1.5 K
    assert curve.flow_c(23.0, 2.0) == pytest.approx(33.5)
    assert curve.flow_c(24.0, 2.0) - curve.flow_c(23.0, 2.0) == pytest.approx(1.5)


# Learning


def test_learned_towards_the_house_with_enough_data() -> None:
    curve = learn(_rows(14), WEIGHTS, START, NOW)

    assert curve.hours == 14 * 24
    # 14 days of hours (about 240 weighted ones) against 24 on the start
    assert 0.85 < curve.learned_share < 0.95
    assert curve.slope == pytest.approx(S_TRUE, abs=0.05)
    assert curve.shift_k == pytest.approx(P_TRUE, abs=0.5)
    # The prior still pulls a little (24 virtual hours against about 240 weighted ones)
    assert curve.flow_c(SETPOINT, 0.0) == pytest.approx(SETPOINT + S_TRUE * SETPOINT + P_TRUE, abs=1.0)


def test_few_hours_stay_close_to_the_start() -> None:
    """A few hours pull only a little; with as many hours as the prior about halfway."""
    few = learn(_rows(2 / 24), WEIGHTS, START, NOW)
    some = learn(_rows(CURVE_PRIOR_HOURS / 24), WEIGHTS, START, NOW)

    start = start_curve(START, NOW)
    truth = SETPOINT + S_TRUE * (SETPOINT - 3) + P_TRUE
    assert abs(few.flow_c(SETPOINT, 3) - start.flow_c(SETPOINT, 3)) < 0.3 * abs(truth - start.flow_c(SETPOINT, 3))
    assert abs(some.flow_c(SETPOINT, 3) - truth) < abs(start.flow_c(SETPOINT, 3) - truth)
    nothing = learn([], WEIGHTS, START, NOW)
    assert (nothing.slope, nothing.learned_share) == (0.5, 0.0)
    assert some.learned_share == pytest.approx(0.5, abs=0.02)


def test_learning_pulls_to_a_shifted_start() -> None:
    """The prior lies on the start curve with its shift: without data the start values exactly."""
    start = Start(slope=0.3, shift_k=-1.0, source=FROM_USER)
    curve = learn([], WEIGHTS, start, NOW)

    assert (curve.slope, curve.shift_k) == (pytest.approx(0.3), pytest.approx(-1.0))


@pytest.mark.parametrize(
    ("changes", "offset"),
    [
        pytest.param({"flags": {"valid": False}}, 0.0, id="not valid"),
        pytest.param({"flags": {"valid": True, "ventilation": [ROOM]}}, 0.0, id="fast change"),
        pytest.param({"compressor": 0.5}, 0.0, id="compressor pausing"),
        pytest.param({"outside_c": None}, 0.0, id="no outside"),
        pytest.param({}, 0.5, id="room not at its setpoint"),
    ],
)
def test_hours_left_out(changes: dict[str, Any], offset: float) -> None:
    assert select_points(_rows(1, room_offset=offset, **changes), WEIGHTS) == []


def test_rows_without_their_setpoint_do_not_count() -> None:
    """Older rows did not keep the setpoint: the current one could be another, so they are left out."""
    assert select_points(_rows(1, room_setpoint_c=None), WEIGHTS) == []
    assert len(select_points(_rows(1), WEIGHTS)) == 24


def test_limits_of_the_fit() -> None:
    points = select_points(_rows(14), WEIGHTS)
    wild = [p.__class__(start=p.start, room_c=p.room_c, outside_c=p.outside_c, flow_c=p.flow_c + 30) for p in points]

    slope, shift, _share = fit(wild, START, NOW)

    assert 0.2 <= slope <= 3.0 and shift == 10.0


def test_cache_round_trip() -> None:
    curve = learn(_rows(3), WEIGHTS, START, NOW, key="k")

    assert Curve.from_dict(curve.as_dict()) == curve
    assert Curve.from_dict({"format": 1, "k": 200}) is None


# In the control


def _minute(at: datetime, outside: float | None = 2.0, slope: float | None = 0.5) -> Minute:
    reading = Reading(program="heating", activity="heating", strategy="fixed", values={"flow_setpoint": 30.0, "heating_curve_slope": slope})
    return Minute(at=at, reading=reading, evaluation=Evaluation(valid=True, frozen=()), heat_w=None, heat_source=None, outside_c=outside, rooms={})


class House:
    def __init__(self, tmp_path: Path) -> None:
        self.device = DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=3, name="HPSU CAN")
        self.config = SunflowConfig(devices=[self.device], house=HouseSettings(rooms=[Room(entity_id=ROOM)]))
        self.config.house.climate.setpoint_c = 23.0
        self.shadow = FlowShadow(HeatPumpStateStore(tmp_path))
        self.room = "23.0"

    async def tick(self, at: datetime, minute: Minute | None = None) -> dict[str, Any]:
        states = {ROOM: {"state": self.room, "last_updated": at.isoformat()}}
        await self.shadow.tick(self.config, states, {"hp": (self.device, minute or _minute(at))}, at)
        return self.shadow.flow_status("hp")


async def test_flow_target_from_the_curve_and_a_setpoint_change_acts_at_once(tmp_path: Path) -> None:
    house = House(tmp_path)
    flow = await house.tick(NOW)

    assert (flow["outside_c"], flow["curve_c"], flow["target_c"]) == (2.0, 33.5, 33.5)
    assert flow["curve"] == {"slope": 0.5, "shift_k": 0.0, "start_slope": 0.5, "start_shift_k": 0.0, "start_source": FROM_HEAT_PUMP,
                             "hours": 0, "learned_pct": 0, "computed_at": NOW.isoformat()}
    assert flow["heat_pump_slope"] == 0.5
    house.config.house.climate.setpoint_c = 24.0
    flow = await house.tick(NOW + MIN)
    assert flow["curve_c"] == pytest.approx(35.0)
    assert "setpoint_change" not in flow["frozen"]


async def test_outside_averaged_and_without_it_no_target(tmp_path: Path) -> None:
    house = House(tmp_path)
    await house.tick(NOW, _minute(NOW, outside=2.0))
    flow = await house.tick(NOW + MIN, _minute(NOW + MIN, outside=4.0))
    assert flow["outside_c"] == pytest.approx(3.0)

    gone = House(tmp_path / "x")
    flow = await gone.tick(NOW, _minute(NOW, outside=None))
    assert (flow["target_c"], flow["reason"]) == (None, "no_outside")


async def test_surface_type_slope_without_the_heat_pumps(tmp_path: Path) -> None:
    house = House(tmp_path)
    flow = await house.tick(NOW, _minute(NOW, slope=None))

    assert (flow["curve"]["start_slope"], flow["curve"]["start_source"]) == (0.6, FROM_SURFACE)


async def test_a_learned_curve_does_not_jump_the_flow_target(tmp_path: Path) -> None:
    house = House(tmp_path)
    before = await house.tick(NOW)
    house.shadow.set_curve("hp", _curve(0.6, 0.5))
    after = await house.tick(NOW + MIN)
    # Beyond the correction's limit the rest stays as a jump
    house.shadow.set_curve("hp", _curve(0.9, 2.0, hours=60))
    far = await house.tick(NOW + 2 * MIN)

    # The curve gives 23 + 0.6·21 + 0.5 = 36.1; the correction takes the difference
    assert after["curve_c"] == pytest.approx(36.1)
    assert (after["target_c"], after["correction_k"]) == (pytest.approx(before["target_c"]), pytest.approx(-2.6))
    assert far["correction_k"] == -3.0 and far["target_c"] > before["target_c"]


async def test_learned_curve_with_other_start_values_is_not_used(tmp_path: Path) -> None:
    """Other start values: the start curve until learned anew."""
    house = House(tmp_path)
    house.shadow.set_curve("hp", _curve(0.9, 0.0, start_slope=0.4))
    flow = await house.tick(NOW)
    assert flow["curve"]["slope"] == 0.5

    house.shadow.set_curve("hp", _curve(0.9, 0.0))
    assert (await house.tick(NOW + MIN))["curve"]["slope"] == 0.9
    house.shadow.set_curve("hp", _curve(0.9, 0.0, start_shift=1.0))
    assert (await house.tick(NOW + 2 * MIN))["curve"]["slope"] == 0.5


async def test_stored_start_values_apply_at_once_without_a_shift_of_the_correction(tmp_path: Path) -> None:
    """The user's start values win over the heat pump's slope and act at once: the flow target follows."""
    house = House(tmp_path)
    before = await house.tick(NOW)
    house.device.curve_slope_start, house.device.curve_shift_start, house.device.curve_start_source = 0.3, -1.0, "user"
    after = await house.tick(NOW + MIN)

    # 23 + 0.3 · 21 − 1 = 28.3
    assert (before["curve_c"], after["curve_c"]) == (33.5, pytest.approx(28.3))
    assert after["correction_k"] == pytest.approx(before["correction_k"])
    assert (after["curve"]["start_slope"], after["curve"]["start_shift_k"], after["curve"]["start_source"]) == (0.3, -1.0, FROM_USER)


async def test_recorder_learns_the_curve(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    pump.config.house.climate.setpoint_c = SETPOINT
    pump.states["heating_curve"] = ("0.5", None)
    try:
        rows = _rows(7)
        for row in rows:
            row |= {"device_id": "hp", "start": datetime.fromisoformat(row["start"])}
        pump.store.store_heat_pump_samples(rows)
        await pump.tick(NOW)
        await pump.recorder._curve_task
        first = pump.recorder.flow.curves["hp"]
        # Within the hour nothing is computed anew; another heating surface type or slope relearns at once
        await pump.recorder.learn_curves(pump.config, NOW + timedelta(minutes=30))
        assert pump.recorder.flow.curves["hp"] is first
        pump.config.house.heating_surface = "radiator"
        await pump.recorder.learn_curves(pump.config, NOW + timedelta(minutes=31))
    finally:
        pump.store.close()

    assert first.hours == 7 * 24 and first.slope == pytest.approx(S_TRUE, abs=0.1)
    # The heat pump's slope is the start value, whatever the type
    assert first.start_slope == pump.recorder.flow.curves["hp"].start_slope == 0.5
    assert pump.recorder.flow.curves["hp"] is not first


# Start values: read once from the heat pump, set, taken over (API)


async def test_start_values_api_and_read_once_from_the_heat_pump(tmp_path: Path, library: Any) -> None:
    from test_heat_pump_guard import _limits_api  # noqa: PLC0415
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    from sunflow.config.store import ConfigStore  # noqa: PLC0415
    from sunflow.shadow.runner import ShadowRunner  # noqa: PLC0415

    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    pump.states["heating_curve"] = ("0.5", None)
    try:
        await pump.minutes(NOW, 1)
        client, store = _limits_api(tmp_path, library, pump.recorder)
        config = store.load()
        config.devices = pump.config.devices
        store.save(config)
        runner = SimpleNamespace(_store=store, flow_shadow=pump.recorder.flow)

        nothing = client.post("/api/v1/heat_pump/hp/curve_start/learned")
        before = client.get("/api/v1/heat_pump/status").json()["devices"][0]["curve_start"]
        ShadowRunner.keep_curve_starts(runner)
        read = store.load().devices[0]
        set_ = client.put("/api/v1/heat_pump/hp/curve_start", json={"slope": 0.3, "shift_k": -1.0}).json()
        # The heat pump reports another slope: the stored value stays
        pump.recorder.flow._slopes["hp"] = 0.8
        ShadowRunner.keep_curve_starts(runner)
        kept = store.load().devices[0]
        again = client.post("/api/v1/heat_pump/hp/curve_start/heat_pump").json()
        pump.recorder.flow.set_curve("hp", _curve(0.42, -0.3, start_slope=0.8, start_shift=0.0))
        learned = client.post("/api/v1/heat_pump/hp/curve_start/learned").json()
        refused = [client.put("/api/v1/heat_pump/hp/curve_start", json=body).status_code
                   for body in ({"slope": 0.1, "shift_k": 0}, {"slope": 0.5, "shift_k": 11}, {"slope": 0.5})]
        missing = client.put("/api/v1/heat_pump/nope/curve_start", json={"slope": 0.5, "shift_k": 0}).status_code
        pump.recorder.flow._slopes["hp"] = None
        no_value = client.post("/api/v1/heat_pump/hp/curve_start/heat_pump")
    finally:
        pump.store.close()

    assert (nothing.status_code, nothing.json()["detail"]["reason"]) == (409, "nothing_learned")
    assert before == {"slope": None, "shift_k": None, "source": None}
    assert (read.curve_slope_start, read.curve_shift_start, read.curve_start_source) == (0.5, 0.0, "heat_pump")
    assert set_ == {"slope": 0.3, "shift_k": -1.0, "source": "user"}
    assert (kept.curve_slope_start, kept.curve_start_source) == (0.3, "user")
    assert again == {"slope": 0.8, "shift_k": 0.0, "source": "heat_pump"}
    assert learned == {"slope": 0.42, "shift_k": -0.3, "source": "learned"}
    assert refused == [422, 422, 422] and missing == 404
    assert (no_value.status_code, no_value.json()["detail"]["reason"]) == (409, "no_value")
    stored = ConfigStore(tmp_path / "config", lambda: library.profiles).load().devices[0]
    assert (stored.curve_slope_start, stored.curve_shift_start, stored.curve_start_source) == (0.42, -0.3, "learned")
