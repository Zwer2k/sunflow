"""Heating curve, learned from heat_pump_samples (design/heat_pump.md, 8).

    flow = R + S · (R − A) + P

R is the room setpoint, A the outside temperature, S the slope, P the shift (K). Start values per heat pump
(settable): until set, the slope of the heat pump's own curve when the profile reads it (HPSU "Heizkurve",
then stored), else one per heating surface type; P = 0. Learned every hour without a waiting time: from hours
of valid heating in which the room was close to its setpoint, the effective flow setpoint then was enough - a
point (R − A, flow − R). A weighted straight line through them (recent days weigh more), pulled towards the
start values as if CURVE_PRIOR_HOURS hours on the start curve said so: with few points the curve stays near
the start, with many the data count. The share of the data is learned_pct.

Helpers shared with learned.py and the house model (rows of heat_pump_samples) live here as well.
Computed from the table; what is kept in heat_pump_state.json is only a cache.
"""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import math
from typing import Any

from .defaults import (
    CURVE_COMPRESSOR_FULL,
    CURVE_HALF_LIFE_DAYS,
    CURVE_HOUR_MIN_ROWS,
    CURVE_PRIOR_HOURS,
    CURVE_PRIOR_X_K,
    CURVE_ROOM_BAND_K,
    CURVE_SHIFT_RANGE_K,
    CURVE_SLOPE_RANGE,
    curve_slope,
)

INTERVAL = timedelta(minutes=5)
HOUR = timedelta(hours=1)
# Cache format of a curve in heat_pump_state.json; a different one is computed anew
CACHE_FORMAT = 3
# Where the start slope comes from
FROM_HEAT_PUMP = "heat_pump"
FROM_SURFACE = "surface"
FROM_USER = "user"
FROM_LEARNED = "learned"


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _start(row: dict[str, Any]) -> datetime | None:
    start = row.get("start")
    if isinstance(start, datetime):
        return start.astimezone(UTC)
    try:
        return datetime.fromisoformat(start).astimezone(UTC) if isinstance(start, str) else None
    except ValueError:
        return None


def row_room(row: dict[str, Any], weights: dict[str, float]) -> float | None:
    """Weighted mean of the configured rooms in a row; None without one of them."""
    total = weight = 0.0
    for entity_id, value in (row.get("rooms") or {}).items():
        if entity_id in weights and (number := _float(value)) is not None:
            total += number * weights[entity_id]
            weight += weights[entity_id]
    return total / weight if weight > 0 else None


def _full(row: dict[str, Any] | None) -> bool:
    return row is not None and (_float(row.get("compressor")) or 0.0) >= CURVE_COMPRESSOR_FULL


def learnable(row: dict[str, Any]) -> bool:
    """Valid heating without after-run and without a fast change (flags of heat_pump_samples)."""
    flags = row.get("flags") or {}
    return flags.get("valid") is True and not flags.get("after_run") and not flags.get("ventilation")


def usable_slope(value: float | None, slope_range: tuple[float, float]) -> float | None:
    """A slope the heat pump reports as start value: above 0 (0 = not set) and within the range, else None."""
    low, high = slope_range
    return value if value is not None and value > 0 and low <= value <= high else None


@dataclass(frozen=True, kw_only=True)
class Start:
    """Start values of the curve and where they came from (user, heat_pump, learned or surface)."""

    slope: float
    shift_k: float
    source: str

    def same(self, slope: float, shift_k: float) -> bool:
        return abs(self.slope - slope) < 1e-6 and abs(self.shift_k - shift_k) < 1e-6


def start_values(
    slope: float | None, shift_k: float | None, source: str | None, surface: str | None, heat_pump_slope: float | None,
) -> Start:
    """The start values: the stored ones (the user's, read from the heat pump or taken over from the learned
    curve); while none is stored, the heat pump's own slope, else the heating surface type's; shift 0."""
    if slope is not None:
        return Start(slope=slope, shift_k=shift_k or 0.0, source=source or FROM_USER)
    if heat_pump_slope is not None and heat_pump_slope > 0:
        return Start(slope=heat_pump_slope, shift_k=shift_k or 0.0, source=FROM_HEAT_PUMP)
    return Start(slope=curve_slope(surface), shift_k=shift_k or 0.0, source=FROM_SURFACE)


@dataclass(frozen=True, kw_only=True)
class CurvePoint:
    """One hour that was enough: room setpoint, outside and effective flow setpoint (means)."""

    start: datetime
    room_c: float
    outside_c: float
    flow_c: float


