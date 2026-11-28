# Strompreise

**Deutsch** · [English](en/prices.md)

Sunflow plant mit dem Bezugspreis jeder Viertelstunde. Die Quelle wählst du unter
Einstellungen → Tarif & Prognose; angeboten werden alle Sensoren mit einer Einheit pro kWh.

| Anbieter / Quelle | Integration in Home Assistant | Wie Sunflow die Preise liest | Preis |
|---|---|---|---|
| Tibber | Tibber (Core) | Aktion `tibber.get_prices` für den Haushalt des Preissensors | Endpreis inkl. Steuern |
| Nord Pool | Nord Pool (Core) | Aktion `nordpool.get_prices_for_date` für heute und morgen, Gebiet des Sensors | Börsenpreis netto |
| Nord Pool | nordpool (HACS) | Attribute `raw_today` / `raw_tomorrow` | wie in der Integration eingestellt |
| aWATTar, smartENERGY, Energy-Charts, SMARD, ENTSO-E, Tibber, Energyforecast | EPEX Spot (HACS) | Attribut `data` | Sensor „Total Price“: Endpreis; „Market Price“: Börsenpreis netto |
| ENTSO-E | ENTSO-e (HACS) | Attribut `prices` | wie in der Integration eingestellt |
| Octopus (dynamische Tarife) | Octopus Germany (HACS) | Attribut `rates` (Preis je Viertelstunde, heute und ab Veröffentlichung morgen); fehlt es gerade, der übliche Verlauf der letzten Wochen mit Warnung | Endpreis |
| Octopus (Go, Heat, Intelligent Go, Festpreis) | Octopus Germany (HACS) | Attribut `timeslots`, beim Festpreis der aktuelle Wert | Endpreis |
| Octopus mit variablem Netzentgelt (Modul 3) | Octopus Germany (HACS), Sensor „… with Variable Grid Fee“ | Attribut `price_combinations` (heute) | Endpreis |
| Tibber | Tibber Prices (HACS) | Aktion `tibber_prices.get_price` | Endpreis inkl. Steuern |
| Green Planet Energy | Green Planet Energy (Core) | Aktion `green_planet_energy.get_prices` (höchstens 24 Stunden) | Endpreis |
| Rabot Energy | Rabot Energy (HACS) | Attribut `prices` | Endpreis |
| Ostrom | Ostrom (HACS, oliverwehrens) | Attribut `prices` (je Stunde) | Endpreis |
| 1KOMMA5° | 1KOMMA5° (HACS, BirknerAlex oder mrebbert) | Attribut `forecast` | Endpreis mit Netzentgelt und MwSt. |
| Schweizer Tarife (ElCom) | ElCom Tariffs (HACS) | Attribute `raw_today` / `raw_tomorrow` in CHF | Endpreis |
| Energi Data Service | Energi Data Service (HACS) | Attribute `raw_today` / `raw_tomorrow` | wie in der Integration eingestellt |
| andere | beliebiger Preissensor | nur der aktuelle Wert; schwankt er üblicherweise über den Tag, der Verlauf der letzten Wochen | sonst als konstant angenommen |

Preise in CHF/kWh oder Rp/kWh plant Sunflow wie €/kWh bzw. ct/kWh; die Anzeige bleibt in €.
Trag dann auch Einspeisevergütung und Verschleißkosten in Rappen ein.

Bei einem reinen Börsenpreis (Nord Pool Core, EPEX Spot „Market Price“) fehlen Netzentgelt,
Umlagen, Gebühr des Anbieters und Mehrwertsteuer. Trag dafür einen Aufschlag in ct/kWh und die
Mehrwertsteuer ein; Sunflow rechnet dann (Börsenpreis + Aufschlag) × (1 + MwSt.). Bei EPEX Spot
ist es einfacher, gleich den Sensor „Total Price“ zu wählen und die Aufschläge in der Integration
einzustellen.

Preise, die Sunflow über eine Aktion abfragt, werden 15 Minuten zwischengespeichert. Die Preise
für morgen veröffentlichen die Börsen gegen Mittag; vorher plant Sunflow mit dem, was bekannt ist.

## Über die veröffentlichten Preise hinaus

Sunflow plant bis zu 96 Stunden voraus. Dynamische Tarife kennen aber nur heute und – ab etwa
Mittag – morgen. Für die Stunden danach schätzt Sunflow den Preis: je Uhrzeit das Mittel des
Preissensors der letzten zwei Wochen (ab sieben Tagen Verlauf), sonst die letzten 24 bekannten
Stunden noch einmal. Geschätzte Preise sind im Plan gestrichelt und als „geschätzt“ markiert; sie
prägen nur das Ende des Plans und werden ersetzt, sobald die echten Preise da sind. Geschätzt wird
nur so weit, wie alle PV-Prognosen reichen, damit fehlender PV-Ertrag den Plan nicht verzerrt.
Zeittarife und feste Preise sind ohnehin bekannt und reichen über den ganzen Zeitraum.

## Einspeisung nach Börsenpreis

Die Einspeisevergütung stellst du je Wechselrichter unter Einstellungen → Tarif & Prognose ein:

| Modell | Vergütung je Viertelstunde |
|---|---|
| Feste EEG-Vergütung | der eingetragene Satz |
| Solarspitzengesetz | der eingetragene Satz, bei negativem Börsenpreis nichts |
| Direktvermarktung | Börsenpreis plus eingetragener Aufschlag (z. B. Marktprämie minus Entgelt des Direktvermarkters); bei negativem Börsenpreis der Börsenpreis ohne Aufschlag |

Beide Modelle brauchen den reinen Börsenpreis. Ist der Bezugspreis selbst ein Börsenpreis
(Nord Pool Core, EPEX Spot „Market Price“), nimmt Sunflow ihn ohne Aufschläge. Sonst wählst du
unter der Einspeisevergütung eine Quelle „Börsenpreis“, z. B. den Sensor „Market Price“ von
EPEX Spot. Ohne Börsenpreis erkennt Sunflow negative Preise nur an einem negativen Bezugspreis,
und Direktvermarktung rechnet mit dem Aufschlag als fester Vergütung.
