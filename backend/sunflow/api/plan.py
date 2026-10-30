"""The plan and the measured history around it."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from sunflow.analysis.history import fetch_history, history_series
from sunflow.config.models import SunflowConfig
from sunflow.control.controller import PlanController
from sunflow.ha import HAError
from sunflow.shadow.runner import ShadowRunner, current_plan
from sunflow.shadow.store import actual_action

from .common import _library, _local_tz, _slot_start, _snapshot

router = APIRouter()


PV_SOURCE_NAMES = {"solcast": "Solcast", "forecast_solar": "Forecast.Solar"}


def plan_response(result: Any, config: SunflowConfig) -> dict[str, Any]:
    """Serialize a plan result for the UI."""
    plan = result.plan
    return {
        "status": plan.status,
        "warnings": result.warnings,
        "price_source": result.price_source,
        "pv_planes": result.pv_planes,
        "load_quality": result.load.quality if result.load else None,
        "load_history_hours": result.load.history_hours if result.load else 0,
        "load_scaled_to_kwh": result.load.scaled_to_kwh if result.load else None,
        "load_history_daily_kwh": result.load.history_daily_kwh if result.load else None,
        "load_excluded_hours": result.load.excluded_hours if result.load else 0,
        "load_separated_hours": result.load.separated_hours if result.load else 0,
        "settings": config.optimizer.model_dump(),
        "strategy": config.effective_strategy(datetime.now(UTC)),
        "cost": plan.cost,
        "baseline_cost": plan.baseline_cost,
        "alternative_cost": plan.alternative_cost,
        "pv_correction": result.pv_correction.as_dict() if result.pv_correction and result.pv_correction.slots else None,
        "load_model": result.load_model.as_dict() if result.load_model else None,
        "usages": {device_id: asdict(usage) for device_id, usage in result.usages.items()},
        "baseline_alternative_cost": plan.baseline_alternative_cost,
        "baseline_slot_costs": plan.baseline_slot_costs,
        "terminal_value": plan.terminal_value,
        "baseline_terminal_value": plan.baseline_terminal_value,
        "savings": plan.savings if plan.slots else None,
        "battery": {
            "capacity_wh": result.battery.capacity_wh,
            "min_soc_wh": result.battery.min_soc_wh,
            "reserve_wh": result.reserve_wh,
            "limit_wh": result.care.limit_wh if result.care else None,
            "full_charge_at": result.care.full_charge_at.isoformat() if result.care and result.care.full_charge_at else None,
            "last_full": result.care.last_full.isoformat() if result.care and result.care.last_full else None,
        }
        if result.battery
        else None,
        "flex": [
            asdict(info) | next(
                ({"planned_wh": r.planned_wh, "unmet_wh": r.unmet_wh} for r in plan.flex if r.name == info.name),
                {"planned_wh": 0.0, "unmet_wh": 0.0},
            )
            for info in result.flex
        ],
        "slots": [asdict(slot) for slot in plan.slots],
        # The forecast as its source delivers it, before learning and nowcast
        "raw_pv": result.raw_pv[: len(plan.slots)],
        "pv_sources": sorted({PV_SOURCE_NAMES.get(p.source, p.source) for p in config.forecast_planes if p.device_id}),
    }


@router.get("/plan")
async def get_plan(request: Request) -> dict[str, Any]:
    """Optimize the battery schedule for the next hours (read only, nothing is controlled)."""
    try:
        runner = getattr(request.app.state, "runner", None)
        result = await current_plan(
            request.app.state.ha,
            request.app.state.store,
            _library(request),
            request.app.state.snapshots,
            await runner.pv_correction() if runner else None,
            runner.load_model if runner else None,
            runner.plane_corrections if runner else None,
        )
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    # "Neu berechnen" must not be replaced by an older background plan on the next poll
    if (runner := getattr(request.app.state, "runner", None)) is not None:
        runner.latest = result
    return plan_response(result, request.app.state.store.load())


@router.get("/plan/latest")
async def get_latest_plan(request: Request) -> dict[str, Any]:
    """Return the plan of the background runner (computed at most 15 minutes ago)."""
    runner: ShadowRunner = request.app.state.runner
    # Without a plan yet, all open tabs wait for one shared computation
    if (latest := await runner.ensure_plan()) is None:
        raise HTTPException(status_code=503, detail=runner.status.last_error or "Plan wird noch berechnet")
    sample = runner.last_sample
    response = plan_response(latest, request.app.state.store.load())
    if runner.status.last_error and runner.status.last_plan:
        # An older plan is still shown; say that newer attempts failed and why
        since = runner.status.last_plan.astimezone(await _local_tz(request)).strftime("%H:%M")
        response["warnings"] = [
            f"Neuberechnung fehlgeschlagen, angezeigt wird der Plan von {since} ({runner.status.last_error}).",
            *response["warnings"],
        ]
    controller: PlanController | None = getattr(request.app.state, "controller", None)
    return response | {
        "controlling": bool(controller and any(s.active for s in controller.statuses.values())),
        "computed": runner.status.last_plan.isoformat() if runner.status.last_plan else None,
        # What the battery does right now (Sunflow does not control yet; another system may)
        "actual": asdict(sample)
        | {"time": runner.last_sample_at.isoformat() if runner.last_sample_at else None}
        | {"action": actual_action(sample.grid_w, sample.battery_w)}
        if sample
        else None,
    }


def _flex_windows(slots: list[Any], name: str) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for slot in slots:
        power = slot.flex_w.get(name, 0.0)
        if power < 50:
            if windows and not windows[-1].get("closed"):
                windows[-1]["closed"] = True
            continue
        if windows and not windows[-1].get("closed"):
            windows[-1]["end"] = slot.end.isoformat()
            windows[-1]["energy_wh"] += power / 4
        else:
            windows.append({"start": slot.start.isoformat(), "end": slot.end.isoformat(), "energy_wh": power / 4})
    return [{k: v for k, v in w.items() if k != "closed"} for w in windows]


@router.get("/history")
async def get_history(
    request: Request,
    hours: Annotated[int, Query(ge=1, le=31 * 24)] = 24,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Return measured SoC, PV, load and grid of the hours before end (default: now) from HA statistics."""
    hours = max(1, min(hours, 7 * 24))
    snapshot = await _snapshot(request)
    config = request.app.state.store.load()
    series = history_series(config, _library(request).profiles, snapshot)
    now = _slot_start()
    # An earlier window when the charts are scrolled back; never into the future
    end = now if end is None else min(end.astimezone(UTC), now)
    if end < now - timedelta(days=31):
        raise HTTPException(status_code=422, detail="Nicht weiter als 31 Tage zurück")
    # 15 minute resolution up to two days, hourly above to keep charts readable
    bucket = timedelta(minutes=15) if hours <= 48 else timedelta(hours=1)
    start = end - timedelta(hours=hours)
    if bucket == timedelta(hours=1):
        start = start.replace(minute=0)
    try:
        points = await fetch_history(request.app.state.ha, series, start, end, bucket)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return {"start": start.isoformat(), "end": end.isoformat(), "points": points}
