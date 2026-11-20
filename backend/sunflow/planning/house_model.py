"""How the house cools down and warms up, learned from its room temperatures (a 1R1C grey-box model).

Per hour the weighted mean room temperature changes by

    ΔT = a·(T_out - T) + b·Q + c·S + d

a: the share of the difference to outside lost per hour, the time constant is 1/a;
b: kelvin per kWh of heat from the heat pump, the house's heat capacity is 1/b (kWh/K)
   and its heat loss a/b (kW/K);
c: kelvin per hour at full sun (S from 0 to 1, from the irradiance learning source);
d: internal gains (people, devices) and heat that is not measured, in kelvin per hour.

A weighted ridge regression on the recorder's hourly means; recent days weigh more. The last days
are held out and simulated freely, a day at a time: the model counts as useful only where it beats
"the temperature stays as it is".
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from statistics import median
from typing import Any

import numpy as np

from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.ha import HAClient
from sunflow.planning.load_forecast import fetch_hourly_means

HISTORY_DAYS = 28
MIN_DAYS = 7
HALF_LIFE_DAYS = 14
HOLDOUT_DAYS = 2
RIDGE = 0.001
# Plausible time constants (h); single-family houses are mostly 30-150 h
TAU_RANGE_H = (5.0, 500.0)
# Without a heat measurement: COP of an air heat pump from the Carnot limit (flow 35 °C, 40 % of it)
CARNOT_SHARE = 0.4
FLOW_C = 35.0
# A room whose hourly changes stray this much more than the others' (sun, radiator, window)
RESTLESS_FACTOR = 2.5
RESTLESS_MIN_K = 0.3
HOUR = timedelta(hours=1)


@dataclass(frozen=True, kw_only=True)
class HourRow:
    """One hour: mean room and outside temperature, heat from the heat pump (kWh), sun (0-1)."""

    start: datetime
    inside: float
    outside: float
    heat: float | None = None
    sun: float | None = None


@dataclass(frozen=True, kw_only=True)
class HouseModel:
    """The fitted coefficients and how well they did on the held out days."""

    a: float
    b: float | None
    c: float | None
    d: float
    days: int
    samples: int
    # Mean error (K) of a day's free simulation on the held out days, and of "it stays as it is"
    error_k: float | None
    baseline_k: float | None

    @property
    def tau_h(self) -> float | None:
        """Time constant: hours until two thirds of the difference to outside are gone."""
        return 1 / self.a if self.a > 0 else None

    @property
    def capacity_kwh_per_k(self) -> float | None:
        """Heat that warms the whole house by one kelvin."""
        return 1 / self.b if self.b and self.b > 0 else None

    @property
    def loss_w_per_k(self) -> float | None:
        """Heat loss per kelvin to outside."""
        return self.a / self.b * 1000 if self.b and self.b > 0 and self.a > 0 else None

    @property
    def plausible(self) -> bool:
        """Cools towards outside at a believable rate; heat warms."""
        tau = self.tau_h
        return tau is not None and TAU_RANGE_H[0] <= tau <= TAU_RANGE_H[1] and (self.b is None or self.b > 0)

    @property
    def useful(self) -> bool:
        """Plausible and better than "the temperature stays" on unseen days."""
        return (
            self.plausible and self.error_k is not None and self.baseline_k is not None and self.error_k < self.baseline_k
        )

    def step(self, inside: float, row: HourRow) -> float:
        """The room temperature an hour later."""
        change = self.a * (row.outside - inside) + self.d
        if self.b is not None and row.heat is not None:
            change += self.b * row.heat
        if self.c is not None and row.sun is not None:
            change += self.c * row.sun
        return inside + change

    def cooling_k_per_h(self, inside: float, outside: float) -> float:
        """How fast the house cools without heat or sun (positive: cooling)."""
        return self.a * (inside - outside) - self.d


def _transitions(rows: list[HourRow]) -> list[tuple[HourRow, float]]:
    """Each hour with the room temperature of the next one, where both are there."""
    by_start = {r.start: r for r in rows}
    return [(r, nxt.inside) for r in rows if (nxt := by_start.get(r.start + HOUR)) is not None]


def _design(pairs: list[tuple[HourRow, float]], heat: bool, sun: bool) -> tuple[np.ndarray, np.ndarray]:
    columns = [[r.outside - r.inside for r, _ in pairs]]
    if heat:
        columns.append([r.heat or 0.0 for r, _ in pairs])
    if sun:
        columns.append([r.sun or 0.0 for r, _ in pairs])
    columns.append([1.0] * len(pairs))
    x = np.array(columns, dtype=float).T
    y = np.array([nxt - r.inside for r, nxt in pairs], dtype=float)
    return x, y


def _solve(pairs: list[tuple[HourRow, float]], heat: bool, sun: bool, now: datetime) -> list[float]:
    x, y = _design(pairs, heat, sun)
    age = np.array([(now - r.start).total_seconds() / 86400 for r, _ in pairs])
    weights = 0.5 ** (age / HALF_LIFE_DAYS)
    xw = x * weights[:, None]
    gram = x.T @ xw
    # Relative to each column's own size, so sun (0-1) is not held back more than temperatures
    # A tiny fixed part keeps a column of zeros (no heat for weeks) solvable; it then fits as 0
    penalty = RIDGE * np.diag(gram) + 1e-9 * weights.sum()
    # The constant (internal gains) stays free
    penalty[-1] = 0.0
    return list(np.linalg.solve(gram + np.diag(penalty), xw.T @ y))


def _model(coefficients: list[float], heat: bool, sun: bool, **extra: Any) -> HouseModel:
    values = iter(coefficients)
    a = float(next(values))
    b = float(next(values)) if heat else None
    c = float(next(values)) if sun else None
    return HouseModel(a=a, b=b, c=c, d=float(next(values)), **extra)


def simulate(model: HouseModel, rows: list[HourRow], anchor_hours: int = 24) -> list[float | None]:
    """The room temperature the model expects per hour, started from the measured one every anchor_hours.

    After a gap it starts again from the measurement; the anchor hours themselves are None.
    """
    result: list[float | None] = []
    inside: float | None = None
    previous: HourRow | None = None
    for i, row in enumerate(rows):
        restart = previous is None or row.start - previous.start != HOUR or i % anchor_hours == 0
        if restart or inside is None or previous is None:
            result.append(None)
            inside = row.inside
        else:
            inside = model.step(inside, previous)
            result.append(inside)
        previous = row
    return result


def _errors(model: HouseModel, rows: list[HourRow]) -> tuple[float | None, float | None]:
    """Root mean square error of the free simulation and of "it stays as it was at the anchor"."""
    predicted = simulate(model, rows)
    model_sq, stay_sq = [], []
    anchor = None
    for row, value in zip(rows, predicted, strict=True):
        if value is None:
            anchor = row.inside
            continue
        model_sq.append((value - row.inside) ** 2)
        stay_sq.append((anchor - row.inside) ** 2 if anchor is not None else 0.0)
    if not model_sq:
        return None, None
    return math.sqrt(sum(model_sq) / len(model_sq)), math.sqrt(sum(stay_sq) / len(stay_sq))


def fit(rows: list[HourRow], now: datetime) -> HouseModel | None:
    """Learn the house from hourly rows; None with fewer than MIN_DAYS days."""
    rows = sorted(rows, key=lambda r: r.start)
    pairs = _transitions(rows)
    heat = bool(pairs) and sum(r.heat is not None for r, _ in pairs) >= 0.8 * len(pairs)
    sun = bool(pairs) and sum(r.sun is not None for r, _ in pairs) >= 0.8 * len(pairs)
    pairs = [(r, nxt) for r, nxt in pairs if (not heat or r.heat is not None) and (not sun or r.sun is not None)]
    days = len({r.start.date() for r, _ in pairs})
    if days < MIN_DAYS:
        return None
    cutoff = now - timedelta(days=HOLDOUT_DAYS)
    train = [p for p in pairs if p[0].start < cutoff]
    held = [r for r in rows if r.start >= cutoff]
    error_k = baseline_k = None
    if train and held:
        trial = _model(_solve(train, heat, sun, cutoff), heat, sun, days=days, samples=len(train), error_k=None, baseline_k=None)
        error_k, baseline_k = _errors(trial, held)
    return _model(_solve(pairs, heat, sun, now), heat, sun, days=days, samples=len(pairs), error_k=error_k, baseline_k=baseline_k)


def carnot_cop(outside: float) -> float:
    """A rough COP of an air heat pump when nothing measures it."""
    flow = FLOW_C + 273.15
    lift = flow - (outside + 273.15)
    return min(max(CARNOT_SHARE * flow / lift, 1.0), 6.0) if lift > 0 else 6.0


def weighted_inside(per_room: dict[str, dict[datetime, float]], weights: dict[str, float]) -> dict[datetime, float]:
    """Weighted mean of the rooms per hour, from the rooms that have a value."""
    totals: dict[datetime, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for entity_id, values in per_room.items():
        for start, value in values.items():
            totals[start][0] += value * weights[entity_id]
            totals[start][1] += weights[entity_id]
    return {start: total / weight for start, (total, weight) in totals.items() if weight > 0}


def restless_rooms(per_room: dict[str, dict[datetime, float]], inside: dict[datetime, float]) -> list[str]:
    """Rooms whose hourly changes stray far more than the others' (sun on the sensor, a radiator, a window)."""
    spread = {}
    for entity_id, values in per_room.items():
        deviations = [
            (values[s + HOUR] - values[s]) - (inside[s + HOUR] - inside[s])
            for s in values
            if s + HOUR in values and s in inside and s + HOUR in inside
        ]
        if len(deviations) >= 48:
            spread[entity_id] = math.sqrt(sum(d * d for d in deviations) / len(deviations))
    if len(spread) < 3:
        return []
    typical = median(spread.values())
    return sorted(e for e, v in spread.items() if v > max(RESTLESS_FACTOR * typical, RESTLESS_MIN_K))


