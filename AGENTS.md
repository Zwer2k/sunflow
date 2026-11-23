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
