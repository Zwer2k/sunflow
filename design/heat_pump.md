# Entwurf: Vorlaufregelung für Wärmepumpen in Sunflow

Stand: 22.11.2026 · Status: Spezifikation; ESP-Teil (Manifest, `tv_max`, Zehntel für Max/Min T-Vorlauf;
Abschnitt 3, 12) umgesetzt; Sunflow: Altlösung entfernt (Abschnitt 16, 2a), Datenhaltung (2b), Manifest,
Kompatibilität und Profil (2c, Teil 1), Zustände und Datensammlung (2c, Teil 2a), climate-Entity und
Schattenbetrieb mit Korrektur ohne Kennlinie (2c, Teil 2b), Lüft-Erkennung (3, Teil 1), Kennlinie,
Vorsteuerung und Hausmodell aus `heat_pump_samples` (3, Teil 2), Grenzen und Schutzschicht im Schatten (3, Teil 3),
Schreiben (3, Teil 4), Spielraum (4, Teil 4.1) umgesetzt, Rest offen

Sunflow regelt den Vorlauf-Sollwert einer Luft-Wasser-Wärmepumpe so, dass das Haus den eingestellten
Raum-Soll hält – selbstlernend, ohne die Wärmepumpe in Fehlbetrieb zu treiben – und nutzt einen
einstellbaren Spielraum zum Vorheizen bei PV-Überschuss oder günstigem Strom. Erstes Gerät: Rotex/Daikin
HPSU über die ESPHome-Komponenten `daikin_rotex_can` (CAN) und `daikin_rotex_uart` (UART). Das Konzept ist
herstellerneutral; andere Wärmepumpen (auch über HA-Integrationen) bekommen ein Profil.

Ersetzt die bisherige Wärmepumpen-Steuerung (Komfortband, Referenzraum, SG-Ready-Sperre/Verstärkung);
sie wird vollständig entfernt (Abschnitt 16, Schritt 2a). Übernommen werden nur die konfigurierten
Raum-Temperatursensoren (6.1) und das Hausmodell (9.3). SG-Ready entfällt; Heizstäbe sind nicht Teil dieses Entwurfs.

---

## 1. Begriffe

| Begriff | Bedeutung |
|---|---|
| Tv | Vorlauftemperatur (Ist), HPSU-Schlüssel `tv` |
| Tr | Rücklauftemperatur, `tr` |
| Vorlauf-Soll (wirksam) | Soll, mit dem die WP tatsächlich regelt, `target_supply_temperature` |
| Vorlauf-Ziel (`V_Ziel`) | Vorlauf, den Sunflow für das Haus will (Kennlinie + Korrektur), noch ohne Schaltregeln; nicht der gemessene Tv |
| Stellwert | was Sunflow schreibt: Vorlauf-Ziel nach Schaltregeln, Grenzen und Rundung; HPSU „Fest“: `max_target_flow_temp` (Max T-Vorlauf) |
| Raum-Ist | gewichtetes Mittel der Raumsensoren ohne gerade gestörte Räume |
| Raum-Soll | vom Nutzer eingestellt (climate-Entity „Haus“), wird nicht gelernt |
| Planziel | Raumtemperatur, die die Planung je Viertelstunde vorgibt (Soll + Spielraum) |
| Kennlinie | gelernte Wärmeabgabe der Heizflächen: Wärme ↔ (Wassertemperatur − Raum-Ist) |
| Korrektur | langsamer PI-Anteil, der Modellfehler ausgleicht |
| Strategie | wie ein Vorlauf-Ziel in Schreibvorgänge umgesetzt wird (S1 „Fest“, S2 „Witterung“) |
| Manifest | vom ESP gelieferte Beschreibung seiner Entities mit stabilen Schlüsseln |

---

## 2. Architektur

```
 Raum-Soll (climate) ─┐
 Spielraum +x/−y ─────┤
 Preise, PV-Prognose ─┤   ┌──────────────────────────┐
 Hausmodell ──────────┴──▶│ 1. Planung (MILP, 15 min) │── Planziel / Wärme je Slot
                          └──────────────────────────┘
                                       │
                          ┌──────────────────────────┐
 Kennlinie, Raum-Ist ────▶│ 2. Vorlauf-Regler         │── Vorlauf-Ziel (sofort sichtbar)
                          │   Vorsteuerung + Korrektur│
                          └──────────────────────────┘
                                       │
                          ┌──────────────────────────┐
 Zustand, Tv, Budget ────▶│ 3. Schutzschicht/Aktor    │── Schreibvorgang (Strategie S1/S2)
                          └──────────────────────────┘
                                       │
                          Gerät (Profil + Manifest) → HPSU
```

- Schicht 1: Das MILP (`ThermalLoad` in `optimizer.py`) existiert und wird in Schritt 4 neu angebunden
  (Raum-Soll + Spielraum); die bisherige Anbindung `planning/house_plan.py` wurde in 2a entfernt.
- Schicht 2 und 3 sind neu. Schicht 3 ist herstellerneutral und wird über das Profil parametriert.
- Berechnen ist billig und passiert bei jedem Anlass sofort; **Schreiben** ist teuer (EEPROM) und wird
  getrennt davon gesteuert (Abschnitt 8.5).

---

## 3. Geräteanbindung

### 3.1 Problem

ESPHome-`unique_id`s enthalten den Anzeigenamen aus der Nutzer-YAML (`<MAC>/0/number/Vorlauf Soll
Geregelt`). Namen sind frei wählbar und mehrsprachig (de/en/fr/it), Auswahlwerte übersetzt („Heizen“/
„Heating“), einzelne Entities per `type:` als number/select/switch anlegbar. Die stabilen Schlüssel der
Komponente (`tv`, `supply_setpoint_regulated`, …) erreichen HA nicht. Deshalb: **Manifest ist Pflicht.**
Firmware ohne Manifest wird nicht unterstützt (kein Rückfall über Namenslisten).

### 3.2 Manifest-Abruf (umgesetzt)

Die Komponente registriert selbst eine Native-API-Aktion mit Antwort und einem Ganzzahl-Argument
`page`. **Die Nutzer-YAML bleibt unverändert** – kein `api: actions:`, kein Package. In HA erscheint
sie als Dienst `esphome.<node>_rotex_can_manifest` bzw. `esphome.<node>_rotex_uart_manifest` (getrennte
Namen, damit beide Komponenten auf einem ESP laufen). Sunflow ruft ihn per WebSocket
`call_service(..., return_response=True)` auf (`ha/client.py` kann das).

Aufruf in HA (Entwicklerwerkzeuge → Aktionen, „Antwort zurückgeben“):

```yaml
action: esphome.hpsu_can_rotex_can_manifest
data:
  page: 0
```

Umsetzung (je Komponente `manifest.py` + `manifest_action.h`):
- Python baut das Manifest beim Kompilieren vollständig (statisches JSON im Flash), teilt es in Seiten
  zu höchstens 16 KiB und übergibt sie mit `set_manifest_action_name()` / `add_manifest_page()`.
  Größe und Seitenzahl werden beim Build geloggt. Damit bleibt jede Antwort weit unter den Grenzen,
  egal wie die Komponente wächst.
- Jede Seite wiederholt die Kopfdaten und enthält `page` und `pages`. Sunflow liest Seite 0 und bei
  `pages > 1` die übrigen. Ungültige Seite → Antwort mit Fehler „page out of range“.
- Nur aktiv, wenn die YAML `api:` hat und ESPHome ≥ 2026.1.0 ist (ab da passt die C++-API: Aktion mit
  Aufruf-ID, `StringRef`-Fehlertext); sonst Log-Hinweis, Komponente funktioniert normal. Die
  CAN-Komponente selbst braucht auf `main` ohnehin ≥ 2026.2 (`make_entity_preference`).
- Das Argument `page` wird direkt aus der Anfrage gelesen (`legacy_int`/`int_`), nicht über
  `api::get_execute_arg_value()`: ESPHome 2026.1 baut `user_services.cpp` nur, wenn die YAML selbst
  Aktionen hat – sonst Linkerfehler.
- Python setzt die Defines `USE_API_USER_DEFINED_ACTIONS`, `USE_API_CUSTOM_SERVICES`,
  `USE_API_USER_DEFINED_ACTION_RESPONSES(_JSON)` und lädt `json`.
- C++ `ManifestAction : api::UserServiceDescriptor` meldet sich mit `SUPPORTS_RESPONSE_ONLY` und dem
  Argument `page` (int) an und antwortet über `send_action_response(call_id, …, json)`. Registrierung
  in `setup()` per `register_user_service()`: läuft nach dem generierten `initialize_user_services()`,
  mit dem ESPHome eventuelle eigene `api: actions:` des Nutzers setzt (ersetzt die Liste) – beide
  bestehen nebeneinander.
- `CustomAPIDevice::register_service()` kann keine Antworten liefern – deshalb eigene Klasse.
- Kein Webserver nötig, gleicher verschlüsselter Kanal wie alle Daten.

Geprüft: Build mit ESPHome 2026.11.0 – CAN, UART und beide auf einem ESP mit einer zusätzlichen
eigenen YAML-Aktion, jeweils ohne Warnungen der Komponenten; UART zusätzlich mit 2026.1.0.
Laufzeit (2026.11.0) auf der ESPHome-Host-Plattform mit aioesphomeapi (wie HA), mit und ohne
Verschlüsselung:
Aktion mit Argument `page` gelistet, 32 KiB Testmanifest in 3 Seiten vollständig, ungültige Seiten mit
Fehler, YAML-Aktion funktioniert weiter; ohne `api:` deaktiviert und baut. Realistische Größe:
CAN-Beispiel 113 Entities ≈ 18 KiB in 2 Seiten, UART 28 Entities ≈ 3 KiB in 1 Seite.
Auf echter Hardware (vor Seiten-Umbau) für CAN und UART über HA bestätigt.

- Dienstname: Sunflow listet die Dienste der Domain `esphome` (`get_services`). Der Knotenname steckt im
  Dienstnamen (`<knoten>_rotex_can_manifest`, `-` wird `_`). Zuordnung zum HA-Gerät **nur über die
  Entities** (`heat_pump/manifest.py`, `assign_device`; HA liefert den Knotennamen eines
  ESPHome-Config-Entry über `config_entries/get` nicht):
  1. Das Gerät ist das ESPHome-Gerät, bei dem alle Entities des Manifests vorkommen (`/<domain>/<name>`
     am Ende der `unique_id`, Rückfall `original_name`; abgeschaltete Entities zählen mit).
  2. Haben mehrere Geräte alle (gleiche Firmware, gleiche Entity-Namen), entscheidet hilfsweise der
     Knotenname aus dem Dienstnamen, verglichen mit dem Gerätenamen der Registrierung als Slug (`name`,
     sonst `name_by_user`; z. B. `HPSU.CAN` → `hpsu_can`). Zugeordnet wird nur bei genau einem Treffer.
  Mehrdeutig oder kein Treffer: keine Zuordnung, Hinweis in Zusammenfassung und Log; der Nutzer wählt
  dann im Assistenten (Schritt 5). Reicht das nicht, MAC ins Manifest (ESP-Änderung, vorher abstimmen).
- Abruf und Neuladen: siehe 3.6.

**Vorfilter:** Ob ein Knoten einen dieser Dienste anbietet (HA-Dienstliste, ohne Kosten).
`esphome: project:` wird nicht ausgewertet: frei änderbar, veraltet beim Aktualisieren über
`external_components`, nur einmal je Knoten, teils vom Nutzer anderweitig belegt.

**Vertrauenswürdig ist nur, was der Komponenten-Code erzeugt:** `id` (Konstante, erkennt die
Komponente), `git` (beim Bauen ermittelt, nur Diagnose). Eine Versionsnummer gibt es bewusst nicht
(siehe 3.6).

**Risiko ESPHome-Updates:** Die Aktion nutzt interne, aber öffentliche Klassen der ESPHome-API, die
sich zwischen Versionen ändern (bisher: Name als `StringRef` → `ProgmemStringRef`, Fehlertext
`std::string` → `StringRef`). Die vorhandenen Workflows kompilieren die Beispiel-YAMLs mit der
jeweils aktuellen stabilen ESPHome-Version (CAN `build.yml`, UART `compile-test.yaml`) und fangen so
Brüche nach einem Release ab. Empfehlung: zusätzlich ESPHome beta/dev bauen, um Brüche vor dem Release
zu sehen; bei Bruch `manifest_action.h` anpassen. Ein Bruch betrifft nur die Firmware-Erstellung, nicht
einen laufenden ESP.

### 3.3 Manifest-Schema (umgesetzt)

```json
{
  "id": "rotex_hpsu_can",        // "rotex_hpsu_can" | "rotex_hpsu_uart"; nie geändert
  "name": "Rotex/Daikin HPSU – ESPHome CAN",   // nur Anzeige
  "git": "a1b2c3d",              // beim Bauen ermittelt, null ohne Git; nur Diagnose
  "language": "de",
  "protocol": "auto",            // nur UART: "I" | "S" | "auto"
  "page": 0,                     // diese Seite (0-basiert)
  "pages": 2,                    // Anzahl Seiten; Entities über alle Seiten zusammenfassen
  "entities": [
    {"key": "tv", "domain": "sensor", "name": "TV", "unit": "°C"},
    {"key": "max_target_flow_temp", "domain": "number", "name": "Max T-Vorlauf",
     "unit": "°C", "min": 20, "max": 90, "step": 0.1},
    {"key": "hk_function", "domain": "select", "name": "HK Funktion",
     "values": [[0, "Witterungsgeführt", "weather_dependent"], [1, "Fest", "fixed"]]},
    {"key": "mode_of_operating", "domain": "text_sensor", "name": "Betriebsart",
     "values": [[0, "Bereitschaft", "standby"], [1, "Heizen", "heating"], [2, "Kühlen", "cooling"],
                [3, "Abtauen", "defrosting"], [4, "Warmwasserbereitung", "hot_water_production"]]}
  ]
}
```

Regeln:
- `key` ist der Schlüssel aus `sensor_configuration` der Komponente. Er sollte stabil bleiben; ändert er
  sich doch, erkennt Sunflow das beim nächsten Laden (3.6).
- `name`/`domain` sind die tatsächlichen Werte der Firmware (Domain wie angelegt, z. B. `select` statt
  `number`, wenn der Nutzer `type: select` wählt); Sunflow findet damit die HA-Entity über das Ende der
  `unique_id` (`/<domain>/<name>`), Rückfall `original_name`.
- `values`: `[Wert, Text]` oder `[Wert, Text, Übersetzungsschlüssel]`. Wert = Rohwert/Bedeutung der
  Komponente (sprachneutral), Text = was die Entity zeigt. Sunflow arbeitet mit Schlüssel bzw. Wert.
  UART zusätzlich `values_s` für das S-Protokoll.
- Weitere Felder je Entity: `unit`, `min`/`max`/`step` (Number), `internal`, `device` (ESPHome-Untergerät).
- Laufzeit-Zustände (z. B. ob `supply_setpoint_regulated` aktiv ist) stehen nicht im Manifest; Sunflow
  liest die Entity.
- Hinweis: „Abtau-Optimierung“ (`optimized_defrosting`) ist eine Auswahl der CAN-Komponente, kein Wert von
  `mode_of_operating`. Ist sie an, stellt die Komponente beim Abtauen den Betriebsmodus kurz auf Sommer und
  danach zurück (weniger Wasser aus dem Speicher in den Fußboden). Sunflow sieht dann Programm `summer`
  und friert ein (Abschnitt 4); sonst ist sie für Sunflow ohne Bedeutung.

### 3.4 Topologien und Profile

| Variante | Profile | Sunflow kann |
|---|---|---|
| nur CAN | `esphome/rotex_hpsu_can` | messen (Tv, Tr, TvBH, Wärme, Energie, Zustand) und **regeln** |
| nur UART | `esphome/rotex_hpsu_uart` | nur messen (Kältekreis, Kompressor, Inverterstrom). UART-Komponente hat keine schreibbaren Entities |
| CAN + UART, zwei ESPs | CAN Hauptgerät, UART **Begleiter** | wie CAN, zusätzlich Kompressor-/Stromdaten |
| CAN + UART, ein ESP | wie oben, Untergeräte desselben Knotens | wie oben |

**Erkennung** nur über das Manifest: Ein ESPHome-Knoten ist ein Rotex-Gerät, wenn er die Aktion
`rotex_can_manifest` bzw. `rotex_uart_manifest` anbietet und das Manifest `id: rotex_hpsu_can` bzw.
`rotex_hpsu_uart` liefert. Andere ESPHome-Knoten bieten die Aktion nicht an und zählen
nicht. Ob CAN und UART auf einem oder zwei ESPs laufen, spielt keine Rolle. Kein Abgleich über einzelne
Entities: welche Entities angelegt sind, entscheidet der Nutzer in seiner YAML.

**Paarung** CAN ↔ UART (nur nötig, wenn beide vorhanden sind):
1. genau ein CAN- und höchstens ein UART-Manifest → automatisch (der Normalfall, auch auf einem ESP);
2. mehrere Wärmepumpen (mehrere CAN- oder UART-Manifeste) → Auswahl im Assistenten.
Messwertvergleich ist ungeeignet (CAN- und UART-Sensoren weichen bis > 1 K ab).

**Speichern der Zuordnung** (in der Gerätekonfiguration, je Rolle CAN und UART-Begleiter):
- `ha_device_id` des ESPHome-Geräts (wie bei allen Sunflow-Geräten) **und** die MAC-Adresse aus
  `connections` des Device-Registry (z. B. `34:b7:da:5e:53:40`); dazu die Komponente (`can`/`uart`).
- Zwei gleiche ESPs mit gleicher Firmware sind über ihre MAC (und damit `device_id`) unterscheidbar.
  Der Assistent zeigt bei mehreren Kandidaten Name, Bereich und MAC.
- CAN und UART auf einem ESP: gleiche `device_id`/MAC, unterschieden über das Manifest (`id`).
- Findet Sunflow die `device_id` nicht mehr (z. B. ESPHome-Integration gelöscht und neu angelegt),
  sucht es das Gerät mit derselben MAC und bindet es neu (Hinweis im Journal).
- ESP getauscht (neue MAC): neu zuordnen im Assistenten. Gelernte Daten bleiben, sie hängen an Sunflows
  eigener Geräte-ID, nicht an der des ESP.

Ein Begleiter liefert nur optionale Rollen; fehlt er, bleiben sie leer.

### 3.5 Erweiterungen in Sunflow (allgemein)

- Profil-`match` um `manifest: {id}` erweitern; Rollen über Manifest-Schlüssel.
- Auflösung Schlüssel → Entity zur Laufzeit (wie heute `bind_roles`), Manifest im Snapshot gecacht.
- `profile_version` wird ausgewertet: abweichend → Hinweis.
- Fehlende Pflichtrolle eines übernommenen Geräts → Fehler in der Einrichtungs-Zusammenfassung (heute
  still `None`).
- Begleitgerät im Profil-Schema (`companion: {profile, roles}`).

### 3.6 Kompatibilität und Neuladen

Keine Versionsnummer und keine Prüfsumme: eine Nummer kann man vergessen hochzuzählen, eine Prüfsumme
schlägt bei jeder harmlosen Änderung an (z. B. Nutzer legt eine Entity an) und müsste in drei Codebasen
gleich berechnet werden. Stattdessen prüft **Sunflow bei jedem Laden genau das, was es benutzt** – die
Prüfung existiert nur einmal, in Sunflow.

Prüfung je Rolle des Profils (inklusive Begleiter):
- Schlüssel vorhanden;
- Domain passt (z. B. `number` zum Schreiben, `select`/`text_sensor` für Zustände);
- Einheit passt (z. B. `°C`, `%`, `kW`);
- Auswahlwerte: alle Übersetzungsschlüssel bzw. Werte, auf die Sunflow reagiert, sind vorhanden
  (z. B. `heating`, `hot_water_production`, `defrosting`, `fixed`, `weather_dependent`, `on`/`off`);
- Rolle, auf die Sunflow schreibt (`flow_control.strategies.*.write.role`, HPSU `max_flow_setpoint`):
  Domain immer `number`, Einheit wie im Profil, `min`/`max` decken die Grenzen ab
  (`flow_control.limits`), Schrittweite `step` höchstens `MAX_SETPOINT_STEP_K` = Totzone `DEADBAND_K`
  (0,5 K, `heat_pump/defaults.py`; 0,5 selbst ist erlaubt). Gröber (z. B. 1,0 bei alter Firmware ohne
  Zehntel) → inkompatibel: „Schrittweite von Max T-Vorlauf zu grob (1 K, höchstens 0,5 K) – Firmware der
  Komponente aktualisieren“. Die Grenze ist eine Eigenschaft der Firmware und hängt nicht an einer im
  Profil oder später vom Nutzer geänderten Totzone;
- Manifest strukturell lesbar (`id`, `entities`, `page`/`pages`).

Ergebnis:
- Pflichtrolle fehlt oder passt nicht → Gerät **inkompatibel**: nicht mehr schreiben (8.6), Meldung in Zusammenfassung und Journal mit dem
  konkreten Grund („HPSU CAN: Schlüssel `max_target_flow_temp` fehlt“). Ob umbenannt oder vom Nutzer
  nicht angelegt, ist für die Meldung gleich.
