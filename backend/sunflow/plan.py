"""Collect forecasts, prices and battery state and run the optimizer."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any

from .config_store import DeviceConfig, SunflowConfig
from .discovery import Snapshot
from .ha import HAClient
from .load_forecast import LoadForecast, build_load_forecast, fetch_hourly_means, find_load_entity
from .optimizer import BatterySpec, Plan, Settings, SlotInput, optimize
from .resolver import preview
from .site import device_parameters
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


@dataclass(kw_only=True)
class PlanResult:
    """Plan with the inputs' provenance and warnings."""

    plan: Plan
    battery_device: str | None = None
    load: LoadForecast | None = None
    price_source: str = "none"
    pv_planes: int = 0
    warnings: list[str] = field(default_factory=list)


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
        ),
        None,
    )


# Export penalty for "pv_absorption": large enough to keep room for PV before sunny days
PV_ABSORPTION_PENALTY_CT = 5.0


def optimizer_settings(config: SunflowConfig, battery: BatterySpec) -> Settings:
    """Translate the user's planning settings and strategy into optimizer settings."""
    options = config.optimizer
    return Settings(
        wear_ct_per_kwh=options.wear_ct_per_kwh,
        allow_grid_charge=options.allow_grid_charge,
        export_penalty_ct_per_kwh=PV_ABSORPTION_PENALTY_CT if options.strategy == "pv_absorption" else 0.0,
        reserve_wh=battery.capacity_wh * options.backup_reserve_pct / 100 if options.strategy == "backup" else 0.0,
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
    load = build_load_forecast(history, start, HORIZON_HOURS, tz)
    if load.quality == "default":
        warnings.append("Keine Verbrauchshistorie – Hausverbrauch als konstant angenommen.")
    elif load.history_hours < 7 * 24:
        warnings.append(f"Erst {load.history_hours} h Verbrauchshistorie – die Lastprognose wird mit der Zeit genauer.")

    settings = optimizer_settings(config, battery)
    inputs = build_inputs(start, prices, pv, load, feed_in_price(config, battery_device))
    if len(inputs) < MIN_HORIZON_HOURS * 4:
        warnings.append("Preise reichen nur wenige Stunden in die Zukunft.")
    plan = optimize(inputs, battery, settings)
    return PlanResult(
        plan=plan,
        battery_device=battery_device.id,
        load=load,
        price_source=price_source,
        pv_planes=len(series),
        warnings=warnings,
    )
