"""Electricity price and PV forecast sources read from Home Assistant."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
import itertools
from typing import Any

from .ha import HAClient

SLOT = timedelta(minutes=15)
PRICE_UNITS = {"€/kWh": 1.0, "EUR/kWh": 1.0, "ct/kWh": 0.01, "Cent/kWh": 0.01}


@dataclass(frozen=True, kw_only=True)
class PricePoint:
    """Import price for an interval, in €/kWh."""

    start: datetime
    end: datetime
    price: float


@dataclass(frozen=True, kw_only=True)
class ForecastPoint:
    """Average PV power forecast for an interval, in W."""

    start: datetime
    end: datetime
    power: float
    power10: float | None = None
    power90: float | None = None


@dataclass(frozen=True, kw_only=True)
class PriceSource:
    """An entity usable as import price."""

    entity_id: str
    name: str
    adapter: str
    current: float | None


@dataclass(frozen=True, kw_only=True)
class ForecastSource:
    """A PV forecast plane, e.g. one Solcast site."""

    source: str
    ref: str
    name: str
    capacity_kw: float | None
    entity_id: str | None
    # Today's forecast, shown to tell unnamed planes apart
    today_kwh: float | None = None


def price_factor(unit: str | None) -> float | None:
    """Return the factor to €/kWh, None if the unit is not a price per kWh."""
    return PRICE_UNITS.get(unit or "")


def detect_price_adapter(attributes: dict[str, Any]) -> str:
    """Pick how to read the price schedule from a sensor's attributes."""
    if isinstance(attributes.get("timeslots"), list) and attributes["timeslots"]:
        return "timeslots"
    if isinstance(attributes.get("raw_today"), list):
        return "nordpool"
    if isinstance(attributes.get("data"), list) and attributes["data"] and "start_time" in attributes["data"][0]:
        return "epex_spot"
    return "fixed"


def price_sources(states: list[dict[str, Any]]) -> list[PriceSource]:
    """Return all entities that look like an electricity price."""
    result = []
    for state in states:
        attributes = state.get("attributes", {})
        factor = price_factor(attributes.get("unit_of_measurement"))
        if factor is None or not state["entity_id"].startswith("sensor."):
            continue
        try:
            current = float(state["state"]) * factor
        except TypeError, ValueError:
            current = None
        result.append(
            PriceSource(
                entity_id=state["entity_id"],
                name=attributes.get("friendly_name", state["entity_id"]),
                adapter=detect_price_adapter(attributes),
                current=current,
            )
        )
    return result


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _covers(rule: dict[str, str], moment: time) -> bool:
    start, end = _parse_time(rule["from_time"]), _parse_time(rule["to_time"])
    if start < end:
        return start <= moment < end
    # Wraps midnight; "05:00 -> 00:00" means until the end of the day
    return moment >= start or moment < end


def timeslot_prices(
    attributes: dict[str, Any], start: datetime, hours: int, tz: tzinfo
) -> list[PricePoint]:
    """Expand time-of-use slots (e.g. Octopus Go) into 15 minute prices.

    Activation rules are local wall-clock times; rates are in ct/kWh.
    """
    slots = attributes["timeslots"]
    points = []
    # Step in UTC: adding to a local aware datetime is wall-clock arithmetic and breaks at DST changes
    moment = start.astimezone(UTC)
    for _ in range(hours * 4):
        local = moment.astimezone(tz).time()
        slot = next(
            (s for s in slots if any(_covers(r, local) for r in s.get("activation_rules", []))),
            None,
        )
        if slot is not None:
            points.append(
                PricePoint(
                    start=moment.astimezone(tz),
                    end=(moment + SLOT).astimezone(tz),
                    price=float(slot["rate"]) / 100,
                )
            )
        moment += SLOT
    return points


def nordpool_prices(attributes: dict[str, Any], factor: float) -> list[PricePoint]:
    """Read Nord Pool style raw_today / raw_tomorrow lists."""
    points = []
    for item in [*attributes.get("raw_today", []), *(attributes.get("raw_tomorrow") or [])]:
        if item.get("value") is None:
            continue
        points.append(
            PricePoint(
                start=datetime.fromisoformat(str(item["start"])),
                end=datetime.fromisoformat(str(item["end"])),
                price=float(item["value"]) * factor,
            )
        )
    return points


