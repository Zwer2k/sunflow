# Datenhaltung und Migrationen

Stand: 20.11.2026 · Status: Regeln; Regel 7 und Import aller Tabellen spezifiziert, nicht umgesetzt
(erster Schritt der Wärmepumpen-Umsetzung, `heat_pump.md` Abschnitt 16)

Wie Sunflow gespeicherte Daten bei Updates weiterentwickelt, ohne Nutzerdaten zu verlieren und ohne den
Rückweg auf eine ältere Version (z. B. Wechsel zwischen Sunflow und Sunflow Edge) zu verbauen.

## Bestand

| Datei | Format | Version | Migration |
|---|---|---|---|
| `config.json` | JSON (pydantic) | Feld `version`, `CONFIG_VERSION` | `config/store.py`: `MIGRATIONS` je Schritt, Original bleibt als `config.v<n>.json`; neuere Konfiguration: unbekannte/ungültige Felder auf Standard (`_repaired`); atomar mit `fsync` |
| `shadow.db` | SQLite | `PRAGMA user_version`, `SCHEMA_VERSION` | `shadow/store.py`: `SCHEMA_STEPS` je Version, `migrate()` beim Start; neuere Datenbank bleibt unberührt |
| `eeprom_writes.json` | JSON | – | nur Tageszähler, verwerfbar |
| `heat_pump_state.json` (geplant, `heat_pump.md`) | JSON | – | Laufzeit-Zustand, verwerfbar |

Export/Import (`api/system.py`): ein Export einer älteren Version wird beim Import migriert, Spalten einer
neueren Version werden weggelassen. Die Export-Datei wird in ein temporäres Verzeichnis entpackt und dort
migriert; das Original bleibt unverändert.

Heute führt `ShadowStore.merge_from` nur die Tabelle `slots` zusammen; `plan_ahead`, `soiling_ahead` und
`imported_sessions` gehen beim Import verloren, ebenso künftig `heat_pump_samples`. Soll:
- Eine feste Liste in `shadow/store.py` legt je Tabelle fest, ob sie beim Import übernommen wird
  (Standard: ja; ausnehmen nur, was verwerfbar ist).
- Je Tabelle werden die Spalten übernommen, die beide Versionen kennen; Zeilen mit gleichem
  Primärschlüssel werden ersetzt (`INSERT OR REPLACE`).
- Eine Tabelle, die nur die Export-Datei kennt (neuere Version), wird übersprungen; eine, die nur die
  eigene Datenbank kennt, bleibt unverändert.
- Alles in einer Transaktion: Ein Fehler lässt die eigene Datenbank wie vorher.
- Rückgabe: Anzahl je Tabelle (für die Meldung im UI).
- Tests: Import älterer und neuerer Export mit mehreren Tabellen; Fehler mitten im Import ändert nichts.

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
   überschrieben. Schlägt das Backup fehl (z. B. Platte voll), wird nicht migriert und der Fehler
   gemeldet. *(Offen: in `shadow/store.py` noch nicht umgesetzt.)*
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
