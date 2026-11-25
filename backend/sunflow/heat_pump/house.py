"""The house model (design/heat_pump.md, 14.1): heating surface and house, learned from heat_pump_samples.

    Cs · dTs/dt = Q − Kf · (Ts − Tr)                   heating surface (screed, radiators)
    Cb · dTr/dt = Kf · (Ts − Tr) − U · (Tr − A) + G    house

Q heat of the heat pump into the heating surface (kW), A outside, U heat loss (kW/K), Kf surface → room (kW/K),
Cs, Cb heat storage (kWh/K), G internal gains (kW). Time in hours.

Every 5 minute row counts: Q is the measured heat while the heat pump heats (also after-run and heating that
is not valid), 0 for hot water, defrost (its heat comes from the hot water tank), standby and anything else.
A gap of more than GAP starts a new section at the measured room temperature, the surface a learned offset
above it. The model runs freely through each section; its hourly means of the room are fitted to the measured
ones (rows with a fast change give no value), weighted by the rows of the hour and their age.

For the preview of the heating curve (16) the heat comes from the water: Q = Kw · share · (Tw − Ts).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import math
from typing import Any

import numpy as np
from scipy.linalg import expm
from scipy.optimize import least_squares
from scipy.signal import lfilter

# One row of heat_pump_samples; more than GAP without a row starts a new section
STEP = timedelta(minutes=5)
STEP_H = STEP.total_seconds() / 3600
GAP = timedelta(minutes=15)
# Rows of the last FIT_DAYS, an hour counts half after HALF_LIFE_DAYS
FIT_DAYS = 28
HALF_LIFE_DAYS = 14
# Hours with a room temperature: shown from SHOW_HOURS on, usable from USABLE_HOURS on (14.1, 14.2)
SHOW_HOURS = 24
USABLE_HOURS = 48
# Test: fitted without the last TEST_HOURS, these predicted freely
TEST_HOURS = 24
# Days of the compressor share used for the preview; days of the chart; hours of the past in the preview
SHARE_DAYS = 7
CHART_DAYS = 7
PAST_HOURS = 48
# Bounds of the fitted values: U (kW/K), Kf (kW/K), Cs (kWh/K), Cb (kWh/K), G (kW), surface offset at a
# section's start (K). A value at its bound makes the model implausible.
NAMES = ("u", "kf", "cs", "cb", "g", "offset")
LOW = np.array([0.01, 0.05, 0.2, 1.0, -5.0, -5.0])
HIGH = np.array([2.0, 20.0, 200.0, 500.0, 5.0, 20.0])
# U, Kf, Cs, Cb are fitted as logarithms (all positive, over orders of magnitude)
LOGS = 4
# Share of the fitted range (of the logarithm) closer than which a value counts as at its bound
AT_BOUND = 0.01
STARTS = [(u, cs, cb) for u in (0.1, 0.4) for cs in (2.0, 10.0) for cb in (10.0, 50.0)]


@dataclass(frozen=True, kw_only=True)
class Row:
    """One 5 minute step: heat into the surface (kW), outside, room temperature (None: no value) and, while the
    compressor heats, the water temperature with the compressor's share and the offset of the effective setpoint."""

    start: datetime
    heat_kw: float
    outside_c: float
    room_c: float | None
    share: float
    water_c: float | None
    offset_k: float | None
    heating: bool


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) else None


def _start(row: dict[str, Any]) -> datetime | None:
    start = row.get("start")
    try:
        start = datetime.fromisoformat(start) if isinstance(start, str) else start
    except ValueError:
        return None
    return start.astimezone(UTC) if isinstance(start, datetime) else None


def _room(row: dict[str, Any], weights: dict[str, float]) -> float | None:
    """Weighted mean of the configured rooms; None in a row with a fast change (6)."""
    if (row.get("flags") or {}).get("ventilation"):
        return None
    total = weight = 0.0
    for entity_id, value in (row.get("rooms") or {}).items():
        if entity_id in weights and (number := _num(value)) is not None:
            total += number * weights[entity_id]
            weight += weights[entity_id]
    return total / weight if weight > 0 else None


