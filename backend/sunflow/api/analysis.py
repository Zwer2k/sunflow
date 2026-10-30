"""Energy balance, efficiencies, costs and recommendations."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from sunflow.analysis.costs import cost_history
from sunflow.analysis.energy import (
    battery_counters,
    efficiency_history,
    energy_balance,
    energy_sources,
)
from sunflow.analysis.recommendations import build as build_recommendations
from sunflow.config.store import ConfigStore
from sunflow.ha import HAError
from sunflow.planning.plan import feed_in_price
from sunflow.shadow.runner import ShadowRunner

from .common import _library, _local_tz, _snapshot
from .devices import _efficiency_estimate
from .sources import get_weather_entities

router = APIRouter()


ENERGY_PERIODS = {"today": 0, "7d": 7, "30d": 30}


@router.get("/energy")
async def get_energy(request: Request, period: str = "today") -> dict[str, Any]:
    """Energy balance from the energy counters: today (since local midnight) or the last days."""
    if period not in ENERGY_PERIODS:
        raise HTTPException(status_code=422, detail=f"period must be one of {list(ENERGY_PERIODS)}")
    config = request.app.state.store.load()
    snapshot = await _snapshot(request)
    sources = energy_sources(config, _library(request).profiles, snapshot)
    tz = await _local_tz(request)
    end = datetime.now(tz)
    midnight = end.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=ENERGY_PERIODS[period])
    try:
        return await energy_balance(request.app.state.ha, sources, start, end)
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
            if "efficiency_pct" in device.parameters or "battery_window" in profiles[device.profile]["capabilities"]:
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
    battery_device = next(
        (d for d in config.devices if "battery_window" in profiles[d.profile]["capabilities"]), None
    )
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
