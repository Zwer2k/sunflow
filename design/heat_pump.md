# Vorlaufregelung für Wärmepumpen

Stand: 25.11.2026. Verbindliche Beschreibung, wie Sunflow eine Wärmepumpe regelt. Code und Dokument
entsprechen sich; was noch nicht umgesetzt ist, steht in Abschnitt 17. Geräteanbindung (Manifest, Profil,
ESPHome-Komponenten): `heat_pump_device.md`. Speicherregeln: `datenhaltung.md`. Umgesetztes, das hier noch
fehlt und zu klären ist: `heat_pump_undocumented.md`.

## 1. Ziel

- Raum-Ist folgt dem Raum-Soll (bzw. dem Planziel, 14) möglichst genau; nach einer Soll-Änderung nähert
  es sich in Stunden, ohne Überschwingen.
- Von Anfang an brauchbar (Heizkurve mit Startwerten), mit der Zeit besser (Lernen ohne Wartezeit).
- Kompressor möglichst ohne ungewollte Abschaltungen, möglichst wenige Schreibvorgänge (EEPROM).
- Einfach und nachvollziehbar: jeder Schritt hat einen Grund, der angezeigt wird.

Erstes Gerät: Rotex/Daikin HPSU über die ESPHome-Komponente `daikin_rotex_can` (CAN, lesen und schreiben),
optional `daikin_rotex_uart` (UART, nur lesen). Andere Wärmepumpen bekommen ein Profil.

## 2. Begriffe

| Begriff | Bedeutung |
|---|---|
| Tv, Tr | Vorlauf, Rücklauf (gemessen); `tv_max` = Spitze von Tv zwischen zwei Meldungen |
| Vorlauf-Soll (wirksam) | Soll, mit dem die WP gerade regelt |
| Stellwert | was Sunflow schreibt: bei der HPSU „Fest“ Max T-Vorlauf |
| Raum-Ist | gewichtetes Mittel der Raumsensoren ohne gerade gestörte Räume (6) |
| Raum-Soll | eingestellt (Thermostat „Haus“), nicht gelernt |
| Planziel | Raumtemperatur je Viertelstunde aus der Planung, im Spielraum um den Raum-Soll (14) |
| Außen (Regelung) | gemessene Außentemperatur plus erwartete Änderung aus der Prognose (7) |
| Heizkurve | Vorlauf abhängig von Raum-Soll und Außen, Steilheit und Verschiebung gelernt (8) |
| Korrektur | langsamer PI-Anteil aus Raum-Soll − Raum-Ist (9) |
| Vorlauf-Ziel | Heizkurve + Korrektur |

## 3. Ablauf

```
 Außen gemessen ─┐
 Prognose ───────┴─▶ Außen (Regelung) ─┐
 Raum-Soll / Planziel ─────────────────┼─▶ Heizkurve ──┐
 Raum-Ist ─▶ Korrektur ─────────────────────────────────┴─▶ Vorlauf-Ziel ─▶ Grenzen ─▶ Schutz ─▶ Schreiben ─▶ Max T-Vorlauf
```

Jede Minute wird alles neu berechnet und angezeigt. Geschrieben wird nur, wenn die Regeln in 11 und 12 es
verlangen.

## 4. Modus und Zustände

**Modus** (eine Auswahl im UI, im Thermostat „Haus“ Aus/Heizen):

| Modus | zeichnet auf | lernt | schreibt |
|---|---|---|---|
| Aus | nein | nein | nein |
| Nur Lernen (Standard) | ja | ja | nein, zeigt „würde schreiben“ |
| Lernen + Regeln | ja | ja | ja (nur in Sunflow einschaltbar, mit Bestätigung) |

Einschalten von „Lernen + Regeln“ geht nur, wenn die WP kompatibel und erreichbar ist, Messwerte da sind,
HK-Funktion „Fest“ gilt und die ESP-Regelung aus ist. Beim Einschalten schreibt Sunflow nichts; es übernimmt
Max T-Vorlauf und Sperre, wie sie sind. Ausschalten stellt nichts zurück.