- Optionale Rolle fehlt → nur die Funktion aus, Hinweis.
- Felder, die Sunflow nicht nutzt, werden nicht geprüft und können sich frei ändern.

Neuladen des Manifests:
1. **Neu geflasht:** Ereignis `device_registry_updated` mit geänderter `sw_version` des ESP. Das ist die
   ESPHome-Version der Firmware plus Build-Zeit (z. B. `2026.11.0 (2026-11-18 22:50:38 +0100)`), keine
   Version der Komponente; die Build-Zeit ändert sich mit jedem Flashen.
2. **Neustart / Verbindungsabbruch:** Entities des Knotens `unavailable` → wieder verfügbar. Ab
   `unavailable` schreibt Sunflow für das Gerät nichts, bis das Manifest neu geladen und geprüft ist.
3. **Rückfall:** stündlich.

Umsetzung (2c, Teil 1): `heat_pump/compat.py` leitet je Rolle (`role_requirements`) Domain (Rollenfeld
`domain`, bei der beschriebenen Rolle immer `number`), Einheit, nötige Auswahlwerte (aus `states` und
`flow_control`) und für die beschriebene Rolle Bereich (`flow_control.limits`) und höchste Schrittweite
(`MAX_SETPOINT_STEP_K`) ab und prüft dagegen. `heat_pump/link.py` (`ManifestManager`) lädt neu
(Ereignis `device_registry_updated`, Rollen-Entity wieder verfügbar, stündlich), speichert das
Manifest in `heat_pump_manifests.json` (Hash ohne `git`) und meldet jede Änderung von Manifest,
Kompatibilität, Profilversion und Neu-Bindung über die MAC einmal in Journal und Log. Status je Gerät:
`GET /api/v1/heat_pump/status`.

Geändert hat sich etwas, wenn das neue Manifest vom gespeicherten abweicht (Vergleich in Sunflow). Ein
Signal vom ESP beim Start wird nicht genutzt: ESPHome-Knoten dürfen in HA nur Ereignisse auslösen, wenn
der Nutzer das in der ESPHome-Integration freigibt (Standard: aus).

---

## 4. Zustände

Zwei Achsen, über sprachneutrale Schlüssel des Manifests; das Profil bildet herstellerspezifische
Zustände ab. Unbekannte Werte → „unbekannt“ → keine Eingriffe, Journal-Eintrag.

**Programm** (vom Nutzer gewählter Betriebsmodus, HPSU `operating_mode`). Alle Werte der HPSU
(Schlüssel aus dem Manifest, Rohwert in Klammern):

| HPSU | Schlüssel | kanonisch | Sunflow |
|---|---|---|---|
| Bereitschaft (1) | `standby` | `standby` | Heizen aus (Frostschutz) → nicht schreiben, nicht lernen, Korrektur einfrieren |
| Heizen (3) | `heating` | `heating` | Regelung aktiv – **empfohlener Modus**, wenn Sunflow regelt |
| Absenken (4) | `lowering` | `heating` | Regelung aktiv; HPSU nutzt T-Vorlauf Nacht (siehe unten) |
| Sommer (5) | `summer` | `summer` | keine Raumheizung, Warmwasser läuft weiter → nicht schreiben, nicht lernen, Korrektur einfrieren; Aktivität beobachten |
| Automatik 1 (11) | `automatic_1` | `heating` | Regelung aktiv; in Absenk-Zeiten des HPSU-Zeitprogramms T-Vorlauf Nacht (siehe unten) |
| Automatik 2 (12) | `automatic_2` | `heating` | wie Automatik 1 |
| Kühlen (17) | `cooling` | `cooling` | umgekehrter Prozess → nicht unterstützt, Hände weg |
| – | unbekannt | `unknown` | Hände weg, Journal |

Warum der Modus nötig ist: Die Aktivität „Bereitschaft“ (Kompressor aus) gibt es sowohl bei erlaubtem
Heizen (Pause → Startregel bei Bedarf) als auch bei Bereitschaft/Sommer (Heizen aus → nichts tun). Ohne
Modus würde Sunflow im Sommer gegen den auskühlenden Raum anregeln (wirkungslose EEPROM-Schreibvorgänge,
Korrektur läuft hoch).

**Absenken / Automatik:** In Absenk-Zeiten gilt bei „Fest“ T-Vorlauf Nacht (`flow_temperature_night`)
statt T-Vorlauf Tag (`flow_temperature_day`). Liegt er unter Sunflows Stellwert, begrenzt die HPSU selbst
und Sunflows Wert wirkt nicht. Erkennung über den Vergleich Stellwert ↔ wirksamer Vorlauf-Soll (dauerhaft
darunter → Hinweis „HPSU begrenzt den Vorlauf selbst (Absenk-Zeit, T-Vorlauf Nacht)“); in dieser Zeit
nicht lernen und die Korrektur einfrieren. Empfehlung an den Nutzer: Modus „Heizen“, Nachtabsenkung über
den Raum-Soll von Sunflow.

Sommer ≠ Kühlen. Genaue Unterschiede Sommer/Bereitschaft: offener Punkt 2 (Abschnitt 15).

**Aktivität** (was die WP gerade tut, HPSU `mode_of_operating`), nur im Programm `heating` relevant:

| kanonisch | Sunflow |
|---|---|
| `heating` | regeln, lernen |
| `hot_water` | **einfrieren**: nicht lernen, Korrektur halten; geschrieben wird nur die Vorbereitung auf den Start nach Warmwasser (8.4) |
| `defrost` | einfrieren + Nachlauf (Parameter, Start 10 min) |
| `standby` / idle | Kompressor aus: nur Startregel bei Heizbedarf |
| `unknown` | einfrieren |

Die Aktivität ist der echte Zustand des Kompressors, nicht der Nutzerwunsch (der steht im Programm).

**Bypass-Ventil (BPV, `bypass_valve`, %)** entscheidet, wohin das Wasser fließt: 0 % = in den
Heizkreis (Fußboden), 100 % = kurzer Weg (Warmwasser, Abtauen und die Übergänge davor/danach). Solange
das Wasser den kurzen Weg nimmt, sind Tv, TvBH und Tr für den Heizkreis nicht aussagekräftig.
- Regeln (Nachführen, Korrektur) und Lernen nur bei Aktivität `heating` **und** BPV = 0 % seit
  mindestens 10 min (Beruhigungszeit, Parameter). Keine Toleranz nötig: der Sensor meldet im Heizkreis
  exakt 0. Typischer Ablauf nach Warmwasser: BPV 100 % → Zwischenwerte in der Bereitschaft → 0 %
  beim Wechsel auf Heizen; der Kompressor startet einige Minuten später, Tv schwankt danach noch
  eine Weile (Größenordnung 20 min).
- Aktivität `heating` mit BPV > 0 % oder innerhalb der Beruhigungszeit gilt als Übergang → einfrieren.
- Die Startregel (8.4) gilt davon unabhängig: Sie hängt am Kompressor, nicht am Ventil.

Zusätzliche Eingänge: Kompressor läuft (`status_kompressor`), Störung (`error_code`). Störung = Fehlercode
beginnt mit `E` (Übersetzungsschlüssel `err_E…`; ein Text, den das Manifest nicht kennt, zählt, wenn er
mit `E` und einer Ziffer beginnt). Warnungen (`W…`) und „Kein Fehler“ sind keine Störung. Bei einer
Störung: nicht schreiben, nicht lernen, Korrektur einfrieren, Journal.

**Gültiger Heizbetrieb** – Voraussetzung für Regeln (Nachführen, Korrektur, 8.2) und Lernen (9.1); sonst
einfrieren. Alle Bedingungen gleichzeitig:
- Programm `heating` (Heizen, Absenken, Automatik 1/2);
- Aktivität `heating` und BPV = 0 % seit mindestens der Beruhigungszeit (10 min);
- kein Nachlauf: nach Warmwasser und nach Abtauen je `frozen_after` (10 min) ab dem Ende. Nachlauf und
  BPV-Beruhigung laufen gleichzeitig ab ihrem jeweiligen Ende; es gilt das spätere Ende, nichts wird
  addiert. Nach einem Neustart von Sunflow ist unbekannt, was vor dem letzten Wechsel der Aktivität lief:
  liegt er kürzer als der längste Nachlauf zurück, gilt ein Nachlauf „unbekannt“;
- keine Raumthermostat-Sperre aktiv, keine Störung;
- die HPSU begrenzt den Vorlauf nicht selbst (nur Strategie `fixed`): begrenzt = wirksamer Soll liegt
  mehr als die Totzone (0,5 K) unter Max T-Vorlauf, länger als 5 min (`SELF_LIMIT_AFTER_S`); nach einem
  eigenen Schreibvorgang zählt die Frist ab dem Schreiben (Übernahme 30–60 s). Ende ohne Wartezeit,
  sobald der wirksame Soll wieder innerhalb der Totzone liegt. Fehlt einer der beiden Werte: nicht
  gültig. Bei anderer Strategie greift das Kriterium nicht (S2 später);
- Gerät erreichbar und kompatibel (3.6).
Die Startregel (8.4) und Schutzschritte gelten unabhängig davon.

Journal: nur bei unbekanntem Zustandswert (einmal je Wert), Beginn und Ende einer Störung, Sperre an und
aus, Beginn von „HPSU begrenzt den Vorlauf selbst“. Wechsel gültig ↔ nicht gültig (z. B. jedes Abtauen)
stehen nur in `heat_pump_samples` (`flags`, `events`) und in `GET /api/v1/heat_pump/status`.

Umsetzung (2c, Teil 2a): `heat_pump/states.py` (`read` bildet Anzeigetexte über die `values` des
Manifests auf Schlüssel und über `states` des Profils auf kanonische Zustände ab; `HeatingTracker`
bewertet den gültigen Heizbetrieb mit den Gründen fürs Einfrieren, z. B. `bypass_settling`,
`after_hot_water`, `blocked`, `self_limited`). Die BPV-Beruhigung zählt ab `last_changed` der Entity
in HA (übersteht einen Neustart von Sunflow), sonst ab der eigenen ersten Beobachtung.

---

## 5. Strategien

Sunflow liest `hk_function` und **ändert ihn nie**.

| `hk_function` | Strategie | Status |
|---|---|---|
| `fixed` (Fest) | **S1**: Stellwert = Max T-Vorlauf; HPSU übernimmt ihn nach 30–60 s als Vorlauf-Soll | wird umgesetzt |
| `weather_dependent` | **S2**: HPSU-Heizkurve; Stellgrößen Heizkurve, Raumsoll-Verschiebung, Max T-Vorlauf als Deckel | vorgemerkt; bis dahin: lernen, nicht schreiben |
| unbekannt | – | nicht schreiben |

S2 wird erst nach einer Analyse-Phase entworfen: einige Tage „Witterungsgeführt“, Vergleich mit „Fest“
(Kompressorstarts/h, Abstand Tv − Soll vor Abschaltungen, Schwankung des Soll). Hypothese: Die HPSU
vermeidet die 3-K-Abschaltung selbst und kommt mit ≤ 24 Schreibvorgängen/Tag aus.

Profil allgemein: `setpoint_kind: direct` (echter Sollwert; HPSU „Fest“) oder `curve`
(Kurvenparameter; HPSU „Witterung“). Andere Wärmepumpen bieten eine oder beide.

**Umschalten:** Sunflow erkennt den Wechsel von `hk_function`, aktiviert die passende Strategie
**stoßfrei** (erster Wert = aktueller wirksamer Vorlauf-Soll) und lädt deren eigenen Zustand.

---

## 6. Räume und Raum-Soll

### 6.1 Räume

- Liste HA-Temperatursensoren mit Gewicht (z. B. Fläche). **Kein Referenzraum.**
- Raum-Ist = gewichtetes Mittel der verfügbaren, nicht gestörten Räume.
- Fester Versatz je Raum zum Mittel wird gelernt (Etagen, Abgleich) → Abweichung davon = Störung.
- Unruhige Räume (`restless_rooms`) werden für die Korrektur geringer gewichtet.
- Optional später: Untergrenze je Raum (kältester Raum darf sie nicht unterschreiten).
- Sensoren melden teils nur alle ~15 min; die Logik muss damit auskommen.
- Ein Raum zählt nicht, wenn sein Sensor `unavailable` ist oder seit mehr als 2 h (`ROOM_STALE_AFTER`)
  nichts gemeldet hat. Maßgeblich ist `last_reported` (auch bei unverändertem Wert), das Sunflow vor
  jedem Schritt der Korrektur – bei geändertem Raumwert, sonst spätestens alle 30 min – je Raumsensor
  einzeln über REST (`GET /api/states/<entity_id>`) liest; die über `state_changed` geführten Zustände
  kennen eine erneute Meldung nicht, und `state_reported` lässt sich nicht nach Entities filtern. Fehlt
  `last_reported` oder schlägt die Abfrage fehl, gibt es kein Veraltet-Kriterium (nie auf
  `last_updated` zurückfallen, sonst fiele ein stabiler Raum heraus). Umsetzung `heat_pump/rooms.py`
  (`reported_times`, `room_temperature`), gedacht auch für Lüft-Erkennung und Hausmodell.
- **Meldung des Geräts statt der Entity** (0.80.19): Manche Integrationen schreiben einen unveränderten Wert
  nicht neu (MQTT, z. B. Zigbee2MQTT): `last_reported` einer stabilen Temperatur bleibt stehen, obwohl das
  Gerät weiter meldet (z. B. die Luftfeuchte). Maßgeblich ist deshalb das jüngste `last_reported` des
  Raumsensors und der übrigen aktiven `sensor`-Entities desselben Geräts (Entity-Registry, `device_sensors`,
  mit jeder Abfrage neu gelesen); ohne Gerät oder ohne lesbare Registry nur der Raumsensor selbst. Ein
  Raum gilt damit erst als veraltet, wenn das ganze Gerät seit 2 h schweigt.

### 6.2 Schnelle Änderung (Lüften, Kochen …)

Typischer Verlauf beim Stoßlüften: schneller Abfall (oft 1–4 K in 10–20 min), schnelle Teilerholung,
sobald das Fenster zu ist (Luft wärmt sich an Wänden/Möbeln, etwa eine Stunde), Rest langsam über
Stunden. Kochen oder ein Backofen: schneller Anstieg, danach langsames Abkühlen. In beiden Fällen ändert
sich vor allem die Luft; der langsame Rest ist echte Wärme, die Wänden, Möbeln und Boden fehlt (bzw. die sie
gewonnen haben) – er soll in die Korrektur eingehen, der schnelle Teil nicht (die Fußbodenheizung kann
ihm nicht folgen; der P-Anteil würde schreiben und kurz danach zurück).

- Ereignis: Raum ändert sich um ≥ `start_k` innerhalb ≤ `start_min`, nach unten oder oben (≥ 2 Meldungen).
  Schwellen in Kelvin, nicht in Sensorschritten: Sensoren melden in Schritten von 0,1, 0,3 oder 0,5 K; ein
  einzelner Schritt und langsames Abkühlen (z. B. nach dem Kochen) bleiben darunter.
- Folge: Raum zählt für die Korrektur nicht, bis der schnelle Teil vorbei ist (über `settle_min` höchstens
  `settle_k` Bewegung, höchstens `max_h`). Danach zählt er mit seinem Rückstand bzw. Überschuss. Für das
  Lernen der Kennlinie und des Hausmodells werden die Intervalle ausgelassen.
- Ein Abfall nach einem erkannten Anstieg (Abkühlen nach dem Kochen) gehört zum selben Ereignis, bis es
  vorbei ist.
- **Keine Uhrzeit-Features.** Wiederkehrende Tagesmuster deckt die Planung ab; ein gelernter Tagesgang
  innerer Gewinne nur, wenn die Auswertung ihn zeigt.

Einstellung `house.fast_change` (UI bei den Räumen, additiv ohne Migration; fehlend oder ungültig → Startwert):

| Feld | Startwert | Bereich |
|---|---|---|
| `start_k` / `start_min` (Beginn ab … K in … min) | 0,8 K / 20 min | 0,2–5 K / 5–120 min |
| `settle_k` / `settle_min` (vorbei bei höchstens … K in … min) | 0,5 K / 30 min | 0,1–5 K / 10–180 min |
| `max_h` (höchstens) | 2 h | 0,5–12 h |

Die Startwerte trennen Stoßlüften und Kochen (mehr als 1 K in 10–20 min) von langsamem Abkühlen (unter
0,8 K in 20 min) und einzelnen Sensorschritten; nach den ersten Wochen prüfen (15, Punkt 11).

Umsetzung (3, Teil 1; umgestellt 0.80.21, `heat_pump/ventilation.py`, Startwerte `FAST_*` in
`heat_pump/defaults.py`):
- Je Raum die Folge der angezeigten Werte (gilt bis zur nächsten Änderung, Zeit aus `last_changed`).
  Ereignis, wenn der höchste (niedrigste) im Fenster `start_min` angezeigte Wert mindestens `start_k` über
  (unter) dem aktuellen liegt (zwei Meldungen: der Wert davor und der aktuelle); Richtung `down`/`up`.
- Ende, wenn der schnelle Teil vorbei ist: über die letzten `settle_min` (vollständig bekannt) höchstens
  `settle_k` Unterschied; spätestens `max_h` nach dem Erkennen. Danach zählen für das nächste Ereignis nur
  Werte ab dem Ende.
- Während des Ereignisses zählt der Raum nicht für Raum-Ist, und die Korrektur ist eingefroren (Grund
  `ventilation`, 8.2; Name bleibt). Ändert sich, aus welchen Räumen Raum-Ist besteht (schnelle Änderung,
  veraltet, nicht verfügbar), nimmt die Korrektur den neuen Wert als Bezug statt eines Schritts – kein Sprung.
- Laufende und in den letzten 24 h beendete Ereignisse (höchstens 20) stehen in `heat_pump_state.json`
  (`house.ventilation`, je Ereignis `start_c`, `direction`, `extreme_c`, `change_k`; für Abfälle wie bisher
  zusätzlich `low_c`/`drop_k`); nach einem Neustart endet ein laufendes Ereignis erst, wenn der Raum über
  `settle_min` bekannt ist. Kein Journal, nur Log. UI (Bereich Wärmepumpe): Räume mit schneller Änderung
  mit Beginn und Werten, schnelle Änderungen der letzten 24 h mit Vorzeichen; Status
  `GET /api/v1/heat_pump/status` (`house.ventilation`, `ventilation_recent`, `rooms_ventilated`; Namen
  bleiben).
- In `heat_pump_samples` `flags.ventilation` (10, Name bleibt, enthält jetzt auch Anstiege); `valid` bleibt
  unverändert der gültige Heizbetrieb.

### 6.3 Raum-Soll und Spielraum

- **Raum-Soll**: eingestellt, nicht gelernt. Bereitgestellt als `climate`-Entity „Haus“ in `sunflow_ems`
  (Soll, Ist = Raum-Ist, Modus heat/off) und im Sunflow-UI. Sunflow speichert den Wert, HA spiegelt ihn.
  Bereich 18–27 °C, Standard 21,0 (`ROOM_SETPOINT_*` in `heat_pump/defaults.py`, gilt für Modell,
  Entity und Migration); Schrittweite der Entity 0,5 °C, über `climate.set_temperature` auf 0,1 °C
  genau (Sunflow rundet auf 0,1). Werte außerhalb werden abgelehnt, nicht begrenzt. Wer nicht heizen
  will, nutzt den Modus `off`, keinen niedrigen Soll; den Frostschutz übernimmt die Wärmepumpe.
  Gespeichert in `config.json` unter `house.climate` (`setpoint_c`, `mode`); Migration 5 → 6:
  `setpoint_c` = `comfort_min`, wenn das eine Zahl in 18–27 ist, sonst 21,0; die alten Felder bleiben
  liegen. Schnelle Folgeänderungen aus HA fasst die Entity zusammen (2 s), gespeichert wird der letzte
  Wert (`PUT /api/v1/house/climate`). Die Entity gibt es, sobald Räume oder eine Wärmepumpe
  eingerichtet sind.
  Nachtabsenkung o. Ä. über HA-Zeitpläne/Automationen auf diese Entity. Modus `off`: Sunflow schreibt
  nichts (wie Steuerung aus, 8.6), rechnet und zeigt aber weiter an (wie Schattenbetrieb).
- **Spielraum**: „Vorheizen bis +x K“ (Start 1,0 K) und „Absenken bis −y K“ (Start 0 K). Abweichung vom
  Soll kostet in der Planung → ohne Ersparnis bleibt Sunflow auf dem Soll.
  Umsetzung (4, Teil 4.1): gespeichert mit dem Raum-Soll in `house.climate` als `preheat_k` (x) und
  `lower_k` (y), je 0–3 K (`ROOM_MARGIN_*` in `heat_pump/defaults.py`), auf 0,1 K gerundet; additiv ohne
  Migration – fehlt ein Feld oder ist es ungültig (keine Zahl, außerhalb des Bereichs), gilt der Startwert
  ohne Fehler. Ändern über `PUT /api/v1/house/climate` (Teiländerung wie Soll und Modus; Werte außerhalb
  werden dort abgelehnt) und im Sunflow-UI beim Raum-Soll; nicht über die climate-Entity (HA kennt kein
  Band zum Einstellen). Status `GET /api/v1/heat_pump/status` (`house.preheat_k`, `lower_k`, Bereich
  `margin_max_k`). Wirkung mit der Planung (Abschnitt 7, 4.2): Band und Ziel der Raumheizung.
