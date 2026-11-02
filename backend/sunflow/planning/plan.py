"""Collect forecasts, prices and battery state and run the optimizer."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
import math
from statistics import median
import time
from typing import Any

from sunflow.config.models import (
    BATTERY_PRIORITY_ID,
    DeviceConfig,
    FeedIn,
    ForecastPlane,
    SunflowConfig,
    Vehicle,
)
from sunflow.control.charger import ChargerReady, ChargerWatch
from sunflow.devices.discovery import Snapshot
from sunflow.devices.profiles import battery_actions, controls_battery, supported_action
from sunflow.devices.resolver import preview
from sunflow.devices.site import device_kind, device_parameters
from sunflow.ha import HAClient, HACommandError, HAError
from sunflow.i18n import N_, number, t

from .habits import (
    ConsumerUsage,
    Departure,
    learn_departure,
    learn_usage,
    learn_usage_from_energy,
)
from .hot_water import HotWaterUsage, learn_hot_water
from .load_forecast import (
    LoadForecast,
    build_load_forecast,
    fetch_hourly_means,
    find_load_entity,
    subtract_loads,
    unmeasured_consumers,
)
from .load_model import LoadModel
from .optimizer import BatterySpec, FlexLoad, Plan, Settings, SlotInput, optimize
from .price_estimate import extend_prices
from .pv_learning import PvCorrection, cloud_class
from .sources import (
    ForecastPoint,
    PricePoint,
    fetch_prices,
    forecast_solar_forecast,
    is_exchange,
    price_factor,
    resample,
    solcast_forecast,
    sum_forecasts,
    with_surcharges,
)
from .vehicle_usage import VehicleUsage, usage_from as vehicle_usage_from
from .vehicles import PLUGGED, Recognized, recognize, soc_of, vehicle_max_power

# The plan looks this far ahead; beyond the published prices with estimated ones
HORIZON_HOURS = 96
# Database queries change slowly; every replan (each quarter hour) reusing them keeps
# the load on the recorder low. On a failure the last result stays good enough
HOURLY_TTL = timedelta(hours=1)
HABITS_TTL = timedelta(hours=6)
# Price history whose mean per hour estimates prices not published yet
PRICE_HISTORY_DAYS = 14
_query_cache: dict[tuple[str, ...], tuple[float, Any]] = {}


async def cached[T](key: tuple[str, ...], ttl: timedelta, fetch: Callable[[], Awaitable[T]]) -> T:
    """Result of fetch, at most ttl old; the last known one when Home Assistant fails."""
    hit = _query_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < ttl.total_seconds():
        return hit[1]
    try:
        value = await fetch()
    except HACommandError:
        if hit is not None:
            return hit[1]
        raise
    _query_cache[key] = (time.monotonic(), value)
    return value

MIN_HORIZON_HOURS = 4
SLOT = timedelta(minutes=15)
# Without any price, a nominal one makes the optimizer maximize self-consumption
NOMINAL_PRICE = 0.30


PRIORITY_STEP = 0.001
# €/kWh for needs that must be met (above any price) and for "as fast as possible"
MUST_HAVE_COST = 2.0
FAST_URGENCY = 1.0
# Heating 1 litre of water by 1 K takes 1.163 Wh
WATER_WH_PER_LITRE_KELVIN = 1.163
# Target of a heating own consumer when none is set (evcc loadpoints report their own)
DEFAULT_HOT_WATER_C = 60.0
HEATER_DEADLINE = timedelta(hours=24)
# Charger of an e-bike or tool battery: energy from the socket per energy into the battery
CHARGER_EFFICIENCY = 0.85
# A car at home but not plugged in: hinted when the plan gives it this much within this time
PLUG_IN_HINT_AHEAD = timedelta(hours=6)
PLUG_IN_HINT_MIN_WH = 1000.0
MIN_CHARGE_W = 100.0
SLOT_HOURS = 0.25
# A car without a departure (set or learned) is charged within this time
VEHICLE_DEADLINE = timedelta(hours=48)
# The learned main usage counts as deadline only this long ahead, so there is time to heat
HEATER_MIN_LEAD = timedelta(hours=3)
# Reason of a consumer without a daily need (a recommendation asks for it)
NO_DAILY_NEED = N_("Energiebedarf pro Tag fehlt")
# Without a configured daily need the tank is assumed to cool down by this much per day
HEATER_DAILY_DROP_K = 10
# A hot water tank at this temperature counts as empty (0 %)
COLD_WATER_C = 15.0


@dataclass(kw_only=True)
class FlexInfo:
    """Need of a loadpoint as seen by the planner, also when it is not planned."""

    device_id: str
    name: str
    kind: str
    current: float | None
    target: float | None
    unit: str
    need_wh: float
    deadline: datetime | None
    max_power_w: float
    # Need in every following 24 h window (hot water used, daily consumption)
    daily_wh: float = 0.0
    # The deadline is the typical departure learned from the history
    learned_departure: bool = False
    reason: str | None = None  # why nothing is planned
    # Wallboxes with assigned vehicles: the one recognized, and how
    vehicle_id: str | None = None
    vehicle: str | None = None
    vehicle_method: str | None = None
    # A car is connected but not one of the assigned: the wallbox regulates itself
    unknown_vehicle: bool = False
    # Level over time (vehicle %, tank °C): its sensor, energy per unit (from the grid),
    # what the storage loses per day, and the range shown as 0..100 %
    level_entity: str | None = None
    wh_per_unit: float | None = None
    loss_wh_per_day: float = 0.0
    level_range: tuple[float, float] | None = None
    # Vehicles: battery capacity, to show the level as energy
    capacity_wh: float | None = None
    # Hot water and cars on the way: the usual draw per slot (learned), and from how many days
    drawn_wh: list[float] = field(default_factory=list)
    usage_days: int = 0
    # A car on the way: when it usually comes back (planned from then on), with which SoC
    expected_arrival: datetime | None = None
    arrival_soc: float | None = None
    # At home (its presence sensor) but not plugged in: worth plugging in when energy is planned
    at_home: bool = False


def plug_in_hints(infos: list[FlexInfo], slots: list[Any], now: datetime) -> list[dict[str, Any]]:
    """Cars at home but not plugged in that the plan gives energy soon: worth plugging in."""
    hints = []
    for info in infos:
        if not info.at_home:
            continue
        soon = [s for s in slots if s.start < now + PLUG_IN_HINT_AHEAD and (s.flex_w or {}).get(info.name, 0.0) >= MIN_CHARGE_W]
        energy = sum(s.flex_w[info.name] for s in soon) * SLOT_HOURS
        if energy < PLUG_IN_HINT_MIN_WH:
            continue
        # The share the PV surplus covers (what the house does not use)
        pv = sum(min(s.flex_w[info.name], max(s.pv_w - s.load_w, 0.0)) for s in soon) * SLOT_HOURS
        hints.append(
            {
                "vehicle_id": info.vehicle_id, "vehicle": info.vehicle, "wallbox_id": info.device_id, "wallbox": info.name,
                "start": soon[0].start.isoformat(), "energy_wh": round(energy), "pv_wh": round(pv),
            }
        )
    return hints


def is_home(vehicle_id: str, presences: dict[str, Departure] | None, states: dict[str, dict[str, Any]] | None) -> bool:
    """Whether the car's presence sensor says it is at home now."""
    entity = (presences or {})[vehicle_id].entity_id if vehicle_id in (presences or {}) else None
    return str((states or {}).get(entity or "", {}).get("state", "")).lower() in PLUGGED


def coming_back(recognized: Recognized, assigned: list[Vehicle], connected: bool) -> tuple[Vehicle | None, str | None]:
    """The recognized car; with none plugged in, the only car of the wallbox is the one that comes back."""
    if recognized.vehicle is None and not connected and len(assigned) == 1:
        return assigned[0], "expected"
    return recognized.vehicle, recognized.method


