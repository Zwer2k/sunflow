# Entwurf: Vorlaufregelung für Wärmepumpen in Sunflow

Stand: 21.11.2026 · Status: Spezifikation; ESP-Teil (Manifest, `tv_max`, Zehntel für Max/Min T-Vorlauf;
Abschnitt 3, 12) umgesetzt; Sunflow: Altlösung entfernt (Abschnitt 16, 2a), Datenhaltung (2b), Manifest,
Kompatibilität und Profil (2c, Teil 1) umgesetzt, Rest offen

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

Zusätzliche Eingänge: Kompressor läuft (`status_kompressor`), Störung (`error_code`). Bei einer Störung
(Fehlercode ≠ 0): nicht schreiben, nicht lernen, Korrektur einfrieren, Journal.

**Gültiger Heizbetrieb** – Voraussetzung für Regeln (Nachführen, Korrektur, 8.2) und Lernen (9.1); sonst
einfrieren. Alle Bedingungen gleichzeitig:
- Programm `heating` (Heizen, Absenken, Automatik 1/2);
- Aktivität `heating` und BPV = 0 % seit mindestens der Beruhigungszeit (10 min);
- keine Raumthermostat-Sperre aktiv, keine Störung;
- die HPSU begrenzt den Vorlauf nicht selbst (wirksamer Soll ≈ Stellwert, siehe Absenken/Automatik);
- Gerät erreichbar und kompatibel (3.6).
Die Startregel (8.4) gilt unabhängig davon.

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

### 6.2 Lüft-Erkennung

Typischer Verlauf beim Stoßlüften: schneller Abfall (Größenordnung 1 K in einer halben Stunde),
schnelle Teilerholung, sobald das Fenster zu ist (Luft wärmt sich an Wänden/Möbeln), Rest langsam über
Stunden.

- Ereignis: Raum fällt ≥ 0,3 K innerhalb ≤ 30 min (≥ 2 Meldungen) bzw. Steigung < −0,6 K/h.
- Folge: Raum zählt für die Korrektur nicht, bis die schnelle Erholung vorbei ist (Steigung wieder flach,
  höchstens 2 h). Für Lernen der Kennlinie wird das Intervall ausgelassen.
- Der langsame Rest wird nicht aktiv ausgeregelt: Die Fußbodenheizung gibt bei kälterem Raum von selbst
  mehr Wärme ab (Selbstregeleffekt); Korrektur ist langsam mit Totzone.
- **Keine Uhrzeit-Features.** Wiederkehrende Tagesmuster deckt die Planung ab; ein gelernter Tagesgang
  innerer Gewinne nur, wenn die Auswertung ihn zeigt.

### 6.3 Raum-Soll und Spielraum

- **Raum-Soll**: eingestellt, nicht gelernt. Bereitgestellt als `climate`-Entity „Haus“ in `sunflow_ems`
  (Soll, Ist = Raum-Ist, Modus heat/off) und im Sunflow-UI. Sunflow speichert den Wert, HA spiegelt ihn.
  Nachtabsenkung o. Ä. über HA-Zeitpläne/Automationen auf diese Entity. Modus `off`: Sunflow schreibt
  nichts (wie Steuerung aus, 8.6), rechnet und zeigt aber weiter an (wie Schattenbetrieb).
- **Spielraum**: „Vorheizen bis +x K“ (Start 1,0 K) und „Absenken bis −y K“ (Start 0 K). Abweichung vom
  Soll kostet in der Planung → ohne Ersparnis bleibt Sunflow auf dem Soll.
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

**Außentemperatur – gemessen für jetzt, Prognose für später:**

| Verwendung | Quelle |
|---|---|
| aktueller Bedarf (Vorsteuerung 8.1, Korrektur 8.2) | **gemessene** Außentemperatur: Sensor der WP (`temperature_outside`) oder eigener Sensor (Lernquelle „Außentemperatur“) |
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

- `n` je Heizflächentyp vorgegeben: Fußbodenheizung 1,1 · Heizkörper 1,3 · gemischt 1,2 (Einstellung).
- `K` gelernt (Abschnitt 9). Spreizung `s` gelernt als Funktion der Leistung (einfaches Modell, z. B.
  linear in Q).
- Benötigte Wärme `Q*`: aus der Planung, sonst aus dem Hausmodell
  `Q* = (a/b)·(Planziel − T_außen) − Gewinne`; ohne Hausmodell aus der Kennlinie im Gleichgewicht.
  `T_außen` ist für den aktuellen Slot die gemessene Außentemperatur (Abschnitt 7), nicht die Prognose.
- Vorlauf-Ziel: `V_Ziel = Planziel + (Q*/K)^(1/n) + s(Q*)/2`.
- Ohne gelernte Kennlinie (Start): V_Ziel = zuletzt wirksamer Vorlauf-Soll; nur die Korrektur bewegt ihn.

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

### 8.3 Grenzen

- Stellwert in [min, max] (Nutzereinstellung je Wärmepumpe). Startwerte Fußbodenheizung 25 / 40 °C,
  Heizkörper höher; nie außerhalb der Grenzen der Entity (Manifest `min`/`max`).
