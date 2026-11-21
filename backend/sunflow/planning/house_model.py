"""How the house cools down and warms up, learned from its room temperatures (a 1R1C grey-box model).

Per hour the weighted mean room temperature changes by

    ΔT = a·(T_out - T) + b·Q + c·S + d

a: the share of the difference to outside lost per hour, the time constant is 1/a;
b: kelvin per kWh of heat from the heat pump, the house's heat capacity is 1/b (kWh/K)
   and its heat loss a/b (kW/K);
c: kelvin per hour at full sun (S from 0 to 1, from the irradiance learning source);
d: internal gains (people, devices) and heat that is not measured, in kelvin per hour.

A weighted ridge regression on hourly rows; recent days weigh more. The last days are held out and
simulated freely, a day at a time: the model counts as useful only where it beats "the temperature
stays as it is".

The hourly rows come from Home Assistant's recorder (hourly means, hours with hot water or defrost
left out) until heat_pump_samples covers at least MIN_DAYS days of valid heating (design/heat_pump.md,
9.3); then from the heat pump's 5 minute rows, without hours with hot water, defrost, after-run,
ventilation or heating that was not valid. The switch happens only when the model from the samples
is there and not worse in use than the one from Home Assistant, and it is kept (no jump back and forth).
"""

import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import math
from statistics import median
from typing import TYPE_CHECKING, Any

import numpy as np

from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.devices.profiles import manifest_id
from sunflow.devices.resolver import bind_roles, device_entities
from sunflow.ha import HAClient
from sunflow.heat_pump.curve import row_room
from sunflow.heat_pump.defaults import HOUSE_SAMPLES_DAY_MIN_S, HOUSE_SAMPLES_HOUR_MIN_ROWS
from sunflow.heat_pump.manifest import entities_by_key
from sunflow.heat_pump.states import canonical, value_key
from sunflow.planning.load_forecast import fetch_hourly_means

if TYPE_CHECKING:
    from sunflow.devices.discovery import Snapshot
    from sunflow.shadow.store import ShadowStore

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


def device_roles(
    device: DeviceConfig | None, profiles: dict[str, dict[str, Any]], snapshot: "Snapshot | None"
) -> dict[str, str | None]:
    """Role -> entity of the heat pump: the user's choice, else bound by the profile (through its manifest)."""
    if device is None:
        return {}
    roles: dict[str, str | None] = {}
    profile = profiles.get(device.profile)
    if snapshot is not None and profile is not None:
        manifests = snapshot.manifests.get(device.ha_device_id)
        roles = bind_roles(profile, device_entities(snapshot, device.ha_device_id), manifests)
    for role, override in device.role_overrides.items():
        if override.entity_id:
            roles[role] = override.entity_id
    return roles


def outside_entity(config: SunflowConfig, roles: dict[str, str | None]) -> str | None:
    """The outside temperature chosen for the house (house.outside_source); roles: role -> entity of the heat pump.

    auto: the own sensor (learning source) when set, else the heat pump's outside sensor.
    """
    own = config.learning_sources.outdoor_temperature
    match config.house.outside_source:
        case "learning_source":
            return own
        case "heat_pump":
            return roles.get("outside_temperature")
        case "heat_pump_unit":
            return roles.get("outside_temperature_unit")
    return own or roles.get("outside_temperature")


async def _hourly(client: HAClient, entity_id: str | None, now: datetime) -> dict[datetime, float]:
    if not entity_id:
        return {}
    return {start: value for start, value in await fetch_hourly_means(client, entity_id, now, days=HISTORY_DAYS) if math.isfinite(value)}


HA_HOURLY = "ha_hourly"
SAMPLES = "heat_pump_samples"


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
    # Hours left out because the heat pump made hot water or defrosted in them (from the samples also
    # after-run, ventilation and heating that was not valid)
    excluded_hours: int = 0
    # ha_hourly (Home Assistant's hourly means) or heat_pump_samples
    source: str = HA_HOURLY
    # Days of valid heating in heat_pump_samples (9.3), MIN_DAYS needed for the switch
    coverage_days: int = 0
    # Sun value of a bright hour: the model's sun (0-1) is relative to it
    sun_bright: float | None = None
    # Sun (0-1) of every hour the sensor's history has, by hour start: each hour learns with its own
    # hour's value, also hours that rows leave out here but the samples keep (9.3)
    sun_hours: dict[datetime, float] = field(default_factory=dict)