def on_the_way(
    info: FlexInfo, connected: bool, usages: dict[str, VehicleUsage] | None, habits: dict[str, Departure] | None,
    slot_starts: list[datetime], tz: tzinfo, presences: dict[str, Departure] | None = None,
    states: dict[str, dict[str, Any]] | None = None,
) -> list[float] | None:
    """SoC a car on the way uses per slot until its usual arrival (none while plugged in); None without enough learned data or its SoC.

    A car at home but not plugged in may be plugged in any time; a car's own presence
    habit (when it comes home) wins over the plug-in times at the wallbox.
    """
    if connected:
        return []
    vehicle = info.vehicle_id or ""
    if vehicle in (presences or {}) and is_home(vehicle, presences, states) and info.current is not None:
        info.expected_arrival, info.at_home = slot_starts[0], True
        return [0.0] * len(slot_starts)
    usage = (usages or {}).get(vehicle)
    presence = (presences or {}).get(vehicle)
    habit = presence if presence is not None and presence.arrival_workday is not None else (habits or {}).get(info.device_id)
    arrival = habit.next_arrival(slot_starts[0], tz) if habit is not None and usage is not None else None
    if usage is None or arrival is None or arrival >= slot_starts[-1] + SLOT or info.current is None:
        return None
    info.expected_arrival, info.usage_days = arrival, usage.days
    return [usage.used_pct(start, min(start + SLOT, arrival), tz) if start < arrival else 0.0 for start in slot_starts]


def hot_water_need(
    info: FlexInfo, drawn: HotWaterUsage | None, ready_by: datetime | None, params: dict[str, Any],
    slot_starts: list[datetime], tz: tzinfo,
) -> None:
    """Deadline, daily need and draw per slot of a tank, from its learned usage where known."""
    wh_per_kelvin = params["tank_l"] * WATER_WH_PER_LITRE_KELVIN
    # Without a ready time: hot by the usual main usage, with time enough to heat
    first = ready_by or (drawn.next_peak(slot_starts[0] + HEATER_MIN_LEAD, tz) if drawn else slot_starts[0] + HEATER_DEADLINE)
    info.deadline = min(first, slot_starts[-1] + SLOT)
    info.daily_wh = params.get("daily_energy_wh") or (drawn.daily_wh if drawn else HEATER_DAILY_DROP_K * wh_per_kelvin)
    info.wh_per_unit, info.loss_wh_per_day = wh_per_kelvin, info.daily_wh
    if drawn is not None:
        info.drawn_wh = [drawn.drawn_wh(start, start + SLOT, tz) for start in slot_starts]
        info.usage_days = drawn.days


def expected_levels(info: FlexInfo, slots: list[Any]) -> list[float]:
    """Level at the end of every slot (vehicle %, tank °C) from the planned power and the losses."""
    if info.current is None or not info.wh_per_unit:
        return []
    level, result = info.current, []
    for index, slot in enumerate(slots):
        hours = (slot.end - slot.start).total_seconds() / 3600
        # The learned draw of this slot, else an even share of the daily need
        drawn = info.drawn_wh[index] if index < len(info.drawn_wh) else info.loss_wh_per_day * hours / 24
        energy = (slot.flex_w or {}).get(info.name, 0.0) * hours - drawn
        level += energy / info.wh_per_unit
        if info.level_range is not None:
            low, high = info.level_range
            # A car stops at full; a tank does not cool below cold water
            level = min(max(level, low), high) if info.kind == "vehicle" else max(level, low)
        result.append(round(level, 2))
    return result


def _number(states: dict[str, dict[str, Any]], entity_id: str | None) -> float | None:
    """The numeric state of an entity; None without a usable value."""
    try:
        return float(states[entity_id]["state"]) if entity_id else None
    except KeyError, TypeError, ValueError:
        return None


def charger_need(
    info: FlexInfo, device: DeviceConfig, charger: ChargerReady | None, states: dict[str, dict[str, Any]], target: float | None
) -> str | None:
    """Need of a load with a battery of its own; the reason when it is not planned (not ready, no energy)."""
    if charger is None or not charger.ready or device.charger is None:
        return t("Nicht ladebereit (nicht angesteckt oder voll)")
    soc = _number(states, device.charger.soc_entity)
    if soc is not None and device.charger.capacity_wh:
        info.current, info.unit = soc, "%"
        info.target = target if target is not None else 100.0
        info.need_wh = max(info.target - soc, 0.0) / 100 * device.charger.capacity_wh / CHARGER_EFFICIENCY
    elif charger.energy_wh:
        info.need_wh = charger.energy_wh
    else:
        return t("Energie je Ladung fehlt (Einstellungen → Geräte → Akku laden)")
    return None


