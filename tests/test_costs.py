"""Tests for the daily electricity costs."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from sunflow.costs import cost_history
from sunflow.energy import EnergySources

BERLIN = ZoneInfo("Europe/Berlin")


async def test_daily_costs_and_savings() -> None:
    """Yesterday: 4 kWh import at 30 ct, 2 kWh export at 8 ct, 10 kWh consumption."""
    yesterday = (datetime.now(BERLIN) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = [(yesterday + timedelta(hours=h)).astimezone(UTC) for h in range(24)]

    def per_hour(total_wh: float) -> list[dict[str, Any]]:
        return [{"start": h.timestamp() * 1000, "change": total_wh / 24} for h in hours]

    class Client:
        async def command(self, msg_type: str, **data: Any) -> dict[str, list[dict[str, Any]]]:
            if data["statistic_ids"] == ["sensor.price"]:
                # A gap after hour 5: the last price carries on
                return {"sensor.price": [{"start": h.timestamp() * 1000, "state": 0.30} for h in hours[:6]]}
            return {"sensor.import": per_hour(4000), "sensor.export": per_hour(2000), "sensor.load": per_hour(10000)}

    sources = EnergySources(
        statistics={"grid_import": ["sensor.import"], "grid_export": ["sensor.export"], "load": ["sensor.load"]}
    )

    (day,) = await cost_history(Client(), sources, "sensor.price", None, 0.08, 2, BERLIN)

    assert day["import_cost"] == pytest.approx(1.20)
    assert day["export_revenue"] == pytest.approx(0.16)
    assert day["net_cost"] == pytest.approx(1.04)
    assert day["without_pv_cost"] == pytest.approx(3.00)
    assert day["savings"] == pytest.approx(1.96)
    assert day["complete"]
