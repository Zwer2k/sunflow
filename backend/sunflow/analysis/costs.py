"""Electricity costs per day from the energy counters and the price history.

Per hour: import x price, export x feed-in rate, and what the whole consumption
would have cost from the grid (no PV, no battery). The difference is what PV and
battery saved that day.
"""

from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from sunflow.ha import HAClient

from .energy import EnergySources

HOUR_MS = 3_600_000


def _hour(row: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(row["start"] / 1000, UTC)


async def cost_history(
    client: HAClient,
    sources: EnergySources,
    price_entity: str | None,
    fixed_price: float | None,
    feed_in: float,
    days: int,
    tz: tzinfo,
) -> list[dict[str, Any]]:
    """Daily import cost, export revenue, net cost and cost without PV and battery (€)."""
    end = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    start = (end - timedelta(days=days)).replace(hour=0)
    keys = ("grid_import", "grid_export", "load")
    ids = [s for key in keys for s in sources.statistics.get(key, [])]
    if not ids or (price_entity is None and fixed_price is None):
        return []
    energy = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=sorted(set(ids)),
        period="hour",
        types=["change"],
        units={"energy": "Wh"},
    )
    per_hour: dict[str, dict[datetime, float]] = {key: {} for key in keys}
    for key in keys:
        for statistic in sources.statistics.get(key, []):
            for row in energy.get(statistic, []):
                if row.get("change") is not None:
                    hour = _hour(row)
                    per_hour[key][hour] = per_hour[key].get(hour, 0.0) + row["change"]

    prices: dict[datetime, float] = {}
    if price_entity:
        # Price sensors are "total" or "measurement"; the hourly state is the price at the end
        result = await client.command(
            "recorder/statistics_during_period",
            start_time=(start - timedelta(days=1)).isoformat(),
            end_time=end.isoformat(),
            statistic_ids=[price_entity],
            period="hour",
            types=["state", "mean"],
        )
        for row in result.get(price_entity, []):
            value = row.get("mean") if row.get("mean") is not None else row.get("state")
            if value is not None:
                prices[_hour(row)] = value
    known = sorted(prices)

    def price_at(hour: datetime) -> float | None:
        if fixed_price is not None and not prices:
            return fixed_price
        # Hours without a statistic (Home Assistant was down) keep the last known price
        earlier = [h for h in known if h <= hour]
        return prices[earlier[-1]] if earlier else None

    daily: dict[date, dict[str, float]] = {}
    for hour in sorted(set(per_hour["grid_import"]) | set(per_hour["grid_export"])):
        price = price_at(hour)
        if price is None:
            continue
        day = hour.astimezone(tz).date()
        entry = daily.setdefault(day, {"import": 0.0, "export": 0.0, "without": 0.0, "hours": 0})
        entry["import"] += per_hour["grid_import"].get(hour, 0.0) / 1000 * price
        entry["export"] += per_hour["grid_export"].get(hour, 0.0) / 1000 * feed_in
        # Without PV and battery the whole consumption would come from the grid
        entry["without"] += per_hour["load"].get(hour, 0.0) / 1000 * price
        entry["hours"] += 1
    has_load = bool(per_hour["load"])
    return [
        {
            "date": day.isoformat(),
            "import_cost": entry["import"],
            "export_revenue": entry["export"],
            "net_cost": entry["import"] - entry["export"],
            "without_pv_cost": entry["without"] if has_load else None,
            "savings": entry["without"] - (entry["import"] - entry["export"]) if has_load else None,
            "complete": entry["hours"] >= 23,
        }
        for day, entry in sorted(daily.items())
    ]