- Leistungsbereich der Wärmepumpe (Heizleistung min/max, Start HPSU 0,6 / 6 kW; die Klasse des
  Außengeräts steht im Manifest `outdoor_unit`): begrenzt die Wärme in der Planung, und die
  Mindestleistung erkennt „Mindestleistung > Bedarf“ (Takten absehbar → Sperre statt Nachführen, 8.4).
  Später aus den Messwerten (`thermal_power` im Dauerlauf) gelernt.
- Plausibilität: V_Ziel außerhalb eines Bands um den Wert der gelernten Kennlinie bzw. letzten wirksamen
  Soll (Start ±8 K) → nicht schreiben, Journal.

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
- Taste „jetzt schreiben“ im UI überspringt den Mindestabstand (zählt gegen das Budget).
- Kein Schreiben bei `hot_water`, `defrost`, `unknown`, BPV > 0 %, Programm ≠ heating, Gerät nicht
  erreichbar, ESP-Regelung aktiv (`supply_setpoint_regulated` > 0 → ESP regelt selbst → Hinweis,
  keine Übernahme).
- **Fremde Schreiber**: Ändert sich Max T-Vorlauf, ohne dass Sunflow geschrieben hat (pyscript,
  Automation, ESP-Regelung, Hand), meldet Sunflow das im Journal und in der Zusammenfassung.
- Anzeige: „geschrieben 10:42: 31,2 °C“ / „ausstehend: 30,6 °C, wird um 10:45 geschrieben“ / Grund.
- Journal: jeder Schreibvorgang mit Grund (Soll-Änderung, Plan, Schutzschritt, Start, manuell).

### 8.6 Ende der Steuerung und Ausfall

Sunflow stellt nichts zurück – weder Max T-Vorlauf noch die Raumthermostat-Sperre. Die zuletzt
geschriebenen Werte bleiben stehen. Läuft Sunflow (wieder), berechnet es aus dem Gelernten, ob und mit
welchem Vorlauf die WP laufen soll, und ändert die Werte, falls sie nicht passen. Ein Originalwert wird
nicht gemerkt; der Watchdog der Integration greift hier nicht ein.

Bewusst in Kauf genommen: Ist die Sperre `on`, wenn Sunflow ausfällt oder nicht mehr regelt (Absturz,
HA aus, Steuerung abgeschaltet, Gerät inkompatibel), heizt die HPSU nicht, bis Sunflow wieder regelt oder
der Nutzer die Sperre selbst aufhebt. Sunflow zeigt deshalb in der UI und im Journal an, wenn die Sperre
aktiv ist.

---

## 9. Lernen

### 9.1 Datensammlung

- Minütlich messen, als 5-min-Mittel speichern: Tv, Tr, TvBH, Wärmeleistung, Raum-Ist je Raum, außen,
  Programm, Aktivität, BPV, Raumthermostat-Sperre, Kompressor, wirksamer Soll, Stellwert, Strategie,
  Ereignis-Flags
  (Lüften, Warmwasser-Nachlauf, Abtau-Nachlauf).
- Gültig für die Kennlinie: gültiger Heizbetrieb (Abschnitt 4), Kompressor läuft ≥ 15 min, kein Nachlauf,
  Raum-Ist verfügbar, Wärmeleistung plausibel.

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
| Schreibzähler | `eeprom_writes.json` (vorhanden) | JSON |
| Raum-Soll, Spielraum, Räume, Heizflächentyp, Grenzen, Wetter-Entity | `config.json` (vorhanden, Migration) | JSON |
| Gerätezuordnung (`ha_device_id`, MAC, Komponente, Paarung CAN ↔ UART) | `config.json` (`DeviceConfig`) | JSON |

Warum `shadow.db` statt eigener Datei: Export/Import (`api/system.py`), MCP-SQL (`query_shadow_db`) und
Migration (`user_version`, `SCHEMA_STEPS`) gibt es dort schon; Abfragen gegen Preis/PV/Plan aus `slots`
gehen per JOIN. Eigene Tabelle statt Spalten in `slots`, weil Takt (5 statt 15 min) und Zweck anders sind.
Regeln für Schemaänderungen: `datenhaltung.md`. Alle Tabellen der Wärmepumpen-Regelung beginnen mit
`heat_pump_` (Regel 12); heute ist das nur `heat_pump_samples`.

- `heat_pump_samples`: Schlüssel (`device_id`, `start`), Spalten mit Einheit im Namen (`tv_c`, `tr_c`,
  `tvbh_c`, `tv_max_c` (Spitze im Intervall, für die Sprung-Lernwerte), `heat_w`, `outside_c`,
  `bypass_pct`, `setpoint_c`, `written_c`, …), Ereignisse (Kompressorstart/-stopp mit Tv und Soll), Zustände als
  kanonische Schlüssel (`program`, `activity`, `strategy`), Räume als JSON (`rooms`: entity → °C),
  Flags als JSON (`flags`: Lüften, Nachlauf, Sperre, gültig).
