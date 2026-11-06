"""Energy balance, efficiencies, costs and recommendations."""

import asyncio
from datetime import UTC, datetime, timedelta
import logging
import re
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from sunflow.analysis.battery_sim import STRATEGIES, history_hours, simulate
from sunflow.analysis.costs import cost_history
from sunflow.analysis.energy import (
    battery_counters,
    efficiency_history,
    energy_balance,
    energy_sources,
)
from sunflow.analysis.inventory import data_inventory
from sunflow.analysis.recommendations import build as build_recommendations
from sunflow.config.models import BatteryScenario
from sunflow.config.store import ConfigStore
from sunflow.devices.profiles import controls_battery, planned_battery
from sunflow.devices.site import device_parameters
from sunflow.ha import HAError
from sunflow.planning.appliances import learn_usage as learn_appliance_usage
from sunflow.planning.plan import feed_in_price
from sunflow.shadow.runner import ShadowRunner

from .common import _library, _local_tz, _snapshot, keep_fresh, kept_fresh
from .devices import _efficiency_estimate
from .sources import get_weather_entities

router = APIRouter()


_LOGGER = logging.getLogger(__name__)

ENERGY_PERIODS = {"today": 0, "7d": 7, "30d": 30}
# The energy balance comes from Home Assistant's 5 minute statistics: newer than their last
# compilation it cannot be. Today's is recomputed shortly after each one (keep_energy_today)
ENERGY_FRESH = timedelta(minutes=6)
# Home Assistant compiles the 5 minute statistics a few seconds after each 5 minutes
STATISTICS_COMPILED = timedelta(seconds=30)


async def _energy(request: Request, period: str) -> dict[str, Any]:
    config = request.app.state.store.load()
    snapshot = await _snapshot(request)
    sources = energy_sources(config, _library(request).profiles, snapshot)
    tz = await _local_tz(request)
    end = datetime.now(tz)
    midnight = end.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=ENERGY_PERIODS[period])
    return await energy_balance(request.app.state.ha, sources, start, end)


async def keep_energy_today(app: Any) -> None:
    """Recompute today's energy balance right after each compilation of the 5 minute statistics."""
    # The helpers only need the app of a request
    request = SimpleNamespace(app=app)
    while True:
        now = datetime.now(UTC)
        since = timedelta(minutes=now.minute % 5, seconds=now.second, microseconds=now.microsecond)
        await asyncio.sleep(((STATISTICS_COMPILED - since) % timedelta(minutes=5)).total_seconds())
        if not app.state.ha.connected:
            continue
        try:
            keep_fresh(app.state, ("energy", "today"), await _energy(request, "today"))
        except Exception as err:  # noqa: BLE001 - the loop must never die; a page computes it then
            _LOGGER.debug("Energy balance in the background failed: %s", err)


@router.get("/energy")
async def get_energy(request: Request, period: str = "today") -> dict[str, Any]:
    """Energy balance from the energy counters: today (since local midnight) or the last days."""
    if period not in ENERGY_PERIODS:
        raise HTTPException(status_code=422, detail=f"period must be one of {list(ENERGY_PERIODS)}")
    try:
        return await kept_fresh(request, ("energy", period), ENERGY_FRESH, lambda: _energy(request, period))
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


