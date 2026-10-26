"""Collect forecasts, prices and battery state and run the optimizer."""

import asyncio
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from .config_store import BATTERY_PRIORITY_ID, DeviceConfig, SunflowConfig
from .discovery import Snapshot
from .ha import HAClient, HAError
from .load_forecast import (
    LoadForecast,
    build_load_forecast,
    fetch_hourly_means,
    find_load_entity,
    subtract_loads,
)
from .optimizer import BatterySpec, FlexLoad, Plan, Settings, SlotInput, optimize
from .habits import ConsumerUsage, Departure, learn_departure, learn_usage
from .load_model import LoadModel
from .pv_learning import PvCorrection, cloud_class
from .resolver import preview
from .site import device_kind, device_parameters
from .sources import (
    ForecastPoint,
    PricePoint,
    forecast_solar_forecast,
    price_series,
    resample,
    solcast_forecast,
    sum_forecasts,
)

HORIZON_HOURS = 48
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
HEATER_DEADLINE = timedelta(hours=24)
# Without a configured daily need the tank is assumed to cool down by this much per day
HEATER_DAILY_DROP_K = 10


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


def flex_loads(
    config: SunflowConfig,
    profiles: dict[str, dict[str, Any]],
    snapshot: Snapshot,
    slot_starts: list[datetime],
    tz: tzinfo,
    battery_capacity_wh: float | None = None,
    departures: dict[str, Departure] | None = None,
    usages: dict[str, ConsumerUsage] | None = None,
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
        current = roles["vehicle_soc"].value if "vehicle_soc" in roles else None
        target = roles["limit_soc"].value if "limit_soc" in roles else None
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

        if kind == "heater":
            litres = params.get("tank_l")
            if not litres:
                info.reason = "Speichervolumen fehlt in den Kenndaten"
                warnings.append(f"{device.name}: Speichervolumen fehlt – Warmwasser wird nicht eingeplant.")
                continue
            if current is None or target is None:
                info.reason = "Temperatur oder Ziel nicht lesbar"
                continue
            info.need_wh = max(target - current, 0) * litres * WATER_WH_PER_LITRE_KELVIN
            info.deadline = min(ready_by or slot_starts[0] + HEATER_DEADLINE, horizon_end)
            info.daily_wh = params.get("daily_energy_wh") or HEATER_DAILY_DROP_K * litres * WATER_WH_PER_LITRE_KELVIN
            storages[device.id] = (current, target, litres * WATER_WH_PER_LITRE_KELVIN)
            if settings.alternative_cost_ct is None:
                warnings.append(
                    f"{device.name}: Kosten der üblichen Heizung (z. B. Fernwärme) fehlen – Sunflow heizt im "
                    "Zweifel mit Netzstrom. Preis unter Kenndaten → Planung eintragen."
                )
        elif kind == "load":
            # Set by the user, else learned from the power sensor
            daily = params.get("daily_energy_wh") or (usage.daily_wh if usage else None)
            if not daily:
                info.reason = "Energiebedarf pro Tag fehlt"
                continue
            info.need_wh = daily
            info.deadline = min(ready_by or slot_starts[0] + HEATER_DEADLINE, horizon_end)
            info.daily_wh = daily
        else:
            if roles.get("connected") is None or roles["connected"].raw_state != "on":
                info.reason = "Fahrzeug nicht angesteckt"
                continue
            capacity = params.get("vehicle_capacity_wh")
            if not capacity:
                info.reason = "Akkukapazität fehlt in den Kenndaten"
                warnings.append(f"{device.name}: Akkukapazität des Fahrzeugs fehlt – Laden wird nicht eingeplant.")
                continue
            if current is None:
                info.reason = "Ladestand des Fahrzeugs unbekannt"
                continue
            learned = (departures or {}).get(device.id)
            if ready_by:
                info.deadline = min(ready_by, horizon_end)
            elif learned and (departure := learned.next_after(slot_starts[0], tz)) is not None:
                # No departure set: the car usually leaves at this time
                info.deadline = min(departure, horizon_end)
                info.learned_departure = True
            else:
                info.deadline = horizon_end
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
            storages[device.id] = (current, target or 100, capacity / efficiency / 100)

        later_needs = daily_needs(slot_starts, info.deadline, info.daily_wh) if info.daily_wh else []
        if info.need_wh <= 0 and not later_needs:
            info.reason = "Ziel bereits erreicht"
            continue
        if paused:
            info.reason = "pausiert"
            continue
        rank = flex_ids.index(device.id) if device.id in flex_ids else len(flex_ids)
        end = slot_starts[later_needs[-1][1] - 1] + SLOT if later_needs else info.deadline
        by_device[device.id] = load = (
            FlexLoad(
                name=device.name,
                energy_wh=info.need_wh,
                later_needs=later_needs,
                max_power_w=max_power,
                available=[start < end for start in slot_starts],
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
    clouds: list[float | None] = field(default_factory=list)
    temperatures: list[float | None] = field(default_factory=list)
    load_model: LoadModel | None = None
    usages: dict[str, ConsumerUsage] = field(default_factory=dict)
    pv_correction: PvCorrection | None = None


async def consumer_usages(
    client: HAClient, config: SunflowConfig, profiles: dict[str, dict[str, Any]], snapshot: Snapshot
) -> dict[str, ConsumerUsage]:
    """Learned daily energy and running power of own consumers with a power sensor."""
    result = {}
    for device in config.devices:
        profile = profiles[device.profile]
        if "load" not in profile["capabilities"]:
            continue
        role = next(
            (
                r
                for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
                if r.role == "power"
            ),
            None,
        )
        if role is None or not role.entity_id:
            continue
        try:
            usage = await learn_usage(client, role.entity_id)
        except HAError:
            continue
        if usage is not None:
            result[device.id] = usage
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
            result[device.id] = await learn_departure(client, role.entity_id, tz)
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
    notes: list[str] = field(default_factory=list)


WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def german_time(moment: datetime) -> str:
    """'Mo 16:30' independent of the system locale."""
    return f"{WEEKDAYS[moment.weekday()]} {moment:%H:%M}"


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


async def battery_care(
    client: HAClient,
    config: SunflowConfig,
    profile: dict[str, Any],
    device: DeviceConfig,
    snapshot: Snapshot,
    battery: BatterySpec,
    inputs: list[SlotInput],
    tz: tzinfo,
) -> BatteryCare:
    """Everyday charge limit, and when a full charge is due the slot to reach it.

    The full charge is aimed at the end of the PV surplus on the day it falls due (or
    later in the horizon), so it comes from PV rather than the grid.
    """
    options = config.optimizer
    care = BatteryCare()
    if options.max_soc_pct < 100:
        care.limit_wh = battery.capacity_wh * options.max_soc_pct / 100
    every = options.full_charge_every_days
    grid_after = options.full_charge_grid_after_days
    if not (every or grid_after) or not inputs:
        return care
    window = max(every or 0, grid_after or 0) + 3
    care.last_full = await last_full_charge(client, soc_entity(profile, device, snapshot), window)
    since = care.last_full.astimezone(tz).strftime("%d.%m.") if care.last_full else f"vor über {window} Tagen"
    # Not full within the queried window counts as overdue as well
    if grid_after and (care.last_full is None or inputs[0].start - care.last_full >= timedelta(days=grid_after)):
        care.full_charge_slot = cheapest_window_end(inputs)
        care.full_charge_at = inputs[care.full_charge_slot].start + SLOT
        care.from_grid = True
        care.notes.append(
            f"Vollladung überfällig (zuletzt voll: {since}) – wird bis {german_time(care.full_charge_at.astimezone(tz))} "
            "zum günstigsten Preis aus dem Netz nachgeholt, falls PV nicht reicht."
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
    for t, slot in enumerate(inputs):
        if slot.start >= due_day and slot.pv_w > slot.load_w:
            candidates[slot.start.astimezone(tz).date()] = t
    if not candidates:
        care.notes.append(f"Vollladung fällig (zuletzt voll: {since}) – im Planungszeitraum ist kein PV-Überschuss in Sicht.")
        return care
    care.full_charge_slot = candidates[min(candidates)]
    care.full_charge_at = inputs[care.full_charge_slot].start + SLOT
    care.notes.append(
        f"Vollladung fällig (zuletzt voll: {since}) – geplant bis "
        f"{german_time(care.full_charge_at.astimezone(tz))}."
    )
    return care


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
    profile: dict[str, Any], device: DeviceConfig, snapshot: Snapshot, soc_fallback: float | None = None
) -> tuple[BatterySpec | None, str | None]:
    """Build the battery spec from parameters and the live SoC; returns (spec, problem).

    soc_fallback (%) stands in when the live SoC cannot be read.
    """
    params = {p["name"]: p["effective"] for p in device_parameters(profile, device, snapshot)}
    capacity = params.get("capacity_wh")
    if not capacity:
        return None, f"{device.name}: Kapazität unbekannt – bitte in den Kenndaten angeben."
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
        return None, f"{device.name}: Ladestand nicht lesbar."
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
        export_penalty_ct_per_kwh=PV_ABSORPTION_PENALTY_CT if strategy == "pv_absorption" else 0.0,
        reserve_wh=battery.capacity_wh * options.backup_reserve_pct / 100 if strategy == "backup" else 0.0,
    )


def feed_in_price(config: SunflowConfig, battery_device: DeviceConfig | None) -> float:
    """Feed-in €/kWh: the battery inverter's plant first, else the first configured rate."""
    candidates = ([battery_device] if battery_device else []) + config.devices
    for device in candidates:
        if device.feed_in and device.feed_in.rate_ct_per_kwh is not None:
            return device.feed_in.rate_ct_per_kwh / 100
    return 0.0


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
    """Align all series on 15 minute slots until prices run out (max 48 h)."""
    pv_15 = resample(pv, SLOT)
    inputs = []
    moment = start
    for _ in range(HORIZON_HOURS * 4):
        price = value_at(prices, moment, "price") if prices else NOMINAL_PRICE
        if price is None:
            break
        inputs.append(
            SlotInput(
                start=moment,
                pv_w=value_at(pv_15, moment, "power") or 0.0,
                pv_low_w=value_at(pv_15, moment, "power10"),
                load_w=value_at(load.points, moment, "power") or 0.0,
                price=price,
                feed_in=feed_in,
            )
        )
        moment += SLOT
    return inputs


async def compute_plan(
    client: HAClient,
    config: SunflowConfig,
    profiles: dict[str, dict[str, Any]],
    snapshot: Snapshot,
    start: datetime,
    tz: tzinfo,
    pv_correction: PvCorrection | None = None,
    load_model: LoadModel | None = None,
) -> PlanResult:
    """Fetch forecasts and prices, then optimize the battery schedule."""
    warnings: list[str] = []
    battery_device = next(
        (d for d in config.devices if "battery_window" in profiles[d.profile]["capabilities"]),
        None,
    )
    if battery_device is None:
        return PlanResult(plan=Plan(status="no battery"), warnings=["Kein steuerbarer Speicher übernommen."])
    profile = profiles[battery_device.profile]
    battery, problem = battery_spec(profile, battery_device, snapshot)
    if battery is None and problem and "Ladestand" in problem:
        # Inverter integrations drop single reads ("unknown"); the last valid value from
        # the history is good enough, the SoC changes slowly
        fallback = await last_valid_value(client, soc_entity(profile, battery_device, snapshot), SOC_FALLBACK_AGE)
        if fallback is not None:
            value, since = fallback
            battery, problem = battery_spec(profile, battery_device, snapshot, soc_fallback=value)
            if since < datetime.now(UTC) - timedelta(minutes=30):
                warnings.append(
                    f"{battery_device.name}: Ladestand seit {since.astimezone(tz).strftime('%H:%M')} nicht lesbar – "
                    f"geplant mit dem letzten Wert {value:.0f} %."
                )
    if battery is None:
        return PlanResult(plan=Plan(status="no battery data"), warnings=[problem or ""])
    if not battery.max_charge_w or not battery.max_discharge_w:
        warnings.append("Lade- oder Entladeleistung ist 0 – der Speicher kann so nicht genutzt werden.")

    # Prices
    price_source = "none"
    prices: list[PricePoint] = []
    if config.tariff.import_entity_id and (state := snapshot.states.get(config.tariff.import_entity_id)):
        prices = price_series(state, start, HORIZON_HOURS, tz)
        price_source = "entity"
    elif config.tariff.fixed_price_ct is not None:
        prices = [PricePoint(start=start, end=start + timedelta(hours=HORIZON_HOURS), price=config.tariff.fixed_price_ct / 100)]
        price_source = "fixed"
    else:
        warnings.append("Kein Bezugspreis – geplant wird nur auf Eigenverbrauch.")

    # PV forecast of all used planes
    series = []
    for plane in config.forecast_planes:
        if plane.device_id is None:
            continue
        match plane.source:
            case "solcast":
                series.append(await solcast_forecast(client, plane.ref, start, HORIZON_HOURS))
            case "forecast_solar":
                series.append(await forecast_solar_forecast(client, plane.ref, start, HORIZON_HOURS))
    if not series:
        warnings.append("Keine PV-Prognose – geplant wird ohne PV-Ertrag.")
    horizon_end = start + timedelta(hours=HORIZON_HOURS)
    used_planes = [p for p in config.forecast_planes if p.device_id is not None]
    for plane, points in zip(used_planes, series, strict=False):
        last = max((p.end for p in points), default=start)
        if last < horizon_end - timedelta(hours=12):
            warnings.append(
                f"PV-Prognose „{plane.display_name}“ reicht nur bis {german_time(last.astimezone(tz))} – "
                "danach fehlt ihr Ertrag im Plan (die PV-Prognose ist dort eher zu niedrig)."
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
        history = await fetch_hourly_means(client, load_entity, start)
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
            loadpoint_histories.append(await fetch_hourly_means(client, entity, start))
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
            f"Erst {load.history_hours} h Verbrauchshistorie – gib deinen durchschnittlichen Tagesverbrauch "
            "in den Einstellungen an, damit die Lastprognose stimmt."
        )
    if load.excluded_hours:
        warnings.append(
            f"{load.excluded_hours} h mit mehr als "
            f"{config.optimizer.large_load_threshold_w / 1000:.1f}".replace(".", ",") + " kW "
            "(z. B. Wallbox) sind aus der Lastprognose ausgeklammert – solche Lasten plant Sunflow noch nicht ein."
        )

    settings = optimizer_settings(config, battery)
    inputs = build_inputs(start, prices, pv, load, feed_in_price(config, battery_device))
    if len(inputs) < MIN_HORIZON_HOURS * 4:
        warnings.append("Preise reichen nur wenige Stunden in die Zukunft.")
    raw_pv = [slot.pv_w for slot in inputs]
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
    if config.optimizer.pv_learning and pv_correction is not None and pv_correction.slots:
        corrected = []
        for slot, cloud in zip(inputs, clouds, strict=True):
            factor = pv_correction.factor(slot.start.astimezone(tz).hour, cloud_class(cloud))
            low = slot.pv_low_w * factor if slot.pv_low_w is not None else None
            corrected.append(replace(slot, pv_w=slot.pv_w * factor, pv_low_w=low))
        inputs = corrected
    if (risk := PV_RISK[config.optimizer.pv_risk]) > 0:
        # Plan part of the way towards the pessimistic forecast: a shortfall costs grid
        # power at the full price, a surplus only the feed-in tariff
        inputs = [
            replace(slot, pv_w=slot.pv_w - risk * max(slot.pv_w - slot.pv_low_w, 0.0))
            if slot.pv_low_w is not None
            else slot
            for slot in inputs
        ]
    departures = await vehicle_departures(client, config, profiles, snapshot, tz)
    usages = await consumer_usages(client, config, profiles, snapshot)
    loads, flex_infos, flex_warnings, battery_gate = flex_loads(
        config,
        profiles,
        snapshot,
        [i.start for i in inputs],
        tz,
        battery_capacity_wh=battery.capacity_wh,
        departures=departures,
        usages=usages,
    )
    settings = replace(settings, battery_gate_after=battery_gate)
    warnings += flex_warnings
    care = await battery_care(client, config, profile, battery_device, snapshot, battery, inputs, tz)
    settings = replace(settings, soc_limit_wh=care.limit_wh, full_charge_slot=care.full_charge_slot)
    if care.from_grid:
        # Explicitly wanted by the user: grid charging for the overdue full charge
        settings = replace(settings, allow_grid_charge=True, full_charge_penalty=OVERDUE_FULL_CHARGE_PENALTY)
    warnings += care.notes
    # The solver is CPU bound; keep the event loop (HA connection, API) responsive
    plan = await asyncio.to_thread(optimize, inputs, battery, settings, loads)
    return PlanResult(
        raw_pv=raw_pv,
        clouds=clouds,
        temperatures=weather["temperature"],
        load_model=load_model if load_model is not None and load_model.useful else None,
        usages=usages,
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
