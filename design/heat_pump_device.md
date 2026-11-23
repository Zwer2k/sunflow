# Geräteanbindung der Wärmepumpe (Manifest, Profil, ESPHome-Komponenten)

Ergänzung zu `heat_pump.md`: wie Sunflow eine Wärmepumpe erkennt, ihre Entities findet und prüft. Umgesetzt.

## 1. Geräteanbindung

### 1.1 Problem

ESPHome-`unique_id`s enthalten den Anzeigenamen aus der Nutzer-YAML (`<MAC>/0/number/Vorlauf Soll
Geregelt`). Namen sind frei wählbar und mehrsprachig (de/en/fr/it), Auswahlwerte übersetzt („Heizen“/
„Heating“), einzelne Entities per `type:` als number/select/switch anlegbar. Die stabilen Schlüssel der
Komponente (`tv`, `supply_setpoint_regulated`, …) erreichen HA nicht. Deshalb: **Manifest ist Pflicht.**
Firmware ohne Manifest wird nicht unterstützt (kein Rückfall über Namenslisten).

### 1.2 Manifest-Abruf (umgesetzt)

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
- Abruf und Neuladen: siehe 1.6.

**Vorfilter:** Ob ein Knoten einen dieser Dienste anbietet (HA-Dienstliste, ohne Kosten).
`esphome: project:` wird nicht ausgewertet: frei änderbar, veraltet beim Aktualisieren über
`external_components`, nur einmal je Knoten, teils vom Nutzer anderweitig belegt.

**Vertrauenswürdig ist nur, was der Komponenten-Code erzeugt:** `id` (Konstante, erkennt die
Komponente), `git` (beim Bauen ermittelt, nur Diagnose). Eine Versionsnummer gibt es bewusst nicht
(siehe 1.6).

**Risiko ESPHome-Updates:** Die Aktion nutzt interne, aber öffentliche Klassen der ESPHome-API, die
sich zwischen Versionen ändern (bisher: Name als `StringRef` → `ProgmemStringRef`, Fehlertext
`std::string` → `StringRef`). Die vorhandenen Workflows kompilieren die Beispiel-YAMLs mit der
jeweils aktuellen stabilen ESPHome-Version (CAN `build.yml`, UART `compile-test.yaml`) und fangen so
Brüche nach einem Release ab. Empfehlung: zusätzlich ESPHome beta/dev bauen, um Brüche vor dem Release
zu sehen; bei Bruch `manifest_action.h` anpassen. Ein Bruch betrifft nur die Firmware-Erstellung, nicht
einen laufenden ESP.

### 1.3 Manifest-Schema (umgesetzt)

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
  sich doch, erkennt Sunflow das beim nächsten Laden (1.6).
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
  und friert ein (heat_pump.md, 4); sonst ist sie für Sunflow ohne Bedeutung.

### 1.4 Topologien und Profile

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

### 1.5 Erweiterungen in Sunflow (allgemein)

- Profil-`match` um `manifest: {id}` erweitern; Rollen über Manifest-Schlüssel.
- Auflösung Schlüssel → Entity zur Laufzeit (wie heute `bind_roles`), Manifest im Snapshot gecacht.
- `profile_version` wird ausgewertet: abweichend → Hinweis.
- Fehlende Pflichtrolle eines übernommenen Geräts → Fehler in der Einrichtungs-Zusammenfassung (heute
  still `None`).
- Begleitgerät im Profil-Schema (`companion: {profile, roles}`).

### 1.6 Kompatibilität und Neuladen

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
- Pflichtrolle fehlt oder passt nicht → Gerät **inkompatibel**: nicht mehr schreiben (heat_pump.md, 12), Meldung in Zusammenfassung und Journal mit dem
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

## 2. Geräteprofil Rotex HPSU (umgesetzt, `profiles/heat_pumps/esphome/rotex_hpsu_can.yaml`)

Sunflow beschreibt Geräte über Profile (`profiles/`, Schema `schema/profile.schema.json`): Erkennung,
Rollen (welche Entity welchen Wert liefert), Steuerung und Grenzen. Für die HPSU kamen ins Schema:
`match.manifest`, das Rollenfeld `domain`, sowie `companion`, `states` und
`flow_control`. Ändert sich das Profil, steigt `version` (keine Datenmigration). Die Fähigkeit `flow_control` kommt erst
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
                         missing_ok: true}   # ESP-Regelung, nur gelesen (> 0: Sunflow schreibt nicht, heat_pump.md 12)
  outdoor_unit:         {key: outdoor_unit, domain: [select, text_sensor], optional: true}
  electric_energy:      {key: total_electrical_energy, domain: [sensor], unit: kWh, optional: true}

companion:
  profile: esphome/rotex_hpsu_uart
  manifest: rotex_hpsu_uart   # gebunden am einen Gerät mit diesem Manifest
  roles:
    flow_temperature_uart: {key: tv, domain: [sensor], unit: "°C", optional: true, missing_ok: true}
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
  learned_defaults: {steady_swing: 0.5}
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
  `min`/`max` gegen `limits`, `step` gegen `MAX_SETPOINT_STEP_K`, 1.6).
- `limits` (Stellwertbereich, Startwerte), `heat_power`, `compressor_rules`, `learned_defaults`,
  `adopt_delay`, `settle_after_bypass`, `frozen_after`: Parameter für Zustände, Grenzen und Schutz (heat_pump.md, 4, 10, 11).
- `heating_block`: Auswahlwerte für gesperrt/frei der Rolle `heating_block`, Mindestdauer.
- `writes`: Mindestabstände und Budget (heat_pump.md, 12); `deadband` nur, wenn ein Profil von `DEADBAND_K` abweicht.
Beim Laden prüft Sunflow, dass `states` und `flow_control` nur Rollen des Profils nennen.
Rollenfeld `missing_ok` (Schema, seit Profilversion 3): bei einer optionalen Rolle, deren Fehlen der Normalfall
ist (die ESP-Regelung), meldet die Kompatibilitätsprüfung einen fehlenden Schlüssel nicht; passt der
vorhandene Schlüssel nicht (Domain, Einheit), bleibt es ein optionales Problem.

---

## 3. Änderungen an den ESPHome-Komponenten

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
  `tv_max` gemeinsam im Intervall von `tv` veröffentlicht (Spitzen zwischen zwei `tv`-Werten); in den Beispiel-YAMLs ergänzt.
- Max T-Vorlauf und Min T-Vorlauf mit Schrittweite 0,1 °C (Register in Zehntel); beim Schreiben wird der
  skalierte Wert gerundet statt abgeschnitten (vorher z. B. 27,3 → 27,2).
- `supply_setpoint_regulated` bleibt als Option (Sunflow übernimmt nicht, solange sie aktiv ist; gelesen über
  die Rolle `regulated_setpoint`, heat_pump.md 12).

