# Datenhaltung und Migrationen

Stand: 23.11.2026 · Status: Regeln; Regel 7 und Import aller Tabellen umgesetzt (0.80.4)

Wie Sunflow gespeicherte Daten bei Updates weiterentwickelt, ohne Nutzerdaten zu verlieren und ohne den
Rückweg auf eine ältere Version (z. B. Wechsel zwischen Sunflow und Sunflow Edge) zu verbauen.

## Bestand

| Datei | Format | Version | Migration |
|---|---|---|---|
| `config.json` | JSON (pydantic) | Feld `version`, `CONFIG_VERSION` | `config/store.py`: `MIGRATIONS` je Schritt, Original bleibt als `config.v<n>.json`; neuere Konfiguration: unbekannte/ungültige Felder auf Standard (`_repaired`); atomar mit `fsync`; Version 6: `house.climate` (Raum-Soll aus `comfort_min`); `house.heating_surface` additiv ohne Migration (fehlt/unbekannt → `underfloor`); je Gerät `flow_min_c`/`flow_max_c` additiv ohne Migration (fehlt → Startgrenzen des Heizflächentyps, ungültig → auf Standard); je Gerät `flow_active` (Schalter aktives Schreiben, Wärmepumpe) additiv ohne Migration (fehlt oder nicht genau `true` → aus); `house.fast_change` (schnelle Änderung eines Raums, `heat_pump.md` 6) additiv ohne Migration (fehlt oder Feld ungültig → Startwert des Felds); `house.climate.preheat_k`/`lower_k` (Spielraum der Planung, `heat_pump.md` 6) additiv ohne Migration (fehlt, keine Zahl oder außerhalb 0–3 K → Startwert 1,0 bzw. 0); je Gerät `curve_slope_start`/`curve_shift_start`/`curve_start_source` (Startwerte der Heizkurve, `heat_pump.md` 8) additiv ohne Migration (fehlt, keine Zahl oder außerhalb 0–10 bzw. ±10 K → nicht gesetzt) |
| `shadow.db` | SQLite | `PRAGMA user_version`, `SCHEMA_VERSION` | `shadow/store.py`: `SCHEMA_STEPS` je Version, `migrate()` beim Start, vorher Kopie `shadow.v<n>.db` (Regel 7); neuere Datenbank bleibt unberührt. Version 10: Tabelle `heat_pump_samples` (5-min-Werte der Wärmepumpe, 1 Jahr, `heat_pump.md` 15); Version 11: Spalten `target_c` (Vorlauf-Ziel) und `room_setpoint_c` (Raum-Soll) in `heat_pump_samples` |
| `eeprom_writes.json` | JSON | – | Tageszähler und Zeiten der letzten 26 h je Entity (`times`, Budget je Stunde/Tag), verwerfbar |
| `heat_pump_state.json` | JSON | Feld `format` (1) | Laufzeit-Zustand der Vorlaufregelung (schnelle Änderungen der Räume `ventilation`, Quelle des Hausmodells, je Gerät Korrektur je Strategie, zuletzt verwendete Heizkurve `curve_used`, Zustand der Schutzschicht `guard`: zuletzt geschrieben (im Schatten: geschrieben hätte) mit Grund, Sperre mit Grund, letzte Schreibvorgänge, fremde Schreiber `foreign`: zuletzt gesehener Max T-Vorlauf, laufende Episode, ESP-Regelung; seit 0.80.18 `active`/`writing`: Schalter zuletzt gesehen und ob Sunflow übernommen hat – fehlt das, übernimmt es beim nächsten Takt neu mit Korrektur 0) und Cache des Gelernten (je Gerät `curve` und `learned`, je eigenes Feld `format`; Regel 10: aus `heat_pump_samples` neu berechnet, ein fehlender oder fremder Cache wird neu gerechnet); verwerfbar, unbekannte Schlüssel werden ignoriert, geschrieben nur bei Änderung, atomar mit `fsync`; im Export, nicht im Import (`heat_pump.md` 15) |
| `heat_pump_manifests.json` | JSON | Feld `format` (1) | zuletzt geladenes Manifest je Gerät (Manifest, Hash, Ladezeit, `sw_version`, bereits gemeldete Hinweise); verwerfbar: fehlt oder ist sie kaputt, gilt das nächste Laden als neu; unbekannte Schlüssel werden ignoriert; geschrieben nur bei Änderung, atomar mit `fsync`; Einträge entfernter Geräte fallen beim Laden weg; nicht im Export (`heat_pump_device.md` 1.6) |

Export/Import (`api/system.py`): ein Export einer älteren Version wird beim Import migriert, Spalten einer
neueren Version werden weggelassen. Die Export-Datei wird in ein temporäres Verzeichnis entpackt und dort
migriert; das Original bleibt unverändert.

`ShadowStore.merge_from` führt alle Tabellen zusammen (umgesetzt, Tests in `tests/test_shadow_data.py`):
- `IMPORT_TABLES` in `shadow/store.py` legt je Tabelle fest, ob sie beim Import übernommen wird
  (Standard: ja; ausnehmen nur, was verwerfbar ist). Eine neue Tabelle muss dort eingetragen werden
  (ein Test vergleicht die Liste mit dem Schema); fehlt sie trotzdem, wird sie übernommen.
