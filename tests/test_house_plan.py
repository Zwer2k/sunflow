"""The room heating planned with the house (design/heat_pump.md, 14.2, 14.3): band around the
room setpoint, soft cost away from it, the heat pump out of the load forecast, the setting decides whether
the planning uses the house model, the mode whether the battery plan plans with it or a second run only
shows it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sunflow.api.heat_pump import router
from sunflow.api.plan import plan_response
from sunflow.config.models import ClimateSettings, DeviceConfig, HouseSettings, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.heat_pump.defaults import PLAN_DEVIATION_EUR_PER_KH, PLAN_MAX_LOAD_SHARE
from sunflow.heat_pump.learned import Learned
from sunflow.heat_pump.limits import ControlSettings
from sunflow.heat_pump.states import Reading
from sunflow.planning.house_plan import (
    ACTIVE,
    FAILED,
    MODE_OFF,
    MODEL_OFF,
    MODEL_UNUSABLE,
    NO_HEAT_PUMP,
    NO_MODEL,
    NO_OUTSIDE,
    NO_ROOM,
    NO_SAMPLE,
    PROGRAM,
    SHADOW,
    HouseInput,
    carnot_cop,
    house_input,
    house_plan,
    house_setup,
    uses_model,
)
from sunflow.planning.optimizer import BatterySpec, Plan, Settings, SlotInput, ThermalLoad, optimize
from sunflow.planning.plan import PlanResult, optimize_with_house

from house_models import house_model

START = datetime(2026, 11, 16, tzinfo=UTC)
SLOT = timedelta(minutes=15)
# Nearly empty and small: the house is the storage that matters here
BATTERY = BatterySpec(capacity_wh=1000, soc_wh=100, min_soc_wh=100, max_charge_w=100, max_discharge_w=100)
# House τ 50 h, 25 kWh/K, a little internal gain; a fast surface
MODEL = house_model()
P = MODEL.params
PROFILES = {"esphome/rotex_hpsu_can": {"capabilities": ["heat_pump"]}}


def _slots(hours: int, price: list[float], load: float = 2000.0) -> list[SlotInput]:
    return [
        SlotInput(start=START + i * SLOT, pv_w=0.0, load_w=load, price=price[i // 4], feed_in=0.08)
        for i in range(hours * 4)
    ]


def _house(n: int, inside: float = 21.0, outside: float = 8.0, **extra: Any) -> ThermalLoad:
    # Band 21-22 °C around the setpoint 21 °C, as with the start values of the margin (+1 K, -0 K)
    base = (P.u * (21.0 - outside) - P.g) / 3.0 * 1000
    values: dict[str, Any] = {
        "name": "hp", "u": P.u, "kf": P.kf, "cs": P.cs, "cb": P.cb, "gains": P.g, "inside": inside,
        "surface": inside + base * 3.0 / 1000 / P.kf, "outside": [outside] * n,
        "cop": [3.0] * n, "lower": [21.0] * n, "upper": [22.0] * n, "max_power_w": 3000, "baseline_w": [base] * n,
        "cold_cost": 50.0, "warm_cost": 0.2, "target": [21.0] * n, "deviation_cost": PLAN_DEVIATION_EUR_PER_KH,
    }
    return ThermalLoad(**(values | extra))


# Optimizer: target with a soft cost


def test_stays_on_the_setpoint_without_a_saving() -> None:
    """Flat prices: nothing to gain, the planned room temperature stays on the setpoint."""
    inputs = _slots(12, [0.30] * 12)

    plan = optimize(inputs, BATTERY, thermal=[_house(len(inputs))])

    rooms = [s.room_c["hp"] for s in plan.slots]
    assert min(rooms) >= 21.0 - 1e-6
    assert max(rooms) <= 21.05
    assert sum(s.heat_w["hp"] for s in plan.slots) > 0


def test_preheats_before_dear_hours_within_the_margin() -> None:
    """Cheap first, dear later: the house is heated ahead up to the margin and coasts through the dear hours."""
    inputs = _slots(8, [0.10] * 4 + [0.50] * 4)

    plan = optimize(inputs, BATTERY, thermal=[_house(len(inputs))])

    rooms = [s.room_c["hp"] for s in plan.slots]
    assert max(rooms) > 21.3
    assert max(rooms) <= 22.0 + 1e-6
    assert min(rooms) >= 21.0 - 1e-6
    cheap = sum(s.heat_w["hp"] for s in plan.slots[:16])
    dear = sum(s.heat_w["hp"] for s in plan.slots[16:])
    assert cheap > 3 * dear


def test_a_small_price_difference_is_not_worth_the_deviation() -> None:
    """A cent of difference pays for no more than a trace of warmth just before the dearer hours."""
    inputs = _slots(8, [0.30] * 4 + [0.31] * 4)

    plan = optimize(inputs, BATTERY, thermal=[_house(len(inputs))])

    assert max(s.room_c["hp"] for s in plan.slots) <= 21.1
    assert sum(s.room_c["hp"] > 21.02 for s in plan.slots) <= 3


def test_without_deviation_cost_the_same_difference_preheats() -> None:
    """The same prices without the soft cost: preheating pays a little - so it is the cost that holds it back."""
    inputs = _slots(8, [0.10] * 4 + [0.11] * 4)
    house = _house(len(inputs), target=None, deviation_cost=0.0)

    plan = optimize(inputs, BATTERY, thermal=[house])

    assert max(s.room_c["hp"] for s in plan.slots) > 21.2


def test_lowering_within_the_margin_in_dear_hours() -> None:
    """With room to lower (band 20-22) the house cools in dear hours; the soft cost keeps it small otherwise."""
    inputs = _slots(8, [0.50] * 4 + [0.10] * 4)
    house = _house(len(inputs), lower=[20.0] * len(inputs))

    plan = optimize(inputs, BATTERY, thermal=[house])

    rooms = [s.room_c["hp"] for s in plan.slots]
    assert min(rooms) < 20.9
    assert min(rooms) >= 20.0 - 1e-6


def test_heat_output_limits_the_power_per_slot() -> None:
    """The electric power stays below the highest heat output / COP in every slot."""
    inputs = _slots(4, [0.10] * 2 + [0.50] * 2)
    house = _house(len(inputs), inside=20.0, max_heat_w=3000.0, cop=[3.0] * 8 + [2.0] * 8)

    plan = optimize(inputs, BATTERY, thermal=[house])

    assert max(s.heat_w["hp"] for s in plan.slots[:8]) <= 1000 + 1e-6
    assert max(s.heat_w["hp"] for s in plan.slots[8:]) <= 1500 + 1e-6
    assert house.power_limit(0) == pytest.approx(1000)


# The thermal load from the house and the room setpoint


def _config(mode: str = "heat", writing: bool = False, **climate: Any) -> SunflowConfig:
    device = DeviceConfig(id="hp", ha_device_id="dev", profile="esphome/rotex_hpsu_can", profile_version=3, name="HPSU", flow_active=writing)
    return SunflowConfig(devices=[device], house=HouseSettings(climate=ClimateSettings(mode=mode, **climate)))


def _input(**changes: Any) -> HouseInput:
    values: dict[str, Any] = {
        "device_id": "hp", "name": "HPSU", "model": MODEL, "inside_c": 21.0, "outside_c": 2.0,
        "program": "heating", "max_heat_w": 12000.0, "writing": False,
    }
    return HouseInput(**(values | changes))


def test_setup_band_target_and_the_heat_pump_out_of_the_load() -> None:
    config = _config(setpoint_c=21.0, preheat_k=1.5, lower_k=0.5)
    inputs = _slots(2, [0.30] * 2, load=1500.0)
    temperatures: list[float | None] = [0.0] * 4 + [5.0] * 4

    setup = house_setup(config, _input(), inputs, temperatures)

    load = setup.load
    assert load is not None and setup.mode == SHADOW and setup.reason is None
    assert (load.lower[0], load.upper[0], load.target[0] if load.target else None) == (20.5, 22.5, 21.0)
    assert load.deviation_cost == PLAN_DEVIATION_EUR_PER_KH
    assert load.inside == 21.0 and load.outside == temperatures
    # The model's surface above the room, carried over to the room temperature now
    assert load.surface == pytest.approx(21.0 + MODEL.surface_c - MODEL.room_c)
    assert (load.u, load.kf, load.cs, load.cb, load.gains) == (P.u, P.kf, P.cs, P.cb, P.g)
    assert load.cop == [carnot_cop(t) for t in temperatures]
    for t, (slot, adjusted) in enumerate(zip(inputs, setup.adjusted, strict=True)):
        # Heat to hold the setpoint (not the lower bound) / COP, within the output and 70 % of the load
        need = (P.u * (21.0 - temperatures[t]) - P.g) / load.cop[t] * 1000
        expected = min(need, 12000.0 / load.cop[t], PLAN_MAX_LOAD_SHARE * 1500.0)
        assert load.baseline_w[t] == pytest.approx(expected)
        assert adjusted.load_w == pytest.approx(slot.load_w - expected)
    # 70 % of the forecast caps it on the cold slots
    assert load.baseline_w[0] == pytest.approx(PLAN_MAX_LOAD_SHARE * 1500.0)


def test_setup_lets_the_output_cap_the_baseline() -> None:
    setup = house_setup(_config(), _input(max_heat_w=2000.0), _slots(1, [0.3], load=10000.0), [-10.0] * 4)

    assert setup.load is not None
    assert setup.load.baseline_w[0] == pytest.approx(2000.0 / carnot_cop(-10.0))
    assert setup.load.power_limit(0) == pytest.approx(2000.0 / carnot_cop(-10.0))


def test_setup_fills_forecast_gaps_and_uses_the_measured_value_without_a_forecast() -> None:
    inputs = _slots(1, [0.3])

    gaps = house_setup(_config(), _input(outside_c=3.0), inputs, [None, 1.0, None, 2.0])
    measured = house_setup(_config(), _input(outside_c=3.0), inputs, [])

    assert gaps.load is not None and gaps.load.outside == [1.0, 1.0, 1.0, 2.0]
    assert measured.load is not None and measured.load.outside == [3.0] * 4


@pytest.mark.parametrize(
    ("config", "house", "reason", "mode"),
    [
        (_config(), None, NO_HEAT_PUMP, None),
        (_config(mode="off", writing=True), _input(writing=True), MODE_OFF, None),
        (_config(), _input(model=None), NO_MODEL, SHADOW),
        (_config(plan_model="off"), _input(), MODEL_OFF, SHADOW),
        (_config(), _input(model=house_model(test_rms_k=0.4)), MODEL_UNUSABLE, SHADOW),
        (_config(), _input(model=house_model(room_hours=40)), MODEL_UNUSABLE, SHADOW),
        (_config(plan_model="on"), _input(model=house_model(at_bound=("u",))), MODEL_UNUSABLE, SHADOW),
        (_config(), _input(program=None), NO_SAMPLE, SHADOW),
        (_config(), _input(program="summer"), PROGRAM, SHADOW),
        (_config(writing=True), _input(inside_c=None, writing=True), NO_ROOM, ACTIVE),
        (_config(), _input(outside_c=None), NO_OUTSIDE, SHADOW),
    ],
)
def test_setup_why_not_planned(config: SunflowConfig, house: HouseInput | None, reason: str, mode: str | None) -> None:
    setup = house_setup(config, house, _slots(1, [0.3]), [None] * 4)

    assert (setup.load, setup.reason, setup.mode) == (None, reason, mode)
    plan = house_plan(config, house, setup, None)
    assert plan.reason == reason and plan.slots == []
    assert (plan.setpoint_c, plan.lower_c, plan.upper_c) == (21.0, 21.0, 22.0)


@pytest.mark.parametrize(
    ("setting", "model", "used"),
    [
        ("auto", house_model(), True),
        ("auto", house_model(test_rms_k=0.4), False),
        ("auto", house_model(test_rms_k=None, stay_rms_k=None), False),
        ("auto", house_model(room_hours=47), False),
        ("auto", house_model(at_bound=("cs",)), False),
        ("on", house_model(test_rms_k=0.4, room_hours=30), True),
        ("on", house_model(at_bound=("cs",)), False),
        ("off", house_model(), False),
        ("on", None, False),
    ],
)
def test_the_setting_decides_whether_the_planning_uses_the_model(setting: str, model: Any, used: bool) -> None:
    """Auto: usable (48 h, plausible, better than "it stays"); on: plausible; off: never (14.2)."""
    assert uses_model(setting, model) is used


def test_a_model_not_usable_says_why() -> None:
    setup = house_setup(_config(), _input(model=house_model(room_hours=40)), _slots(1, [0.3]), [2.0] * 4)

    assert (setup.reason, setup.detail) == (MODEL_UNUSABLE, "few_hours")
    assert house_model(at_bound=("u",)).reason() == "implausible"
    assert house_model(test_rms_k=0.5).reason() == "test"
    assert house_model().reason() is None


def test_input_from_the_first_heat_pump_and_its_last_sample() -> None:
    now = START + timedelta(minutes=3)
    minute = SimpleNamespace(
        at=START, outside_c=-2.5, reading=Reading(program="heating"), control=ControlSettings(power_max_w=5000.0),
    )
    flow = SimpleNamespace(last_minutes={"hp": minute}, room=SimpleNamespace(value_c=20.7), learned={"hp": Learned(computed_at=START, power_max_w=7000.0)})

    house = house_input(_config(writing=True), PROFILES, flow, MODEL, now)

    assert house == HouseInput(
        device_id="hp", name="HPSU", model=MODEL, inside_c=20.7, outside_c=-2.5, program="heating",
        max_heat_w=7000.0, writing=True,
    )


def test_input_without_a_recent_sample() -> None:
    minute = SimpleNamespace(at=START, outside_c=-2.5, reading=Reading(program="heating"), control=None)
    flow = SimpleNamespace(last_minutes={"hp": minute}, room=None, learned={})

    house = house_input(_config(), PROFILES, flow, None, START + timedelta(minutes=11))

    assert house is not None
    # Nothing learned: the profile's start value (6 kW)
    assert (house.program, house.outside_c, house.inside_c, house.max_heat_w) == (None, None, None, 6000.0)
    assert house_input(SunflowConfig(), PROFILES, flow, None, START) is None


# The mode decides: in the plan, a second run only shown, or none


BIG_BATTERY = BatterySpec(capacity_wh=10000, soc_wh=2000, min_soc_wh=500, max_charge_w=3000, max_discharge_w=3000)


async def _optimize(config: SunflowConfig, house: HouseInput) -> tuple[Plan, Any, list[SlotInput]]:
    inputs = _slots(8, [0.10] * 4 + [0.50] * 4)
    plan, heating = await optimize_with_house(config, house, inputs, [8.0] * len(inputs), [BIG_BATTERY], Settings(), [])
    return plan, heating, inputs


async def test_learning_and_control_plans_the_battery_with_the_heat_pump() -> None:
    plan, heating, inputs = await _optimize(_config(writing=True), _input(writing=True))

    assert heating.mode == ACTIVE and heating.reason is None
    assert all("hp" in s.heat_w for s in plan.slots)
    # The house load without the heat pump's usual draw, the heat pump planned on its own
    assert plan.slots[0].load_w < inputs[0].load_w
    assert len(heating.slots) == len(plan.slots)
    assert heating.slots[0]["target_c"] is not None and heating.slots[0]["heat_w"] >= heating.slots[0]["power_w"]
    assert max(s["target_c"] for s in heating.slots) > 21.5
    assert heating.energy_kwh is not None and heating.baseline_kwh is not None


async def test_learning_only_keeps_the_plan_and_shows_a_second_run() -> None:
    plan, heating, inputs = await _optimize(_config(), _input())

    assert heating.mode == SHADOW and heating.reason is None
    # The plan as without room heating: the heat pump stays in the load forecast
    assert all(s.heat_w == {} for s in plan.slots)
    assert [s.load_w for s in plan.slots] == [s.load_w for s in inputs]
    # What Sunflow would preheat
    assert max(s["target_c"] for s in heating.slots) > 21.5


async def test_mode_off_plans_no_room_heating() -> None:
    plan, heating, inputs = await _optimize(_config(mode="off", writing=True), _input(writing=True))

    assert (heating.mode, heating.reason, heating.slots) == (None, MODE_OFF, [])
    assert all(s.heat_w == {} for s in plan.slots)
    assert [s.load_w for s in plan.slots] == [s.load_w for s in inputs]


async def test_a_failing_second_run_keeps_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    import sunflow.planning.plan as plan_module

    real = plan_module.optimize

    def failing(*args: Any) -> Plan:
        if len(args) > 4:
            raise RuntimeError("solver")
        return real(*args)

    monkeypatch.setattr(plan_module, "optimize", failing)

    plan, heating, _ = await _optimize(_config(), _input())

    assert plan.slots and heating.reason == FAILED and heating.slots == []


async def test_no_room_heating_without_a_heat_pump() -> None:
    inputs = _slots(2, [0.3] * 2)

    plan, heating = await optimize_with_house(SunflowConfig(), None, inputs, [8.0] * len(inputs), [BIG_BATTERY], Settings(), [])

    assert heating is None and plan.slots and all(s.heat_w == {} for s in plan.slots)


async def test_plan_response_has_the_room_heating() -> None:
    plan, heating, _ = await _optimize(_config(writing=True), _input(writing=True))

    response = plan_response(PlanResult(plan=plan, house=heating), SunflowConfig())

    house = response["house"]
    assert house["mode"] == ACTIVE and house["device_id"] == "hp" and len(house["slots"]) == len(plan.slots)
    assert set(house["slots"][0]) == {"start", "end", "target_c", "power_w", "heat_w", "baseline_w", "outside_c", "cop"}
    assert plan_response(PlanResult(plan=plan), SunflowConfig())["house"] is None


# Planning again at once after a change


def _api(tmp_path: Path, library: Any) -> tuple[TestClient, list[int]]:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.profiles = library
    app.state.store = ConfigStore(tmp_path, lambda: library.profiles)
    config = app.state.store.load()
    config.devices = [DeviceConfig(id="hp", ha_device_id="x", profile="esphome/rotex_hpsu_can", profile_version=3, name="HPSU", flow_active=True)]
    app.state.store.save(config)
    replans: list[int] = []
    app.state.runner = SimpleNamespace(heat_pump=None, shadow=None, request_replan=lambda: replans.append(1))
    return TestClient(app), replans


@pytest.mark.parametrize(
    "body", [{"setpoint_c": 22.0}, {"preheat_k": 2.0}, {"lower_k": 0.5}, {"mode": "off"}, {"plan_model": "off"}]
)
def test_a_change_of_setpoint_margin_or_mode_plans_again(tmp_path: Path, library: Any, body: dict[str, Any]) -> None:
    client, replans = _api(tmp_path, library)

    assert client.put("/api/v1/house/climate", json=body).status_code == 200

    assert replans == [1]


def test_no_change_plans_nothing_new(tmp_path: Path, library: Any) -> None:
    client, replans = _api(tmp_path, library)

    client.put("/api/v1/house/climate", json={"setpoint_c": 21.0, "preheat_k": 1.0})

    assert replans == []


def test_switching_learning_and_control_off_plans_again(tmp_path: Path, library: Any) -> None:
    client, replans = _api(tmp_path, library)

    assert client.put("/api/v1/heat_pump/hp/writing", json={"active": False}).json() == {"active": False}
    client.put("/api/v1/heat_pump/hp/writing", json={"active": False})

    assert replans == [1]
