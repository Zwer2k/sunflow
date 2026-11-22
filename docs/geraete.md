# Unterstützte Geräte

**Deutsch** · [English](en/devices.md)

Sunflow arbeitet mit Profilen (`profiles/`): Ein Profil sagt, welche Entitäten einer
Home-Assistant-Integration welche Werte liefern und wie ein Gerät gesteuert wird. Geräte
ohne Integration bindet Sunflow direkt per Modbus TCP an.

## Legende

**Prüfstand**

| Zeichen | Bedeutung |
|---|---|
| ✅ | an echter Hardware geprüft |
| 🧪 | gegen ein nachgebildetes Gerät mit den Entitäts-Schlüsseln der Integration getestet, noch nicht an Hardware |
| – | trifft nicht zu |

**Speicher-Aktionen**

| Aktion | Bedeutung |
|---|---|
| Automatik | Eigenverbrauch, das Gerät regelt selbst |
| Halten | nicht entladen, PV darf weiter laden |
| Laden | Laden erzwingen, auch aus dem Netz |
| Entladen | Entladen erzwingen, auch ins Netz |
| Laden begrenzen / Entladen begrenzen | höchstens die geplante Leistung |

Kann ein Gerät eine Aktion nicht, plant Sunflow sie nicht ein oder nimmt die
nächstliegende, z. B. „Halten“ statt „Entladen begrenzen“. Ohne „Laden“ gibt es kein
Laden aus dem Netz.

## Wechselrichter und Speicher

| Gerät | Anbindung | Profil | Messen | Speicher-Aktionen | PV abregeln | Voraussetzungen | Messen geprüft | Steuern geprüft |
|---|---|---|---|---|---|---|---|---|
| Sofar HYD-KTL (2. Gen.) | solax_modbus (Sofar-Plugin) | `solax_modbus/sofar` | PV je String, PV extern, Speicher, Netz, Verbrauch, Zähler | alle, leistungsgenau (Passivmodus-Fenster) | – | Passivmodus (wird beim Übernehmen gesetzt) | ✅ | ✅ |
| SolaX X1/X3 Hybrid Gen4/Gen5 | solax_modbus (SolaX-Plugin) | `solax_modbus/solax_gen4_5` | PV, Speicher, Netz, Verbrauch, Zähler | Automatik, Halten, Laden, Entladen | – | Remote Control Mode 1 der Integration | 🧪 | 🧪 |
| Growatt SPH mit Akku (z. B. SPH 4000–10000TL3 BH-UP) | solax_modbus (Growatt-Plugin) | `solax_modbus/growatt_hybrid` | PV, PV je String, Speicher, Netz (Bezug minus Einspeisung), Verbrauch, Zähler | Automatik, Laden, Entladen (VPP-Fernsteuerung) | – | VPP-Funktion (setzt Sunflow beim Übernehmen und im Gerätetest) | ✅ | ✅ (Laden, Automatik) |
| Growatt MOD/MIN und SPH ohne Akku | solax_modbus (Growatt-Plugin) | `solax_modbus/growatt_pv` | PV, PV je String, Zähler | – | – | – | ✅ | – |
| Deye-Mikrowechselrichter (z. B. SUN-M80G3) | solarman (HACS, davidrapan) | `solarman/deye_micro` | PV (AC), PV je Eingang, Zähler | – | – | – | ✅ | – |
| Huawei SUN2000 mit LUNA2000 | huawei_solar | `huawei_solar/sun2000_luna2000` | PV je String, Speicher, Netz (mit Smart Meter), Zähler | Automatik, Halten, Laden, Entladen | ja | Installateur-Zugang in der Integration | 🧪 | 🧪 |
| Huawei SUN2000 ohne Speicher | huawei_solar | `huawei_solar/pv_only` | PV, PV je String, Zähler | – | ja | Abregeln: Installateur-Zugang | ✅ | 🧪 (Abregeln) |
| SolarEdge StorEdge / Home Hub | solaredge_modbus (Core) | `solaredge_modbus/storedge` | PV, Speicher, Netz (mit Zähler), Zähler | alle | – | Speicher-Fernsteuerung (wird beim Übernehmen gesetzt) | 🧪 | 🧪 |
| Fronius Symo/Primo GEN24 Plus | fronius (Core) | `fronius/gen24` | PV, Speicher, Netz, Verbrauch, Zähler | Automatik, Halten, Laden begrenzen, Entladen begrenzen | ja | Modbus TCP mit Speichersteuerung im Wechselrichter freigeben | 🧪 | 🧪 |
| GoodWe ET/EH/BT | goodwe (Core) | `goodwe/hybrid` | PV, Speicher, Netz, Verbrauch, Zähler | Automatik, Laden, Entladen (nur volle Leistung) | – | – | 🧪 | 🧪 |
| Kostal Plenticore plus / G3 | kostal_plenticore (Core) | `kostal_plenticore/plenticore` | PV je String, Speicher, Netz, Verbrauch | Automatik, Halten | ja | – | 🧪 | 🧪 |
| Victron ESS (GX mit Multi/Quattro) | direkt, Modbus TCP | `modbus/victron_gx` | PV (MPPT und AC-gekoppelt), Speicher, Netz | Automatik, Halten, Laden (nur volle Leistung) | – | Modbus TCP im GX freigeben | 🧪 | 🧪 |
| Indevolt SolidFlex / PowerFlex (Steckerspeicher mit PV-Eingängen) | indevolt (Core) | `indevolt/indevolt` | PV, PV je Eingang, Speicher, AC-Ausgang, Zähler | Automatik, Halten, Entladen begrenzen, Laden, Entladen | – | – | 🧪 | 🧪 |
| Homevolt | homevolt (Core) | `homevolt/homevolt` | Speicher | Automatik, Halten, Laden, Entladen | – | Local Mode (setzt Sunflow selbst, wird nicht gespeichert) | 🧪 | 🧪 |
| Marstek Venus E / D / A (Steckerspeicher) | marstek_modbus (HACS, Modbus über RS485-Gateway oder Ethernet) | `marstek_modbus/venus` | Speicher, AC-Leistung, Zähler | Automatik, Halten, Laden, Entladen | – | RS485-Steuerung (setzt Sunflow selbst) | 🧪 | 🧪 |
| Zendure SolarFlow / Hyper / AIO | zendure_ha (HACS, offiziell) über das Gerät „Zendure Manager“ | `zendure_ha/manager` | Speicher (alle Zendure-Geräte zusammen) | Automatik, Halten, Laden, Entladen | – | Hausstromzähler im Zendure Manager für „Automatik“ (smart) | 🧪 | 🧪 |