- Attribute der climate-Entity: aktuelles Planziel, Grund („Vorheizen wegen PV“), Vorlauf-Ziel,
  geschriebener/ausstehender Stellwert.
- Entfällt: `comfort_min`, `comfort_max`, `night_min`, `night_start`, `night_end`, `reference` in
  `HouseSettings` (Migration der Konfiguration: `comfort_min` → Raum-Soll-Startwert).

---

## 7. Planung (Schicht 1)

- Bestehendes MILP (`ThermalLoad`) wird in Schritt 4 neu angebunden; `planning/house_plan.py` wurde in 2a
  entfernt. Bis dahin plant Sunflow ohne Raumheizung, die Lastprognose bleibt unverändert.
- Raumtemperatur je 15 min im Band [Soll − y, Soll + x], Kosten für
  Abweichung vom Soll (weich, klein gegenüber echter Ersparnis), Wärmepumpenleistung als Variable, COP
  vorab (Carnot, später gelernt).
- Ausgabe je Slot: Planziel-Raumtemperatur und geplante Wärme.
- Neuplanung: regulär alle 15 min und **sofort** bei Raum-Soll-Änderung (Laufzeit ~0,1 s).
- Ohne nutzbares Hausmodell (< 7 Tage, nicht plausibel): Planziel = Raum-Soll, Wärme = 0 → Regler
  arbeitet nur mit Kennlinie + Korrektur.

**Entscheidungen zu Schritt 4 (Nutzer, 22.11.):**
- Kosten der Abweichung vom Soll innerhalb des Spielraums: 0,05 € je K und Stunde, nach oben und unten
  gleich (Konstante in `heat_pump/defaults.py`, nicht einstellbar). Außerhalb des Bands wie bisher: darunter
  sehr teuer (praktisch hart), darüber teuer, aber möglich (Sonne kann das Haus darüber treiben).
- Wärmepumpe aus der Lastprognose: wie in der früheren Anbindung über das Modell – Strom, um den Soll zu
  halten = Hausmodell-Wärme / COP, je Slot höchstens 70 % der prognostizierten Hauslast; im Vergleich ohne
  Sunflow wieder addiert.
- Modus: Nur bei „Lernen + Regeln“ plant der Batterie- und Verbrauchsplan mit der Wärmepumpe. Bei „Nur
  Lernen“ bleibt er wie ohne Raumheizung (die Wärmepumpe steckt in der Lastprognose); der Vorheiz-Plan wird
  in einer eigenen zweiten Rechnung nur für Anzeige und Schatten ermittelt („würde vorheizen“). Bei Aus kein
  Vorheiz-Plan, Planziel = Soll.
- Mehrere Wärmepumpen: geplant wird ein Haus mit der ersten Wärmepumpe (wie die climate-Attribute); weitere
  laufen ohne Plan (Planziel = Soll).

**Umsetzung (4, Teil 4.2, 0.80.24):** `planning/house_plan.py` (`HouseInput`, `house_setup`, `house_plan`),
`optimize_with_house` in `planning/plan.py`, `ThermalLoad` mit `target`/`deviation_cost` (Abweichung nach oben und
unten je Slot als eigene Variablen) und `max_heat_w` (elektrische Grenze je Slot = höchste Heizleistung / COP).
Konstanten in `heat_pump/defaults.py` (`PLAN_DEVIATION_EUR_PER_KH` 0,05, `PLAN_COLD_EUR_PER_KH` 50,
`PLAN_WARM_EUR_PER_KH` 0,2 wie bisher, `PLAN_MAX_LOAD_SHARE` 0,7, `PLAN_SAMPLE_MAX_AGE_S` 10 min).
- Eingaben (`ShadowRunner.house_input`): erste Wärmepumpe (`heat_pump_device`), nutzbares Hausmodell des Runners
  (`useful`, `b` > 0), Raum-Ist (6.1), letzter Messwert der Wärmepumpe aus `FlowShadow.last_minutes` (Programm,
  gemessene Außentemperatur; höchstens 10 min alt), höchste Heizleistung aus den gelernten Werten (sonst Startwert
  des Profils, `learned.values`), Schalter `flow_active`.
- Band [Soll − `lower_k`, Soll + `preheat_k`], Ziel = Soll je Slot; Außentemperatur der Prognose je Slot, Lücken mit
  dem letzten bekannten Wert, ohne Prognose die gemessene; COP `carnot_cop`. **Ohne Sonne** (keine
  Einstrahlungsprognose; Nutzer 22.11.): nur innere Gewinne `d`, die Korrektur fängt sonnige Tage auf.
- Grundlast: Strom, um den Soll zu halten = max(a·(Soll − T_out) − d, 0)/b / COP, begrenzt auf die elektrische
  Grenze und 70 % der Lastprognose des Slots; aus der Lastprognose genommen, im Vergleich ohne Sunflow wieder addiert.
- Modus: „Lernen + Regeln“ (`flow_active` und Modus Heizen) plant mit `ThermalLoad` im Batterieplan; „Nur Lernen“ rechnet
  den Batterieplan ohne und eine zweite Rechnung nur für die Anzeige (Fehler dort lassen den Plan unberührt, Grund
  `failed`); Aus: kein Vorheiz-Plan.
- Kein Plan der Raumheizung (Planziel = Soll), mit Grund: `mode_off`, `no_model`, `no_sample`, `program` (mit
  Programm), `no_room`, `no_outside`, `failed`; ohne Wärmepumpe gar keine Anzeige. Ohne steuerbaren Speicher rechnet
  Sunflow keinen Plan (bestehende Einschränkung, Nutzer 22.11.: so lassen).
- Ausgabe `PlanResult.house` → `GET /api/v1/plan…` unter `house` (`mode` `active`|`shadow`, `reason`, Soll, Band,
  Raum-Ist, je Slot `target_c`, `power_w`, `heat_w`, `baseline_w`, `outside_c`, `cop`; Strom geplant/ohne Planung in
  kWh); Plan-Ansicht Abschnitt „Raumheizung“ (Planziel im Spielraum, Stromaufnahme geplant/ohne Planung; „würde
  vorheizen“ bei „Nur Lernen“). `plan_target_c` im Status bleibt bis 4.3 der Soll.
- Neuplanung sofort bei Änderung von Soll, Spielraum, Modus (`PUT /api/v1/house/climate`) und Schalter
  (`PUT /api/v1/heat_pump/<id>/writing`). Gespeicherte Lastprognose (`planned_load_w`) mit der Grundlast der Wärmepumpe,
  frühe Neuplanung vergleicht den Verbrauch mit Last + geplanter Wärmepumpe.
- Beobachtet beim Testen: oberhalb des Bands kostet es 0,2 €/K·h; bei großen Preisunterschieden plant Sunflow einige
  Zehntel Kelvin über Soll + `preheat_k` (bewusst wie bisher „teuer, aber möglich“).

**Außentemperatur – gemessen für jetzt, Prognose für später:**

| Verwendung | Quelle |
|---|---|
| aktueller Bedarf (Vorsteuerung 8.1, Korrektur 8.2) | **gemessene** Außentemperatur: Sensor der WP (`temperature_outside` oder Außengerät `ta2`) oder eigener Sensor (Lernquelle „Außentemperatur“), wählbar (9.3) |
| künftige Slots der Planung (vorheizen, kalte Nacht, PV-Fenster) | **Prognose** der `weather`-Entity (`weather.get_forecasts`, stündlich; wie heute in `planning/plan.py`) |

- Die Prognose ist nur so genau wie das Wettermodell (typisch ±1–2 K) und weicht örtlich ab. Sunflow
  lernt die Abweichung Prognose ↔ eigene Messung (abhängig von Tageszeit und Vorlaufzeit) und korrigiert
  die Prognose damit. Restfehler fängt die Raum-Korrektur mit echten Messwerten auf.
- Bei Fußbodenheizung ist die Prognose besonders nützlich: wegen der Trägheit des Estrichs muss vor
  Kälte früher angehoben werden.
- **Keine Pflicht-Abhängigkeit:** Ohne Wetter-Entity (oder wenn der Abruf fehlschlägt) nimmt die Planung
  an, dass die gemessene Außentemperatur gleich bleibt; Regelung und Lernen laufen normal, nur das
  vorausschauende Anheben entfällt. Hinweis in der UI. In der Regel ist eine Wetter-Entity vorhanden
  (HA legt bei der Einrichtung *Met.no* an); wählbar wie heute (`optimizer.weather_entity`, sonst die
  erste gefundene).

---

## 8. Vorlauf-Regler und Schutzschicht (S1)

### 8.1 Vorsteuerung über die Kennlinie

Wärmeabgabe der Heizflächen:

`Q = K · (Tw − T_Raum)^n` mit `Tw = (Tv + Tr)/2`, Spreizung `s = Tv − Tr`

- `n` je Heizflächentyp vorgegeben: Fußbodenheizung 1,1 · Heizkörper 1,3 · gemischt 1,2 (Einstellung
  `house.heating_surface` in `config.json`: `underfloor`/`radiator`/`mixed`, Standard `underfloor`;
  fehlt das Feld oder ist der Wert unbekannt, z. B. aus einer neueren Version, gilt der Standard ohne
  Fehler). `n` ist der Startwert der Kennlinie und wird nicht gelernt (9.2). Startwerte je Typ zusammen
  mit den Startgrenzen des Stellwerts (8.3; Fußboden 25/40 °C, gemischt 25/50 °C, Heizkörper 30/55 °C)
  und der Nachstellzeit Ti der Korrektur (8.2) an einer Stelle: `HEATING_SURFACES` in `heat_pump/defaults.py`. Ändert der Nutzer den Typ, wird die
  Kennlinie sofort neu bewertet; die Daten in `heat_pump_samples` bleiben, Hinweis im UI.
- `K` gelernt (Abschnitt 9). Spreizung `s` gelernt als Funktion der Leistung (einfaches Modell, z. B.
  linear in Q).
- Benötigte Wärme `Q*`: aus der Planung (ab Schritt 4), sonst aus dem Hausmodell
  `Q* = (a·(Planziel − T_außen) − d − c·S)/b` (Gewinne: innere `d`, Sonne `c·S`); ohne nutzbares
  Hausmodell die mittlere abgegebene Wärme der letzten 3 h (Gleichgewicht, Quelle `heat_recent`).
  `T_außen` ist für den aktuellen Slot die gemessene Außentemperatur (Abschnitt 7), nicht die Prognose.
- Vorlauf-Ziel: `V_Ziel = Planziel + (Q*/K)^(1/n) + s(Q*)/2 + Korrektur`.
- **Rangfolge der Quellen** (Status `source`): `house_model` (Hausmodell nutzbar, Außentemperatur
  gemessen), sonst `heat_recent`, sonst `base` (V_Ziel = Basis + Korrektur, unten). `base` gilt auch, wenn
  die Kennlinie nicht nutzbar ist (zu wenig Daten, nicht plausibel, nicht besser als „Vorlauf bleibt“,
  Heizflächentyp geändert) oder `Q*` ≤ 200 W (`FEED_HEAT_MIN_W`, auch `Q*` ≤ 0); der Grund steht in
  Status und UI (`source_reason`).
- `heat_recent`: Mittel der Minutenwerte im gültigen Heizbetrieb der letzten 3 h (`FEED_HEAT_WINDOW_S`),
  Kompressor-Pausen (Aktivität Bereitschaft im Programm Heizen, sonst nichts eingefroren) mit 0.
  Minuten mit Warmwasser, Abtauen, Nachlauf, Lüften oder Sperre zählen weder als Wärme noch als Zeit.
  Weniger als 2 h gezählte Minuten (`FEED_HEAT_MIN_S`) → `base`. Nach einem Neustart aus den Zeilen von
  `heat_pump_samples` der letzten 3 h vorbelegt. **Zirkularität:** Diese Wärme entsteht aus dem Vorlauf,
  den die HPSU gerade fährt; die Vorsteuerung bildet damit nur den aktuellen Zustand nach, die Richtung
  kommt allein aus der Korrektur.
- Quellenwechsel: Die Korrektur bleibt unverändert (sie gilt je Strategie, nicht je Quelle); im Schatten
  springt V_Ziel dabei einmal, ohne Sprungschutz. Der Wechsel steht mit alter und neuer Quelle in
  `heat_pump_samples` (`events`, `kind: flow_source`, `from`, `to`), nicht im Journal; das UI zeigt
  Quelle, Basis- bzw. Kennlinienwert und Korrektur getrennt. Umsetzung `heat_pump/feedforward.py`.
- **Sprungschutz beim Quellenwechsel** (3, Teil 4.5; wirkt nur bei aktivem Schreiben, das erst Teil 4.7
  einschaltet – bis dahin nur in der Simulation getestet): Startwert = Kennlinienwert bzw. Basis. Beim
  Wechsel wird die Korrektur der aktuellen Strategie um alter − neuer Startwert verschoben (nach dem
  normalen Schritt der Korrektur in dieser Minute), sodass V_Ziel bleibt; die verschobene Korrektur bleibt
  in ±3 K (`CORRECTION_LIMIT_K`), der Rest bleibt als Sprung (`source_shift` in `heat_pump/feedforward.py`).
  Der Raum-Bezug der Korrektur bleibt (kein P-Schritt). Der alte Startwert ist der zuletzt berechnete
  (`start_c`/`start_at` je Gerät in `heat_pump_state.json`, übersteht einen Neustart) und gilt nur, wenn er
  höchstens 30 min alt ist (`SOURCE_SHIFT_MAX_AGE_S`); sonst kein Sprungschutz. Journal (aktiv): Rest über
  0,05 K (`SOURCE_JUMP_NOTE_K`) mit Größe des Sprungs, ebenso ein Wechsel ohne Sprungschutz (alter Wert
  unbekannt oder zu alt); ohne V_Ziel (keine Basis, Strategie unbekannt) nichts. Im Schatten keine
  Verschiebung und kein Journal. Ereignis `flow_source` zusätzlich mit `shadow`, `jump_k` (neuer − alter
  Startwert, wenn bekannt) und aktiv `protected`, `shift_k`, `rest_k` (Abschnitt 10); der letzte Wechsel im
  Status als `source_change`. Tests `tests/test_heat_pump_source_shift.py`.
- Ohne gelernte Kennlinie (Start): V_Ziel = zuletzt wirksamer Vorlauf-Soll; nur die Korrektur bewegt ihn.
  Umsetzung (2c, Teil 2b, `heat_pump/shadow.py`): V_Ziel = Basis + Korrektur. Solange Sunflow nicht
  schreibt (Schatten, Modus `off`), ist die Basis laufend der wirksame Vorlauf-Soll, übernommen nur bei
  gültigem Heizbetrieb (Abschnitt 4); sonst gilt der letzte solche Wert (in `heat_pump_state.json`,
  übersteht einen Neustart). Ohne einen solchen Wert kein V_Ziel, Grund `no_base`.
  **Schritt 3:** Beim Übernehmen wird die Basis eingefroren und die Korrektur auf 0 gesetzt; V_Ziel
  springt dadurch nicht.

### 8.2 Raum-Korrektur

PI auf Raum-Ist gegen Planziel, addiert auf V_Ziel.
- Proportionalteil auf **Raum-Ist** (nicht auf die Regelabweichung) → Soll-Änderungen erzeugen keinen
  Sprung.
- Integralanteil: lange Zeitkonstante (Fußboden: viele Stunden; Heizkörper schneller), Totzone ±0,2 K,
  Begrenzung ±3 K, Anti-Windup.
- **Bedingte Integration**: eingefroren außerhalb des gültigen Heizbetriebs (Abschnitt 4), bei
  Lüft-Ereignis, Soll-Änderung bis Raum-Ist das neue Ziel erreicht hat, fehlenden Raumdaten, Stellwert an
  einer Grenze.
- Integralanteil wird je Strategie gespeichert.

Umsetzung (2c, Teil 2b, `heat_pump/correction.py`, Konstanten in `heat_pump/defaults.py`; Ti je
Heizflächentyp seit 3, Teil 4.1):
- Inkrementell: ΔKorrektur = Kp/Ti · e · Δt − Kp · ΔRaum-Ist, e = Planziel − Raum-Ist (Planziel =
  Raum-Soll bis Schritt 4); Kp = 1,0 K Vorlauf je K Raum für jeden Typ, Ti je Heizflächentyp
  (`house.heating_surface`, `HEATING_SURFACES`, `ti_h`): Fußboden 6 h, gemischt 4 h, Heizkörper 2 h;
  unbekannter Typ wie der Standard. Ein Wechsel des Typs ändert nur die Inkremente ab dann, nicht den Wert
  der Korrektur (kein Sprung, keine Rückrechnung); das gilt im Schatten wie aktiv. Status
  `parameters.ti_h` und UI zeigen das aktuelle Ti. Der I-Teil wirkt nur außerhalb der
  Totzone ±0,2 K. Begrenzung ±3 K: ein Inkrement, das darüber hinausführt, wird abgeschnitten.
- Ein Schritt wird gerechnet, sobald sich das Raum-Ist ändert, spätestens 30 min nach dem letzten mit
  dem letzten Raum-Ist (dann nur I-Teil); Δt höchstens 30 min, auch nach einer Lücke.
- Eingefroren (kein gültiger Heizbetrieb, Soll-Änderung, keine Raumdaten `rooms_missing`, Lüften
  `ventilation` (6.2, ab 3, Teil 1), Strategie unbekannt): weder I- noch P-Inkrement; was Raum-Ist in
  der Pause tut, wird verworfen, nicht nachgeholt (kein Sprung beim Wiederaufnehmen). Nach einem
  Neustart dient der erste Wert als Bezug. Ein Strategiewechsel beginnt die Korrektur der neuen
  Strategie ebenso ohne Sprung, ebenso ein Wechsel der Räume, aus denen Raum-Ist besteht (neuer Bezug
  ohne P-Schritt).
- Soll-Änderung (≥ 0,1 K, im Minutentakt aus der Konfiguration erkannt, egal ob über HA, UI oder API):
  eingefroren, bis Raum-Ist das neue Ziel bis auf die Totzone erreicht (Anheben: Ist ≥ Soll − 0,2;
  Absenken: Ist ≤ Soll + 0,2), höchstens 24 h; liegt Raum-Ist schon in der Totzone, wird nicht
  eingefroren. Eine neue Änderung während des Wartens gilt ab dann mit neuem Ziel, neuer Richtung und
  neuer Frist. Frist abgelaufen: Korrektur läuft weiter, ein Log-Eintrag. Status `setpoint_change` mit
  Ziel und Richtung, kein Journal. Zustand in `heat_pump_state.json`.
- „Stellwert an einer Grenze“ greift erst mit dem Schreiben (Schritt 3).
- Schattenbetrieb: Die Korrektur wird gerechnet, gespeichert und angezeigt (gekennzeichnet als
  Schatten), wirkt aber nicht zurück und läuft deshalb leicht an eine Grenze. **Schritt 3:** Beim
  Wechsel auf aktiv beginnt sie bei 0, nicht mit dem Schattenwert.

### 8.3 Grenzen

- Stellwert in [min, max] (Nutzereinstellung je Wärmepumpe). Startwerte Fußbodenheizung 25 / 40 °C,
  Heizkörper höher; nie außerhalb der Grenzen der Entity (Manifest `min`/`max`).
- Leistungsbereich der Wärmepumpe (Heizleistung min/max, Start HPSU 0,6 / 6 kW; die Klasse des
  Außengeräts steht im Manifest `outdoor_unit`): begrenzt die Wärme in der Planung, und die
  Mindestleistung erkennt „Mindestleistung > Bedarf“ (Takten absehbar → Sperre statt Nachführen, 8.4).
  Später aus den Messwerten (`thermal_power` im Dauerlauf) gelernt.
- Plausibilität: V_Ziel außerhalb eines Bands um den Wert der gelernten Kennlinie bzw. letzten wirksamen
  Soll (Start ±8 K) → nicht schreiben, Journal.

Umsetzung (3, Teil 3, nur Schatten; `heat_pump/limits.py`, `heat_pump/learned.py`, Konstanten in
`heat_pump/defaults.py`):
- Stellwertbereich: Nutzereinstellung je Wärmepumpe `flow_min_c`/`flow_max_c` (Gerät in `config.json`,
  seit 0.80.11; `PUT /api/v1/heat_pump/<id>/flow_limits`, im Status `flow_limits`), eine leere Seite =
  Startgrenze des Heizflächentyps aus `HEATING_SURFACES`; immer eingeengt auf `min`/`max` der
  beschriebenen Entity im Manifest. `flow_max_c` ist zugleich der höchste Vorlauf für den Estrich und
  schützt: liegt die Untergrenze darüber (z. B. nach Wechsel des Heizflächentyps), gilt die Obergrenze für
  beide. Eingabe erlaubt 15–70 °C, auf Zehntel gerundet; min muss unter max liegen (auch mit dem Startwert
  der leeren Seite), sonst 422. Rundung auf `step` der Entity (ohne Angabe die gröbste erlaubte Schrittweite 0,5 K):
  Vorlauf-Ziel zum nächsten Schritt, Schutzwerte aufwärts, alles höchstens bis zur Obergrenze.
