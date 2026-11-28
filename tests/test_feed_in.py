"""Tests for the feed-in by exchange price: solar peak law and direct marketing."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sunflow.config.models import DeviceConfig, FeedIn, SunflowConfig, TariffConfig
from sunflow.devices.discovery import Snapshot
from sunflow.planning import plan
from sunflow.planning.optimizer import SlotInput
from sunflow.planning.plan import exchange_prices, feed_in_of, slot_feed_in
from sunflow.planning.sources import PricePoint

START = datetime(2026, 5, 10, 11, tzinfo=UTC)
SLOT = timedelta(minutes=15)
# Import price (a final price, always positive) and the exchange price of three slots
IMPORT = [0.25, 0.18, 0.22]
MARKET = [0.06, -0.02, None]


def _inputs() -> list[SlotInput]:
    return [SlotInput(start=START + SLOT * i, pv_w=3000, load_w=400, price=price, feed_in=0.08) for i, price in enumerate(IMPORT)]


def _exchange() -> list[PricePoint]:
    return [PricePoint(start=START + SLOT * i, end=START + SLOT * (i + 1), price=p) for i, p in enumerate(MARKET) if p is not None]


@pytest.mark.parametrize(
    ("model", "rate", "exchange", "expected"),
    [
        pytest.param("eeg_fixed", 8.0, True, [0.08, 0.08, 0.08], id="eeg-fixed"),
        # Nothing while the exchange price is negative, though the import price is not
        pytest.param("solar_peak", 8.0, True, [0.08, 0.0, 0.08], id="solar-peak"),
        # Without an exchange price only a negative import price tells
        pytest.param("solar_peak", 8.0, False, [0.08, 0.08, 0.08], id="solar-peak-without-exchange"),
        # Exchange price plus market premium; at a negative price no premium; unknown: the rate as fixed
        pytest.param("direct", 2.0, True, [0.08, -0.02, 0.08], id="direct"),
        pytest.param("direct", 2.0, False, [0.08, 0.08, 0.08], id="direct-without-exchange"),
    ],
)
def test_feed_in_per_slot(model: str, rate: float, exchange: bool, expected: list[float]) -> None:
    """The feed-in follows the net exchange price as the model says."""
    feed_in = FeedIn(model=model, rate_ct_per_kwh=rate)  # type: ignore[arg-type]

    result = slot_feed_in(_inputs(), feed_in, _exchange() if exchange else None)

    assert [round(slot.feed_in, 4) for slot in result] == expected


def test_direct_marketing_needs_no_rate() -> None:
    """Direct marketing pays the exchange price alone when no premium is entered."""
    device = DeviceConfig(id="inv", name="WR", ha_device_id="ha", profile="p", profile_version=1)
    config = SunflowConfig(devices=[device], feed_in={"inv": FeedIn(model="direct")})

    assert feed_in_of(config, device) == FeedIn(model="direct")


@pytest.mark.parametrize(
    ("tariff", "fetched_from", "found"),
    [
        pytest.param(TariffConfig(import_entity_id="sensor.tibber", exchange_entity_id="sensor.epex_market"),
                     [("sensor.epex_market", None, None)], True, id="chosen"),
        # An exchange import price is the market price itself (before surcharges)
        pytest.param(TariffConfig(import_entity_id="sensor.epex_market", exchange_surcharge_ct=17.0),
                     [("sensor.epex_market", None, None)], True, id="import-is-exchange"),
        pytest.param(TariffConfig(import_entity_id="sensor.tibber"), [], False, id="none"),
    ],
)
async def test_exchange_price_source(
    monkeypatch: pytest.MonkeyPatch, tariff: TariffConfig, fetched_from: list[tuple[str, None, None]], found: bool
) -> None:
    """The chosen exchange price, else the import price if it is one; never with surcharges."""
    fetched: list[tuple[str, Any, Any]] = []

    async def fetch_prices(client: object, state: dict[str, Any], entry: object, start: datetime, hours: int, tz: object,
                           surcharge_ct: float | None = None, vat_pct: float | None = None) -> list[PricePoint]:
        fetched.append((state["entity_id"], surcharge_ct, vat_pct))
        return [PricePoint(start=start, end=start + timedelta(hours=hours), price=0.05)]

    monkeypatch.setattr(plan, "fetch_prices", fetch_prices)
    market = {"entity_id": "sensor.epex_market", "state": "0.05",
              "attributes": {"unit_of_measurement": "€/kWh", "price_per_kwh": 0.05, "data": [{"start_time": "x"}]}}
    tibber = {"entity_id": "sensor.tibber", "state": "0.3", "attributes": {"unit_of_measurement": "€/kWh"}}
    snapshot = Snapshot(entries=[], devices=[], entities=[], states={s["entity_id"]: s for s in (market, tibber)}, energy_prefs=None)

    points = await exchange_prices(object(), snapshot, SunflowConfig(tariff=tariff), START, UTC)  # type: ignore[arg-type]

    assert fetched == fetched_from
    assert (points is not None) is found
