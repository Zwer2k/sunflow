# So plant Sunflow

**Deutsch** · [English](en/planning.md)

## Der Plan

Sunflow rechnet für jede Viertelstunde der nächsten bis zu 96 Stunden aus, was Hausspeicher und
steuerbare Verbraucher tun sollen. Dafür löst es ein lineares Optimierungsproblem (gemischt
ganzzahlig): Es sucht den Plan mit den geringsten Kosten, der alle Bedingungen erfüllt.

**Eingänge**

- Bezugspreis je Viertelstunde (siehe [Strompreise](strompreise.md)), Einspeisevergütung je Anlage.
- PV-Prognose je Fläche mit der gelernten Korrektur.
- Lastprognose für den Hausverbrauch ohne steuerbare Verbraucher.
- Ladestand, Kapazität, Leistungsgrenzen und Wirkungsgrade der Speicher.
- Ziele und Zeiten der Verbraucher: Auto bis 7 Uhr auf 80 %, Warmwasser bis zum Abend auf 55 °C usw.

**Kosten im Plan**

- Netzbezug zum Preis der Viertelstunde, abzüglich Einspeisevergütung.
- Verluste beim Laden und Entladen (Wirkungsgrad des Speichers und des Wechselrichters).
- Verschleiß je entladener kWh (Einstellungen → Planung → Verschleißkosten).
- Optional die Alterung bei hohem Ladestand.
- Aufschläge der Strategie und weiche Ziele wie „Netzdienlich“. Sie lenken den Plan, erscheinen
  aber nicht in den angezeigten Kosten.

Sunflow plant neu, sobald neue Preise oder Prognosen vorliegen, jede Viertelstunde, und nach jeder
Änderung in Steuerung oder Einstellungen.

## Strategien des Hausspeichers

| | Ausgewogen | Möglichst viel PV aufnehmen | Möglichst autark |
|---|---|---|---|
| Ziel | geringste Stromkosten | wie Ausgewogen, Platz für PV | so wenig Netzbezug wie möglich |
| Laden aus dem Netz | wenn der Preisunterschied Verluste und Verschleiß übersteigt | kaum vor sonnigen Tagen | nie (außer überfällige Vollladung) |
| Im Plan | Kosten | jede eingespeiste kWh zählt 5 ct weniger | jede kWh aus dem Netz zählt 1 € extra |
| Passt zu | dynamischen Tarifen und Zeittarifen | viel PV, wenig Speicher | festem Preis, Unabhängigkeit |

- **Ausgewogen** hält Energie für teure Stunden zurück und entlädt dort.
- **PV aufnehmen** unterscheidet sich von „Ausgewogen“ nur, wenn eingespeist würde, im Winter
  also kaum. Eine Einspeisebegrenzung kennt sie nicht; dafür gibt es „Netzdienlich“.
- **Autark** deckt jeden Bedarf sofort aus dem Speicher und gibt Verbrauchern bevorzugt
  PV-Überschuss. Bei Zeittarifen kostet sie mehr, weil günstiger Nachtstrom nicht gespeichert wird.

Die Strategie stellst du unter Einstellungen → Planung ein, vorübergehend unter Steuerung oder
mit der Aktion `sunflow_ems.set_strategy`.

## Lohnt sich Laden aus dem Netz?

Netzladen lohnt sich nur, wenn der spätere Preis den heutigen um mehr als Verluste und
Verschleiß übersteigt. Bei 72 % Wirkungsgrad im Zyklus und 6 ct Verschleiß je kWh muss Strom für
22 ct später mehr als etwa 36,5 ct kosten (22 / 0,72 + 6). Darum lädt Sunflow bei kleinen
Preisunterschieden nicht aus dem Netz. Das ist Absicht und kein Fehler.

## Speicher schonen

- **Ladegrenze im Alltag**: Höher als die Grenze lädt Sunflow nur für eine fällige Vollladung.
- **Voll laden alle … Tage**: zum Ausgleichen der Zellen, bevorzugt mit PV.
- **Spätestens nach … aus dem Netz**: Hat PV den Speicher so lange nicht gefüllt, lädt Sunflow
  ihn zur günstigsten Zeit aus dem Netz voll.