Hinweise:

- **Flash-Werte:** Huawei, GoodWe, Kostal, Victron und der Betriebsmodus des Sofar speichern ihre
  Einstellungen dauerhaft im Flash. Sunflow schreibt sie nur, wenn sie sich ändern, und
  höchstens so oft am Tag, wie das Profil erlaubt.
- **Nur volle Leistung:** GoodWe und Victron laden bzw. entladen auf Befehl immer mit voller
  Leistung. Die Planung rechnet noch mit der geplanten Leistung; die Abweichung fängt die
  nächste Neuplanung auf.
- **Abregeln:** Sunflow regelt nur Anlagen ab, die unter „Steuerung“ freigegeben sind, und
  nur wenn Einspeisen kostet (negative Vergütung, über der netzdienlichen Grenze).

## Zähler

| Gerät | Anbindung | Profil | Messen | Messen geprüft |
|---|---|---|---|---|
| Tibber Pulse (lokal) | tibber_local | `tibber_local/pulse` | Netzbezug und Einspeisung am Stromzähler, Zählerstände | ✅ |
| Stromzähler mit IR-Lesekopf (EDL21/SML) | edl21 | `edl21/meter` | Netzbezug und Einspeisung am Stromzähler, Zählerstände; viele Zähler senden die Leistung erst nach Eingabe der PIN vom Netzbetreiber | 🧪 |
| HomeWizard P1 Meter | homewizard | `homewizard/p1` | Netzbezug und Einspeisung am P1-Port des Zählers, Zählerstände | 🧪 |
| powerfox poweropti | powerfox (Cloud) | `powerfox/poweropti` | Netzbezug und Einspeisung am Stromzähler (etwa minütlich), Zählerstände | 🧪 |
| inexogy (Discovergy) | discovergy (Cloud) | `discovergy/meter` | Netzbezug und Einspeisung am Stromzähler, Zählerstände | 🧪 |
| Netzzähler von Hand | beliebige Sensoren | `generic/grid_meter` | Netzleistung mit Vorzeichen oder Bezug und Einspeisung getrennt, Zählerstände | 🧪 |

