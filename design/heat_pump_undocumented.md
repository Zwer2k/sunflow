# Wärmepumpe: umgesetzt, aber nicht (genau) im Design

Stand: 23.11.2026 (0.80.33). Arbeitsliste zum Durchgehen: Was der Code tut, ohne dass `heat_pump.md` oder
`heat_pump_device.md` es beschreiben (oder nur ungenau). Je Punkt entscheidet der Nutzer:
**ins Design** (bleibt, wird beschrieben), **ändern** oder **ausbauen**. Erledigte Punkte werden hier
gestrichen; danach gilt wieder: Code = Design.

Bewertet nach dem Grundsatz in `AGENTS.md` („korrekte Berechnung vor zusätzlichen Regeln“). Spalte
„Bewertung“ ist die Einschätzung des Agenten, die Entscheidung trifft der Nutzer. Als Arbeitsliste (nicht
Spezifikation) enthält sie Befunde aus den Daten der Anlage: `heat_pump_samples` über die Sunflow-API
(21.–23.11., 52 h, außen −1…7 °C) und HA-History per MCP.

**Übergreifende Befunde**
- Die HPSU meldet bei laufendem Kompressor oft 0–0,35 kW Wärme (Tv ≈ Tr; bei 0,1 K Auflösung ist die
  Wärmeberechnung bei kleiner Spreizung ungenau): 104 von 429 gültigen Zeilen (24 %) ≤ 269 W. Jede Regel,
  die auf die untere Wärme reagiert, erbt diesen Messfehler (C3, D1).
- Mehrere Werte springen von „Startwert“ auf „gelernt“, sobald eine Mindestmenge erreicht ist (E1, E7, D1,
  I2). Die Heizkurve macht es anders: Startwert als Prior, Daten gewichtet, kein Sprung. Später
  vereinheitlichen (eigener Punkt nach dieser Liste).

## B. Heizkurve (8)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| B2 | Die 24 Start-Stunden liegen verteilt auf R − A = 5, 15 und 25 K (`CURVE_PRIOR_X_K`). | `curve.py` | ins Design | Nötiger Teil des Priors: bestimmt, wie stark Startsteilheit und -verschiebung festhalten. Ohne Verteilung wäre die Steilheit unbestimmt. Eine Regel, wirkt nur an einer Stelle. | B5 (Startwerte), Anteil des Gelernten |
| B3 | Ein Steilheitswert 0 aus der WP gilt als „nicht gesetzt“ (dann Heizflächentyp). | `curve.py` | ins Design | Eine Steilheit 0 als Start ergäbe Vorlauf = Raum-Soll – sicher nicht gemeint. Einfache, eindeutige Regel. | B5 |
| B4 | Neu gelernt wird außer stündlich auch sofort, wenn sich Heizflächentyp, Räume oder Raumgewichte ändern. | `samples.py` (`curve_key`) | ins Design | Korrektheit: sonst rechnet die Kurve bis zu 1 h mit alten Raumgewichten. | C2 (Korrektur wird beim Kurvenwechsel verschoben) |
| B5 | Gespeicherte Startsteilheit darf in `config.json` 0–10 sein; gegen den WP-Bereich geprüft wird erst beim Setzen über die API. | `config/models.py`, `api/heat_pump.py` | **ändern** | Dieselbe Frage an zwei Stellen mit verschiedenen Grenzen. Ein von Hand gesetzter Wert außerhalb des WP-Bereichs wird als Prior benutzt, die gelernte Steilheit aber in den WP-Bereich geklemmt – widersprüchlich. Vorschlag: nur eine Prüfung beim Verwenden (WP-Bereich), `config.json` nur Plausibilität. | B2, B3 |

