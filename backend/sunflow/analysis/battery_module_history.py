"""Values of a battery's modules over a day."""

from datetime import datetime, timedelta
from typing import Any

from sunflow.ha import HAClient

from .day_series import combine, slot_means


async def module_day(
    client: HAClient, modules: list[dict[str, Any]], start: datetime, end: datetime, step: timedelta
) -> list[dict[str, Any]]:
    """Per battery module: SoC, current, highest temperature and cell voltage spread per slot, where it has them."""
    wanted = ("soc", "current", "temperature_max", "cell_voltage_max", "cell_voltage_min")
    means = await slot_means(
        client, [e for m in modules for k in wanted for e in m["entities"].get(k, [])], start, end, step
    )
    days = []
    for module in modules:
        def series(key: str, entities: dict[str, list[str]] = module["entities"]) -> list[list[float | None]]:
            return [means[e] for e in entities.get(key, [])]

        day: dict[str, Any] = {"id": module["id"]}
        if values := series("soc"):
            day["soc"] = values[0]
        if values := series("current"):
            day["current"] = values[0]
        if values := series("temperature_max"):
            day["temperature_max"] = combine(values, "max")
        if (high := series("cell_voltage_max")) and (low := series("cell_voltage_min")):
            spread = combine([high[0], low[0]], "difference")
            day["cell_spread_mv"] = [None if v is None else round(v * 1000, 1) for v in spread]
        days.append(day)
    return days
