# Wärmepumpe: umgesetzt, aber nicht (genau) im Design

Stand: 23.11.2026 (0.80.33). Arbeitsliste zum Durchgehen: Was der Code tut, ohne dass `heat_pump.md` oder
`heat_pump_device.md` es beschreiben (oder nur ungenau). Je Punkt entscheidet der Nutzer:
**ins Design** (bleibt, wird beschrieben), **ändern** oder **ausbauen**. Erledigte Punkte werden hier
gestrichen; danach gilt wieder: Code = Design.

Spalte „Vorschlag“ ist nur die Einschätzung des Agenten.

## B. Heizkurve (8)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| B2 | Die 24 Start-Stunden liegen verteilt auf R − A = 5, 15 und 25 K (`CURVE_PRIOR_X_K`). Bestimmt, wie stark die Startsteilheit gegenüber der Startverschiebung festhält. | `curve.py` | ins Design |
| B3 | Ein Steilheitswert 0 aus der WP gilt als „nicht gesetzt“ (dann Heizflächentyp). | `curve.py` | ins Design |
| B4 | Neu gelernt wird außer stündlich auch sofort, wenn sich Heizflächentyp, Räume oder Raumgewichte ändern. | `samples.py` (`curve_key`) | ins Design |
| B5 | Gespeicherte Startsteilheit darf in `config.json` 0–10 sein; geprüft gegen den WP-Bereich wird erst beim Setzen über die API. | `config/models.py`, `api/heat_pump.py` | ins Design oder auf WP-Bereich beschränken |

## C. Korrektur (9)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| C1 | Ein Schritt höchstens alle 30 min ohne Raumänderung, Δt höchstens 30 min (`CORRECTION_MAX_STEP_S`); was der Raum während einer Pause tat, wird nicht nachgeholt. | `correction.py` | ins Design |
| C2 | Wechsel des Heizflächentyps (andere Nachstellzeit) und Wechsel der HK-Funktion lassen die Korrektur nicht springen; je Strategie eine eigene Korrektur. | `correction.py`, `shadow.py` | ins Design |
| C3 | Auch die **Leistungsgrenzen** der WP halten die Korrektur an (Wärme ≥ 95 % des Maximums: nicht weiter hoch; ≤ 105 % des Minimums: nicht weiter runter). Design nennt nur „Leistungsgrenze“. | `guard.py` (`_limits`) | prüfen: behalten oder nur Stellwertgrenzen |

## D. Leistungsbereich der WP (13)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| D1 | Gelernt aus Dauerlauf-Zeilen der letzten 28 Tage (ab 288 Zeilen): Minimum = 5-%-Quantil der Wärme, Maximum nur nach oben (99-%-Quantil). Startwerte aus dem Profil (0,6–6 kW). Wofür genau: nur C3 und die Planung. | `learned.py`, `defaults.py` | prüfen: brauchen wir das? |
| D2 | „Wärme ins Haus“: wählbare Quelle (Wärmemengenzähler oder Wärme der WP). | `config`, `samples.py` | ins Design |

## E. Schutz (11)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| E1 | **Schwankung** gelernt: 95-%-Quantil von `tv_max` − Tv in Dauerlauf-Zeilen (Kompressor in der Zeile und den 3 davor an, kein Start), ab 288 Zeilen. Design nennt nur „Schwankung gelernt“. | `learned.py` | ins Design |
| E2 | Wiederanlauf: Raum unbekannt zählt als „Wärme gewollt“. | `guard.py` | ins Design |
| E3 | In den 10 min nach einem Start wird gar nichts Normales geschrieben, nur Nachführen. | `guard.py` | ins Design |
| E4 | Normale Werte nur bei gültigem Heizbetrieb; Wiederanlauf auch außerhalb (z. B. Bypass-Beruhigung). | `guard.py` | ins Design |
| E5 | Sunflow schreibt auch nicht, wenn Tv oder Max T-Vorlauf fehlen (`tv_missing`, `setpoint_missing`). | `guard.py` | ins Design |
| E6 | Sperre nur, wenn das Profil eine Sperr-Rolle hat; Mindestdauer aus dem Profil (Start 20 min). | `guard.py`, Profil | ins Design |
| E7 | Stopp-Einordnung kennt zusätzlich „pending“ (Folge noch offen, < 10 min alt). Die Tabelle „Abschaltungen“ zeigt die letzten 30. | `learned.py` | ins Design |

