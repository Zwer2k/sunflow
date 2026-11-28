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
| SolarEdge StorEdge / Home Hub | solaredge_modbus (Core) | `solaredge_modbus/storedge` | PV, Speicher, Netz (mit Zähler), Verbrauch (aus AC-Leistung und Zähler berechnet), Zähler | alle | – | Speicher-Fernsteuerung (wird beim Übernehmen gesetzt) | 🧪 | 🧪 |
| SolarEdge StorEdge / Home Hub | solaredge_modbus_multi (HACS, WillCodeForCats) | `solaredge_modbus_multi/storedge` | PV, Speicher, Netz (mit Zähler), Verbrauch (aus AC-Leistung und Zähler berechnet), Zähler | alle | – | In der Integration „Batterien erkennen“ und „Storage Control“ aktivieren (der Zähler wird standardmäßig erkannt); Laden aus dem Netz braucht eine AC-Ladeerlaubnis ≠ „Disabled“ | 🧪 | 🧪 |
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
| Verbrauchszähler von Hand | beliebige Sensoren (eigener Zähler, Template) | `generic/load_meter` | Hausverbrauch, Zählerstand | 🧪 |

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
| Peblar Home / Business | peblar (Core) | `peblar/charger` | Laden/Stopp, Ladestrom minütlich | 1 ↔ 3 Phasen durch Sunflow (falls die Installation es erlaubt) | – | – | – |
| NRGkick Gen2 | nrgkick (Core) | `nrgkick/charger` | Laden/Pause, Ladestrom minütlich (0,1 A) | 1 ↔ 3 Phasen durch Sunflow | – | – | – |
| OpenEVSE | openevse (Core) | `openevse/charger` | Laden/Stopp über Override, Ladestrom minütlich | – | – | – | – |
| V2C Trydan | v2c (Core) | `v2c/charger` | Laden/Pause, Ladestrom minütlich; dynamischen Modus der Wallbox ausschalten | – | – | – | – |
| Wallbox Pulsar, Commander, Copper | wallbox (Core, Cloud) | `wallbox/charger` | Laden/Pause, Ladestrom höchstens alle 5 min (Cloud) | – | – | – | – |
| Lektrico | lektrico (Core) | `lektrico/charger` | Ladestrom über die dynamische Grenze minütlich, 0 A stoppt | 1 ↔ 3 Phasen durch Sunflow | – | – | – |
| Easee Home / Charge | easee (HACS, Cloud) | `easee/charger` | Laden/Pause, dynamische Grenze höchstens alle 5 min (Cloud) | 1 ↔ 3 Phasen durch Sunflow (Cloud-Einstellung, nur bei Änderung) | – | – | – |
| KEBA KeContact P30 / P40 | keba (HACS, dannerph) | `keba/charger` | Laden/Stopp, temporärer Ladestrom (currtime) minütlich | – | – | – | – |
| Heidelberg Energy Control, Amperfied connect | heidelberg_energy_control (HACS) | `heidelberg_energy_control/charger` | Laden/Stopp, Ladestrom minütlich (RAM); Standby der Box ausschalten | – | – | – | – |
| Webasto / Ampure Unite, Vestel EVC04 | unite_ev_charger (HACS) | `unite_ev_charger/charger` | Laden/Stopp, Ladestrom minütlich; Steuermodus der Integration „external“ | 1 ↔ 3 Phasen durch Sunflow | – | – | – |
| cFos Power Brain Wallbox | cfos_powerbrain (HACS) | `cfos_powerbrain/charger` | Laden/Stopp, Ladestrom minütlich (RAM) | 1 ↔ 3 Phasen durch Sunflow | – | – | – |
| Mennekes AMTRON 4You / 4Business | mennekes_amtron (HACS) | `mennekes_amtron/charger` | HEMS-Strom minütlich, 0 A stoppt; Kommunikations-Timeout der Box 300 s | – | – | – | – |
| SMA EV Charger | smaev (HACS) | `smaev/charger` | Laden/Stopp über den Lademodus, Ladestrom höchstens alle 5 min | – | – | – | – |
| Fronius Wattpilot | wattpilot (HACS) | `wattpilot/charger` | Laden/Stopp (frc), Ladestrom (amp) höchstens alle 3 min | 1 ↔ 3 Phasen durch Sunflow | – | – | – |
| Jede OCPP-1.6-Wallbox (ABB, Alfen, Easee, go-e, KEBA, Wallbox, Zaptec …) | ocpp (HACS) | `ocpp/chargepoint` | Höchststrom als Ladeprofil minütlich, 0 A pausiert | – | – | – | – |

Ladestand des Autos: Sunflow nimmt ihn aus der Integration des Autos (Einstellungen →
Fahrzeuge). Hat das Auto keinen solchen Sensor, gilt der Ladestand, den die Wallbox meldet. Das
können nur Wallboxen, die ihn vom Auto bekommen: evcc, EEBUS und OCPP-Wallboxen mit ISO 15118
(Messwert „SoC“ in der OCPP-Integration aktivieren). Gewöhnliche AC-Wallboxen kennen ihn nicht.

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