def heat_pump_device(config: SunflowConfig, profiles: dict[str, dict[str, Any]]) -> DeviceConfig | None:
    """The heat pump whose heat warms the house: the first device with the capability heat_pump."""
    return next(
        (d for d in config.devices if "heat_pump" in profiles.get(d.profile, {}).get("capabilities", [])), None
    )


def _role(device: DeviceConfig | None, role: str) -> str | None:
    override = device.role_overrides.get(role) if device else None
    return override.entity_id if override and override.entity_id else None


async def _hourly(client: HAClient, entity_id: str | None, now: datetime) -> dict[datetime, float]:
    if not entity_id:
        return {}
    return {start: value for start, value in await fetch_hourly_means(client, entity_id, now, days=HISTORY_DAYS) if math.isfinite(value)}


@dataclass(frozen=True, kw_only=True)
class HouseData:
    """What the model is learned from, per hour, and where the heat came from."""

    rows: list[HourRow]
    per_room: dict[str, dict[datetime, float]]
    inside: dict[datetime, float]
    # meter (the house's own heat sensor), measured, cop (electric x COP sensor), estimated (electric x Carnot) or None
    heat_source: str | None
    outside_entity: str | None
    sun_entity: str | None


async def house_data(
    client: HAClient, config: SunflowConfig, profiles: dict[str, dict[str, Any]], now: datetime
) -> HouseData:
    """The recorder's hourly means of rooms, outside, heat and sun.

    Heat: the house's own heat meter, else the heat pump's heat output, else estimated from its
    electric power; without either the model learns without heat.
    """
    house = config.house
    device = heat_pump_device(config, profiles)
    outside_entity = _role(device, "outside_temperature") or config.learning_sources.outdoor_temperature
    sun_entity = config.learning_sources.irradiance
    per_room = {room.entity_id: await _hourly(client, room.entity_id, now) for room in house.rooms}
    inside = weighted_inside(per_room, {room.entity_id: room.weight for room in house.rooms})
    outside = await _hourly(client, outside_entity, now)
    if house.heat_entity and (meter := await _hourly(client, house.heat_entity, now)):
        heat, heat_source = {s: max(w, 0.0) / 1000 for s, w in meter.items()}, "meter"
    else:
        heat, heat_source = await _heat(client, device, outside, now)
    sun = _relative(await _hourly(client, sun_entity, now))
    rows = [
        HourRow(start=start, inside=value, outside=outside[start], heat=heat.get(start) if heat_source else None,
                sun=sun.get(start) if sun else None)
        for start, value in sorted(inside.items())
        if start in outside
    ]
    return HouseData(
        rows=rows, per_room=per_room, inside=inside, heat_source=heat_source, outside_entity=outside_entity,
        sun_entity=sun_entity if sun else None,
    )