Erkennt Sunflow deinen Zähler nicht (z. B. Tibber über die Cloud, DSMR, P1 Monitor, Shelly 3EM
oder ein Template-Sensor), legst du ihn unter „Geräte → Zähler → Netzzähler von Hand“ an: entweder
eine Netzleistung mit Vorzeichen (Bezug positiv; wahlweise Einspeisung positiv) oder Bezug und
Einspeisung als zwei Sensoren, dann rechnet Sunflow Bezug minus Einspeisung. Ein Zähler hat
Vorrang vor der Netzmessung des Wechselrichters; unter „Bilanzprüfung“ lässt sich die Quelle ändern.

## Wallboxen und Ladepunkte

| Gerät | Anbindung | Profil | Steuerung | Phasenumschaltung | Fahrzeug-Freigabe | Messen geprüft | Steuern geprüft |
|---|---|---|---|---|---|---|---|
| evcc-Ladepunkt (Wallbox oder Heizstab) | evcc_intg | `evcc_intg/loadpoint` | Modus „Sofort“, „PV“, „Aus“; evcc folgt dem PV-Überschuss selbst | durch evcc selbst (im PV-Modus) | – | ✅ | – (noch nicht bestätigt) |
| go-e Charger mit API v2 (Gemini, HOME+, HOMEfix) | goecharger_mqtt | `goecharger_mqtt/charger` | Laden/Stopp, Ladestrom nach PV-Überschuss (amp, höchstens alle 3 min) | 1 ↔ 3 Phasen durch Sunflow (Schwellen, Verzögerungen, Mindestabstand, Tageslimit in den Kenndaten) | Ladekarte des erkannten Fahrzeugs | ✅ | 🧪 (Phasen), sonst – (noch nicht bestätigt) |
| go-e Charger mit API v1 (HOME, ältere Firmware) | goecharger (HTTP) | `goecharger/charger` | Laden erlauben/sperren, Ladestrom minütlich nach PV-Überschuss (amx, ohne Flash-Schreiben) | – (feste Phasen in den Kenndaten) | – | – (noch nicht bestätigt) | – (noch nicht bestätigt) |

Phasenumschaltung (go-e, Kenndaten der Wallbox): Bei geplanter fester Leistung (z. B. aus dem
Netz) wählt Sunflow die Phasen sofort. Beim PV-Überschuss entscheidet das Mittel der letzten
Minuten: hoch auf 3 Phasen erst nach der Hochschalt-Verzögerung und nur, wenn der Plan auch für
die nächste Viertelstunde genug erwartet; herunter schneller, damit kein Netzstrom fließt.
Mindestabstand und Tageslimit verhindern Pendeln bei wechselhaftem Wetter. Beim Zurückgeben
bekommt die Wallbox ihren vorherigen Phasenmodus wieder.

Ladestrom der go-e: Sunflow setzt den Ladestrom „Requested current“ (`amp`), wie evcc bei
API v2 – den temporären Strom `amx` übernimmt die Firmware 56.x nicht. Weil die go-e diesen Wert
behält, ändert Sunflow ihn höchstens alle 3 Minuten (Kenndatum `current_interval_min`) und stellt
den vorher eingestellten Strom wieder her, sobald die Wallbox selbst regelt oder zurückgegeben wird.
Den höchsten Strom der go-e übernimmt Sunflow aus ihrem eingestellten Maximalstrom
(`ama`, z. B. 32 A bei einer 22-kW-Wallbox), in den Kenndaten überschreibbar. Die höchste
Ladeleistung des Autos einphasig und dreiphasig (Einstellungen → Fahrzeuge, z. B. Fiat 500e:
7,4 kW / 11 kW) begrenzt den Strom zusätzlich und geht in die Planung ein.