- Plausibilität: Bezug ist der aktuelle wirksame Vorlauf-Soll und, wenn die Quelle die Kennlinie ist,
  deren Wert; weicht V_Ziel von einem davon mehr als 8 K (`PLAUSIBLE_BAND_K`) ab, wird kein normaler
  Wert geschrieben (Schutzschritte bleiben), Journal einmal je Episode.
- Leistungsbereich: Startwerte aus dem Profil (`heat_power`), gelernt aus Zeilen im Dauerlauf (gültiger
  Heizbetrieb, Kompressor in der Zeile und den 3 Zeilen davor durchgehend an, kein Start), ab 288 Zeilen
  der letzten 28 Tage: Mindestleistung = 5-%-Quantil der Wärme, Höchstleistung nur angehoben
  (99-%-Quantil, wenn darüber), weil milde Wochen sie nicht zeigen.
- Grenze erreicht (Grund im Status `limited`): V_Ziel an Ober-/Untergrenze (`limit_max`/`limit_min`),
  gemessene Wärme bei laufendem Kompressor innerhalb 5 % an der Höchst-/Mindestleistung
  (`power_max`/`power_min`), nicht plausibel (`implausible`). Die Korrektur ist dann **in Richtung der
  Grenze** eingefroren (Inkremente dorthin werden verworfen wie ein eingefrorener Schritt, in
  Gegenrichtung zählen sie), damit sie nicht an der Grenze hängen bleibt; ausgewertet wird die Grenze der
  vorigen Minute.

### 8.4 3-K-Schaltregeln (Profilparameter)

`start_margin` (Kompressor startet, wenn Soll ≥ Tv + 3,0 K), `stop_margin` (aus, wenn Tv ≥ Soll + 3,0 K).
Die HPSU schaltet exakt an diesen Grenzen; Temperaturen haben eine Nachkommastelle. Keine Reserve.

Das Risiko der 3-K-Abschaltung liegt fast nur beim **Kompressorstart**, nicht im laufenden Betrieb
(beobachtet an einer HPSU):
- Laufender Heizbetrieb: Tv schwankt nur wenig (einige Zehntel Kelvin).
- Start: Tv springt kurz nach dem Start (innerhalb etwa einer Minute) um mehrere Kelvin über den Wert vor
  dem Start und fällt dann zurück; nach Warmwasser startet die HPSU selbst.
- Ungewollte Abschaltungen entstehen typischerweise kurz nach dem Start nach Warmwasser, wenn der Soll
  vor/beim Start abgesenkt ist; danach läuft der Kompressor lange nicht wieder an, weil die Startregel
  nicht erfüllt ist.

**Von Sunflow im Betrieb gelernt, je Anlage** (statt fester Abstände). Sunflow misst diese Werte an der
jeweiligen Wärmepumpe laufend selbst und passt seine Abstände daran an. Die Werte in Klammern sind nur
vorsichtige Startwerte, die gelten, bis genug eigene Messungen vorliegen:
- `steady_swing`: Schwankung von Tv im eingeschwungenen Heizbetrieb (0,5 K);
- `start_jump`, `start_settle`: Höhe und Dauer des Sprungs nach einem Start, getrennt für Start nach
  Warmwasser, nach Abtauen und normal (6,0 K bzw. 5 min).
- Grundlage: Sensor `tv_max` der CAN-Komponente (Pflichtrolle). Die Komponente liest Tv alle 2 s,
  veröffentlicht aber im Intervall von `tv` (z. B. 30 s) **beide zusammen**: `tv` = letzter Messwert,
  `tv_max` = höchster Messwert seit der vorigen Veröffentlichung (inklusive des letzten). Damit gehen
  Spitzen zwischen zwei Tv-Werten nicht verloren, ohne dass HA mehr Werte speichert.
  Fenster ohne gültige Messung: keine Veröffentlichung. Gleicher Wert zweimal → HA legt keinen neuen
  Eintrag an; Bedeutung bleibt (gilt bis zum nächsten Wert).
- Zeitbezug: `tv_max` mit Zeitstempel T = Maximum im Zeitraum (vorige Veröffentlichung, T]. `tv` und
  `tv_max` kommen als zwei getrennte Zustandsänderungen kurz nacheinander an; Sunflow kann dazwischen
  lesen. Deshalb kein Paar-Vergleich zum selben Zeitpunkt: `tv_max` wird als eigene Zeitreihe ausgewertet
  (Spitze je Intervall), und Entscheidungen nutzen vorsichtig `max(tv, tv_max)` der jeweils neuesten Werte.
- Ungewollte Abschaltung (Kompressor aus, Tv ≥ Soll + 3,0, nicht von Sunflow gewollt) → Journal, betroffener
  Wert sofort erhöht; ohne Vorfälle sinkt er langsam (Wochen). Ziel: keine Vorfälle.

**Ablauf:**
- **Starten**: Heizbedarf und Kompressor aus → Stellwert ≥ max(Tv + start_margin, Tv + `start_jump` −
  2,9). Die Startregel allein deckt einen Sprung bis 5,9 K; ist `start_jump` größer, gilt der zweite Wert.
- **Vor Warmwasser-Ende vorbereiten**: Während Warmwasser (Max T-Vorlauf wirkt dann nicht auf den
  Heizbetrieb; einmal beobachten) den Stellwert so setzen, dass der Start nach Warmwasser sicher ist:
  ≥ Tv vor dem Warmwasser + `start_jump` (nach Warmwasser) − 2,9. Ist er schon hoch genug, nichts schreiben.
- **Halten**: nach einem Start den Stellwert nicht senken, bis `start_settle` vorbei ist und Tv flach ist.
- **Danach einmal absenken** auf `max(V_Ziel, Tv + steady_swing − 2,9)` – ein Schritt statt vieler.
- **Nachführen** (laufender Betrieb): erreicht Tv + `steady_swing` die Abschaltgrenze Soll + 3,0 (z. B.
  Mindestleistung > Bedarf), Stellwert anheben, soweit das Notkontingent reicht; sonst Sperre (unten).
- Geschrieben wird nur, wenn nötig: Schutzschritt (Untergrenze über aktuellem Soll) oder lohnende Änderung
  (≥ Totzone) bei ruhigem Tv. Nicht schreiben, solange Tv noch schwankt.
- Pro Start damit höchstens zwei Schreibvorgänge (vorbereiten/halten, einmal absenken).

- **Rundung**: Stellwerte auf die Schrittweite der Entity aus dem Manifest (`step`, Max T-Vorlauf: 0,1 °C)
  runden, und zwar immer in die sichere Richtung – bei Starten und bei der Untergrenze gegen Abschalten
  **aufrunden**. Die Schrittweite kommt aus dem Manifest, nicht aus der Zahl der Nachkommastellen: sie kann
  auch z. B. 0,5 sein.
- **Gewollt aus**: Plant Sunflow keine Wärme (Raum über Ziel, Absenken, teurer Strom), schaltet es den
  Kompressor immer über die Raumthermostat-Sperre aus (unten). Max T-Vorlauf wird nie absichtlich unter
  die Abschaltgrenze gesetzt.
- **Sperre über den Raumthermostat-Eingang** (`room_therm`, HPSU „Raum Thermostat“): `on` = zu warm,
  die HPSU heizt nicht (Kompressor/Heizung/Vorlauf gesperrt); `off` = freigegeben. Dafür ist der Eingang
  in der HPSU gedacht. Sunflow nutzt ihn für jedes gewollte Aus – z. B. Mindestleistung > Bedarf, Raum
  deutlich über Ziel, Strom zu teuer (Planung). Freigabe: `off`, danach Startregel (Stellwert ≥ Tv + 3 K).
  Mindestdauer je Zustand (Start 20 min) gegen Takten. Bei Ende der Steuerung und Ausfall wird sie nicht
  zurückgenommen (8.6).
- Nicht verhinderbar: Takten, wenn Mindestleistung > Bedarf und Budget erschöpft → Journal-Ereignis.

Umsetzung (3, Teil 3, nur Schatten; `heat_pump/guard.py`, gelernte Werte `heat_pump/learned.py`,
Konstanten in `heat_pump/defaults.py`). Sunflow schreibt nichts; „geschrieben“ ist hier der Wert, den
Sunflow zuletzt geschrieben hätte (virtuell, Start beim vorgefundenen Max T-Vorlauf), ebenso die Sperre
(Start beim vorgefundenen Zustand). Tv und Kompressor sind die echten der Wärmepumpe. Jeden Takt:
- Unterer Schutzwert „− 2,9“ = `stop_margin` − 0,1 (`SHUTDOWN_RESOLUTION_K`).
- **Start:** Beginn von Heizen mit laufendem Kompressor (Kompressorstart oder Wechsel von Warmwasser/
  Abtauen auf Heizen). Art: nach Warmwasser bzw. Abtauen, wenn das innerhalb von 30 min endete
  (`START_AFTER_S`), sonst normal. Bezug: Tv vor dem Warmwasser bzw. Abtauen (letzter Tv bei Heizen/
  Bereitschaft mit BPV 0), sonst Tv der Minute vor dem Start. Die erste Minute nach einem Neustart von
  Sunflow ist kein Start.
- **Pause** (Programm Heizen, Kompressor aus, nicht gesperrt): Untergrenze max(Tv + `start_margin`, Tv +
  `start_jump` − 2,9); innerhalb von 30 min nach Warmwasser/Abtauen stattdessen die Vorbereitung (unten),
  weil die HPSU dann selbst startet. Jedes Aus, das nicht über die Sperre gewollt ist, gilt als Heizbedarf.
- **Warmwasser:** Untergrenze Tv vor dem Warmwasser + `start_jump` (nach Warmwasser) − 2,9; Abtauen:
  nichts (8.5), der Start danach wird mit seinem Bezug geschützt.
- **Halten:** ab dem Start Untergrenze Bezug + `start_jump` der Art − 2,9; Ende, wenn `start_settle` der Art
  vorbei und Tv ruhig ist (Spanne von Tv inklusive `tv_max` über 5 min ≤ `steady_swing`,
  `TV_FLAT_WINDOW_S`). Während des Haltens wird nur nachgeführt, wenn die Spitze selbst die Grenze
  erreicht (Spitze − 2,9 statt Spitze + Schwankung − 2,9, der Sprung ist schon eingerechnet).
- **Normal** (laufender Betrieb nach dem Halten, gültiger Heizbetrieb, Tv ruhig, plausibel): Ziel
  max(V_Ziel in Grenzen, Spitze der letzten 5 min + `steady_swing` − 2,9); geschrieben, wenn es um mehr als
  die Totzone vom geschriebenen abweicht. Das erste solche Schreiben nach einem Start ist das einmalige
  Absenken.
- **Nachführen:** Spitze der letzten 5 min + `steady_swing` − 2,9 über dem geschriebenen Wert → anheben auf
  diesen Wert + 0,5 K (`TRACK_HEADROOM_K`), damit ein steigender Tv nicht jede Minute einen
  Schreibvorgang auslöst. Ist der Bedarf `Q*` (8.1) bekannt und kleiner als die Mindestleistung, und die
  Sperre darf schalten → Sperre statt Nachführen (`min_power`).
- **Sperre** (nur mit Rolle `heating_block`): an, wenn Raum-Ist ≥ Planziel + 0,5 K (`BLOCK_ROOM_ABOVE_K`,
  `room_warm`) oder wie oben `min_power`; aus bei `room_warm`, wenn Raum-Ist ≤ Planziel + 0,2 K, bei
  `min_power`, wenn ≤ Planziel − 0,2 K; ohne Raum-Ist aus. Jeder Zustand mindestens `min_duration` (20 min).
  Gesperrt: kein Stellwert.
- **Fremde Sperre** (`foreign`, seit 0.80.11): Eine Sperre, die Sunflow nicht gesetzt hat (beim Start
  vorgefunden oder später von Hand, durch eine Automation oder einen anderen Regler gesetzt), hebt Sunflow
  nicht auf (8.6). Phase `blocked`, kein Stellwert, keine eigene Sperr-Entscheidung; Journal (Warnung)
  einmal beim Erkennen, im Status `block_reason` = `foreign`, im UI deutlich als „nicht von Sunflow
  gesetzt“. Hebt sie jemand anders auf, regelt Sunflow wieder; die Mindestdauer läuft ab dann. Im
  Schatten ist jede echte Sperre fremd; ab Teil 4 ist eine Sperre, die Sunflow selbst geschrieben hat,
  nicht fremd. Der frühere Wert `adopted` in `heat_pump_state.json` wird als `foreign` gelesen.
- **Kein Schreiben** (Status `inactive`): Modus `off`, Strategie ≠ `fixed`, Gerät nicht nutzbar, Programm ≠
  Heizen, unbekannte Aktivität, Störung, Tv oder Max T-Vorlauf unbekannt, ESP-Regelung aktiv
  (`esp_regulation`, seit 3, Teil 4.4, 8.5).
- **Ungewollte Abschaltung:** Kompressor an → aus, vorher Aktivität Heizen, nicht Warmwasser/Abtauen danach,
  keine (echte) Sperre, höchste Spitze der letzten 3 min (inklusive der Veröffentlichungen seit der letzten
  Minute) ≥ wirksamer Soll + `stop_margin` − 0,2 K (`STOP_TOLERANCE_K`). Innerhalb von 30 min nach einem
  Start wird `start_jump` dieser Art erhöht (gesehen: Spitze − Bezug), sonst `steady_swing` (Spitze − Mittel
  von Tv der letzten 5 min); jeweils auf das Gesehene + 0,5 K (`BUMP_EXTRA_K`), wenn das mehr ist als der
  aktuelle Wert. Die Erhöhung sinkt um 0,1 K je Tag (`BUMP_DECAY_K_PER_DAY`) und wird in
  `heat_pump_state.json` gehalten. Im Schatten sind das Abschaltungen der heutigen Regelung.
- **Gelernt** (stündlich mit der Kennlinie, Cache `devices.<id>.learned`): `steady_swing` = 95-%-Quantil
  von `tv_max_c − tv_c` der Dauerlauf-Zeilen (ab 288); `start_jump`, `start_settle` je Art = 90-%-Quantil
  der Ereignisse `start_jump` der letzten 28 Tage (ab 5 je Art), sonst die Startwerte aus dem Profil.
- Ergebnis: Stellwert (virtuell geschrieben), Vorlauf-Ziel in Grenzen, Schutz-Untergrenze, Phase (`idle`,
  `hot_water`, `defrost`, `start_hold`, `running`, `blocked`, `inactive`), Sperre, die letzten 20
  geplanten Schreibvorgänge und ungewollten Abschaltungen, gelernte Werte mit Herkunft (`start`,
  `learned`, `raised`) im Status `state.flow.guard`; Ereignisse in `heat_pump_samples` (10); Journal bei
  ungewollter Abschaltung (Warnung), Sperr-Entscheidung („Schatten: Sunflow würde die
  Raumthermostat-Sperre setzen/aufheben …“) und nicht plausiblem V_Ziel.
- Zählung je Tag (Zeitzone von HA): `GET /api/v1/heat_pump/<id>/shadow_writes?days=7` aus den Ereignissen;
  normal, Start, Nachführen, Sperre an/aus, ungewollte Abschaltungen, dazu das Budget des Profils. Seit
  Teil 4.2 mit Mindestabstand (8.5), noch ohne Budget (Teil 4.3), daher eine Obergrenze dessen, was aktiv
  geschrieben würde.
- Seit Teil 4.2 schreibt die Schutzschicht nicht mehr selbst: Sie liefert je Takt den gewünschten Stellwert
  mit Grund (`normal`, `start`, `track`; Schutzschritt = `start`/`track`), die Schreibschicht (8.5)
  entscheidet, wann er geschrieben wird. Sperre an/aus geht nicht durch die Schreibschicht (eigene
  Mindestdauer, kein EEPROM).

### 8.5 Schreiben

Berechnen ≠ Schreiben.

- Bei jedem Anlass (Raum-Soll-Änderung, neuer Plan, Messwert, Zustandswechsel) werden sofort neu
  berechnet und angezeigt: das Vorlauf-Ziel `V_Ziel` und daraus der Stellwert (Max T-Vorlauf nach
  Schaltregeln, Grenzen, Rundung). **Kein Entprellen** der Berechnung. Ob der Stellwert auch geschrieben
  wird, entscheiden die Regeln unten. Tv selbst wird nie gesetzt – den regelt die HPSU.
- Schreiben:
  - erste Änderung nach Ruhe: **sofort** (Vorderflanke);
  - weitere Änderungen innerhalb des Mindestabstands: nur der letzte Wert wird gemerkt;
  - nach Ablauf des Mindestabstands: letzten Wert schreiben, falls er sich vom geschriebenen unterscheidet;
  - nur bei Abweichung > Totzone (Start 0,5 K) vom geschriebenen Wert, außer Schutzschritte.
- **Mindestabstand** = kürzeste Zeit zwischen zwei Schreibvorgängen auf den Stellwert; verhindert, dass
  viele kleine Änderungen einzeln ins EEPROM geschrieben werden.
  - Normalbetrieb (Raum-Soll-Änderung, neuer Plan): 5 min, einstellbar (zum Testen z. B. 1 min).
    Beispiel: Raum-Soll 10:00 auf 23,0, 10:01 auf 22,5, 10:02 auf 22,8 → 10:00 Stellwert zu 23,0 sofort
    geschrieben; 10:01/10:02 nur gemerkt; 10:05 Stellwert zu 22,8 geschrieben – zwei statt drei.
  - Schutzschritte (Anheben gegen die 3-K-Abschaltung, Start): 2 min. Sie dürfen nicht 5 min warten;
    kürzer ist nicht sinnvoll, weil die HPSU einen neuen Wert erst nach 30–60 s übernimmt und man dessen
    Wirkung sonst noch nicht sieht.
- **Budget** (`EepromGuard`): normal 48/Tag, Notkontingent zusätzlich 24/Tag nur für Schutzschritte
  (Vorbereiten vor Warmwasser-Ende, Nachführen, Start); Obergrenze je Stunde 6. Werte im Profil, einstellbar.
  Die Raumthermostat-Sperre zählt nicht: die HPSU legt den Eingang nicht im EEPROM ab (15, Punkt 7); gegen
  Takten wirkt die Mindestdauer je Zustand.
- Taste „jetzt schreiben“ im UI überspringt den Mindestabstand (zählt gegen das Budget).
- Kein Schreiben bei `hot_water`, `defrost`, `unknown`, BPV > 0 %, Programm ≠ heating, Gerät nicht
  erreichbar, ESP-Regelung aktiv (`supply_setpoint_regulated` > 0 → ESP regelt selbst → Hinweis,
  keine Übernahme).
- **Fremde Schreiber**: Ändert sich Max T-Vorlauf, ohne dass Sunflow geschrieben hat (pyscript,
  Automation, ESP-Regelung, Hand), meldet Sunflow das im Journal und in der Zusammenfassung.
- Anzeige: „geschrieben 10:42: 31,2 °C“ / „ausstehend: 30,6 °C, wird um 10:45 geschrieben“ / Grund.
- Journal: jeder Schreibvorgang mit Grund (Soll-Änderung, Plan, Schutzschritt, Start, manuell).

Umsetzung (3, Teil 4.2, `heat_pump/writer.py`, Schicht hinter der Schutzschicht 8.4; `write_step` verbindet
beide je Takt; noch nichts an die Wärmepumpe):
- Eingang je Takt: gewünschter Stellwert, Grund und ob Schutzschritt (von der Schutzschicht), dazu der
  geschriebene Wert mit Zeit (im Schatten virtuell). Kein Wunsch (nichts nötig, `inactive`, gesperrt) →
  nichts ausstehend; ein ausstehender Wert wird nicht gespeichert, sondern jeden Takt neu berechnet, also
  gilt immer nur der letzte.
- Rundung auf `step` in die sichere Richtung (Schutzschritt aufwärts, normal zum nächsten Schritt), dann in
  [min, max] (8.3). Normal nur bei Abweichung > Totzone; Schutzschritt bei jeder Anhebung, nie abwärts.
- Mindestabstand ab dem letzten Schreibvorgang auf den Stellwert, gleich welcher Art: normal
  `writes.min_interval` (5 min), Schutzschritt `writes.protect_interval` (2 min); fehlt der Wert im Profil,
  gelten diese, ein kleinerer als 60 s (`WRITE_INTERVAL_FLOOR_S`) zählt als 60 s. Einstellbar nur im Profil
  (Entscheidung des Nutzers). Ohne bekannten letzten Schreibvorgang (Start, Neustart ohne Zeit) sofort.
