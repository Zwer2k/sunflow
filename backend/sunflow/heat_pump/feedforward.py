"""Feedforward of the flow target from the heating curve (design/heat_pump.md, 8.1).

    V_Ziel = target room temperature + (Q*/K)^(1/n) + s(Q*)/2 + correction

Q* is the heat the house needs now; its source in this order:
1. house_model: the learned house model, Q* = (a·(target − T_out) − d − c·S)/b with the measured
   outside temperature (gains: internal d and sun c·S);
2. heat_recent: the mean heat of the last FEED_HEAT_WINDOW (3 h) from the minutes of valid heating,
   compressor pauses as 0; hot water, defrost, after-run, ventilation, block and the rest count
   neither as heat nor as time. Needs FEED_HEAT_MIN (2 h) of counted minutes. This heat comes from
   the flow the heat pump runs now, so the feedforward only follows the current state; the direction
   comes from the correction alone;
3. base: no feedforward, V_Ziel = base + correction (as without a curve), also when the curve is not
   usable (too little data, not plausible, not better than "the flow stays") or Q* is not above
   FEED_HEAT_MIN_W.
A change of the source is only shown (and noted in heat_pump_samples); in shadow there is no guard
against the jump it causes.
"""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .curve import Curve
from .defaults import FEED_HEAT_MIN_S, FEED_HEAT_MIN_W, FEED_HEAT_WINDOW_S

if TYPE_CHECKING:
    from sunflow.planning.house_model import HouseModel

    from .samples import Minute

WINDOW = timedelta(seconds=FEED_HEAT_WINDOW_S)
MINUTE = timedelta(minutes=1)
INTERVAL = timedelta(minutes=5)
BASE = "base"
HOUSE_MODEL = "house_model"
HEAT_RECENT = "heat_recent"
# Frozen reason of a minute in which the compressor pauses in the heating program
PAUSE = "activity_idle"


class RecentHeat:
    """Heat of the minutes of the last FEED_HEAT_WINDOW that count (8.1, source heat_recent)."""

    def __init__(self) -> None:
        """Empty; seeded from heat_pump_samples after a start."""
        # (time, counted minutes, mean heat W)
        self._values: deque[tuple[datetime, float, float]] = deque()
        self.seeded = False

    def add(self, minute: "Minute", aired: bool) -> None:
        """One minute: valid heating with its heat, a compressor pause as 0, anything else not at all."""
        if aired:
            return
        evaluation = minute.evaluation
        if evaluation.valid and minute.heat_w is not None:
            self._values.append((minute.at, 1.0, max(minute.heat_w, 0.0)))
        elif set(evaluation.frozen) == {PAUSE}:
            self._values.append((minute.at, 1.0, 0.0))

    def seed(self, rows: list[dict[str, Any]], now: datetime) -> None:
        """Rows of heat_pump_samples of the window, as their minutes (before the first minute after a start)."""
        self.seeded = True
        values = []
        for row in rows:
            start = row.get("start")
            start = datetime.fromisoformat(start) if isinstance(start, str) else start
            if not isinstance(start, datetime):
                continue
            end = start.astimezone(UTC) + INTERVAL
            flags = row.get("flags") or {}
            frozen = set(flags.get("frozen") or [])
            if flags.get("ventilation") or flags.get("after_run") or not frozen <= {PAUSE} or end > now or now - end >= WINDOW:
                continue
            heat = row.get("heat_w")
            if heat is None:
                if flags.get("valid") or row.get("compressor"):
                    continue
                heat = 0.0
            values.append((end, float(row.get("samples") or 0), max(float(heat), 0.0)))
        self._values = deque(sorted(values) + list(self._values))

    def mean(self, now: datetime) -> tuple[float | None, float]:
        """Mean heat (W) over the counted minutes of the window, None below FEED_HEAT_MIN; and the counted seconds."""
        while self._values and now - self._values[0][0] >= WINDOW:
            self._values.popleft()
        minutes = sum(count for _, count, _ in self._values)
        seconds = minutes * MINUTE.total_seconds()
        if seconds < FEED_HEAT_MIN_S or minutes <= 0:
            return None, seconds
        return sum(count * heat for _, count, heat in self._values) / minutes, seconds


@dataclass(frozen=True, kw_only=True)
class HouseContext:
    """The house model the planning learned, with the sun reference to compute its gains now."""

    model: "HouseModel"
    sun_entity: str | None = None
    # Sun value of a bright hour (the model's sun is relative to it, 0-1)
    sun_bright: float | None = None


def house_heat_w(house: HouseContext | None, target_c: float, outside_c: float | None, sun: float | None) -> float | None:
    """Q* from the house model (W): (a·(target − outside) − d − c·S)/b; None without a usable model or outside."""
    if house is None or outside_c is None:
        return None
    model = house.model
    if not model.useful or model.b is None or model.b <= 0:
        return None
    gains = model.d
    if model.c is not None and sun is not None and house.sun_bright:
        gains += model.c * min(max(sun / house.sun_bright, 0.0), 1.0)
    return (model.a * (target_c - outside_c) - gains) / model.b * 1000


@dataclass(frozen=True, kw_only=True)
class Feed:
    """Which source the flow target uses and what the curve gives."""

    source: str
    # Why the base is used (curve_learning, curve_unsure, curve_implausible, curve_stale, no_curve, no_heat, heat_low)
    reason: str | None
    heat_w: float | None
    curve_c: float | None


def feedforward(
    curve: Curve | None, target_c: float, house_w: float | None, recent_w: float | None, surface: str | None = None
) -> Feed:
    """Source in order house model, recent heat, base; the curve's flow for Q* unless the base is used."""
    if curve is None:
        return Feed(source=BASE, reason="no_curve", heat_w=None, curve_c=None)
    if surface is not None and curve.surface != surface:
        # The heating surface type changed: evaluated anew with the next learning
        return Feed(source=BASE, reason="curve_stale", heat_w=None, curve_c=None)
    if not curve.usable:
        return Feed(source=BASE, reason=f"curve_{curve.state}", heat_w=None, curve_c=None)
    source, heat = (HOUSE_MODEL, house_w) if house_w is not None else (HEAT_RECENT, recent_w) if recent_w is not None else (BASE, None)
    if heat is None:
        return Feed(source=BASE, reason="no_heat", heat_w=None, curve_c=None)
    if heat <= FEED_HEAT_MIN_W:
        return Feed(source=BASE, reason="heat_low", heat_w=heat, curve_c=None)
    return Feed(source=source, reason=None, heat_w=heat, curve_c=curve.flow_c(target_c, heat))