12-V-Batterie schützen (Einstellungen → Fahrzeuge): Manche Autos, z. B. der Fiat 500e, entladen
ihre 12-V-Bordnetzbatterie, wenn sie lange angesteckt bleiben, ohne zu laden. Mit einem
Spannungssensor aus der Integration des Autos überwacht Sunflow sie, solange das Auto an der
Wallbox steckt. Unter der eingestellten Grenze (Vorgabe 12,2 V) lädt die Wallbox für die
eingestellte Zeit (Vorgabe 5 min) mit dem kleinsten Strom und – wenn sie umschalten kann –
einphasig; dabei lädt das Auto auch die 12-V-Batterie nach. Danach gilt wieder der Plan. Frühestens
nach der Pause (Vorgabe 60 min) beginnt es erneut, damit ein selten aktualisierter Sensor nicht
ständig neu auslöst. Lädt die Wallbox nach Plan ohnehin, bleibt es dabei. Ein evcc-Ladepunkt
lädt dafür im Modus „Min+PV“. Wirkt nur, solange Sunflow die Wallbox steuert.

## Wärmepumpen

| Gerät | Anbindung | Profil | Messen | Steuern | Voraussetzungen | Messen geprüft |
|---|---|---|---|---|---|---|
| Rotex/Daikin HPSU | ESPHome mit der Komponente `daikin_rotex_can` (CAN-Bus) | `esphome/rotex_hpsu_can` | Vorlauf, Vorlauf-Spitze, Rücklauf, wirksamer Vorlauf-Soll, Wärmeleistung, Außentemperatur, Betriebsmodus, Betriebsart, Bypass-Ventil, Kompressor, Fehlercode | „Nur Lernen“: Vorlauf-Ziel berechnet; „Lernen + Regeln“: Max T-Vorlauf und Raumthermostat-Sperre | Firmware mit Manifest und `tv_max` | 🧪 |

Sunflow erkennt die HPSU nicht an Entitätsnamen – die sind in ESPHome frei wählbar und übersetzt –,
sondern am **Manifest**, das die Komponente als ESPHome-Aktion (`…_rotex_can_manifest`) mitliefert.
Andere ESPHome-Geräte erscheinen deshalb nicht unter den erkannten Geräten. Beim Übernehmen
speichert Sunflow das Home-Assistant-Gerät, dessen MAC-Adresse und die Komponente (CAN).

- **Zuordnung:** Den Knoten findet Sunflow über die Entitäten des Manifests. Haben mehrere ESPHome-Geräte
  sie alle, entscheidet der Gerätename: `HPSU.CAN` passt zur Aktion `hpsu_can_rotex_can_manifest`.
  Passt kein oder mehr als ein Gerät, ordnet Sunflow keines zu und meldet das in der Zusammenfassung.
- **Kompatibilität:** Bei jedem Laden prüft Sunflow jede Rolle des Profils: Schlüssel vorhanden,
  passender Typ (z. B. Auswahl), Einheit, alle Auswahlwerte, auf die es reagiert, und beim
  Max T-Vorlauf, dass der Bereich die Grenzen abdeckt und die Schrittweite höchstens 0,5 K beträgt
  (ältere Firmware mit ganzen Grad: Komponente aktualisieren). Fehlt eine Pflichtrolle oder
  passt sie nicht, gilt das Gerät als **nicht kompatibel**: Sunflow nutzt es nicht und nennt den
  Grund in der Zusammenfassung und im Protokoll (z. B. „Schlüssel max_target_flow_temp fehlt“).
  Fehlt eine optionale Rolle, fehlt nur diese Funktion.
- **Neu laden:** nach dem Flashen (neue Firmware-Version), wenn der Knoten nach einem Ausfall wieder
  erreichbar ist, und sonst stündlich. Hat sich das Manifest geändert, steht das einmal im
  Protokoll, ebenso jede Änderung der Kompatibilität.
- **Neu angelegte Integration:** Findet Sunflow das Home-Assistant-Gerät nicht mehr, sucht es eines
  mit derselben MAC-Adresse und ordnet es neu zu (Eintrag im Protokoll).
