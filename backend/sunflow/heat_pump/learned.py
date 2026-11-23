"""Values of the protection layer learned from heat_pump_samples (design/heat_pump.md, 10, 11, 13).

- steady_swing: how far Tv peaks above its mean in steady running (rows with the compressor on through
  the row and SWING_STEADY_ROWS before, no start among them): a high quantile of tv_max − tv.
- Stop margin: every compressor stop is recorded as an event ("stop", samples.py) with Tv, its peak, Tv of
  the UART node, the effective setpoint and the activity before. Each is classified: a defrost or hot water
  following within STOP_CLASSIFY_S, the block, another activity before it - or unexplained. Unexplained
  stops at a Tv above the setpoint within STOP_MARGIN_RANGE_K are the heat pump's own shutdown rule: the
  stop margin is their low quantile, once there are STOP_MIN_EVENTS (a margin above the profile's start
  value needs STOP_MIN_EVENTS_ABOVE, the safe direction is taken sooner).
- Heat output range: the minimum is a low quantile of the heat in steady running; the maximum is only
  raised above the start value by a high quantile (mild weeks do not show it).
Until there is enough data the profile's start values count. Computed from the table every hour; what is
kept in heat_pump_state.json is only a cache.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .curve import INTERVAL, _float, _full, _start
from .defaults import (
    POWER_HEAT_MAX_W,
    POWER_HEAT_MIN_W,
    POWER_MAX_QUANTILE,
    POWER_MIN_QUANTILE,
    POWER_MIN_ROWS,
    STOP_CLASSIFY_S,
    STOP_LEARN_DAYS,
    STOP_MARGIN_RANGE_K,
    STOP_MIN_EVENTS,
    STOP_MIN_EVENTS_ABOVE,
    STOP_QUANTILE,
    STOPS_SHOWN,
    SWING_MIN_ROWS,
    SWING_QUANTILE,
    SWING_STEADY_ROWS,
)
from .limits import ControlSettings

# Cache format in heat_pump_state.json; a different one is computed anew
CACHE_FORMAT = 2
STOP_EVENT = "stop"
# Classes of a compressor stop
DEFROST = "defrost"
HOT_WATER = "hot_water"
BLOCK = "block"
OTHER = "other"
UNEXPLAINED = "unexplained"
# Too recent to tell what followed it
PENDING = "pending"
STOP_CLASSES = (UNEXPLAINED, DEFROST, HOT_WATER, BLOCK, OTHER, PENDING)


def quantile(values: list[float], share: float) -> float:
    """The value below which share of the values lie (no interpolation)."""
    ordered = sorted(values)
    return ordered[int(share * (len(ordered) - 1))]


def steady_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows of steady running: valid heating, the compressor on through the row and SWING_STEADY_ROWS
    rows before, no start in any of them."""
    by_start = {start: row for row in rows if (start := _start(row)) is not None}

    def calm(row: dict[str, Any] | None) -> bool:
        return _full(row) and not any(e.get("kind") == "start" for e in (row or {}).get("events") or [] if isinstance(e, dict))

    result = []
    for start, row in sorted(by_start.items()):
        if (row.get("flags") or {}).get("valid") is not True:
            continue
        if all(calm(by_start.get(start - k * INTERVAL)) for k in range(SWING_STEADY_ROWS + 1)):
            result.append(row)
    return result