- Ausstehend (Status `guard.pending`): Wert, Grund, Schutzschritt, `due_at` (wann er geschrieben wird) und
  `held` (`spacing` oder `budget`, dann ohne `due_at` und mit `budget` = `day`/`emergency`/`hour`). UI: „würde um … geschrieben“ im
  Schatten, „wird um … geschrieben“ aktiv.
- „Jetzt schreiben“ (`POST /api/v1/heat_pump/<id>/write_now`, Ablauf vom Nutzer festgelegt): 202, wenn das
  Schreiben aktiv ist und ein Wert aussteht; 409 mit `reason` `not_active` (Schatten) bzw. `nothing_pending`;
  404 ohne Wärmepumpe. Der Wunsch wird nur im Speicher gemerkt (nicht über einen Neustart) und im nächsten
  Takt mit frischen Messwerten über denselben Weg geschrieben (Schutzschicht, Grenzen, Rundung, Budget),
  ohne Mindestabstand. Kann er dort nicht geschrieben werden (`nothing_pending`, `inactive`, `budget`,
  `not_active`), verfällt er: Grund im Status (`guard.write_now.last`) und im Journal. Das Ereignis `write`
  trägt dann `manual: true` (Grund bleibt der der Schutzschicht und zählt wie dieser), das Journal nennt den
  Grund „manuell“. Status `guard.write_now.requested_at`, solange er wartet; das UI zeigt „wird
  geschrieben …“ bis zum Takt und die Taste nur bei aktivem Schreiben.
- Journal: im Schatten keins (Ereignisse wie bisher, `shadow: true`); bei aktivem Schreiben je Schreibvorgang
  „Max T-Vorlauf geschrieben: … (vorher …), Grund: …“. Aktiv schaltet der Schalter je Wärmepumpe (Teil 4.7,
  `FlowShadow.writing_active`, Standard aus); Status `guard.shadow` folgt dem.
- Status `guard.parameters.min_interval_s`/`protect_interval_s`; Tests `tests/test_heat_pump_writer.py`.

Umsetzung Budget (3, Teil 4.3, `heat_pump/budget.py`, Hook `budget` der Schreibschicht; noch nichts an die
Wärmepumpe):
- Grenzen aus `writes.per_day`/`emergency_per_day`/`per_hour` (fehlt ein Wert: 48/24/6, `ControlSettings.budget`).
  Gezählt wird jeder Schreibvorgang des Stellwerts, gleich welcher Grund (auch „jetzt schreiben“). Normal
  geht bis `per_day`; ein Schutzschritt nutzt zuerst das normale Budget, dann das Notkontingent, also bis
  `per_day + emergency_per_day`; `per_hour` gilt für alle, gleitend über 60 min.
- Tag = Kalendertag in der Zeitzone von HA (stündlich neu gelesen); solange sie unbekannt ist, zählen die
  letzten 24 h (nie großzügiger als ein Kalendertag). Sommerzeitwechsel: Tagesbeginn je lokalem Datum.
- Schatten: virtuell gezählt (`VirtualLog`), beim ersten Takt aus den Ereignissen `write` mit Grund
  normal/start/track der letzten 26 h in `heat_pump_samples` (und den noch nicht gespeicherten) befüllt,
  danach je virtuellem Schreibvorgang; doppelte Ereignisse zählen einmal, `eeprom_writes.json` bleibt
  unberührt. Aktiv (ab 4.7): `EepromLog` liest die Zeiten, die `EepromGuard` je Entity mitschreibt
  (Schlüssel `times`, 26 h; Tageszähler wie bisher).
- Hält das Budget einen Wert zurück: `guard.pending` mit `held` `budget`, `due_at` leer; ein normaler Wert
  wartet. Ein Schutzschritt, den das Budget zurückhält (Notkontingent erschöpft oder 6/h), setzt statt
  dessen die Raumthermostat-Sperre (Grund `budget`, Ereignis `block_on`, zählt nicht), sofern die Rolle da
  ist, keine fremde Sperre gilt und die Mindestdauer seit dem letzten Wechsel um ist; aufgehoben wird sie
  nach der Mindestdauer, sobald ein Schutzschritt wieder geschrieben werden dürfte (dann gelten wieder die
  Regeln 8.4). Geht auch die Sperre nicht, bleibt der Schutzschritt ausstehend und das Journal warnt einmal
  je Episode.
- Journal: jede Art der Erschöpfung (`day`, `emergency`, `hour`) einmal je lokalem Tag, im Schatten mit
  „Schatten:“. Status `guard.budget` (`per_day`, `emergency_per_day`, `per_hour`, `used_today`, `used_hour`,
  `normal_left`, `emergency_left`, `hour_left`, `normal`/`protect` = was gerade zurückhält, `day_start`,
  `local_day`, `virtual`, `seeded`); UI-Kachel „Budget heute (virtuell)“. `shadow_writes` zählt damit, was
  mit Mindestabstand und Budget geschrieben würde; `budget` dort mit den Startwerten, wenn das Profil keine
  nennt. Tests `tests/test_heat_pump_budget.py`.

Umsetzung fremde Schreiber (3, Teil 4.4, `heat_pump/foreign.py`, in der Schutzschicht je Takt; noch nichts an
die Wärmepumpe):
- Sunflow merkt sich den zuletzt gesehenen echten Max T-Vorlauf (`guard.foreign.seen_c` in
  `heat_pump_state.json`, übersteht einen Neustart: eine Änderung, während Sunflow nicht lief, zählt auch).
  Der erste Wert überhaupt wird still übernommen; eine Änderung unter einem halben `step` ist keine,
  ein unbekannter Wert wird übersprungen (der letzte bekannte bleibt).
- Eigen ist eine Änderung nur auf den Wert, den Sunflow **echt** geschrieben hat (ab 4.7), innerhalb von
  10 min (`OWN_WRITE_CONFIRM_S`) nach diesem Schreibvorgang; im Schatten ist jede echte Änderung fremd.
- Fremde Änderung: Ereignis `foreign_write` in `heat_pump_samples`; der geschriebene (im Schatten
  virtuelle) Wert folgt dem echten, und der Mindestabstand läuft ab der Änderung (`written_at`), damit
  Sunflow nicht sofort dagegen schreibt. Sie zählt nicht ins Budget.
- Episode: Die erste fremde Änderung schreibt eine Warnung ins Journal („Max T-Vorlauf von außen geändert:
  … → … °C …“, mit Hinweis, wenn die ESP-Regelung aktiv ist); weitere werden gezählt, nicht einzeln
  gemeldet. Die Episode endet 24 h nach der letzten fremden Änderung (`FOREIGN_EPISODE_S`, Journal-Eintrag);
  bis dahin nennt die Einrichtungs-Zusammenfassung die Wärmepumpe (Warnung, Zahl und „zuletzt vor …“).
- ESP-Regelung: optionale Profilrolle `regulated_setpoint` (Schlüssel `supply_setpoint_regulated`, Profil
  Version 3, nur lesen, `missing_ok`: nicht eingerichtet ist kein Fehlen). Wert > 0 → aktiv: Grund
  `esp_regulation` in `inactive` (nichts schreiben, keine Übernahme), Journal (Warnung) beim Beginn, Eintrag
  beim Ende, Zusammenfassung, solange aktiv; 0 oder unbekannt → nicht aktiv (wie die Komponente selbst).
  Ihre Schreibvorgänge auf Max T-Vorlauf sind fremde Änderungen mit `regulation: true`.
- Status `guard.foreign` (`seen_c`, laufende `episode` mit `since`, `last_at`, `count`, `from_c`,
  `value_c`; die letzten 20 fremden Änderungen `recent`; `regulation` mit `active`, `value_c`, `since`;
  `own_pending`); `shadow_writes` zählt je Tag `foreign_writes` (nicht im Budget). Keine eigene UI-Anzeige
  außer dem Grund „ESP-Regelung aktiv“; Tests `tests/test_heat_pump_foreign.py`.

### 8.6 Ende der Steuerung und Ausfall

Sunflow stellt nichts zurück – weder Max T-Vorlauf noch die Raumthermostat-Sperre. Die zuletzt
geschriebenen Werte bleiben stehen. Läuft Sunflow (wieder), berechnet es aus dem Gelernten, ob und mit
welchem Vorlauf die WP laufen soll, und ändert die Werte, falls sie nicht passen. Ein Originalwert wird
nicht gemerkt; der Watchdog der Integration greift hier nicht ein.

Bewusst in Kauf genommen: Ist die Sperre `on`, wenn Sunflow ausfällt oder nicht mehr regelt (Absturz,
HA aus, Steuerung abgeschaltet, Gerät inkompatibel), heizt die HPSU nicht, bis Sunflow wieder regelt oder
der Nutzer die Sperre selbst aufhebt. Sunflow zeigt deshalb in der UI und im Journal an, wenn die Sperre
aktiv ist.

Eine Sperre, die Sunflow nicht selbst gesetzt hat, hebt es nie auf; es zeigt sie nur an (Status, UI,
Journal) und regelt erst wieder, wenn sie aufgehoben ist (8.4, „Fremde Sperre“).

---

## 9. Lernen

### 9.1 Datensammlung

- Minütlich messen, als 5-min-Mittel speichern: Tv, Tr, TvBH, Wärmeleistung, Raum-Ist je Raum, außen,
  Programm, Aktivität, BPV, Raumthermostat-Sperre, Kompressor, wirksamer Soll, Stellwert, Strategie,
  Ereignis-Flags
  (Lüften, Warmwasser-Nachlauf, Abtau-Nachlauf).
- Gültig für die Kennlinie: gültiger Heizbetrieb (Abschnitt 4, schließt den Nachlauf ein), Kompressor
  läuft ≥ 15 min, Raum-Ist verfügbar, Wärmeleistung plausibel.
- Umgesetzt (2c, Teil 2a): `heat_pump/samples.py` (`HeatPumpRecorder`) misst im Takt des Runners jede
  Minute und speichert je 5 min eine Zeile (Abschnitt 10). `tv_max` und Kompressorstarts/-stopps kommen
  über `state_changed`-Ereignisse, damit zwischen zwei Messungen nichts verloren geht. Lüften (6.2):
  `flags.ventilation` (umgesetzt in 3, Teil 1).

**Übersicht – was gelernt wird:**

| Was | Abschnitt | gemeinsam / je Strategie |
|---|---|---|
| Kennlinie (K, n, Spreizung) | 8.1, 9.2 | gemeinsam |
| Hausmodell | 9.3 | gemeinsam |
| Versatz je Raum | 6.1 | gemeinsam |
| Sprung- und Schwankungswerte (`steady_swing`, `start_jump`, `start_settle`) | 8.4 | gemeinsam |
| Leistungsbereich der WP | 8.3 | gemeinsam |
| Prognose-Korrektur (Prognose ↔ Messung) | 7 | gemeinsam |
| Korrektur (Integralanteil) | 8.2 | je Strategie |

### 9.2 Kennlinie

- Fit von `K` (und Spreizungsmodell) auf gültigen Intervallen: gewichtete Ridge-Regression mit
  Vergessensfaktor (Halbwertszeit 14 Tage, wie Hausmodell); optional `n` mit
  `scipy.optimize.least_squares`.
- Neuberechnung stündlich aus der Datenbank; Parameter mit Gütemaß (Fehler auf zurückgehaltenen Daten).
- Nutzung erst, wenn Gütemaß besser als „Vorlauf bleibt“ und Parameter plausibel.
- Gemeinsam für alle Strategien (Physik der Heizflächen).

Umsetzung (3, Teil 2, `heat_pump/curve.py`, Konstanten `CURVE_*` in `heat_pump/defaults.py`):
- Intervalle aus den letzten 28 Tagen: `flags.valid`, kein `after_run`, kein `ventilation`, ≥ 4
  Minutenwerte, Kompressor im Intervall und 15 min davor durchgehend an (drei vorige Zeilen mit
  `compressor` ≥ 0,99), Raum-Ist aus `rooms` mit den Gewichten der konfigurierten Räume, Wärme 200 W bis
  30 kW, mittlere Wassertemperatur ≥ 0,5 K über Raum-Ist. Alles andere fällt heraus, zählt nicht als 0.
- `n` bleibt der Startwert des Heizflächentyps (nicht gelernt). `K` aus `Q = K·x`, `x = (Tw − T)^n`;
  Spreizung `s = s0 + s1·Q` (Q in kW, Konstante unbestraft); Ridge 0,001 relativ zur Spalte.
- Gütemaß: Fit ohne die letzten 2 Tage; auf diesen sagt die Kennlinie Tv aus gemessener Wärme und
  Raum-Ist voraus (RMSE); „Vorlauf bleibt“ = Tv des ersten Intervalls je Tag.
- Zustand: `learning` (weniger als 144 Lern-Intervalle, 3 Lern-Tage oder 24 Prüf-Intervalle),
  `implausible` (bei der mittleren Wärme Übertemperatur außerhalb 1–40 K oder Spreizung außerhalb
  0–15 K), `unsure` (nicht besser als „Vorlauf bleibt“), `ready` (nutzbar).
- Neu gerechnet stündlich und sofort bei geändertem Heizflächentyp oder geänderten Räumen/Gewichten; Ergebnis
  nur als Cache in `heat_pump_state.json` (`devices.<id>.curve`, Feld `format`; unlesbar oder anderes
  Format → neu rechnen).
- Anzeige (gekennzeichnet als Schatten) im UI und in den Attributen der climate-Entity „Haus“
  (`heating_curve`, `flow_source`): K, n, Spreizung, Güte, Datenumfang; Status
  `GET /api/v1/heat_pump/status` (`state.flow.curve`, `source`, `source_reason`, `curve_c`,
  `heat_needed_w`).

### 9.3 Hausmodell

- Bestehendes 1R1C-Modell (`planning/house_model.py`) bleibt; Wärme aus den 5-min-Daten statt nur aus
  Stundenmitteln, Lüft-Intervalle ausgeschlossen.
- Gemeinsam für alle Strategien.

**Wärmequelle** (für Hausmodell, Kennlinie und `heat_w` in `heat_pump_samples`), beides unterstützt:
- **Eigener Wärmemengenzähler** (`HouseSettings.heat_entity`, HA-Sensor in W): ist er gesetzt, gilt er.
- **Sonst die Wärmepumpe** über die Rolle `heat_power` (bei der HPSU `thermal_power`).
- Fehlt beides, wird die Wärme wie bisher aus dem Stromverbrauch geschätzt (nur Hausmodell; die Kennlinie
  braucht eine gemessene Wärme).
- Welche Quelle gilt, zeigt die UI an.
- Rollen der Wärmepumpe werden über das Manifest gebunden (Nutzer-Overrides gehen vor); der Recorder
  liefert die Leistung in W (Statistik umgerechnet, Zustände normalisiert), auch bei einem Sensor in kW.
- Warmwasser- und Abtau-Zeiten werden beim Lernen ausgeschlossen: jede Stunde mit einem Anteil > 0 an
  `hot_water` oder `defrost` (aus der HA-History der Rolle `activity`) fällt heraus, für beide
  Wärmequellen (ein eigener Zähler misst das Warmwasser meist mit). Ohne Wärmepumpen-Gerät ist keine
  Aktivität bekannt, dann wird nichts ausgelassen; Stunden vor Beginn der History bleiben drin. Quelle
  sind die Stundenmittel, bis `heat_pump_samples` mindestens `MIN_DAYS` (7 Tage) gültigen Heizbetrieb
  abdeckt (umgesetzt in 3, Teil 2, unten).

Umstellung auf `heat_pump_samples` (3, Teil 2, `planning/house_model.py`, `learn_house`):
- Abdeckung: Tage (UTC) der ganzen Tabelle mit mindestens 1 h gültigem Heizbetrieb ohne Lüften
  (`HOUSE_SAMPLES_DAY_MIN_S`). Ab 7 Tagen werden die Stunden aus den 5-min-Zeilen der letzten 28 Tage
  gebildet: mindestens 10 von 12 Zeilen; Raum-Ist = Wert der Zeile zu Beginn der Stunde (das Modell
  rechnet von Stundenbeginn zu Stundenbeginn), Außentemperatur und Wärme als Mittel. Eine Stunde fällt
  heraus bei Warmwasser, Abtauen, Nachlauf, Lüften, Störung, unbekannter Aktivität oder Heizen ohne
  gültigen Heizbetrieb; Pausen und Sommer/Bereitschaft (Wärme 0) bleiben drin. Sonne weiter aus HA: je
  Stunde das Stundenmittel des Helligkeitssensors genau dieser Stunde (nie der aktuelle Wert), aus allen
  Stunden seiner Statistik – auch für Stunden, für die HA sonst keine Zeile hat (korrigiert in 3, Teil 3).
- Ohne Sprung in der Anzeige: umgestellt wird erst, wenn das Modell aus den Messwerten zustande kommt
  und nicht schlechter ist (nutzbar, oder das aus HA ist es auch nicht); danach bleibt es dabei
  (`house.model_source` in `heat_pump_state.json`). Kommt aus den Messwerten kein Modell zustande (z. B.
  lange Lücke), gilt wieder HA statt einer leeren Anzeige.
- Das UI zeigt unter „Gelernt“ die Quelle (Stundenmittel aus HA bzw. Messwerte der Wärmepumpe) und die
  Abdeckung; `GET /api/v1/house/model` liefert `source` und `coverage_days`.

**Außentemperatur** (Hausmodell und `outside_c`), Einstellung `house.outside_source`: `auto` (eigener
Sensor aus den Lernquellen, falls gesetzt, sonst Rolle `outside_temperature` der Wärmepumpe),
`heat_pump` (Rolle `outside_temperature`), `heat_pump_unit` (Rolle `outside_temperature_unit`, bei der
HPSU `ta2`, Fühler des Außengeräts), `learning_source` (nur der eigene Sensor).

### 9.4 Je Strategie

Integralanteil der Korrektur, letzte Schreibvorgänge, Start-/Halte-Zustand (8.4); S2 später zusätzlich die
Abbildung Kurvenparameter → wirksamer Soll.

---

## 10. Speicherung

| Was | Wo | Format |
|---|---|---|
| 5-min-Messdaten (1 Jahr) | Tabelle `heat_pump_samples` in `shadow.db` | SQLite, neuer Schritt in `SCHEMA_STEPS` |
| Gelerntes (Kennlinie, Versatz je Raum, Sprung-/Schwankungswerte, Leistungsbereich, Prognose-Korrektur) | aus der Tabelle berechnet, Cache in `heat_pump_state.json` | JSON |
| Strategie-Zustand je Gerät und Strategie (`fixed`, `weather`) | `heat_pump_state.json` | JSON, atomar (tmp + replace) |
| Schreibzähler | `eeprom_writes.json` (vorhanden; Tageszähler, Zeiten der letzten 26 h je Entity) | JSON |
| Raum-Soll, Spielraum, Räume, Heizflächentyp, Grenzen, Wetter-Entity | `config.json` (vorhanden, Migration) | JSON |
| Gerätezuordnung (`ha_device_id`, MAC, Komponente, Paarung CAN ↔ UART) | `config.json` (`DeviceConfig`) | JSON |

Warum `shadow.db` statt eigener Datei: Export/Import (`api/system.py`), MCP-SQL (`query_shadow_db`) und
Migration (`user_version`, `SCHEMA_STEPS`) gibt es dort schon; Abfragen gegen Preis/PV/Plan aus `slots`
gehen per JOIN. Eigene Tabelle statt Spalten in `slots`, weil Takt (5 statt 15 min) und Zweck anders sind.
Regeln für Schemaänderungen: `datenhaltung.md`. Alle Tabellen der Wärmepumpen-Regelung beginnen mit
`heat_pump_` (Regel 12); heute ist das nur `heat_pump_samples`.