- Je Tabelle werden die Spalten übernommen, die beide Versionen kennen; Zeilen mit gleichem
  Primärschlüssel werden ersetzt (`INSERT OR REPLACE … SELECT` über die per `ATTACH` angehängte
  Export-Datei, ohne die Zeilen in den Speicher zu laden).
- Eine Tabelle, die nur die Export-Datei kennt (neuere Version), wird übersprungen; eine, die nur die
  eigene Datenbank kennt, bleibt unverändert.
- Alles in einer Transaktion: Ein Fehler lässt die eigene Datenbank wie vorher.
- Rückgabe: Anzahl je Tabelle; `POST /import` liefert sie als `imported` (dazu weiterhin
  `imported_slots` für die Meldung im UI).

Grundsatz zur Ablage: Rohmessungen, die HA ohnehin hat, aus HA lesen (History/Statistiken) statt kopieren.
Was HA nicht oder nicht lange genug hat (Pläne, Entscheidungen, Journal, gefilterte Messwerte mit
Zuständen), in `shadow.db` als eigene Tabelle – keine weiteren Datenbankdateien. Wichtige Ergebnisse
zusätzlich als Entities über `sunflow_ems`, damit sie in HA sichtbar sind.

## Grundsatz

Eine ältere Sunflow-Version muss mit Daten einer neueren weiterarbeiten können (sie lässt sie unverändert).
Deshalb sind Änderungen am Schema **additiv**.

## Regeln

1. **Nur ergänzen.** Spalten und Tabellen werden hinzugefügt, nicht umbenannt oder gelöscht, solange eine
   ältere Version sie noch lesen oder schreiben könnte.
2. **Umbenennen in zwei Schritten.** Neue Spalte anlegen und im selben Schritt per `UPDATE` befüllen; ab da
   beide schreiben. Erst nach mehreren Releases die alte nicht mehr schreiben. Löschen ist optional (leere
   Spalten kosten in SQLite fast nichts).
3. **Neue Bedeutung = neue Spalte.** SQLite ist dynamisch typisiert, der deklarierte Typ ist zweitrangig.
   Ändert sich Inhalt, Einheit, Vorzeichen oder JSON-Struktur, gibt es eine neue Spalte mit Einheit im
   Namen (`_w`, `_wh`, `_c`, `_pct`); eine bestehende Spalte wird nie umgedeutet.
4. **JSON-Spalten und JSON-Dateien:** neue Schlüssel erlaubt; beim Lesen unbekannte Schlüssel ignorieren,
   fehlende mit Standard füllen; bestehende Schlüssel nie umdeuten.
5. **Schritte sind wiederholbar.** Jeder DDL-Befehl committet einzeln; ein unterbrochener Schritt läuft
   beim nächsten Start erneut. `migrate()` ignoriert „duplicate column“/„already exists“; Daten-Schritte
   (`UPDATE`) so schreiben, dass ein zweiter Lauf nichts ändert (z. B. `WHERE neu IS NULL`).
6. **Schritte nur anhängen.** Ein veröffentlichter Schritt wird nie geändert; Korrekturen sind neue Schritte.
7. **Backup vor der Migration.** Vor dem ersten Schritt eine Kopie `<name>.v<n>.db` über die
   SQLite-Backup-API, wie `config.v<n>.json`. Nur wenn tatsächlich migriert wird (`user_version` <
   `SCHEMA_VERSION`), nicht bei jedem Start; eine vorhandene Kopie gleichen Namens wird nicht
   überschrieben; eine neue, leere Datenbank wird nicht kopiert. Schlägt das Backup fehl (z. B. Platte
   voll), wird nicht migriert und der Fehler gemeldet (`ShadowBackupError`, die App startet nicht).
   Die Kopie entsteht unter `<name>.v<n>.db.tmp` und wird erst vollständig umbenannt. Umgesetzt in
   `shadow/store.py` (`backup_before_migration`); Export-Dateien beim Import brauchen keine Kopie, sie
   werden ohnehin in einem temporären Verzeichnis migriert.
8. **Unvermeidbarer Umbau** (z. B. Primärschlüssel): neue Tabelle unter neuem Namen anlegen und befüllen;
   die alte bleibt für ältere Versionen bestehen.
9. **Große Datenmengen** (Jahre an 5-min-Werten): Daten-Migrationen blockweise oder im Hintergrund, nicht
   den Start blockieren; auf einem Raspberry Pi messen.
10. **Abgeleitetes nicht speichern.** Was sich aus Rohdaten neu berechnen lässt (Modellparameter,
    Kennlinien, Statistiken), liegt in einem verwerfbaren Cache. Ändert sich das Verfahren, wird neu
    gerechnet statt migriert. Rohdaten in kanonischen Einheiten speichern.
11. **Tests.** Für jede Schemaversion eine kleine Beispiel-Datenbank bzw. Konfiguration; geprüft wird:
    Migration auf den aktuellen Stand, zweiter Lauf ändert nichts, neuere Version bleibt unberührt,
    Export/Import über Versionen.
12. **Namen mit Präfix je Bereich.** Neue Tabellen beginnen mit dem Bereich, zu dem sie gehören (z. B.
    `heat_pump_` für die Wärmepumpen-Regelung). So sind sie bei SQL-Abfragen erkennbar und lassen sich
    gezielt zurücksetzen oder exportieren. Bestehende Tabellen (`slots`, `plan_ahead`, …) werden nicht
    umbenannt (Regel 1).