**Zustände** (über das Profil auf kanonische Werte abgebildet):
- Programm: `heating` (Heizen, Absenken, Automatik), `summer`, `standby`, `cooling`, `unknown`. Nur bei
  `heating` regelt Sunflow.
- Aktivität: `heating`, `hot_water`, `defrost`, `idle`, `cooling`, `unknown`.
- **Gültiger Heizbetrieb** (Voraussetzung für Korrektur und Lernen): Programm und Aktivität `heating`,
  Bypass-Ventil seit 10 min bei 0 %, kein Nachlauf (10 min nach Warmwasser/Abtauen), keine Sperre, keine
  Störung (Fehlercode `E…`), die WP begrenzt den Vorlauf nicht selbst (wirksamer Soll > 5 min mehr als
  0,5 K unter Max T-Vorlauf), Gerät nutzbar. Sonst ist die Korrektur eingefroren, mit Grund.
- Unbekannte Werte: nichts schreiben, einmal ins Journal.

## 5. Strategie

Sunflow liest die HK-Funktion der WP und ändert sie nie. Nur „Fest“ wird geregelt (Stellwert = Max
T-Vorlauf). Bei „Witterungsgeführt“ lernt Sunflow, schreibt aber nichts.

## 6. Räume und Raum-Soll

- Räume: Temperatursensoren mit Gewicht. Ein Raum zählt nicht, wenn sein Gerät seit 2 h nichts gemeldet
  hat (jüngste Meldung irgendeines Sensors desselben Geräts) oder er nicht verfügbar ist.
- **Schnelle Änderung** (Lüften, Kochen): ändert sich ein Raum um ≥ 0,8 K in 20 min (hoch oder runter),
  zählt er nicht, bis er sich 30 min lang um ≤ 0,5 K bewegt, höchstens 2 h; Werte einstellbar. Danach zählt er
  mit seinem Rest. Wechselt die Zusammensetzung des Raum-Ist, springt die Korrektur nicht.
- **Raum-Soll**: 18–27 °C, über das Thermostat „Haus“ oder das UI. Eine Änderung wirkt sofort über die
  Heizkurve (8); die Korrektur wird dabei nicht angehalten.
- **Spielraum**: „Vorheizen bis +x K“ (Start 1,0) und „Absenken bis −y K“ (Start 0), je 0–3 K, für die
  Planung (14).

## 7. Außentemperatur für die Regelung

**Außen = gemessen + (Prognose in h − Prognose jetzt)**, die Änderung begrenzt auf ±5 K.

- Gemessen: Außenfühler der WP oder eigener Sensor (Einstellung), Mittel der letzten 15 min.
- Prognose: stündliche Werte der Wetter-Entity (`weather.get_forecasts`, `hourly`), zwischen den Stunden
  linear verbunden. Durch die Differenz fällt ein fester Versatz zwischen Fühler und Prognose heraus.
- Vorausschau h: so lange, wie ein höherer Vorlauf braucht, bis er im Raum ankommt (Estrich bzw.
  Heizkörper). Start je Heizflächentyp: Fußboden 3 h, gemischt 2 h, Heizkörper 1 h.
- Ohne Prognose (keine Wetter-Entity, Abruf fehlgeschlagen): nur der gemessene Wert.
- Sunflow speichert je 5 min die verwendete Prognose-Änderung.
- Später gelernt (17): aus den aufgezeichneten Tagen nachrechnen, mit welcher Vorausschau (1–6 h) der
  Raum am wenigsten vom Soll abgewichen wäre, und diese nehmen; ebenso, ob die Prognose-Änderung
  abgeschwächt werden soll.
- Sensoren in HA: „Außen +1 h“ bis „+6 h“ (gemessen + Prognose-Änderung) und „Außen (Regelung)“.

## 8. Heizkurve

**Vorlauf = R + S · (R − A) + P**

