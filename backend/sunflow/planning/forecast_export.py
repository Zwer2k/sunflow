"""The learned PV forecast for Home Assistant: per plane, per plant and in total, by hour."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any

from sunflow.config.models import ForecastPlane, SunflowConfig

from .pv_learning import PvCorrection
from .sources import ForecastPoint

TOTAL = "total"


@dataclass(kw_only=True)
class ForecastTarget:
    """One forecast Home Assistant can show: Wh per hour (start in UTC) and its key figures."""

    id: str
    kind: str
    name: str
    plant: str | None = None
    wh_hours: dict[str, float] = field(default_factory=dict)

    def summary(self, now: datetime, tz: tzinfo) -> dict[str, float]:
        """Today, rest of today, tomorrow (Wh) and the power expected now (W)."""
        today = now.astimezone(tz).date()
        hour = now.replace(minute=0, second=0, microsecond=0)
        result = {"today_wh": 0.0, "remaining_today_wh": 0.0, "tomorrow_wh": 0.0, "power_now_w": 0.0}
        for start, wh in self.wh_hours.items():
            moment = datetime.fromisoformat(start)
            day = moment.astimezone(tz).date()
            if day == today:
                result["today_wh"] += wh
                if moment >= hour:
                    # The running hour counts with its remaining share
                    share = 1 - (now - moment).total_seconds() / 3600 if moment == hour else 1.0
                    result["remaining_today_wh"] += wh * share
            elif day == today + timedelta(days=1):
                result["tomorrow_wh"] += wh
            if moment == hour:
                result["power_now_w"] = wh
        return {k: round(v, 1) for k, v in result.items()}


def corrected(points: list[ForecastPoint], correction: PvCorrection | None, tz: tzinfo) -> dict[str, float]:
    """Wh per hour of a raw forecast, times the learned factor of its local hour."""
    hours: dict[str, float] = {}
    for point in points:
        local = point.start.astimezone(tz)
        factor = correction.factor(local.hour, None) if correction is not None and correction.slots else 1.0
        start = point.start.replace(minute=0, second=0, microsecond=0)
        key = start.astimezone(point.start.tzinfo).isoformat()
        hours[key] = hours.get(key, 0.0) + point.power * factor * (point.end - point.start).total_seconds() / 3600
    return hours


def _add(target: dict[str, float], hours: dict[str, float]) -> None:
    for key, wh in hours.items():
        target[key] = target.get(key, 0.0) + wh


async def export_forecasts(
    config: SunflowConfig,
    fetch: Callable[[ForecastPlane], Awaitable[list[ForecastPoint]]],
    site: PvCorrection | None,
    planes: dict[str, PvCorrection],
    tz: tzinfo,
    recorded: dict[str, list[ForecastPoint]] | None = None,
) -> list[ForecastTarget]:
    """Corrected forecasts of every plane assigned to a plant, of each plant and of all.

    A plane uses its own learned correction, else the site's (as the planner does,
    without the cloud classes: the weather forecast does not reach as far). Hours the
    source no longer delivers (today's past, earlier days) come from the forecast Sunflow
    recorded then.
    A last day the source covers only partly is left out, it would show as a day
    without sun.
    """
    names = {d.id: d.name for d in config.devices}
    total = ForecastTarget(id=TOTAL, kind="total", name="Gesamt")
    plants: dict[str, ForecastTarget] = {}
    targets: list[ForecastTarget] = []
    for plane in config.forecast_planes:
        if plane.device_id is None or plane.device_id not in names:
            continue
        own = planes.get(plane.key)
        points = await fetch(plane)
        first = min((p.start for p in points), default=None)
        earlier = [p for p in (recorded or {}).get(plane.key, []) if first is None or p.end <= first]
        hours = corrected([*earlier, *points], own if own is not None and own.slots else site, tz)
        targets.append(
            ForecastTarget(
                id=f"plane:{plane.key}", kind="plane", name=plane.display_name, plant=names[plane.device_id], wh_hours=hours
            )
        )
        plant = plants.setdefault(
            plane.device_id, ForecastTarget(id=f"plant:{plane.device_id}", kind="plant", name=names[plane.device_id])
        )
        _add(plant.wh_hours, hours)
        _add(total.wh_hours, hours)
    result = [total, *plants.values(), *targets]
    for target in result:
        target.wh_hours = complete_days(target.wh_hours, tz)
    return result


def complete_days(hours: dict[str, float], tz: tzinfo) -> dict[str, float]:
    """Without a last day whose evening the forecast does not reach."""
    if not hours:
        return hours
    starts = {key: datetime.fromisoformat(key).astimezone(tz) for key in hours}
    last_day = max(starts.values()).date()
    if any(moment.date() == last_day and moment.hour == 23 for moment in starts.values()):
        return hours
    return {key: wh for key, wh in hours.items() if starts[key].date() != last_day}


def round_hours(hours: dict[str, float]) -> dict[str, float]:
    """Sorted, whole Wh, as the energy dashboard takes them."""
    return {key: round(wh) for key, wh in sorted(hours.items())}


def as_response(targets: list[ForecastTarget], now: datetime, tz: tzinfo) -> dict[str, Any]:
    """For the API."""
    return {
        "generated": now.isoformat(),
        "targets": [
            {
                "id": t.id,
                "kind": t.kind,
                "name": t.name,
                "plant": t.plant,
                "wh_hours": round_hours(t.wh_hours),
                **t.summary(now, tz),
            }
            for t in targets
        ],
    }
