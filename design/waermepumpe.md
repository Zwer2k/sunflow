# Entwurf: Wärmepumpe in Sunflow

Stand: 15.11.2026 · Status: Stufe 1–3 umgesetzt, Stufe 4 offen

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
- **In der Wärmepumpe eingebaut:** nur sperren, freigeben oder begrenzen, die Wärmepumpe
  entscheidet (`HeatPumpSettings.rod`, Rollen `rod_allow`, `rod_block`, `rod_select`, `rod_limit`;
  Zustände frei / gesperrt / freigegeben). Belegt: EMS-ESP `auxheateroff` (Sperr-Schalter) und
  `maxheat…` (kW-Stufen), NIBE 47370/40181 (Freigabe) und 47212/40103 (max. kW), HeishaMon
  `SetDHWHeaterState`/`SetRoomHeaterState` (blocked/free). Nur indirekt (Zwangsmodi, die WP
  entscheidet): Luxtronik ZWE, Stiebel „Notbetrieb“, Daikin/Panasonic „Powerful“, Viessmann
  Einmalladung, Vaillant Boost. Fast alles sind Flash-Parameter: Wechsel höchstens alle 60 min,
  EEPROM-Wächter. Eine Freigabe schaltet nicht ein.
- Zweite Variante: ein Verbraucher mit Art „Wärme“ als Heizstab (`HeatPumpSettings.rod_id`).

### Modi

| Modus | Verhalten |
|---|---|
| **Aus** | Sunflow schaltet ihn nie zu |
| **Nur Überschuss** | nur mit PV, die sonst eingespeist oder abgeregelt würde, über die Leistung der Wärmepumpe hinaus |
| **Automatisch** | Sunflow wählt je Viertelstunde zwischen Wärmepumpe und Heizstab nach Kosten |
| **An (von Hand)** | Notbetrieb, z. B. wenn die Wärmepumpe im Winter nicht reicht |

**Jahreszeit:** Heizperiode nach Außentemperatur (Tagesmittel unter z. B. 15 °C, Wechsel erst
nach 2–3 Tagen), nach Datum oder aus einer Entität (Sommer-/Winterbetrieb der Wärmepumpe).
Standard je Jahreszeit: Heizperiode **Aus**, Sommer **Nur Überschuss** (aus dem Netz heizt die
Wärmepumpe mit COP 3–4 billiger). Modi und Jahreszeit gelten nur für den Heizstab, den eine
Wärmepumpe zugeordnet hat (`HeatPumpSettings.rod_id`); angelegt wird er als Verbraucher mit Art
„Wärme“, zugeordnet in der Gruppe „Heizstab“ der Wärmepumpe.

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

## Hintergrund aus der Praxis

- **Bivalenzpunkt:** Unterhalb (meist −5 bis −8 °C) reicht eine Luft-Wasser-Wärmepumpe allein nicht;
  dann heizt ihr eingebauter Heizstab mit, im Mittel nur etwa 2 % der Jahresheizarbeit. Den regelt
  die Wärmepumpe selbst – Sunflow schaltet nur einen externen Heizstab.
- **Warmwasser:** Die Wärmepumpe arbeitet bis etwa 50–55 °C effizient, darüber sinkt der COP und
  der Verdichter leidet. Höhere Temperaturen (Legionellenschutz 60 °C) übernimmt besser der
  Heizstab, am besten mit PV-Überschuss oder zu einer günstigen Stunde.
- **Überschuss in die Heizung:** Statt Warmwasser höher zu heizen, den Vorlauf bzw. die Heizkurve um
  1–4 K anheben; Estrich und Räume speichern die Wärme. Ein Sollwert für die Methode „Sollwert“
  kann deshalb auch ein Vorlauf- oder Raumsollwert sein.
- **Schalten mit Ruhe:** evcc gibt Boost frei, wenn der Überschuss 5 min anliegt, und nimmt ihn
  nach 20 min ohne zurück; Fachleute mitteln das PV-Signal über etwa 20 min. Sunflow plant
  Viertelstunden und hält jeden Zustand mindestens 20 min.
- **§ 14a EnWG:** Dimmen bzw. Sperren durch den Netzbetreiber hat Vorrang; Sunflow darf diesen
  Kontakt nicht schalten.

