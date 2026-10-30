"""State for the Sunflow integration: plan, control, live values and consumers in one answer."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from sunflow.config.models import SunflowConfig
from sunflow.control.controller import PlanController
from sunflow.planning.one_time import OneTimeLoad, OneTimeLoads

from .common import _replan

router = APIRouter()

# Consumer power from which a slot counts as "runs"
RUNS_FROM_W = 100.0
# A PV surplus below this is not worth switching a load for
SURPLUS_FROM_W = 300.0


def _start(slot: Any) -> datetime:
    return datetime.fromisoformat(str(slot.start))


def consumers_of(result: Any) -> list[Any]:
    """Planned consumers of a plan result."""
    return list(getattr(result, "flex", None) or [])


def _windows(slots: list[Any], runs: Any) -> list[tuple[datetime, datetime, list[Any]]]:
    """Consecutive slots for which runs(slot) is true: (start, end, slots)."""
    windows: list[tuple[datetime, datetime, list[Any]]] = []
    for slot in slots:
        if not runs(slot):
            continue
        start = _start(slot)
        if windows and windows[-1][1] == start:
            windows[-1] = (windows[-1][0], start + timedelta(minutes=15), [*windows[-1][2], slot])
        else:
            windows.append((start, start + timedelta(minutes=15), [slot]))
    return windows


def calendar(slots: list[Any], consumers: list[Any], loads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Planned windows as calendar events: consumers, grid charging, PV limiting, one-time loads."""
    events = []
    verbs = {"vehicle": "lädt", "heater": "heizt"}
    for info in consumers:
        for start, end, run in _windows(slots, lambda s, n=info.name: (s.flex_w or {}).get(n, 0.0) >= RUNS_FROM_W):
            energy = sum(s.flex_w[info.name] for s in run) * 0.25 / 1000
            events.append(
                {
                    "summary": f"{info.name} {verbs.get(info.kind, 'läuft')}",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "description": f"{energy:.1f} kWh geplant".replace(".", ","),
                }
            )
    for start, end, run in _windows(slots, lambda s: s.action == "force_charge" and s.import_w > RUNS_FROM_W):
        energy = sum(s.import_w for s in run) * 0.25 / 1000
        events.append(
            {
                "summary": "Speicher lädt aus dem Netz",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "description": f"{energy:.1f} kWh aus dem Netz".replace(".", ","),
            }
        )
    for start, end, _ in _windows(slots, lambda s: (getattr(s, "curtail_w", 0.0) or 0.0) > RUNS_FROM_W):
        events.append({"summary": "PV wird begrenzt", "start": start.isoformat(), "end": end.isoformat(), "description": ""})
    for load in loads:
        if load["start"] and load["end"]:
            events.append({"summary": load["name"], "start": load["start"], "end": load["end"], "description": "Einmalige Last"})
    return sorted(events, key=lambda e: e["start"])


