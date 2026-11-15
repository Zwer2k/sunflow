# Entwurf: Wärmepumpe in Sunflow

Stand: 15.11.2026 · Status: Entwurf, Stufe 1 in Arbeit

Sunflow soll Wärmepumpen messen, prognostizieren und – wo möglich – über SG-Ready in
günstige oder sonnige Stunden schieben, dazu einen Heizstab im Speicher in Stufen nutzen.
Sunflow schaltet die Wärmepumpe dabei nie hart ein oder aus: Es gibt ihr nur Empfehlungen,
ihre eigene Regelung bleibt zuständig.

## Ein Gerät, drei optionale Bausteine

| Baustein | Was Sunflow damit macht | Ohne ihn |
|---|---|---|
| **Messung** | Verbrauch, Wärme und COP live; lernt Leistung je Zustand und Bedarf nach Außentemperatur | Nennleistungen |
| **SG-Ready** | verschiebt die Wärmepumpe (verstärkt, ggf. Sperre) | nur gemessen und prognostiziert |
| **Heizstab** | nimmt PV-Überschuss in Stufen auf, Notbetrieb im Winter | – |

Jeder Baustein ist optional; ohne SG-Ready und Heizstab ist die Wärmepumpe ein reines
Messgerät.

## Messung

Drei Größen; aus zwei davon berechnet Sunflow die dritte.

| Größe | Varianten |
|---|---|
| **Strom** (Aufnahme) | ein Leistungssensor · je Phase 1–3 Leistungssensoren · je Phase 1–3 Stromsensoren (A, × Spannung) · nur Energiezähler · keine |
| **Wärme** (Abgabe) | Leistung · Energiezähler · keine |
| **COP** | Sensor · keiner |

- COP = Wärme ÷ Strom, Wärme = Strom × COP, Strom = Wärme ÷ COP.
- Rangfolge je Wert: gemessen → berechnet → gelernt → eingestellt.
- Plausibilität: COP außerhalb 1–7 oder große Abweichung zwischen gemessenem und berechnetem
  COP ergibt einen Hinweis (z. B. Wärme- statt Stromsensor gewählt).
- Manche Integrationen melden unter „Leistung“ die Wärmeleistung: Der Assistent fragt deshalb
  getrennt nach Strom und Wärme.
- Messung je Phase zeigt die Last je Phase (wichtig für den Heizstab, siehe Schieflast) und
  erlaubt, einen über denselben Zähler gemessenen Heizstab herauszurechnen.

### Konfiguration

Eine Seite „Was misst deine Wärmepumpe?“; Felder erscheinen nur für angehakte Größen,
Vorschläge kommen aus den Entitäten des Geräts (Einheit, `device_class`):

```
Messung                                        Vorschlag aus dem Gerät
☑ Stromverbrauch     [Gesamt ▾]                sensor.wp_leistung (W)
☑ Wärmeleistung      [Leistung ▾]              sensor.wp_waermeleistung (W)
☐ COP                                          → wird berechnet
Speichertemperatur   sensor.wp_warmwasser (°C)
Außentemperatur      sensor.wp_aussen (°C)
```

Danach Live-Werte mit Herkunft („gemessen“, „berechnet“), damit eine falsche Entität sofort
auffällt.

## SG-Ready

| Zustand | Kontakte | Bedeutung | Einsatz durch Sunflow |
|---|---|---|---|
| 1 Sperre | 1 : 0 | Wärmepumpe läuft nicht | teure Stunden, kurz und begrenzt |
| 2 Normal | 0 : 0 | eigene Regelung | Grundzustand, Rückfall bei Fehlern |
| 3 Verstärkt | 0 : 1 | Sollwerte angehoben, Wärme auf Vorrat | PV-Überschuss, günstiger Strom |
| 4 Anlauf | 1 : 1 | Zwangsbetrieb | nur bei negativem Preis, abschaltbar |

- Ansteuerung über zwei Relais (`switch`) oder ein `select` der Hersteller-Integration.
- **§14a EnWG:** Liegt Kontakt 1 auf der Sperre des Netzbetreibers, nutzt Sunflow ihn nicht;
  dann nur „Normal ↔ Verstärkt“ über Kontakt 2. Der Assistent fragt danach.
- Was „verstärkt“ bewirkt, wird an der Wärmepumpe eingestellt; Sunflow misst nur den Effekt.
- Schutz: Mindestdauer je Zustand (z. B. 20 min), Grenze für Wechsel pro Stunde, Sperre höchstens
  2 h am Stück und begrenzt pro Tag; der Watchdog schaltet auf „Normal“ (beide Kontakte aus).

## Heizstab

### Bauformen

- **Extern** im Puffer-/Warmwasserspeicher mit 1–3 Relais (je Phase), einem `select` mit
  Stufen oder einem Relais. Bei 3 × 2 kW: 0 / 2 / 4 / 6 kW. Einphasig wechselt die Phase reihum;
  die Grenze von 4,6 kVA je Phase (VDE-AR-N 4100) gilt für Wärmepumpe und Heizstab zusammen.