### Direkt über Modbus TCP

Für Wallboxen ohne passende Integration spricht Sunflow die Wallbox selbst an (Einstellungen →
Geräte → Direkt anbinden). Die Register stammen aus den Treibern von evcc. Hält eine Wallbox
nur mit regelmäßigen Schreibzugriffen den Fernmodus, schreibt Sunflow sie, solange es steuert;
danach fällt die Wallbox auf ihren sicheren Strom zurück.

| Gerät | Profil | Steuerung | Phasenumschaltung | Voraussetzungen | Messen geprüft | Steuern geprüft |
|---|---|---|---|---|---|---|
| ABB Terra AC | `modbus/abb_terra_ac` | Ladestrom minütlich (mA), 0 A pausiert | – | Modbus TCP in der TerraConfig-App freigeben | – | – |
| Alfen Eve Single / Double | `modbus/alfen_eve` | Ladestrom minütlich, alle 20 s erneuert (Gültigkeitsdauer) | 1 ↔ 3 Phasen durch Sunflow | Lizenz „Active Load Balancing“, Datenquelle „Energy Management System“ | – | – |
| Webasto Next / Ampure Unite (neu) | `modbus/webasto_next` | Ladestrom minütlich, Lebenszeichen alle 5 s | – | – | – | – |
| Vestel EVC04, Webasto/Ampure Unite, E.ON vBox | `modbus/vestel_evc04` | Ladestrom minütlich, Lebenszeichen alle 5 s | 1 ↔ 3 Phasen durch Sunflow (Firmware ≥ 3.187) | – | – | – |
| KEBA KeContact P30 c/x | `modbus/keba_p30` | Laden/Stopp, Ladestrom minütlich (mA), Failsafe alle 20 s | – | Modbus per DIP-Schalter oder Weboberfläche freigeben | – | – |
| Bender CC612/CC613 (Mennekes AMTRON Professional, Webasto Live, Garo, Ensto …) | `modbus/bender_cc613` | Ladestrom minütlich, 0 A pausiert | – | „Modbus TCP Server for energy management systems“ freigeben | – | – |
| Mennekes AMTRON Xtra / Premium (HCC3) | `modbus/mennekes_hcc3` | Ladestrom minütlich, 0 A stoppt | – | – | – | – |
| Phoenix Contact EV-ETH (Wallbe, ESL Walli, E3/DC Easy Connect, Compleo Pro) | `modbus/phoenix_ev_eth` | Laden/Stopp (Coil), Ladestrom minütlich | – | DIP-Schalter 10 an | – | – |
| Compleo eBox Solo / Duo, SENEC.Wallbox, Neoom | `modbus/compleo_ebox` | Leistungsgrenze in 100-W-Schritten minütlich | – | – | – | – |
| cFos Power Brain Wallbox (direkt) | `modbus/cfos_powerbrain` | Laden/Stopp, Ladestrom minütlich (0,1 A) | 1 ↔ 3 Phasen durch Sunflow | Port 4701 | – | – |
| Amperfied connect.home / connect.solar | `modbus/amperfied_connect` | Ladestrom minütlich (0,1 A), Standby aus | – | – | – | – |

### Über EEBUS

EEBUS-Wallboxen (Elli Charger Connect/Pro, Porsche, EVBox Livo, Kontron/Steca und andere) steuert
Sunflow über die eigene App „Sunflow EEBUS“. Sie läuft als einzige im Host-Netzwerk, weil EEBUS
Geräte per mDNS findet; Sunflow selbst bleibt im normalen App-Netz. Installieren, die angezeigte
SKI der App in der App der Wallbox eintragen, dann unter Geräte → Direkt anbinden „EEBUS Wallbox“
wählen und die gefundene Wallbox koppeln. Beim ersten Mal zeigt Sunflow einen Schlüssel, der in der
Konfiguration der EEBUS-App unter „token“ eingetragen wird: Ohne ihn antwortet die App niemandem,
so kann keine andere App die Wallbox steuern. Steht die Wallbox in einem anderen Netzsegment (VLAN),
in das mDNS nicht reicht, unter „Wallbox in einem anderen Netzsegment“ ihre SKI und IP eintragen.

| Gerät | Profil | Steuerung | Phasenumschaltung | Voraussetzungen | Messen geprüft | Steuern geprüft |
|---|---|---|---|---|---|---|
| EEBUS-Wallbox | `eebus/wallbox` | Ladestrom je Phase minütlich (OPEV), 0 A pausiert; Ladestand des Autos, wenn es ihn meldet | – | App „Sunflow EEBUS“ | – | – |

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
Modul-Optimierer haben eigene Profile in `profiles/optimizers/` (Schema:
`schema/optimizer.schema.json`), Speichermodule in `profiles/battery_modules/` (Schema:
`schema/battery_module.schema.json`). Sie nennen die Integration, woran ein Gerät als Optimierer bzw.
Modul erkannt wird und welche Entität welchen Wert liefert. Werte, die eine Integration nicht hat,
bleiben einfach weg.