def has_heat(rows: Iterable[dict[str, Any]]) -> bool:
    """Whether the heat pump's heat is measured: a heating row holds a heat value."""
    return any(row.get("activity") == "heating" and _num(row.get("heat_w")) is not None for row in rows)


def sections(rows: Iterable[dict[str, Any]], weights: dict[str, float]) -> list[list[Row]]:
    """The rows as sections of consecutive 5 minute steps; a heating row without a heat value is missing.

    A missing row up to GAP repeats the inputs of the one before (without a room value); a longer gap starts a
    new section. A section starts with its first room temperature; one without any is left out."""
    parsed: list[tuple[datetime, dict[str, Any]]] = sorted(
        ((start, row) for row in rows if (start := _start(row)) is not None), key=lambda item: item[0]
    )
    result: list[list[Row]] = []
    current: list[Row] = []
    outside: float | None = None
    previous: datetime | None = None

    def close() -> None:
        first = next((i for i, r in enumerate(current) if r.room_c is not None), None)
        if first is not None and len(current) - first >= 2:
            result.append(current[first:])

    for start, row in parsed:
        heating = row.get("activity") == "heating"
        heat_w = _num(row.get("heat_w"))
        if heating and heat_w is None:
            continue
        if previous is not None and start - previous > GAP:
            close()
            current, outside = [], None
        outside = _num(row.get("outside_c")) if _num(row.get("outside_c")) is not None else outside
        if outside is None:
            continue
        if current and previous is not None:
            for k in range(1, round((start - previous) / STEP)):
                current.append(Row(
                    start=previous + k * STEP, heat_kw=current[-1].heat_kw, outside_c=current[-1].outside_c, room_c=None,
                    share=current[-1].share, water_c=current[-1].water_c, offset_k=None, heating=current[-1].heating,
                ))
        compressor = _num(row.get("compressor")) or 0.0
        tv, tr, setpoint = _num(row.get("tv_c")), _num(row.get("tr_c")), _num(row.get("setpoint_c"))
        water = (tv + tr) / 2 if heating and compressor >= 0.5 and tv is not None and tr is not None else None
        current.append(Row(
            start=start,
            heat_kw=heat_w / 1000 if heating and heat_w is not None else 0.0,
            outside_c=outside,
            room_c=_room(row, weights),
            share=compressor if heating else 0.0,
            water_c=water,
            offset_k=setpoint - water if water is not None and setpoint is not None and compressor >= 0.99 else None,
            heating=heating,
        ))
        previous = start
    close()
    return result


@dataclass(frozen=True)
class Params:
    """U, Kf (kW/K), Cs, Cb (kWh/K), G (kW)."""

    u: float
    kf: float
    cs: float
    cb: float
    g: float

    def matrix(self, kw_share: float = 0.0) -> np.ndarray:
        """System matrix of (surface, room); kw_share adds the water's conductance to the surface (preview)."""
        return np.array([
            [-(self.kf + kw_share) / self.cs, self.kf / self.cs],
            [self.kf / self.cb, -(self.kf + self.u) / self.cb],
        ])

    @property
    def tau_surface_h(self) -> float:
        return self.cs / self.kf

    @property
    def tau_house_h(self) -> float:
        return self.cb / self.u