- **In der Wärmepumpe eingebaut:** steuerbar nur, wenn die Integration eine Stufen-Entität hat;
  sonst nur gemessen bzw. aus der Leistung geschätzt.

### Modi

| Modus | Verhalten |
|---|---|
| **Aus** | Sunflow schaltet ihn nie zu |
| **Nur Überschuss** | nur mit PV, die sonst eingespeist oder abgeregelt würde, über die Leistung der Wärmepumpe hinaus |
| **Automatisch** | Sunflow wählt je Viertelstunde zwischen Wärmepumpe und Heizstab nach Kosten |
| **An (von Hand)** | Notbetrieb, z. B. wenn die Wärmepumpe im Winter nicht reicht |

**Jahreszeit:** Heizperiode nach Außentemperatur (Tagesmittel unter z. B. 15 °C, Wechsel erst
nach 2–3 Tagen), nach Datum oder aus einer Entität (Sommer-/Winterbetrieb der Wärmepumpe).
Standard je Jahreszeit: Heizperiode **Aus**, Sommer **Automatisch**.

**Automatisch** vergleicht die Kosten einer kWh Wärme: Wärmepumpe = Preis ÷ COP, Heizstab =
Preis ÷ 1, Überschuss kostet die entgangene Einspeisevergütung. Der Heizstab kommt zum Zug,
wenn der Überschuss die Leistung der Wärmepumpe übersteigt, PV sonst abgeregelt würde oder der
Preis negativ ist, die Wärmepumpe ihre Höchsttemperatur erreicht hat (z. B. Wärmepumpe bis
50 °C, Heizstab bis 65 °C) oder sie gesperrt ist.

**Von Hand zuschalten:** in Sunflow (Steuerung), über die Entität „*Heizstab* Modus“ und die
Aktion `sunflow_ems.set_consumer`; bis Uhrzeit, für eine Dauer, bis Zieltemperatur oder bis
zur Rücknahme, spätestens nach einer einstellbaren Zeit (z. B. 24 h). Steigt die
Speichertemperatur trotz laufender Wärmepumpe nicht, meldet Sunflow das (Hinweis und Ereignis
`sunflow_ems_heat_pump_insufficient`); zugeschaltet wird nur von Hand.

## Was der COP bringt

- Echter Kostenvergleich Wärmepumpe ↔ Heizstab je Viertelstunde.
- Gelernte COP-Kurve über die Außentemperatur, getrennt nach Heizen und Warmwasser: Sunflow
  bevorzugt warme Mittagsstunden, zusammen mit PV doppelt günstig.
- Prognose des Wärmebedarfs statt des Stroms (stabiler, weil der COP schwankt).
- Statistik: Wärme, Strom, COP und PV-Anteil pro Monat.

## Stufen

1. **Gerät „Wärmepumpe“ mit Messung** (alle Varianten), Live-Werte mit Herkunft und
   Plausibilitätsprüfung. Die Wärmepumpe bleibt in der Prognose des Hausverbrauchs; das gelernte
   Verbrauchsmodell berücksichtigt die Außentemperatur bereits.
2. **Heizstab** mit 1–3 Relais, Modi, Jahreszeit und Zuschalten von Hand.
3. **SG-Ready**, zuerst „Normal ↔ Verstärkt“ über Kontakt 2, Sperre später; eigene Prognose
   der Wärmepumpe nach Außentemperatur.
4. **Gemeinsame Planung** von Wärmepumpe und Heizstab im Optimierer (Wärmespeicher mit zwei
   Erzeugern unterschiedlicher Effizienz).

Jede Stufe läuft zuerst im Schattenmodus.

## Test

Entwickelt wird gegen eine nachgebaute Wärmepumpe (Hilfs-Entitäten in Home Assistant); getestet
an einer echten Anlage mit Sunflow Edge, zuerst nur Messung, dann Schattenmodus, erst danach
Steuerung.

Für die Profile werden von der Testanlage gebraucht:

- Hersteller, Modell und Home-Assistant-Integration der Wärmepumpe.
- SG-Ready: zwei Relais (welche?) oder ein `select`; liegt Kontakt 1 auf der Sperre des
  Netzbetreibers; was ist für „verstärkt“ eingestellt?
- Messung: Zähler gesamt oder je Phase, in W, A oder kWh; ein- oder dreiphasiger Anschluss;
  Wärmeleistung oder COP als Sensor?
- Heizstab: extern oder eingebaut, Phasen und kW je Phase, wie geschaltet.
- Temperaturen: Warmwasser, Puffer, Außenfühler.
- Am einfachsten: ein Screenshot der Geräteseite der Wärmepumpe in Home Assistant mit allen
  Entitäten.
