"""Values of the protection layer learned from heat_pump_samples (design/heat_pump.md, 8.3, 8.4, 9.1).

- steady_swing: how far Tv peaks above its mean in steady running (rows with the compressor on through
  the row and SWING_STEADY_ROWS before, no start among them): a high quantile of tv_max − tv.
- start_jump, start_settle per kind of start (normal, after hot water, after defrost): from the events
  `start_jump` the shadow control notes for each start (jump above the reference Tv, time until Tv was
  calm again); a high quantile once there are JUMP_MIN_EVENTS of a kind.
- Heat output range: the minimum is a low quantile of the heat in steady running; the maximum is only
  raised above the start value by a high quantile (mild weeks do not show it).
Until there is enough data the profile's start values count. After an unwanted shutdown the value
concerned is raised at once ("bump", kept in heat_pump_state.json); the raise fades over weeks.
Computed from the table every hour; what is kept in heat_pump_state.json is only a cache.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .curve import INTERVAL, _float, _full, _start
from .defaults import (
    BUMP_DECAY_K_PER_DAY,
    CURVE_HEAT_MAX_W,
    CURVE_HEAT_MIN_W,
    JUMP_MIN_EVENTS,
    JUMP_QUANTILE,
    POWER_MAX_QUANTILE,
    POWER_MIN_QUANTILE,
    POWER_MIN_ROWS,
    SWING_MIN_ROWS,
    SWING_QUANTILE,
    SWING_STEADY_ROWS,
)
from .limits import ControlSettings

NORMAL = "normal"
START_KINDS = (NORMAL, "hot_water", "defrost")
START_JUMP_EVENT = "start_jump"
# Cache format in heat_pump_state.json; a different one is computed anew
CACHE_FORMAT = 1


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


@dataclass(frozen=True, kw_only=True)
class Learned:
    """What was learned, None where the data are not enough yet, and what it rests on."""

    computed_at: datetime
    steady_swing: float | None = None
    swing_rows: int = 0
    start_jump: dict[str, float] = field(default_factory=dict)
    start_settle_s: dict[str, float] = field(default_factory=dict)
    starts: dict[str, int] = field(default_factory=dict)
    power_min_w: float | None = None
    power_max_w: float | None = None
    power_rows: int = 0

    def as_dict(self) -> dict[str, Any]:
        """For the cache in heat_pump_state.json."""
        return {
            "format": CACHE_FORMAT, "computed_at": self.computed_at.isoformat(), "steady_swing": self.steady_swing,
            "swing_rows": self.swing_rows, "start_jump": self.start_jump, "start_settle_s": self.start_settle_s,
            "starts": self.starts, "power_min_w": self.power_min_w, "power_max_w": self.power_max_w,
            "power_rows": self.power_rows,
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
                start_jump={k: float(v) for k, v in (data.get("start_jump") or {}).items() if k in START_KINDS},
                start_settle_s={k: float(v) for k, v in (data.get("start_settle_s") or {}).items() if k in START_KINDS},
                starts={k: int(v) for k, v in (data.get("starts") or {}).items() if k in START_KINDS},
                power_min_w=_float(data.get("power_min_w")),
                power_max_w=_float(data.get("power_max_w")),
                power_rows=int(data.get("power_rows") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None


def learn(rows: list[dict[str, Any]], now: datetime) -> Learned:
    """Swing, start jumps and settling times, heat output range from the rows (oldest first)."""
    steady = steady_rows(rows)
    swings = []
    heats = []
    for row in steady:
        tv, peak, heat = _float(row.get("tv_c")), _float(row.get("tv_max_c")), _float(row.get("heat_w"))
        if tv is not None and peak is not None:
            swings.append(max(peak - tv, 0.0))
        if heat is not None and CURVE_HEAT_MIN_W <= heat <= CURVE_HEAT_MAX_W:
            heats.append(heat)
    jumps: dict[str, list[float]] = {}
    settles: dict[str, list[float]] = {}
    for row in rows:
        for event in row.get("events") or []:
            if not isinstance(event, dict) or event.get("kind") != START_JUMP_EVENT or event.get("start_kind") not in START_KINDS:
                continue
            kind = event["start_kind"]
            if (jump := _float(event.get("jump_k"))) is not None:
                jumps.setdefault(kind, []).append(max(jump, 0.0))
            if (settle := _float(event.get("settle_s"))) is not None:
                settles.setdefault(kind, []).append(max(settle, 0.0))
    return Learned(
        computed_at=now,
        steady_swing=round(quantile(swings, SWING_QUANTILE), 2) if len(swings) >= SWING_MIN_ROWS else None,
        swing_rows=len(swings),
        start_jump={k: round(quantile(v, JUMP_QUANTILE), 2) for k, v in jumps.items() if len(v) >= JUMP_MIN_EVENTS},
        start_settle_s={k: round(quantile(v, JUMP_QUANTILE)) for k, v in settles.items() if len(v) >= JUMP_MIN_EVENTS},
        starts={k: len(v) for k, v in jumps.items()},
        power_min_w=round(quantile(heats, POWER_MIN_QUANTILE)) if len(heats) >= POWER_MIN_ROWS else None,
        power_max_w=round(quantile(heats, POWER_MAX_QUANTILE)) if len(heats) >= POWER_MIN_ROWS else None,
        power_rows=len(heats),
    )


def bump_value(bump: Any, now: datetime) -> float:
    """What is left of a raise after an unwanted shutdown ({"k", "at"}); 0 when faded or unreadable."""
    try:
        k, at = float(bump["k"]), datetime.fromisoformat(bump["at"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    days = max((now - at) / timedelta(days=1), 0.0)
    return max(k - BUMP_DECAY_K_PER_DAY * days, 0.0)


@dataclass(frozen=True, kw_only=True)
class Values:
    """The values the protection layer uses now: learned (else start value), raised by a bump."""

    steady_swing: float
    start_jump: dict[str, float]
    start_settle_s: dict[str, float]
    power_min_w: float
    power_max_w: float
    # Where each comes from: start, learned, raised
    sources: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        """For the status."""
        return {
            "steady_swing": round(self.steady_swing, 2),
            "start_jump": {k: round(v, 2) for k, v in self.start_jump.items()},
            "start_settle_s": {k: round(v) for k, v in self.start_settle_s.items()},
            "power_min_w": round(self.power_min_w), "power_max_w": round(self.power_max_w),
            "sources": self.sources,
        }


def values(settings: ControlSettings, learned: Learned | None, bumps: dict[str, Any], now: datetime) -> Values:
    """Learned values where there are enough data, else the start values; raised by what is left of a bump."""
    sources: dict[str, str] = {}

    def pick(name: str, start: float, learned_value: float | None) -> float:
        value, source = (learned_value, "learned") if learned_value is not None else (start, "start")
        raised = bump_value(bumps.get(name), now)
        if raised > value:
            value, source = raised, "raised"
        sources[name] = source
        return value

    swing = pick("steady_swing", settings.steady_swing, learned.steady_swing if learned else None)
    jumps = {k: pick(f"start_jump.{k}", settings.start_jump, (learned.start_jump if learned else {}).get(k)) for k in START_KINDS}
    settles = {}
    for kind in START_KINDS:
        learned_settle = (learned.start_settle_s if learned else {}).get(kind)
        settles[kind] = learned_settle if learned_settle is not None else settings.start_settle_s
        sources[f"start_settle.{kind}"] = "learned" if learned_settle is not None else "start"
    power_min = learned.power_min_w if learned and learned.power_min_w is not None else settings.power_min_w
    power_max = max(settings.power_max_w, learned.power_max_w) if learned and learned.power_max_w is not None else settings.power_max_w
    sources["power"] = "learned" if learned and learned.power_min_w is not None else "start"
    return Values(
        steady_swing=swing, start_jump=jumps, start_settle_s=settles,
        power_min_w=power_min, power_max_w=max(power_max, power_min), sources=sources,
    )