async def house_data(
    client: HAClient,
    config: SunflowConfig,
    profiles: dict[str, dict[str, Any]],
    now: datetime,
    snapshot: "Snapshot | None" = None,
) -> HouseData:
    """The recorder's hourly means of rooms, outside, heat and sun.

    Heat: the house's own heat meter, else the heat pump's heat output (role heat_power), else
    estimated from its electric power; without either the model learns without heat. Hours in
    which the heat pump made hot water or defrosted are left out, for every source: their heat
    did not go into the house. The snapshot resolves the heat pump's roles through its manifest.
    """
    house = config.house
    device = heat_pump_device(config, profiles)
    roles = device_roles(device, profiles, snapshot)
    outside_entity_id = outside_entity(config, roles)
    sun_entity = config.learning_sources.irradiance
    per_room = {room.entity_id: await _hourly(client, room.entity_id, now) for room in house.rooms}
    inside = weighted_inside(per_room, {room.entity_id: room.weight for room in house.rooms})
    outside = await _hourly(client, outside_entity_id, now)
    if house.heat_entity and (meter := await _hourly(client, house.heat_entity, now)):
        heat, heat_source = {s: max(w, 0.0) / 1000 for s, w in meter.items()}, "meter"
    else:
        heat, heat_source = await _heat(client, device, roles, outside, now)
    sun_raw = await _hourly(client, sun_entity, now)
    sun = _relative(sun_raw)
    busy = await _busy_hours(client, device, profiles, roles, snapshot, now)
    rows = [
        HourRow(start=start, inside=value, outside=outside[start], heat=heat.get(start) if heat_source else None,
                sun=sun.get(start) if sun else None)
        for start, value in sorted(inside.items())
        if start in outside and start not in busy
    ]
    return HouseData(
        rows=rows, per_room=per_room, inside=inside, heat_source=heat_source, outside_entity=outside_entity_id,
        sun_entity=sun_entity if sun else None,
        excluded_hours=sum(1 for start in inside if start in outside and start in busy),
        sun_bright=_bright(sun_raw) if sun else None,
        sun_hours=sun,
    )


# Activities whose heat does not go into the house
BUSY_ACTIVITIES = frozenset({"hot_water", "defrost"})


def busy_hours(
    rows: list[dict[str, Any]], key_of: Callable[[str], str | None], start: datetime, end: datetime
) -> set[datetime]:
    """Hour starts with any share of hot water or defrost, from minimal history rows ({"s", "lu"/"lc"}).

    key_of maps a shown state to the canonical activity. Hours before the first row are not known
    and stay in.
    """
    hours: set[datetime] = set()
    changes = [
        (max(datetime.fromtimestamp(row.get("lu") or row.get("lc") or 0, UTC), start), row.get("s")) for row in rows
    ]
    for (since, state), (until, _) in zip(changes, [*changes[1:], (end, None)], strict=True):
        if state is None or key_of(state) not in BUSY_ACTIVITIES or until <= since:
            continue
        hour = since.replace(minute=0, second=0, microsecond=0)
        while hour < min(until, end):
            hours.add(hour)
            hour += HOUR
    return hours


async def _busy_hours(
    client: HAClient,
    device: DeviceConfig | None,
    profiles: dict[str, dict[str, Any]],
    roles: dict[str, str | None],
    snapshot: "Snapshot | None",
    now: datetime,
) -> set[datetime]:
    """Hours with hot water or defrost from the activity's state history; none without a heat pump."""
    profile = profiles.get(device.profile) if device else None
    if device is None or profile is None or snapshot is None or not (entity_id := roles.get("activity")):
        return set()
    found = (snapshot.manifests.get(device.ha_device_id) or {}).get(manifest_id(profile) or "")
    entity = entities_by_key(found.manifest if found else None).get(profile["roles"].get("activity", {}).get("key", ""))
    if entity is None:
        return set()
    start = (now - timedelta(days=HISTORY_DAYS)).replace(minute=0, second=0, microsecond=0)
    result = await client.command(
        "history/history_during_period",
        start_time=start.isoformat(),
        end_time=now.isoformat(),
        entity_ids=[entity_id],
        minimal_response=True,
        no_attributes=True,
        significant_changes_only=False,
    )
    return busy_hours(result.get(entity_id, []), lambda raw: canonical(profile, "activity", value_key(entity, raw)), start, now)