def flex_loads(
    config: SunflowConfig,
    profiles: dict[str, dict[str, Any]],
    snapshot: Snapshot,
    slot_starts: list[datetime],
    tz: tzinfo,
    battery_capacity_wh: float | None = None,
    departures: dict[str, Departure] | None = None,
    usages: dict[str, ConsumerUsage] | None = None,
    hot_water: dict[str, HotWaterUsage] | None = None,
    vehicle_usages: dict[str, VehicleUsage] | None = None,
    presences: dict[str, Departure] | None = None,
    chargers: dict[str, ChargerReady] | None = None,
) -> tuple[list[FlexLoad], list[FlexInfo], list[str], tuple[tuple[str, float], ...]]:
    """Derive the energy need of every flexible consumer from live values and its settings.

    Also returns the storages (name, Wh) the home battery waits for before charging from PV.
    """
    loads: list[FlexLoad] = []
    by_device: dict[str, FlexLoad] = {}
    # Per vehicle: current level and target in %, Wh per %
    # Per vehicle or hot water tank: current level and target (% or °C), Wh per unit
    storages: dict[str, tuple[float, float, float]] = {}
    infos: list[FlexInfo] = []
    warnings: list[str] = []
    if not slot_starts:
        return loads, infos, warnings, ()
    horizon_end = slot_starts[-1] + SLOT
    order = config.priority_order()
    battery_rank = order.index(BATTERY_PRIORITY_ID)
    flex_ids = [i for i in order if i != BATTERY_PRIORITY_ID]
    for device in config.devices:
        profile = profiles[device.profile]
        if "loadpoint" not in profile["capabilities"] and "load" not in profile["capabilities"]:
            continue
        settings, paused = config.effective_flex(device, slot_starts[0])
        ready_by = next_time(settings.ready_by, slot_starts[0], tz) if settings.ready_by else None
        roles = {
            r.role: r
            for r in preview(
                profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()}
            )
        }
        params = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot)}
        kind = device_kind(profile, device, snapshot) or ("load" if "load" in profile["capabilities"] else "vehicle")
        usage = (usages or {}).get(device.id)
        max_power = params.get("max_power") or (usage.running_w if usage and usage.running_w else 0.0)
        # A loadpoint reports the tank temperature as its "SoC", an own heating consumer as temperature
        level = roles.get("vehicle_soc") or roles.get("temperature")
        current = level.value if level is not None else None
        target = roles["limit_soc"].value if "limit_soc" in roles else None
        if kind == "heater" and target is None:
            target = DEFAULT_HOT_WATER_C
        if settings.target is not None:
            target = settings.target
        info = FlexInfo(
            device_id=device.id,
            name=device.name,
            kind=kind,
            current=current,
            target=target,
            unit={"heater": "°C", "vehicle": "%"}.get(kind, ""),
            need_wh=0.0,
            deadline=None,
            max_power_w=max_power,
        )
        infos.append(info)
        info.level_entity = level.entity_id if level is not None else None

        if kind == "heater":
            litres = params.get("tank_l")
            if not litres:
                info.reason = t("Speichervolumen fehlt in den Kenndaten")
                warnings.append(t("{name}: Speichervolumen fehlt – Warmwasser wird nicht eingeplant.", name=device.name))
                continue
            if current is None or target is None:
                info.reason = t("Temperatur oder Ziel nicht lesbar")
                continue
            info.need_wh = max(target - current, 0) * litres * WATER_WH_PER_LITRE_KELVIN
            hot_water_need(info, (hot_water or {}).get(device.id), ready_by, params, slot_starts, tz)
            storages[device.id] = (current, target, litres * WATER_WH_PER_LITRE_KELVIN)
            # Shown as share between cold water and the target
            info.level_range = (COLD_WATER_C, max(target, COLD_WATER_C + 1))
            if settings.alternative_cost_ct is None:
                warnings.append(
                    t(
                        "{name}: Kosten der üblichen Heizung (z. B. Fernwärme) fehlen – Sunflow heizt im Zweifel mit Netzstrom. Preis unter Einstellungen → Verbraucher eintragen.",
                        name=device.name,
                    )
                )
        elif kind == "load" and device.charger is not None:
            if (reason := charger_need(info, device, (chargers or {}).get(device.id), snapshot.states, target)) is not None:
                info.reason = reason
                continue
            info.deadline = min(ready_by or slot_starts[0] + HEATER_DEADLINE, horizon_end)
        elif kind == "load":
            # Set by the user, else learned from the power sensor
            daily = params.get("daily_energy_wh") or (usage.daily_wh if usage else None)
            if not daily:
                info.reason = t(NO_DAILY_NEED)
                continue
            info.need_wh = daily
            info.deadline = min(ready_by or slot_starts[0] + HEATER_DEADLINE, horizon_end)
            info.daily_wh = daily
        else:
            connected = roles.get("connected") is not None and roles["connected"].raw_state == "on"
            if assigned := config.vehicles_at(device.id):
                recognized = recognize(config, device, roles, snapshot.states)
                vehicle, method = coming_back(recognized, assigned, connected)
                if vehicle is None:
                    if connected:
                        info.reason = t("{reason} – Wallbox regelt selbst", reason=recognized.reason)
                        info.unknown_vehicle = True
                    else:
                        info.reason = t("Fahrzeug nicht angesteckt")
                    continue
                info.vehicle_id, info.vehicle, info.vehicle_method = vehicle.id, vehicle.name, method
                if (limit := vehicle_max_power(vehicle)) is not None and max_power:
                    # The car's own charger may take less than the wallbox gives
                    max_power = info.max_power_w = min(max_power, limit)
                params = params | {"vehicle_capacity_wh": vehicle.capacity_wh, "charge_efficiency_pct": vehicle.charge_efficiency_pct}
                current = info.current = soc_of(vehicle, snapshot.states)
                info.level_entity = vehicle.soc_entity or info.level_entity
                info.capacity_wh, info.level_range = vehicle.capacity_wh, (0.0, 100.0)
                # How this vehicle charges at this wallbox; a change in the control tab still applies on top
                profile_at = config.charging_profile(vehicle.id, device.id)
                settings, paused = config.effective_flex(device, slot_starts[0], profile_at.flex if profile_at else None)
                ready_by = next_time(settings.ready_by, slot_starts[0], tz) if settings.ready_by else None
                target = info.target = settings.target if settings.target is not None else target
            # On the way: planned from its usual arrival with the SoC it usually comes back with
            used = on_the_way(info, connected, vehicle_usages, departures, slot_starts, tz, presences, snapshot.states)
            if used is None:
                info.reason = t("Fahrzeug nicht angesteckt")
                # Nothing planned and no usage learned: the chart keeps its SoC until it is plugged in
                if (capacity := params.get("vehicle_capacity_wh")) and info.current is not None:
                    info.wh_per_unit, info.capacity_wh, info.level_range = capacity / 100, capacity, (0.0, 100.0)
                continue
            current = info.arrival_soc = max(info.current - sum(used), 0.0) if used else current
            capacity = params.get("vehicle_capacity_wh")
            if not capacity:
                info.reason = t("Akkukapazität fehlt in den Kenndaten")
                warnings.append(t("{name}: Akkukapazität des Fahrzeugs fehlt – Laden wird nicht eingeplant.", name=device.name))
                continue
            if current is None:
                info.reason = t("Ladestand des Fahrzeugs unbekannt")
                continue
            learned = (departures or {}).get(device.id)
            if ready_by:
                info.deadline = min(ready_by, horizon_end)
            elif learned and (departure := learned.next_after(info.expected_arrival or slot_starts[0], tz)) is not None:
                # No departure set: the car usually leaves at this time
                info.deadline = min(departure, horizon_end)
                info.learned_departure = True
            else:
                # A longer plan must not put charging off for days
                info.deadline = min(slot_starts[0] + VEHICLE_DEADLINE, horizon_end)
            plan_soc = roles["plan_soc"].value if "plan_soc" in roles else None
            plan_time = roles["plan_time"].raw_state if "plan_time" in roles else None
            if plan_soc and plan_time and plan_time not in ("unknown", "unavailable"):
                # A charging plan in evcc sets target and departure
                try:
                    info.deadline = min(datetime.fromisoformat(plan_time), horizon_end)
                    target = info.target = plan_soc
                except ValueError:
                    pass
            efficiency = (params.get("charge_efficiency_pct") or 90) / 100
            info.need_wh = max((target or 100) - current, 0) / 100 * capacity / efficiency
            info.wh_per_unit, info.level_range = capacity / 100 / efficiency, (0.0, 100.0)
            info.capacity_wh = capacity
            # The chart drains the car on its way (the expected levels start from now)
            info.drawn_wh = [pct * info.wh_per_unit for pct in used]
            storages[device.id] = (current, target or 100, capacity / efficiency / 100)

        later_needs = daily_needs(slot_starts, info.deadline, info.daily_wh) if info.daily_wh else []
        if info.need_wh <= 0 and not later_needs:
            info.reason = t("Ziel bereits erreicht")
            continue
        if paused:
            info.reason = t("pausiert")
            continue
        rank = flex_ids.index(device.id) if device.id in flex_ids else len(flex_ids)
        end = slot_starts[later_needs[-1][1] - 1] + SLOT if later_needs else info.deadline
        by_device[device.id] = load = (
            FlexLoad(
                name=device.name,
                energy_wh=info.need_wh,
                later_needs=later_needs,
                max_power_w=max_power,
                available=[start < end and (info.expected_arrival is None or start >= info.expected_arrival) for start in slot_starts],
                # With an alternative heat source the need is optional at that price
                unmet_cost=settings.alternative_cost_ct / 100
                if settings.alternative_cost_ct is not None
                else MUST_HAVE_COST,
                pv_only=settings.strategy == "pv_only",
                urgency=FAST_URGENCY if settings.strategy == "fast" else 0.0,
                # "Only PV surplus" excludes the home battery as a source
                uses_battery=settings.uses_battery and settings.strategy != "pv_only",
                # 0.1 ct/kWh per rank: decides only when loads compete for the same energy
                priority_bonus=PRIORITY_STEP * (len(flex_ids) - rank),
                below_battery=order.index(device.id) > battery_rank if device.id in order else True,
                expected=info.expected_arrival is not None,
            )
        )
        loads.append(load)
    loads = list(with_conditions(config, by_device, storages, battery_capacity_wh, slot_starts[0]).values())
    return loads, infos, warnings, storage_gates(config, BATTERY_PRIORITY_ID, by_device, storages)


def storage_gates(
    config: SunflowConfig,
    device_id: str,
    by_device: dict[str, FlexLoad],
    storages: dict[str, tuple[float, float, float]],
) -> tuple[tuple[str, float], ...]:
    """Energy (Wh) the planned vehicles and tanks above an entry must get before it is released."""
    gates = []
    for condition in config.valid_conditions(device_id):
        # A vehicle not plugged in or a storage that is not planned does not hold anything back
        if condition.device_id in storages and condition.device_id in by_device:
            current, target, wh_per_unit = storages[condition.device_id]
            # A condition above the storage's target is met once the target is reached
            required = max(min(condition.min_pct, target) - current, 0) * wh_per_unit
            if required > 0:
                gates.append((by_device[condition.device_id].name, required))
    return tuple(gates)


def with_conditions(
    config: SunflowConfig,
    by_device: dict[str, FlexLoad],
    storages: dict[str, tuple[float, float, float]],
    battery_capacity_wh: float | None,
    now: datetime,
) -> dict[str, FlexLoad]:
    """Apply the priority conditions as gates; storages map to (current, target, Wh per unit)."""
    result = {}
    for device_id, load in by_device.items():
        # "Sofort" ignores the conditions
        device = config.device(device_id)
        if device is None or config.effective_flex(device, now)[0].strategy == "fast":
            result[device_id] = load
            continue
        battery_wh: float | None = None
        for condition in config.valid_conditions(device_id):
            if condition.device_id == BATTERY_PRIORITY_ID and battery_capacity_wh:
                battery_wh = max(battery_wh or 0.0, battery_capacity_wh * condition.min_pct / 100)
        after = list(storage_gates(config, device_id, by_device, storages))
        result[device_id] = replace(load, gate_battery_wh=battery_wh, gate_after=after) if battery_wh or after else load
    return result


def daily_needs(slot_starts: list[datetime], first_deadline: datetime, daily_wh: float) -> list[tuple[int, int, float]]:
    """Recurring 24 h needs after the first deadline, as long as a whole window fits the horizon."""
    needs = []
    window_start = first_deadline
    while True:
        window_end = window_start + timedelta(hours=24)
        if window_end > slot_starts[-1] + SLOT:
            return needs
        first = sum(1 for start in slot_starts if start < window_start)
        end = sum(1 for start in slot_starts if start < window_end)
        needs.append((first, end, daily_wh))
        window_start = window_end