@router.get("/efficiency/history")
async def get_efficiency_history(
    request: Request, days: Annotated[int, Query(ge=7, le=365)] = 60
) -> list[dict[str, Any]]:
    """Daily efficiency of batteries and inverters with energy counters, for the Analyse tab."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    return await efficiency_history(
        request.app.state.ha,
        battery_counters(config, profiles, snapshot),
        energy_sources(config, profiles, snapshot).inverters,
        days,
        await _local_tz(request),
    )


INVENTORY_TTL = timedelta(hours=1)


ENTITY_ID = re.compile(r"[a-z_]+\.[a-z0-9_]+")
INVENTORY_MAX_EXTRA = 50


@router.get("/data-inventory")
async def get_data_inventory(request: Request, refresh: bool = False, entities: str = "") -> dict[str, Any]:
    """How far back Home Assistant keeps the measurements, the weather and the PV forecasts.

    entities: further entity ids to check, comma separated (not cached).
    """
    extra = [e.strip() for e in entities.split(",") if e.strip()]
    if len(extra) > INVENTORY_MAX_EXTRA or not all(ENTITY_ID.fullmatch(e) for e in extra):
        raise HTTPException(status_code=422, detail=f"entities: up to {INVENTORY_MAX_EXTRA} entity ids, comma separated")
    cache = getattr(request.app.state, "inventory_cache", None)
    now = datetime.now(UTC)
    if extra or refresh or cache is None or now - cache[0] > INVENTORY_TTL:
        config = request.app.state.store.load()
        try:
            inventory = await data_inventory(
                request.app.state.ha,
                config,
                _library(request).profiles,
                await _snapshot(request),
                await _local_tz(request),
                extra,
            )
        except HAError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        if extra:
            return inventory
        cache = (now, inventory)
        request.app.state.inventory_cache = cache
    return cache[1]


APPLIANCES_TTL = timedelta(hours=6)


@router.get("/appliances/usage")
async def get_appliance_usage(request: Request) -> list[dict[str, Any]]:
    """Consumers that are only measured: what they use and when, learned from the statistics."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    measured = [d for d in config.devices if "appliance" in profiles[d.profile]["capabilities"]]
    cache = getattr(request.app.state, "appliance_cache", None)
    now = datetime.now(UTC)
    key = [(d.id, d.role_overrides["power"].entity_id) for d in measured if "power" in d.role_overrides]
    if cache is not None and cache[1] == key and now - cache[0] <= APPLIANCES_TTL:
        return cache[2]
    tz = await _local_tz(request)
    result = []
    for device_id, entity_id in key:
        try:
            usage = await learn_appliance_usage(request.app.state.ha, entity_id, tz)
        except HAError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err
        device = config.device(device_id)
        result.append(
            {"device_id": device_id, "name": device.name if device else device_id, "entity_id": entity_id}
            | ({"usage": usage.as_dict()} if usage else {"usage": None})
        )
    request.app.state.appliance_cache = (now, key, result)
    return result


RECOMMENDATION_ESTIMATES_TTL = timedelta(hours=1)


@router.get("/recommendations")
async def get_recommendations(request: Request) -> list[dict[str, Any]]:
    """Settings the measured data or the plan suggest to change."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    runner: ShadowRunner = request.app.state.runner
    # The counter estimates query 90 days of statistics; an hour old is recent enough
    cache = getattr(request.app.state, "efficiency_cache", None)
    now = datetime.now(UTC)
    if cache is None or now - cache[0] > RECOMMENDATION_ESTIMATES_TTL:
        estimates = {}
        for device in config.devices:
            if "efficiency_pct" in device.parameters or controls_battery(profiles[device.profile]):
                try:
                    estimates[device.id] = await _efficiency_estimate(request, device)
                except HTTPException:
                    estimates[device.id] = None
        cache = (now, estimates)
        request.app.state.efficiency_cache = cache
    rows = await asyncio.to_thread(runner.shadow.rows, now - timedelta(days=14))
    days = len({row["start"][:10] for row in rows if row.get("actual_load_w") is not None})
    return build_recommendations(
        config,
        runner.latest,
        cache[1],
        rows,
        days,
        await runner.pv_correction(),
        await get_weather_entities(request),
        runner.sun_planes,
    )


class Dismiss(BaseModel):
    """A recommendation to hide until its numbers change."""

    id: str
    fingerprint: str = ""


@router.post("/recommendations/dismiss")
async def dismiss_recommendation(request: Request, body: Dismiss) -> None:
    """Hide a recommendation."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    key = f"{body.id}#{body.fingerprint}"
    if key not in config.dismissed_recommendations:
        # Old entries of the same recommendation are no longer needed
        config.dismissed_recommendations = [
            k for k in config.dismissed_recommendations if not k.startswith(f"{body.id}#")
        ] + [key]
        store.save(config)