- R = Raum-Soll (später das Planziel, 14), A = Außen (Regelung), S = Steilheit, P = Verschiebung (K).
- **Startwerte S₀, P₀ je WP**, einstellbar. Solange keine gesetzt sind, liest Sunflow S₀ einmal aus der WP,
  wenn das Profil eine Rolle dafür hat (HPSU „Heizkurve“), und speichert ihn; P₀ = 0 (die HPSU kennt keine
  Verschiebung). Ohne Wert der WP gilt S₀ je Heizflächentyp (Fußboden 0,6, gemischt 1,0, Heizkörper 1,4),
  nicht gespeichert. Danach gelten die gespeicherten Werte, egal was die WP meldet.
- Startwerte ändern: eingeben, „aus der WP neu einlesen“ (S₀ wie gemeldet, P₀ = 0) oder „Gelerntes
  übernehmen“ (S, P der gelernten Kurve, z. B. als Start für die nächste Heizperiode). Wirkt sofort, auch in
  der Anzeige.
- Physik: Das Haus verliert Wärme proportional zu R − A, die Heizflächen geben sie etwa proportional zu
  Vorlauf − R ab; für Fußboden genügt eine Gerade. Begrenzung durch die Grenzen (10).
- Eine Soll-Änderung um 1 K hebt den Vorlauf sofort um 1 + S Kelvin.

**Lernen (stündlich, ohne Wartezeit):**
- Datenpunkte: jede 5-min-Zeile mit gültigem Heizbetrieb ohne schnelle Änderung, in der Raum-Ist nahe am
  Raum-Soll lag (±0,3 K): (R − A, wirksamer Vorlauf-Soll − R), Gewicht 1/12 Stunde. Dieser Vorlauf hat
  gereicht. Keine Gruppierung in Stunden, keine Mindestzahl. Zeilen ohne gespeicherten Raum-Soll zählen nicht.
- Gewichtete Ausgleichsgerade über die letzten 28 Tage (Halbwertszeit 14 Tage), **zu den Startwerten hin
  gezogen**: die Startkurve zählt wie 24 Stunden auf ihr. S bleibt im Bereich der Steilheits-Entity der WP
  (Manifest, HPSU 0–2,55; ohne sie 0–3), P in ±10 K. Derselbe Bereich gilt für den Startwert S₀.
- **Anteil des Gelernten** = Gewicht der Datenpunkte ÷ (Gewicht + 24): anfangs 0 % (nur Startwerte), dann
  steigend; 100 % wird nie erreicht (alle Zeilen der 28 Tage ≈ 94 %). Angezeigt mit S, P und S₀, P₀.
- Ändert sich die gelernte Kurve, wird die Korrektur um alt − neu verschoben (in ±3 K): das Vorlauf-Ziel
  springt nicht, die Korrektur wandert in die Kurve. Ändern sich die Startwerte (vom Nutzer oder, ohne
  gespeicherte, über den Heizflächentyp), gilt die neue Kurve sofort ohne Verschiebung; die Datenpunkte
  bleiben und werden zu den neuen Startwerten hin neu verrechnet.

## 9. Korrektur

- PI auf Raum-Ist: ΔK = Kp/Ti · e · Δt − Kp · ΔRaum-Ist, e = Raum-Soll − Raum-Ist; Kp = 1 K/K, Ti je
  Heizflächentyp (Fußboden 6 h, gemischt 4 h, Heizkörper 2 h), Totzone ±0,2 K, Begrenzung ±3 K.
- Eingefroren außerhalb des gültigen Heizbetriebs, wenn kein Raum zählt, und an einer Grenze (nur in deren
  Richtung). Ein Raum mit schneller Änderung fällt nur aus dem Raum-Ist (6); die Korrektur läuft mit den
  übrigen Räumen weiter.
- Bei „Nur Lernen“ wirkt sie nicht zurück. Beim Einschalten von „Lernen + Regeln“ wird sie auf
  Max T-Vorlauf der WP − Heizkurve gesetzt (in ±3 K): das Vorlauf-Ziel springt nicht.

## 10. Grenzen