def next_time(hhmm: str, after: datetime, tz: tzinfo) -> datetime:
    """Return the next local wall-clock time hh:mm after the given moment."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    local = after.astimezone(tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        # Build tomorrow's time from the date so DST changes keep the wall clock
        tomorrow = (local + timedelta(days=1)).date()
        candidate = datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute, tzinfo=tz)
    return candidate


@dataclass(kw_only=True)
class PlanResult:
    """Plan with the inputs' provenance and warnings."""

    plan: Plan
    battery_device: str | None = None
    load: LoadForecast | None = None
    price_source: str = "none"
    pv_planes: int = 0
    flex: list[FlexInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    battery: BatterySpec | None = None
    reserve_wh: float = 0.0
    care: BatteryCare | None = None
    # Uncorrected PV forecast and forecast cloud coverage per slot (for learning)
    raw_pv: list[float] = field(default_factory=list)
    plane_forecasts_now: dict[str, float] = field(default_factory=dict)
    plane_corrections: dict[str, PvCorrection] = field(default_factory=dict)
    clouds: list[float | None] = field(default_factory=list)
    temperatures: list[float | None] = field(default_factory=list)
    load_model: LoadModel | None = None
    usages: dict[str, ConsumerUsage] = field(default_factory=dict)
    vehicle_usages: dict[str, VehicleUsage] = field(default_factory=dict)
    plug_in_hints: list[dict[str, Any]] = field(default_factory=list)
    pv_correction: PvCorrection | None = None


async def consumer_usages(
    client: HAClient, config: SunflowConfig, profiles: dict[str, dict[str, Any]], snapshot: Snapshot
) -> dict[str, ConsumerUsage]:
    """Learned daily energy (and running power) of own consumers with a power sensor or energy counter."""
    result = {}
    for device in config.devices:
        profile = profiles[device.profile]
        if "load" not in profile["capabilities"]:
            continue
        roles = {
            r.role: r
            for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
            if r.entity_id
        }
        # The power sensor also tells the running power; a counter only the energy
        if "power" in roles:
            entity, learner = roles["power"].entity_id, learn_usage
        elif "energy" in roles:
            entity, learner = roles["energy"].entity_id, learn_usage_from_energy
        else:
            continue
        try:
            usage = await cached(("usage", entity), HABITS_TTL, lambda entity=entity, learner=learner: learner(client, entity))
        except HAError:
            continue
        if usage is not None:
            result[device.id] = usage
    return result


async def hot_water_usages(
    client: HAClient, config: SunflowConfig, profiles: dict[str, dict[str, Any]], snapshot: Snapshot, now: datetime, tz: tzinfo
) -> dict[str, HotWaterUsage]:
    """Learned hot water usage of tanks with a temperature and a heating power sensor."""
    result = {}
    for device in config.devices:
        profile = profiles[device.profile]
        if device_kind(profile, device, snapshot) != "heater":
            continue
        roles = {
            r.role: r
            for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
            if r.entity_id
        }
        params = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot)}
        level = roles.get("temperature") or roles.get("vehicle_soc")
        power = roles.get("power") or roles.get("charge_power")
        if level is None or power is None or not params.get("tank_l"):
            continue
        wh_per_kelvin = params["tank_l"] * WATER_WH_PER_LITRE_KELVIN
        try:
            usage = await cached(
                ("hot_water", level.entity_id, power.entity_id),
                HABITS_TTL,
                lambda t=level.entity_id, p=power.entity_id, k=wh_per_kelvin: learn_hot_water(client, t, p, k, now, tz),
            )
        except HAError:
            continue
        if usage is not None:
            result[device.id] = usage
    return result


async def learned_vehicle_usages(
    client: HAClient, config: SunflowConfig, now: datetime, tz: tzinfo, own: dict[str, list[tuple[datetime, float]]] | None
) -> dict[str, VehicleUsage]:
    """Learned use per car from its SoC statistics, else from Sunflow's own samples (own: car id -> hourly SoC)."""
    result = {}
    for vehicle in config.vehicles:
        if not vehicle.soc_entity:
            continue
        try:
            socs = await cached(("hourly", vehicle.soc_entity), HABITS_TTL, lambda e=vehicle.soc_entity: fetch_hourly_means(client, e, now))
        except HAError:
            socs = []
        # A sensor without statistics: the samples Sunflow kept itself
        if (usage := vehicle_usage_from(socs or (own or {}).get(vehicle.id, []), tz)) is not None:
            result[vehicle.id] = usage
    return result


async def vehicle_presences(client: HAClient, config: SunflowConfig, tz: tzinfo) -> dict[str, Departure]:
    """When each car with a presence sensor usually comes home and leaves."""
    result = {}
    for vehicle in config.vehicles:
        if not vehicle.plugged_entity:
            continue
        try:
            result[vehicle.id] = await cached(
                ("presence", vehicle.plugged_entity), HABITS_TTL, lambda e=vehicle.plugged_entity: learn_departure(client, e, tz)
            )
        except HAError:
            continue
    return result


async def vehicle_departures(
    client: HAClient, config: SunflowConfig, profiles: dict[str, dict[str, Any]], snapshot: Snapshot, tz: tzinfo
) -> dict[str, Departure]:
    """Typical departure per loadpoint with a connected sensor (vehicles only)."""
    result = {}
    for device in config.devices:
        profile = profiles[device.profile]
        if "loadpoint" not in profile["capabilities"] or device_kind(profile, device, snapshot) != "vehicle":
            continue
        role = next(
            (
                r
                for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
                if r.role == "connected"
            ),
            None,
        )
        if role is None or not role.entity_id:
            continue
        try:
            result[device.id] = await cached(
                ("departure", role.entity_id), HABITS_TTL, lambda entity=role.entity_id: learn_departure(client, entity, tz)
            )
        except HAError:
            continue
    return result


def weather_entity(config: SunflowConfig, snapshot: Snapshot) -> str | None:
    """The configured weather entity, else the first one in Home Assistant."""
    if config.optimizer.weather_entity:
        return config.optimizer.weather_entity
    return next((e for e in sorted(snapshot.states) if e.startswith("weather.")), None)


async def weather_forecast(
    client: HAClient, entity_id: str | None, slot_starts: list[datetime], attributes: tuple[str, ...]
) -> dict[str, list[float | None]]:
    """Values of the weather entity's hourly forecast (e.g. cloud_coverage, temperature) per slot."""
    empty = {attribute: [None] * len(slot_starts) for attribute in attributes}
    if not entity_id or not slot_starts:
        return empty
    try:
        response = await client.call_service(
            "weather", "get_forecasts", {"type": "hourly"}, target={"entity_id": entity_id}, return_response=True
        )
        forecast = response["response"][entity_id]["forecast"]
    except (HAError, KeyError, TypeError):
        # Corrections and models then work without weather
        return empty
    by_hour = {
        datetime.fromisoformat(item["datetime"]).astimezone(UTC).replace(minute=0, second=0, microsecond=0): item
        for item in forecast
        if "datetime" in item
    }
    if not by_hour:
        return empty
    known = sorted(by_hour)

    def nearest(moment: datetime) -> dict[str, Any] | None:
        # The hourly forecast starts with the next hour; the current one takes its neighbour
        hour = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if hour in by_hour:
            return by_hour[hour]
        closest = min(known, key=lambda h: abs(h - hour))
        return by_hour[closest] if abs(closest - hour) <= timedelta(hours=2) else None

    items = [nearest(start) for start in slot_starts]
    return {attribute: [item.get(attribute) if item else None for item in items] for attribute in attributes}


async def cloud_coverage(client: HAClient, entity_id: str | None, slot_starts: list[datetime]) -> list[float | None]:
    """Forecast cloud coverage (%) per slot."""
    return (await weather_forecast(client, entity_id, slot_starts, ("cloud_coverage",)))["cloud_coverage"]


@dataclass(kw_only=True)
class BatteryCare:
    """Charge limit and due full charge of the plan."""

    limit_wh: float | None = None
    last_full: datetime | None = None
    full_charge_slot: int | None = None
    full_charge_at: datetime | None = None
    # Overdue: charge full from the grid if PV does not do it in time
    from_grid: bool = False
    # A day with less PV than consumption ahead, for which the charge limit is lifted
    poor_day: date | None = None
    notes: list[str] = field(default_factory=list)