## C. Korrektur (9)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| C1 | Ein Schritt höchstens alle 30 min ohne Raumänderung, Δt höchstens 30 min (`CORRECTION_MAX_STEP_S`); was der Raum während einer Pause tat, wird nicht nachgeholt. | `correction.py` | ins Design | Begrenzt das Integral nach Datenlücken; ohne die Grenze würde eine lange Lücke als ein großer Schritt nachgeholt. Fällt eine Pause in eingefrorene Zeit, wäre Nachholen falsch. | H1 (Raum veraltet), H2 (schnelle Änderung friert ein) |
| C2 | Wechsel des Heizflächentyps (andere Nachstellzeit) und der HK-Funktion lassen die Korrektur nicht springen; je Strategie eine eigene Korrektur. | `correction.py`, `shadow.py` | ins Design | Folgt aus der inkrementellen Form, keine Zusatzregel. | B4 |
| C3 | Auch die Leistungsgrenzen halten die Korrektur an (Wärme ≥ 95 % des Maximums: nicht weiter hoch; ≤ 105 % des Minimums: nicht weiter runter). | `guard.py` (`_limits`) | **ausbauen** | Beruht auf der unteren Wärme-Messung, die bei kleiner Spreizung unbrauchbar ist: Die Korrektur war in 24 % der gültigen Zeilen nach unten eingefroren (auch jetzt, Grund `power_min`). Die obere Grenze wirkte in 8 von 429 Zeilen. Begrenzung reicht über die Stellwertgrenzen (10), die eindeutig sind. | D1 (liefert die Grenzen), D2 (Wärmequelle), Abschnitt 10 |

## D. Leistungsbereich der WP (13)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| D1 | Gelernt aus Dauerlauf-Zeilen der letzten 28 Tage (ab 288): Minimum = 5-%-Quantil der Wärme, Maximum nur nach oben (99-%-Quantil). Startwerte aus dem Profil (0,6–6 kW). | `learned.py`, `defaults.py` | **ausbauen** (Lernen) | Minimum: nur von C3 genutzt, gelernt aus dem Messfehler (256 W). Maximum: wird nur übernommen, wenn es über dem Profil liegt – gelernt 5,1 kW < 6 kW, wirkt also nie. Ohne C3 bleibt nur die Planung: dort reicht das Maximum aus dem Profil. | C3, I4/I5 (Planung nutzt das Maximum), E1 (gleiche Dauerlauf-Zeilen) |
| D2 | „Wärme ins Haus“: wählbare Quelle (Wärmemengenzähler oder Wärme der WP). | `config`, `samples.py` | ins Design | Echte Auswahl des Nutzers, keine verdeckte Regel. Ein Zähler im Heizkreis wäre genauer als die HPSU-Wärme (siehe übergreifender Befund). | C3, D1, I2, I7 |

## E. Schutz (11)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| E1 | Schwankung gelernt: 95-%-Quantil von `tv_max` − Tv in Dauerlauf-Zeilen (Kompressor in der Zeile und den 3 davor ≥ 99 %, kein Start), ab 288 Zeilen. | `learned.py` | ins Design | Gelernt 0,48 K aus 385 Zeilen, plausibel nahe am Profil (0,5). Der Dauerlauf-Filter ist hier nötig (Startspitzen sind keine Schwankung). Sprung Start → gelernt siehe übergreifender Befund. | Untergrenze des Schutzes (11), G2 (`tv_max`), D1 |
| E2 | Wiederanlauf: Raum unbekannt zählt als „Wärme gewollt“. | `guard.py` | ins Design | Sichere Seite: lieber anlaufen als auskühlen. Eine Regel, eindeutig. | H1 |
| E3 | In den 10 min nach einem Start wird nichts Normales geschrieben, nur Nachführen. | `guard.py` | ins Design | Steht als „10 min nach Start einmal absenken“ schon sinngemäß im Design; nur ausdrücklich machen. | E4 |
| E4 | Normale Werte nur bei gültigem Heizbetrieb; Wiederanlauf auch außerhalb (z. B. Bypass-Beruhigung). | `guard.py` | ins Design | Nutzt die eine Definition „gültiger Heizbetrieb“ (4) – passt zum Grundsatz. | E3, Abschnitt 4 |
| E5 | Kein Schreiben, wenn Tv oder Max T-Vorlauf fehlen (`tv_missing`, `setpoint_missing`). | `guard.py` | ins Design | Ohne Messwert ist jede Rechnung falsch. Entspricht „unbekannte Werte: nichts schreiben“ (4). | G1 |
| E6 | Sperre nur, wenn das Profil eine Sperr-Rolle hat; Mindestdauer aus dem Profil (Start 20 min). | `guard.py`, Profil | ins Design | Geräteeigenschaft, gehört ins Profil. | F8, L1 |
| E7 | Stopp-Einordnung kennt zusätzlich „pending“ (Folge noch offen, < 10 min alt). Die Tabelle zeigt die letzten 30. | `learned.py` | ins Design | Ohne „pending“ würde ein Stopp vor einem Abtauen kurz als ungeklärt gezählt. 53 Stopps: 39 Abtauen, 3 Warmwasser, 8 ungeklärt – Abschaltgrenze noch nicht gelernt (braucht 5 passende). | E1, K1 |