- Stellwert in [min, max]: Einstellung je WP, sonst Start je Heizflächentyp (Fußboden 25–40 °C, gemischt
  25–50, Heizkörper 30–55), immer im Bereich der Entity laut Manifest. Max ist zugleich der höchste
  Vorlauf für die Heizflächen (Estrich) und darf die WP-eigene Grenze nicht überschreiten.
- Rundung auf die Schrittweite der Entity; Schutzwerte aufwärts.
- An einer Grenze (Vorlauf-Ziel oder Leistungsgrenze der WP) läuft die Korrektur nicht weiter in diese
  Richtung.

## 11. Schutz gegen Abschaltungen

Sunflow sagt das Schalten des Kompressors nicht voraus, es reagiert darauf.

- **Normal:** Läuft der Kompressor im Heizbetrieb, ist der Stellwert das Vorlauf-Ziel.
- **Wiederanlauf:** Ist der Kompressor 5 min aus, obwohl Wärme gewollt ist (Programm Heizen, keine Sperre,
  kein Warmwasser/Abtauen, Raum-Ist < Soll + 0,5 K), hebt Sunflow den Stellwert einmal auf Tv +
  Einschaltabstand (Start 3 K). Der Grund des Aus spielt keine Rolle.
- **Nach dem Start** 10 min warten, dann in einem Schritt auf max(Vorlauf-Ziel, Untergrenze).
- **Untergrenze** = höchster Tv der letzten 5 min − Abschaltgrenze + Schwankung. Steigt Tv darüber, führt
  Sunflow den Stellwert nach (+ Schwankung als Abstand).
- Während Warmwasser und Abtauen wird nichts geschrieben.
- **Abschaltgrenze lernen:** Jeder Kompressor-Stopp wird mit Tv, `tv_max`, TVBH, Tv des UART-Knotens,
  wirksamem Soll, Aktivität und Sperre aufgezeichnet und eingeordnet: Abtauen/Warmwasser folgt innerhalb
  10 min, Sperre, andere Aktivität – sonst „Abschaltregel“. Abschaltgrenze = 10-%-Quantil von Tv − Soll
  dieser Stopps (0,5–8 K, letzte 14 Tage) ab 5 Stopps; größer als der Profilwert erst ab 10. Bis dahin der
  Profilwert (3 K).
- **Lernphase** (Schalter je WP, Start an): keine Untergrenze, Sunflow senkt bis aufs Vorlauf-Ziel und
  lässt den Kompressor abschalten; der Wiederanlauf holt ihn zurück. Der Nutzer beendet sie.
- **Gewollt aus:** Raumthermostat-Sperre bei Raum-Ist ≥ Soll + 0,5 K, aufgehoben bei ≤ Soll + 0,2 K,
  jeder Zustand mindestens 20 min. Eine Sperre, die Sunflow nicht gesetzt hat, hebt es nie auf; es zeigt
  sie an und regelt erst danach wieder.

## 12. Schreiben

- Erste Änderung nach einer Ruhepause sofort; danach Mindestabstand 5 min (Wiederanlauf, Nachführen:
  2 min), dazwischen gilt nur der letzte Wert. Normale Werte nur bei Abweichung > 0,5 K und nur bei gültigem
  Heizbetrieb (4); Wiederanlauf und Nachführen auch sonst.
- **Wartegrund:** Weicht der Stellwert vom Vorlauf-Ziel ab, zeigt der Status, warum er wartet (nach dem Start,
  kein gültiger Heizbetrieb mit Gründen, Warmwasser/Abtauen, Kompressor aus, Sperre, Mindestabstand, Budget,
  Totzone) und, wenn bekannt, bis wann (Ende von Nachlauf und Bypass-Wartezeit, Start + 10 min, Mindestabstand).
- Budget (Profil): 48 je Tag, dazu 24 nur für Wiederanlauf/Nachführen, höchstens 6 je Stunde; Tag in der
  Zeitzone von HA. Ist es erschöpft, wartet der Wert (Journal einmal). Die Sperre zählt nicht (kein EEPROM).