WEEKDAYS = (N_("Mo"), N_("Di"), N_("Mi"), N_("Do"), N_("Fr"), N_("Sa"), N_("So"))


def german_time(moment: datetime) -> str:
    """'Mo 16:30' independent of the system locale."""
    return f"{t(WEEKDAYS[moment.weekday()])} {moment:%H:%M}"


def short_date(moment: date | datetime) -> str:
    """'24.12.' in German, '12/24' in English."""
    return t("{day}.{month}.", day=f"{moment:%d}", month=f"{moment:%m}")


def cheapest_window_end(inputs: list[SlotInput], hours: int = 24) -> int:
    """Last slot of the first block with the lowest price within the next hours."""
    ahead = inputs[: hours * 4]
    lowest = min(slot.price for slot in ahead)
    first = next(t for t, slot in enumerate(ahead) if slot.price <= lowest + 1e-9)
    last = first
    while last + 1 < len(ahead) and ahead[last + 1].price <= lowest + 1e-9:
        last += 1
    return last


# Share of the way from the expected (p50) towards the pessimistic PV forecast (p10)
PV_RISK = {"expected": 0.0, "balanced": 0.3, "cautious": 0.7}

# A due full charge is worth this much per kWh missing; overdue it must happen
OVERDUE_FULL_CHARGE_PENALTY = 2.0


# The SoC counts as full from here (BMS readings rarely show exactly 100)
FULL_SOC_PCT = 99


async def last_full_charge(client: HAClient, entity_id: str | None, days: int) -> datetime | None:
    """Start of the last hour in which the SoC reached full, from the hourly statistics."""
    if not entity_id:
        return None
    end = datetime.now(UTC)
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=(end - timedelta(days=days)).isoformat(),
        end_time=end.isoformat(),
        statistic_ids=[entity_id],
        period="hour",
        types=["max"],
    )
    full = [row["start"] for row in result.get(entity_id, []) if (row.get("max") or 0) >= FULL_SOC_PCT]
    return datetime.fromtimestamp(max(full) / 1000, UTC) if full else None


def poor_pv_day(pv: list[ForecastPoint], inputs: list[SlotInput], tz: tzinfo, days: int) -> date | None:
    """First of the next days whose PV forecast is below the expected consumption.

    Tomorrow and the following days up to days ahead; today does not count, its PV is
    what the battery is charged with now. Consumption is the mean of the load forecast,
    the PV forecast the full range of the source (beyond the price horizon).
    """
    if not inputs or not pv:
        return None
    daily_load_wh = sum(slot.load_w for slot in inputs) / len(inputs) * 24
    today = inputs[0].start.astimezone(tz).date()
    energy: dict[date, float] = {}
    for point in pv:
        day = point.start.astimezone(tz).date()
        if today < day <= today + timedelta(days=days):
            energy[day] = energy.get(day, 0.0) + point.power * (point.end - point.start).total_seconds() / 3600
    # A day only counts when the forecast reaches past it (some sources leave out the night)
    last = max(point.end for point in pv).astimezone(tz)
    covered = [day for day in sorted(energy) if last >= datetime.combine(day, datetime.max.time(), tz) - timedelta(hours=4)]
    return next((day for day in covered if energy[day] < daily_load_wh), None)


async def battery_care(
    client: HAClient,
    config: SunflowConfig,
    profile: dict[str, Any],
    device: DeviceConfig,
    snapshot: Snapshot,
    battery: BatterySpec,
    inputs: list[SlotInput],
    tz: tzinfo,
    pv: list[ForecastPoint] | None = None,
) -> BatteryCare:
    """Everyday charge limit, and when a full charge is due the slot to reach it.

    The full charge is aimed at the end of the PV surplus on the day it falls due (or
    later in the horizon), so it comes from PV rather than the grid. With
    "unless_poor_days" the limit is lifted while a poor PV day is ahead.
    """
    options = config.optimizer
    care = BatteryCare()
    if options.max_soc_pct < 100:
        poor = (
            poor_pv_day(pv or [], inputs, tz, options.poor_days_ahead)
            if options.max_soc_mode == "unless_poor_days"
            else None
        )
        if poor is None:
            care.limit_wh = battery.capacity_wh * options.max_soc_pct / 100
        else:
            care.poor_day = poor
            care.notes.append(
                t(
                    "Wenig PV erwartet am {weekday} {date} – Ladegrenze ausgesetzt, der Speicher darf voll laden.",
                    weekday=t(WEEKDAYS[poor.weekday()]), date=short_date(poor),
                )
            )
    every = options.full_charge_every_days
    grid_after = options.full_charge_grid_after_days
    if not (every or grid_after) or not inputs:
        return care
    window = max(every or 0, grid_after or 0) + 3
    try:
        entity = soc_entity(profile, device, snapshot)
        care.last_full = await cached(
            ("full", str(entity), str(window)), HOURLY_TTL, lambda: last_full_charge(client, entity, window)
        )
    except HACommandError:
        # Unknown counts as not full within the window: a full charge gets planned
        care.last_full = None
    since = short_date(care.last_full.astimezone(tz)) if care.last_full else t("vor über {days} Tagen", days=window)
    # Not full within the queried window counts as overdue as well
    if grid_after and (care.last_full is None or inputs[0].start - care.last_full >= timedelta(days=grid_after)):
        care.full_charge_slot = cheapest_window_end(inputs)
        care.full_charge_at = inputs[care.full_charge_slot].start + SLOT
        care.from_grid = True
        care.notes.append(
            t(
                "Vollladung überfällig (zuletzt voll: {since}) – wird bis {time} zum günstigsten Preis aus dem Netz nachgeholt, falls PV nicht reicht.",
                since=since, time=german_time(care.full_charge_at.astimezone(tz)),
            )
        )
        return care
    if not every:
        return care
    due = (care.last_full + timedelta(days=every)) if care.last_full else inputs[0].start
    if due > inputs[-1].start:
        return care
    # Last slot with PV surplus on each day from the due day on
    candidates: dict[Any, int] = {}
    due_day = due.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    for index, slot in enumerate(inputs):
        if slot.start >= due_day and slot.pv_w > slot.load_w:
            candidates[slot.start.astimezone(tz).date()] = index
    if not candidates:
        care.notes.append(t("Vollladung fällig (zuletzt voll: {since}) – im Planungszeitraum ist kein PV-Überschuss in Sicht.", since=since))
        return care
    care.full_charge_slot = candidates[min(candidates)]
    care.full_charge_at = inputs[care.full_charge_slot].start + SLOT
    care.notes.append(
        t("Vollladung fällig (zuletzt voll: {since}) – geplant bis {time}.", since=since, time=german_time(care.full_charge_at.astimezone(tz)))
    )
    return care


# The problem of a battery whose SoC cannot be read (then the history's last value stands in)
SOC_UNREADABLE = N_("{name}: Ladestand nicht lesbar.")
# How old the last valid SoC from the history may be when the live value is missing
SOC_FALLBACK_AGE = timedelta(hours=6)


def soc_entity(profile: dict[str, Any], device: DeviceConfig, snapshot: Snapshot) -> str | None:
    """Entity of the battery SoC role."""
    role = next(
        (
            r
            for r in preview(
                profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()}
            )
            if r.role == "battery_soc"
        ),
        None,
    )
    return role.entity_id if role else None


async def last_valid_value(client: HAClient, entity_id: str | None, age: timedelta) -> tuple[float, datetime] | None:
    """Last numeric state of an entity within age, with the time it was reported."""
    if not entity_id:
        return None
    end = datetime.now(UTC)
    result = await client.command(
        "history/history_during_period",
        start_time=(end - age).isoformat(),
        end_time=end.isoformat(),
        entity_ids=[entity_id],
        minimal_response=True,
        no_attributes=True,
    )
    for row in reversed(result.get(entity_id, [])):
        try:
            return float(row["s"]), datetime.fromtimestamp(row["lu"], UTC)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def battery_spec(
    profile: dict[str, Any],
    device: DeviceConfig,
    snapshot: Snapshot,
    soc_fallback: float | None = None,
    modules: list[str] | None = None,
) -> tuple[BatterySpec | None, str | None]:
    """Build the battery spec from parameters and the live SoC; returns (spec, problem).

    soc_fallback (%) stands in when the live SoC cannot be read.
    """
    params = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot, modules)}
    capacity = params.get("capacity_wh")
    if not capacity:
        return None, t("{name}: Kapazität unbekannt – bitte in den Kenndaten angeben.", name=device.name)
    roles = {
        r.role: r.value
        for r in preview(
            profile,
            snapshot,
            device.ha_device_id,
            {k: o.model_dump() for k, o in device.role_overrides.items()},
        )
    }
    soc = roles.get("battery_soc")
    if soc is None:
        soc = soc_fallback
    if soc is None:
        return None, t(SOC_UNREADABLE, name=device.name)
    # Battery round trip at its terminals (cells, DC/DC), split evenly between directions;
    # the inverter's AC/DC conversion applies to grid charging and to every discharge
    battery_way = math.sqrt((params.get("efficiency_pct") or 90) / 100)
    inverter = (params.get("inverter_efficiency_pct") or 100) / 100
    return (
        BatterySpec(
            capacity_wh=capacity,
            soc_wh=capacity * soc / 100,
            min_soc_wh=capacity * (params.get("min_soc") or 0) / 100,
            max_charge_w=params.get("max_charge") or 0,
            max_discharge_w=params.get("max_discharge") or 0,
            efficiency_charge=battery_way,
            efficiency_charge_grid=battery_way * inverter,
            efficiency_discharge=battery_way * inverter,
        ),
        None,
    )


