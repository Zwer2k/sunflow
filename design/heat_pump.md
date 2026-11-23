# Vorlaufregelung für Wärmepumpen

Stand: 23.11.2026. Verbindliche Beschreibung, wie Sunflow eine Wärmepumpe regelt. Code und Dokument
entsprechen sich; was noch nicht umgesetzt ist, steht in Abschnitt 17. Geräteanbindung (Manifest, Profil,
ESPHome-Komponenten): `heat_pump_device.md`. Speicherregeln: `datenhaltung.md`.

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
- Startwerte: S aus der WP, wenn das Profil eine Rolle dafür hat (HPSU `heizkurve`), sonst je Heizflächentyp
  (Fußboden 0,6, gemischt 1,0, Heizkörper 1,4); P = 0.
- Physik: Das Haus verliert Wärme proportional zu R − A, die Heizflächen geben sie etwa proportional zu
  Vorlauf − R ab; für Fußboden genügt eine Gerade. Begrenzung durch die Grenzen (10).
- Eine Soll-Änderung um 1 K hebt den Vorlauf sofort um 1 + S Kelvin.

**Lernen (stündlich, ohne Wartezeit):**
- Datenpunkte: Stunden mit gültigem Heizbetrieb, ohne schnelle Änderung, in denen Raum-Ist nahe am Soll
  lag (±0,3 K): (R − A, verwendetes Vorlauf-Ziel − R). Dieser Vorlauf hat gereicht.
- Gewichtete Ausgleichsgerade über die letzten 28 Tage (Halbwertszeit 14 Tage), **zu den Startwerten hin
  gezogen**: mit wenigen Punkten bleibt die Kurve nahe am Start, mit vielen zählen die Daten. S bleibt in
  0,2–3,0, P in ±10 K.
- Ändert sich die gelernte Kurve, wird die Korrektur um alt − neu verschoben (in ±3 K): das Vorlauf-Ziel
  springt nicht, die Korrektur wandert in die Kurve. Ändert sich der Startwert (Heizkurve der WP oder
  Heizflächentyp umgestellt), gilt die neue Startkurve sofort ohne Verschiebung, bis neu gelernt ist.

## 9. Korrektur

- PI auf Raum-Ist: ΔK = Kp/Ti · e · Δt − Kp · ΔRaum-Ist, e = Raum-Soll − Raum-Ist; Kp = 1 K/K, Ti je
  Heizflächentyp (Fußboden 6 h, gemischt 4 h, Heizkörper 2 h), Totzone ±0,2 K, Begrenzung ±3 K.
- Eingefroren außerhalb des gültigen Heizbetriebs, bei schneller Änderung, fehlenden Räumen und an einer
  Grenze (nur in deren Richtung).
- Bei „Nur Lernen“ wirkt sie nicht zurück; beim Einschalten von „Lernen + Regeln“ beginnt sie bei 0.

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
  2 min), dazwischen gilt nur der letzte Wert. Normale Werte nur bei Abweichung > 0,5 K.
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
| Hausmodell | Planung | 14 |
| Vorausschau, Güte der Prognose | Außen (Regelung), später | 7 |

Gelernt wird aus den 5-min-Messwerten (`heat_pump_samples`), stündlich neu; was gelernt ist, ist ein
verwerfbarer Cache. Bei „Aus“ wird nichts aufgezeichnet und nichts gelernt.

## 14. Planung (Vorheizen)

- Das Hausmodell (1R1C: wie schnell das Haus auskühlt, wie viel Wärme es speichert) sagt voraus, wie sich
  der Raum mit und ohne Heizung entwickelt. Damit plant die Batterie- und Verbrauchsplanung die
  Wärmepumpe mit: bei PV-Überschuss oder billigem Strom bis „Vorheizen bis“ über den Soll, bei teurem Strom
  bis „Absenken bis“ darunter. Jedes Kelvin Abweichung kostet 0,05 € je Stunde; ohne Ersparnis bleibt es
  beim Soll.
- Das Planziel je Viertelstunde ist der Eingang R der Heizkurve (8) und der Sollwert der Korrektur (9).
- Ohne gelerntes Hausmodell (unter 7 Tagen oder nicht plausibel) ist das Planziel der Raum-Soll.
- Bei „Nur Lernen“ wird der Vorheiz-Plan nur angezeigt.

## 15. Speicherung

- `heat_pump_samples` in `shadow.db`: je WP und 5 min Mittelwerte (Tv, Tr, TVBH, `tv_max`, wirksamer Soll,
  Max T-Vorlauf, geschriebener Wert, Vorlauf-Ziel, Raum-Soll, Wärme, außen, BPV, Kompressoranteil),
  Zustände, Räume, Ereignisse (Schreibvorgänge, Kompressor-Start/-Stopp, fremde Änderungen), Flags
  (gültig, Gründe, Sperre, schnelle Änderung). Ein Jahr.
- `heat_pump_state.json`: Korrektur, geschriebener Wert, Sperre, fremde Schreiber, Caches des Gelernten.
- `config.json`: Raum-Soll, Modus, Spielraum, Räume, Heizflächentyp, Außentemperatur-Quelle, je WP
  Grenzen, „Lernen + Regeln“, Lernphase.
- `eeprom_writes.json`: echte Schreibvorgänge (Budget).

## 16. Home Assistant und UI

- Thermostat „Haus“: Raum-Soll, Raum-Ist, Aus/Heizen; Attribute Vorlauf-Ziel, Stellwert, Planziel, Grund.
- Sensoren je WP: Vorlauf-Ziel, Stellwert, Schreibvorgänge heute; dazu die Prognose-Sensoren (7).
- UI (Einstellungen → Wärmepumpe): Statuszeile, Modus, Raum-Soll; je WP Regelkette, Verlauf, Anlage,
  Schreiben und Schutz, Gelernt (Heizkurve, Abschaltgrenze, Stopps, Lernphase); Hausmodell; Einstellungen.
  Erklärtexte über „?“.

## 17. Offene Punkte und Umsetzungsstand

Umgesetzt: 4–6, 8, 9, 10–13 (Außen noch ohne Prognose: gemessen, Mittel 15 min), 15, 16,
Planung bis zur Anzeige (14).

Offen, in dieser Reihenfolge:
1. **Außen (Regelung)** mit Prognose-Änderung, Vorausschau je Heizflächentyp und Sensoren +1 h bis +6 h (7).
2. **Planziel an den Regler** (14).
3. Nach einigen Wochen: Vorausschau und Gewicht der Prognose-Änderung aus den Daten lernen (7).
4. Lernphase der Abschaltgrenze auswerten (welcher Tv-Fühler, feste Schwelle?); die HPSU kennt laut
   Anleitung den Parameter „Vorlauftemperatur Überhöhung“ (0–4 K, Standard 3: Verdichter stoppt bei Tv >
   Soll + Überhöhung, startet wieder bei Tv < Soll) – prüfen, ob die CAN-Komponente ihn liefert; dann
   auslesen statt lernen.
5. Startwerte Budget nach einigen Wochen prüfen.
