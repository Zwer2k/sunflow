"""Electricity price and PV forecast sources read from Home Assistant."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
import itertools
from typing import Any

from sunflow.ha import HAClient, HACommandError

SLOT = timedelta(minutes=15)
# Planned in the price's own currency: Swiss francs (and Rappen) count like euros (and cents)
PRICE_UNITS = {
    "€/kWh": 1.0, "EUR/kWh": 1.0, "ct/kWh": 0.01, "Cent/kWh": 0.01, "€/MWh": 0.001, "EUR/MWh": 0.001,
    "CHF/kWh": 1.0, "Rp/kWh": 0.01, "Rp./kWh": 0.01, "DKK/kWh": 1.0,
}
# Pure exchange prices: grid fee, levies, the provider's fee and VAT still come on top
EXCHANGE_ADAPTERS = {"nordpool_core", "epex_market"}
# Sources without a schedule: only their current price, or none at all right now
NO_SCHEDULE_ADAPTERS = {"fixed", "octopus_unavailable"}
# Prices fetched with an action are asked for again after this long
PRICE_CACHE = timedelta(minutes=15)
_cache: dict[tuple[str, str], tuple[datetime, list[PricePoint]]] = {}


@dataclass(frozen=True, kw_only=True)
class PricePoint:
    """Import price for an interval, in €/kWh."""

    start: datetime
    end: datetime
    price: float
    # Not published yet: estimated from the usual prices of this hour
    estimated: bool = False


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
    # A pure exchange price: the tariff's surcharges and VAT are added
    exchange: bool = False


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


def detect_price_adapter(attributes: dict[str, Any], platform: str | None = None) -> str:
    """Pick how to read the price schedule: from a sensor's attributes or its integration's action."""
    if platform == "tibber":
        return "tibber"
    # Their sensors carry no price list; the integrations answer an action
    if platform in ("tibber_prices", "green_planet_energy"):
        return platform
    # Octopus Germany: a dynamic tariff lists its final price per slot in rates (today, tomorrow
    # once published); its timeslots hold only the fixed part, so without rates nothing is known
    if isinstance(attributes.get("rates"), list) and attributes["rates"] and "value_inc_vat" in attributes["rates"][0]:
        return "octopus_rates"
    if "timeslots" in attributes and "DYNAMIC" in str(attributes.get("code") or "").upper():
        return "octopus_unavailable"
    # Its price with the variable grid fee (module 3): today's prices grouped with their intervals
    if isinstance(attributes.get("price_combinations"), list) and attributes["price_combinations"]:
        return "octopus_combined"
    if isinstance(attributes.get("timeslots"), list) and attributes["timeslots"]:
        return "timeslots"
    # Energi Data Service: raw_today as for Nord Pool, but by hour
    if isinstance(attributes.get("raw_today"), list) and attributes["raw_today"] and "hour" in attributes["raw_today"][0]:
        return "energidataservice"
    # The Nord Pool custom integration lists the prices (as does ElCom for Swiss tariffs); the core one answers an action
    if isinstance(attributes.get("raw_today"), list):
        return "nordpool"
    if platform == "nordpool":
        return "nordpool_core"
    if isinstance(attributes.get("data"), list) and attributes["data"] and "start_time" in attributes["data"][0]:
        # EPEX Spot: "Market Price" (the net exchange price) also has the current one as price_per_kwh,
        # "Total Price" (with surcharges and tax) only the list
        return "epex_market" if "price_per_kwh" in attributes else "epex_spot"
    if isinstance(attributes.get("prices"), list) and attributes["prices"] and "time" in attributes["prices"][0]:
        return "entsoe"
    # Rabot Energy: [{start, end, price}]
    if isinstance(attributes.get("prices"), list) and attributes["prices"] and {"start", "price"} <= attributes["prices"][0].keys():
        return "price_list"
    # Ostrom: {local start: price} per hour
    if isinstance(attributes.get("prices"), dict) and attributes["prices"]:
        return "price_dict"
    # 1KOMMA5°: [{start, end, price}] or [{datetime, price}], future slots only
    forecast = attributes.get("forecast")
    if isinstance(forecast, list) and forecast and "price" in forecast[0] and ("start" in forecast[0] or "datetime" in forecast[0]):
        return "forecast_list"
    return "fixed"


def is_grid_fee_only(attributes: dict[str, Any]) -> bool:
    """Octopus Germany's variable grid fee alone (net, no energy price): no import price."""
    rates = attributes.get("rates")
    return isinstance(rates, list) and bool(rates) and "rate_eur_per_kwh" in rates[0] and "value_inc_vat" not in rates[0]