- **Profilversion:** Ist das Profil neuer als beim Übernehmen, weist die Zusammenfassung darauf hin.
  Version 2 ergänzt den Außenfühler des Außengeräts (TA2, optional), Version 3 die ESP-Regelung
  „Vorlauf Soll Geregelt“ (optional; nicht eingerichtet ist der Normalfall und wird nicht als fehlend gemeldet).
- **Zustände und Aufzeichnung:** Sunflow liest jede Minute Betriebsmodus, Betriebsart, HK-Funktion,
  Bypass-Ventil, Raumthermostat und Fehlercode und speichert je 5 Minuten die Mittelwerte mit den
  Zuständen (Tabelle `heat_pump_samples`, ein Jahr lang; im Export enthalten). Als **gültiger
  Heizbetrieb** zählt nur: Modus Heizen/Absenken/Automatik, Betriebsart Heizen, Bypass-Ventil seit
  10 Minuten auf 0 %, kein Nachlauf (10 Minuten nach Warmwasser oder Abtauen), keine
  Raumthermostat-Sperre, keine Störung (Fehlercode mit E; Warnungen mit W zählen nicht), die HPSU
  begrenzt den Vorlauf nicht selbst (bei „Fest“: Vorlauf-Soll mehr als 0,5 K unter Max T-Vorlauf,
  länger als 5 Minuten, z. B. T-Vorlauf Nacht) und das Gerät ist erreichbar und kompatibel. Nur dann
  wird später geregelt und gelernt. Das Protokoll nennt unbekannte Zustände, Beginn und Ende einer
  Störung, die Sperre an und aus und den Beginn einer Begrenzung durch die HPSU; den aktuellen Stand
  zeigt `GET /api/v1/heat_pump/status` und Einstellungen → Wärmepumpe. Sunflow schreibt dabei nichts.
- **Modus Aus / Nur Lernen / Lernen + Regeln:** Aus Raum-Soll (Thermostat „Haus“) und Raum-Ist berechnet
  Sunflow ein Vorlauf-Ziel und zeigt es an (Einstellungen → Wärmepumpe, Attribute des Thermostats). Bei
  „Nur Lernen“ (Standard) wird nichts an die Wärmepumpe geschrieben; „Lernen + Regeln“ schaltet das
  Schreiben ein (Einstellungen → Wärmepumpe); bei „Aus“ lernt und zeichnet Sunflow nichts auf.

Den UART-Knoten (`daikin_rotex_uart`) als Begleiter und die Vorlaufregelung unterstützt Sunflow in
einem späteren Schritt.

## Eigene Verbraucher

Geräte, die Sunflow nicht von selbst erkennt, legst du in den Einstellungen unter
„Geräte → Eigene Verbraucher“ an. Leistung, Zähler und Temperatur sind optional.

| Art | Profil | Steuerung | Typische Geräte | Steuern geprüft |
|---|---|---|---|---|
| Ein/Aus | `generic/switch` | Schalter (switch, input_boolean) | Shelly, Steckdose, E-Bike-Lader | 🧪 |
| Leistungsstufen | `generic/stepped` | Auswahl (select) mit Stufen in W/kW | ESPHome-Heizstab mit Stufen | 🧪 |
| Relais je Phase | `generic/relays` | 1–3 Schalter, je einer pro Phase, mit gleicher Leistung | Heizstab mit 1–3 Heizelementen | 🧪 |
| Ziel-Stromstärke | `generic/current` | Zahl (number) in A, optional Schalter | Wallbox oder Heizstab mit einstellbarem Strom | 🧪 |
| Nur messen | `generic/meter` | keine – Leistungsmessung (sensor), optional Zähler | Herd, Waschmaschine, Trockner, Spülmaschine | – |

„Nur messen“ schaltet Sunflow nie. Es lernt aus den Stundenwerten der letzten 8 Wochen, wie viel
das Gerät am Tag braucht, an wie vielen Tagen es läuft und zu welcher Stunde meist (werktags und am
Wochenende getrennt); zu sehen unter Analyse → Lernen. Auf die Planung wirkt es noch nicht.