@router.get("/costs/history")
async def get_cost_history(request: Request, days: Annotated[int, Query(ge=1, le=90)] = 30) -> list[dict[str, Any]]:
    """Daily electricity costs, feed-in revenue and the cost without PV and battery."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    snapshot = await _snapshot(request)
    battery_device = planned_battery(config.devices, profiles)
    fixed = config.tariff.fixed_price_ct
    return await cost_history(
        request.app.state.ha,
        energy_sources(config, profiles, snapshot),
        config.tariff.import_entity_id,
        fixed / 100 if fixed is not None else None,
        feed_in_price(config, battery_device),
        days,
        await _local_tz(request),
    )


# The simulation reads hourly statistics and optimizes day by day: kept for a while
SCENARIOS_FRESH = timedelta(hours=1)


@router.get("/config/battery-scenarios")
async def get_battery_scenarios(request: Request) -> list[dict[str, Any]]:
    """Batteries one might buy (or the existing one run differently), for the simulation."""
    return [s.model_dump() for s in request.app.state.store.load().battery_scenarios]


@router.put("/config/battery-scenarios")
async def set_battery_scenarios(request: Request, body: list[BatteryScenario]) -> list[dict[str, Any]]:
    """Store the scenarios; ids must be unique."""
    if len({s.id for s in body}) != len(body):
        raise HTTPException(status_code=422, detail="Scenario ids must be unique")
    store: ConfigStore = request.app.state.store
    config = store.load()
    config.battery_scenarios = body
    store.save(config)
    return [s.model_dump() for s in body]


@router.get("/analysis/battery-scenarios/existing")
async def get_existing_battery_scenario(request: Request) -> dict[str, Any] | None:
    """The existing battery as a scenario (instead of it), to try other settings on its history."""
    config = request.app.state.store.load()
    profiles = _library(request).profiles
    device = planned_battery(config.devices, profiles)
    if device is None:
        return None
    snapshot = await _snapshot(request)
    params = {
        p["name"]: p["effective"]
        for p in device_parameters(profiles[device.profile], device, snapshot, config.battery_modules.get(device.id))
    }
    capacity = params.get("capacity_wh")
    if not capacity:
        return None
    cost = config.battery_cost(device.id)
    return BatteryScenario(
        id=f"existing-{device.id[:8]}",
        name=device.name,
        capacity_kwh=capacity / 1000,
        usable_pct=100 - (params.get("min_soc") or 0),
        max_charge_kw=(params.get("max_charge") or 5000) / 1000,
        max_discharge_kw=(params.get("max_discharge") or 5000) / 1000,
        efficiency_pct=params.get("efficiency_pct") or 93,
        inverter_efficiency_pct=params.get("inverter_efficiency_pct") or 96.5,
        mode="replace",
        wear_ct=cost.wear_ct_per_kwh if cost.include else 0.0,
        grid_charge=config.optimizer.allow_grid_charge,
    ).model_dump()


@router.get("/analysis/battery-scenarios")
async def get_battery_simulation(
    request: Request,
    days: Annotated[int, Query(ge=7, le=365)] = 30,
    strategy: Annotated[list[Literal["balanced", "pv_absorption", "autarky", "storage"]] | None, Query()] = None,
) -> dict[str, Any]:
    """What each scenario would have changed over the last days, with the measured operation.

    Each scenario runs without control and planned with each strategy asked for.
    """
    config = request.app.state.store.load()
    scenarios = config.battery_scenarios
    strategies = tuple(s for s in STRATEGIES if s in (strategy or ["balanced"]))

    async def compute() -> dict[str, Any]:
        profiles = _library(request).profiles
        snapshot = await _snapshot(request)
        fixed = config.tariff.fixed_price_ct
        hours = await history_hours(
            request.app.state.ha,
            energy_sources(config, profiles, snapshot),
            config.tariff.import_entity_id,
            fixed / 100 if fixed is not None else None,
            feed_in_price(config, planned_battery(config.devices, profiles)),
            days,
            await _local_tz(request),
        )
        # Purchase prices entered for the wear of the existing batteries
        costs = [config.battery_cost(d.id) for d in config.devices if controls_battery(profiles[d.profile])]
        prices = [c.calculation.price_eur for c in costs if c.calculation]
        # Optimizing day by day takes seconds: off the event loop
        return await asyncio.to_thread(simulate, hours, scenarios, sum(prices) or None, strategies)

    key = ("battery-scenarios", days, strategies, tuple(s.model_dump_json() for s in scenarios))
    try:
        return await kept_fresh(request, key, SCENARIOS_FRESH, compute)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