Quellen: [Umweltbundesamt, Trinkwarmwasserkonzepte](https://www.umweltbundesamt.de/sites/default/files/medien/11850/publikationen/factsheet_trinkwarmwasserkonzepte.pdf),
[evcc – Wärmeerzeuger](https://docs.evcc.io/de/heating),
[Verbraucherzentrale RLP – Wärmepumpen](https://www.verbraucherzentrale-rlp.de/sites/default/files/2026-02/250428_bhp_vz_waermepumpen_26.pdf),
[1KOMMA5° – Heizstab](https://1komma5.com/de/waermepumpe/heizstab/).

## Was der COP bringt

- Echter Kostenvergleich Wärmepumpe ↔ Heizstab je Viertelstunde.
- Gelernte COP-Kurve über die Außentemperatur, getrennt nach Heizen und Warmwasser: Sunflow
  bevorzugt warme Mittagsstunden, zusammen mit PV doppelt günstig.
- Prognose des Wärmebedarfs statt des Stroms (stabiler, weil der COP schwankt).
- Statistik: Wärme, Strom, COP und PV-Anteil pro Monat.

## Haus als Wärmespeicher

**Modell (1R1C, umgesetzt in `planning/house_model.py`):** je Stunde
`ΔT = a·(T_außen − T) + b·Q + c·S + d` mit gewichteter Ridge-Regression (Halbwertszeit 14 Tage,
Strafe relativ zur Spaltengröße). τ = 1/a, Wärmekapazität 1/b, Wärmeverlust a/b, Sonne c (S von 0
bis 1 aus der Helligkeit), innere Gewinne d. Wärme Q: eigener Sensor (Wärmemengenzähler), sonst
Wärmepumpe gemessen, Strom × COP-Sensor oder Strom × Carnot-COP (40 %, Vorlauf 35 °C). Bewertung
auf den letzten 2 Tagen, freie Simulation über 24 h gegen „bleibt gleich“; plausibel bei τ 5–500 h
und b > 0. Typische Einfamilienhäuser: 80–400 W/K, 10–40 kWh/K, τ 30–150 h.

**Vorbilder und was wir übernehmen** (Recherche November 2026):

| Projekt | Was es macht | Für Sunflow |
|---|---|---|
| EMHASS (MIT) | Wärmemodelle im LP: `thermal_config` (Raum, Aufheizrate, Abkühlkonstante), `thermal_battery` (Speicher, COP nach Carnot vorab je Schritt), `heat_topology` (Verlust UA·(T − T_außen)); harte Grenzen plus Malus unter Wunschtemperatur. Lernt nichts, Parameter von Hand; viele frische Fehler (u. a. mehr Wärme geplant, als Heizkörper abgeben). | Formulierung für Stufe 4: Raumtemperatur als Zustand, COP vorab je Viertelstunde, Komfortband hart, Malus darunter; zusätzlich Grenze der Wärmeleistung. |
| hplib (FZ Jülich, MIT/CC BY) | COP und Strom aus Außen- und Vorlauftemperatur, Parameter aus Keymark, generisch je Bauart | Formel und generische Parameter als Startwert für den COP, bis er gelernt ist |
| Heat Pump Cost Optimizer (tvofi, MIT) | Zwei Zonen, Estrich, Puffer, Warmwasser mit gemeinsamem Verdichter; lernt Verluste, COP, Sonne | Ideen (gemeinsamer Verdichter, Warmwasser-Fenster); Code zu groß und automatisch erzeugt |
| Predheat | Zwei Massen (Raum und Heizwasser), Kalibrierung aus Abkühl- und Heizphase; nur Simulation | Idee |
| Heating Curve Optimizer (GPL) | Heizkurven-Offset ±4 K über 24 h als Stellgröße | Weitere Ansteuerung „Heizkurve verschieben“ |
| OpenEMS, evcc | Feste Regeln für SG-Ready (Schwellen, Sperre max. 2 h, Mindestzeiten) | bestätigt unsere Regeln; SG-Ready verlangt mind. 10 min je Signal |
| adaptive_flow_control | Lernt den Vorlauf-Sollwert (Ridge, verzögerte Bewertung) | Kann neben Sunflow laufen: Sunflow verschiebt dessen Raum-Sollwert |

**Regeln abgesichert (umgesetzt, `control/heat_pump.py` `HouseView`):** In der Heizperiode sperrt
Sunflow nur, wenn der Referenzraum ohne Heizen und Sonne bis zum Ende der Sperre (Höchstdauer,
Tagesrest, Ende der teuren Stunden) über der Untergrenze bleibt; sonst endet die Sperre sofort.
Verstärken endet an der Obergrenze (mit Mindestdauer). Das Modell lernt der Runner alle 6 h und
nutzt es nur, solange es besser ist als „bleibt gleich“.

**Nächster Schritt:** Stufe 4.
Beim Lernen beachten: Sobald Sunflow die Wärmepumpe verschiebt, verzerrt die Rückkopplung das
Modell; deshalb erst im Schattenbetrieb vergleichen.

## Stufen

1. **Gerät „Wärmepumpe“ mit Messung** (alle Varianten), Live-Werte mit Herkunft und
   Plausibilitätsprüfung. Die Wärmepumpe bleibt in der Prognose des Hausverbrauchs; das gelernte
   Verbrauchsmodell berücksichtigt die Außentemperatur bereits.
2. **Heizstab** mit 1–3 Relais, Modi, Jahreszeit und Zuschalten von Hand. „Automatisch“ plant
   bis Stufe 4 nach Strompreis, noch ohne Vergleich mit dem COP der Wärmepumpe; Schieflast und
   der Hinweis „Wärmepumpe reicht nicht“ folgen.
3. **Ansteuerung** über SG-Ready (zwei Relais, nur Kontakt 2, Auswahl), Sollwert, gemeldeten
   PV-Überschuss oder Boost-Schalter, einstellbar; Regeln aus dem Plan (Überschuss, günstigste
   und teuerste Stunden, Mindestdauer, Sperrgrenzen), Schattenmodus. Eigener Einstellungsbereich
   „Wärmepumpe“. Offen: eigene Prognose der Wärmepumpe nach Außentemperatur.
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