# Excluded large-load hours worth a notice: more than this many and this share of the history
LARGE_LOAD_NOTICE_HOURS = 5
LARGE_LOAD_NOTICE_SHARE = 0.02


def large_load_notice(load: LoadForecast, threshold_w: float | None) -> str | None:
    """A notice when many hours were left out of the load forecast as large loads."""
    # A few rare peaks (oven, sauna) are what the exclusion is for; many hint at an unknown large consumer
    if threshold_w is None or load.excluded_hours <= max(LARGE_LOAD_NOTICE_HOURS, LARGE_LOAD_NOTICE_SHARE * load.history_hours):
        return None
    return t(
        "{hours} h Hausverbrauch über {threshold} kW (ohne Wallbox, Heizstab und andere Verbraucher in Sunflow) zählen nicht zur Lastprognose. Steckt ein regelmäßiger Großverbraucher dahinter, lege ihn als Verbraucher an, damit Sunflow ihn einplant.",
        hours=load.excluded_hours, threshold=number(threshold_w / 1000),
    )


# Import penalty for "autarky": far above price differences and wear, so the battery
# covers every demand it can and loads go to the PV surplus
AUTARKY_IMPORT_PENALTY_CT = 100.0

# Export penalty for "pv_absorption": large enough to keep room for PV before sunny days
PV_ABSORPTION_PENALTY_CT = 5.0


def optimizer_settings(config: SunflowConfig, battery: BatterySpec) -> Settings:
    """Translate the user's planning settings and strategy into optimizer settings."""
    options = config.optimizer
    strategy = config.effective_strategy(datetime.now(UTC))
    autarky = strategy == "autarky"
    return Settings(
        wear_ct_per_kwh=options.wear_ct_per_kwh,
        # Autarky never charges from the grid
        allow_grid_charge=options.allow_grid_charge and not autarky,
        import_penalty_ct_per_kwh=AUTARKY_IMPORT_PENALTY_CT if autarky else 0.0,
        export_soft_limit_w=options.export_limit_w if options.grid_friendly else None,
        import_soft_limit_w=options.import_limit_w if options.grid_friendly else None,
        export_penalty_ct_per_kwh=PV_ABSORPTION_PENALTY_CT if strategy == "pv_absorption" else 0.0,
        reserve_wh=battery.capacity_wh * options.backup_reserve_pct / 100 if options.backup_reserve else 0.0,
        high_soc_wh=battery.capacity_wh * options.high_soc_threshold_pct / 100 if options.high_soc_aging else None,
        high_soc_ct_per_kwh_h=options.high_soc_ct_per_kwh_h,
    )


def restrict_to_device(settings: Settings, actions: set[str], warnings: list[str]) -> Settings:
    """Only plan what the battery can be told: grid charging needs force_charge."""
    if "force_charge" not in actions and settings.allow_grid_charge:
        settings = replace(settings, allow_grid_charge=False)
        warnings.append(t("Der Speicher kann nicht zum Laden gezwungen werden – kein Laden aus dem Netz eingeplant."))
    if "force_discharge" not in actions:
        settings = replace(settings, allow_battery_export=False)
    return settings


def device_actions(plan: Plan, profile: dict[str, Any]) -> None:
    """Slot actions as the device carries them out (the closest supported one), in place."""
    for slot in plan.slots:
        if (action := supported_action(profile, slot.action, slot.action_power_w))[0] != slot.action:
            slot.action, slot.action_power_w = action


def feed_in_of(config: SunflowConfig, battery_device: DeviceConfig | None) -> FeedIn | None:
    """Feed-in of the battery inverter's plant first, else the first configured rate."""
    candidates = ([battery_device] if battery_device else []) + config.devices
    for device in candidates:
        if (feed_in := config.feed_in.get(device.id)) and feed_in.rate_ct_per_kwh is not None:
            return feed_in
    return None


def feed_in_price(config: SunflowConfig, battery_device: DeviceConfig | None) -> float:
    """Feed-in €/kWh (see feed_in_of)."""
    feed_in = feed_in_of(config, battery_device)
    return feed_in.rate_ct_per_kwh / 100 if feed_in and feed_in.rate_ct_per_kwh is not None else 0.0


def slot_feed_in(inputs: list[SlotInput], feed_in: FeedIn | None) -> list[SlotInput]:
    """Feed-in per slot: under the solar peak law nothing is paid at negative prices.

    Only the import price is known; when even it is negative the market price is too.
    """
    if feed_in is None or feed_in.model != "solar_peak":
        return inputs
    return [replace(slot, feed_in=0.0) if slot.price < 0 else slot for slot in inputs]


def curtailable_shares(
    config: SunflowConfig,
    profiles: dict[str, dict[str, Any]],
    planes: list[ForecastPlane],
    plane_slots: dict[str, list[float]],
    n: int,
) -> list[float]:
    """Share of the PV per slot from plants Sunflow may limit (their planes).

    Only plants switched to Sunflow's control: limiting often needs extra rights (e.g. the
    installer login), so a plant that merely could be limited is planned without.
    """
    limited = {d.id for d in config.devices if "pv_limit" in profiles[d.profile] and config.control.devices.get(d.id)}
    keys = {p.key for p in planes if p.device_id in limited}
    if not keys:
        return [0.0] * n
    shares = []
    for index in range(n):
        total = sum(powers[index] for powers in plane_slots.values())
        part = sum(powers[index] for key, powers in plane_slots.items() if key in keys)
        shares.append(part / total if total > 0 else 0.0)
    return shares


def value_at(points: list[Any], moment: datetime, attribute: str) -> float | None:
    """Return the attribute of the point covering moment."""
    for point in points:
        if point.start <= moment < point.end:
            return getattr(point, attribute)
    return None


def build_inputs(
    start: datetime,
    prices: list[PricePoint],
    pv: list[ForecastPoint],
    load: LoadForecast,
    feed_in: float,
) -> list[SlotInput]:
    """Align all series on 15 minute slots until prices run out (at most the horizon)."""
    pv_15 = resample(pv, SLOT)
    inputs = []
    moment = start
    for _ in range(HORIZON_HOURS * 4):
        point = next((p for p in prices if p.start <= moment < p.end), None)
        if prices and point is None:
            break
        price = point.price if point is not None else NOMINAL_PRICE
        inputs.append(
            SlotInput(
                start=moment,
                pv_w=value_at(pv_15, moment, "power") or 0.0,
                pv_low_w=value_at(pv_15, moment, "power10"),
                load_w=value_at(load.points, moment, "power") or 0.0,
                price=price,
                feed_in=feed_in,
                price_estimated=point.estimated if point is not None else False,
            )
        )
        moment += SLOT
    return inputs


# Weight of the live PV deviation halves over this time ahead
NOWCAST_HALF_LIFE = timedelta(minutes=60)
NOWCAST_RANGE = (0.2, 3.0)
# Weight of the live consumption deviation halves over this time ahead
LOAD_NOWCAST_HALF_LIFE = timedelta(minutes=30)
# Twilight: the ratio of small values says nothing
NOWCAST_MIN_W = 150.0
# Cloud coverage (%) below which the sky counts as clear, and the share of the risk left then
CLEAR_SKY_CLOUD = 20
CLEAR_SKY_RISK = 0.25


