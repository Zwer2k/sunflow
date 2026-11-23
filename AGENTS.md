# Sunflow – Hinweise für Agenten

Energiemanagement für Home Assistant: Python-Backend (App/Add-on), Lit-Frontend, schlanke
HA-Integration `sunflow_ems`, Geräteprofile. Aufbau siehe `README.md` („Repository layout“).

## Vor der Arbeit lesen

- **Wärmepumpe, Heizung, Vorlauf, Räume, Hausmodell:** zuerst `design/heat_pump.md` – die
  verbindliche Spezifikation, kompakt halten (keine Historie, keine Umsetzungsdetails, kein Widerspruch zum
  Code). Geräteanbindung (Manifest, Profil, ESP): `design/heat_pump_device.md`.
- **Gespeicherte Daten (SQLite, `config.json`, Migrationen):** `design/datenhaltung.md`.
- **Umsetzung der Wärmepumpen-Regelung:** in der Reihenfolge von `heat_pump.md` Abschnitt 17 (offene
  Punkte und Umsetzungsstand) – nicht stillschweigend entscheiden, sondern nachfragen.
- **Geräte und Profile:** `schema/profile.schema.json`, vorhandene Profile unter `profiles/`,
  `docs/geraete.md`.
- **Verhalten für Nutzer:** `docs/` (Deutsch) und `docs/en/` (Englisch) beschreiben, wie Sunflow sich
  verhält. Änderungen am Verhalten dort in beiden Sprachen nachziehen.

## Grundsatz: korrekte Berechnung vor zusätzlichen Regeln

Ziel ist eine nachvollziehbare, möglichst korrekte Berechnung. Regeln, die nicht dokumentiert sind
oder in vielen Abhängigkeiten zusammenwirken, führen zu verfälschten Messwerten und ungewolltem Verhalten.

- **Jede Regel steht im Design.** Filter, Schwellen, Mindestmengen und Sonderfälle in Berechnungen
  (Lernen, Regelung, Planung) gibt es nur, wenn das zugehörige Design-Dokument sie beschreibt. Was der
  Code zusätzlich tut, wird nicht stillschweigend ergänzt, sondern dem Nutzer zur Entscheidung vorgelegt
  (Arbeitsliste: `design/heat_pump_undocumented.md`).
- **Eine Bedingung an genau einer Stelle.** Ob ein Messwert gültig ist, entscheidet eine einzige,
  dokumentierte Definition (z. B. „gültiger Heizbetrieb“). Keine zweite, versteckte Prüfung derselben
  Frage an anderer Stelle (im Backend oder im UI).
- **Daten nicht verwerfen, sondern richtig gewichten.** Schwellen, die gültige Daten ganz ausschließen
  (z. B. „erst ab n Werten je Stunde“), verschieben das Ergebnis zu bequemen Fällen. Gültige Werte gehen
  mit ihrem tatsächlichen Gewicht ein.
- **Regeln entfernen statt ergänzen.** Bei jeder Änderung prüfen, ob eine bestehende Regel überflüssig
  ist, sich mit einer anderen überschneidet oder das Ergebnis verfälscht. Solche Regeln werden ausgebaut,
  nicht durch weitere Regeln ausgeglichen.
- **Belegen statt annehmen.** Wirkung einer Regel vor dem Entscheiden mit echten Daten beziffern
  (`heat_pump_samples` über die Sunflow-API, HA-History per MCP) und das Ergebnis mit vorlegen.

## Entwicklung

- Backend: `backend/`, Python ≥ 3.14 laut README, Abhängigkeiten in `backend/pyproject.toml`
  (Extra `dev`: pytest, pytest-asyncio, ruff). Start und Umgebung: README, Abschnitt „Development“.
- Tests: liegen in `tests/` (pytest, `asyncio_mode = auto`, aus `backend/` gestartet:
  `python -m pytest`). Tests laufen ohne Wartezeiten (`sleep`); Zeit wird injiziert.
- Code, Kommentare und Logtexte auf Englisch; Nutzer-Doku und App-Changelog auf Deutsch.
- Commits: englische Sätze im Imperativ wie „Support EDL21 … (0.79.13)“; Versionsnummer in Klammern,
  wenn die Edge-App hochgezählt wird (`sunflow_app_edge/config.yaml`, mit jeder Änderung auf main).
  Nutzerrelevante Änderungen in `sunflow_app/CHANGELOG.md` (Deutsch).

## Regeln

- Commits macht der Nutzer selbst; der Agent committet nur auf ausdrückliche Aufforderung.
- Design-Dokumente: keine Messwerte, Daten oder Uhrzeiten vom Nutzersystem als Beispiele; die frühere
  Integration Adaptive Flow Control nicht erwähnen (wird entfernt).
- Keine Geräte ohne Freigabe steuern; Schreibvorgänge in Wärmepumpen-EEPROM sind budgetiert
  (`EepromGuard`, siehe Spezifikation).
