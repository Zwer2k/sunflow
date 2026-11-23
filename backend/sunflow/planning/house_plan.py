"""The heat pump planned with the learned house: the optimizer's thermal load (design/heat_pump.md, 7;
16 step 4 part 2).

The room temperature becomes a heat storage: within the margin around the room setpoint
[setpoint − lower_k, setpoint + preheat_k] the plan preheats in sunny or cheap hours and lets the house
cool in dear ones. Every kelvin away from the setpoint costs PLAN_DEVIATION_EUR_PER_KH per hour, so
without a saving the plan stays on the setpoint. What the heat pump draws to hold the setpoint (house
model heat / Carnot COP, at most PLAN_MAX_LOAD_SHARE of the slot's forecast) is taken out of the house
load forecast, which learned it, and planned anew; the comparison without Sunflow adds it back.

Only the first heat pump, only in the heating program and with a usable house model. The sun is left
out (no irradiance forecast): on sunny days the plan heats a little too much, the room correction
catches it. Mode "Lernen + Regeln": the battery plan plans with the heat pump. "Nur Lernen": the battery
plan stays as without room heating; a second run shows what Sunflow would preheat. Off: no plan.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import math
from typing import Any

from sunflow.config.models import SunflowConfig
from sunflow.heat_pump.defaults import (
    PLAN_COLD_EUR_PER_KH,
    PLAN_DEVIATION_EUR_PER_KH,
    PLAN_MAX_LOAD_SHARE,
    PLAN_SAMPLE_MAX_AGE_S,
    PLAN_WARM_EUR_PER_KH,
)
from sunflow.heat_pump.learned import values as guard_values
from sunflow.heat_pump.limits import ControlSettings
from sunflow.heat_pump.states import HEATING
from sunflow.planning.house_model import HouseModel, carnot_cop, heat_pump_device
from sunflow.planning.optimizer import Plan, SlotInput, ThermalLoad

SLOT = timedelta(minutes=15)
SAMPLE_MAX_AGE = timedelta(seconds=PLAN_SAMPLE_MAX_AGE_S)
# The battery and consumer plan plans with the heat pump ("Lernen + Regeln")
ACTIVE = "active"
# A second run only to show what Sunflow would preheat ("Nur Lernen")
SHADOW = "shadow"
# Why there is no plan of the room heating (the target is the setpoint then)
NO_HEAT_PUMP = "no_heat_pump"
MODE_OFF = "mode_off"
NO_MODEL = "no_model"
NO_SAMPLE = "no_sample"
PROGRAM = "program"
NO_ROOM = "no_room"
NO_OUTSIDE = "no_outside"
FAILED = "failed"


@dataclass(frozen=True, kw_only=True)
class HouseInput:
    """What the planning needs from the house and the first heat pump now."""

    device_id: str
    name: str
    model: HouseModel | None
    # Room temperature now (6.1) and the measured outside temperature of the heat pump's last sample
    inside_c: float | None
    outside_c: float | None
    # Program of the last sample (None: no recent sample)
    program: str | None
    # Highest heat output (W): learned, else the profile's start value
    max_heat_w: float
    # The user's switch of the heat pump: "Lernen + Regeln"
    writing: bool


def house_input(
    config: SunflowConfig, profiles: dict[str, dict[str, Any]], flow: Any, model: HouseModel | None, now: datetime
) -> HouseInput | None:
    """The first heat pump with the room temperature, its last sample and learned output; None without one."""
    device = heat_pump_device(config, profiles)
    if device is None:
        return None
    minute = (getattr(flow, "last_minutes", None) or {}).get(device.id)
    fresh = minute is not None and timedelta(0) <= now - minute.at <= SAMPLE_MAX_AGE
    room = getattr(flow, "room", None)
    settings = (minute.control if minute is not None else None) or ControlSettings()
    learned = (getattr(flow, "learned", None) or {}).get(device.id)
    return HouseInput(
        device_id=device.id,
        name=device.name,
        model=model,
        inside_c=room.value_c if room is not None else None,
        outside_c=minute.outside_c if fresh else None,
        program=minute.reading.program if fresh else None,
        max_heat_w=guard_values(settings, learned).power_max_w,
        writing=device.flow_active,
    )


@dataclass(kw_only=True)
class HouseSetup:
    """The thermal load for the optimizer and the house load without the heat pump; or why there is none."""

    mode: str | None
    reason: str | None = None
    load: ThermalLoad | None = None
    adjusted: list[SlotInput] = field(default_factory=list)
    detail: str | None = None


def _outside(temperatures: list[float | None], now: float | None) -> list[float] | None:
    """The forecast per slot, gaps filled with the last known value (at first the measured one); without a
    forecast the measured value stays."""
    known = next((v for v in temperatures if v is not None), now)
    if known is None:
        return None
    result = []
    for value in temperatures:
        known = value if value is not None and math.isfinite(value) else known
        result.append(known)
    return result


def house_setup(
    config: SunflowConfig, house: HouseInput | None, inputs: list[SlotInput], temperatures: list[float | None]
) -> HouseSetup:
    """The heat pump as a thermal load with the house within the margin, or why it is not planned."""
    climate = config.house.climate
    if house is None:
        return HouseSetup(mode=None, reason=NO_HEAT_PUMP)
    if climate.mode == "off":
        return HouseSetup(mode=None, reason=MODE_OFF)
    mode = ACTIVE if house.writing else SHADOW
    model = house.model
    if model is None or not model.useful or model.b is None or model.b <= 0:
        return HouseSetup(mode=mode, reason=NO_MODEL)
    if house.program is None:
        return HouseSetup(mode=mode, reason=NO_SAMPLE)
    if house.program != HEATING:
        # Hot water only, standby, summer ...: the rooms are not heated
        return HouseSetup(mode=mode, reason=PROGRAM, detail=house.program)
    if house.inside_c is None:
        return HouseSetup(mode=mode, reason=NO_ROOM)
    outside = _outside(list(temperatures[: len(inputs)]) + [None] * (len(inputs) - len(temperatures)), house.outside_c)
    if outside is None or not inputs:
        return HouseSetup(mode=mode, reason=NO_OUTSIDE)
    setpoint = climate.setpoint_c
    cop = [carnot_cop(o) for o in outside]
    limits = [house.max_heat_w / max(c, 1.0) for c in cop]
    baseline, adjusted = [], []
    for slot, temp, factor, limit in zip(inputs, outside, cop, limits, strict=True):
        # Heat to hold the setpoint against the losses (kWh per hour), as electric power
        need = max(model.a * (setpoint - temp) - model.d, 0.0) / model.b
        power = min(need / factor * 1000, limit, PLAN_MAX_LOAD_SHARE * max(slot.load_w, 0.0))
        baseline.append(power)
        adjusted.append(replace(slot, load_w=slot.load_w - power))
    n = len(inputs)
    load = ThermalLoad(
        name=house.device_id,
        a=model.a,
        b=model.b,
        gains=model.d,
        inside=house.inside_c,
        outside=outside,
        cop=cop,
        lower=[setpoint - climate.lower_k] * n,
        upper=[setpoint + climate.preheat_k] * n,
        max_power_w=max(limits),
        max_heat_w=house.max_heat_w,
        baseline_w=baseline,
        cold_cost=PLAN_COLD_EUR_PER_KH,
        warm_cost=PLAN_WARM_EUR_PER_KH,
        target=[setpoint] * n,
        deviation_cost=PLAN_DEVIATION_EUR_PER_KH,
    )
    return HouseSetup(mode=mode, load=load, adjusted=adjusted)


@dataclass(kw_only=True)
class HousePlan:
    """The plan of the room heating per slot (planned room temperature and heat), or why there is none."""

    mode: str | None
    reason: str | None
    device_id: str | None
    name: str | None
    setpoint_c: float
    lower_c: float
    upper_c: float
    inside_c: float | None
    detail: str | None = None
    slots: list[dict[str, Any]] = field(default_factory=list)
    # Electric energy (kWh) of the heat pump planned and to hold the setpoint without planning
    energy_kwh: float | None = None
    baseline_kwh: float | None = None


def house_plan(config: SunflowConfig, house: HouseInput | None, setup: HouseSetup, plan: Plan | None) -> HousePlan:
    """The room heating of a plan (the battery plan or the second run) for the display."""
    climate = config.house.climate
    result = HousePlan(
        mode=setup.mode,
        reason=setup.reason,
        device_id=house.device_id if house else None,
        name=house.name if house else None,
        setpoint_c=climate.setpoint_c,
        lower_c=round(climate.setpoint_c - climate.lower_k, 2),
        upper_c=round(climate.setpoint_c + climate.preheat_k, 2),
        inside_c=house.inside_c if house else None,
        detail=setup.detail,
    )
    load = setup.load
    if load is None:
        return result
    if plan is None or not plan.slots:
        result.reason = FAILED
        return result
    name = load.name
    for t, slot in enumerate(plan.slots):
        power = slot.heat_w.get(name, 0.0)
        base = slot.heat_base_w.get(name, 0.0)
        result.slots.append({
            "start": slot.start.isoformat(),
            "end": slot.end.isoformat(),
            "target_c": round(slot.room_c[name], 2) if name in slot.room_c else None,
            "power_w": round(power),
            "heat_w": round(power * load.cop[t]),
            "baseline_w": round(base),
            "outside_c": round(load.outside[t], 1),
            "cop": round(load.cop[t], 2),
        })
    result.energy_kwh = round(sum(s.heat_w.get(name, 0.0) for s in plan.slots) * SLOT.total_seconds() / 3600 / 1000, 2)
    result.baseline_kwh = round(sum(s.heat_base_w.get(name, 0.0) for s in plan.slots) * SLOT.total_seconds() / 3600 / 1000, 2)
    return result
