"""Learn when and how much consumers that are only measured run (stove, washing machine …).

From the hourly means of the power sensor (long-term statistics): energy per day, on how
many days the consumer runs, and its usual hours on workdays and at the weekend. Sunflow
does not switch these consumers; what is learned only describes them for now.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from sunflow.ha import HAClient

HISTORY_DAYS = 56
MIN_DAYS = 7
# A day with less is a day the consumer did not run (standby only)
ACTIVE_DAY_WH = 50.0
# The usual hour counts when the consumer used at least this much there on average
USUAL_HOUR_WH = 20.0


@dataclass(kw_only=True)
class ApplianceUsage:
    """What one measured consumer usually uses."""

    days: int = 0
    daily_wh: float = 0.0
    active_share: float = 0.0
    # Mean energy (Wh) per hour of the day, for workdays and for the weekend
    workday: list[float] = field(default_factory=lambda: [0.0] * 24)
    weekend: list[float] = field(default_factory=lambda: [0.0] * 24)

    @staticmethod
    def _usual(profile: list[float]) -> int | None:
        hour = max(range(24), key=lambda h: profile[h])
        return hour if profile[hour] >= USUAL_HOUR_WH else None

    def as_dict(self) -> dict[str, Any]:
        """For the API."""
        return {
            "days": self.days,
            "daily_wh": round(self.daily_wh),
            "active_share": round(self.active_share, 2),
            "usual_workday_hour": self._usual(self.workday),
            "usual_weekend_hour": self._usual(self.weekend),
            "workday": [round(w, 1) for w in self.workday],
            "weekend": [round(w, 1) for w in self.weekend],
        }


def usage_from(hours: list[tuple[datetime, float]], tz: tzinfo) -> ApplianceUsage | None:
    """Usage from hourly mean powers (W, so Wh per hour); None with less than a week of data."""
    per_day: dict[date, dict[int, float]] = {}
    for start, watts in hours:
        local = start.astimezone(tz)
        per_day.setdefault(local.date(), {})[local.hour] = max(watts, 0.0)
    # Only whole days: a day cut by the start of the history or by now would count too little
    full = {day: values for day, values in per_day.items() if len(values) >= 23}
    if len(full) < MIN_DAYS:
        return None
    usage = ApplianceUsage(days=len(full))
    totals = [sum(values.values()) for values in full.values()]
    usage.daily_wh = sum(totals) / len(totals)
    usage.active_share = sum(1 for total in totals if total >= ACTIVE_DAY_WH) / len(totals)
    for weekend in (False, True):
        days = [values for day, values in full.items() if (day.weekday() >= 5) == weekend]
        if days:
            profile = [sum(values.get(h, 0.0) for values in days) / len(days) for h in range(24)]
            if weekend:
                usage.weekend = profile
            else:
                usage.workday = profile
    return usage


async def learn_usage(client: HAClient, entity_id: str, tz: tzinfo) -> ApplianceUsage | None:
    """Usage over the last weeks from the statistics of the power sensor."""
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=(end - timedelta(days=HISTORY_DAYS)).isoformat(),
        end_time=end.isoformat(),
        statistic_ids=[entity_id],
        period="hour",
        types=["mean"],
        units={"power": "W"},
    )
    hours = [
        (datetime.fromtimestamp(row["start"] / 1000, UTC), row["mean"])
        for row in result.get(entity_id, [])
        if row.get("mean") is not None
    ]
    return usage_from(hours, tz)
