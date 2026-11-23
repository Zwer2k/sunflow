"""Heating curve flow = R + S·(R − A) + P (design/heat_pump.md, 8): start slope, learning pulled to the start
values, the flow target from curve and correction, no jump when the curve changes; with synthetic data."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import random
from typing import Any

import pytest
from sunflow.config.models import DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.heat_pump.curve import FROM_HEAT_PUMP, FROM_SURFACE, Curve, fit, learn, select_points, start_curve, start_slope
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


def test_start_slope_from_the_heat_pump_else_the_surface_type() -> None:
    assert start_slope("underfloor", 0.5) == (0.5, FROM_HEAT_PUMP)
    # Not usable (missing, 0 = off, out of range): the heating surface type's
    assert start_slope("underfloor", None) == (0.6, FROM_SURFACE)
    assert start_slope("radiator", 0.0) == (1.4, FROM_SURFACE)
    assert [curve_slope(s) for s in ("underfloor", "mixed", "radiator", "unknown")] == [0.6, 1.0, 1.4, 0.6]
    assert set(HEATING_SURFACES) == {"underfloor", "mixed", "radiator"}


def test_curve_formula() -> None:
    curve = start_curve(0.5, FROM_HEAT_PUMP, NOW)

    # 23 + 0.5 · (23 − 2): a setpoint 1 K higher raises the flow by 1.5 K
    assert curve.flow_c(23.0, 2.0) == pytest.approx(33.5)
    assert curve.flow_c(24.0, 2.0) - curve.flow_c(23.0, 2.0) == pytest.approx(1.5)


# Learning


def test_learned_towards_the_house_with_enough_data() -> None:
    curve = learn(_rows(14), WEIGHTS, SETPOINT, 0.5, FROM_HEAT_PUMP, NOW)

    assert curve.hours == 14 * 24
    assert curve.slope == pytest.approx(S_TRUE, abs=0.05)
    assert curve.shift_k == pytest.approx(P_TRUE, abs=0.5)
    # The prior still pulls a little (24 virtual hours against about 240 weighted ones)
    assert curve.flow_c(SETPOINT, 0.0) == pytest.approx(SETPOINT + S_TRUE * SETPOINT + P_TRUE, abs=1.0)


def test_few_hours_stay_close_to_the_start() -> None:
    """A few hours pull only a little; with as many hours as the prior about halfway."""
    few = learn(_rows(2 / 24), WEIGHTS, SETPOINT, 0.5, FROM_HEAT_PUMP, NOW)
    some = learn(_rows(CURVE_PRIOR_HOURS / 24), WEIGHTS, SETPOINT, 0.5, FROM_HEAT_PUMP, NOW)

    start = start_curve(0.5, FROM_HEAT_PUMP, NOW)
    truth = SETPOINT + S_TRUE * (SETPOINT - 3) + P_TRUE
    assert abs(few.flow_c(SETPOINT, 3) - start.flow_c(SETPOINT, 3)) < 0.3 * abs(truth - start.flow_c(SETPOINT, 3))
    assert abs(some.flow_c(SETPOINT, 3) - truth) < abs(start.flow_c(SETPOINT, 3) - truth)
    assert learn([], WEIGHTS, SETPOINT, 0.5, FROM_HEAT_PUMP, NOW).slope == 0.5


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
    assert select_points(_rows(1, room_offset=offset, **changes), WEIGHTS, SETPOINT) == []


def test_room_setpoint_of_the_row_counts() -> None:
    rows = _rows(1, room_setpoint_c=None)

    # Older rows without it: the current setpoint
    assert len(select_points(rows, WEIGHTS, SETPOINT)) == 24
    assert select_points(rows, WEIGHTS, 23.0) == []


def test_limits_of_the_fit() -> None:
    points = select_points(_rows(14), WEIGHTS, SETPOINT)
    wild = [p.__class__(start=p.start, room_c=p.room_c, outside_c=p.outside_c, flow_c=p.flow_c + 30) for p in points]

    slope, shift = fit(wild, 0.5, NOW)

    assert 0.2 <= slope <= 3.0 and shift == 10.0


def test_cache_round_trip() -> None:
    curve = learn(_rows(3), WEIGHTS, SETPOINT, 0.5, FROM_HEAT_PUMP, NOW, key="k")

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
    assert flow["curve"] == {"slope": 0.5, "shift_k": 0.0, "start_slope": 0.5, "start_source": FROM_HEAT_PUMP, "hours": 0,
                             "computed_at": NOW.isoformat()}
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
    house.shadow.set_curve("hp", Curve(slope=0.6, shift_k=0.5, start_slope=0.5, start_source=FROM_HEAT_PUMP, hours=50, computed_at=NOW))
    after = await house.tick(NOW + MIN)
    # Beyond the correction's limit the rest stays as a jump
    house.shadow.set_curve("hp", Curve(slope=0.9, shift_k=2.0, start_slope=0.5, start_source=FROM_HEAT_PUMP, hours=60, computed_at=NOW))
    far = await house.tick(NOW + 2 * MIN)

    # The curve gives 23 + 0.6·21 + 0.5 = 36.1; the correction takes the difference
    assert after["curve_c"] == pytest.approx(36.1)
    assert (after["target_c"], after["correction_k"]) == (pytest.approx(before["target_c"]), pytest.approx(-2.6))
    assert far["correction_k"] == -3.0 and far["target_c"] > before["target_c"]


async def test_learned_curve_with_another_start_slope_is_not_used(tmp_path: Path) -> None:
    """The heat pump's slope changed: the start curve until learned anew."""
    house = House(tmp_path)
    house.shadow.set_curve("hp", Curve(slope=0.9, shift_k=0.0, start_slope=0.4, start_source=FROM_HEAT_PUMP, hours=50, computed_at=NOW))
    flow = await house.tick(NOW)

    assert flow["curve"]["slope"] == 0.5


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
