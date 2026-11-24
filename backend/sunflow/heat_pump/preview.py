"""Preview of the heating curve (design/heat_pump.md, 16 "Vorschau der Heizkurve").

A house with two stores, fitted to the hourly means of heat_pump_samples, simulates how the room would
develop with a heating curve. Display only: nothing here acts on the control or the learning.

Model (heat flows relative to the heating surface's conductance ks = 1):
    cs dTs/dt = kw · share · (Tw − Ts) − kf · (Ts − Tr)      heating surface (screed, radiators)
    cb dTr/dt = kf · (Ts − Tr) − u · (Tr − A) + g             house
with the water temperature Tw (mean of flow and return while heating), share the compressor's share of the
hour in heating, A outside. kf = SURFACE_SPLIT, kw such that kw and kf in series give ks = 1; cs = tau_s ·
(kw + kf), cb = tau_b · u. Fitted: u, tau_s, tau_b, g and the surface temperature at the start.
In equilibrium, heating the share s of the time: k · (Tw − Tr) = u · (Tr − A) − g with k = 1 / (1 / (kw·s) +
1 / kf). The model's own heating curve thus has the slope u / k and the shift d − g / k (d: effective setpoint −
water temperature of the data).
"""

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from scipy.linalg import expm
from scipy.optimize import least_squares

# Days of samples the model is fitted to; hours with a room temperature it needs at least
FIT_DAYS = 14
MIN_ROOM_HOURS = 24
# Test of the prediction: fitted without the last hours, then these predicted (needs twice as many)
TEST_HOURS = 24
# Days of the compressor share used for the prediction; hours of the past shown
SHARE_DAYS = 7
PAST_HOURS = 48
DAYS_MAX = 7
# Surface → room conductance relative to ks (fixed split, 16)
SURFACE_SPLIT = 2.5
KF = SURFACE_SPLIT
KW = 1 / (1 - 1 / SURFACE_SPLIT)
# Bounds of the fitted parameters: u, tau_s (h), tau_b (h), g (K), surface temperature at the start (°C)
BOUNDS = ((0.02, 0.1, 2.0, -15.0, 10.0), (3.0, 24.0, 400.0, 15.0, 60.0))
STARTS = [(u, ts, tb) for u in (0.3, 1.0) for ts in (2.0, 8.0) for tb in (30.0, 150.0)]
# Compressor share rounded to this in the model (few distinct step matrices)
SHARE_STEP = 0.05


@dataclass(frozen=True, kw_only=True)
class Hour:
    """Hourly means of one heat pump (inputs of the model and the measured room temperature)."""

    start: datetime
    water_c: float | None
    share: float
    outside_c: float | None
    room_c: float | None
    # Effective setpoint − water temperature while heating (for the prediction)
    offset_k: float | None


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) else None


