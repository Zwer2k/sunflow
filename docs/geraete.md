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

## Wärmepumpe

Wärmepumpen haben unter Einstellungen einen eigenen Bereich **Wärmepumpe** (Profil
`generic/heat_pump`). Dort stehen Messung, Ansteuerung, der Heizstab der Wärmepumpe und die Heizperiode.

**Messung** (alles optional, gebraucht wird der Stromverbrauch oder Wärme und COP): Strom gesamt,
je Phase (1–3 Leistungs- oder Stromsensoren, Strom × Spannung) oder über einen Zähler, dazu
Wärmeleistung und COP. Aus zwei von Strom, Wärme und COP berechnet Sunflow den dritten Wert und
zeigt, ob ein Wert gemessen oder berechnet ist; passt der COP nicht (unter 1 oder über 7, oder
gemessener und berechneter COP weichen stark ab), weist es darauf hin. „Vorschläge aus Gerät“
füllt die Felder aus den Entitäten der Wärmepumpe vor. Die Wärmepumpe bleibt Teil des
Hausverbrauchs, dessen Prognose die Außentemperatur schon berücksichtigt.

**Ansteuerung** – Sunflow empfiehlt einen Zustand (normal, verstärkt, gesperrt, Anlaufbefehl), die
Regelung der Wärmepumpe entscheidet, was sie daraus macht:

| Art | Entitäten | Zustände |
|---|---|---|
| SG-Ready über zwei Relais | Kontakt 1 (Sperre), Kontakt 2 (verstärkt), je wahlweise als Öffner | alle |
| SG-Ready nur Kontakt 2 | Kontakt 2 – wenn Kontakt 1 die Sperre des Netzbetreibers ist (§14a EnWG) | normal, verstärkt |
| SG-Ready über eine Auswahl | select oder input_select; die Optionen werden über ihre Namen zugeordnet | alle, die eine Option haben |
| Sollwert anheben | Zahl (z. B. Warmwasser-Sollwert), normaler Wert, ± Kelvin | normal, verstärkt, gesperrt |
| PV-Überschuss melden | Zahl in W, die die Wärmepumpe selbst auswertet | normal, verstärkt |
| Boost-Schalter | Schalter der Integration (z. B. Warmwasser-Einmalladung) | normal, verstärkt |

Wann: **verstärkt** ab einem geplanten Überschuss (Einspeisung oder abgeregelte PV, Standard
1,5 kW) und wahlweise in den N günstigsten Stunden des Tages; **gesperrt** nur, wenn eingeschaltet,
in den N teuersten Stunden, höchstens 2 h am Stück und 4 h am Tag; **Anlaufbefehl** wahlweise bei
negativem Preis. Jeder Zustand bleibt mindestens 20 min (schont den Verdichter); bezieht das Haus
beim Verstärken auf Überschuss doch Strom aus dem Netz, geht es zurück auf normal. Ohne aktuellen
Plan, beim Abgeben der Steuerung und über den Watchdog der Integration gilt „normal“.

Gesteuert wird erst, wenn die Wärmepumpe unter Steuerung eingeschaltet ist; bis dahin zeigt
Sunflow im Bereich Wärmepumpe, was es setzen würde.

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

### Heizstab einer Wärmepumpe

Der Heizstab wird mit der Wärmepumpe eingerichtet (Einstellungen → Wärmepumpe, Gruppe
**Heizstab**). Die Wärmepumpe macht aus 1 kWh Strom 3–4 kWh Wärme, der Heizstab nur 1 kWh; er soll
deshalb nur laufen, wenn es sich lohnt oder die Wärmepumpe nicht reicht. Zwei Varianten:

**1. Eingebaut – über die Integration der Wärmepumpe.** Die Zusatz- oder Notheizung in der
Wärmepumpe lässt sich bei vielen Herstellern nur sperren, freigeben oder in der Leistung begrenzen;
ob sie heizt, entscheidet die Wärmepumpe. Sunflow kennt vier Formen:

| Die Integration bietet | Beispiele |
|---|---|
| Sperr-Schalter (an = gesperrt) | Bosch, Buderus, Junkers über EMS-ESP: „Zusatzheizer deaktivieren“ (`auxheateroff`) |
| Freigabe-Schalter (an = darf heizen) | NIBE (`nibe_heatpump`): „Allow additive heating“ / „Permit additional heat“ |
| Auswahl (frei/gesperrt oder kW-Stufen) | Panasonic über HeishaMon: „DHW/Room Heater State“; EMS-ESP: `maxheat…` in kW-Stufen |
| Höchstleistung als Zahl | NIBE: „Max int add. power“ in kW |

Modi: In der Heizperiode **nur bei Bedarf** (Standard) – gesperrt, außer es ist kälter als die
eingestellte Grenze (etwa der Bivalenzpunkt, Standard −5 °C), der Referenzraum liegt unter dem
Komfortband (die Wärmepumpe reicht nicht oder ist gestört), der Strompreis ist negativ oder du
gibst ihn frei („Heizstab 24 h freigeben“) – oder **Wärmepumpe entscheidet**. Ohne aktuellen Plan
bleibt er frei. Im Sommer zusätzlich
**bei PV-Überschuss** (Standard). Ohne Außentemperatur (von der Wärmepumpe oder unter Lernquellen)
sperrt Sunflow nie. Die Einstellung liegt meist im Flash-Speicher der Wärmepumpe: Sunflow ändert
sie höchstens alle 60 min (einstellbar), schreibt nur bei Abweichung und zählt die Schreibvorgänge
gegen das Tageslimit; nach 6 Wechseln am Tag sperrt es nicht mehr neu, damit eine Freigabe immer
noch geschrieben werden kann. Gibt Sunflow die Steuerung ab, stellt es den normalen Wert wieder her.

**2. Eigener Heizstab – als Verbraucher angelegt.** Ein Heizstab außerhalb der Wärmepumpe, meist im
Warmwasser- oder Pufferspeicher, wird zuerst als eigener Verbraucher mit Art „Wärme“ angelegt (z. B.
„Relais je Phase (1–3)“ oder „Leistungsstufen“) und dann in der Gruppe Heizstab gewählt.

Die Wärmepumpe macht aus 1 kWh Strom 3–4 kWh Wärme, der Heizstab nur 1 kWh. Deshalb läuft der
zugeordnete Heizstab **nach Jahreszeit**:

| | Empfohlen (Standard) | Wahlweise |
|---|---|---|
| Heizperiode (eigener Heizstab) | Aus – der PV-Überschuss geht an die Wärmepumpe (Verstärken) | Nur PV-Überschuss, Automatisch |
| Sommer | Nur PV-Überschuss – die Wärmepumpe hält ihren Sollwert, der Heizstab heizt mit dem restlichen Überschuss darüber hinaus (z. B. auf 60–65 °C) | Aus, Automatisch (nach Kosten) |

Hohe Speichertemperaturen über den Heizstab zu erreichen schont die Wärmepumpe, deren COP bei
hohen Temperaturen sinkt. Bis zu welcher Temperatur der Heizstab heizt, steht unter Verbraucher
(Zieltemperatur). Der Modus der Jahreszeit ersetzt die Strategie des Heizstabs; ohne Zuordnung zu
einer Wärmepumpe gilt er nicht. Reicht die Wärmepumpe im Winter
nicht, unter Steuerung „Schnellstmöglich“ wählen: Das gilt bis zur gewählten Zeit, höchstens aber
für die eingestellten Stunden (Standard 24 h), damit der Heizstab nicht den Rest des Winters
läuft. Über die Aktion `sunflow_ems.set_consumer` geht das auch per Automation.

Die **Heizperiode** wird erkannt

- nach **Außentemperatur**: Liegt das Tagesmittel eines Sensors drei Tage in Folge unter 15 °C,
  beginnt sie; drei Tage darüber beenden sie (Schwelle und Tage einstellbar),
- nach **Datum** (z. B. 01.10. bis 30.04.) oder
- aus einer **Entität**, etwa dem Sommer-/Winterbetrieb der Wärmepumpe.

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
