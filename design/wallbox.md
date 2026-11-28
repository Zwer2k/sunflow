# Wallbox-Standard

Stand: 28.11.2026

Sunflow kennt keine Wallbox-Marken. Die Steuerung arbeitet nur mit dem, was ein Profil
nach diesem Standard beschreibt. Eine neue Wallbox braucht deshalb nur ein Profil, keinen Code.
`tests/test_wallbox_standard.py` prüft jedes Wallbox-Profil gegen die Pflichtteile.

## Anbindung

| Weg | Profil | Beispiel |
|---|---|---|
| Home-Assistant-Integration | `profiles/loadpoints/<integration>/<name>.yaml`, Rollen über das Ende der `unique_id` | go-e, Peblar, Easee |
| Direkt über Modbus TCP oder HTTP | `profiles/loadpoints/direct/<name>.yaml` mit `connection` (Register bzw. JSON-Pfade) | ABB Terra AC, Alfen, KEBA |
| EEBUS (geplant) | wie direkt, über einen EEBUS-Dienst in der App (eebus-go), der SHIP/SPINE übernimmt | Elli, Porsche |

Gibt es für eine Wallbox eine gute HA-Integration, ist sie der erste Weg. Direkt angebunden wird,
was in HA fehlt oder dort nicht steuerbar ist. Eine Wallbox darf nur von einer Stelle gesteuert
werden: neben Sunflow nicht gleichzeitig evcc, openWB oder die Regelung der Integration selbst.

## Rollen

| Rolle | Pflicht | Bedeutung |
|---|---|---|
| `charge_power` | ja | Ladeleistung jetzt (W oder kW) |
| `connected` | ja¹ | Auto angesteckt: binary_sensor `on`, ein Text mit `on_states` oder ein Status, den ein Direktprofil mit `states` benennt |
| `charging` | nein | lädt gerade |
| `charged_energy` | nein | Energie der laufenden Ladung |
| `stored_current` | nein | Strom, den die Wallbox selbst behält; Sunflow stellt ihn beim Zurückgeben wieder her |
| `allowed_current` | nein | Strom, den die Wallbox jetzt zulässt (zeigt, ob der gesetzte Strom ankommt) |
| `transaction`, `card_required` | nein | Ladekarte: gelesene Karte und ob eine nötig ist |
| `phase_mode` | nein | aktuelle Phasenwahl |
| `vehicle_soc` | nein | Ladestand des Autos, meist aus der Integration des Autos zugeordnet |

¹ Nur ein Ladepunkt, der das Auto selbst verwaltet (evcc), darf ohne auskommen.

Teilt eine Integration einen Schlüssel zwischen Plattformen (Sensor und Schalter), nennt die
Rolle ihre Domäne (`domain: sensor`). Schlüssel werden so lang gewählt, dass sie nichts anderes
treffen (`charge_current_limit`, nicht `current`).

## Kenndaten

| Kenndatum | Pflicht | Bedeutung |
|---|---|---|
| `max_power` | ja | höchste Ladeleistung (W), wenn möglich aus der Wallbox abgeleitet |
| `min_current_a`, `max_current_a` | ja, wenn die Wallbox nach Strom geregelt wird | Grenzen je Phase (A) |
| `phases` | ja, wenn nach Strom geregelt | Phasen des Anschlusses, wenn die Wallbox nicht umschaltet |
| `current_interval_min` | ja, wenn die Wallbox den Strom dauerhaft speichert oder ihn über eine Cloud bekommt | kürzester Abstand zwischen zwei Stromänderungen |
| `phase_mode`, `phase_up_w`, `phase_down_w` und Verzögerungen | ja bei `phase_switch` | wann Sunflow die Phasen wechselt |
| `vehicle_capacity_wh`, `charge_efficiency_pct` | nein | Auto ohne eigene Fahrzeugdaten |

## Aktionen

`charge_control` beschreibt, was Sunflow tut:

| Aktion | Pflicht | Bedeutung |
|---|---|---|
| `charge` | ja | laden mit `{{ current_a }}` (A je Phase) oder `{{ power_w }}` (W) |
| `stop` | ja | nicht laden, Sunflow behält die Wallbox |
| `release` | ja² | zurück an die eigene Logik der Wallbox |
| `authorize` | nein | Ladevorgang für die Karte `{{ transaction }}` freigeben |
| `follow` | nein | die Wallbox folgt dem PV-Überschuss selbst (z. B. evcc „PV“) |
| `minimum` | nein | kleinste Leistung (12-V-Batterie des Autos) |

² Oder `remember`: ein Wert, den Sunflow beim Übernehmen merkt und beim Zurückgeben wiederherstellt.

`phase_switch` hat `one` und `three` und optional `remember`.

Laufzeitwerte in Schritten: `current_a`, `power_w`, `transaction`, `device_serial`,
`device_id_<Untergerät>`, `role_<Rolle>` (Wert der Rolle) und alle Kenndaten.

## Regeln

- **Stoppen ist nicht Zurückgeben.** `stop` hält das Auto an, bis Sunflow wieder laden will;
  `release` gibt die Wallbox ihrer eigenen Logik zurück (gespeicherter Strom, eigener Zeitplan).
- **Flüchtige Werte** (RAM, temporärer Strom) dürfen jede Minute geschrieben werden.
- **Dauerhaft gespeicherte Werte** (Flash, Einstellungen der Wallbox) bekommen `storage: eeprom`
  und einen `eeprom_guard`, oder für den Ladestrom `current_interval_min` (mindestens 3 min).
- **Cloud-Integrationen** schreiben selten: `current_interval_min` mindestens 5 min, bei
  Vorgaben des Herstellers mehr (Zaptec: 15 min).
- **Wachhund.** Viele Wallboxen fallen auf einen sicheren Strom zurück, wenn niemand mehr schreibt.
  Direkt angebunden hält `connection.keepalive` sie wach, solange Sunflow steuert, und hört beim
  Zurückgeben auf; fällt Sunflow aus, greift der Rückfall der Wallbox. Bei Integrationen übernimmt
  das die Integration; das Profil nennt den Rückfall im Kopfkommentar.
- **Kopfkommentar** jedes Profils: Integration bzw. Protokoll, Herkunft der Schlüssel oder Register,
  Vorzeichen und Einheiten, was dauerhaft gespeichert wird, Voraussetzungen (z. B. ein Modus der
  Integration) und „Not verified on hardware yet“, bis jemand es an echter Hardware geprüft hat.
