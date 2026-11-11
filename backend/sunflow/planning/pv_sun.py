"""Learn the PV forecast correction per roof plane by the sun's position, from history.

Shading by trees and buildings depends on where the sun stands, not on the clock: what
is learned in autumn at 20° elevation holds again in spring. Per plane, the past
forecasts (Solcast keeps them) and the measured power of the plane's strings (long-term
statistics) give hourly pairs; the factor measured / forecast is learned per cell of sun
elevation and azimuth, separately for clear and cloudy hours (shade matters little in
diffuse light). Neighbouring cells smooth each other, and a cell with little data stays
close to the plane's overall factor. Runs in shadow mode: it is only compared, not planned.
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from sunflow.config.models import ForecastPlane, SunflowConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.resolver import preview
from sunflow.devices.site import pv_strings
from sunflow.ha import HAClient, HACommandError
from sunflow.planning.pv_episodes import SoilingHistory, daily_energy, soiling_history
from sunflow.planning.pv_season import snow_on_all
from sunflow.planning.pv_steps import PlaneState

from .sun import sun_position

ELEVATION_STEP = 5.0
AZIMUTH_STEP = 10.0
AZIMUTH_CELLS = int(360 / AZIMUTH_STEP)
# Hours with less forecast energy say nothing about shading
MIN_FORECAST_WH = 50.0
# Forecast as share of the most the forecast gives at that sun position: clear above
CLEAR_SHARE = 0.6
# Forecast energy (Wh) of a cell that counts as much as the level above
PRIOR_WH = 3_000.0
FACTOR_RANGE = (0.05, 1.7)
# Neighbouring cells count half in the prior of a cell
NEIGHBOUR_WEIGHT = 0.5
BACKTEST_DAYS = 21
# The forecast's own uncertainty, p10 / p50: below the first very uncertain, above the second
# fairly sure; per class it is learned how far towards p10 the measurement tends to be
SPREAD_BOUNDS = (0.3, 0.6)
# Squared Wh that count as much as "the forecast as it is" (weight 1) when learning a weight
SPREAD_PRIOR = 2_000_000.0
SPREAD_RANGE = (0.0, 1.5)
# Planning uses a plane's model only when learned from this many days and better in the backtest
MIN_DAYS_TO_PLAN = 60
SOLCAST_CHUNK = timedelta(days=30)
HISTORY_DAYS = 730
# Soiling (leaves, snow, dirt): the last days against what the plane delivered before them
SOILING_RECENT_DAYS = 30
SOILING_MIN_DAYS = 4
# Below this share of the learned expectation; against the raw forecast (no history) far below only
SOILING_RATIO = 0.7
SOILING_RATIO_RAW = 0.5
# Hours with a low sun say little (shade); days with little expected energy are left out
SOILING_MIN_ELEVATION = 10.0
SOILING_MIN_DAY_WH = 500.0
# The current state (e.g. autumn leaves come and go): the last days, the latest counting most
LEVEL_DAYS = 3
LEVEL_HALF_LIFE_DAYS = 1.0
# Above this the plane counts as clean; below it planning uses the lower level
LEVEL_APPLY = 0.9


@dataclass(frozen=True, kw_only=True)
class Sample:
    """One hour of a plane: forecast and measured energy with the sun's position."""

    start: datetime
    forecast_wh: float
    actual_wh: float
    elevation: float
    azimuth: float
    clear: bool = False
    # The forecast's pessimistic case (Solcast p10); its distance to forecast_wh is its uncertainty
    forecast10_wh: float | None = None


