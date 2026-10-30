"""State for the Sunflow integration: plan, control, live values and consumers in one answer."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request

from sunflow.config.models import SunflowConfig
from sunflow.control.controller import PlanController

from .common import _replan

router = APIRouter()

# Consumer power from which a slot counts as "runs"
RUNS_FROM_W = 100.0
# A PV surplus below this is not worth switching a load for
SURPLUS_FROM_W = 300.0


def _start(slot: Any) -> datetime:
    return datetime.fromisoformat(str(slot.start))


def integration_state(
    config: SunflowConfig, result: Any, plan_at: datetime | None, sample: Any, controller: PlanController | None,
    now: datetime, last_error: str | None = None,
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
    return {
        "time": now.isoformat(),
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
    )