- `heat_pump_samples` (umgesetzt, Schema-Schritt 9 → 10, Index auf `start`): Schlüssel (`device_id`,
  `start` = Beginn des 5-min-Intervalls, UTC). Spalten:
  - `samples` (Anzahl Minutenwerte);
  - Mittelwerte mit Einheit im Namen: `tv_c`, `tr_c`, `tvbh_c`, `setpoint_c` (wirksamer Soll),
    `max_setpoint_c` (aktueller Max T-Vorlauf), `bypass_pct`, `heat_w`, `outside_c`;
  - `tv_max_c`: höchste Spitze im Intervall (für die Sprung-Lernwerte);
  - `written_c`: von Sunflow geschriebener Stellwert (Mittel der Minuten mit aktivem Schreiben, seit 3, Teil
    4.7; im Schatten leer);
  - `heat_source` (`meter`/`measured`); `compressor`: Anteil der Minuten mit laufendem Kompressor (0–1);
  - Zustände als kanonische Schlüssel (`program`, `activity`, `strategy`; häufigster Wert im Intervall);
    `error`: Schlüssel des Fehlercodes (z. B. `err_0`) oder unbekannter Text;
  - JSON: `rooms` (entity → °C), `events` (Kompressorstart/-stopp mit Zeit, Tv, `tv_max`, wirksamem Soll
    und Max T-Vorlauf), `flags` (`valid` nur, wenn jede Minute gültig war; `frozen`: Gründe;
    `after_run`: Nachlauf `hot_water`/`defrost`/`unknown`; `blocked`, `fault`, `self_limited`;
    `ventilation`: in einer Minute des Intervalls gelüftete Räume, sortierte Liste der Entities, leer
    ohne Lüften; fehlt in älteren Zeilen → leer). In `events` zusätzlich der Quellenwechsel des
    Vorlauf-Ziels (8.1): `{"at", "kind": "flow_source", "from", "to"}` (ab 3, Teil 2; ab 3, Teil 4.5 dazu
    `shadow`, `jump_k` = neuer − alter Startwert, wenn der alte bekannt ist, und bei aktivem Schreiben
    `protected`, `shift_k`, `rest_k`). Ab 3, Teil 3 (8.4,
    im Schatten mit `shadow: true`): geplante Schreibvorgänge `{"at", "kind": "write", "reason"
    (normal/start/track/block_on/block_off), "value_c", "from_c" bzw. "block", "block_reason", "phase"}`,
    Starts `{"at", "kind": "start_jump", "start_kind", "start_at", "ref_c", "peak_c", "jump_k", "settle_s"}`
    (Grundlage des Lernens) und ungewollte Abschaltungen `{"at", "kind": "unwanted_stop", "tv_c", "peak_c",
    "setpoint_c", "max_setpoint_c", "during", "raised", "value"}`. `written_c` bleibt leer, solange nichts
    geschrieben wird (Schatten). Ab 3, Teil 4.2 laufen die Schreibvorgänge des Stellwerts durch die Schreibschicht
    (8.5, Mindestabstand); ein Schreibvorgang über „jetzt schreiben“ trägt zusätzlich `"manual": true`.
    Ab 3, Teil 4.4 fremde Änderungen von Max T-Vorlauf `{"at", "kind": "foreign_write", "from_c",
    "value_c", "written_c" (was Sunflow bis dahin als geschrieben hatte), "regulation", "activity", "shadow"}`.
- Aufräumen: Zeilen älter als 1 Jahr löschen (im Runner einmal täglich, Blöcke zu 5000 Zeilen).
- Lernen zurücksetzen: Zeilen des Geräts löschen und `heat_pump_state.json`-Eintrag verwerfen.
- Export/Import: `heat_pump_samples` wird beim Import übernommen (in `IMPORT_TABLES` eintragen); der
  Import führt alle Tabellen zusammen, siehe `datenhaltung.md` („Export/Import“).
- `heat_pump_state.json` (klein, verwerfbar; umgesetzt in 2c, Teil 2b, `heat_pump/state_store.py`):
  `house` (zuletzt gesehener Raum-Soll, laufende Soll-Änderung `settling`, Lüften `ventilation` mit
  `active` und `recent`, 6.2, Quelle des Hausmodells `model_source`, 9.3) und je Gerät `base_c`,
  `base_at`, `corrections` je Strategie, `source` (zuletzt genutzte Quelle des Vorlauf-Ziels, 8.1),
  `curve` (Cache der Kennlinie, 9.2), `learned` (Cache der gelernten Schwankungs-, Sprung- und
  Leistungswerte, eigenes `format`) und `guard` (Schutzschicht 8.4: zuletzt geschrieben – im Schatten
  virtuell –, Sperre mit Zeit und Grund, Tv vor Warmwasser/Abtauen, laufender Start, Erhöhungen nach
  ungewollten Abschaltungen, die letzten geplanten Schreibvorgänge und Abschaltungen, fremde Schreiber
  `foreign`: zuletzt gesehener Max T-Vorlauf, erwarteter eigener Wert, Episode, ESP-Regelung, letzte fremde
  Änderungen); unbekannte Schlüssel werden ignoriert, geschrieben nur bei
  Änderung, atomar. Im Export enthalten, beim Import nicht übernommen.
- `config.json`: `house.heating_surface` (Heizflächentyp, 8.1; additiv ohne Migration, Standard
  `underfloor`).

---

## 11. Geräteprofil Rotex HPSU (umgesetzt, `profiles/heat_pumps/esphome/rotex_hpsu_can.yaml`)

Sunflow beschreibt Geräte über Profile (`profiles/`, Schema `schema/profile.schema.json`): Erkennung,
Rollen (welche Entity welchen Wert liefert), Steuerung und Grenzen. Für die HPSU kamen ins Schema:
`match.manifest`, das Rollenfeld `domain`, sowie `companion`, `states` und
`flow_control`. Was noch niemand auswertet (`companion`, `states`, `flow_control` außer für die
Kompatibilitätsprüfung), wird nur gegen das Schema geprüft und darf in späteren Schritten angepasst werden
(keine Datenmigration; ändert sich das Profil, steigt `version`). Die Fähigkeit `flow_control` kommt erst
in Schritt 3 hinzu. Weitere Wärmepumpen bekommen nur ein eigenes Profil dieser Art.

```yaml
profile: esphome/rotex_hpsu_can
version: 3                  # 2: optionale Rolle outside_temperature_unit (ta2); 3: regulated_setpoint
match:
  integration: esphome
  manifest: {id: rotex_hpsu_can}
capabilities: [heat_pump]

roles:                      # domain: erlaubte Entity-Domains laut Manifest
  flow_temperature:     {key: tv, domain: [sensor], unit: "°C"}
  flow_temperature_bh:  {key: tvbh, domain: [sensor], unit: "°C"}
  return_temperature:   {key: tr, domain: [sensor], unit: "°C"}
  flow_setpoint:        {key: target_supply_temperature, domain: [sensor], unit: "°C"}
  heat_power:           {key: thermal_power, domain: [sensor], unit: kW}
  flow_temperature_max: {key: tv_max, domain: [sensor], unit: "°C"}   # Spitze zwischen zwei tv-Werten
  outside_temperature:  {key: temperature_outside, domain: [sensor], unit: "°C"}
  outside_temperature_unit: {key: ta2, domain: [sensor], unit: "°C", optional: true}   # Fühler Außengerät
  compressor:           {key: status_kompressor, domain: [binary_sensor]}
  program:              {key: operating_mode, domain: [select, text_sensor]}
  activity:             {key: mode_of_operating, domain: [text_sensor]}
  strategy_select:      {key: hk_function, domain: [select, text_sensor]}
  bypass_valve:         {key: bypass_valve, domain: [sensor], unit: "%"}
  heating_block:        {key: room_therm, domain: [select, switch]}   # on = gesperrt, off = frei
  error:                {key: error_code, domain: [text_sensor, sensor]}
  max_flow_setpoint:    {key: max_target_flow_temp, domain: [number], unit: "°C"}   # Max T-Vorlauf, beschrieben
  regulated_setpoint:   {key: supply_setpoint_regulated, domain: [number], unit: "°C", optional: true,
                         missing_ok: true}   # ESP-Regelung, nur gelesen (> 0: Sunflow schreibt nicht, 8.5)
  outdoor_unit:         {key: outdoor_unit, domain: [select, text_sensor], optional: true}
  electric_energy:      {key: total_electrical_energy, domain: [sensor], unit: kWh, optional: true}

companion:
  profile: esphome/rotex_hpsu_uart
  roles:
    compressor_current: {key: inv_prim_current, domain: [sensor], unit: A, optional: true}
    compressor_speed:   {key: inv_freq, domain: [sensor], unit: rpm, optional: true}

states:
  program:  {heating: [heating, lowering, automatic_1, automatic_2], summer: [summer],
             standby: [standby], cooling: [cooling]}
  activity: {heating: [heating], hot_water: [hot_water_production],
             defrost: [defrosting], idle: [standby], cooling: [cooling]}

flow_control:
  strategies:
    fixed:   {when: {strategy_select: fixed}, setpoint_kind: direct,
              write: {role: max_flow_setpoint, storage: eeprom}}
    weather: {when: {strategy_select: weather_dependent}, setpoint_kind: curve, enabled: false}
  compressor_rules: {start_margin: 3.0, stop_margin: 3.0}
  learned_defaults: {steady_swing: 0.5, start_jump: 6.0, start_settle: 5min}
  adopt_delay: 60s
  limits: {min: 25, max: 40}                  # Startwerte Fußboden, Nutzer stellt ein
  heat_power: {min: 0.6, max: 6.0}            # kW, Startwerte; später gelernt
  settle_after_bypass: 10min                  # BPV muss so lange 0 % sein, sonst Übergang
  heating_block: {"on": "on", "off": "off", min_duration: 20min}   # in YAML quoten (sonst true/false)
  writes: {min_interval: 5min, protect_interval: 2min,   # Totzone: DEADBAND_K (0,5 K), im Profil überschreibbar
           per_day: 48, emergency_per_day: 24, per_hour: 6}
  frozen_after: {hot_water: 10min, defrost: 10min}
```

`flow_control`:
- `strategies`: je Strategie die Bedingung `when` (Rolle → Auswahlwert, hier `strategy_select`),
  `setpoint_kind` (`direct`/`curve`), optional `enabled` und `write: {role, storage}`. Geschrieben wird
  über eine **Rolle**, nicht über einen Schlüssel: Die Rolle (`max_flow_setpoint`) muss in `roles`
  stehen und wird in der Kompatibilitätsprüfung als beschreibbar geprüft (Domain `number`, Einheit °C,
  `min`/`max` gegen `limits`, `step` gegen `MAX_SETPOINT_STEP_K`, 3.6).
- `limits` (Stellwertbereich, Startwerte), `heat_power`, `compressor_rules`, `learned_defaults`,
  `adopt_delay`, `settle_after_bypass`, `frozen_after`: Parameter für Schritt 2c, Teil 2, und 3.
- `heating_block`: Auswahlwerte für gesperrt/frei der Rolle `heating_block`, Mindestdauer.
- `writes`: Mindestabstände und Budget (8.5); `deadband` nur, wenn ein Profil von `DEADBAND_K` abweicht.
Beim Laden prüft Sunflow, dass `states` und `flow_control` nur Rollen des Profils nennen.
Rollenfeld `missing_ok` (Schema, seit 3, Teil 4.4): bei einer optionalen Rolle, deren Fehlen der Normalfall
ist (die ESP-Regelung), meldet die Kompatibilitätsprüfung einen fehlenden Schlüssel nicht; passt der
vorhandene Schlüssel nicht (Domain, Einheit), bleibt es ein optionales Problem.

---

## 12. Änderungen an den ESPHome-Komponenten

Beide Komponenten (CAN, UART) gleich aufgebaut:
- `manifest.py`: baut das Manifest beim Kompilieren, teilt es in Seiten, prüft ESPHome-Version und
  `api:`, setzt die nötigen Defines.
- `manifest_action.h`: die Native-API-Aktion `rotex_<can|uart>_manifest` (Argument `page`).
- `__init__.py`: `json` in `AUTO_LOAD`, Manifest-Eintrag beim Anlegen jeder Entity,
  `register_manifest()` am Ende von `to_code`.
- Komponente: `set_manifest_action_name()`, `add_manifest_page()`, Registrierung in `setup()`.
- `Utils::entity_name()` statt des ab ESPHome 2026.11 veralteten `EntityBase::get_name()`
  (Versionsweiche über `ESPHOME_VERSION_CODE`).

Nur CAN:
- Sensor `tv_max` (Konfig `entities: tv_max:`, braucht `tv`): Tv wird dann alle 2 s gelesen, `tv` und
  `tv_max` gemeinsam im Intervall von `tv` veröffentlicht (8.4); in den Beispiel-YAMLs ergänzt.
- Max T-Vorlauf und Min T-Vorlauf mit Schrittweite 0,1 °C (Register in Zehntel); beim Schreiben wird der
  skalierte Wert gerundet statt abgeschnitten (vorher z. B. 27,3 → 27,2).
- `supply_setpoint_regulated` bleibt als Option (Sunflow übernimmt nicht, solange sie aktiv ist; gelesen über
  die Rolle `regulated_setpoint`, 8.5).

## 13. HA-Seite und UI

- `sunflow_ems`:
  - `climate`-Entity „Haus“: Raum-Soll (einstellbar), Raum-Ist, Modus; Attribute Planziel und Grund
    (z. B. „Vorheizen wegen PV“), Vorlauf-Ziel, Stellwert geschrieben/ausstehend; seit 3, Teil 2 auch
    Quelle des Vorlauf-Ziels (`flow_source`, `flow_source_reason`, `flow_curve_temperature`,
    `flow_heat_needed`) und die gelernte Kennlinie (`heating_curve`, mit `shadow: true`); seit 3, Teil 4.6
    Stellwert der ersten Wärmepumpe (`setpoint_written`, `setpoint_written_at`, `setpoint_pending`,
    `setpoint_pending_at`, `setpoint_pending_reason`, `setpoint_held`) und das Kennzeichen
    `mode` (`shadow` | `active`, nur Anzeige; der Schalter ist in der Sunflow-UI, seit 4.7). Bei mehreren Wärmepumpen
    unter `heat_pumps` je Wärmepumpe dieselben Namen ohne Präfix (`written`, …, `held`, `mode`). Das ältere
    Attribut `shadow` (true/false) bleibt vorerst, wird aus derselben Quelle gesetzt (`shadow` = `mode` ist
    `shadow`) und gilt als veraltet.
  - Sensoren je Wärmepumpe am Gerät „Sunflow“, Name mit dem Namen der Wärmepumpe vorne, `unique_id` aus
    der Sunflow-Geräte-ID (Umbenennen ändert weder Entity-ID noch History); wird die Wärmepumpe in Sunflow
    entfernt, verschwinden ihre Sensoren (auch aus der Entity-Registry). Ohne bekannten Wert `unknown`,
    nie 0. An allen dreien das Attribut `mode` (`shadow` | `active`, kein zusätzliches `shadow`), Symbol im
    Schatten `mdi:ghost-outline`, aktiv `mdi:thermometer-water` bzw. `mdi:counter`; beim Aktivschalten
    ändern sich nur Attribut und Symbol.
    - „Vorlauf-Ziel“ (`<name> flow target`): V_Ziel in °C, `measurement`, 1 Nachkommastelle.
    - „Stellwert“ (`<name> setpoint`): zuletzt geschriebener Max T-Vorlauf (im Schatten der virtuelle) in
      °C, `measurement`, 1 Nachkommastelle; eine fremde Änderung übernimmt er wie der Stellwert selbst.
      Attribute `pending`, `pending_at` (fällig um), `pending_reason`, `written_at`, `written_reason`
      (`normal`, `start`, `track`, `manual`, `foreign`; leer, solange es der eigene Wert der Wärmepumpe
      ist), `held` (`spacing`, `budget`, `block`, `esp_regulation`; sonst leer), `mode`.
    - „Schreibvorgänge heute“ (`<name> writes today`): Schreibvorgänge auf den Stellwert am lokalen Tag
      (Budget, 8.5; Sperre zählt nicht), im Schatten virtuell; ohne Einheit und ohne `state_class` (nur
      Verlauf, keine Langzeitstatistik; Auswertungen über längere Zeit aus `heat_pump_samples`). Nach einem
      Neustart `unknown`, bis die Schreibvorgänge aus den Ereignissen neu gezählt sind und die Zeitzone
      von HA bekannt ist.
- Sunflow-UI, Bereich Wärmepumpe:
  - Gerät: CAN, ggf. UART-Begleiter, Paarung (Name, Bereich, MAC); erkannte Strategie (aus
    `hk_function`); Programm und Aktivität; Kompatibilität (fehlende Rollen mit Grund, 3.6).
  - Einstellungen: Raum-Soll, Spielraum +x/−y, Heizflächentyp, Grenzen, Leistungsbereich, Budget,
    Mindestabstand, Wetter-Entity.
  - Räume mit Gewicht, Lüft-Ereignisse, gelernter Versatz je Raum.
  - Gelerntes: Kennlinie (K, n, Spreizung, Güte), Hausmodell, Korrektur je Strategie, Sprung- und
    Schwankungswerte (`steady_swing`, `start_jump`, `start_settle`), Prognose-Korrektur.
  - Live: Tv, `tv_max`, Tr, BPV, wirksamer Soll, Vorlauf-Ziel, Stellwert geschrieben/ausstehend,
    Raumthermostat-Sperre (deutlich sichtbar, wenn aktiv; „nicht von Sunflow gesetzt“, wenn fremd),
    „jetzt schreiben“. Seit 3, Teil 3 im Schatten:
    Stellwert und Sperre „würde schreiben“, Schutz-Untergrenze und Phase, Grenzen, gelernte Werte mit
    Herkunft, letzte geplante Schreibvorgänge und ungewollte Abschaltungen, Tabelle der
    Schreibvorgänge je Tag (normal, Start, Nachführen, Sperre an/aus, ungewollte Abschaltungen). Seit 3,
    Teil 4.2: ausstehender Wert mit Zeit und Grund, Mindestabstand, Taste „jetzt schreiben“ (nur bei aktivem
    Schreiben) mit „wird geschrieben …“ und dem Ergebnis, wenn er verfallen ist.
  - Journal: Schreibvorgänge mit Grund, ungewollte Abschaltungen, fremde Schreiber, Sperre an/aus.
- Schattenbetrieb: alles wird berechnet und angezeigt, nichts geschrieben.
- **Modus (seit 0.80.22, Nutzer-Entscheidung):** eine Auswahl oben beim Raum-Soll statt Modus Heizen/Aus
  und eigenem Schalter „Aktives Schreiben“ je Wärmepumpe. Für Nutzer heißt der Schattenbetrieb „Nur
  Lernen“; intern (Code, gespeicherte Daten, HA-Attribut `mode` = `shadow`/`active`) bleiben die Namen.
  | Auswahl | gespeichert | zeichnet auf (`heat_pump_samples`) | lernt | regelt |
  |---|---|---|---|---|
  | Aus | `climate.mode` = `off` | nein (Zeilen verworfen) | nein (Korrektur eingefroren `mode_off`, Kennlinie/gelernte Werte nicht neu, Hausmodell bleibt, keine Erhöhung nach Abschaltung) | nein |
  | Nur Lernen | `heat`, `flow_active` aus | ja | ja | nein, zeigt „würde schreiben“ |
  | Lernen + Regeln | `heat`, `flow_active` an (alle Wärmepumpen, die es dürfen) | ja | ja | ja |
  Reihenfolge beim Umschalten: erst `flow_active` je Wärmepumpe, dann `climate.mode`. Lernen + Regeln nur
  in Sunflow mit Bestätigung und den Voraussetzungen von 4.7; im HA-Thermostat schaltet Aus `climate.mode`
  auf `off` (Schalter bleibt), Heizen zurück auf die zuletzt gültige Stufe. Nach Aus übernimmt Sunflow wie
  beim Einschalten (4.7). Das Hausmodell wird nach einem Neustart bei Aus einmal gelernt (es liegt nur im
  Speicher), danach nicht aufgefrischt.

---

## 14. Tests und Inbetriebnahme

**Simulation (pytest, ohne `sleep`, Zeit injiziert):**
- Haus 1R1C + Estrich-Trägheit, Fußbodenheizung mit `Q = K·(Tw − T)^n`.
- HPSU-Nachbildung: übernimmt Max T-Vorlauf nach 30–60 s, 3-K-Start/Stopp, Mindestleistung,
  Tv-Sprung nach dem Start (größer nach Warmwasser), Schwankung im Betrieb, Warmwasser-Phasen (Tv hoch,
  BPV 100 %), Abtauen, BPV-Übergänge, Raumthermostat-Sperre, `tv`/`tv_max` alle 30 s.
- Fälle: Soll-Sprünge (+1 K, −2 K), 10× Hin-und-her in 10 s und über 3 min, Lüften täglich, PV-Vorheizen,
  Wechsel `hk_function`, Betriebsmodus Sommer/Bereitschaft/Absenken, Sensorausfall, Budget erschöpft,
  fehlende Wetter-Entity, ESP-Neustart mit geändertem Manifest (fehlende Rolle).
- Prüfungen: keine selbst verursachten Abschaltungen; Sprung-/Schwankungswerte werden gelernt und nach
  einer ungewollten Abschaltung erhöht; Budget eingehalten, höchstens zwei Schreibvorgänge je Start;
  Hin-und-her ≤ 3 Schreibvorgänge; Korrektur wird bei Lüften/Soll-Wechsel nicht hochgetrieben; Kennlinie
  konvergiert; Einfrieren bei Warmwasser/Abtauen/BPV > 0 %; stoßfreier Strategiewechsel; inkompatibles
  Gerät wird erkannt und nicht beschrieben.

**Unit-Tests:** Manifest-Auflösung (Seiten, Namen, Sprachen, Typwechsel number/select),
Kompatibilitätsprüfung (3.6), Erkennung und Paarung (mehrere ESPs, MAC), Zustandsabbildung (alle
Betriebsmodi und Aktivitäten), Schreiblogik (Vorderflanke, Zusammenfassen, Totzone, Mindestabstand,
Budget, Rundung auf `step`), Lüft-Erkennung mit 15-min-Sensoren, Prognose-Korrektur, Migration
`HouseSettings` und `shadow.db` (`heat_pump_samples`), Export/Import mit der neuen Tabelle.

**Inbetriebnahme:**
0. Andere Regler abschalten, die denselben Stellwert oder den Raumthermostat-Eingang schreiben
   (Integrationen, Automationen, Skripte, ESP-Regelung `supply_setpoint_regulated` auf 0). Nur eine
   Sunflow-Instanz (App) darf laufen. Sunflow meldet fremde Schreiber (8.5).