- „Jetzt schreiben“ (UI) überspringt den Mindestabstand, nicht das Budget.
- Nicht geschrieben bei Programm ≠ Heizen, Strategie ≠ Fest, Störung, unbekannten Werten, ESP-Regelung
  aktiv, Gerät nicht nutzbar.
- **Fremde Schreiber:** Ändert sich Max T-Vorlauf, ohne dass Sunflow geschrieben hat, folgt Sunflow dem
  neuen Wert, wartet den Mindestabstand ab und warnt einmal je Episode im Journal.
- Nach dem Schreiben prüft Sunflow, ob die WP den Wert übernommen hat (Warnung sonst).
- Ende der Steuerung oder Ausfall: nichts wird zurückgestellt.

## 13. Lernen – Übersicht

| Was | wofür | Abschnitt |
|---|---|---|
| Heizkurve (S, P) | Vorlauf-Ziel | 8 |
| Abschaltgrenze, Schwankung | Schutz | 11 |
| Leistungsbereich der WP | Grenzen, Planung | 10 |
| Hausmodell (Heizfläche + Haus) | Planung, Vorschau der Heizkurve | 14 |
| Vorausschau, Güte der Prognose | Außen (Regelung), später | 7 |

Gelernt wird aus den 5-min-Messwerten (`heat_pump_samples`), stündlich neu; was gelernt ist, ist ein
verwerfbarer Cache. Bei „Aus“ wird nichts aufgezeichnet und nichts gelernt. Heizkurve und Hausmodell sind
getrennt: die Heizkurve regelt, das Hausmodell beschreibt nur das Haus.

## 14. Hausmodell und Planung (Vorheizen)

### 14.1 Hausmodell

Ein Modell mit zwei Speichern, für die Planung (14.3) und die Vorschau der Heizkurve (16):

- Heizfläche (Estrich bzw. Heizkörper), Temperatur Ts: Cs · dTs/dt = Q − Kf · (Ts − Tr)
- Haus, Temperatur Tr (Raum-Ist): Cb · dTr/dt = Kf · (Ts − Tr) − U · (Tr − A) + G

Q Wärme der WP in die Heizfläche (kW), A außen, U Wärmeverlust (kW/K), Kf Übergang Heizfläche → Raum (kW/K),
Cs, Cb Wärmespeicher (kWh/K), G innere Gewinne (kW, konstant). Ohne Sonne (17).

- **Daten:** nur `heat_pump_samples` der ersten WP, letzte 28 Tage, Halbwertszeit 14 Tage. Gerechnet in
  5-min-Schritten; jede Zeile zählt, keine Stunde wird verworfen.
- **Wärme Q je Zeile:** Aktivität Heizen (auch Nachlauf, ungültiger Heizbetrieb) wie gemessen; Warmwasser,
  Abtauen (Wärme kommt aus dem Warmwasserspeicher), Bereitschaft und alles andere 0. Ohne Wärmemessung
  gibt es kein Hausmodell.
- **Lücken** (fehlende Zeilen, z. B. Modus „Aus“; eine Heiz-Zeile ohne Wärmewert fehlt ebenso): bis 15 min
  gelten die Werte der Zeile davor (ohne Raum-Ist), länger beginnt ein neuer Abschnitt; er startet beim
  gemessenen Raum-Ist, die Heizfläche um einen gemeinsamen gelernten Abstand darüber.
- **Anpassen:** kleinste Quadrate der frei vorausgerechneten Raumtemperatur gegen das Raum-Ist als
  Stundenmittel (mittelt die Stufen der Sensoren heraus), je Stunde gewichtet mit ihrem Anteil an Zeilen und
  ihrem Alter. Zeilen mit schneller Änderung (6) liefern keinen Vergleichswert, ihre Wärme zählt. Gelernt: U,
  Kf, Cs, Cb, G, Startabstand der Heizfläche; je Kennwert die Unsicherheit aus der Anpassung. Grenzen: U
  0,01–2 kW/K, Kf 0,05–20 kW/K, Cs 0,2–200 kWh/K, Cb 1–500 kWh/K, G ±5 kW, Startabstand −5…20 K. Liegt ein
  Kennwert an seiner Grenze, ist das Modell nicht plausibel.