def simulate(params: Params, section: list[Row], surface0: float, room0: float) -> tuple[np.ndarray, np.ndarray]:
    """States (surface, room) at the start of every row, and after the last one; exact for inputs held over a step.

    The two stores are a passive network: the system matrix has two real negative eigenvalues. In the
    eigenbasis every mode is a first-order recursion, filtered at once."""
    a = params.matrix()
    mu, vectors = np.linalg.eig(a)
    mu, vectors = np.real(mu), np.real(vectors)
    inverse = np.linalg.inv(vectors)
    heat = np.fromiter((r.heat_kw for r in section), float, len(section))
    outside = np.fromiter((r.outside_c for r in section), float, len(section))
    inputs = np.vstack([heat / params.cs, (params.u * outside + params.g) / params.cb])
    modal_inputs = inverse @ inputs
    w0 = inverse @ np.array([surface0, room0])
    modes = []
    for i in range(2):
        lam = math.exp(mu[i] * STEP_H)
        gain = (lam - 1) / mu[i] if mu[i] != 0 else STEP_H
        after, _ = lfilter([gain], [1.0, -lam], modal_inputs[i], zi=[lam * w0[i]])
        modes.append(np.concatenate([[w0[i]], after]))
    states = vectors @ np.vstack(modes)
    return states[:, :-1].T, states[:, -1]


@dataclass(kw_only=True)
class Hours:
    """The measured hourly means of the room per section, as indices into the section's rows."""

    index: list[np.ndarray]
    hour: list[np.ndarray]
    measured: np.ndarray
    weight: np.ndarray
    starts: list[datetime]


def hours_of(sections_: list[list[Row]], now: datetime) -> Hours:
    """Per section the rows with a room value and their hour number (counted over all sections); per hour the
    measured mean and its weight: share of the hour's rows (of 12) and its age (half after HALF_LIFE_DAYS)."""
    index, hour, measured, weight, starts = [], [], [], [], []
    for section in sections_:
        rows, numbers = [], []
        groups: dict[datetime, list[float]] = {}
        first = len(measured)
        for i, row in enumerate(section):
            if row.room_c is None:
                continue
            key = row.start.replace(minute=0, second=0, microsecond=0)
            if key not in groups:
                groups[key] = []
                starts.append(key)
            groups[key].append(row.room_c)
            rows.append(i)
            numbers.append(first + len(groups) - 1)
        for key, members in groups.items():
            measured.append(sum(members) / len(members))
            age = max((now - key).total_seconds() / 86400, 0.0)
            weight.append(len(members) / 12 * 0.5 ** (age / HALF_LIFE_DAYS))
        index.append(np.array(rows, dtype=int))
        hour.append(np.array(numbers, dtype=int))
    return Hours(index=index, hour=hour, measured=np.array(measured), weight=np.array(weight), starts=starts)


def _params(x: np.ndarray) -> tuple[Params, float]:
    values = np.concatenate([np.exp(x[:LOGS]), x[LOGS:]])
    return Params(*values[:5]), float(values[5])


def _x(values: Iterable[float]) -> np.ndarray:
    v = np.array(list(values), dtype=float)
    return np.concatenate([np.log(v[:LOGS]), v[LOGS:]])


X_LOW, X_HIGH = _x(LOW), _x(HIGH)


def modelled(params: Params, offset: float, sections_: list[list[Row]], hours: Hours) -> tuple[np.ndarray, list[np.ndarray], list[np.ndarray]]:
    """The model's hourly means of the room (matching hours.measured), and per section the states at the rows."""
    sums = np.zeros(len(hours.measured))
    counts = np.zeros(len(hours.measured))
    surfaces, rooms = [], []
    for section, index, number in zip(sections_, hours.index, hours.hour, strict=True):
        room0 = section[0].room_c
        assert room0 is not None
        states, _ = simulate(params, section, room0 + offset, room0)
        surfaces.append(states[:, 0])
        rooms.append(states[:, 1])
        np.add.at(sums, number, states[index, 1])
        np.add.at(counts, number, 1)
    return sums / np.maximum(counts, 1), surfaces, rooms


@dataclass(kw_only=True)
class Fit:
    params: Params
    offset: float
    # Uncertainty (one standard deviation) per fitted value, by name
    spread: dict[str, float]
    at_bound: list[str]
    rms_k: float
    room_hours: int