def nowcast_weights(count: int) -> list[float]:
    """How much the measured PV counts per slot ahead: fully now, half after an hour."""
    return [0.5 ** ((t * SLOT) / NOWCAST_HALF_LIFE) for t in range(count)]


def nowcast(inputs: list[SlotInput], live_pv_w: float) -> list[SlotInput]:
    """Carry the current deviation of the measured PV over to the next hours.

    Clouds that are there now (or a sunnier hour than forecast) usually last a while:
    the forecast is scaled by measured / forecast, fully now and fading out ahead.
    """
    forecast = inputs[0].pv_w
    if max(forecast, live_pv_w) < NOWCAST_MIN_W:
        return inputs
    ratio = min(max((live_pv_w + 50) / (forecast + 50), NOWCAST_RANGE[0]), NOWCAST_RANGE[1])
    result = []
    for slot, weight in zip(inputs, nowcast_weights(len(inputs)), strict=True):
        factor = 1 + (ratio - 1) * weight
        low = slot.pv_low_w * factor if slot.pv_low_w is not None else None
        result.append(replace(slot, pv_w=slot.pv_w * factor, pv_low_w=low))
    return result


def load_nowcast(inputs: list[SlotInput], live_load_w: float) -> list[SlotInput]:
    """Start the consumption forecast at what the house uses now, fading into the forecast.

    The forecast knows typical hours, not the moment: someone away or a device running
    now usually lasts a while, but less long than a cloud does.
    """
    difference = live_load_w - inputs[0].load_w
    return [
        replace(slot, load_w=max(slot.load_w + difference * 0.5 ** ((t * SLOT) / LOAD_NOWCAST_HALF_LIFE), 0.0))
        for t, slot in enumerate(inputs)
    ]


def risk_shares(risk: float, clouds: list[float | None], measured: list[float]) -> list[float]:
    """Share of the way towards the pessimistic forecast per slot.

    Nothing where the PV is being measured (the nowcast covers it) and only a quarter
    under a clear sky: the forecast's spread is about clouds that may or may not come.
    """
    return [
        risk * (1 - known) * (CLEAR_SKY_RISK if cloud is not None and cloud < CLEAR_SKY_CLOUD else 1.0)
        for cloud, known in zip(clouds, measured, strict=True)
    ]


