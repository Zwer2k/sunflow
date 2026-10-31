# Optimierer

Sunflow zeigt Modul-Optimierer so, wie die Module auf dem Dach liegen: In der
Live-Ansicht öffnet ein Klick auf einen PV-String (Zahl am Kreis = Anzahl Optimierer)
seine Module mit den aktuellen Werten. Welche Werte erscheinen (Leistung, Spannung und
Strom am Modul bzw. Ausgang, Temperatur, PWM), lässt sich dort ein- und ausblenden; die
Wahl merkt sich der Browser. Hellere Module liefern weniger als das beste des Strings.

## Quellen

| Hersteller | Integration | Werte | Zuordnung zum String |
|---|---|---|---|
| Huawei (SUN2000-…-P) | [Huawei Solar](https://github.com/wlcrs/huawei_solar) | Leistung, Spannung/Strom Ein- und Ausgang, Temperatur | Optimierer sind Unter-Geräte des Wechselrichters. Ist nur ein String in Betrieb, automatisch; sonst in den Einstellungen wählen (die Integration gibt den String nicht weiter). |
| Tigo TS4 | [Tigo RS485](https://github.com/Zwer2k/ha-tigo-rs485) | Leistung, Spannung Ein-/Ausgang, Strom, Temperatur, PWM | Über das Label aus der Tigo-Integration: „A1“ = String A (String 1 des Wechselrichters), Modul 1. Der Wechselrichter wird vorgeschlagen (der einzige ohne eigene Optimierer) oder gewählt. |

## Einstellungen → Optimierer

- **Quellen:** was gefunden wurde; für Tigo der Wechselrichter, an dem sie hängen.
- **Dach je String:** Module auf freie Plätze ziehen oder antippen und dann den Platz
  antippen (Handy); auf ein anderes Modul gezogen tauschen beide. „Vorschlag
  wiederherstellen“ setzt String und Plätze auf den Vorschlag zurück.
- **Zuordnung:** String und Name je Optimierer; „automatisch“ folgt dem Vorschlag.
