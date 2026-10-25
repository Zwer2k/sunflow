"""Collect forecasts, prices and battery state and run the optimizer."""

import asyncio
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from .config_store import BATTERY_PRIORITY_ID, DeviceConfig, SunflowConfig
from .discovery import Snapshot
from .ha import HAClient
from .load_forecast import (
    LoadForecast,
    build_load_forecast,
    fetch_hourly_means,
    find_load_entity,
    subtract_loads,
)
from .optimizer import BatterySpec, FlexLoad, Plan, Settings, SlotInput, optimize
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
    reason: str | None = None  # why nothing is planned


def flex_loads(
    config: SunflowConfig,
    profiles: dict[str, dict[str, Any]],
    snapshot: Snapshot,
    slot_starts: list[datetime],
    tz: tzinfo,
) -> tuple[list[FlexLoad], list[FlexInfo], list[str]]:
    """Derive the energy need of every flexible consumer from live values and its settings."""
    loads: list[FlexLoad] = []
    infos: list[FlexInfo] = []
    warnings: list[str] = []
    if not slot_starts:
        return loads, infos, warnings
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
        max_power = params.get("max_power") or 0.0
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
        elif kind == "load":
            daily = params.get("daily_energy_wh")
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
            info.deadline = min(ready_by, horizon_end) if ready_by else horizon_end
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

        later_needs = daily_needs(slot_starts, info.deadline, info.daily_wh) if info.daily_wh else []
        if info.need_wh <= 0 and not later_needs:
            info.reason = "Ziel bereits erreicht"
            continue
        if paused:
            info.reason = "pausiert"
            continue
        rank = flex_ids.index(device.id) if device.id in flex_ids else len(flex_ids)
        end = slot_starts[later_needs[-1][1] - 1] + SLOT if later_needs else info.deadline
        loads.append(
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
    return loads, infos, warnings


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


def battery_spec(
    profile: dict[str, Any], device: DeviceConfig, snapshot: Snapshot
) -> tuple[BatterySpec | None, str | None]:
    """Build the battery spec from parameters and the live SoC; returns (spec, problem)."""
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
    if roles.get("battery_soc") is None:
        return None, f"{device.name}: Ladestand nicht lesbar."
    return (
        BatterySpec(
            capacity_wh=capacity,
            soc_wh=capacity * roles["battery_soc"] / 100,
            min_soc_wh=capacity * (params.get("min_soc") or 0) / 100,
            max_charge_w=params.get("max_charge") or 0,
            max_discharge_w=params.get("max_discharge") or 0,
            # The parameter is the round trip; split evenly between charging and discharging
            efficiency_charge=math.sqrt((params.get("efficiency_pct") or 90) / 100),
            efficiency_discharge=math.sqrt((params.get("efficiency_pct") or 90) / 100),
        ),
        None,
    )


# Export penalty for "pv_absorption": large enough to keep room for PV before sunny days
PV_ABSORPTION_PENALTY_CT = 5.0


def optimizer_settings(config: SunflowConfig, battery: BatterySpec) -> Settings:
    """Translate the user's planning settings and strategy into optimizer settings."""
    options = config.optimizer
    strategy = config.effective_strategy(datetime.now(UTC))
    return Settings(
        wear_ct_per_kwh=options.wear_ct_per_kwh,
        allow_grid_charge=options.allow_grid_charge,
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
                f"PV-Prognose „{plane.display_name}“ reicht nur bis {last.astimezone(tz):%a %H:%M} – "
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
    loads, flex_infos, flex_warnings = flex_loads(config, profiles, snapshot, [i.start for i in inputs], tz)
    warnings += flex_warnings
    # The solver is CPU bound; keep the event loop (HA connection, API) responsive
    plan = await asyncio.to_thread(optimize, inputs, battery, settings, loads)
    return PlanResult(
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