async def _heat(
    client: HAClient, device: DeviceConfig | None, roles: dict[str, str | None], outside: dict[datetime, float], now: datetime
) -> tuple[dict[datetime, float], str | None]:
    """Heat per hour in kWh: measured, else electric x COP (sensor, else Carnot estimate).

    The recorder gives power in W (statistics converted, states normalized), also for a sensor in kW.
    """
    if device is None:
        return {}, None
    if heat_power := await _hourly(client, roles.get("heat_power"), now):
        return {s: max(w, 0.0) / 1000 for s, w in heat_power.items()}, "measured"
    electric = await _hourly(client, roles.get("power"), now)
    if not electric:
        phases = [await _hourly(client, roles.get(f"power_{p}"), now) for p in ("l1", "l2", "l3")]
        phases = [p for p in phases if p]
        electric = {s: sum(p[s] for p in phases) for s in phases[0] if all(s in p for p in phases)} if phases else {}
    if not electric:
        return {}, None
    if cop := await _hourly(client, roles.get("cop"), now):
        return {s: max(w, 0.0) / 1000 * cop[s] for s, w in electric.items() if s in cop}, "cop"
    return {s: max(w, 0.0) / 1000 * carnot_cop(outside[s]) for s, w in electric.items() if s in outside}, "estimated"


def _bright(values: dict[datetime, float]) -> float | None:
    """A bright hour of the period (95th percentile); None without a positive one."""
    if not values:
        return None
    ordered = sorted(values.values())
    bright = ordered[int(0.95 * (len(ordered) - 1))]
    return bright if bright > 0 else None


def _relative(values: dict[datetime, float]) -> dict[datetime, float]:
    """Sun from 0 to 1: lux and W/m² alike, against a bright hour of the period."""
    if (bright := _bright(values)) is None:
        return {}
    return {s: min(max(v / bright, 0.0), 1.0) for s, v in values.items()}


# From heat_pump_samples (9.3)

# Activities whose hour is used: the heat pump heats (validly), pauses or is off
CALM_ACTIVITIES = frozenset({"heating", "idle"})


def _hour_left_out(row: dict[str, Any]) -> bool:
    """A 5 minute row whose hour is left out: hot water, defrost, after-run, ventilation, fault, unknown
    activity or heating that was not valid (its heat is not reliable)."""
    flags = row.get("flags") or {}
    activity = row.get("activity")
    if activity not in CALM_ACTIVITIES or flags.get("after_run") or flags.get("ventilation") or flags.get("fault"):
        return True
    return activity == "heating" and flags.get("valid") is not True


def _start_utc(row: dict[str, Any]) -> datetime | None:
    start = row.get("start")
    try:
        start = datetime.fromisoformat(start) if isinstance(start, str) else start
    except ValueError:
        return None
    return start.astimezone(UTC) if isinstance(start, datetime) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# The row giving an hour's room temperature starts at most this long after the hour
HOUR_START_WITHIN = timedelta(minutes=10)


def sample_hours(
    rows: list[dict[str, Any]], weights: dict[str, float]
) -> tuple[dict[datetime, dict[str, Any]], int, str | None]:
    """Hourly values from 5 minute rows: per hour inside (room temperature at its start), outside and heat
    (kWh, None if not measured) as means, the rooms; the hours left out (with enough rows); the heat
    source most rows name."""
    hours: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if (start := _start_utc(row)) is not None:
            hours[start.replace(minute=0, second=0, microsecond=0)].append(row)
    result: dict[datetime, dict[str, Any]] = {}
    excluded = 0
    sources: list[str] = []
    for hour, members in sorted(hours.items()):
        if len(members) < HOUSE_SAMPLES_HOUR_MIN_ROWS:
            continue
        if any(_hour_left_out(row) for row in members):
            excluded += 1
            continue
        # The model steps from the temperature at one hour's start to the next one's: the row at the start
        # (a mean of the whole hour would smear the heat of neighbouring hours into each other)
        first = min(members, key=lambda r: _start_utc(r) or hour)
        inside = row_room(first, weights) if (_start_utc(first) or hour) - hour < HOUR_START_WITHIN else None
        outside = _mean([float(row["outside_c"]) for row in members if isinstance(row.get("outside_c"), (int, float))])
        if inside is None or outside is None:
            continue
        heats = [max(float(row["heat_w"]), 0.0) for row in members if isinstance(row.get("heat_w"), (int, float))]
        sources += [row["heat_source"] for row in members if row.get("heat_source")]
        rooms: dict[str, list[float]] = defaultdict(list)
        for row in members:
            for entity_id, value in (row.get("rooms") or {}).items():
                if entity_id in weights and isinstance(value, (int, float)):
                    rooms[entity_id].append(float(value))
        result[hour] = {
            "inside": inside,
            "outside": outside,
            "heat": sum(heats) / len(heats) / 1000 if len(heats) >= HOUSE_SAMPLES_HOUR_MIN_ROWS else None,
            "rooms": {e: sum(v) / len(v) for e, v in rooms.items()},
        }
    source = max(set(sources), key=sources.count) if sources else None
    return result, excluded, source