def price_sources(states: list[dict[str, Any]], registry: dict[str, dict[str, Any]] | None = None) -> list[PriceSource]:
    """Return all entities that look like an electricity price; registry: entity id -> registry entry."""
    result = []
    for state in states:
        attributes = state.get("attributes", {})
        factor = price_factor(attributes.get("unit_of_measurement"))
        if factor is None or not state["entity_id"].startswith("sensor.") or is_grid_fee_only(attributes):
            continue
        try:
            current = float(state["state"]) * factor
        except TypeError, ValueError:
            current = None
        adapter = detect_price_adapter(attributes, (registry or {}).get(state["entity_id"], {}).get("platform"))
        result.append(
            PriceSource(
                entity_id=state["entity_id"],
                name=attributes.get("friendly_name", state["entity_id"]),
                adapter=adapter,
                current=current,
                exchange=adapter in EXCHANGE_ADAPTERS,
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


def octopus_rates(attributes: dict[str, Any]) -> list[PricePoint]:
    """Read Octopus Germany's rates (value_inc_vat in €/kWh); unsorted and with mixed UTC offsets."""
    points = {}
    for item in attributes["rates"]:
        if item.get("value_inc_vat") is None:
            continue
        start = datetime.fromisoformat(item["start"])
        points[start] = PricePoint(start=start, end=datetime.fromisoformat(item["end"]), price=float(item["value_inc_vat"]))
    return [points[start] for start in sorted(points)]


def octopus_combined(attributes: dict[str, Any]) -> list[PricePoint]:
    """Read Octopus Germany's price with the variable grid fee: each price with its intervals of today."""
    points = [
        PricePoint(
            start=datetime.fromisoformat(interval["start"]),
            end=datetime.fromisoformat(interval["end"]),
            price=float(combination["combined_price_gross_eur_per_kwh"]),
        )
        for combination in attributes["price_combinations"]
        if combination.get("combined_price_gross_eur_per_kwh") is not None
        for interval in combination.get("intervals", [])
    ]
    return sorted(points, key=lambda p: p.start)


def listed_prices(items: list[dict[str, Any]], factor: float) -> list[PricePoint]:
    """Prices as [{start, end, price}], or by start only ({datetime, price}): each until the next."""
    items = [i for i in items if i.get("price") is not None]
    if all("end" in i for i in items):
        return sorted(
            (PricePoint(start=datetime.fromisoformat(i["start"]), end=datetime.fromisoformat(i["end"]), price=float(i["price"]) * factor) for i in items),
            key=lambda p: p.start,
        )
    return with_ends([(datetime.fromisoformat(i.get("start") or i["datetime"]), float(i["price"]) * factor) for i in items])


def with_ends(starts: list[tuple[datetime, float]]) -> list[PricePoint]:
    """Prices given by start only: each lasts until the next, the last as long as the one before (else an hour)."""
    starts = sorted(starts)
    points = []
    for index, (start, price) in enumerate(starts):
        if index + 1 < len(starts):
            end = starts[index + 1][0]
        else:
            end = start + (start - starts[index - 1][0] if index else timedelta(hours=1))
        points.append(PricePoint(start=start, end=end, price=price))
    return points


def entsoe_prices(attributes: dict[str, Any], factor: float) -> list[PricePoint]:
    """Read the ENTSO-e integration's prices list ({time, price})."""
    return with_ends(
        [(datetime.fromisoformat(str(item["time"])), float(item["price"]) * factor) for item in attributes["prices"] if item.get("price") is not None]
    )


def started_now(points: list[PricePoint], state: dict[str, Any], start: datetime, factor: float) -> list[PricePoint]:
    """A list of future slots only: the current price (the state) until the first of them."""
    if not points or points[0].start <= start:
        return points
    try:
        price = float(state["state"]) * factor
    except TypeError, ValueError:
        return points
    return [PricePoint(start=start, end=points[0].start, price=price), *points]


def tibber_points(response: dict[str, Any], home: str | None) -> list[PricePoint]:
    """Prices of one home from the answer of tibber.get_prices (the first home if it is not named)."""
    homes = response.get("prices") or {}
    items = homes.get(home) if home in homes else next(iter(homes.values()), [])
    return with_ends([(datetime.fromisoformat(str(i["start_time"])), float(i["price"])) for i in items if i.get("price") is not None])


def nordpool_points(response: dict[str, Any], area: str | None) -> list[PricePoint]:
    """Prices of one area from the answer of nordpool.get_prices_for_date (€/MWh to €/kWh)."""
    items = response.get(area) if area in response else next(iter(response.values()), [])
    return [
        PricePoint(start=datetime.fromisoformat(i["start"]), end=datetime.fromisoformat(i["end"]), price=float(i["price"]) / 1000)
        for i in items or []
        if i.get("price") is not None
    ]


def is_exchange(state: dict[str, Any], entry: dict[str, Any] | None) -> bool:
    """Whether the entity gives a pure exchange price (surcharges and VAT still to be added)."""
    return detect_price_adapter(state.get("attributes", {}), (entry or {}).get("platform")) in EXCHANGE_ADAPTERS


def with_surcharges(points: list[PricePoint], surcharge_ct: float | None, vat_pct: float | None) -> list[PricePoint]:
    """An exchange price as paid: (price + surcharges) x (1 + VAT)."""
    add, vat = (surcharge_ct or 0.0) / 100, 1 + (vat_pct or 0.0) / 100
    return [PricePoint(start=p.start, end=p.end, price=(p.price + add) * vat) for p in points]


async def _cached_prices(key: tuple[str, str], fetch: Any) -> list[PricePoint]:
    now = datetime.now(UTC)
    if (hit := _cache.get(key)) is not None and now - hit[0] < PRICE_CACHE:
        return hit[1]
    points = await fetch()
    # The key holds the slot's start: without this every quarter hour would leave a price list behind
    for old in [k for k, (fetched, _) in _cache.items() if now - fetched >= PRICE_CACHE]:
        del _cache[old]
    _cache[key] = (now, points)
    return points


async def fetch_prices(
    client: HAClient, state: dict[str, Any], entry: dict[str, Any] | None, start: datetime, hours: int, tz: tzinfo,
    surcharge_ct: float | None = None, vat_pct: float | None = None,
) -> list[PricePoint]:
    """The import price schedule of a price entity from start on, also from its integration's action.

    entry: the entity's registry entry (platform, config entry, unique id). Exchange prices get
    the surcharges and VAT.
    """
    entry = entry or {}
    attributes = state.get("attributes", {})
    adapter = detect_price_adapter(attributes, entry.get("platform"))
    end = start + timedelta(hours=hours)
    match adapter:
        case "tibber":

            async def tibber() -> list[PricePoint]:
                response = await client.call_service(
                    "tibber", "get_prices", {"start": start.isoformat(), "end": end.isoformat()}, return_response=True
                )
                return tibber_points(response["response"], attributes.get("app_nickname"))

            points = await _cached_prices((state["entity_id"], start.isoformat()), tibber)
        case "tibber_prices":

            async def tibber_prices() -> list[PricePoint]:
                response = await client.call_service(
                    "tibber_prices", "get_price",
                    {"entry_id": entry.get("config_entry_id") or "", "start_time": start.isoformat(), "end_time": end.isoformat()},
                    return_response=True,
                )
                answer = response["response"]
                # total in the currency per kWh, whatever unit the sensors show
                return with_ends([
                    (datetime.fromisoformat(str(i["startsAt"])), float(i["total"]))
                    for i in answer.get("price_info") or [] if i.get("total") is not None
                ]) if answer.get("success") else []

            points = await _cached_prices((state["entity_id"], start.isoformat()), tibber_prices)
        case "green_planet_energy":

            async def green_planet() -> list[PricePoint]:
                # At most 24 hours, today and tomorrow as far as published; €/kWh
                response = await client.call_service(
                    "green_planet_energy", "get_prices",
                    {"config_entry_id": entry.get("config_entry_id"), "hours": min(hours, 24)}, return_response=True,
                )
                return listed_prices(response["response"].get("prices") or [], 1.0)

            points = await _cached_prices((state["entity_id"], start.isoformat()), green_planet)
        case "nordpool_core":
            # The unique id is "<area>-current_price"
            area = (entry.get("unique_id") or "").split("-")[0] or None

            async def nordpool() -> list[PricePoint]:
                first, last = start.astimezone(tz).date(), (end - timedelta(seconds=1)).astimezone(tz).date()
                days = [first + timedelta(days=d) for d in range((last - first).days + 1)]
                points = []
                for day in days[:2]:
                    try:
                        response = await client.call_service(
                            "nordpool", "get_prices_for_date",
                            {"config_entry": entry.get("config_entry_id"), "date": day.isoformat()}, return_response=True,
                        )
                    except HACommandError:
                        # Tomorrow's prices are published around noon
                        continue
                    points += nordpool_points(response["response"], area)
                return points

            points = await _cached_prices((state["entity_id"], start.isoformat()), nordpool)
        case _:
            points = price_series(state, start, hours, tz)
    points = [p for p in points if p.end > start and p.start < end]
    return with_surcharges(points, surcharge_ct, vat_pct) if adapter in EXCHANGE_ADAPTERS else points


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
        case "octopus_rates":
            points = octopus_rates(attributes)
        case "octopus_combined":
            points = octopus_combined(attributes)
        case "octopus_unavailable":
            return []
        case "nordpool":
            points = nordpool_prices(attributes, factor)
        case "epex_spot" | "epex_market":
            points = epex_spot_prices(attributes)
        case "entsoe":
            points = entsoe_prices(attributes, factor)
        case "energidataservice":
            points = with_ends([
                (datetime.fromisoformat(str(i["hour"])), float(i["price"]) * factor)
                for i in [*attributes["raw_today"], *(attributes.get("raw_tomorrow") or [])] if i.get("price") is not None
            ])
        case "price_list":
            points = listed_prices(attributes["prices"], factor)
        case "price_dict":
            points = with_ends([(datetime.fromisoformat(k), float(v) * factor) for k, v in attributes["prices"].items() if v is not None])
        case "forecast_list":
            points = started_now(listed_prices(attributes["forecast"], factor), state, start, factor)
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
    """Add forecasts per interval (e.g. several planes of one plant), on a 30 minute grid.

    Planes without an uncertainty range (Forecast.Solar) get the relative range of the
    planes that have one (Solcast) in the same interval, else none (p10 = p90 = p50).
    """
    # power and p10/p90 of planes with a range, power of planes without
    totals: dict[tuple[datetime, datetime], list[float]] = {}
    for points in series:
        for p in resample(points, timedelta(minutes=30)):
            total = totals.setdefault((p.start, p.end), [0.0, 0.0, 0.0, 0.0])
            if p.power10 is not None and p.power90 is not None:
                total[0] += p.power
                total[1] += p.power10
                total[2] += p.power90
            else:
                total[3] += p.power
    result = []
    for (start, end), (ranged, low, high, plain) in sorted(totals.items()):
        low_ratio = low / ranged if ranged > 0 else 1.0
        high_ratio = high / ranged if ranged > 0 else 1.0
        result.append(
            ForecastPoint(
                start=start,
                end=end,
                power=ranged + plain,
                power10=low + plain * low_ratio,
                power90=high + plain * high_ratio,
            )
        )
    return result