## F. Schreiben (12)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| F1 | Mindestabstand aus dem Profil nie unter 60 s. | `limits.py` | ins Design | Schutz gegen ein fehlerhaftes Profil, EEPROM. | F6 |
| F2 | Normale Werte werden auf den nächsten Schritt gerundet (nicht aufwärts). | `writer.py` | ins Design | Kleinster Fehler; Schutzschritte runden sicher (Design). | — |
| F3 | Schreibfehler: gilt als nicht geschrieben, Journal einmal, nächster Takt versucht es neu. | `active.py`, `shadow.py` | ins Design | Zustand bleibt wahr (nichts als geschrieben gezählt, was nicht ankam). | F4 |
| F4 | Bei „Nur Lernen“ zählt das Budget virtuell; beim Start aus den `write`-Ereignissen der letzten 26 h nachgezählt. | `budget.py`, `samples.py` | ins Design | Nötig, damit „Nur Lernen“ zeigt, was „Lernen + Regeln“ schreiben würde. | F5, J4 |
| F5 | Budget erschöpft bei einem Schutzschritt: Journal einmal je Episode. Kommentar in `budget.py` („wird durch die Sperre ersetzt“) veraltet. | `writer.py`, `budget.py` | ins Design, Kommentar korrigieren | Verhalten korrekt, Kommentar beschreibt entfernte Logik (verwirrt). | F4, L3 |
| F6 | „Jetzt schreiben“ verfällt mit Grund, wenn der nächste Takt nicht schreiben kann. | `writer.py` | ins Design | Sichtbar statt still – passt zum Grundsatz. | F1, F4 |
| F7 | Fremde Änderung < ½ Schritt zählt nicht; Episode endet 24 h nach der letzten Änderung; läuft eine, nennt sie die Einrichtungs-Zusammenfassung. | `foreign.py` | ins Design | ½ Schritt = Rundung der Entity, kein Schreiber. Keine fremden Änderungen in den Daten. | F8 |
| F8 | Eigene Sperre: der echte Zustand darf 10 min nachlaufen, ohne als fremd zu gelten. | `guard.py` | ins Design | Sonst würde die eigene Sperre als fremd gemeldet. | E6, F7 |

## G. Zustände (4)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| G1 | Journal je Änderung: unbekannter Zustand, Störung/behoben (Klartext aus dem Manifest), Sperre an/aus, WP begrenzt selbst. | `samples.py` (`_note`) | ins Design | Macht Zustände nachvollziehbar, beeinflusst keine Rechnung. | E5 |
| G2 | `tv_max`, Kompressor-Start/-Stopp kommen aus `state_changed`-Ereignissen, nicht aus dem Minutentakt. | `samples.py` | ins Design (Geräteanbindung) | Korrektheit: ein Minutentakt verpasst kurze Spitzen und Stopps. | E1, E7 |