def integration_state(
    config: SunflowConfig, result: Any, plan_at: datetime | None, sample: Any, controller: PlanController | None,
    now: datetime, last_error: str | None = None, loads: OneTimeLoads | None = None,
) -> dict[str, Any]:
    """Everything the integration shows as entities, from the latest plan and measurement."""
    slots = list(result.plan.slots) if result is not None and result.plan is not None else []
    current = next((s for s in slots if _start(s) <= now < _start(s) + timedelta(minutes=15)), None)
    ahead = [s for s in slots if _start(s) + timedelta(minutes=15) > now]
    capacity = result.battery.capacity_wh if result is not None and getattr(result, "battery", None) else None
    in_an_hour = next((s for s in ahead if _start(s) >= now + timedelta(hours=1)), None)
    cheapest = min(ahead, key=lambda s: (s.price, _start(s)), default=None)
    surplus = None
    if sample is not None and sample.pv_w is not None and sample.load_w is not None:
        surplus = max(sample.pv_w - sample.load_w, 0.0)
    devices = []
    for device in config.devices:
        if controller is None or not controller.controllable(device):
            continue
        status = controller.statuses.get(device.id)
        devices.append(
            {
                "id": device.id,
                "name": device.name,
                # Switched on for this device; it is controlled while the main switch is on as well
                "enabled": config.control.devices.get(device.id, False),
                "controlled": config.control.enabled and config.control.devices.get(device.id, False),
                **({k: v for k, v in asdict(status).items() if k in ("action", "power_w", "reason", "phases", "limit_w", "error")} if status else {}),
            }
        )
    consumers = []
    for info in getattr(result, "flex", None) or []:
        runs = [s for s in ahead if (getattr(s, "flex_w", None) or {}).get(info.name, 0.0) >= RUNS_FROM_W]
        consumers.append(
            {
                "id": info.device_id,
                "name": info.name,
                "kind": info.kind,
                "level": info.current,
                "target": info.target,
                "unit": info.unit,
                "vehicle": info.vehicle,
                "need_wh": info.need_wh,
                "planned_wh": sum(s.flex_w[info.name] for s in runs) * 0.25,
                "next_start": _start(runs[0]).isoformat() if runs else None,
                "running": current is not None and (current.flex_w or {}).get(info.name, 0.0) >= RUNS_FROM_W,
                "reason": info.reason,
            }
        )
    if loads is not None:
        loads.update(slots, now)
    load_list = loads.as_list() if loads is not None else []
    return {
        "calendar": calendar(ahead, consumers_of(result), load_list),
        # Only while Sunflow controls: what hands the devices back if the App stops
        "fallback": controller.fallback if controller is not None and config.control.enabled else [],
        "time": now.isoformat(),
        "loads": load_list,
        "control": {"enabled": config.control.enabled, "paused": controller.paused if controller else None},
        "strategy": {"default": config.optimizer.strategy, "effective": config.effective_strategy(now)},
        "plan": {
            "updated": plan_at.isoformat() if plan_at else None,
            "error": last_error,
            "action": current.action if current else None,
            "action_power_w": current.action_power_w if current else None,
            "price_ct": round(current.price * 100, 2) if current else None,
            "feed_in_ct": round(current.feed_in * 100, 2) if current else None,
            "cheapest_start": _start(cheapest).isoformat() if cheapest else None,
            "cheapest_price_ct": round(cheapest.price * 100, 2) if cheapest else None,
            "soc_in_an_hour_pct": round(in_an_hour.soc_wh / capacity * 100, 1) if in_an_hour and capacity else None,
            "savings_eur": round(result.plan.savings, 2) if slots else None,
            "grid_charging": current is not None and current.action == "force_charge" and current.import_w > RUNS_FROM_W,
            "curtailing": current is not None and (getattr(current, "curtail_w", 0.0) or 0.0) > RUNS_FROM_W,
        },
        "live": {
            "pv_w": sample.pv_w if sample else None,
            "load_w": sample.load_w if sample else None,
            "grid_w": sample.grid_w if sample else None,
            "battery_w": sample.battery_w if sample else None,
            "soc_pct": round(sample.soc_wh / capacity * 100, 1) if sample and sample.soc_wh is not None and capacity else None,
            "surplus_w": surplus,
            "surplus": surplus is not None and surplus >= SURPLUS_FROM_W,
        },
        "devices": devices,
        "consumers": consumers,
    }


class NewLoad(BaseModel):
    """A one-time run to place in the plan."""

    model_config = ConfigDict(allow_inf_nan=False)

    name: str = Field(min_length=1)
    energy_wh: float = Field(gt=0, le=100_000)
    power_w: float = Field(gt=0, le=50_000)
    earliest: datetime | None = None
    finish_by: datetime | None = None


@router.post("/integration/loads")
async def add_load(request: Request, body: NewLoad) -> dict[str, Any]:
    """Place a one-time run (e.g. the washing machine) at its cheapest start; Home Assistant switches it."""
    loads: OneTimeLoads = request.app.state.loads
    load = OneTimeLoad(**body.model_dump())
    loads.loads.append(load)
    runner = request.app.state.runner
    loads.update(list(runner.latest.plan.slots) if runner.latest is not None else [], datetime.now(UTC))
    loads.save()
    return next(item for item in loads.as_list() if item["id"] == load.id)


@router.delete("/integration/loads/{load_id}")
async def remove_load(request: Request, load_id: str) -> None:
    """Forget a one-time run."""
    loads: OneTimeLoads = request.app.state.loads
    if not any(load.id == load_id for load in loads.loads):
        raise HTTPException(status_code=404, detail="Unknown load")
    loads.loads = [load for load in loads.loads if load.id != load_id]
    loads.save()


@router.post("/integration/replan")
async def replan(request: Request) -> dict[str, Any]:
    """Plan again now (e.g. from a button in Home Assistant)."""
    _replan(request)
    return {"requested": True}


@router.get("/integration/state")
async def get_integration_state(request: Request) -> dict[str, Any]:
    """State for the Sunflow integration's entities."""
    runner = request.app.state.runner
    return integration_state(
        request.app.state.store.load(),
        runner.latest,
        runner.status.last_plan,
        runner.last_sample,
        getattr(request.app.state, "controller", None),
        datetime.now(UTC),
        runner.status.last_error,
        getattr(request.app.state, "loads", None),
    )
