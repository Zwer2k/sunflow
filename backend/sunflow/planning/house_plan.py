"""The heat pump planned with the learned house: the optimizer's thermal load (stage 4).

The room temperature becomes a heat storage: the plan preheats in sunny or cheap hours and lets
the house cool in dear ones, within the comfort band. What the heat pump would draw unplanned
(holding the lower bound) is taken out of the house load forecast, which learned it, and planned
anew; the comparison without Sunflow adds it back.
"""

from dataclasses import replace
from datetime import timedelta, tzinfo
import math

from sunflow.config.models import SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.i18n import t
from sunflow.planning.house_model import (
    HouseModel,
    carnot_cop,
    heat_pump,
    lower_bound,
    room_now,
)
from sunflow.planning.optimizer import SlotInput, ThermalLoad

SLOT = timedelta(minutes=15)
# At most this share of the forecast house load is taken as the heat pump's
MAX_LOAD_SHARE = 0.7
# € per K and hour below the band: far above what heating a kelvin costs (25 kWh/K at COP 3 and 0.50 €: about 4 €)
COLD_COST = 50.0
# € per K and hour above it: heating beyond the band only for a clear gain (negative prices)
WARM_COST = 0.2


def _outside(temperatures: list[float | None], now: float | None) -> list[float] | None:
    """The forecast per slot, gaps filled with the last known value (at first the current one)."""
    known = next((v for v in temperatures if v is not None), now)
    if known is None:
        return None
    result = []
    for value in temperatures:
        known = value if value is not None else known
        result.append(known)
    return result


def thermal_loads(
    config: SunflowConfig,
    snapshot: Snapshot,
    inputs: list[SlotInput],
    temperatures: list[float | None],
    model: HouseModel | None,
    tz: tzinfo,
) -> tuple[list[ThermalLoad], list[SlotInput], list[str]]:
    """The heat pump as a thermal load with the house, and the house load without it; with warnings."""
    device = heat_pump(config)
    if device is None or device.heat_pump is None or not device.heat_pump.preheat or not inputs:
        return [], inputs, []
    if not config.heating_season.active:
        # In summer the heat pump heats hot water; the rooms do not count
        return [], inputs, []
    if model is None or model.b is None or model.b <= 0:
        return [], inputs, [t("Vorheizen nicht geplant: Das Hausmodell ist noch nicht zuverlässig oder kennt die Wärme der Heizung nicht.")]
    room = room_now(config.house, snapshot.states)
    role = device.role_overrides.get("outside_temperature")
    source = role.entity_id if role and role.entity_id else config.learning_sources.outdoor_temperature
    outside_now = _sensor(snapshot, source)
    outside = _outside(temperatures, outside_now)
    if room is None or outside is None:
        return [], inputs, [t("Vorheizen nicht geplant: Raum- oder Außentemperatur fehlt.")]
    inside, offset = room
    settings = device.heat_pump
    house = config.house
    cop = [carnot_cop(o) for o in outside]
    lower = [lower_bound(house, slot.start + SLOT, tz) - offset for slot in inputs]
    upper = [house.comfort_max - offset] * len(inputs)
    baseline, adjusted = [], []
    for slot, low, temp, factor in zip(inputs, lower, outside, cop, strict=True):
        # Heat to hold the lower bound against the losses (kWh per hour), as electric power
        need = max(model.a * (low - temp) - model.d, 0.0) / model.b
        power = min(need / factor * 1000, settings.max_power_w, MAX_LOAD_SHARE * max(slot.load_w, 0.0))
        baseline.append(power)
        adjusted.append(replace(slot, load_w=slot.load_w - power))
    load = ThermalLoad(
        name=device.id,
        a=model.a,
        b=model.b,
        gains=model.d,
        inside=inside,
        outside=outside,
        cop=cop,
        lower=lower,
        upper=upper,
        max_power_w=settings.max_power_w,
        baseline_w=baseline,
        cold_cost=COLD_COST,
        warm_cost=WARM_COST,
        offset=offset,
    )
    return [load], adjusted, []


def _sensor(snapshot: Snapshot, entity_id: str | None) -> float | None:
    """A temperature sensor's value now."""
    try:
        value = float(snapshot.states[entity_id]["state"]) if entity_id else None
    except KeyError, TypeError, ValueError:
        return None
    return value if value is not None and math.isfinite(value) else None