- **Alterung bei hohem Ladestand** (kalendarische Alterung): Zellen altern schneller, je länger
  sie voll stehen. Jede kWh über der Schwelle kostet je Stunde einen kleinen Betrag. Sunflow lädt
  dann möglichst kurz vor dem Bedarf, z. B. am Ende des günstigen Nachtfensters statt ab
  Mitternacht. Beispiel: 4 kWh über 80 % für 10 Stunden kosten bei 0,1 ct rund 4 ct.
- **Notstrom-Reserve**: In jeder Strategie bleibt dieser Ladestand für Stromausfälle frei.

## Verbraucher

Wallboxen, Warmwasser und eigene Verbraucher plant Sunflow im selben Rechenschritt wie den
Speicher. Je Verbraucher gilt eine Betriebsart:

- **Kostenoptimal**: PV-Überschuss oder günstige Zeiten, sodass das Ziel rechtzeitig erreicht wird.
- **Nur PV-Überschuss**: nie aus dem Netz oder dem Hausspeicher.
- **Schnellstmöglich** (in der Steuerung „Sofort“): sofort mit voller Leistung.
- **Pause** (nur in der Steuerung): gar nicht.

Die **Priorität** (Steuerung → Priorität) regelt, wer bei knappem Überschuss zuerst bekommt.
**Grenzen** lassen einen Verbraucher warten, bis höher stehende Speicher einen Stand erreicht haben.

**Autos**: Sunflow lernt Verbrauch, Ankunft und Abfahrt. Ist das einzige Auto einer Wallbox
unterwegs, plant es mit gelerntem Verbrauch und Ankunft schon ein, aber nur mit PV-Überschuss
oder günstigem Strom und ohne Pflicht. Ist das Auto zu Hause, aber nicht angesteckt, und bekommt
es bald Energie, erscheint der Hinweis „Einstecken lohnt sich“ (in Home Assistant als Ereignis
`sunflow_ems_plug_in_suggested`).

**Einmalige Lasten** wie Waschmaschine oder Trockner plant die Aktion `sunflow_ems.plan_load`
zum günstigsten Zeitpunkt ein (siehe [Sunflow-Integration](integration.md)).

## Lernen

- **PV-Prognose**: Sunflow vergleicht Prognose und Messung je Stunde und Bewölkung und lernt
  einen Korrekturfaktor, je Dachfläche, wenn ihr Strings zugeordnet sind. Neuere Tage zählen
  mehr (Halbwertszeit 30 Tage). „Neu lernen ab heute“ verwirft alte Werte einer Fläche.
- **Unsichere Prognose**: Mit „Vorsichtig“ plant Sunflow mehr mit dem schlechten Fall (Solcast
  p10), sodass es eher nachts günstig nachlädt, als bei wenig Sonne teuer nachzukaufen.
- **Hausverbrauch**: Bis genug Verlauf vorliegt, rechnet Sunflow mit Stundenmitteln, danach
  (ab 14 Tagen) mit einem Modell aus Uhrzeit, Wochentag und Temperatur. Steuerbare Verbraucher
  sind herausgerechnet, seltene Spitzen ab der eingestellten Grenze ausgeklammert.
- **Autos und Warmwasser**: Verbrauch je Stunde, Werktag und Wochenende getrennt.

## Preise jenseits der veröffentlichten

Dynamische Tarife kennen nur heute und ab Mittag morgen. Für die Stunden danach schätzt Sunflow den
Preis aus dem Verlauf der letzten zwei Wochen. Diese Werte prägen nur das Planende und werden
ersetzt, sobald die echten Preise da sind (siehe [Strompreise](strompreise.md)).

## Netzdienlich

Mit **Netzdienlich** versucht Sunflow, Einspeise- und Bezugsspitzen über eingestellten Grenzen zu
vermeiden. Dazu verschiebt es das Laden des Speichers und der Verbraucher. Jede kWh über der
Grenze zählt im Plan wie 20 ct Mehrkosten. Lässt sich eine Spitze nicht vermeiden, wird sie
zugelassen. Das ist ein weiches Ziel und kein Ersatz für eine Begrenzung im Wechselrichter.