def epex_spot_prices(attributes: dict[str, Any]) -> list[PricePoint]:
    """Read EPEX Spot style data lists (price_per_kwh in €/kWh)."""
    return [
        PricePoint(
            start=datetime.fromisoformat(item["start_time"]),
            end=datetime.fromisoformat(item["end_time"]),
            price=float(item["price_per_kwh"]),
        )
        for item in attributes["data"]
        if item.get("price_per_kwh") is not None
    ]


def price_series(
    state: dict[str, Any], start: datetime, hours: int, tz: tzinfo
) -> list[PricePoint]:
    """Return the import price schedule of a price entity from start on."""
    attributes = state.get("attributes", {})
    factor = price_factor(attributes.get("unit_of_measurement")) or 1.0
    end = start + timedelta(hours=hours)
    match detect_price_adapter(attributes):
        case "timeslots":
            return timeslot_prices(attributes, start, hours, tz)
        case "nordpool":
            points = nordpool_prices(attributes, factor)
        case "epex_spot":
            points = epex_spot_prices(attributes)
        case _:
            try:
                price = float(state["state"]) * factor
            except TypeError, ValueError:
                return []
            # Only the current price is known; assume it holds for the horizon
            return [PricePoint(start=start, end=end, price=price)]
    return [p for p in points if p.end > start and p.start < end]


def solcast_sites(states: list[dict[str, Any]], platform_entities: set[str]) -> list[ForecastSource]:
    """Return Solcast sites from their per-site sensors."""
    sites = []
    for state in states:
        attributes = state.get("attributes", {})
        if state["entity_id"] not in platform_entities or "resource_id" not in attributes:
            continue
        sites.append(
            ForecastSource(
                source="solcast",
                ref=attributes["resource_id"],
                name=attributes.get("name", state["entity_id"]),
                capacity_kw=attributes.get("capacity"),
                entity_id=state["entity_id"],
                today_kwh=_float(state.get("state")),
            )
        )
    return sites


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def solcast_points(data: list[dict[str, Any]], period: timedelta) -> list[ForecastPoint]:
    """Convert Solcast periods (average kW) to forecast points in W."""
    points = []
    for item in data:
        start = datetime.fromisoformat(str(item["period_start"]))
        points.append(
            ForecastPoint(
                start=start,
                end=start + period,
                power=float(item["pv_estimate"]) * 1000,
                power10=float(item["pv_estimate10"]) * 1000 if "pv_estimate10" in item else None,
                power90=float(item["pv_estimate90"]) * 1000 if "pv_estimate90" in item else None,
            )
        )
    return points


async def solcast_forecast(
    client: HAClient, site: str, start: datetime, hours: int
) -> list[ForecastPoint]:
    """Query the forecast of one Solcast site via its action."""
    response = await client.call_service(
        "solcast_solar",
        "query_forecast_data",
        {
            "start_date_time": start.isoformat(),
            "end_date_time": (start + timedelta(hours=hours)).isoformat(),
            "site": site,
        },
        return_response=True,
    )
    return solcast_points(response["response"]["data"], timedelta(minutes=30))


FORECAST_SOLAR_DEVICE_PREFIX = "Solar production forecast "


def forecast_solar_planes(
    entries: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    entities: list[dict[str, Any]] | None = None,
    states: dict[str, dict[str, Any]] | None = None,
) -> list[ForecastSource]:
    """Return one plane per Forecast.Solar config entry.

    Named after its device without the generic "Solar production forecast",
    else the entry title, else numbered.
    """
    planes = []
    for number, entry in enumerate(
        (e for e in entries if e["domain"] == "forecast_solar" and e["state"] == "loaded"), start=1
    ):
        device = next((d for d in devices if entry["entry_id"] in d.get("config_entries", [])), None)
        name = (device or {}).get("name_by_user") or (device or {}).get("name") or ""
        name = name.removeprefix(FORECAST_SOLAR_DEVICE_PREFIX.strip()).strip(" -–")
        name = name or entry["title"] or f"Forecast.Solar {number}"
        today = next(
            (
                e["entity_id"]
                for e in entities or []
                if e.get("config_entry_id") == entry["entry_id"]
                and (e.get("unique_id") or "").endswith("energy_production_today")
            ),
            None,
        )
        planes.append(
            ForecastSource(
                source="forecast_solar",
                ref=entry["entry_id"],
                name=name,
                capacity_kw=None,
                entity_id=today,
                today_kwh=_float((states or {}).get(today, {}).get("state")) if today else None,
            )
        )
    return planes


