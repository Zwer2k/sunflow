"""Each roof plane's measured strings against Solcast's raw forecast: the past days and ahead."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sunflow.config.models import ForecastPlane

HOUR = timedelta(hours=1)


def _hourly(values: list[tuple[datetime, dict[str, float | None]]]) -> list[dict[str, Any]]:
    """Mean per full hour of each field; an hour missing a field in any slot has None for it."""
    hours: dict[datetime, list[dict[str, float | None]]] = defaultdict(list)
    for start, fields in values:
        hours[start.replace(minute=0, second=0, microsecond=0)].append(fields)
    result = []
    for start in sorted(hours):
        slots = hours[start]
        point: dict[str, Any] = {"start": start.isoformat(), "end": (start + HOUR).isoformat()}
        for name in slots[0]:
            known = [s[name] for s in slots if s[name] is not None]
            point[name] = round(sum(known) / len(known), 1) if len(known) == len(slots) else None
        result.append(point)
    return result


def _energy_ratio(points: list[dict[str, Any]], part: str, whole: str) -> float | None:
    """Energy of part against whole over the hours where both are known."""
    pairs = [(p[part], p[whole]) for p in points if p[part] is not None and p[whole] is not None]
    total = sum(w for _, w in pairs)
    return round(sum(p for p, _ in pairs) / total, 3) if total > 0 else None


def plane_comparison(
    planes: Sequence[ForecastPlane],
    rows: list[dict[str, Any]],
    slot_starts: list[datetime],
    plane_pv: dict[str, dict[str, list[float | None]]],
) -> list[dict[str, Any]]:
    """Per plane: hourly history (raw forecast, measured strings) and forecast (raw, p10, p90, learned)."""
    result = []
    for plane in planes:
        history = _hourly(
            [
                (
                    datetime.fromisoformat(row["start"]),
                    {
                        "raw": (row.get("plane_forecasts") or {}).get(plane.key),
                        # All strings of the plane measured, or nothing for the slot
                        "measured": sum(strings[s] for s in plane.strings)
                        if plane.strings and all((strings := row.get("actual_strings") or {}).get(s) is not None for s in plane.strings)
                        else None,
                    },
                )
                for row in rows
            ]
        )
        ahead = plane_pv.get(plane.key)
        forecast = (
            _hourly(
                [
                    (start, {name: ahead[name][i] for name in ("raw", "low", "high", "expected")})
                    for i, start in enumerate(slot_starts)
                ]
            )
            if ahead
            else []
        )
        result.append(
            {
                "key": plane.key,
                "name": plane.display_name,
                "strings": len(plane.strings),
                "history": history,
                "forecast": forecast,
                "measured_share": _energy_ratio(history, "measured", "raw"),
                "expected_share": _energy_ratio(forecast, "expected", "raw"),
            }
        )
    return result
