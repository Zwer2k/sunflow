"""Learned household load: hour of day, weekend and outside temperature.

A weighted ridge regression on the shadow data (measured base load without the
controllable consumers, forecast temperature of the slot):

    load = profile[hour] + weekend[block] + heating * max(T_heat - T, 0) + cooling * max(T - T_cool, 0)

Recent days weigh more (half-life), so the model follows seasons and habits. It is
used only when it beats the simple per hour average on the last days it was not
trained on.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

import numpy as np

MIN_DAYS = 14
HALF_LIFE_DAYS = 21
HOLDOUT_DAYS = 5
HEATING_BELOW_C = 15.0
COOLING_ABOVE_C = 22.0
# Regularization of the weekend and temperature terms only; the hourly levels stay free
RIDGE = 1.0
WEEKEND_BLOCKS = 6  # 4 hour blocks


def _features(local: datetime, temperature: float) -> np.ndarray:
    row = np.zeros(24 + WEEKEND_BLOCKS + 2)
    row[local.hour] = 1.0
    if local.weekday() >= 5:
        row[24 + local.hour // 4] = 1.0
    row[24 + WEEKEND_BLOCKS] = max(HEATING_BELOW_C - temperature, 0.0)
    row[24 + WEEKEND_BLOCKS + 1] = max(temperature - COOLING_ABOVE_C, 0.0)
    return row


@dataclass(kw_only=True)
class LoadModel:
    """Fitted coefficients and how well they did on unseen days."""

    coefficients: np.ndarray
    days: int
    samples: int
    error_model: float
    error_average: float

    @property
    def useful(self) -> bool:
        """Better than the plain average on the held out days."""
        return self.error_model < self.error_average

    @property
    def heating_w_per_k(self) -> float:
        """Extra base load per degree below the heating limit."""
        return float(self.coefficients[24 + WEEKEND_BLOCKS])

    def predict(self, local: datetime, temperature: float) -> float:
        """Base load (W) at a local time and outside temperature."""
        return max(float(_features(local, temperature) @ self.coefficients), 0.0)

    def as_dict(self) -> dict[str, Any]:
        """For the API."""
        return {
            "days": self.days,
            "samples": self.samples,
            "error_model": self.error_model,
            "error_average": self.error_average,
            "useful": self.useful,
            "heating_w_per_k": self.heating_w_per_k,
        }


def _samples(rows: list[dict[str, Any]], tz: tzinfo, threshold: float | None) -> list[tuple[datetime, float, float]]:
    result = []
    for row in rows:
        load, consumers, temperature = row.get("actual_load_w"), row.get("actual_loadpoint_w"), row.get("weather_temp")
        if load is None or consumers is None or temperature is None:
            continue
        base = max(load - consumers, 0.0)
        # Like the statistical forecast: sporadic large loads are not part of the base load
        if threshold is not None and base > threshold:
            continue
        result.append((datetime.fromisoformat(row["start"]).astimezone(tz), temperature, base))
    return result


def _fit(samples: list[tuple[datetime, float, float]], newest: datetime) -> np.ndarray:
    x = np.array([_features(local, temperature) for local, temperature, _ in samples])
    y = np.array([load for _, _, load in samples])
    age = np.array([(newest - local).total_seconds() / 86400 for local, _, _ in samples])
    weights = 0.5 ** (age / HALF_LIFE_DAYS)
    xw = x * weights[:, None]
    penalty = np.full(x.shape[1], RIDGE)
    penalty[:24] = 1e-6
    return np.linalg.solve(x.T @ xw + np.diag(penalty), xw.T @ y)


def _hour_average(samples: list[tuple[datetime, float, float]]) -> dict[int, float]:
    sums: dict[int, list[float]] = {}
    for local, _, load in samples:
        sums.setdefault(local.hour, []).append(load)
    return {hour: sum(values) / len(values) for hour, values in sums.items()}


def fit(rows: list[dict[str, Any]], tz: tzinfo, threshold: float | None = None) -> LoadModel | None:
    """Fit the model; None until there are enough days with temperature and consumer data."""
    samples = sorted(_samples(rows, tz, threshold))
    days = len({local.date() for local, _, _ in samples})
    if days < MIN_DAYS:
        return None
    newest = samples[-1][0]
    split = newest - timedelta(days=HOLDOUT_DAYS)
    train = [s for s in samples if s[0] < split]
    test = [s for s in samples if s[0] >= split]
    coefficients = _fit(train, split)
    average = _hour_average(train)
    error_model = float(np.mean([abs(load - max(float(_features(t, temp) @ coefficients), 0.0)) for t, temp, load in test]))
    error_average = float(np.mean([abs(load - average.get(t.hour, 0.0)) for t, _, load in test]))
    return LoadModel(
        coefficients=_fit(samples, newest),
        days=days,
        samples=len(samples),
        error_model=error_model,
        error_average=error_average,
    )
