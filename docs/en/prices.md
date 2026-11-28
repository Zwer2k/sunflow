# Electricity prices

[Deutsch](../strompreise.md) · **English**

Sunflow plans with the import price of every quarter hour. Choose the source under Settings →
Tariff & forecast; all sensors with a unit per kWh are offered.

| Provider / source | Integration in Home Assistant | How Sunflow reads the prices | Price |
|---|---|---|---|
| Tibber | Tibber (Core) | action `tibber.get_prices` for the home of the price sensor | final price incl. taxes |
| Nord Pool | Nord Pool (Core) | action `nordpool.get_prices_for_date` for today and tomorrow, area of the sensor | net spot price |
| Nord Pool | nordpool (HACS) | attributes `raw_today` / `raw_tomorrow` | as configured in the integration |
| aWATTar, smartENERGY, Energy-Charts, SMARD, ENTSO-E, Tibber, Energyforecast | EPEX Spot (HACS) | attribute `data` | sensor "Total Price": final price; "Market Price": net spot price |
| ENTSO-E | ENTSO-e (HACS) | attribute `prices` | as configured in the integration |
| Octopus (dynamic tariffs) | Octopus Germany (HACS) | attribute `rates` (price per quarter hour, today and tomorrow once published); while it is missing, the usual course of the last weeks with a warning | final price |
| Octopus (Go, Heat, Intelligent Go, fixed price) | Octopus Germany (HACS) | attribute `timeslots`, for a fixed price the current value | final price |
| Octopus with variable grid fee (module 3) | Octopus Germany (HACS), sensor "… with Variable Grid Fee" | attribute `price_combinations` (today) | final price |
| Tibber | Tibber Prices (HACS) | action `tibber_prices.get_price` | final price incl. taxes |
| Green Planet Energy | Green Planet Energy (Core) | action `green_planet_energy.get_prices` (24 hours at most) | final price |
| Rabot Energy | Rabot Energy (HACS) | attribute `prices` | final price |
| Ostrom | Ostrom (HACS, oliverwehrens) | attribute `prices` (per hour) | final price |
| 1KOMMA5° | 1KOMMA5° (HACS, BirknerAlex or mrebbert) | attribute `forecast` | final price with grid fee and VAT |
| Swiss tariffs (ElCom) | ElCom Tariffs (HACS) | attributes `raw_today` / `raw_tomorrow` in CHF | final price |
| Energi Data Service | Energi Data Service (HACS) | attributes `raw_today` / `raw_tomorrow` | as configured in the integration |
| others | any price sensor | current value only; if it usually varies over the day, its course of the last weeks | otherwise assumed constant |

Prices in CHF/kWh or Rp/kWh are planned like €/kWh or ct/kWh; the display stays in €. Then also
enter the feed-in rate and wear costs in Rappen.

A pure spot price (Nord Pool Core, EPEX Spot "Market Price") lacks grid fees, levies, the
supplier's fee and VAT. Enter a surcharge in ct/kWh and the VAT for it; Sunflow then calculates
(spot price + surcharge) × (1 + VAT). With EPEX Spot it is easier to choose the "Total Price"
sensor right away and configure the surcharges in the integration.

Prices Sunflow fetches through an action are cached for 15 minutes. Exchanges publish tomorrow's
prices around noon; until then Sunflow plans with what is known.

## Beyond the published prices

Sunflow plans up to 96 hours ahead, but dynamic tariffs only know today and – from around noon –
tomorrow. For the hours after that Sunflow estimates the price: per time of day the average of the
price sensor over the last two weeks (from seven days of history), otherwise the last 24 known
hours repeated. Estimated prices are dashed in the plan and marked as "estimated";
they only shape the end of the plan and are replaced as soon as the real prices arrive. Prices
are only estimated as far as all PV forecasts reach, so that missing PV yield does not distort
the plan. Time-of-use tariffs and fixed prices are known anyway and cover the whole period.