- **Übergang Wasser → Heizfläche** (für die Vorschau): Q = Kw · Anteil · (Tw − Ts), Tw = Mittel aus Tv und Tr
  bei Heizbetrieb, Anteil = Kompressoranteil; Kw nach dem Anpassen aus den Heiz-Zeilen (gemessenes Q, Ts aus
  dem Modell) gelernt.
- **Test:** angepasst ohne die letzten 24 h, dann diese 24 h frei vorausgerechnet (Start beim gemessenen
  Raum-Ist, Heizfläche aus dem Modell); verglichen mit „Temperatur bleibt“ (Raum-Ist am Start).
- **Anzeige von Anfang an:** ab 24 Stunden mit Raum-Ist die Kennwerte mit Unsicherheit, Güte (Abweichung
  über alle Stunden, Test gegen „bleibt“), Datenumfang (Stunden mit Raum-Ist, mit Heizen, Spanne außen) und
  die letzten 7 Tage gemessen und Modell.

### 14.2 Nutzung durch die Planung

- Schalter „Hausmodell in der Planung“: Aus / Automatisch (Standard) / Immer.
- **Nutzbar** bei Automatisch: mindestens 48 h Daten, plausibel und im Test besser als „Temperatur bleibt“.
  Bei Immer: sobald es ein plausibles Modell gibt.
- Nicht nutzbar (oder Aus): kein Vorheizen, Planziel = Raum-Soll. Angezeigt wird der Grund.

### 14.3 Planung

- Die Batterie- und Verbrauchsplanung plant die WP mit, je Viertelstunde mit beiden Speichern des Modells
  (Heizfläche und Haus, ab dem Zustand jetzt): bei PV-Überschuss oder billigem Strom bis „Vorheizen bis“ über
  den Soll, bei teurem Strom bis „Absenken bis“ darunter. Jedes Kelvin Abweichung kostet 0,05 € je Stunde;
  ohne Ersparnis bleibt es beim Soll.
- Start: Raum-Ist jetzt, Heizfläche um den Abstand Heizfläche − Raum am Ende der Daten darüber.
- Strom der WP = Q / COP, COP geschätzt (40 % von Carnot bei 35 °C Vorlauf); Q höchstens die gelernte höchste
  Heizleistung. Bedarf ohne Planung (Soll halten): Q = U · (Soll − A) − G.
- Wärme, die am Ende des Plans in Haus und Heizfläche bleibt, zählt mit dem mittleren Preis und COP: das Haus
  höchstens bis „Vorheizen bis“, die Heizfläche höchstens bis zu der Temperatur, die das Haus dort hält.
- Das Planziel je Viertelstunde ist der Eingang R der Heizkurve (8) und der Sollwert der Korrektur (9).
- Bei „Nur Lernen“ wird der Vorheiz-Plan nur angezeigt.

## 15. Speicherung

- `heat_pump_samples` in `shadow.db`: je WP und 5 min Mittelwerte (Tv, Tr, TVBH, `tv_max`, wirksamer Soll,
  Max T-Vorlauf, geschriebener Wert, Vorlauf-Ziel, Raum-Soll, Wärme, außen, BPV, Kompressoranteil),
  Zustände, Räume, Ereignisse (Schreibvorgänge, Kompressor-Start/-Stopp, fremde Änderungen), Flags
  (gültig, Gründe, Sperre, schnelle Änderung). Ein Jahr.
- `heat_pump_state.json`: Korrektur, geschriebener Wert, Sperre, fremde Schreiber, Caches des Gelernten.
- `config.json`: Raum-Soll, Modus, Spielraum, Räume, Heizflächentyp, Außentemperatur-Quelle, je WP
  Grenzen, „Lernen + Regeln“, Lernphase, Startwerte der Heizkurve mit Herkunft, Hausmodell in der Planung
  (Aus/Automatisch/Immer).
- `eeprom_writes.json`: echte Schreibvorgänge (Budget).

## 16. Home Assistant und UI