def samples_house_data(rows: list[dict[str, Any]], config: SunflowConfig, ha: HouseData, coverage_days: int) -> HouseData:
    """The house's hourly rows from heat_pump_samples; sun and the outside entity as from Home Assistant.

    The sun of an hour is the sensor's mean of that very hour (never the current value), taken from
    all hours of its history - not only from the hours Home Assistant's rows keep (those leave out
    hours with hot water or without an outside or room value there)."""
    weights = {room.entity_id: room.weight for room in config.house.rooms}
    hours, excluded, heat_source = sample_hours(rows, weights)
    sun = ha.sun_hours
    per_room: dict[str, dict[datetime, float]] = {room.entity_id: {} for room in config.house.rooms}
    for hour, values in hours.items():
        for entity_id, value in values["rooms"].items():
            per_room[entity_id][hour] = value
    rows_out = [
        HourRow(start=hour, inside=v["inside"], outside=v["outside"], heat=v["heat"],
                sun=sun.get(hour) if ha.sun_entity else None)
        for hour, v in sorted(hours.items())
    ]
    return HouseData(
        rows=rows_out, per_room=per_room, inside={h: v["inside"] for h, v in hours.items()},
        heat_source=heat_source if any(r.heat is not None for r in rows_out) else None,
        outside_entity=ha.outside_entity, sun_entity=ha.sun_entity, excluded_hours=excluded,
        source=SAMPLES, coverage_days=coverage_days, sun_bright=ha.sun_bright,
    )


@dataclass(frozen=True, kw_only=True)
class HouseLearning:
    """The data the house was learned from and the model (None with too little data)."""

    data: HouseData
    model: HouseModel | None


def choose_samples(samples: HouseModel | None, ha: HouseModel | None, kept: bool) -> bool:
    """Learn from the samples: their model is there and, unless chosen before, not worse in use."""
    if samples is None:
        # Back to Home Assistant rather than showing nothing
        return False
    return kept or samples.useful or ha is None or not ha.useful


async def learn_house(
    client: HAClient,
    config: SunflowConfig,
    profiles: dict[str, dict[str, Any]],
    now: datetime,
    snapshot: "Snapshot | None" = None,
    store: "ShadowStore | None" = None,
    kept: bool = False,
) -> HouseLearning:
    """Learn the house from Home Assistant's hourly means, or from heat_pump_samples once they cover
    MIN_DAYS days of valid heating (9.3); kept: the samples were chosen before."""
    ha = await house_data(client, config, profiles, now, snapshot)
    ha_model = fit(ha.rows, now)
    device = heat_pump_device(config, profiles)
    if store is None or device is None:
        return HouseLearning(data=ha, model=ha_model)
    coverage = await asyncio.to_thread(store.heat_pump_valid_days, device.id, HOUSE_SAMPLES_DAY_MIN_S)
    ha = replace(ha, coverage_days=coverage)
    if coverage < MIN_DAYS:
        return HouseLearning(data=ha, model=ha_model)
    rows = await asyncio.to_thread(store.heat_pump_samples, device.id, now - timedelta(days=HISTORY_DAYS))
    samples = samples_house_data(rows, config, ha, coverage)
    samples_model = fit(samples.rows, now)
    if choose_samples(samples_model, ha_model, kept):
        return HouseLearning(data=samples, model=samples_model)
    return HouseLearning(data=ha, model=ha_model)


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