1. Firmware mit Manifest und `tv_max` auf CAN (und Manifest auf UART).
2. Sunflow: Gerät erkennen, Räume eintragen, **Schattenbetrieb 1–3 Tage** (Daten sammeln, Vergleich).
3. Aktiv mit S1, Budget eng beobachten, Journal prüfen.
4. Danach Analyse-Phase S2.

---

## 15. Offene Punkte

1. Manifest mit Seiten und `tv_max` auf echter Hardware testen (Manifest ohne Seiten bereits bestätigt).
   **Geklärt (Prüfung per MCP vor Teil 4):** Die CAN-Firmware liefert das Manifest in zwei Seiten; Sunflow
   setzt sie zusammen und meldet das Gerät in `GET /api/v1/heat_pump/status` als `compatible`, nutzbar,
   ohne fehlende Rollen (`problems` leer). `tv_max` arbeitet wie in 8.4 beschrieben: in der HA-History
   liegt es nie unter `tv`, kommt mit `tv` zusammen im Takt von `tv`, und bei Kompressorstarts (aus
   Bereitschaft und nach Warmwasser) sowie im Dauerlauf zeigt es Spitzen zwischen zwei `tv`-Werten, die in
   `tv` fehlen. Die gesehenen Startsprünge liegen deutlich unter dem Startwert von `start_jump`; der
   Startwert bleibt, bis Sunflow eigene Werte gelernt hat. Beobachtung: Bei schnell fallendem Tv ist `tv_max`
   oft gleich dem vorigen `tv` (die HPSU liefert den Tv-Wert offenbar seltener neu, als die Komponente
   liest); das liegt auf der sicheren Seite (Spitze eher zu hoch) und macht `steady_swing` im Dauerlauf
   kaum größer, keine Änderung nötig.
2. Sommer vs. Bereitschaft der HPSU im Detail (RoCon-Anleitung).
3. Startwerte Budget (48 + 24) nach den ersten Wochen prüfen. Seit 3, Teil 4.3 zählt
   `shadow_writes` mit Mindestabstand und Budget (virtuell, 8.5); damit lässt sich die Zahl schon im Schatten
   beurteilen – Tage mit Budget-Sperre oder vollem Notkontingent zeigen, dass die Werte zu knapp sind.
   Seit 3, Teil 4.6 zeigt der HA-Sensor „Schreibvorgänge heute“ den laufenden Tag (im Schatten virtuell);
   zur Beurteilung über Wochen weiter `shadow_writes` bzw. `heat_pump_samples`, nicht die HA-History.
4. Höchster Vorlauf für den Estrich (Nutzereinstellung, Standard?). **Geklärt, umgesetzt (0.80.11):**
   Einstellung min/max des Stellwerts je Wärmepumpe (8.3), leer = Startgrenzen des Heizflächentyps
   (`HEATING_SURFACES`), immer eingeengt auf `min`/`max` der Entity im Manifest; max ist zugleich der
   höchste Vorlauf für den Estrich (Standard Fußboden 40 °C). Offen: Bereich von Max T-Vorlauf, siehe
   Punkt 9.
5. Untergrenze je Raum ja/nein.
6. S2-Entwurf nach Analyse.
7. Raumthermostat-Eingang: liegt er in der HPSU im EEPROM? Davon hängt ab, ob die Sperre ins
   Schreibbudget zählt. **Geklärt:** nein (Auskunft des Nutzers, HPSU). Der Eingang „Raum Thermostat“
   (CAN-Komponente `room_therm`, Register `FA 06 78`) ist dafür gedacht, die HPSU von außen ein- bzw. bei
   zu warmem Haus auszuschalten. Die Sperre zählt nicht ins Schreibbudget (8.5); gegen Takten bleibt die
   Mindestdauer je Zustand (20 min).
8. Wirkt Max T-Vorlauf während Warmwasser auf den Heizbetrieb? (Annahme: nein; Vorbereitung vor dem
   Start nach Warmwasser hängt davon ab.) Beobachtung aus `heat_pump_samples`, solange noch andere Regler
   Max T-Vorlauf ändern: Warmwasser-Episoden (aufeinanderfolgende Zeilen mit `activity` = `hot_water`),
   darin `max_setpoint_c` mit dem Wert vor der Episode und innerhalb vergleichen; für Episoden mit
   Änderung prüfen, ob Warmwasser weiterläuft (`activity`, `compressor`), ob der wirksame Soll
   (`setpoint_c`) während Warmwasser mitgeht, und beim ersten Start danach (Ereignis `start` mit
   `max_setpoint_c`/`setpoint_c`) ob der geänderte Wert gilt; dazu ungewollte Abschaltungen
   (`unwanted_stop`) in den 40 min danach. Annahme bestätigt, wenn Warmwasser unbeeinflusst bleibt und der
   Start danach den während Warmwasser gesetzten Wert nutzt. Dasselbe geht ohne `heat_pump_samples` aus der
   HA-History (Betriebsart, Max T-Vorlauf, Vorlauf-Soll, Kompressor; etwa 10 Tage). **Stand: aus den Daten
   nicht beantwortbar, wird in Teil 4 im aktiven Betrieb beobachtet (Abschnitt 16, 3.4, Todo)** – in der ausgewerteten History wurde Max T-Vorlauf während Warmwasser nie geändert
   (die bisherigen Regler schreiben erst nach dem Ende). Nötig ist ein Test am Gerät (vom Nutzer): während
   einer Warmwasser-Bereitung Max T-Vorlauf einmal ändern, z. B. um 3 K über den Wert vorher, und prüfen,
   ob Warmwasser normal weiterläuft und ob der Kompressor beim Start danach mit dem neuen Wert läuft
   (wirksamer Vorlauf-Soll, keine Abschaltung kurz nach dem Start). Seit 3, Teil 4.4 steht jede fremde
   Änderung von Max T-Vorlauf als Ereignis `foreign_write` mit `activity` in `heat_pump_samples`; eine
   Änderung während Warmwasser (auch dieser Test) lässt sich damit direkt mit den Zeilen und dem Ereignis
   `start` danach auswerten.
9. **Bereich von Max T-Vorlauf gegen die eigene Grenze der HPSU** (für 4.7 entschieden, siehe
   „Entscheidung“ unten; Vorschlag und Test am Gerät offen). Das Manifest meldet für Max T-Vorlauf (`max_target_flow_temp`) 20–90 °C – den
   Registerbereich, fest beim Kompilieren. Die HPSU begrenzt den wirksamen Vorlauf-Soll laut Nutzer selbst
   niedriger (40 °C); dazu passt der HPSU-Parameter „Max Temp Heizung“ (`max_heating_temperature`, im
   Manifest number 5–85 °C, Schritt 1), eine Nutzereinstellung in der HPSU, kein fester Gerätewert.
   **Auswirkung:** 8.3 engt den Stellwertbereich nur auf `min`/`max` der Entity ein, also auf 90 °C.
   - Fußbodenheizung (Startgrenze 40 °C): fällt zufällig zusammen, solange `flow_max_c` nicht darüber
     gesetzt wird.
   - Heizkörper (55 °C) und gemischt (50 °C), ebenso jedes `flow_max_c` über der HPSU-Grenze: Sunflow hält
     Werte bis zur Obergrenze für wirksam, die HPSU fährt aber höchstens ihre Grenze. Folgen: (a) V_Ziel
     über der Grenze ändert nichts, die Korrektur läuft ohne Wirkung hoch, „Grenze erreicht“ (`limit_max`)
     greift nicht, weil die Obergrenze höher liegt; (b) Schutzschritte über der Grenze (Starten,
     Vorbereiten, Nachführen) sind wirkungslos, kosten aber Budget: Die Abschaltgrenze liegt am
     wirksamen Soll + 3 K, nicht am geschriebenen Wert – Sunflow hält sich für geschützt, die HPSU schaltet
     ab, und das Lernen erhöht die Sprungwerte für einen Fehler, der kein Sprung war; (c) die
     Plausibilität (Bezug wirksamer Soll) und „nicht schreiben, wenn schon gesetzt“ vergleichen mit einem
     Wert, den die HPSU nie übernimmt, sodass derselbe Wert wieder und wieder geschrieben würde.
   **Vorschlag (nicht umgesetzt):** Die Obergrenze des Stellwerts = Minimum aus Nutzer-/Startgrenze,
   Manifest-`max` und der eigenen Grenze der HPSU. Dazu (1) im Profil eine optionale Rolle für „Max Temp
   Heizung“ (z. B. `flow_setpoint_cap`, Schlüssel `max_heating_temperature`, nur lesen, nie schreiben),
   jeden Takt gelesen, damit eine Änderung in der HPSU sofort gilt; eingeengt wird abwärts gerundet auf
   `step`. Liegt ein Schutzwert über dieser Grenze, ist er nicht erreichbar: dann Sperre statt Schreiben
   (wie „Budget erschöpft“), Journal. (2) Als Absicherung für Geräte ohne diese Rolle eine
   Klemm-Erkennung: Bleibt im gültigen Heizbetrieb der wirksame Soll nach `adopt_delay` um mehr als einen
   Schritt unter dem geschriebenen Wert, gilt der wirksame Soll als Obergrenze (bis er wieder folgt),
   einmal Journal, Status `limited` = `device_cap`. (3) Das UI zeigt die HPSU-Grenze bei den Grenzen und
   warnt, wenn `flow_max_c` oder die Startgrenze des Heizflächentyps darüber liegt. Eine Änderung am
   Manifest (Abschnitt 12) hilft nicht: `min`/`max` dort sind fest beim Kompilieren, die HPSU-Grenze ist
   eine Einstellung. Vorher am Gerät bestätigen (Nutzer), dass „Max Temp Heizung“ der begrenzende Wert ist
   (z. B. Max T-Vorlauf kurz über die Grenze setzen und den wirksamen Vorlauf-Soll ansehen).
   **Befund (per MCP gelesen, vor 4.7):** „HK Funktion“ steht auf Fest; „T Vorlauf Tag“ (CAN-Schlüssel
   `flow_temperature_day`, Register `FA 01 29`) und „Max Temp Heizung“ (`max_heating_temperature`,
   `FA 06 6E`) stehen beide auf 40 °C; Betriebsmodus „Heizen“, „HZ Unterstützung“ aus. Die Grenze kann also
   von beiden kommen. In der HA-History lag Max T-Vorlauf mehrmals kurz über 40 °C (nach Warmwasser); der
   wirksame Vorlauf-Soll blieb dann bei 40 °C und folgte darunter dem Max T-Vorlauf – die Grenze ist damit
   belegt, ihre Quelle nicht.
   **Was T Vorlauf Tag bei Fest bedeutet (RoCon-HP-Anleitung, Fachmann; CAN-Komponente):** Mit
   [HK Funktion] = 1 (Fest) regelt die HPSU auf einen festen Vorlauf-Soll: im Heizbetrieb [T-Vorlauf Tag]
   während der Heizzeit (Betriebsart „Automatik 1/2“, „Heizen“), [T-Vorlauf Nacht] während der Absenkzeit
   („Automatik 1/2“, „Absenken“). [Max T-Vorlauf] begrenzt den so ermittelten Soll nach oben. Wirksamer Soll
   im Heizbetrieb also min(T-Vorlauf Tag bzw. Nacht, Max T-Vorlauf); Sunflow stellt über Max T-Vorlauf nur
   unterhalb von T-Vorlauf Tag. Die Komponente liest T Vorlauf Tag nur (number 20–90 °C, Schritt 0,1) und
   verwendet es nicht in ihrer eigenen Regelung; ihr Beispiel „Heizkurve zu Rotex“ schreibt T Vorlauf
   Tag/Nacht als selbst berechnete Heizkurve. [Max Temp Heizung] begrenzt laut Parameterliste den Vorlauf-Soll
   „bei aktiver Heizungsunterstützungsfunktion“ (Wärme aus dem Speicher, BIV); die Störungstabelle der
   Anleitung nennt es aber auch bei „Heizung wird nicht ausreichend warm“. Wahrscheinlich begrenzt also
   T Vorlauf Tag; sicher ist das erst nach dem Test unten. Folge für Automatik 1/2 und Absenken: in der
   Absenkzeit gilt T-Vorlauf Nacht als Grenze (Werkseinstellung 10 °C); Sunflow kennt das Zeitprogramm nicht.
   - **Todo – Test am Gerät (offen, nur auf ausdrückliche Anweisung des Nutzers):** je Kandidat zwei
     Schreibvorgänge (EEPROM), nur im Heizbetrieb (Betriebsmodus „Heizen“, Betriebsart Heizen oder
     Bereitschaft), nicht während Warmwasser und nicht in den 10 min danach, am besten bei Kompressor aus
     (ein Soll unter Tv − 3 K schaltet einen laufenden Kompressor ab). Kandidat 1: T Vorlauf Tag auf 1 K unter
     den aktuellen Max T-Vorlauf setzen (nicht unter Min T-Vorlauf), nach 2 min den Vorlauf-Soll lesen
     (folgt er auf diesen Wert, begrenzt T Vorlauf Tag), danach T Vorlauf Tag auf den alten Wert zurück.
     Kandidat 2: dasselbe mit Max Temp Heizung. Vorher und nachher HK Funktion, Betriebsmodus, Max T-Vorlauf
     und Vorlauf-Soll festhalten; Ergebnis hier eintragen. Die Umsetzung muss ohne den Test sicher sein.
     **Kandidat 2 getestet (Freigabe des Nutzers, per MCP):** Max Temp Heizung im Heizbetrieb bei laufendem
     Kompressor für 3 min auf 2 K unter Max T-Vorlauf gesenkt, danach auf den alten Wert zurück. Der wirksame
     Vorlauf-Soll blieb auf Max T-Vorlauf, folgte also nicht. **Max Temp Heizung begrenzt den Heizbetrieb
     (sehr wahrscheinlich) nicht**, passend zur Parameterliste (nur bei Heizungsunterstützung). Einschränkung:
     Der Sensor Vorlauf-Soll meldet nur bei Änderung, ein Neulesen in den 3 min ist nicht belegt; eine
     verzögert wirkende Grenze wäre nicht erkannt. Kandidat 1 (T Vorlauf Tag) bleibt offen und ist jetzt der
     wahrscheinliche Begrenzer.
   **Entscheidung für 4.7 (einfachste sichere Variante, umgesetzt 0.80.18):** keine neue Rolle und keine
   automatische Obergrenze. Die Obergrenze des Stellwerts stellt der Nutzer je Wärmepumpe (`flow_max_c`, 8.3)
   höchstens auf die eigene Grenze der HPSU (Fußboden: Startgrenze 40 °C, passt zur heutigen Einstellung).
   Abgesichert durch (a) die vorhandene Erkennung „Wärmepumpe begrenzt selbst“ (Abschnitt 4, `self_limited`:
   friert Korrektur und normales Schreiben ein, Journal) und (b) die Prüfung nach `adopt_delay` (4.7): bleibt der
   wirksame Soll im Heizbetrieb mehr als die Totzone unter dem geschriebenen Wert, Warnung im Journal mit dem
   Hinweis auf die Obergrenze. Ein Schreiben über die Grenze schadet der Wärmepumpe nicht (sie fährt ihre
   Grenze), kostet aber Budget und schützt beim Start nicht. Der Vorschlag oben (Rolle, Klemm-Erkennung als
   Obergrenze) bleibt für später (Abschnitt 16, „Nach dem Aktivschalten“).
10. **Quellenwechsel vor dem Aktivschalten beobachten** (Beobachtung, keine Entscheidung). Seit 3, Teil 4.5
    trägt jedes Ereignis `flow_source` auch im Schatten `jump_k` (Sprung des Startwerts). Vor 4.7 aus
    `heat_pump_samples` prüfen, wie oft die Quelle wechselt (z. B. `heat_recent` ↔ `base` an der 2-h-Schwelle)
    und wie groß die Sprünge sind: Häufiges Hin und Her verschiebt die Korrektur aktiv jedes Mal (hin und
    zurück hebt sich auf, außer an der Grenze ±3 K); Sprünge über 3 K zeigen, dass der Rest als Sprung bleibt
    und die Schutzschicht ihn abfangen muss. Fällt das auf, vor 4.7 mit dem Nutzer klären (z. B. Hysterese
    beim Wechsel der Quelle).

11. **Schwellen der schnellen Änderung beobachten** (6.2, seit 0.80.21; Beobachtung): aus
    `ventilation_recent` bzw. `flags.ventilation` prüfen, ob echtes Lüften und Kochen erkannt werden, ob
    langsames Abkühlen oder einzelne Sensorschritte noch auslösen und ob der schnelle Teil mit `settle_k`/
    `settle_min` passend endet (zu früh: Korrektur springt nach dem Ende; zu spät: Rückstand zählt verspätet).
    Die Werte sind im UI einstellbar; geänderte Startwerte mit dem Nutzer klären.

## 16. Reihenfolge

1. ESP: Manifest (CAN, UART), `tv_max`, Zehntel für Max/Min T-Vorlauf – umgesetzt; Hardware-Test offen.
2. Sunflow:
   a. **Altlösung entfernen** (umgesetzt): bisherige Wärmepumpen-Steuerung samt SG-Ready aus Backend, Frontend,
      Profilen/Schema, HA-Integration, Doku (Deutsch/Englisch) und Tests entfernen, ebenso
      `design/waermepumpe.md`. Behalten: die konfigurierten Raum-Temperatursensoren (werden die Räume in
      6.1) und das Hausmodell (`planning/house_model.py`, 9.3) samt Lernen und Gütetest; daran hängende
      Teile der Altlösung (z. B. Untergrenze für die SG-Ready-Sperre, Referenzraum) entfernen. Vorher
      Bestandsaufnahme: was hängt sonst daran (Heizstab, Batterieplanung `planning/plan.py`,
      Hausbilder)? Geteilte Teile nicht ohne Rückfrage löschen. Alte Felder in `config.json` müssen beim
      Laden weiter funktionieren (ignorieren, nicht abbrechen; `datenhaltung.md`).
   b. **Datenhaltung vorbereiten** (umgesetzt, 0.80.4): Backup vor Migration, Import aller Tabellen
      (`datenhaltung.md`, Regel 7 und „Export/Import“).
   c. Teil 1 (umgesetzt, 0.80.5): Manifest auslesen und neu laden, Kompatibilitätsprüfung je Rolle,
      `profile_version`, Profil `esphome/rotex_hpsu_can` mit Schema-Erweiterung, Gerät über das Manifest
      erkennen und anlegen (`ha_device_id`, MAC, Komponente), Neu-Bindung über die MAC; nur CAN, nichts
      schreiben. Teil 2a (umgesetzt, 0.80.6): Zustände und gültiger Heizbetrieb (Abschnitt 4),
      Datensammlung (`heat_pump_samples` in `shadow.db`, Aufräumen nach 1 Jahr, Import), Wärmequelle
      und Außentemperatur für das Hausmodell (9.3, Profil Version 2), Zustände in
      `GET /api/v1/heat_pump/status`; nichts schreiben. Teil 2b (umgesetzt, 0.80.7): climate-Entity
      „Haus“ (6.3, Migration 5 → 6), Schattenbetrieb mit Korrektur ohne Kennlinie (8.1, 8.2), Raum-Ist
      mit Veraltet-Kriterium (6.1), Anzeige der Zustände, Live-Werte und des Vorlauf-Ziels im UI;
      nichts schreiben.
