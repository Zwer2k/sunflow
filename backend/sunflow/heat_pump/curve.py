"""Heating curve of the heating surfaces, learned from heat_pump_samples (design/heat_pump.md, 8.1, 9.2).

    Q = K · (Tw − T_room)^n,   Tw = (Tv + Tr)/2,   spread s = Tv − Tr = s0 + s1 · Q

n is the start value of the heating surface type (house.heating_surface) and is not learned. K and
the spread model are fitted by a weighted ridge regression on the valid intervals of the last weeks;
recent days weigh more (half-life). The last days are held out: the curve predicts the flow
temperature there from the measured heat and room temperature, and is used only when it does so
better than "the flow stays" (the flow at the first held out interval of each day) and its
parameters are plausible.

Only intervals of valid heating (section 4) count, without after-run, without ventilation, with the
compressor running through the interval and long enough before it. Everything else is left out, not
counted as zero. Computed from the table; what is kept in heat_pump_state.json is only a cache.
"""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import math
from statistics import median
from typing import Any

import numpy as np

from .defaults import (
    CURVE_COMPRESSOR_FULL,
    CURVE_DT_MIN_K,
    CURVE_HALF_LIFE_DAYS,
    CURVE_HEAT_MAX_W,
    CURVE_HEAT_MIN_W,
    CURVE_HOLDOUT_DAYS,
    CURVE_MIN_DAYS,
    CURVE_MIN_HELD,
    CURVE_MIN_INTERVALS,
    CURVE_MIN_ROW_SAMPLES,
    CURVE_OVERTEMP_RANGE_K,
    CURVE_RIDGE,
    CURVE_RUN_BEFORE_S,
    CURVE_SPREAD_RANGE_K,
    curve_exponent,
)

INTERVAL = timedelta(minutes=5)
RUN_BEFORE = timedelta(seconds=CURVE_RUN_BEFORE_S)
# Cache format of a curve in heat_pump_state.json; a different one is computed anew
CACHE_FORMAT = 1


@dataclass(frozen=True, kw_only=True)
class CurvePoint:
    """One valid interval: room, flow and return temperature, heat."""

    start: datetime
    room_c: float
    tv_c: float
    tr_c: float
    heat_w: float


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
    """Valid heating without after-run and without ventilation (flags of heat_pump_samples)."""
    flags = row.get("flags") or {}
    return flags.get("valid") is True and not flags.get("after_run") and not flags.get("ventilation")


def select_points(rows: list[dict[str, Any]], weights: dict[str, float]) -> list[CurvePoint]:
    """The intervals the curve is learned from (9.1); all others are left out."""
    by_start = {start: row for row in rows if (start := _start(row)) is not None}
    before = int(RUN_BEFORE / INTERVAL)
    points = []
    for start, row in sorted(by_start.items()):
        if not learnable(row) or (row.get("samples") or 0) < CURVE_MIN_ROW_SAMPLES:
            continue
        # The compressor ran through this interval and RUN_BEFORE before it
        if not all(_full(by_start.get(start - k * INTERVAL)) for k in range(before + 1)):
            continue
        tv, tr, heat = _float(row.get("tv_c")), _float(row.get("tr_c")), _float(row.get("heat_w"))
        room = row_room(row, weights)
        if tv is None or tr is None or heat is None or room is None:
            continue
        if not CURVE_HEAT_MIN_W <= heat <= CURVE_HEAT_MAX_W or (tv + tr) / 2 - room < CURVE_DT_MIN_K:
            continue
        points.append(CurvePoint(start=start, room_c=room, tv_c=tv, tr_c=tr, heat_w=heat))
    return points