## F. Schreiben (12)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| F1 | Mindestabstand aus dem Profil nie unter 60 s. | `limits.py` | ins Design |
| F2 | Normale Werte werden auf den nächsten Schritt gerundet (nicht aufwärts). | `writer.py` | ins Design |
| F3 | Schreibfehler: Sunflow nimmt den Schreibvorgang zurück (gilt als nicht geschrieben), Journal einmal, nächster Takt versucht es neu. | `active.py`, `shadow.py` | ins Design |
| F4 | Im Modus „Nur Lernen“ zählt das Budget virtuell; beim Start aus den `write`-Ereignissen der letzten 26 h nachgezählt. | `budget.py`, `samples.py` | ins Design |
| F5 | Budget erschöpft bei einem Schutzschritt: Journal einmal je Episode. (Kommentar in `budget.py` behauptet noch „wird durch die Sperre ersetzt“ – veraltet.) | `writer.py`, `budget.py` | ins Design, Kommentar korrigieren |
| F6 | „Jetzt schreiben“ verfällt mit Grund, wenn der nächste Takt nicht schreiben kann (nichts ausstehend, Budget, kein gültiger Zustand, nicht aktiv). | `writer.py` | ins Design |
| F7 | Fremde Änderung kleiner als ½ Schritt der Entity zählt nicht; eine Episode endet 24 h nach der letzten Änderung (Journal); läuft eine, nennt sie die Einrichtungs-Zusammenfassung. | `foreign.py` | ins Design |
| F8 | Eigene Sperre: der echte Zustand darf 10 min nachlaufen, ohne als fremd zu gelten. | `guard.py` | ins Design |

## G. Zustände (4)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| G1 | Journal je Änderung: unbekannter Zustand, Störung/behoben (mit Klartext des Fehlercodes aus dem Manifest), Sperre an/aus, WP begrenzt selbst. | `samples.py` (`_note`) | ins Design |
| G2 | `tv_max`, Kompressor-Start/-Stopp kommen aus `state_changed`-Ereignissen, nicht aus dem Minutentakt. | `samples.py` | ins Design (Geräteanbindung) |

## H. Räume (6)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| H1 | „Zuletzt gemeldet“ wird per REST gelesen (`last_reported`), nur bei Änderung oder alle paar Minuten; schlägt das fehl, entfällt die 2-h-Prüfung. | `rooms.py`, `shadow.py` | ins Design |
| H2 | Schnelle Änderung: einstellbare Bereiche (Beginn 0,2–5 K in 5–120 min, vorbei 0,1–5 K in 10–180 min, höchstens 0,5–12 h); beendete Ereignisse 24 h angezeigt (höchstens 20). | `defaults.py`, UI | ins Design |
| H3 | Außentemperatur-Quelle hat vier Stufen: automatisch, Fühler der WP, Fühler des Außengeräts (HPSU TA2), eigener Sensor. Design nennt zwei. | `config`, `samples.py` | ins Design oder auf zwei reduzieren |