def fit(sections_: list[list[Row]], now: datetime) -> Fit | None:
    """Weighted least squares of the hourly room means; None with fewer than SHOW_HOURS hours with a room value."""
    hours = hours_of(sections_, now)
    if len(hours.measured) < SHOW_HOURS:
        return None
    root = np.sqrt(hours.weight)

    def residual(x: np.ndarray) -> np.ndarray:
        params, offset = _params(x)
        model, _, _ = modelled(params, offset, sections_, hours)
        return (model - hours.measured) * root

    best = None
    for u, cs, cb in STARTS:
        x0 = np.clip(_x((u, 1.0, cs, cb, 0.0, 2.0)), X_LOW + 1e-6, X_HIGH - 1e-6)
        try:
            result = least_squares(residual, x0, bounds=(X_LOW, X_HIGH), max_nfev=200)
        except (ValueError, np.linalg.LinAlgError):
            continue
        if best is None or result.cost < best.cost:
            best = result
    if best is None:
        return None
    params, offset = _params(best.x)
    values = dict(zip(NAMES, (params.u, params.kf, params.cs, params.cb, params.g, offset), strict=True))
    free = max(len(hours.measured) - len(best.x), 1)
    variance = 2 * best.cost / free
    jac = best.jac
    covariance = np.linalg.pinv(jac.T @ jac) * variance
    sigma = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    spread = {
        name: float(values[name] * sigma[i]) if i < LOGS else float(sigma[i]) for i, name in enumerate(NAMES)
    }
    span = X_HIGH - X_LOW
    at_bound = [
        name for i, name in enumerate(NAMES) if best.x[i] - X_LOW[i] < AT_BOUND * span[i] or X_HIGH[i] - best.x[i] < AT_BOUND * span[i]
    ]
    model, _, _ = modelled(params, offset, sections_, hours)
    rms = float(np.sqrt(np.average((model - hours.measured) ** 2, weights=hours.weight)))
    return Fit(params=params, offset=offset, spread=spread, at_bound=at_bound, rms_k=rms, room_hours=len(hours.measured))


def test(sections_: list[list[Row]], now: datetime) -> tuple[float | None, float | None]:
    """Fitted without the last TEST_HOURS, these predicted freely: rms of the model and of "the temperature
    stays" over their hours. A section running on over the cut starts there with the measured room (else the
    last before) and the surface as the model left it; a later section starts as in the fit."""
    cutoff = now - timedelta(hours=TEST_HOURS)
    head = [before for s in sections_ if len(before := [r for r in s if r.start < cutoff]) >= 2]
    result = fit(head, cutoff) if head else None
    if result is None:
        return None, None
    model_sq, stay_sq = [], []
    for whole in sections_:
        before = [r for r in whole if r.start < cutoff]
        section = [r for r in whole if r.start >= cutoff]
        if not section or not any(r.room_c is not None for r in section):
            continue
        if before:
            # Sections start with a room value: the part before the cut as well
            start_room = before[0].room_c
            assert start_room is not None
            _, end = simulate(result.params, before, start_room + result.offset, start_room)
            surface0 = float(end[0])
            known = section[0].room_c if section[0].room_c is not None else next(r.room_c for r in reversed(before) if r.room_c is not None)
            assert known is not None
            room0 = known
        else:
            first_room = section[0].room_c
            assert first_room is not None
            room0, surface0 = first_room, first_room + result.offset
        states, _ = simulate(result.params, section, surface0, room0)
        groups: dict[datetime, list[tuple[float, float]]] = {}
        for row, state in zip(section, states, strict=True):
            if row.room_c is not None:
                groups.setdefault(row.start.replace(minute=0, second=0, microsecond=0), []).append((row.room_c, float(state[1])))
        for members in groups.values():
            measured = sum(m for m, _ in members) / len(members)
            model_sq.append((sum(s for _, s in members) / len(members) - measured) ** 2)
            stay_sq.append((room0 - measured) ** 2)
    if not model_sq:
        return None, None
    return math.sqrt(sum(model_sq) / len(model_sq)), math.sqrt(sum(stay_sq) / len(stay_sq))