@dataclass(frozen=True, kw_only=True)
class Curve:
    """The learned curve with its quality and how much data it rests on."""

    surface: str
    n: float
    # W per K^n; None without enough data to fit
    k: float | None
    # Spread s = spread0_k + spread_k_per_kw · Q
    spread0_k: float | None
    spread_k_per_kw: float | None
    intervals: int
    days: int
    held: int
    # Root mean square error of the flow temperature on the held out days, and of "the flow stays"
    error_k: float | None
    baseline_k: float | None
    first: datetime | None
    last: datetime | None
    computed_at: datetime
    # Room weights and type the curve was computed with (a change computes it anew)
    key: str = ""
    # Days with intervals before the held out ones
    train_days: int = 0
    # Median heat of the intervals: where plausibility and the typical values are judged
    typical_w: float | None = None

    def overtemp_k(self, heat_w: float) -> float:
        """Mean water temperature above the room for a heat."""
        assert self.k is not None
        return (max(heat_w, 0.0) / self.k) ** (1 / self.n)

    def spread_k(self, heat_w: float) -> float:
        """Flow minus return for a heat."""
        return (self.spread0_k or 0.0) + (self.spread_k_per_kw or 0.0) * heat_w / 1000

    def flow_c(self, room_c: float, heat_w: float) -> float:
        """Flow temperature that gives the heat at the room temperature: T + (Q/K)^(1/n) + s(Q)/2."""
        return room_c + self.overtemp_k(heat_w) + self.spread_k(heat_w) / 2

    @property
    def enough(self) -> bool:
        """Enough data: training intervals and days, held out intervals."""
        return (
            self.k is not None
            and self.intervals - self.held >= CURVE_MIN_INTERVALS
            and self.train_days >= CURVE_MIN_DAYS
            and self.held >= CURVE_MIN_HELD
        )

    def plausible_at(self, heat_w: float | None) -> bool:
        """K positive, mean water temperature above the room and spread in their ranges at a heat."""
        if self.k is None or self.k <= 0 or heat_w is None or heat_w <= 0:
            return False
        over, spread = self.overtemp_k(heat_w), self.spread_k(heat_w)
        return (
            CURVE_OVERTEMP_RANGE_K[0] <= over <= CURVE_OVERTEMP_RANGE_K[1]
            and CURVE_SPREAD_RANGE_K[0] <= spread <= CURVE_SPREAD_RANGE_K[1]
        )

    @property
    def state(self) -> str:
        """learning (too little data), implausible, unsure (not better than "the flow stays") or ready."""
        if not self.enough:
            return "learning"
        if not self.plausible_at(self.typical_w):
            return "implausible"
        if self.error_k is None or self.baseline_k is None or self.error_k >= self.baseline_k:
            return "unsure"
        return "ready"

    @property
    def usable(self) -> bool:
        """Used for the feedforward (8.1)."""
        return self.state == "ready"

    def as_dict(self) -> dict[str, Any]:
        """For the cache and the status."""
        data = asdict(self)
        for name in ("first", "last", "computed_at"):
            data[name] = data[name].isoformat() if data[name] else None
        data["format"] = CACHE_FORMAT
        return data

    def status(self) -> dict[str, Any]:
        """Learned values, quality and amount of data for the UI and the attributes (shadow)."""
        typical = self.typical_w
        return {
            "shadow": True,
            "state": self.state,
            "surface": self.surface,
            "n": self.n,
            "k": round(self.k, 2) if self.k is not None else None,
            "spread0_k": round(self.spread0_k, 3) if self.spread0_k is not None else None,
            "spread_k_per_kw": round(self.spread_k_per_kw, 3) if self.spread_k_per_kw is not None else None,
            "typical_heat_w": round(typical) if typical is not None else None,
            "typical_overtemp_k": round(self.overtemp_k(typical), 2) if typical and self.k and self.k > 0 else None,
            "typical_spread_k": round(self.spread_k(typical), 2) if typical and self.k is not None else None,
            "error_k": round(self.error_k, 3) if self.error_k is not None else None,
            "baseline_k": round(self.baseline_k, 3) if self.baseline_k is not None else None,
            "intervals": self.intervals,
            "held": self.held,
            "days": self.days,
            "min_intervals": CURVE_MIN_INTERVALS + CURVE_MIN_HELD,
            "train_days": self.train_days,
            "min_days": CURVE_MIN_DAYS,
            "first": self.first.isoformat() if self.first else None,
            "last": self.last.isoformat() if self.last else None,
            "computed_at": self.computed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Curve | None":
        """From the cache; None if unreadable or of another format."""
        if not isinstance(data, dict) or data.get("format") != CACHE_FORMAT:
            return None
        try:
            times = {name: datetime.fromisoformat(data[name]) if data.get(name) else None for name in ("first", "last", "computed_at")}
            if times["computed_at"] is None:
                return None
            return cls(
                surface=str(data["surface"]),
                n=float(data["n"]),
                k=_float(data.get("k")),
                spread0_k=_float(data.get("spread0_k")),
                spread_k_per_kw=_float(data.get("spread_k_per_kw")),
                intervals=int(data["intervals"]),
                days=int(data["days"]),
                held=int(data["held"]),
                error_k=_float(data.get("error_k")),
                baseline_k=_float(data.get("baseline_k")),
                first=times["first"],
                last=times["last"],
                computed_at=times["computed_at"],
                key=str(data.get("key", "")),
                train_days=int(data.get("train_days", 0)),
                typical_w=_float(data.get("typical_w")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _weights(points: list[CurvePoint], now: datetime) -> np.ndarray:
    age = np.array([(now - p.start).total_seconds() / 86400 for p in points])
    return 0.5 ** (np.maximum(age, 0.0) / CURVE_HALF_LIFE_DAYS)


def _fit(points: list[CurvePoint], n: float, now: datetime) -> tuple[float, float, float] | None:
    """K, spread0 and spread slope from points; None without at least two points or a usable K."""
    if len(points) < 2:
        return None
    w = _weights(points, now)
    x = np.array([((p.tv_c + p.tr_c) / 2 - p.room_c) ** n for p in points])
    q = np.array([p.heat_w for p in points])
    # Q = K·x, ridge relative to the column's own size
    gram = float(np.sum(w * x * x))
    if gram <= 0:
        return None
    k = float(np.sum(w * x * q)) / (gram * (1 + CURVE_RIDGE))
    if not math.isfinite(k) or k <= 0:
        return None
    # s = s0 + s1·Q (kW); the constant stays free
    design = np.column_stack([q / 1000, np.ones(len(points))])
    spread = np.array([p.tv_c - p.tr_c for p in points])
    weighted = design * w[:, None]
    normal = design.T @ weighted
    penalty = np.diag([CURVE_RIDGE * normal[0, 0] + 1e-9 * float(w.sum()), 0.0])
    slope, intercept = np.linalg.solve(normal + penalty, weighted.T @ spread)
    return k, float(intercept), float(slope)


def _flow(k: float, n: float, s0: float, s1: float, room: float, heat_w: float) -> float:
    return room + (heat_w / k) ** (1 / n) + (s0 + s1 * heat_w / 1000) / 2


def _errors(params: tuple[float, float, float], n: float, held: list[CurvePoint]) -> tuple[float | None, float | None]:
    """RMSE of the predicted flow and of "the flow stays as at the first interval of the day"."""
    k, s0, s1 = params
    by_day: dict[Any, list[CurvePoint]] = defaultdict(list)
    for point in held:
        by_day[point.start.date()].append(point)
    model_sq, stay_sq = [], []
    for points in by_day.values():
        anchor = points[0].tv_c
        for point in points[1:]:
            model_sq.append((_flow(k, n, s0, s1, point.room_c, point.heat_w) - point.tv_c) ** 2)
            stay_sq.append((anchor - point.tv_c) ** 2)
    if not model_sq:
        return None, None
    return math.sqrt(sum(model_sq) / len(model_sq)), math.sqrt(sum(stay_sq) / len(stay_sq))


def learn(rows: list[dict[str, Any]], weights: dict[str, float], surface: str, now: datetime, key: str = "") -> Curve:
    """The curve from the rows of heat_pump_samples (any order); weights: room -> weight of the configured rooms."""
    n = curve_exponent(surface)
    points = select_points(rows, weights)
    cutoff = now - timedelta(days=CURVE_HOLDOUT_DAYS)
    train = [p for p in points if p.start < cutoff]
    held = [p for p in points if p.start >= cutoff]
    error_k = baseline_k = None
    if (trial := _fit(train, n, cutoff)) is not None and held:
        error_k, baseline_k = _errors(trial, n, held)
    params = _fit(points, n, now)
    return Curve(
        surface=surface,
        n=n,
        k=params[0] if params else None,
        spread0_k=params[1] if params else None,
        spread_k_per_kw=params[2] if params else None,
        intervals=len(points),
        days=len({p.start.date() for p in points}),
        train_days=len({p.start.date() for p in train}),
        held=len(held),
        error_k=error_k,
        baseline_k=baseline_k,
        first=points[0].start if points else None,
        last=points[-1].start if points else None,
        computed_at=now,
        key=key,
        typical_w=median(p.heat_w for p in points) if points else None,
    )