## I. Hausmodell und Planung (14)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| I1 | Hausmodell mit Sonne (c·S) und inneren Gewinnen (d); Sonne aus der Einstrahlungs-Lernquelle, je Stunde aus der Sensor-Statistik. | `planning/house_model.py` | ins Design |
| I2 | Vor 7 Tagen Wärmepumpen-Daten lernt das Hausmodell aus der HA-Statistik (Stundenmittel), danach aus `heat_pump_samples` – Wechsel nur, wenn nicht schlechter, und endgültig. | `house_model.py` | prüfen: HA-Statistik behalten? |
| I3 | Nutzbar nur, wenn es auf 2 zurückgehaltenen Tagen besser ist als „Temperatur bleibt“; Zeitkonstante 5–500 h; Ridge-Regression. | `house_model.py` | ins Design |
| I4 | Planung: unter dem Spielraum 50 €/K·h (praktisch hart), darüber 0,2 €/K·h; WP-Bedarf (Wärme / Carnot-COP, 40 % von Carnot bei 35 °C Vorlauf) wird aus der Lastprognose genommen, höchstens 70 % je Viertelstunde. | `house_plan.py`, `defaults.py` | ins Design |
| I5 | Planung nur für die erste WP, nur im Programm Heizen, ohne Sonne, ohne Batteriespeicher kein Plan; Neuplanung sofort bei Änderung von Soll, Spielraum, Modus. | `house_plan.py`, `api` | ins Design |
| I6 | Planung nutzt den letzten WP-Messwert höchstens 10 min alt. | `defaults.py` | ins Design |

## J. Home Assistant und API (16)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| J1 | Thermostat „Haus“: Attribut `shadow` (veraltet, = `mode` ist `shadow`), daneben `mode`. | `climate.py` | **ausbauen** (`shadow`) |
| J2 | Thermostat „Haus“: viele weitere Attribute (Korrektur, eingefroren mit Gründen, Heizkurve, Außen, ausstehender Wert mit Grund, Räume, je WP unter `heat_pumps`). | `climate.py` | prüfen, welche gebraucht werden |
| J3 | Raum-Soll im Thermostat in 0,5-K-Schritten, gespeichert auf 0,1 K. HA „Heizen“ kehrt zur zuletzt gültigen Stufe zurück. | `climate.py`, `defaults.py` | ins Design |
| J4 | Sensoren am Gerät „Sunflow“, Symbol Geist im Modus „Nur Lernen“; „Schreibvorgänge heute“ `unknown`, bis Budget und Zeitzone bekannt. | `sensor.py` | ins Design |
| J5 | API zusätzlich: `GET …/shadow_writes` (Tageszählung 7 Tage), `GET …/history?hours=1–168`, `PUT …/flow_limits`, `PUT …/learn_stops`, `GET /house/model`. | `api/heat_pump.py`, `api/house.py` | ins Design (kurz) |
| J6 | Verlauf-Ereignisse enthalten noch `flow_source` (Quellenwechsel gibt es nicht mehr). | `api/heat_pump.py` | **ausbauen** |

## K. UI (16)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| K1 | Verlauf (6 h/24 h/3 Tage) mit Bändern für Warmwasser, Sperre, schnelle Änderung, Kompressorlauf und Kreuzen für Stopps; Anlage als Bild; Tageszählung der Schreibvorgänge; „Jetzt schreiben“-Taste. | `heat-pump-view.js`, `heat-pump-diagrams.js` | ins Design (kurz) |

## L. Altlasten (Kompatibilität mit früheren Ständen)

| # | Was | Wo | Vorschlag |
|---|---|---|---|
| L1 | Sperr-Gründe `adopted`, `min_power`, `budget` aus alten `heat_pump_state.json` werden beim Laden umgedeutet. | `guard.py` (`_ADOPTED`, `_OLD_BLOCKS`) | **ausbauen** |
| L2 | `written_reason` aus Ständen vor 0.80.17 aus den letzten Schreibvorgängen rekonstruiert. | `guard.py` (`_reason_from_writes`) | **ausbauen** |
| L3 | Veraltete Kommentare (z. B. „preparing“ als Schutzschritt, „from part 4.7“). | `budget.py`, `defaults.py` | korrigieren |