## H. Räume (6)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| H1 | „Zuletzt gemeldet“ per REST (`last_reported`); schlägt das fehl, entfällt die 2-h-Prüfung still. | `rooms.py`, `shadow.py` | ins Design, **ändern** | Das Lesen selbst ist nötig (Zigbee meldet unveränderte Werte nicht neu). Das stille Weglassen der Prüfung ist eine verdeckte Regel: dann zählt ein toter Sensor weiter. Mindestens ins Journal. | C1, E2, Raum-Ist (6) |
| H2 | Schnelle Änderung: einstellbare Bereiche; beendete Ereignisse 24 h angezeigt (höchstens 20). | `defaults.py`, UI | ins Design | Grenzen der Eingabe und Anzeige, keine Rechenregel. | C1, Heizkurve (8), I7 |
| H3 | Außentemperatur-Quelle hat vier Stufen: automatisch, Fühler der WP, Fühler des Außengeräts (TA2), eigener Sensor. | `config`, `samples.py` | **ändern** | „Automatisch“ ist eine verdeckte Vorrangregel (Lernquelle, sonst WP) – derzeit wirkt damit der WP-Fühler. Vorschlag: nur ausdrückliche Wahl (WP, Außengerät, eigener Sensor), Standard WP. | Abschnitt 7, Heizkurve (8), I1/I2 |

## I. Hausmodell und Planung (14)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| I1 | Hausmodell mit Sonne (c·S) und inneren Gewinnen (d); Sonne aus der Einstrahlungs-Lernquelle, je Stunde aus der Sensor-Statistik. | `planning/house_model.py` | ins Design | Physikalisch nötige Terme; ohne sie landen Sonne und Gewinne im Wärmeverlust. Derzeit ohne Sonne (keine Quelle). | I2, H3 |
| I2 | Vor 7 Tagen WP-Daten lernt das Hausmodell aus der HA-Statistik (Stundenmittel), danach aus `heat_pump_samples` – Wechsel nur, wenn nicht schlechter, und endgültig. | `house_model.py` | **ausbauen** (HA-Statistik) | Zwei Datenwege mit verschiedenen Filtern und Wärmewerten, dazu eine Wechselregel – schwer nachvollziehbar. Bringt derzeit nichts: nach 5 Tagen noch kein Modell. Vorschlag: nur `heat_pump_samples`, vorher kein Modell (Planung ohne Raumheizung). | I3, I7, D2 |
| I3 | Nutzbar nur, wenn es auf 2 zurückgehaltenen Tagen besser ist als „Temperatur bleibt“; Zeitkonstante 5–500 h; Ridge-Regression. | `house_model.py` | ins Design | Prüft das Ergebnis an Daten, statt ihm blind zu trauen – passt zum Grundsatz. | I2 |
| I4 | Planung: unter dem Spielraum 50 €/K·h, darüber 0,2 €/K·h; WP-Bedarf (Wärme / Carnot-COP, 40 % von Carnot bei 35 °C Vorlauf) aus der Lastprognose, höchstens 70 % je Viertelstunde. | `house_plan.py`, `defaults.py` | ins Design | Feste Annahmen der Planung, beeinflussen kein Lernen. Die 40 % von Carnot und der feste Vorlauf 35 °C sind Schätzungen – im Design als solche kennzeichnen. | I5, D1 |
| I5 | Planung nur für die erste WP, nur im Programm Heizen, ohne Sonne, ohne Batteriespeicher kein Plan; Neuplanung bei Änderung von Soll, Spielraum, Modus. | `house_plan.py`, `api` | ins Design | Bekannte Einschränkungen, keine verdeckte Rechenregel. | I4 |
| I6 | Planung nutzt den letzten WP-Messwert höchstens 10 min alt. | `defaults.py` | ins Design | Älter wäre nicht mehr aktuell. | — |
| I7 | **Neu:** Hausmodell aus `heat_pump_samples` verwirft jede Stunde mit irgendeiner Zeile Warmwasser, Abtauen, Nachlauf oder ungültig, und braucht zwei solche Stunden hintereinander. | `house_model.py` (`_hour_left_out`, `_transitions`) | **ändern** | Verwirft gültige Daten statt richtig zu verbuchen: 8 von 50 Stundenpaaren bleiben, bei Frost praktisch keins – das Modell lernt nur an milden Tagen. Vorschlag: alle Stunden, Wärme je Zeile verbuchen (Warmwasser 0, Abtauen wie gemessen). Messwerte je Betriebsart vorher prüfen (Bereitschaft zeigt −1,2 kW). | I2, D2, H2 |

