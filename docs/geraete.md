# Unterstützte Geräte

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

| Gerät | Anbindung | Profil | Steuerung | Fahrzeug-Freigabe | Messen geprüft | Steuern geprüft |
|---|---|---|---|---|---|---|
| evcc-Ladepunkt (Wallbox oder Heizstab) | evcc_intg | `evcc_intg/loadpoint` | Modus „Sofort“, „PV“, „Aus“; evcc folgt dem PV-Überschuss selbst | – | ✅ | – (noch nicht bestätigt) |
| go-e Charger (Gemini, HOME) | goecharger_mqtt | `goecharger_mqtt/charger` | Laden/Stopp, Ladestrom minütlich nach PV-Überschuss (amx, ohne Flash-Schreiben) | Ladekarte des erkannten Fahrzeugs | ✅ | – (noch nicht bestätigt) |

## Eigene Verbraucher

Geräte, die Sunflow nicht von selbst erkennt, legst du in den Einstellungen unter
„Geräte → Eigene Verbraucher“ an. Leistung, Zähler und Temperatur sind optional.

| Art | Profil | Steuerung | Typische Geräte | Steuern geprüft |
|---|---|---|---|---|
| Ein/Aus | `generic/switch` | Schalter (switch, input_boolean) | Shelly, Steckdose, E-Bike-Lader | 🧪 |
| Leistungsstufen | `generic/stepped` | Auswahl (select) mit Stufen in W/kW | ESPHome-Heizstab mit Stufen | 🧪 |
| Ziel-Stromstärke | `generic/current` | Zahl (number) in A, optional Schalter | Wallbox oder Heizstab mit einstellbarem Strom | 🧪 |

## Ein Gerät fehlt oder ist geprüft?

Wer ein Gerät aus der Tabelle an echter Hardware geprüft hat, meldet das bitte mit
Integrations-Version und Gerätemodell. Danach wird der Prüfstand hier geändert.
Ein neues Gerät braucht ein Profil in `profiles/` (Schema: `schema/profile.schema.json`).
Die vorhandenen Profile zeigen, wie Rollen, Aktionen und Grenzen beschrieben werden.