def transfer(params: Params, offset: float, sections_: list[list[Row]], now: datetime) -> float | None:
    """Kw: heat per kelvin water above the surface at full compressor share, from the heating rows (weighted by
    age); None without such rows."""
    num = den = 0.0
    for section in sections_:
        room0 = section[0].room_c
        assert room0 is not None
        states, _ = simulate(params, section, room0 + offset, room0)
        for row, state in zip(section, states, strict=True):
            if not row.heating or row.water_c is None or row.share <= 0:
                continue
            x = row.share * (row.water_c - float(state[0]))
            weight = 0.5 ** (max((now - row.start).total_seconds() / 86400, 0.0) / HALF_LIFE_DAYS)
            num += weight * row.heat_kw * x
            den += weight * x * x
    kw = num / den if den > 0 else None
    return kw if kw is not None and kw > 0 else None


@dataclass(kw_only=True)
class HouseModel:
    """The learned house with what the planning, the preview and the display need."""

    fit: Fit
    kw: float | None
    test_rms_k: float | None
    stay_rms_k: float | None
    computed_at: datetime
    # State at the end of the data: surface from the model, room as last measured
    surface_c: float
    room_c: float
    outside_c: float
    data_end: datetime
    # Hours with a room temperature, with heating, the outside range, the first row
    heating_hours: float
    outside_min_c: float
    outside_max_c: float
    data_start: datetime
    # For the preview: mean compressor share of the last SHARE_DAYS, effective setpoint − water
    share: float
    offset_k: float
    # Measured and modelled room per hour of the last CHART_DAYS
    chart: list[dict[str, Any]] = field(default_factory=list)

    @property
    def params(self) -> Params:
        return self.fit.params

    @property
    def plausible(self) -> bool:
        return not self.fit.at_bound

    @property
    def usable(self) -> bool:
        """Automatic use by the planning (14.2): enough hours, plausible and better than "it stays" in the test."""
        return (
            self.fit.room_hours >= USABLE_HOURS and self.plausible and self.test_rms_k is not None
            and self.stay_rms_k is not None and self.test_rms_k < self.stay_rms_k
        )

    def reason(self) -> str | None:
        """Why the model is not usable automatically; None when it is."""
        if self.fit.room_hours < USABLE_HOURS:
            return "few_hours"
        if not self.plausible:
            return "implausible"
        if self.test_rms_k is None or self.stay_rms_k is None or self.test_rms_k >= self.stay_rms_k:
            return "test"
        return None

    def heat_to_hold(self, room_c: float, outside_c: float) -> float:
        """Heat (kW) that holds the room against the losses in equilibrium."""
        return max(self.params.u * (room_c - outside_c) - self.params.g, 0.0)

    def _conductance(self) -> float | None:
        """k = 1/Kf + 1/(Kw·share): kelvin water above the room per kW in equilibrium."""
        if self.kw is None:
            return None
        return 1 / self.params.kf + 1 / (self.kw * max(self.share, 0.05))

    @property
    def slope(self) -> float | None:
        """Slope of the heating curve that holds the house on its setpoint in equilibrium."""
        k = self._conductance()
        return self.params.u * k if k is not None else None

    @property
    def shift_k(self) -> float | None:
        k = self._conductance()
        return self.offset_k - self.params.g * k if k is not None else None