3. **Aktivschalten**, in vier Teilen, die sich je einzeln testen und committen lassen. Teil 1 bis 3
   schreiben nichts an die Wärmepumpe (Schattenbetrieb); erst Teil 4 schreibt.
   1. **Lüft-Erkennung** (6.2) je Raum (umgesetzt, 0.80.8; 0.80.21 zur Erkennung schneller Änderungen in
      beide Richtungen umgebaut, Schwellen in Kelvin statt 0,3 K/30 min, einstellbar `house.fast_change`,
      Anlass: jeder einzelne 0,3-K-Schritt eines groben Sensors galt als Lüften): ein gelüfteter Raum zählt eine Zeit lang
      nicht für Raum-Ist und Korrektur; `flags.ventilation` in `heat_pump_samples` (neuer JSON-Schlüssel,
      `datenhaltung.md` Regel 4); Anzeige im UI, Bereich Wärmepumpe.
   2. **Kennlinie lernen** (9.2), **Vorsteuerung** (8.1, mit Heizflächentyp) und **Hausmodell auf
      `heat_pump_samples` umstellen**, sobald die Tabelle mindestens `MIN_DAYS` (7 Tage) gültigen
      Heizbetrieb (Abschnitt 4) abdeckt – Wärme nur aus gültigen Heiz-Intervallen, ohne Warmwasser,
      Abtauen und Lüft-Intervalle (9.3). Bis dahin Stundenmittel aus HA mit ausgelassenen Warmwasser- und
      Abtau-Stunden (umgesetzt in 2c, Teil 2a). Weiterhin Schatten. **Umgesetzt (0.80.9):**
      `heat_pump/curve.py`, `heat_pump/feedforward.py` (Rangfolge Hausmodell → Wärme der letzten 3 h →
      Basis), Einstellung `house.heating_surface`, Umstellung des Hausmodells (`learn_house`), Anzeige im
      UI und in den Attributen; Tests `tests/test_heat_pump_curve.py` (synthetische Daten über mehrere
      Tage, Rauschen, zu wenig Daten, Umschalten des Hausmodells).
   3. **Grenzen** (8.3) und **Schutzschicht** (8.4: Schaltregeln mit gelernten Sprung- und
      Schwankungswerten, Raumthermostat-Sperre) – weiterhin nur im Schatten: Stellwert und Sperre werden
      berechnet und angezeigt, nicht geschrieben. **Umgesetzt (0.80.10):** `heat_pump/limits.py`,
      `heat_pump/learned.py`, `heat_pump/guard.py`; geplante Schreibvorgänge (Zeit, Wert, Grund),
      Starts und ungewollte Abschaltungen in `heat_pump_samples` (`events`), Status und UI („würde
      schreiben“), Journal bei ungewollter Abschaltung und Sperr-Entscheidung; Zählung je Tag im UI
      (`GET /api/v1/heat_pump/<id>/shadow_writes`) zum Beurteilen des Budgets (48 + 24); Korrektur an einer
      Grenze in deren Richtung eingefroren. Sonne beim Hausmodell aus `heat_pump_samples` je Stunde aus
      der Statistik des Sensors (9.3). Tests `tests/test_heat_pump_guard.py` (synthetische Abläufe: Start
      aus Pause, nach Warmwasser, Abtauen, Sperre an/aus, Nachführen bei steigendem Tv, ungewollte
      Abschaltung, Grenzen, Lernen, Zählung).
   4. **Schreiblogik** (8.5) mit Budget (`EepromGuard`), HA-Sensoren (13), Übernahme mit eingefrorener
      Basis und Korrektur 0 (8.1, 8.2) – erst damit schreibt Sunflow aktiv. **Aufteilung (vom Nutzer
      freigegeben)** in sieben Teile, je einzeln getestet und committet. Feste Vorgaben für alle:
      Aktives Schreiben nur über einen Schalter je Wärmepumpe, Standard aus, eingeschaltet nur vom Nutzer;
      Ausschalten, Modus `off` und Ausfall stellen nichts zurück (8.6); bis zum Einschalten läuft alles im
      Schatten weiter. Teil 4.1 bis 4.6 schreiben nichts an die Wärmepumpe.
      1. **Ti je Heizflächentyp** (8.2): Kp 1, Ti aus `HEATING_SURFACES` (Fußboden 6 h, gemischt 4 h,
         Heizkörper 2 h); Wechsel des Typs ohne Sprung der Korrektur; wirkt schon im Schatten.
         **Umgesetzt (0.80.12):** `defaults.correction_ti_h`, `Correction.step(ti_h=…)`, Status und UI;
         Tests in `tests/test_heat_pump_shadow.py`.
      2. **Schreiblogik 8.5** als eigene Schicht hinter der Schutzschicht (Schutzschicht liefert je Takt
         den gewünschten Stellwert und ob er ein Schutzschritt ist; die Schreibschicht entscheidet, wann
         geschrieben wird): Vorderflanke, Mindestabstand 5 min normal / 2 min Schutzschritt (Profil
         `writes`, einstellbar), dazwischen nur den letzten Wert merken, Totzone außer bei Schutzschritten,
         Rundung auf `step` (sichere Richtung), „ausstehend … um …“, Taste „jetzt schreiben“ (überspringt
         den Mindestabstand, nicht das Budget), Journal je Schreibvorgang mit Grund. Im Schatten wirkt sie
         auf die virtuellen Schreibvorgänge; Journal nur für echte, im Schatten Ereignisse wie bisher.
         **Umgesetzt (0.80.13):** `heat_pump/writer.py` (`Writer`, `write_step`), Schutzschicht liefert
         `GuardResult.request`, Intervalle in `ControlSettings` (`min_interval_s`, `protect_interval_s`),
         Status `guard.pending`/`guard.write_now`, `POST /api/v1/heat_pump/<id>/write_now` (im Schatten
         409), UI; Mindestabstand nur im Profil einstellbar (Nutzer). Tests `tests/test_heat_pump_writer.py`.
      3. **Budget über `EepromGuard`**: normal 48/Tag, Notkontingent 24/Tag nur für Schutzschritte, 6/h;
         Tag in der Zeitzone von HA; Raumthermostat-Sperre zählt nicht (Punkt 7). Erschöpft: normal nicht
         mehr schreiben, Schutz über das Notkontingent, danach Sperre statt Nachführen, Journal. Im Schatten
         virtuell gezählt (aus den Ereignissen), nicht in `eeprom_writes.json`; `shadow_writes` zeigt
         dann, was mit Mindestabstand und Budget wirklich geschrieben würde.
         **Umgesetzt (0.80.14):** `heat_pump/budget.py` (`WriteBudget`, `VirtualLog`, `EepromLog`), Grenzen in
         `ControlSettings.budget`, Sperre statt Schutzschritt (Grund `budget`, `Guard.budget_block`), Zeitzone
         in `FlowShadow`, Status `guard.budget`, UI-Kachel, `EepromGuard` merkt die Zeiten (für 4.7). Tests
         `tests/test_heat_pump_budget.py`.
      4. **Fremde Schreiber** (8.5): Änderung von Max T-Vorlauf, die nicht von Sunflow stammt, und
         ESP-Regelung (`supply_setpoint_regulated` > 0, optionale Profilrolle) → Journal (Warnung, einmal je
         Episode), Zusammenfassung, Status; der virtuelle bzw. geschriebene Wert folgt dem echten. Wirkt
         schon im Schatten.
         **Umgesetzt (0.80.15):** `heat_pump/foreign.py` (`ForeignWatch`, in der Schutzschicht), Ereignis
         `foreign_write`, Episode 24 h, eigene Änderung nur nach echtem Schreiben (ab 4.7, 10 min), Mindestabstand
         ab der fremden Änderung, nicht im Budget; Profil Version 3 mit Rolle `regulated_setpoint`
         (Schema-Feld `missing_ok`), Grund `esp_regulation`; Status `guard.foreign`, Warnungen in der
         Einrichtungs-Zusammenfassung, `foreign_writes` in `shadow_writes`. Tests `tests/test_heat_pump_foreign.py`.
      5. **Sprungschutz beim Quellenwechsel** (8.1) für den aktiven Betrieb: Korrektur um alt − neu
         verschieben, damit V_Ziel nicht springt (in der Begrenzung ±3 K, Rest bleibt als Sprung, Journal).
         Im Schatten bewusst nicht; getestet in der Simulation.
         **Umgesetzt (0.80.16):** `source_shift` (`heat_pump/feedforward.py`), `Correction.shift`,
         `FlowShadow._source_change`; alter Startwert `start_c`/`start_at` in `heat_pump_state.json` (höchstens
         30 min alt, sonst kein Schutz, Journal), Ereignis `flow_source` mit `jump_k` und aktiv `shift_k`/
         `rest_k`/`protected`, Status `source_change`, UI-Hinweis je nach Schatten/aktiv. Wirkt erst mit 4.7
         (`writing_active` bis dahin überall falsch); die Tests `tests/test_heat_pump_source_shift.py` setzen
         `FlowShadow.active` als Simulation.
      6. **HA-Sensoren** (13): „Vorlauf-Ziel“, „Stellwert“ (Attribute geschrieben/ausstehend, Zeit, Grund,
         `shadow`), „Schreibvorgänge heute“; climate-Attribute Stellwert geschrieben/ausstehend und Schalter
         (nur Anzeige). Im Schatten gekennzeichnet.
         **Umgesetzt (0.80.17):** Status `flow.setpoint` je Wärmepumpe (`FlowShadow.setpoint_status`: `mode`,
         `written_c`/`_at`/`_reason`, `pending_c`/`_at`/`_reason`, `held`, `held_budget`, `writes_today`),
         Grund des geschriebenen Werts in der Schutzschicht (`Guard.written_reason`, in
         `heat_pump_state.json`; ältere Zustände aus `recent_writes` abgeleitet); `sunflow_ems`: Sensoren
         „Vorlauf-Ziel“, „Stellwert“, „Schreibvorgänge heute“ je Wärmepumpe (`SunflowHeatPumpSensor`; Namen,
         Einheiten und Anzeige vom Nutzer festgelegt, Abschnitt 13), Entfernen mit der Wärmepumpe,
         climate-Attribute `setpoint_*` und `mode` (Schalter nur als Anzeige). Tests
         `tests/test_heat_pump_ha_sensors.py`, `tests_integration/test_sunflow.py` (lief in einer Kopie von
         HA core unter `/tmp`).
      7. **Aktivschalten**: Schalter je Wärmepumpe (Standard aus), Übernahme beim Einschalten mit
         eingefrorener Basis und Korrektur 0 (V_Ziel springt nicht, geschrieben wird erst, wenn die Regeln es
         verlangen), echtes Schreiben über die Profil-Schreibschritte (`storage: eeprom`), eigene Sperre ist
         nicht fremd, Prüfung des Übernommenen (wirksamer Soll nach `adopt_delay`), Journal bei Ein/Aus;
         Ausschalten, Modus `off`, Ausfall: nichts zurückstellen (8.6), danach wieder Schatten.
         Die Erkennung eigener Änderungen (4.4) braucht dann `Guard.record_write(shadow=False)`.
         Der Sprungschutz beim Quellenwechsel (4.5) greift mit `writing_active` ohne weiteres Zutun.
         Voraussetzungen zum Einschalten: Gerät kompatibel, Strategie `fixed`, keine ESP-Regelung, Punkt 9
         entschieden. Simulation nach Abschnitt 14 (HPSU-Nachbildung) als Abnahme. Das erste Schreiben an
         der Wärmepumpe nur mit Freigabe des Nutzers.
         **Umgesetzt (0.80.18, nichts an die Wärmepumpe geschrieben, Schalter überall aus):** Schalter
         `DeviceConfig.flow_active` (Standard aus, nur genau `true` gilt), `PUT /api/v1/heat_pump/<id>/writing`
         (Einschalten 409 mit `reason` `incompatible`, `no_sample`, `strategy`, `esp_regulation`; Ausschalten
         immer), Status `writing` je Gerät, UI-Schalter mit Bestätigung. `FlowShadow._switch`: Journal bei
         Ein/Aus; übernommen wird, sobald eingeschaltet und Modus Heizen (`_adopt`: Basis bleibt wie sie ist und
         folgt nicht mehr dem wirksamen Soll, Korrektur aller Strategien 0 mit neuem Bezug, Schutzschicht vom
         echten Max T-Vorlauf ohne Zeit/Grund und von der echten Sperre – gesetzt = fremd; Merker `active`/
         `writing` je Gerät in `heat_pump_state.json`, fehlt er, wird neu übernommen). Modus Aus: nichts
         geschrieben, Basis folgt wieder, danach neue Übernahme. Echtes Schreiben (`heat_pump/active.py`,
         `WriteTargets` aus Profil-Schreibrolle der Strategie fixed mit `storage`, Sperre als Option des Selects
         laut Manifest bzw. Schalter): `execute` mit `EepromGuard` (Zeit des Takts), Budget aus `EepromLog`;
         Fehler (keine Entität, keine Verbindung, Ablehnung, EEPROM-Grenze) → Schreibvorgang samt Ereignis und
         Journal zurückgenommen, Journal-Warnung einmal bis zum nächsten Erfolg. Eigene Sperre nicht fremd
         (`Guard._follow_block`: echte Sperre darf `OWN_WRITE_CONFIRM_S` brauchen, danach gilt die echte; von
         außen aufgehoben → Journal). Prüfung nach `adopt_delay` (`AdoptCheck`, Profil `adopt_delay`, Standard
         60 s): echter Max T-Vorlauf ≠ geschrieben → `missing`, Schutzschicht folgt dem angezeigten Wert;
         wirksamer Soll im Heizbetrieb unter geschrieben − Totzone → `limited`; je Art eine Journal-Warnung bis
         eine Prüfung passt; Status `guard.adopt`. Ausschalten: nichts zurückgestellt, eine eigene Sperre bleibt
         und gilt ab dann als fremd (Journal). `written_c` in `heat_pump_samples`, `HeatingTracker.written_at`
         aus dem letzten echten Schreiben. Tests `tests/test_heat_pump_active.py` (mit HPSU-Nachbildung: Übernahme
         nach 60 s, eigene Grenze, 3-K-Start/Stopp, Sprung, Sperre), Suite grün.
      - **Nach dem Aktivschalten** (beim Umsetzen von 4.7 aufgefallen, nicht umgesetzt, je mit dem Nutzer
        entscheiden):
        1. Übernahme mit Quelle Kennlinie (`house_model`/`heat_recent`): Korrektur 0 heißt V_Ziel = Kennlinie,
           das kann vom wirksamen Soll abweichen – „springt nicht“ gilt nur für die Quelle Basis. Abgefangen
           durch Totzone und Plausibilität (±8 K); denkbar: Korrektur bei der Übernahme auf wirksam − Kennlinie
           (in ±3 K).
        2. Die Basis bleibt eingefroren, solange eingeschaltet ist und Modus Heizen gilt – auch wenn Sunflow lange
           nicht schreibt (Strategie ≠ fest, ESP-Regelung, Programm ≠ Heizen, fremde Sperre). Nur Modus Aus
           gibt sie frei. Bei langer Pause ist sie veraltet.
        3. Punkt 9: keine automatische Obergrenze aus der HPSU (T-Vorlauf Tag, Max Temp Heizung, in Automatik
           1/2 nachts T-Vorlauf Nacht). Schutzschritte über der Grenze kosten Budget und schützen nicht. Vorschlag
           in Abschnitt 15, Punkt 9 (Rolle, Klemm-Erkennung als Obergrenze).
        4. Nicht übernommener Wert (`missing`): Sunflow schreibt nach dem Mindestabstand erneut, jedes Mal ein
           EEPROM-Schreibvorgang (nur durch das Budget begrenzt). Denkbar: nach n Fehlschlägen Schreiben
           anhalten und warnen.
        5. Ausschalten mit eigener Sperre: Sie bleibt (8.6) und gilt danach als fremd; die HPSU heizt nicht, bis
           der Nutzer sie aufhebt. Denkbar: Hinweis/Taste „Sperre aufheben“ im UI beim Ausschalten.
        6. Fremde Änderungen im aktiven Betrieb: Sunflow schreibt nach dem Mindestabstand dagegen; zwei Regler
           können sich bis zum Budget abwechseln. Denkbar: bei wiederholten fremden Änderungen Schreiben anhalten.
        7. Zwei Tagesbegriffe: `EepromGuard` zählt seine harte Tagesgrenze (hier 72) nach dem Datum des Systems
           der App, das Budget nach der Zeitzone von HA. Unschädlich (das Budget ist enger), aber uneinheitlich.
        8. Prüfung des Übernommenen und die Bestätigungszeit der eigenen Sperre stehen nur im Speicher: nach
           einem Neustart kurz nach einem Schreibvorgang entfällt die Prüfung, und eine noch nicht angezeigte
           eigene Sperre könnte als von außen geändert gelten.
        9. Die Liste „zuletzt geschrieben“ im UI mischt virtuelle (Schatten) und echte Schreibvorgänge; die
           Ereignisse tragen `shadow`, die Anzeige unterscheidet es nicht.
        10. Der Schalter ist nur in der Sunflow-UI/API, nicht als Entity in HA (13 sieht nur die Anzeige `mode`
            vor).
        11. Abnahme-Simulation deckt Übernahme, Sperre, Pause/Start, Fehler und Prüfung ab; Warmwasser, Abtauen,
            Bypass, Soll-Sprünge und erschöpftes Budget im aktiven Betrieb nur über die Tests der Schichten
            (Schatten), nicht mit der HPSU-Nachbildung.
        12. Einschalten prüft nicht, ob andere Regler Max T-Vorlauf oder die Sperre schreiben (Inbetriebnahme
            Schritt 0); das meldet erst die Erkennung fremder Schreiber.
        13. Sonne in der Planung (7, Teil 4.2 ohne Sonne): die PV-Prognose als Ersatz für die Einstrahlung im
            Hausmodell (S je Slot = PV-Prognose / höchste PV-Leistung, 0–1) – ungeprüfte Annahme (PV-Fläche ≠
            Fenster), nicht umgesetzt (Nutzer 22.11.). Das Vorheizen bei PV-Überschuss über den Spielraum ist davon
            unberührt.
      - **Modus in einer Auswahl (0.80.22):** Aus / Nur Lernen / Lernen + Regeln statt Modus und Schalter,
        Aus ohne Aufzeichnung und Lernen (Abschnitt 13). Tests `test_heat_pump_samples.py`
        (`test_mode_off_records_and_learns_nothing`), `test_heat_pump_guard.py`
        (`test_mode_off_learns_nothing_from_a_shutdown`).
      - **Später:** veraltetes climate-Attribut `shadow` entfernen (seit 0.80.17 ersetzt durch `mode`), mit
        Hinweis im CHANGELOG und in der Doku; vorher prüfen, dass Karten und Automationen `mode` nutzen.
      - **Todo (offener Punkt 8):** in den ersten Tagen des aktiven Betriebs per MCP (HA-History) und aus
        `heat_pump_samples` prüfen, ob der während Warmwasser geschriebene Stellwert (Vorbereiten, 8.4)
        beim Start danach gilt: Ereignisse `write` mit Phase `hot_water`, danach `start` mit
        `max_setpoint_c`/`setpoint_c` und keine `unwanted_stop` kurz nach dem Start; Warmwasser läuft
        dabei unverändert weiter. Ergebnis in Abschnitt 15, Punkt 8 eintragen; trifft die Annahme nicht
        zu, die Vorbereitung auf die Zeit nach dem Warmwasser-Ende verlegen (Rückfrage beim Nutzer).

   Vorbereitung (0.80.11): fremd gesetzte Sperre wird nicht aufgehoben, nur angezeigt (8.4, 8.6);
   Grenzen des Stellwerts je Wärmepumpe (8.3, Punkt 4); Sperre außerhalb des Budgets (8.5, Punkt 7);
   Punkt 8 aus der HA-History nicht beantwortbar, wird in Teil 4 im aktiven Betrieb beobachtet (Todo oben).
   Die alten Regler sollen vor dem Aktivschalten abgeschaltet sein (Inbetriebnahme Schritt 0, Abschnitt 14);
   ein bei der Prüfung vor Teil 4 noch laufender Altregler (schrieb den Raumthermostat-Eingang) ist
   inzwischen entfernt.

   **Vor Teil 4:** Hardware-Test geklärt (Abschnitt 15, Punkt 1), ebenso Raumthermostat-Eingang (7) und
   höchster Vorlauf für den Estrich (4); Punkt 8 folgt in Teil 4. Vor Teil 4.7 entschieden (0.80.18):
   Bereich von Max T-Vorlauf gegen die eigene Grenze der HPSU (Punkt 9). Vor dem Einschalten Inbetriebnahme
   Schritt 0 (Abschnitt 14) erneut prüfen: kein anderer Regler darf Max T-Vorlauf oder den
   Raumthermostat-Eingang schreiben.
4. **Planung mit Raum-Soll + Spielraum (Vorheizen)**, in vier Teilen (vom Nutzer freigegeben 22.11.), je
   einzeln getestet und committet; Entscheidungen in Abschnitt 7.
   1. **Spielraum** (6.3): Einstellung `house.climate.preheat_k`/`lower_k`, API, UI beim Raum-Soll, Doku;
      noch ohne Wirkung. **Umgesetzt (0.80.23).**
   2. **Planung anbinden** (7): `ThermalLoad` neu angebunden (Band [Soll − y, Soll + x], neue weiche Kosten
      für die Abweichung vom Soll im MILP), nur mit nutzbarem Hausmodell und Programm Heizen; COP Carnot;
      Wärmepumpe aus der Lastprognose über das Modell (höchstens 70 %); Ausgabe je Slot Planziel und Wärme;
      Neuplanung sofort bei Soll- oder Spielraum-Änderung; bei „Nur Lernen“ eigene zweite Rechnung nur zur
      Anzeige; Anzeige im Plan. **Umgesetzt (0.80.24):** Abschnitt 7 „Umsetzung“; Tests `tests/test_house_plan.py`;
      Regler unverändert (Planziel erst 4.3).
   3. **Planziel an den Regler** (8.1, 8.2): Korrektur gegen das Planziel des laufenden Slots; `Q*` aus der
      Planung als neue erste Quelle `plan` (vor `house_model`), mit Sprungschutz wie die anderen Quellen;
      Änderungen des Planziels frieren die Korrektur nicht ein (nur eine Soll-Änderung); climate-Attribute
      Planziel und Grund („Vorheizen wegen PV“, „… günstiger Strom“).
   4. **Prognose-Korrektur der Außentemperatur** (7): Abweichung Prognose ↔ Messung nach Tageszeit und
      Vorlaufzeit lernen und die Prognose der Planung damit korrigieren; Anzeige unter „Gelernt“.
5. UART-Begleiter, Paarung.
6. S2.
