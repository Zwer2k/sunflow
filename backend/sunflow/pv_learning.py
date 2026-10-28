"""Learn how the PV forecast deviates at this site and correct it.

Shadow mode stores the forecast and the measured PV per 15 minute slot, and the
forecast cloud coverage of the weather entity. From these, correction factors
(measured / forecast) are learned per local hour and cloud class. The levels build
on each other: overall, per hour, per hour and cloud class. A level with little data
stays close to the level above (shrinkage), so the correction starts cautious and
becomes specific as data accumulates.
"""

from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any

# Forecast (W summed over 15 minute slots) that counts as much as the level above. The
# overall factor needs a few days (single days are dominated by forecast misses of the
# weather); per hour and cloud class about a week of data
PRIOR_WEIGHT_OVERALL = 150_000.0
PRIOR_WEIGHT = 30_000.0
# Recent days count more: soiling, leaves and the sun's path change over weeks
HALF_LIFE_DAYS = 30
# Night and twilight slots say nothing about the forecast quality
MIN_FORECAST_W = 50.0
# A single plane can be shaded to a fifth of its forecast in winter (low sun behind trees
# or buildings); the shrinkage keeps such low factors from coming out of few data
FACTOR_RANGE = (0.1, 1.7)


def cloud_class(coverage: float | None) -> str | None:
    """'clear', 'mixed' or 'overcast' from the cloud coverage in %."""
    if coverage is None:
        return None
    if coverage < 30:
        return "clear"
    if coverage < 70:
        return "mixed"
    return "overcast"


@dataclass(kw_only=True)
class PvCorrection:
    """Learned factors; factor() falls back to the next coarser level."""

    overall: float = 1.0
    by_hour: dict[int, float] = field(default_factory=dict)
    by_hour_cloud: dict[tuple[int, str], float] = field(default_factory=dict)
    days: int = 0
    slots: int = 0
    # Mean absolute deviation (W) of the forecast before and after the correction
    error_before: float | None = None
    error_after: float | None = None

    def factor(self, hour: int, cloud: str | None) -> float:
        """Correction for a local hour and cloud class."""
        if cloud is not None and (hour, cloud) in self.by_hour_cloud:
            return self.by_hour_cloud[(hour, cloud)]
        return self.by_hour.get(hour, self.overall)

    def as_dict(self) -> dict[str, Any]:
        """For the API."""
        return {
            "overall": self.overall,
            "by_hour": {str(h): f for h, f in sorted(self.by_hour.items())},
            "by_cloud": {
                cloud: _mean([f for (h, c), f in self.by_hour_cloud.items() if c == cloud])
                for cloud in ("clear", "mixed", "overcast")
            },
            "days": self.days,
            "slots": self.slots,
            "error_before": self.error_before,
            "error_after": self.error_after,
        }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _shrunk(actual: float, forecast: float, prior: float, weight: float = PRIOR_WEIGHT) -> float:
    factor = (actual + weight * prior) / (forecast + weight)
    return min(max(factor, FACTOR_RANGE[0]), FACTOR_RANGE[1])


def learn_planes(
    rows: list[dict[str, Any]],
    planes: dict[str, list[str]],
    tz: tzinfo,
    since: dict[str, datetime] | None = None,
) -> dict[str, PvCorrection]:
    """A correction per forecast plane from the strings it is measured by.

    planes: plane key -> string keys. Rows need plane_forecasts and actual_strings.
    since: plane key -> learning restart; older rows do not count for that plane.
    """
    result = {}
    for key, strings in planes.items():
        if not strings:
            continue
        start = (since or {}).get(key)
        plane_rows = []
        for row in rows:
            if start is not None and datetime.fromisoformat(row["start"]) < start:
                continue
            forecast = (row.get("plane_forecasts") or {}).get(key)
            measured = row.get("actual_strings") or {}
            if forecast is None or not all(s in measured for s in strings):
                continue
            plane_rows.append(
                {
                    "start": row["start"],
                    "forecast_pv_w": forecast,
                    "actual_pv_w": sum(measured[s] for s in strings),
                    "weather_cloud": row.get("weather_cloud"),
                }
            )
        result[key] = learn(plane_rows, tz)
    return result


def learn(rows: list[dict[str, Any]], tz: tzinfo) -> PvCorrection:
    """Learn the factors from shadow rows (forecast_pv_w or planned_pv_w, actual_pv_w, weather_cloud)."""
    samples = []
    for row in rows:
        forecast = row.get("forecast_pv_w")
        if forecast is None:
            # Rows from before the correction existed: the planned PV was the raw forecast
            forecast = row.get("planned_pv_w")
        actual = row.get("actual_pv_w")
        if forecast is None or actual is None or forecast < MIN_FORECAST_W:
            continue
        local = datetime.fromisoformat(row["start"]).astimezone(tz)
        samples.append((local, forecast, max(actual, 0.0), cloud_class(row.get("weather_cloud"))))
    correction = PvCorrection(slots=len(samples), days=len({s[0].date() for s in samples}))
    if not samples:
        return correction
    newest = max(s[0] for s in samples)
    measured = samples
    # Weighted copies for the factors; the error check below uses the plain samples
    samples = [
        (local, forecast * w, actual * w, cloud)
        for local, forecast, actual, cloud in samples
        if (w := 0.5 ** ((newest - local).total_seconds() / 86400 / HALF_LIFE_DAYS))
    ]

    total_forecast = sum(s[1] for s in samples)
    correction.overall = _shrunk(sum(s[2] for s in samples), total_forecast, 1.0, PRIOR_WEIGHT_OVERALL)
    sums: dict[Any, list[float]] = {}
    for local, forecast, actual, cloud in samples:
        for key in (local.hour, (local.hour, cloud)):
            if isinstance(key, tuple) and key[1] is None:
                continue
            entry = sums.setdefault(key, [0.0, 0.0])
            entry[0] += actual
            entry[1] += forecast
    for key, (actual, forecast) in sums.items():
        if isinstance(key, int):
            correction.by_hour[key] = _shrunk(actual, forecast, correction.overall)
    for key, (actual, forecast) in sums.items():
        if isinstance(key, tuple):
            correction.by_hour_cloud[key] = _shrunk(actual, forecast, correction.by_hour.get(key[0], correction.overall))

    # In-sample check of how much closer the corrected forecast comes
    correction.error_before = sum(abs(a - f) for _, f, a, _ in measured) / len(measured)
    correction.error_after = (
        sum(abs(a - f * correction.factor(local.hour, cloud)) for local, f, a, cloud in measured) / len(measured)
    )
    return correction