## J. Home Assistant und API (16)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| J1 | Thermostat „Haus“: Attribut `shadow` (veraltet, = `mode` ist `shadow`). | `climate.py` | **ausbauen** | Doppelte Angabe derselben Sache. | J2 |
| J2 | Thermostat „Haus“: viele weitere Attribute (Korrektur, eingefroren mit Gründen, Heizkurve, Außen, ausstehender Wert, Räume, je WP unter `heat_pumps`). | `climate.py` | **ändern** (Nutzer wählt) | Keine Rechenregel, aber jedes Attribut ist Schnittstelle, die gepflegt werden muss. Nur behalten, was in HA genutzt wird. | J1 |
| J3 | Raum-Soll im Thermostat in 0,5-K-Schritten, gespeichert auf 0,1 K. HA „Heizen“ kehrt zur zuletzt gültigen Stufe zurück. | `climate.py`, `defaults.py` | ins Design | Bedienung, keine Rechenregel. | — |
| J4 | Sensoren am Gerät „Sunflow“, Symbol Geist bei „Nur Lernen“; „Schreibvorgänge heute“ `unknown`, bis Budget und Zeitzone bekannt. | `sensor.py` | ins Design | `unknown` statt falscher Zahl – korrekt. | F4 |
| J5 | API zusätzlich: `GET …/shadow_writes`, `GET …/history?hours=1–168`, `PUT …/flow_limits`, `PUT …/learn_stops`, `GET /house/model`. | `api/heat_pump.py`, `api/house.py` | ins Design (kurz) | Schnittstellen, keine Regeln. | K1 |
| J6 | Verlauf-Ereignisse enthalten noch `flow_source` (Quellenwechsel gibt es nicht mehr). | `api/heat_pump.py` | **ausbauen** | Rest entfernter Logik. | — |

## K. UI (16)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| K1 | Verlauf (6 h/24 h/3 Tage) mit Bändern und Stopp-Kreuzen; Anlage als Bild; Tageszählung; „Jetzt schreiben“-Taste. Das Diagramm „Heizkurve“ zeigt dieselben Punkte wie die Berechnung (seit 0.80.33, vorher eigener Kompressor-Filter). | `heat-pump-view.js`, `heat-pump-diagrams.js` | ins Design (kurz) | Anzeige. Grundsatz: das UI filtert nie anders als das Backend. | J5, E7 |

## L. Altlasten (Kompatibilität mit früheren Ständen)

| # | Was | Wo | Bewertung | Begründung | Hängt zusammen mit |
|---|---|---|---|---|---|
| L1 | Sperr-Gründe `adopted`, `min_power`, `budget` aus alten `heat_pump_state.json` werden beim Laden umgedeutet. | `guard.py` (`_ADOPTED`, `_OLD_BLOCKS`) | **ausbauen** | Der Stand ist längst umgeschrieben; Code ohne Wirkung. | E6 |
| L2 | `written_reason` aus Ständen vor 0.80.17 aus den letzten Schreibvorgängen rekonstruiert. | `guard.py` (`_reason_from_writes`) | **ausbauen** | Wie L1. | — |
| L3 | Veraltete Kommentare (z. B. „preparing“ als Schutzschritt, „from part 4.7“). | `budget.py`, `defaults.py` | korrigieren | Falsche Kommentare führen zu falschen Annahmen. | F5 |