def classify_stops(rows: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """Every compressor stop of the rows with its class and how far Tv was above the effective setpoint;
    a stop whose following STOP_CLASSIFY_S are not stored yet is pending."""
    activities = sorted((start, row.get("activity")) for row in rows if (start := _start(row)) is not None)
    window = timedelta(seconds=STOP_CLASSIFY_S)
    stops = []
    for row in rows:
        for event in row.get("events") or []:
            if not isinstance(event, dict) or event.get("kind") != STOP_EVENT:
                continue
            try:
                at = datetime.fromisoformat(event["at"])
            except (KeyError, TypeError, ValueError):
                continue
            # The rows overlapping the stop and the following STOP_CLASSIFY_S
            after = {activity for start, activity in activities if at - INTERVAL < start <= at + window}
            if event.get("blocked"):
                kind = BLOCK
            elif DEFROST in after:
                kind = DEFROST
            elif HOT_WATER in after:
                kind = HOT_WATER
            elif event.get("activity") not in (None, "heating"):
                kind = OTHER
            elif now is not None and at > now - window - INTERVAL:
                kind = PENDING
            else:
                kind = UNEXPLAINED
            tv, peak, setpoint = _float(event.get("tv_c")), _float(event.get("tv_max_c")), _float(event.get("setpoint_c"))
            top = max((v for v in (tv, peak) if v is not None), default=None)
            uart = _float(event.get("tv_uart_c"))
            stops.append({
                "at": event["at"], "kind": kind, "tv_c": tv, "peak_c": top, "tv_uart_c": uart, "tvbh_c": _float(event.get("tvbh_c")),
                "setpoint_c": setpoint, "max_setpoint_c": _float(event.get("max_setpoint_c")),
                "margin_k": round(top - setpoint, 2) if top is not None and setpoint is not None else None,
                "margin_uart_k": round(uart - setpoint, 2) if uart is not None and setpoint is not None else None,
            })
    return sorted(stops, key=lambda s: s["at"])


def stop_margins(stops: list[dict[str, Any]]) -> list[float]:
    """Margins of the unexplained stops above the setpoint: where the heat pump's own rule switched off."""
    low, high = STOP_MARGIN_RANGE_K
    return [s["margin_k"] for s in stops if s["kind"] == UNEXPLAINED and s["margin_k"] is not None and low <= s["margin_k"] <= high]


@dataclass(frozen=True, kw_only=True)
class Learned:
    """What was learned, None where the data are not enough yet, and what it rests on."""

    computed_at: datetime
    steady_swing: float | None = None
    swing_rows: int = 0
    # Stop margin: the low quantile of the unexplained stops' margins; their count; per class the stops
    stop_margin: float | None = None
    stop_samples: int = 0
    stop_counts: dict[str, int] = field(default_factory=dict)
    # The latest stops, newest first (status, UI)
    stops: list[dict[str, Any]] = field(default_factory=list)
    power_min_w: float | None = None
    power_max_w: float | None = None
    power_rows: int = 0

    def as_dict(self) -> dict[str, Any]:
        """For the cache in heat_pump_state.json and the status."""
        return {
            "format": CACHE_FORMAT, "computed_at": self.computed_at.isoformat(), "steady_swing": self.steady_swing,
            "swing_rows": self.swing_rows, "stop_margin": self.stop_margin, "stop_samples": self.stop_samples,
            "stop_counts": self.stop_counts, "stops": self.stops, "power_min_w": self.power_min_w,
            "power_max_w": self.power_max_w, "power_rows": self.power_rows,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Learned | None":
        """From the cache; None if unreadable or of another format."""
        if not isinstance(data, dict) or data.get("format") != CACHE_FORMAT:
            return None
        try:
            return cls(
                computed_at=datetime.fromisoformat(data["computed_at"]),
                steady_swing=_float(data.get("steady_swing")),
                swing_rows=int(data.get("swing_rows") or 0),
                stop_margin=_float(data.get("stop_margin")),
                stop_samples=int(data.get("stop_samples") or 0),
                stop_counts={k: int(v) for k, v in (data.get("stop_counts") or {}).items() if k in STOP_CLASSES},
                stops=[s for s in data.get("stops") or [] if isinstance(s, dict)],
                power_min_w=_float(data.get("power_min_w")),
                power_max_w=_float(data.get("power_max_w")),
                power_rows=int(data.get("power_rows") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None


def learn(rows: list[dict[str, Any]], now: datetime, start_stop_margin: float | None = None) -> Learned:
    """Swing, stop margin and heat output range from the rows (oldest first); start_stop_margin: the
    profile's value (a learned margin above it needs more stops)."""
    steady = steady_rows(rows)
    swings = []
    heats = []
    for row in steady:
        tv, peak, heat = _float(row.get("tv_c")), _float(row.get("tv_max_c")), _float(row.get("heat_w"))
        if tv is not None and peak is not None:
            swings.append(max(peak - tv, 0.0))
        if heat is not None and POWER_HEAT_MIN_W <= heat <= POWER_HEAT_MAX_W:
            heats.append(heat)
    since = (now - timedelta(days=STOP_LEARN_DAYS)).isoformat()
    stops = [s for s in classify_stops(rows, now) if s["at"] >= since]
    margins = stop_margins(stops)
    margin = round(quantile(margins, STOP_QUANTILE), 2) if len(margins) >= STOP_MIN_EVENTS else None
    if margin is not None and start_stop_margin is not None and margin > start_stop_margin and len(margins) < STOP_MIN_EVENTS_ABOVE:
        margin = None
    counts = {kind: sum(1 for s in stops if s["kind"] == kind) for kind in STOP_CLASSES}
    return Learned(
        computed_at=now,
        steady_swing=round(quantile(swings, SWING_QUANTILE), 2) if len(swings) >= SWING_MIN_ROWS else None,
        swing_rows=len(swings),
        stop_margin=margin,
        stop_samples=len(margins),
        stop_counts=counts,
        stops=list(reversed(stops[-STOPS_SHOWN:])),
        power_min_w=round(quantile(heats, POWER_MIN_QUANTILE)) if len(heats) >= POWER_MIN_ROWS else None,
        power_max_w=round(quantile(heats, POWER_MAX_QUANTILE)) if len(heats) >= POWER_MIN_ROWS else None,
        power_rows=len(heats),
    )


@dataclass(frozen=True, kw_only=True)
class Values:
    """The values the protection layer uses now: learned, else the profile's start value."""

    steady_swing: float
    start_margin: float
    stop_margin: float
    power_min_w: float
    power_max_w: float
    # Where each comes from: start, learned
    sources: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        """For the status."""
        return {
            "steady_swing": round(self.steady_swing, 2),
            "start_margin": round(self.start_margin, 2),
            "stop_margin": round(self.stop_margin, 2),
            "power_min_w": round(self.power_min_w), "power_max_w": round(self.power_max_w),
            "sources": self.sources,
        }


def values(settings: ControlSettings, learned: Learned | None) -> Values:
    """Learned values where there are enough data, else the start values of the profile."""
    swing = learned.steady_swing if learned and learned.steady_swing is not None else None
    stop = learned.stop_margin if learned and learned.stop_margin is not None else None
    power_min = learned.power_min_w if learned and learned.power_min_w is not None else settings.power_min_w
    power_max = max(settings.power_max_w, learned.power_max_w) if learned and learned.power_max_w is not None else settings.power_max_w
    return Values(
        steady_swing=swing if swing is not None else settings.steady_swing,
        start_margin=settings.start_margin,
        stop_margin=stop if stop is not None else settings.stop_margin,
        power_min_w=power_min,
        power_max_w=max(power_max, power_min),
        sources={
            "steady_swing": "learned" if swing is not None else "start",
            "start_margin": "start",
            "stop_margin": "learned" if stop is not None else "start",
            "power": "learned" if learned and learned.power_min_w is not None else "start",
        },
    )
