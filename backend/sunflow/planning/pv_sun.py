"""Learn the PV forecast correction per roof plane by the sun's position, from history.

Shading by trees and buildings depends on where the sun stands, not on the clock: what
is learned in autumn at 20° elevation holds again in spring. Per plane, the past
forecasts (Solcast keeps them) and the measured power of the plane's strings (long-term
statistics) give hourly pairs; the factor measured / forecast is learned per cell of sun
elevation and azimuth, separately for clear and cloudy hours (shade matters little in
diffuse light). Neighbouring cells smooth each other, and a cell with little data stays
close to the plane's overall factor. Runs in shadow mode: it is only compared, not planned.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sunflow.ha import HAClient, HACommandError

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
SOLCAST_CHUNK = timedelta(days=30)
HISTORY_DAYS = 730


@dataclass(frozen=True, kw_only=True)
class Sample:
    """One hour of a plane: forecast and measured energy with the sun's position."""

    start: datetime
    forecast_wh: float
    actual_wh: float
    elevation: float
    azimuth: float
    clear: bool = False


def _cell(elevation: float, azimuth: float) -> tuple[int, int]:
    return int(elevation // ELEVATION_STEP), int(azimuth // AZIMUTH_STEP)


def _clamp(factor: float) -> float:
    return min(max(factor, FACTOR_RANGE[0]), FACTOR_RANGE[1])


def with_clearness(samples: list[Sample]) -> list[Sample]:
    """Mark clear hours: forecast near the most it ever gives at that sun position."""
    envelope: dict[tuple[int, int], list[float]] = defaultdict(list)
    for s in samples:
        envelope[_cell(s.elevation, s.azimuth)].append(s.forecast_wh)
    top = {cell: sorted(values)[int(0.95 * (len(values) - 1))] for cell, values in envelope.items()}
    return [
        Sample(
            start=s.start, forecast_wh=s.forecast_wh, actual_wh=s.actual_wh, elevation=s.elevation,
            azimuth=s.azimuth, clear=s.forecast_wh >= CLEAR_SHARE * top[_cell(s.elevation, s.azimuth)],
        )
        for s in samples
    ]


@dataclass(kw_only=True)
class SunCorrection:
    """Factors per sun cell and clear/cloudy; factor() falls back to the class, then overall."""

    overall: float = 1.0
    by_class: dict[bool, float] = field(default_factory=dict)
    cells: dict[tuple[bool, int, int], float] = field(default_factory=dict)
    hours: int = 0
    days: int = 0

    def factor(self, elevation: float, azimuth: float, clear: bool) -> float:
        """Correction for a sun position."""
        return self.cells.get((clear, *_cell(elevation, azimuth)), self.by_class.get(clear, self.overall))

    def as_dict(self) -> dict[str, Any]:
        """Clear-sky cells for the shading map, with the overall factors."""
        return {
            "overall": round(self.overall, 3),
            "clear": round(self.by_class.get(True, self.overall), 3),
            "cloudy": round(self.by_class.get(False, self.overall), 3),
            "hours": self.hours,
            "days": self.days,
            "elevation_step": ELEVATION_STEP,
            "azimuth_step": AZIMUTH_STEP,
            "cells": [
                {"elevation": e * ELEVATION_STEP, "azimuth": a * AZIMUTH_STEP, "factor": round(f, 3)}
                for (clear, e, a), f in sorted(self.cells.items())
                if clear
            ],
        }


def fit(samples: list[Sample]) -> SunCorrection:
    """Learn the factors from hourly samples (clearness already marked)."""
    used = [s for s in samples if s.forecast_wh >= MIN_FORECAST_WH]
    correction = SunCorrection(hours=len(used), days=len({s.start.date() for s in used}))
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
    hours = _hour_fit(train, local)

    def error(predict: Any) -> float:
        return round(sum(abs(predict(s) - s.actual_wh) for s in test) / len(test), 1)

    return {
        "days": BACKTEST_DAYS,
        "hours": len(test),
        "raw": error(lambda s: s.forecast_wh),
        "by_hour": error(lambda s: s.forecast_wh * hours.get((s.clear, local(s.start).hour), 1.0)),
        "by_sun": error(lambda s: s.forecast_wh * sun.factor(s.elevation, s.azimuth, s.clear)),
        "measured_mean": round(sum(s.actual_wh for s in test) / len(test), 1),
    }


def samples_from(
    forecast: dict[datetime, float], measured: dict[datetime, float], latitude: float, longitude: float
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
            Sample(start=start, forecast_wh=forecast_wh, actual_wh=max(actual, 0.0), elevation=elevation, azimuth=azimuth)
        )
    return with_clearness(result)


async def solcast_hours(client: HAClient, site: str, start: datetime, end: datetime) -> dict[datetime, float]:
    """Past Solcast forecast as energy (Wh) per hour, queried in chunks; days before its history are skipped."""
    hours: dict[datetime, float] = defaultdict(float)
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
        chunk_start = chunk_end
    return dict(hours)


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