async def _heat(
    client: HAClient, device: DeviceConfig | None, outside: dict[datetime, float], now: datetime
) -> tuple[dict[datetime, float], str | None]:
    """Heat per hour in kWh: measured, else electric x COP (sensor, else Carnot estimate)."""
    if device is None:
        return {}, None
    if heat_power := await _hourly(client, _role(device, "heat_power"), now):
        return {s: max(w, 0.0) / 1000 for s, w in heat_power.items()}, "measured"
    electric = await _hourly(client, _role(device, "power"), now)
    if not electric:
        phases = [await _hourly(client, _role(device, f"power_{p}"), now) for p in ("l1", "l2", "l3")]
        phases = [p for p in phases if p]
        electric = {s: sum(p[s] for p in phases) for s in phases[0] if all(s in p for p in phases)} if phases else {}
    if not electric:
        return {}, None
    if cop := await _hourly(client, _role(device, "cop"), now):
        return {s: max(w, 0.0) / 1000 * cop[s] for s, w in electric.items() if s in cop}, "cop"
    return {s: max(w, 0.0) / 1000 * carnot_cop(outside[s]) for s, w in electric.items() if s in outside}, "estimated"


def _relative(values: dict[datetime, float]) -> dict[datetime, float]:
    """Sun from 0 to 1: lux and W/m² alike, against a bright hour of the period."""
    if not values:
        return {}
    ordered = sorted(values.values())
    bright = ordered[int(0.95 * (len(ordered) - 1))]
    if bright <= 0:
        return {}
    return {s: min(max(v / bright, 0.0), 1.0) for s, v in values.items()}


def chart(model: HouseModel | None, rows: list[HourRow], days: int = 7) -> list[dict[str, Any]]:
    """Measured and expected room temperature of the last days, the model started anew each day."""
    if not rows:
        return []
    since = rows[-1].start - timedelta(days=days)
    recent = [r for r in rows if r.start > since]
    predicted = simulate(model, recent) if model is not None else [None] * len(recent)
    return [
        {
            "start": r.start.astimezone(UTC).isoformat(),
            "end": (r.start + HOUR).astimezone(UTC).isoformat(),
            "measured": round(r.inside, 2),
            "model": round(p, 2) if p is not None else None,
            "outside": round(r.outside, 1),
        }
        for r, p in zip(recent, predicted, strict=True)
    ]
