"""Battery modules: current values per module and their course over a day."""

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from sunflow.analysis.unit_history import day_window, module_day
from sunflow.devices.battery_modules import battery_modules
from sunflow.ha import HAError
from sunflow.i18n import t

from .common import _library, _local_tz, _snapshot

router = APIRouter()


@router.get("/battery-modules")
async def get_battery_modules(request: Request) -> list[dict[str, Any]]:
    """Each battery with its modules, their values and problems."""
    batteries = battery_modules(request.app.state.store.load(), _library(request), await _snapshot(request))
    return [
        b | {"modules": [{k: v for k, v in m.items() if k != "entities"} for m in b["modules"]]} for b in batteries
    ]


@router.get("/battery-modules/history")
async def get_battery_module_history(
    request: Request, device: Annotated[str, Query(min_length=1)], day: date | None = None
) -> dict[str, Any]:
    """SoC, current, temperature and cell voltage spread of one battery's modules over a local day."""
    tz = await _local_tz(request)
    now = datetime.now(UTC)
    day = day or now.astimezone(tz).date()
    if (window := day_window(day, tz, now)) is None:
        raise HTTPException(status_code=422, detail=t("Dieser Tag liegt in der Zukunft"))
    start, end, step = window
    batteries = battery_modules(request.app.state.store.load(), _library(request), await _snapshot(request))
    battery = next((b for b in batteries if b["device_id"] == device), None)
    if battery is None:
        raise HTTPException(status_code=404, detail=t("Speicher ohne Module"))
    try:
        days = await module_day(request.app.state.ha, battery["modules"], start, end, step)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return {
        "day": day.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "step_minutes": int(step.total_seconds() // 60),
        "modules": days,
    }