- Thermostat „Haus“: Raum-Soll, Raum-Ist, Aus/Heizen; Attribute Vorlauf-Ziel, Stellwert, Planziel, Grund.
- Sensoren je WP: Vorlauf-Ziel, Stellwert, Schreibvorgänge heute; dazu die Prognose-Sensoren (7).
- UI (Einstellungen → Wärmepumpe): Statuszeile, Modus, Raum-Soll; je WP Regelkette (Raum-Ist und Raum-Soll
  → Korrektur, Raum-Soll und Außen → Heizkurve; ein Klick auf einen Block zeigt darunter Einzelheiten, bei
  der Heizkurve die Formel mit den aktuellen Zahlen, den Anteil des Gelernten und die Startwerte zum
  Einstellen, beim Schutz Stellwert → Vorlauf-Ziel mit Wartegrund), Verlauf, Anlage, Schreiben und Schutz, Gelernt (Heizkurve, Anteil des Gelernten, Startwerte
  nur angezeigt, Abschaltgrenze, Stopps, Lernphase); Hausmodell; Einstellungen. Erklärtexte über „?“.

### Vorschau der Heizkurve

Zeigt, wie sich der Raum mit einer Heizkurve entwickeln würde, ohne etwas zu ändern (nur Anzeige, keine
Wirkung auf Regelung und Lernen). Simuliert wird nur die Heizkurve, ohne Korrektur, Schutz und Planung.

- **Modell:** das Hausmodell (14.1); ohne Hausmodell keine Vorschau. Die Wärme kommt aus der Vorlauftemperatur
  über den gelernten Übergang Kw.
- **Vorhersage:** ab dem gemessenen Raum-Ist, Heizfläche aus dem Modell. Vorlauf = R + S·(R − A) + P in den
  Grenzen des Stellwerts (10), Wasser = Vorlauf − mittlerer Abstand wirksamer Soll − Wasser der Daten,
  Kompressoranteil = Mittel der letzten 7 Tage. Außen: stündliche Prognose der Wetter-Entity, danach die
  tägliche (Mittel aus Höchst- und Tiefstwert), ohne Prognose der gemessene Wert.
- **Kurven:** gemessen und Modell der letzten 48 h; Vorhersage 1–7 Tage (wählbar) mit der aktuellen Kurve
  und mit eingegebenen S, P (und Raum-Soll); Raum-Soll als Linie, Zeitpunkt, an dem das Raum-Ist den Soll
  erreicht. Dazu die Heizkurve, die laut Modell das Haus im Gleichgewicht auf dem Soll hält (aus U, G, Kf, Kw,
  mittlerem Kompressoranteil und Abstand Soll − Wasser); „Werte des Modells eintragen“ setzt sie als Eingabe.

## 17. Offene Punkte und Umsetzungsstand

Umgesetzt: 4–6, 8, 9, 10–13 (Außen noch ohne Prognose: gemessen, Mittel 15 min), 15, 16,
Hausmodell (14.1, 14.2), Planung bis zur Anzeige (14.3).

Offen, in dieser Reihenfolge:
1. **Außen (Regelung)** mit Prognose-Änderung, Vorausschau je Heizflächentyp und Sensoren +1 h bis +6 h (7).
2. **Planziel an den Regler** (14).
3. Nach einigen Wochen: Vorausschau und Gewicht der Prognose-Änderung aus den Daten lernen (7).
4. Lernphase der Abschaltgrenze auswerten (welcher Tv-Fühler, feste Schwelle?); die HPSU kennt laut
   Anleitung den Parameter „Vorlauftemperatur Überhöhung“ (0–4 K, Standard 3: Verdichter stoppt bei Tv >
   Soll + Überhöhung, startet wieder bei Tv < Soll) – prüfen, ob die CAN-Komponente ihn liefert; dann
   auslesen statt lernen.
5. Startwerte Budget nach einigen Wochen prüfen.
6. Später: Sonne im Hausmodell (Einstrahlung und deren Prognose), COP der WP lernen (Strom aufzeichnen)
   statt Carnot-Schätzung (14).