def _room(row: dict[str, Any], weights: dict[str, float]) -> float | None:
    """Weighted mean of the configured rooms; None in a row with a fast change (no consistent mean then)."""
    if (row.get("flags") or {}).get("ventilation"):
        return None
    total = weight = 0.0
    for entity_id, value in (row.get("rooms") or {}).items():
        if entity_id in weights and (number := _num(value)) is not None:
            total += number * weights[entity_id]
            weight += weights[entity_id]
    return total / weight if weight > 0 else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def hourly(rows: Iterable[dict[str, Any]], weights: dict[str, float]) -> list[Hour]:
    """The 5 minute rows as consecutive hours (UTC); an hour without rows has no values (a gap)."""
    groups: dict[datetime, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            start = datetime.fromisoformat(row["start"]).astimezone(UTC)
        except (KeyError, TypeError, ValueError):
            continue
        groups.setdefault(start.replace(minute=0, second=0, microsecond=0), []).append(row)
    if not groups:
        return []
    hours = []
    first, last = min(groups), max(groups)
    at = first
    while at <= last:
        group = groups.get(at, [])
        water, offsets, shares, outside, rooms = [], [], [], [], []
        for row in group:
            heating = row.get("activity") == "heating"
            compressor = _num(row.get("compressor")) or 0.0
            shares.append(compressor if heating else 0.0)
            tv, tr = _num(row.get("tv_c")), _num(row.get("tr_c"))
            if heating and compressor >= 0.5 and tv is not None and tr is not None:
                water.append((tv + tr) / 2)
                if (setpoint := _num(row.get("setpoint_c"))) is not None and compressor >= 0.99:
                    offsets.append(setpoint - (tv + tr) / 2)
            if (value := _num(row.get("outside_c"))) is not None:
                outside.append(value)
            if (value := _room(row, weights)) is not None:
                rooms.append(value)
        hours.append(Hour(
            start=at, water_c=_mean(water), share=_mean(shares) or 0.0, outside_c=_mean(outside), room_c=_mean(rooms),
            offset_k=_mean(offsets),
        ))
        at += timedelta(hours=1)
    return hours


@dataclass(frozen=True)
class Params:
    u: float
    tau_s: float
    tau_b: float
    g: float


class Plant:
    """The two stores, stepped one hour at a time with constant inputs (exact for a linear system)."""

    def __init__(self, params: Params) -> None:
        self.p = params
        self.cs = params.tau_s * (KW + KF)
        self.cb = params.tau_b * params.u
        # Per share (rounded to SHARE_STEP): the step matrix and the inverse of the system matrix
        self._steps: dict[float, tuple[np.ndarray, np.ndarray]] = {}

    def _matrices(self, share: float) -> tuple[np.ndarray, np.ndarray]:
        if share not in self._steps:
            a = np.array([
                [-(KW * share + KF) / self.cs, KF / self.cs],
                [KF / self.cb, -(KF + self.p.u) / self.cb],
            ])
            self._steps[share] = (expm(a), np.linalg.inv(a))
        return self._steps[share]

    def step(self, state: np.ndarray, water_c: float, share: float, outside_c: float) -> np.ndarray:
        """The state (surface, room) one hour later."""
        share = round(round(min(max(share, 0.0), 1.0) / SHARE_STEP) * SHARE_STEP, 3)
        step, inverse = self._matrices(share)
        b = np.array([KW * share * water_c / self.cs, (self.p.u * outside_c + self.p.g) / self.cb])
        steady = -inverse @ b
        return steady + step @ (state - steady)


def _inputs(hours: list[Hour]) -> tuple[list[float], list[float], list[float]]:
    """Water, share and outside per hour; a gap repeats the hour before (outside) or does not heat."""
    water, share, outside = [], [], []
    last_water = next((h.water_c for h in hours if h.water_c is not None), 30.0)
    last_out = next((h.outside_c for h in hours if h.outside_c is not None), 5.0)
    for hour in hours:
        last_water = hour.water_c if hour.water_c is not None else last_water
        last_out = hour.outside_c if hour.outside_c is not None else last_out
        water.append(last_water)
        share.append(hour.share if hour.water_c is not None else 0.0)
        outside.append(last_out)
    return water, share, outside


def replay(params: Params, hours: list[Hour], surface0: float, room0: float) -> list[tuple[float, float, float]]:
    """Per hour from the start state: surface and room at its end, and the room's mean over the hour (the
    middle of its start and end value, compared with the hourly mean of the measurement)."""
    plant = Plant(params)
    water, share, outside = _inputs(hours)
    state = np.array([surface0, room0])
    result = []
    for w, s, o in zip(water, share, outside, strict=True):
        before = float(state[1])
        state = plant.step(state, w, s, o)
        result.append((float(state[0]), float(state[1]), (before + float(state[1])) / 2))
    return result


@dataclass(kw_only=True)
class Fit:
    params: Params
    surface0: float
    room0: float
    rms_k: float
    room_hours: int


def fit(hours: list[Hour]) -> Fit | None:
    """Least squares of the room temperature (end of each hour vs. the next hour's mean is close enough for
    hourly means); None with too few hours."""
    rooms = [h.room_c for h in hours]
    measured = [i for i, r in enumerate(rooms) if r is not None]
    if len(measured) < MIN_ROOM_HOURS:
        return None
    room0 = rooms[measured[0]]
    hours = hours[measured[0]:]
    target = np.array([r if r is not None else np.nan for r in rooms[measured[0]:]])
    mask = ~np.isnan(target)
    assert room0 is not None

    def residual(x: np.ndarray) -> np.ndarray:
        simulated = np.array([mean for _, _, mean in replay(Params(*x[:4]), hours, float(x[4]), room0)])
        return (simulated - target)[mask]

    best = None
    water0 = next((h.water_c for h in hours if h.water_c is not None), room0 + 3)
    for u, tau_s, tau_b in STARTS:
        x0 = np.clip([u, tau_s, tau_b, 0.0, (room0 + water0) / 2], BOUNDS[0], BOUNDS[1])
        try:
            result = least_squares(residual, x0, bounds=BOUNDS, max_nfev=150)
        except (ValueError, np.linalg.LinAlgError):
            continue
        if best is None or result.cost < best.cost:
            best = result
    if best is None:
        return None
    x = best.x
    rms = float(np.sqrt(np.mean(residual(x) ** 2)))
    return Fit(params=Params(*x[:4]), surface0=float(x[4]), room0=room0, rms_k=rms, room_hours=int(mask.sum()))


@dataclass(kw_only=True)
class Model:
    """The fitted house with what the prediction needs."""

    fit: Fit
    hours: list[Hour]
    # Surface and room at the end of the data (room: last measured)
    surface_c: float
    room_c: float
    offset_k: float
    share: float
    test_rms_k: float | None
    computed_at: datetime
    past: list[dict[str, Any]] = field(default_factory=list)

    @property
    def _conductance(self) -> float:
        """Water → house in equilibrium, heating the share of the time (1 at share 1)."""
        share = max(self.share, SHARE_STEP)
        return 1 / (1 / (KW * share) + 1 / KF)

    @property
    def slope(self) -> float:
        return self.fit.params.u / self._conductance

    @property
    def shift_k(self) -> float:
        return self.offset_k - self.fit.params.g / self._conductance


def _test(hours: list[Hour]) -> float | None:
    """Fitted without the last TEST_HOURS, these predicted from their first measured room: the rms."""
    if len(hours) < 2 * TEST_HOURS:
        return None
    head, tail = hours[:-TEST_HOURS], hours[-TEST_HOURS:]
    model = fit(head)
    if model is None:
        return None
    states = replay(model.params, head[next(i for i, h in enumerate(head) if h.room_c is not None):], model.surface0, model.room0)
    surface = states[-1][0]
    room0 = next((h.room_c for h in reversed(head) if h.room_c is not None), None)
    if room0 is None:
        return None
    simulated = replay(model.params, tail, surface, room0)
    errors = [mean - h.room_c for (_, _, mean), h in zip(simulated, tail, strict=True) if h.room_c is not None]
    return float(np.sqrt(np.mean(np.square(errors)))) if errors else None


def build(hours: list[Hour], now: datetime) -> Model | None:
    """Fit, test and the state at the end of the data; None without enough data."""
    result = fit(hours)
    if result is None:
        return None
    first = next(i for i, h in enumerate(hours) if h.room_c is not None)
    used = hours[first:]
    states = replay(result.params, used, result.surface0, result.room0)
    offsets = [h.offset_k for h in used if h.offset_k is not None]
    recent = [h.share for h in used if h.start >= now - timedelta(days=SHARE_DAYS)]
    room = next((h.room_c for h in reversed(used) if h.room_c is not None), result.room0)
    past = [
        {"at": (h.start + timedelta(minutes=30)).isoformat(), "room_c": h.room_c, "model_c": round(state[2], 3)}
        for h, state in zip(used, states, strict=True)
        if h.start >= now - timedelta(hours=PAST_HOURS + 1)
    ]
    return Model(
        fit=result, hours=used, surface_c=states[-1][0], room_c=room,
        offset_k=float(np.median(offsets)) if offsets else 1.0,
        share=sum(recent) / len(recent) if recent else 1.0,
        test_rms_k=_test(used), computed_at=now, past=past,
    )


def crossing(start: datetime, rooms: list[float], room0: float, setpoint: float) -> datetime | None:
    """When the room reaches the setpoint from its side (linear between the hours); None if not."""
    below = room0 < setpoint
    previous = room0
    for i, room in enumerate(rooms):
        if (below and room >= setpoint) or (not below and room <= setpoint):
            share = (setpoint - previous) / (room - previous) if room != previous else 1.0
            return start + timedelta(hours=i + share)
        previous = room
    return None


def predict(
    model: Model, start: datetime, outside: list[float], setpoint_c: float, slope: float, shift_k: float,
    limits: tuple[float, float],
) -> dict[str, Any]:
    """The room hour by hour with the curve R + S·(R − A) + P (within the limits of the set value)."""
    plant = Plant(model.fit.params)
    state = np.array([model.surface_c, model.room_c])
    flows, rooms = [], []
    for a in outside:
        flow = min(max(setpoint_c + slope * (setpoint_c - a) + shift_k, limits[0]), limits[1])
        state = plant.step(state, flow - model.offset_k, model.share, a)
        flows.append(round(flow, 2))
        rooms.append(float(state[1]))
    reach = crossing(start, rooms, model.room_c, setpoint_c)
    return {
        "slope": slope, "shift_k": shift_k, "setpoint_c": setpoint_c,
        "flow_c": flows, "room_c": [round(r, 3) for r in rooms],
        "reach_at": reach.isoformat() if reach else None, "end_c": round(rooms[-1], 2) if rooms else None,
    }


def outside_hours(start: datetime, count: int, hourly: list[dict[str, Any]], daily: list[dict[str, Any]], measured: float) -> tuple[list[float], str]:
    """Outside per hour: the hourly forecast, then the daily one (mean of high and low), else the measured value."""
    by_hour: dict[datetime, float] = {}
    for item in hourly:
        try:
            at = datetime.fromisoformat(item["datetime"]).astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        except (KeyError, TypeError, ValueError):
            continue
        if (value := _num(item.get("temperature"))) is not None:
            by_hour[at] = value
    by_day: list[tuple[datetime, float]] = []
    for item in daily:
        try:
            at = datetime.fromisoformat(item["datetime"]).astimezone(UTC)
        except (KeyError, TypeError, ValueError):
            continue
        high, low = _num(item.get("temperature")), _num(item.get("templow"))
        if high is not None:
            by_day.append((at, (high + low) / 2 if low is not None else high))
    by_day.sort()
    last_hourly = max(by_hour, default=None)
    values, source = [], "measured"
    last = measured
    for i in range(count):
        at = start + timedelta(hours=i)
        if at in by_hour:
            last, source = by_hour[at], "forecast"
        elif last_hourly is None or at > last_hourly:
            day = next((v for d, v in by_day if d <= at < d + timedelta(days=1)), None)
            if day is not None:
                last, source = day, "forecast"
        values.append(last)
    return values, source


class PreviewCache:
    """The fitted model per heat pump, built anew at most once per max_age (also when it failed)."""

    def __init__(self, max_age: timedelta = timedelta(hours=1)) -> None:
        self.max_age = max_age
        self._models: dict[str, tuple[datetime, Model | None]] = {}

    def get(self, device_id: str, now: datetime, build_model: Callable[[], Model | None]) -> Model | None:
        cached = self._models.get(device_id)
        if cached is not None and timedelta(0) <= now - cached[0] < self.max_age:
            return cached[1]
        model = build_model()
        self._models[device_id] = (now, model)
        return model