async def compute_plan(
    client: HAClient,
    config: SunflowConfig,
    profiles: dict[str, dict[str, Any]],
    snapshot: Snapshot,
    start: datetime,
    tz: tzinfo,
    pv_correction: PvCorrection | None = None,
    load_model: LoadModel | None = None,
    plane_corrections: dict[str, PvCorrection] | None = None,
    live_pv_w: float | None = None,
    live_load_w: float | None = None,
    consumer_hours: list[tuple[datetime, float]] | None = None,
    vehicle_hours: dict[str, list[tuple[datetime, float]]] | None = None,
    chargers: ChargerWatch | None = None,
) -> PlanResult:
    """Fetch forecasts and prices, then optimize the battery schedule.

    consumer_hours: hourly power of all controllable consumers from Sunflow's own samples;
    vehicle_hours: hourly SoC per car from them.
    """
    warnings: list[str] = []
    battery_device = next(
        (d for d in config.devices if controls_battery(profiles[d.profile])),
        None,
    )
    if battery_device is None:
        return PlanResult(plan=Plan(status="no battery"), warnings=[t("Kein steuerbarer Speicher übernommen.")])
    profile = profiles[battery_device.profile]
    modules = config.battery_modules.get(battery_device.id)
    battery, problem = battery_spec(profile, battery_device, snapshot, modules=modules)
    if battery is None and problem == t(SOC_UNREADABLE, name=battery_device.name):
        # Inverter integrations drop single reads ("unknown"); the last valid value from
        # the history is good enough, the SoC changes slowly
        try:
            fallback = await last_valid_value(client, soc_entity(profile, battery_device, snapshot), SOC_FALLBACK_AGE)
        except HACommandError:
            fallback = None
        if fallback is not None:
            value, since = fallback
            battery, problem = battery_spec(profile, battery_device, snapshot, soc_fallback=value, modules=modules)
            if since < datetime.now(UTC) - timedelta(minutes=30):
                warnings.append(
                    t(
                        "{name}: Ladestand seit {time} nicht lesbar – geplant mit dem letzten Wert {value} %.",
                        name=battery_device.name, time=since.astimezone(tz).strftime("%H:%M"), value=f"{value:.0f}",
                    )
                )
    if battery is None:
        return PlanResult(plan=Plan(status="no battery data"), warnings=[problem or ""])
    if not battery.max_charge_w or not battery.max_discharge_w:
        warnings.append(t("Lade- oder Entladeleistung ist 0 – der Speicher kann so nicht genutzt werden."))

    # Prices
    price_source = "none"
    prices: list[PricePoint] = []
    if config.tariff.import_entity_id and (state := snapshot.states.get(config.tariff.import_entity_id)):
        tariff = config.tariff
        entry = next((e for e in snapshot.entities if e["entity_id"] == tariff.import_entity_id), None)
        try:
            prices = await fetch_prices(
                client, state, entry, start, HORIZON_HOURS, tz, tariff.exchange_surcharge_ct, tariff.exchange_vat_pct
            )
        except HAError as err:
            warnings.append(t("Strompreise nicht abrufbar ({error}) – geplant wird nur auf Eigenverbrauch.", error=err))
        if prices and max(p.end for p in prices) < start + timedelta(hours=HORIZON_HOURS):
            # Beyond the published prices: the usual ones of each hour (the sensor's history)
            try:
                history = await cached(
                    ("hourly", tariff.import_entity_id), HABITS_TTL,
                    lambda: fetch_hourly_means(client, tariff.import_entity_id or "", start, PRICE_HISTORY_DAYS),
                )
            except HAError:
                history = []
            factor = price_factor(state.get("attributes", {}).get("unit_of_measurement")) or 1.0
            history = [(moment, price * factor) for moment, price in history]
            if is_exchange(state, entry):
                history = [(p.start, p.price) for p in with_surcharges(
                    [PricePoint(start=m, end=m + SLOT, price=v) for m, v in history],
                    tariff.exchange_surcharge_ct, tariff.exchange_vat_pct,
                )]
            prices = extend_prices(prices, history, start + timedelta(hours=HORIZON_HOURS), tz)
        if is_exchange(state, entry) and tariff.exchange_surcharge_ct is None:
            warnings.append(
                t(
                    "Der Strompreis ist ein reiner Börsenpreis – Netzentgelt, Umlagen und Steuern fehlen. Aufschlag und Mehrwertsteuer unter Einstellungen → Tarif & Prognose eintragen."
                )
            )
        price_source = "entity"
    elif config.tariff.fixed_price_ct is not None:
        prices = [PricePoint(start=start, end=start + timedelta(hours=HORIZON_HOURS), price=config.tariff.fixed_price_ct / 100)]
        price_source = "fixed"
    else:
        warnings.append(t("Kein Bezugspreis – geplant wird nur auf Eigenverbrauch."))

    # PV forecast of all used planes
    series = []
    used_planes = []
    for plane in config.forecast_planes:
        if plane.device_id is None:
            continue
        # One failing forecast integration must not stop the plan; the others still count
        try:
            match plane.source:
                case "solcast":
                    points = await solcast_forecast(client, plane.ref, start, HORIZON_HOURS)
                case "forecast_solar":
                    points = await forecast_solar_forecast(client, plane.ref, start, HORIZON_HOURS)
                case _:
                    continue
        except HACommandError as err:
            warnings.append(
                t("PV-Prognose „{plane}“ nicht abrufbar ({error}) – geplant wird ohne diese Fläche.", plane=plane.display_name, error=err)
            )
            continue
        series.append(points)
        used_planes.append(plane)
    if not series:
        warnings.append(t("Keine PV-Prognose – geplant wird ohne PV-Ertrag."))
    # Published prices; estimated ones only reach as far as every PV forecast
    published_end = max((p.end for p in prices if not p.estimated), default=start + timedelta(hours=HORIZON_HOURS))
    pv_ends = [max((p.end for p in points), default=start) for points in series]
    if pv_ends:
        prices = [p for p in prices if not p.estimated or p.end <= min(pv_ends)]
    for plane, last in zip(used_planes, pv_ends, strict=False):
        if last < published_end - timedelta(hours=12):
            warnings.append(
                t(
                    "PV-Prognose „{plane}“ reicht nur bis {time} – danach fehlt ihr Ertrag im Plan (die PV-Prognose ist dort eher zu niedrig).",
                    plane=plane.display_name, time=german_time(last.astimezone(tz)),
                )
            )
    pv = sum_forecasts(series) if series else []

    # Load forecast from the history of the battery inverter's load sensor
    roles = preview(
        profile,
        snapshot,
        battery_device.ha_device_id,
        {k: o.model_dump() for k, o in battery_device.role_overrides.items()},
    )
    history = []
    if load_entity := find_load_entity(roles):
        try:
            history = await cached(("hourly", load_entity), HOURLY_TTL, lambda: fetch_hourly_means(client, load_entity, start))
        except HACommandError as err:
            warnings.append(t("Verlauf des Hausverbrauchs nicht abrufbar ({error}) – Lastprognose mit Standardwerten.", error=err))
    # Loadpoints (wallbox, heater) are part of the measured household load
    loadpoint_histories = []
    for device in config.devices:
        capabilities = profiles[device.profile]["capabilities"]
        if "loadpoint" not in capabilities and "load" not in capabilities:
            continue
        lp_roles = preview(
            profiles[device.profile],
            snapshot,
            device.ha_device_id,
            {k: o.model_dump() for k, o in device.role_overrides.items()},
        )
        if entity := next(
            (r.entity_id for r in lp_roles if r.role in ("charge_power", "power") and r.entity_id), None
        ):
            try:
                loadpoint_histories.append(
                    await cached(("hourly", entity), HOURLY_TTL, lambda: fetch_hourly_means(client, entity, start))
                )
            except HACommandError as err:
                warnings.append(t("Verlauf von {name} nicht abrufbar ({error}) – bleibt im Hausverbrauch.", name=device.name, error=err))
    if consumer_hours:
        loadpoint_histories.append(unmeasured_consumers(consumer_hours, loadpoint_histories))
    history, separated = subtract_loads(history, loadpoint_histories)
    load = build_load_forecast(
        history,
        start,
        HORIZON_HOURS,
        tz,
        large_load_w=config.optimizer.large_load_threshold_w,
        daily_kwh=config.optimizer.daily_consumption_kwh,
    )
    load.separated_hours = separated
    if load.history_hours < 7 * 24 and not config.optimizer.daily_consumption_kwh:
        warnings.append(
            t(
                "Erst {hours} h Verbrauchshistorie – gib deinen durchschnittlichen Tagesverbrauch in den Einstellungen an, damit die Lastprognose stimmt.",
                hours=load.history_hours,
            )
        )
    if notice := large_load_notice(load, config.optimizer.large_load_threshold_w):
        warnings.append(notice)

    settings = optimizer_settings(config, battery)
    inputs = slot_feed_in(build_inputs(start, prices, pv, load, feed_in_price(config, battery_device)), feed_in_of(config, battery_device))
    if len(inputs) < MIN_HORIZON_HOURS * 4:
        warnings.append(t("Preise reichen nur wenige Stunden in die Zukunft."))
    raw_pv = [slot.pv_w for slot in inputs]
    # Forecast per plane and slot, to learn and correct each plane on its own
    plane_slots = {
        plane.key: [value_at(resample(points, SLOT), slot.start, "power") or 0.0 for slot in inputs]
        for plane, points in zip(used_planes, series, strict=False)
    }
    weather = await weather_forecast(
        client, weather_entity(config, snapshot), [slot.start for slot in inputs], ("cloud_coverage", "temperature")
    )
    clouds = weather["cloud_coverage"]
    if load_model is not None and load_model.useful:
        # Learned from this household with the outside temperature; slots without a
        # temperature keep the statistical forecast
        inputs = [
            replace(slot, load_w=load_model.predict(slot.start.astimezone(tz), temperature))
            if temperature is not None
            else slot
            for slot, temperature in zip(inputs, weather["temperature"], strict=True)
        ]
    own = {key: c for key, c in (plane_corrections or {}).items() if c.slots and key in plane_slots}
    site = pv_correction if pv_correction is not None and pv_correction.slots else None
    if config.optimizer.pv_learning and (site or own):
        corrected = []
        for index, (slot, cloud) in enumerate(zip(inputs, clouds, strict=True)):
            hour, kind = slot.start.astimezone(tz).hour, cloud_class(cloud)
            site_factor = site.factor(hour, kind) if site else 1.0
            # Each plane with its own factor (e.g. shading of one roof), the rest with the site's
            total = sum(powers[index] for powers in plane_slots.values())
            weighted = sum(
                powers[index] * (own[key].factor(hour, kind) if key in own else site_factor)
                for key, powers in plane_slots.items()
            )
            factor = weighted / total if total > 0 else site_factor
            low = slot.pv_low_w * factor if slot.pv_low_w is not None else None
            corrected.append(replace(slot, pv_w=slot.pv_w * factor, pv_low_w=low))
        inputs = corrected
    measured = [0.0] * len(inputs)
    if live_pv_w is not None and inputs:
        inputs = nowcast(inputs, live_pv_w)
        measured = nowcast_weights(len(inputs))
    if live_load_w is not None and inputs:
        inputs = load_nowcast(inputs, live_load_w)
    if (risk := PV_RISK[config.optimizer.pv_risk]) > 0:
        # Plan part of the way towards the pessimistic forecast: a shortfall costs grid
        # power at the full price, a surplus only the feed-in tariff
        inputs = [
            replace(slot, pv_w=slot.pv_w - share * max(slot.pv_w - slot.pv_low_w, 0.0))
            if slot.pv_low_w is not None
            else slot
            for slot, share in zip(inputs, risk_shares(risk, clouds, measured), strict=True)
        ]
    departures = await vehicle_departures(client, config, profiles, snapshot, tz)
    usages = await consumer_usages(client, config, profiles, snapshot)
    hot_water = await hot_water_usages(client, config, profiles, snapshot, start, tz)
    vehicle_usages = await learned_vehicle_usages(client, config, start, tz, vehicle_hours)
    presences = await vehicle_presences(client, config, tz)
    loads, flex_infos, flex_warnings, battery_gate = flex_loads(
        config,
        profiles,
        snapshot,
        [i.start for i in inputs],
        tz,
        battery_capacity_wh=battery.capacity_wh,
        departures=departures,
        usages=usages,
        hot_water=hot_water,
        vehicle_usages=vehicle_usages,
        presences=presences,
        chargers=chargers.planning(config, snapshot.states) if chargers else None,
    )
    settings = replace(settings, battery_gate_after=battery_gate)
    warnings += flex_warnings
    care = await battery_care(client, config, profile, battery_device, snapshot, battery, inputs, tz, pv)
    settings = replace(settings, soc_limit_wh=care.limit_wh, full_charge_slot=care.full_charge_slot)
    if care.from_grid:
        # Explicitly wanted by the user: grid charging for the overdue full charge
        settings = replace(settings, allow_grid_charge=True, full_charge_penalty=OVERDUE_FULL_CHARGE_PENALTY)
    warnings += care.notes
    settings = restrict_to_device(settings, battery_actions(profile), warnings)
    shares = curtailable_shares(config, profiles, used_planes, plane_slots, len(inputs))
    inputs = [replace(slot, curtailable_w=slot.pv_w * share) for slot, share in zip(inputs, shares, strict=True)]
    # A car only expected back may come later or not at all: charged where cheaper than the median price
    worth = median(slot.price for slot in inputs) if inputs else MUST_HAVE_COST
    loads = [replace(load, unmet_cost=worth) if load.expected else load for load in loads]
    # The solver is CPU bound; keep the event loop (HA connection, API) responsive
    plan = await asyncio.to_thread(optimize, inputs, battery, settings, loads)
    device_actions(plan, profile)
    return PlanResult(
        raw_pv=raw_pv,
        plane_forecasts_now={key: powers[0] for key, powers in plane_slots.items() if powers},
        plane_corrections=own if config.optimizer.pv_learning else {},
        clouds=clouds,
        temperatures=weather["temperature"],
        load_model=load_model if load_model is not None and load_model.useful else None,
        usages=usages,
        vehicle_usages=vehicle_usages,
        plug_in_hints=plug_in_hints(flex_infos, plan.slots, start),
        pv_correction=pv_correction if config.optimizer.pv_learning else None,
        care=care,
        plan=plan,
        battery_device=battery_device.id,
        load=load,
        price_source=price_source,
        pv_planes=len(series),
        flex=flex_infos,
        warnings=warnings,
        battery=battery,
        reserve_wh=settings.reserve_wh,
    )