### Heizstab mit Relais je Phase

„Relais je Phase (1–3)“ ist ein Heizstab mit bis zu drei Heizelementen, je eines über ein eigenes
Relais (Shelly, Schütz) geschaltet. Angegeben werden die Relais für L1–L3 (eines reicht) und die
Leistung eines Elements. Sunflow schaltet so viele Relais ein, wie in die geplante Leistung
passen (bei 3 × 2 kW also 2, 4 oder 6 kW), und wechselt jeden Tag, welches Relais zuerst kommt,
damit sie gleichmäßig verschleißen.

### Akku laden (E-Bike, Werkzeugakkus)

Manche Verbraucher laden einen eigenen Akku und sind nur manchmal angesteckt, etwa das Ladegerät
eines E-Bikes an einer schaltbaren Steckdose. Unter „Eigene Verbraucher“ lässt sich dafür
**Akku laden** einschalten. Der Verbraucher braucht eine Leistungsmessung.

- **Ladebereit** ist er, wenn die optionale Entität „angesteckt“ an ist. Ohne sie prüft Sunflow es
  mit einer **Probeladung**: Nach dem Auslöser (z. B. die Gartenhaustür wird verriegelt, Zustand
  `locked`; leer heißt bei jeder Änderung) und der eingestellten Wartezeit schaltet es den
  Verbraucher kurz ein. Zieht er dabei mindestens die Schwelle, ist er ladebereit. Ohne
  Ladestand-Entität gilt dafür **Ladebedarf ab**: Ein voller Akku, der angesteckt blieb, zieht nur
  wenig für die Selbstentladung (Erhaltungsladung) – das zählt nicht als ladebereit, und auch
  eingeschaltet gilt er als voll, solange er nie mehr zieht. Ein Wert knapp unter der Leistung zu
  Beginn einer Ladung passt (beim E-Bike z. B. 80 W, wenn es mit 110–145 W lädt). Unter
  **Steuerung** lässt er sich auch von Hand als **Jetzt eingesteckt** (bzw. „Nicht mehr
  eingesteckt“) melden.
- **Eingeplant** wird er nur, solange er ladebereit ist, mit PV-Überschuss oder günstigem Strom und
  wie gewohnt bis „Fertig bis“. Bis dahin ist er ausgeschaltet.
- **Wie viel**: mit Ladestand-Entität und Akkukapazität aus dem Ladestand, sonst mit der Energie je
  Ladung. Den Startwert ersetzt Sunflow durch das Mittel der letzten Ladungen, sobald es sie gemessen
  hat. Ohne Ladestand plant es 25 % Zeit als Reserve ein, falls eine Ladung länger dauert.
- **Am Stück**: Ein Akku lädt möglichst in einem Block, nicht in Stücken (schont ihn, gerade bei
  Kälte). Läuft die Ladung schon, lädt Sunflow bei gleichem Preis einfach weiter.
- **Fenster zu Ende, aber nicht voll**: Sunflow schaltet am Ende des geplanten Fensters ab, der
  Verbraucher bleibt aber ladebereit. Eingeplant wird dann nur noch der Rest der Ladung (mindestens
  ein Fünftel), bis „Fertig bis“ bzw. im nächsten günstigen Fenster.
- **Voll oder abgesteckt**: Bleibt die Leistung beim Laden für die eingestellte Zeit unter der
  Schwelle, schaltet Sunflow ab. Erst der nächste Auslöser bzw. das nächste Anstecken macht ihn wieder
  ladebereit.
- Geschaltet wird nur, wenn Sunflow den Verbraucher steuert (Steuerung → Sunflow steuert).

## Ein Gerät fehlt oder ist geprüft?

Wer ein Gerät aus der Tabelle an echter Hardware geprüft hat, meldet das bitte mit
Integrations-Version und Gerätemodell. Danach wird der Prüfstand hier geändert.
Ein neues Gerät braucht ein Profil in `profiles/` (Schema: `schema/profile.schema.json`).
Die vorhandenen Profile zeigen, wie Rollen, Aktionen und Grenzen beschrieben werden.