def build(rows: list[dict[str, Any]], weights: dict[str, float], now: datetime) -> HouseModel | None:
    """Fit, test and the state at the end of the data; None with too few hours with a room temperature."""
    sections_ = sections(rows, weights)
    result = fit(sections_, now)
    if result is None:
        return None
    test_rms, stay_rms = test(sections_, now)
    hours = hours_of(sections_, now)
    model, _, _ = modelled(result.params, result.offset, sections_, hours)
    last = sections_[-1]
    room0 = last[0].room_c
    assert room0 is not None
    _, end = simulate(result.params, last, room0 + result.offset, room0)
    measured_room = next(r.room_c for r in reversed(last) if r.room_c is not None)
    assert measured_room is not None
    all_rows = [r for s in sections_ for r in s]
    recent = [r.share for r in all_rows if r.start >= now - timedelta(days=SHARE_DAYS)]
    offsets = [r.offset_k for r in all_rows if r.offset_k is not None]
    since = now - timedelta(days=CHART_DAYS)
    chart = [
        {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
         "measured": round(float(m), 2), "model": round(float(p), 2)}
        for start, m, p in zip(hours.starts, hours.measured, model, strict=True)
        if start >= since
    ]
    return HouseModel(
        fit=result,
        kw=transfer(result.params, result.offset, sections_, now),
        test_rms_k=test_rms,
        stay_rms_k=stay_rms,
        computed_at=now,
        surface_c=float(end[0]),
        room_c=measured_room,
        outside_c=last[-1].outside_c,
        data_end=last[-1].start + STEP,
        heating_hours=sum(1 for r in all_rows if r.heating and r.heat_kw > 0) * STEP_H,
        outside_min_c=min(r.outside_c for r in all_rows),
        outside_max_c=max(r.outside_c for r in all_rows),
        data_start=sections_[0][0].start,
        share=sum(recent) / len(recent) if recent else 1.0,
        offset_k=float(np.median(offsets)) if offsets else 1.0,
        chart=chart,
    )


@dataclass(kw_only=True)
class Learning:
    """What was learned about the house and why there is no model yet.

    state: ok (model), learning (fewer than SHOW_HOURS hours with a room temperature), no_heat (the heat pump's
    heat is not measured), no_rooms, no_heat_pump."""

    state: str
    model: HouseModel | None = None
    room_hours: int = 0
    computed_at: datetime | None = None


def learn(rows: list[dict[str, Any]], weights: dict[str, float], now: datetime) -> Learning:
    """The house model from the rows of the last FIT_DAYS (14.1)."""
    if any(row.get("activity") == "heating" for row in rows) and not has_heat(rows):
        return Learning(state="no_heat", computed_at=now)
    sections_ = sections(rows, weights)
    room_hours = len(hours_of(sections_, now).measured)
    model = build(rows, weights, now) if room_hours >= SHOW_HOURS else None
    if model is None:
        return Learning(state="learning", room_hours=room_hours, computed_at=now)
    return Learning(state="ok", model=model, room_hours=room_hours, computed_at=now)


# Preview of the heating curve (16)


def step_with_water(params: Params, kw_share: float, state: np.ndarray, water_c: float, outside_c: float, hours: float = 1.0) -> np.ndarray:
    """The state after `hours` with the water at water_c heating the surface (Q = Kw · share · (Tw − Ts))."""
    a = params.matrix(kw_share)
    b = np.array([kw_share * water_c / params.cs, (params.u * outside_c + params.g) / params.cb])
    steady = -np.linalg.solve(a, b)
    return steady + expm(a * hours) @ (state - steady)


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
    model: HouseModel, start: datetime, outside: list[float], setpoint_c: float, slope: float, shift_k: float,
    limits: tuple[float, float],
) -> dict[str, Any]:
    """The room hour by hour with the curve R + S·(R − A) + P (within the limits of the set value)."""
    assert model.kw is not None
    kw_share = model.kw * model.share
    state = np.array([model.surface_c, model.room_c])
    flows, rooms = [], []
    for a in outside:
        flow = min(max(setpoint_c + slope * (setpoint_c - a) + shift_k, limits[0]), limits[1])
        state = step_with_water(model.params, kw_share, state, flow - model.offset_k, a)
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