def _cell(elevation: float, azimuth: float) -> tuple[int, int]:
    return int(elevation // ELEVATION_STEP), int(azimuth // AZIMUTH_STEP)


def _clamp(factor: float) -> float:
    return min(max(factor, FACTOR_RANGE[0]), FACTOR_RANGE[1])


def envelope(samples: list[Sample]) -> dict[tuple[int, int], float]:
    """The most the forecast gives per sun cell (95th percentile): its clear-sky value."""
    values: dict[tuple[int, int], list[float]] = defaultdict(list)
    for s in samples:
        values[_cell(s.elevation, s.azimuth)].append(s.forecast_wh)
    return {cell: sorted(v)[int(0.95 * (len(v) - 1))] for cell, v in values.items()}


def is_clear(forecast_wh: float, elevation: float, azimuth: float, top: dict[tuple[int, int], float]) -> bool:
    """Forecast near the most it gives at that sun position; unknown positions count as cloudy."""
    cell = _cell(elevation, azimuth)
    return cell in top and forecast_wh >= CLEAR_SHARE * top[cell]


def with_clearness(samples: list[Sample]) -> list[Sample]:
    """Mark clear hours: forecast near the most it ever gives at that sun position."""
    top = envelope(samples)
    return [
        Sample(
            start=s.start, forecast_wh=s.forecast_wh, actual_wh=s.actual_wh, elevation=s.elevation,
            azimuth=s.azimuth, clear=is_clear(s.forecast_wh, s.elevation, s.azimuth, top), forecast10_wh=s.forecast10_wh,
        )
        for s in samples
    ]


@dataclass(kw_only=True)
class SunCorrection:
    """Factors per sun cell and clear/cloudy; factor() falls back to the class, then overall."""

    overall: float = 1.0
    by_class: dict[bool, float] = field(default_factory=dict)
    cells: dict[tuple[bool, int, int], float] = field(default_factory=dict)
    # Clear-sky forecast (Wh per hour) per sun cell, to tell clear from cloudy ahead
    top: dict[tuple[int, int], float] = field(default_factory=dict)
    hours: int = 0
    days: int = 0
    # Current state of the modules (leaves, snow, dirt): share of the learned yield
    level: float = 1.0
    # By uncertainty class: how far from p10 towards p50 the measurement lies (1 = p50)
    spread_weights: dict[int, float] = field(default_factory=dict)

    def factor(self, elevation: float, azimuth: float, clear: bool) -> float:
        """Correction for a sun position."""
        return self.cells.get((clear, *_cell(elevation, azimuth)), self.by_class.get(clear, self.overall))

    def spread_factor(self, forecast: float, forecast10: float | None) -> float:
        """Share of the forecast expected from its uncertainty: p10 + weight * (p50 - p10), relative to p50."""
        if forecast10 is None or forecast <= 0 or (cls := spread_class(forecast, forecast10)) not in self.spread_weights:
            return 1.0
        low = min(forecast10, forecast)
        return (low + self.spread_weights[cls] * (forecast - low)) / forecast

    def factor_at(
        self, moment: datetime, forecast_w: float, latitude: float, longitude: float, forecast10_w: float | None = None
    ) -> float:
        """Correction for a slot starting at moment with this forecast power (its mean over the hour)."""
        elevation, azimuth = sun_position(moment, latitude, longitude)
        if elevation <= 0:
            return 1.0
        cell = self.factor(elevation, azimuth, is_clear(forecast_w, elevation, azimuth, self.top))
        return cell * self.spread_factor(forecast_w, forecast10_w) * self.level

    def as_dict(self) -> dict[str, Any]:
        """Clear-sky cells for the shading map, with the overall factors."""
        return {
            "overall": round(self.overall, 3),
            "clear": round(self.by_class.get(True, self.overall), 3),
            "cloudy": round(self.by_class.get(False, self.overall), 3),
            "hours": self.hours,
            "days": self.days,
            "spread_weights": {str(k): round(w, 3) for k, w in sorted(self.spread_weights.items())},
            "elevation_step": ELEVATION_STEP,
            "azimuth_step": AZIMUTH_STEP,
            "cells": [
                {"elevation": e * ELEVATION_STEP, "azimuth": a * AZIMUTH_STEP, "factor": round(f, 3)}
                for (clear, e, a), f in sorted(self.cells.items())
                if clear
            ],
        }


def spread_class(forecast: float, forecast10: float) -> int:
    """0 very uncertain, 1 uncertain, 2 fairly sure, by p10 / p50."""
    share = forecast10 / forecast if forecast > 0 else 1.0
    return sum(share >= bound for bound in SPREAD_BOUNDS)


def fit_spread(samples: list[Sample], correction: SunCorrection) -> dict[int, float]:
    """Per uncertainty class the weight w with measured = cell factor * (p10 + w * (p50 - p10)).

    Least squares on what the cell factor leaves, pulled towards 1 (the forecast as it is).
    """
    sums: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for s in samples:
        if s.forecast10_wh is None or s.forecast_wh < MIN_FORECAST_WH:
            continue
        cell = correction.factor(s.elevation, s.azimuth, s.clear)
        low = min(s.forecast10_wh, s.forecast_wh)
        spread = s.forecast_wh - low
        target = s.actual_wh / cell - low
        total = sums[spread_class(s.forecast_wh, s.forecast10_wh)]
        total[0] += spread * target
        total[1] += spread * spread
    return {
        cls: min(max((xy + SPREAD_PRIOR) / (xx + SPREAD_PRIOR), SPREAD_RANGE[0]), SPREAD_RANGE[1])
        for cls, (xy, xx) in sums.items()
    }


def fit(samples: list[Sample]) -> SunCorrection:
    """Learn the factors from hourly samples (clearness already marked)."""
    used = [s for s in samples if s.forecast_wh >= MIN_FORECAST_WH]
    correction = SunCorrection(hours=len(used), days=len({s.start.date() for s in used}), top=envelope(samples))
    if not used:
        return correction
    total_f = sum(s.forecast_wh for s in used)
    correction.overall = _clamp((sum(s.actual_wh for s in used) + PRIOR_WH) / (total_f + PRIOR_WH))
    sums: dict[tuple[bool, int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for clear in (True, False):
        part = [s for s in used if s.clear == clear]
        correction.by_class[clear] = _clamp(
            (sum(s.actual_wh for s in part) + PRIOR_WH * correction.overall)
            / (sum(s.forecast_wh for s in part) + PRIOR_WH)
        )
    for s in used:
        total = sums[(s.clear, *_cell(s.elevation, s.azimuth))]
        total[0] += s.actual_wh
        total[1] += s.forecast_wh
    # Hourly samples hit the azimuth in steps: empty cells next to data get their neighbourhood
    cells = {
        (clear, e + de, (a + da) % AZIMUTH_CELLS)
        for clear, e, a in sums
        for de in (-1, 0, 1)
        for da in (-1, 0, 1)
        if e + de >= 0
    }
    for clear, e, a in cells:
        # The neighbourhood is the prior of a cell: a cell with much data of its own stays sharp
        actual = forecast = 0.0
        for de in (-1, 0, 1):
            for da in (-1, 0, 1):
                if (neighbour := sums.get((clear, e + de, (a + da) % AZIMUTH_CELLS))) is None:
                    continue
                weight = 1.0 if de == da == 0 else NEIGHBOUR_WEIGHT
                actual += weight * neighbour[0]
                forecast += weight * neighbour[1]
        parent = correction.by_class[clear]
        around = (actual + PRIOR_WH * parent) / (forecast + PRIOR_WH)
        own_actual, own_forecast = sums.get((clear, e, a), (0.0, 0.0))
        correction.cells[(clear, e, a)] = _clamp((own_actual + PRIOR_WH * around) / (own_forecast + PRIOR_WH))
    return correction


def _hour_fit(samples: list[Sample], local: Any) -> dict[tuple[bool, int], float]:
    """The comparison: one factor per local hour and clear/cloudy, like the current learning."""
    sums: dict[tuple[bool, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for s in samples:
        if s.forecast_wh >= MIN_FORECAST_WH:
            total = sums[(s.clear, local(s.start).hour)]
            total[0] += s.actual_wh
            total[1] += s.forecast_wh
    overall_a = sum(v[0] for v in sums.values())
    overall_f = sum(v[1] for v in sums.values())
    overall = (overall_a + PRIOR_WH) / (overall_f + PRIOR_WH) if overall_f else 1.0
    return {key: _clamp((a + PRIOR_WH * overall) / (f + PRIOR_WH)) for key, (a, f) in sums.items()}


def backtest(samples: list[Sample], tz: Any) -> dict[str, Any] | None:
    """Mean absolute error (Wh per hour) on the last weeks, learned from the hours before."""
    if not samples:
        return None
    split = max(s.start for s in samples) - timedelta(days=BACKTEST_DAYS)
    train = [s for s in samples if s.start < split]
    test = [s for s in samples if s.start >= split and s.forecast_wh >= MIN_FORECAST_WH]
    if not train or not test:
        return None
    local = lambda moment: moment.astimezone(tz)  # noqa: E731
    sun = fit(train)
    spread = SunCorrection(**{**sun.__dict__, "spread_weights": fit_spread(train, sun)})
    hours = _hour_fit(train, local)

    def error(predict: Any) -> float:
        return round(sum(abs(predict(s) - s.actual_wh) for s in test) / len(test), 1)

    return {
        "days": BACKTEST_DAYS,
        "hours": len(test),
        "raw": error(lambda s: s.forecast_wh),
        "by_hour": error(lambda s: s.forecast_wh * hours.get((s.clear, local(s.start).hour), 1.0)),
        "by_sun": error(lambda s: s.forecast_wh * sun.factor(s.elevation, s.azimuth, s.clear)),
        "by_spread": error(
            lambda s: s.forecast_wh
            * spread.factor(s.elevation, s.azimuth, s.clear)
            * spread.spread_factor(s.forecast_wh, s.forecast10_wh)
        ),
        "measured_mean": round(sum(s.actual_wh for s in test) / len(test), 1),
    }


def samples_from(
    forecast: dict[datetime, float],
    measured: dict[datetime, float],
    latitude: float,
    longitude: float,
    forecast10: dict[datetime, float] | None = None,
) -> list[Sample]:
    """Hourly samples where both are known; the sun at the middle of the hour."""
    result = []
    for start, forecast_wh in sorted(forecast.items()):
        if (actual := measured.get(start)) is None:
            continue
        elevation, azimuth = sun_position(start + timedelta(minutes=30), latitude, longitude)
        if elevation <= 0:
            continue
        result.append(
            Sample(
                start=start, forecast_wh=forecast_wh, actual_wh=max(actual, 0.0), elevation=elevation, azimuth=azimuth,
                forecast10_wh=(forecast10 or {}).get(start),
            )
        )
    return with_clearness(result)


async def solcast_hours(
    client: HAClient, site: str, start: datetime, end: datetime
) -> tuple[dict[datetime, float], dict[datetime, float]]:
    """Past Solcast forecast (p50, p10) as energy (Wh) per hour, queried in chunks; days before its history are skipped."""
    hours: dict[datetime, float] = defaultdict(float)
    hours10: dict[datetime, float] = defaultdict(float)
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + SOLCAST_CHUNK, end)
        try:
            response = await client.call_service(
                "solcast_solar",
                "query_forecast_data",
                {"start_date_time": chunk_start.isoformat(), "end_date_time": chunk_end.isoformat(), "site": site},
                return_response=True,
            )
        except HACommandError:
            # Before the kept history the range is refused
            chunk_start = chunk_end
            continue
        for item in response["response"]["data"]:
            period = datetime.fromisoformat(str(item["period_start"]))
            hour = period.replace(minute=0, second=0, microsecond=0)
            # Half-hour periods with the mean power in kW
            hours[hour] += float(item["pv_estimate"]) * 1000 / 2
            hours10[hour] += float(item.get("pv_estimate10", item["pv_estimate"])) * 1000 / 2
        chunk_start = chunk_end
    return dict(hours), dict(hours10)


async def measured_hours(client: HAClient, entities: list[str], start: datetime, end: datetime) -> dict[datetime, float]:
    """Hourly mean power summed over the strings (= Wh per hour); hours missing a string are left out."""
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=entities,
        period="hour",
        types=["mean"],
        units={"power": "W"},
    )
    per_string = [
        {datetime.fromtimestamp(row["start"] / 1000, start.tzinfo): row["mean"] for row in result.get(e, []) if row.get("mean") is not None}
        for e in entities
    ]
    common = set.intersection(*(set(s) for s in per_string)) if per_string else set()
    return {hour: sum(s[hour] for s in per_string) for hour in common}


def daily_ratios(samples: list[Sample], reference: SunCorrection | None, tz: tzinfo) -> list[tuple[date, float]]:
    """Measured / expected energy per local day; expected is the forecast corrected by the reference."""
    days: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for s in samples:
        if s.elevation < SOILING_MIN_ELEVATION or s.forecast_wh < MIN_FORECAST_WH:
            continue
        factor = (
            reference.factor(s.elevation, s.azimuth, s.clear) * reference.spread_factor(s.forecast_wh, s.forecast10_wh)
            if reference is not None
            else 1.0
        )
        total = days[s.start.astimezone(tz).date()]
        total[0] += s.actual_wh
        total[1] += s.forecast_wh * factor
    return [(day, actual / expected) for day, (actual, expected) in sorted(days.items()) if expected >= SOILING_MIN_DAY_WH]


def soiled_run(ratios: list[tuple[date, float]], threshold: float) -> dict[str, Any] | None:
    """The latest days in a row below the threshold, when there are enough of them."""
    run = []
    for day, ratio in reversed(ratios):
        if ratio >= threshold:
            break
        run.append((day, ratio))
    if len(run) < SOILING_MIN_DAYS:
        return None
    shares = sorted(r for _, r in run)
    return {"since": run[-1][0].isoformat(), "days": len(run), "ratio": round(shares[len(shares) // 2], 3)}


def current_level(ratios: list[tuple[date, float]], today: date) -> float:
    """Share of the learned yield the plane delivers now; 1 when it is clean."""
    recent = [(day, ratio) for day, ratio in ratios if (today - day).days < LEVEL_DAYS]
    if not recent:
        return 1.0
    weights = [0.5 ** ((today - day).days / LEVEL_HALF_LIFE_DAYS) for day, _ in recent]
    level = sum(w * r for w, (_, r) in zip(weights, recent, strict=True)) / sum(weights)
    return 1.0 if level >= LEVEL_APPLY else max(level, 0.1)


@dataclass(kw_only=True)
class PlaneSun:
    """What was learned for one plane in use, or why nothing."""

    key: str
    name: str
    source: str
    correction: SunCorrection | None = None
    backtest: dict[str, Any] | None = None
    first: datetime | None = None
    missing: str | None = None
    # Days the plane delivered far less than expected (leaves, snow, dirt), the latest run
    soiling: dict[str, Any] | None = None
    # Compared with the plane's own history (else with the raw forecast)
    soiling_learned: bool = False

    @property
    def plannable(self) -> bool:
        """Enough history, and closer to the measurement than the correction by time of day."""
        return (
            self.correction is not None
            and self.correction.days >= MIN_DAYS_TO_PLAN
            and self.backtest is not None
            and min(self.backtest["by_sun"], self.backtest.get("by_spread", self.backtest["by_sun"])) < self.backtest["by_hour"]
        )

    def as_dict(self) -> dict[str, Any]:
        """For the API."""
        result: dict[str, Any] = {"key": self.key, "name": self.name, "source": self.source, "plannable": self.plannable}
        if self.missing:
            result["missing"] = self.missing
        if self.soiling is not None:
            result["soiling"] = self.soiling | {"learned": self.soiling_learned}
        if self.correction is not None and self.correction.level < 1:
            result["level"] = round(self.correction.level, 3)
        if self.correction is not None:
            result |= {
                "correction": self.correction.as_dict(),
                "backtest": self.backtest,
                "first": self.first.isoformat() if self.first else None,
            }
        return result


def string_power_entities(
    config: SunflowConfig, profiles: dict[str, Any], snapshot: Snapshot, plane: ForecastPlane
) -> list[str]:
    """Power entities of the plane's strings; empty when one is only voltage times current."""
    entities = []
    for string in plane.strings:
        device_id, _, index = string.partition(":")
        device = next((d for d in config.devices if d.id == device_id), None)
        if device is None:
            return []
        roles = {
            r.role: r
            for r in preview(
                profiles[device.profile], snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()}
            )
        }
        role = roles.get(f"pv{index}_power")
        # A device with one string: its PV input power is that string
        if (role is None or not role.entity_id) and len(pv_strings(roles)) == 1:
            role = roles.get("pv_power")
        if role is None or not role.entity_id:
            return []
        entities.append(role.entity_id)
    return entities


async def daily_lows(client: HAClient, entity_id: str, start: datetime, end: datetime, tz: tzinfo) -> dict[date, float]:
    """Lowest temperature (°C) per local day from the long-term statistics."""
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=[entity_id],
        period="day",
        types=["min"],
        units={"temperature": "°C"},
    )
    return {
        datetime.fromtimestamp(row["start"] / 1000, tz).date(): row["min"]
        for row in result.get(entity_id, [])
        if row.get("min") is not None
    }


async def learn_planes(
    client: HAClient,
    config: SunflowConfig,
    profiles: dict[str, Any],
    snapshot: Snapshot,
    tz: tzinfo,
    states: dict[str, PlaneState] | None = None,
) -> tuple[list[PlaneSun], SoilingHistory]:
    """Learn every plane in use from its history (minutes on a Raspberry Pi: run in the background).

    states: soiled days per plane key from the comparison with the other planes; a short history leaves them out.
    Also returns the soiled periods found in the whole history.
    """
    ha_config = await client.command("get_config")
    latitude, longitude = ha_config["latitude"], ha_config["longitude"]
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    planes = []
    history: dict[str, list[Sample]] = {}
    ratios: dict[str, dict[date, float]] = {}
    for plane in config.forecast_planes:
        if not plane.device_id:
            continue
        result = PlaneSun(key=plane.key, name=plane.display_name, source=plane.source)
        planes.append(result)
        entities = string_power_entities(config, profiles, snapshot, plane)
        if plane.source != "solcast":
            result.missing = "no_history"
            continue
        if not entities:
            result.missing = "no_string_power"
            continue
        start = max(now - timedelta(days=HISTORY_DAYS), plane.learn_since or datetime.min.replace(tzinfo=UTC))
        forecast, forecast10 = await solcast_hours(client, plane.ref, start, now)
        measured = await measured_hours(client, entities, start, now)
        samples = await asyncio.to_thread(samples_from, forecast, measured, latitude, longitude, forecast10)
        history[plane.key] = samples
        state = (states or {}).get(plane.key)
        if state is not None and len({s.start.astimezone(tz).date() for s in samples}) < MIN_DAYS_TO_PLAN:
            # A long history finds its own soiled days against the years before
            samples = [s for s in samples if not state.is_soiled(s.start.astimezone(tz).date())]
        recent = samples
        result.soiling, result.soiling_learned = await asyncio.to_thread(_soiling, samples, now, tz)
        if result.soiling is not None:
            # Leaves on the modules now are no shade the plane has in other years
            since = date.fromisoformat(result.soiling["since"])
            samples = [s for s in samples if s.start.astimezone(tz).date() < since]
        result.correction = await asyncio.to_thread(fit, samples)
        result.correction.spread_weights = await asyncio.to_thread(fit_spread, samples, result.correction)
        result.correction.level = current_level(
            daily_ratios([s for s in recent if s.start >= now - timedelta(days=LEVEL_DAYS)], result.correction, tz),
            now.astimezone(tz).date(),
        )
        result.backtest = await asyncio.to_thread(backtest, samples, tz)
        if result.backtest is None or result.backtest["by_spread"] >= result.backtest["by_sun"]:
            # Only used where it predicts better than the sun position alone
            result.correction.spread_weights = {}
        result.first = min(s.start for s in samples) if samples else None
        ratios[plane.key] = dict(await asyncio.to_thread(daily_ratios, recent, result.correction, tz))
    return planes, await _episodes(client, config, history, ratios, now, tz)


async def _episodes(
    client: HAClient,
    config: SunflowConfig,
    history: dict[str, list[Sample]],
    ratios: dict[str, dict[date, float]],
    now: datetime,
    tz: tzinfo,
) -> SoilingHistory:
    """The soiled periods of the history, with the outdoor temperature when a source is chosen."""
    temps = None
    if (entity_id := config.learning_sources.outdoor_temperature) and history:
        first = min((s.start for samples in history.values() for s in samples), default=now)
        try:
            temps = await daily_lows(client, entity_id, first, now, tz)
        except HACommandError:
            temps = None
    energy = await asyncio.to_thread(daily_energy, history, tz)
    return await asyncio.to_thread(soiling_history, energy, ratios, temps, now.astimezone(tz).date())


def _soiling(samples: list[Sample], now: datetime, tz: tzinfo) -> tuple[dict[str, Any] | None, bool]:
    """The latest soiled run against the plane's history before the recent days, else the raw forecast."""
    split = now - timedelta(days=SOILING_RECENT_DAYS)
    older = [s for s in samples if s.start < split]
    reference = fit(older) if len({s.start.date() for s in older}) >= MIN_DAYS_TO_PLAN else None
    if reference is not None:
        # A grey week is no soiling: expected is what Solcast's own uncertainty suggests
        reference.spread_weights = fit_spread(older, reference)
    recent = [s for s in samples if s.start >= split]
    ratios = daily_ratios(recent, reference, tz)
    return soiled_run(ratios, SOILING_RATIO if reference is not None else SOILING_RATIO_RAW), reference is not None


async def refresh_levels(
    client: HAClient, config: SunflowConfig, profiles: dict[str, Any], snapshot: Snapshot, planes: list[PlaneSun], tz: tzinfo
) -> None:
    """Update the current state of the learned planes from the last days (cheap, every few hours)."""
    ha_config = await client.command("get_config")
    now = datetime.now(UTC)
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(days=LEVEL_DAYS)
    by_key = {p.key: p for p in config.forecast_planes}
    for plane in planes:
        if plane.correction is None or (forecast_plane := by_key.get(plane.key)) is None:
            continue
        entities = string_power_entities(config, profiles, snapshot, forecast_plane)
        if not entities:
            continue
        forecast, forecast10 = await solcast_hours(client, forecast_plane.ref, start, now)
        measured = await measured_hours(client, entities, start, now)
        samples = samples_from(forecast, measured, ha_config["latitude"], ha_config["longitude"], forecast10)
        # Clear or cloudy by the learned clear-sky values, not by these few days
        samples = [
            Sample(
                start=s.start, forecast_wh=s.forecast_wh, actual_wh=s.actual_wh, elevation=s.elevation, azimuth=s.azimuth,
                clear=is_clear(s.forecast_wh, s.elevation, s.azimuth, plane.correction.top), forecast10_wh=s.forecast10_wh,
            )
            for s in samples
        ]
        plane.correction.level = current_level(daily_ratios(samples, plane.correction, tz), now.astimezone(tz).date())


async def snow_now(
    client: HAClient, config: SunflowConfig, snapshot: Snapshot, planes: list[PlaneSun], tz: tzinfo
) -> dict[str, Any] | None:
    """Snow on all learned planes over the last days: frost, light, and every plane far below its learned yield."""
    levels = {p.key: p.correction.level for p in planes if p.correction is not None}
    sources = config.learning_sources
    if not levels or not sources.outdoor_temperature:
        return None
    now = datetime.now(UTC)
    start = now - timedelta(days=LEVEL_DAYS)
    try:
        lows = await daily_lows(client, sources.outdoor_temperature, start, now, tz)
        bright = None
        if sources.irradiance:
            hours = await measured_hours(client, [sources.irradiance], start, now)
            bright = max(hours.values(), default=None)
    except HACommandError:
        return None
    unit = (snapshot.states.get(sources.irradiance or "") or {}).get("attributes", {}).get("unit_of_measurement")
    return snow_on_all(levels, min(lows.values(), default=None), bright, unit)

