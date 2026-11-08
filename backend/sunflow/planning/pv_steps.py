"""Soiling of one plane (snow, leaves, dirt) and its cleaning while the other planes deliver as before.

Against the other planes a day's weather and the forecast's error cancel out; what is left is the plane itself.
A sudden drop (snow) or, after a cleaning, a slow slide below the clean share (leaves gathering) is soiled:
those days are not learned, and the plane is planned at its current share of the clean days until it recovers.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
import math
from typing import Any

from sunflow.planning.pv_learning import MIN_FORECAST_W

# Days a regime needs before a change, the days confirming it, and the days before it it is compared with
STEP_MIN_BEFORE = 3
STEP_CONFIRM_DAYS = 3
STEP_MIN_AFTER = 2
STEP_REFERENCE_DAYS = 14
# Pooled change of the plane against the others, and of each confirming day
STEP_RATIO = 1.4
STEP_DAY_RATIO = 1.25
# After a cleaning: its first days are the clean share, three days below this part of it are soiled
CLEAN_DAYS = 7
GRADUAL_DAYS = 3
GRADUAL_LEVEL = 0.8
# Forecast energy (Wh) of the plane and of the others for a day to count
STEP_MIN_DAY_WH = 1000.0

Day = tuple[float, float, float, float]


@dataclass(kw_only=True)
class PlaneState:
    """Changes of a plane, the soiled days not to learn from and its current share of the clean days."""

    changes: list[dict[str, Any]] = field(default_factory=list)
    # Soiled periods as local days, the end exclusive (None: up to now)
    soiled: list[tuple[date, date | None]] = field(default_factory=list)
    level: float = 1.0

    def is_soiled(self, day: date) -> bool:
        """The local day lies in a soiled period."""
        return any(start <= day and (end is None or day < end) for start, end in self.soiled)

    def as_dict(self) -> dict[str, Any]:
        """For the API."""
        return {
            "changes": self.changes,
            "soiled": [{"from": a.isoformat(), "to": b.isoformat() if b else None} for a, b in self.soiled],
            "level": round(self.level, 3),
        }


def daily_energy(
    rows: list[dict[str, Any]], planes: dict[str, list[str]], tz: tzinfo
) -> dict[date, dict[str, tuple[float, float]]]:
    """Measured and raw forecast energy (Wh) per local day and plane, from 15-minute shadow rows."""
    days: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for row in rows:
        forecasts = row.get("plane_forecasts") or {}
        measured = row.get("actual_strings") or {}
        day = datetime.fromisoformat(row["start"]).astimezone(tz).date()
        for key, strings in planes.items():
            forecast = forecasts.get(key)
            if not strings or forecast is None or forecast < MIN_FORECAST_W or not all(s in measured for s in strings):
                continue
            total = days[day][key]
            total[0] += max(sum(measured[s] for s in strings), 0.0) / 4
            total[1] += forecast / 4
    return {day: {key: (a, f) for key, (a, f) in values.items()} for day, values in days.items()}


def _relative(day: dict[str, tuple[float, float]], key: str) -> Day | None:
    """The plane's and the other planes' measured and forecast energy of a day, when both are large enough."""
    if key not in day or day[key][1] < STEP_MIN_DAY_WH:
        return None
    others = [v for k, v in day.items() if k != key]
    other_actual, other_forecast = sum(a for a, _ in others), sum(f for _, f in others)
    if other_forecast < STEP_MIN_DAY_WH or other_actual <= 0:
        return None
    return (*day[key], other_actual, other_forecast)


def _pooled(days: list[Day]) -> float:
    """The plane's share of its forecast against the other planes' share."""
    actual, forecast, other_actual, other_forecast = (sum(d[i] for d in days) for i in range(4))
    return max((actual / forecast) / (other_actual / other_forecast), 1e-6)


def _own(days: list[Day], index: int) -> float:
    """Measured share of the forecast of the plane (index 0) or of the others (index 2)."""
    return max(sum(d[index] for d in days), 1e-6) / sum(d[index + 1] for d in days)


def _change(days: list[Day], start: int, split: int, pair: bool) -> float | None:
    """The change at split against the regime since start, when the confirming days all agree.

    pair: only one other plane, which then shows the same change the other way round; the plane whose own
    share of its forecast moved more is the one that changed.
    """
    reference = _pooled(days[max(start, split - STEP_REFERENCE_DAYS) : split])
    confirm = days[split : split + STEP_CONFIRM_DAYS]
    if len(confirm) < STEP_MIN_AFTER:
        return None
    change = _pooled(confirm) / reference
    sign = 1 if change > 1 else -1
    if sign * math.log(change) < math.log(STEP_RATIO):
        return None
    # One bright day is no change
    if any(sign * math.log(_pooled([d]) / reference) < math.log(STEP_DAY_RATIO) for d in confirm):
        return None
    before = days[max(start, split - STEP_REFERENCE_DAYS) : split]
    if pair and abs(math.log(_own(confirm, 0) / _own(before, 0))) < abs(math.log(_own(confirm, 2) / _own(before, 2))):
        return None
    return change


def plane_state(energy: dict[date, dict[str, tuple[float, float]]], key: str, pair: bool = False) -> PlaneState:
    """The plane's changes from the oldest day on, which periods were soiled and its share now."""
    days = [(day, values) for day in sorted(energy) if (values := _relative(energy[day], key)) is not None]
    values = [v for _, v in days]
    starts = [(0, None)]
    split = STEP_MIN_BEFORE
    while split <= len(days) - STEP_MIN_AFTER:
        start = starts[-1][0]
        if (change := _change(values, start, split, pair)) is None:
            split += 1
            continue
        # The strongest day of the change among the confirming ones (the day itself is often in between)
        best = split
        for later in range(split + 1, min(split + STEP_CONFIRM_DAYS, len(days) - STEP_MIN_AFTER + 1)):
            other = _change(values, start, later, pair)
            if other is not None and (other > 1) == (change > 1) and abs(math.log(other)) > abs(math.log(change)):
                best, change = later, other
        starts.append((best, change))
        split = best + STEP_MIN_BEFORE
    state = PlaneState(changes=[{"since": days[i][0].isoformat(), "change": round(c, 2)} for i, c in starts[1:]])
    if len(starts) == 1:
        return state
    bounds = [i for i, _ in starts] + [len(days)]
    soiled = [False] * len(days)
    reference = None
    level = 1.0
    for index, (begin, change) in enumerate(starts):
        end = bounds[index + 1]
        if change is not None and change < 1:
            # A drop: soiled until the next rise, at its share of the clean days before
            soiled[begin:end] = [True] * (end - begin)
            level = _pooled(values[begin:end]) / reference if reference else 1.0
            continue
        if change is None and starts[1][1] > 1:
            # Before the first cleaning: soiled since the data began
            soiled[begin:end] = [True] * (end - begin)
            continue
        if change is None:
            reference = _pooled(values[max(begin, end - STEP_REFERENCE_DAYS) : end])
            level = 1.0
            continue
        # After a cleaning its first days are the clean share; leaves then gather slowly
        reference = _pooled(values[begin : min(end, begin + CLEAN_DAYS)])
        level = 1.0
        for day in range(begin + GRADUAL_DAYS, end + 1):
            share = _pooled(values[day - GRADUAL_DAYS : day]) / reference
            if share < GRADUAL_LEVEL:
                soiled[day - GRADUAL_DAYS : day] = [True] * GRADUAL_DAYS
            if day == end:
                level = share if share < GRADUAL_LEVEL else 1.0
    state.level = min(level, 1.0)
    start = None
    for index, flag in enumerate([*soiled, False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            state.soiled.append((days[start][0], days[index][0] if index < len(days) else None))
            start = None
    return state


def plane_states(rows: list[dict[str, Any]], planes: dict[str, list[str]], tz: tzinfo) -> dict[str, PlaneState]:
    """The state of every plane with a change; needs at least two measured planes."""
    measured = {key: strings for key, strings in planes.items() if strings}
    if len(measured) < 2:
        return {}
    energy = daily_energy(rows, measured, tz)
    return {key: state for key in measured if (state := plane_state(energy, key, len(measured) == 2)).changes}