- Aufräumen: Zeilen älter als 1 Jahr löschen (im Runner, blockweise).
- Lernen zurücksetzen: Zeilen des Geräts löschen und `heat_pump_state.json`-Eintrag verwerfen.
- Export/Import: `heat_pump_samples` wird beim Import übernommen (in `IMPORT_TABLES` eintragen); der
  Import führt alle Tabellen zusammen, siehe `datenhaltung.md` („Export/Import“).
- `heat_pump_state.json` (klein, verwerfbar) zusätzlich in den Export aufnehmen.

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
version: 1
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
  compressor:           {key: status_kompressor, domain: [binary_sensor]}
  program:              {key: operating_mode, domain: [select, text_sensor]}
  activity:             {key: mode_of_operating, domain: [text_sensor]}
  strategy_select:      {key: hk_function, domain: [select, text_sensor]}
  bypass_valve:         {key: bypass_valve, domain: [sensor], unit: "%"}
  heating_block:        {key: room_therm, domain: [select, switch]}   # on = gesperrt, off = frei
  error:                {key: error_code, domain: [text_sensor, sensor]}
  max_flow_setpoint:    {key: max_target_flow_temp, domain: [number], unit: "°C"}   # Max T-Vorlauf, beschrieben
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
- `supply_setpoint_regulated` bleibt als Option (Sunflow übernimmt nicht, solange sie aktiv ist).

## 13. HA-Seite und UI

- `sunflow_ems`:
  - `climate`-Entity „Haus“: Raum-Soll (einstellbar), Raum-Ist, Modus; Attribute Planziel und Grund
    (z. B. „Vorheizen wegen PV“), Vorlauf-Ziel, Stellwert geschrieben/ausstehend.
  - Sensoren „Vorlauf-Ziel“, „Stellwert“ und „Schreibvorgänge heute“.
- Sunflow-UI, Bereich Wärmepumpe:
  - Gerät: CAN, ggf. UART-Begleiter, Paarung (Name, Bereich, MAC); erkannte Strategie (aus
    `hk_function`); Programm und Aktivität; Kompatibilität (fehlende Rollen mit Grund, 3.6).
  - Einstellungen: Raum-Soll, Spielraum +x/−y, Heizflächentyp, Grenzen, Leistungsbereich, Budget,
    Mindestabstand, Wetter-Entity.
  - Räume mit Gewicht, Lüft-Ereignisse, gelernter Versatz je Raum.
  - Gelerntes: Kennlinie (K, n, Spreizung, Güte), Hausmodell, Korrektur je Strategie, Sprung- und
    Schwankungswerte (`steady_swing`, `start_jump`, `start_settle`), Prognose-Korrektur.
  - Live: Tv, `tv_max`, Tr, BPV, wirksamer Soll, Vorlauf-Ziel, Stellwert geschrieben/ausstehend,
    Raumthermostat-Sperre (deutlich sichtbar, wenn aktiv), „jetzt schreiben“.
  - Journal: Schreibvorgänge mit Grund, ungewollte Abschaltungen, fremde Schreiber, Sperre an/aus.
- Schattenbetrieb: alles wird berechnet und angezeigt, nichts geschrieben.

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
   (Integrationen, Automationen, Skripte, ESP-Regelung `supply_setpoint_regulated` auf 0). Sunflow
   meldet fremde Schreiber (8.5).
1. Firmware mit Manifest und `tv_max` auf CAN (und Manifest auf UART).
2. Sunflow: Gerät erkennen, Räume eintragen, **Schattenbetrieb 1–3 Tage** (Daten sammeln, Vergleich).
3. Aktiv mit S1, Budget eng beobachten, Journal prüfen.
4. Danach Analyse-Phase S2.

---

## 15. Offene Punkte

1. Manifest mit Seiten und `tv_max` auf echter Hardware testen (Manifest ohne Seiten bereits bestätigt).
2. Sommer vs. Bereitschaft der HPSU im Detail (RoCon-Anleitung).
3. Startwerte Budget (48 + 24) nach den ersten Wochen prüfen.
4. Höchster Vorlauf für den Estrich (Nutzereinstellung, Standard?).
5. Untergrenze je Raum ja/nein.
6. S2-Entwurf nach Analyse.
7. Raumthermostat-Eingang: liegt er in der HPSU im EEPROM? Davon hängt ab, ob die Sperre ins
   Schreibbudget zählt.
8. Wirkt Max T-Vorlauf während Warmwasser auf den Heizbetrieb? (Annahme: nein; Vorbereitung vor dem
   Start nach Warmwasser hängt davon ab.)

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
      schreiben. Teil 2: Zustände, Datensammlung (`heat_pump_samples` in `shadow.db`), climate-Entity,
      Schattenbetrieb mit einfacher Regelung (Korrektur ohne Kennlinie).
3. Kennlinie lernen, Vorsteuerung, Schutzschicht (Schaltregeln mit gelernten Sprungwerten,
   Raumthermostat-Sperre), Schreiblogik, Budget → aktiv.
4. Planung mit Raum-Soll + Spielraum (Vorheizen).
5. UART-Begleiter, Paarung.
6. S2.
