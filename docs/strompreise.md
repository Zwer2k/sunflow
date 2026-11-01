# Strompreise

Sunflow plant mit dem Bezugspreis jeder Viertelstunde. Die Quelle wählst du unter
Einstellungen → Tarif & Prognose; angeboten werden alle Sensoren mit einer Einheit pro kWh.

| Anbieter / Quelle | Integration in Home Assistant | Wie Sunflow die Preise liest | Preis |
|---|---|---|---|
| Tibber | Tibber (Core) | Aktion `tibber.get_prices` für den Haushalt des Preissensors | Endpreis inkl. Steuern |
| Nord Pool | Nord Pool (Core) | Aktion `nordpool.get_prices_for_date` für heute und morgen, Gebiet des Sensors | Börsenpreis netto |
| Nord Pool | nordpool (HACS) | Attribute `raw_today` / `raw_tomorrow` | wie in der Integration eingestellt |
| aWATTar, smartENERGY, Energy-Charts, SMARD, ENTSO-E, Tibber, Energyforecast | EPEX Spot (HACS) | Attribut `data` | Sensor „Total Price“: Endpreis; „Market Price“: Börsenpreis netto |
| ENTSO-E | ENTSO-e (HACS) | Attribut `prices` | wie in der Integration eingestellt |
| Octopus (Zeittarife) | Octopus Germany (HACS) | Attribut `timeslots` | Endpreis |
| andere | beliebiger Preissensor | nur der aktuelle Wert | wird als konstant angenommen |

Bei einem reinen Börsenpreis (Nord Pool Core, EPEX Spot „Market Price“) fehlen Netzentgelt,
Umlagen, Gebühr des Anbieters und Mehrwertsteuer. Trag dafür einen Aufschlag in ct/kWh und die
Mehrwertsteuer ein; Sunflow rechnet dann (Börsenpreis + Aufschlag) × (1 + MwSt.). Bei EPEX Spot
ist es einfacher, gleich den Sensor „Total Price“ zu wählen und die Aufschläge in der Integration
einzustellen.

Preise, die Sunflow über eine Aktion abfragt, werden 15 Minuten zwischengespeichert. Die Preise
für morgen veröffentlichen die Börsen gegen Mittag; vorher plant Sunflow mit dem, was bekannt ist.