def select_points(rows: list[dict[str, Any]], weights: dict[str, float]) -> list[CurvePoint]:
    """Hours with at least CURVE_HOUR_MIN_ROWS rows of valid heating, the compressor running, the room within
    CURVE_ROOM_BAND_K of the row's setpoint and the effective setpoint and outside known. Rows without a stored
    setpoint (recorded before it was kept) do not count."""
    hours: dict[datetime, list[tuple[float, float, float]]] = defaultdict(list)
    for row in rows:
        start = _start(row)
        if start is None or not learnable(row) or not _full(row):
            continue
        flow, outside, room = _float(row.get("setpoint_c")), _float(row.get("outside_c")), row_room(row, weights)
        target = _float(row.get("room_setpoint_c"))
        if target is None or flow is None or outside is None or room is None or abs(room - target) > CURVE_ROOM_BAND_K:
            continue
        hours[start.replace(minute=0)].append((target, outside, flow))
    points = []
    for start, values in sorted(hours.items()):
        if len(values) < CURVE_HOUR_MIN_ROWS:
            continue
        n = len(values)
        points.append(CurvePoint(
            start=start, room_c=sum(v[0] for v in values) / n, outside_c=sum(v[1] for v in values) / n,
            flow_c=sum(v[2] for v in values) / n,
        ))
    return points


def fit(points: list[CurvePoint], start: Start, now: datetime, slope_range: tuple[float, float]) -> tuple[float, float, float]:
    """Slope, shift and the share of the data (0-1): weighted least squares of (flow − R) over (R − A), with
    CURVE_PRIOR_HOURS virtual hours on the start curve spread over CURVE_PRIOR_X_K - few points move it a
    little, many count most; the start keeps its share. Within slope_range and ±CURVE_SHIFT_RANGE_K."""
    prior = CURVE_PRIOR_HOURS / len(CURVE_PRIOR_X_K)
    data = [(x, start.slope * x + start.shift_k, prior) for x in CURVE_PRIOR_X_K]
    for p in points:
        age = max((now - p.start).total_seconds() / 86400, 0.0)
        data.append((p.room_c - p.outside_c, p.flow_c - p.room_c, 0.5 ** (age / CURVE_HALF_LIFE_DAYS)))
    sw = sum(w for _, _, w in data)
    mx = sum(w * x for x, _, w in data) / sw
    my = sum(w * y for _, y, w in data) / sw
    sxx = sum(w * (x - mx) ** 2 for x, _, w in data)
    sxy = sum(w * (x - mx) * (y - my) for x, y, w in data)
    slope = sxy / sxx
    low, high = slope_range
    slope = min(max(slope, low), high)
    shift = min(max(my - slope * mx, -CURVE_SHIFT_RANGE_K), CURVE_SHIFT_RANGE_K)
    share = 1.0 - CURVE_PRIOR_HOURS / sw
    return slope, shift, share


@dataclass(frozen=True, kw_only=True)
class Curve:
    """The heating curve in use: slope, shift, where the start slope came from and the data it rests on."""

    slope: float
    shift_k: float
    start_slope: float
    start_shift_k: float
    start_source: str
    hours: int
    # Share of the learned hours in the curve (0-1); the rest is the start
    learned_share: float
    computed_at: datetime
    # What it depends on besides the table (heating surface type, rooms, start slope); a change relearns it
    key: str = ""

    @property
    def start(self) -> Start:
        return Start(slope=self.start_slope, shift_k=self.start_shift_k, source=self.start_source)

    def flow_c(self, room_c: float, outside_c: float) -> float:
        """The flow the curve gives for a room setpoint and an outside temperature."""
        return room_c + self.slope * (room_c - outside_c) + self.shift_k

    def as_dict(self) -> dict[str, Any]:
        """For the cache in heat_pump_state.json."""
        return asdict(self) | {"format": CACHE_FORMAT, "computed_at": self.computed_at.isoformat()}

    def status(self) -> dict[str, Any]:
        """For the status and the UI."""
        return {
            "slope": round(self.slope, 3), "shift_k": round(self.shift_k, 2), "start_slope": round(self.start_slope, 3),
            "start_shift_k": round(self.start_shift_k, 2), "start_source": self.start_source, "hours": self.hours,
            "learned_pct": round(100 * self.learned_share), "computed_at": self.computed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Curve | None":
        """From the cache; None if unreadable or of another format."""
        if not isinstance(data, dict) or data.get("format") != CACHE_FORMAT:
            return None
        try:
            return cls(
                slope=float(data["slope"]), shift_k=float(data["shift_k"]), start_slope=float(data["start_slope"]),
                start_shift_k=float(data["start_shift_k"]), start_source=str(data["start_source"]), hours=int(data["hours"]),
                learned_share=float(data["learned_share"]),
                computed_at=datetime.fromisoformat(data["computed_at"]), key=str(data.get("key") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


def start_curve(start: Start, now: datetime, key: str = "") -> Curve:
    """The curve before anything is learned: the start values."""
    return Curve(
        slope=start.slope, shift_k=start.shift_k, start_slope=start.slope, start_shift_k=start.shift_k,
        start_source=start.source, hours=0, learned_share=0.0, computed_at=now, key=key,
    )


def learn(
    rows: list[dict[str, Any]], weights: dict[str, float], start: Start, now: datetime,
    slope_range: tuple[float, float] = CURVE_SLOPE_RANGE, key: str = "",
) -> Curve:
    """The curve from the rows (any order), pulled towards the start values."""
    points = select_points(rows, weights)
    slope, shift, share = fit(points, start, now, slope_range)
    return Curve(
        slope=slope, shift_k=shift, start_slope=start.slope, start_shift_k=start.shift_k, start_source=start.source,
        hours=len(points), learned_share=share, computed_at=now, key=key,
    )