def wh_period_points(wh_period: dict[str, float], period: timedelta) -> list[ForecastPoint]:
    """Convert energy per interval (Wh, keyed by interval start) to average power in W."""
    hours = period / timedelta(hours=1)
    points = []
    for key, wh in sorted(wh_period.items()):
        start = datetime.fromisoformat(key)
        points.append(ForecastPoint(start=start, end=start + period, power=float(wh) / hours))
    return points


def period_end_points(wh_period: dict[str, float], step: timedelta = timedelta(minutes=30)) -> list[ForecastPoint]:
    """Convert Forecast.Solar energies to average power on a regular grid.

    The API's watt_hours_period is the energy of the interval that ENDS at the
    timestamp (it equals the trapezoid of the watts between the previous and this
    timestamp; verified 2026-10-25). Home Assistant's hourly aggregation files it
    under the hour that starts there, which shifts the forecast one hour late, so
    the raw series is spread over the grid here instead.
    """
    entries = sorted((datetime.fromisoformat(k), float(v)) for k, v in wh_period.items())
    energy: dict[datetime, float] = {}
    for (begin, _), (end, wh) in itertools.pairwise(entries):
        span = (end - begin).total_seconds()
        if span <= 0 or wh <= 0:
            continue
        # Spread evenly over the grid cells the interval overlaps
        cell = begin - (begin - begin.replace(minute=0, second=0, microsecond=0)) % step
        while cell < end:
            overlap = (min(cell + step, end) - max(cell, begin)).total_seconds()
            energy[cell] = energy.get(cell, 0.0) + wh * overlap / span
            cell += step
    hours = step / timedelta(hours=1)
    return [ForecastPoint(start=cell, end=cell + step, power=wh / hours) for cell, wh in sorted(energy.items())]


async def forecast_solar_forecast(
    client: HAClient, entry_id: str, start: datetime, hours: int
) -> list[ForecastPoint]:
    """Query one Forecast.Solar entry via its get_forecast action (raw timestamps)."""
    response = await client.call_service(
        "forecast_solar",
        "get_forecast",
        {
            "config_entry": entry_id,
            # One extra hour so the interval ending at the first timestamp is complete
            "start": (start - timedelta(hours=1)).isoformat(),
            "end": (start + timedelta(hours=hours)).isoformat(),
            "resolution": "raw",
        },
        return_response=True,
    )
    return [p for p in period_end_points(response["response"]["wh_period"]) if p.end > start]


def resample(points: list[ForecastPoint], step: timedelta) -> list[ForecastPoint]:
    """Split intervals into steps of equal average power so sources of different resolution add up."""
    result = []
    for p in points:
        start = p.start
        while start < p.end:
            end = min(start + step, p.end)
            result.append(
                ForecastPoint(start=start, end=end, power=p.power, power10=p.power10, power90=p.power90)
            )
            start = end
    return result


def sum_forecasts(series: list[list[ForecastPoint]]) -> list[ForecastPoint]:
    """Add forecasts per interval (e.g. several planes of one plant), on a 30 minute grid."""
    totals: dict[tuple[datetime, datetime], list[float]] = {}
    for points in series:
        for p in resample(points, timedelta(minutes=30)):
            total = totals.setdefault((p.start, p.end), [0.0, 0.0, 0.0])
            total[0] += p.power
            total[1] += p.power10 or 0
            total[2] += p.power90 or 0
    return [
        ForecastPoint(start=s, end=e, power=v[0], power10=v[1], power90=v[2])
        for (s, e), v in sorted(totals.items())
    ]
