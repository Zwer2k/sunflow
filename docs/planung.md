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
- Verschleiß je entladener kWh, je Speicher (Einstellungen → Geräte → Speicher → Verschleißkosten).
- Optional die Alterung bei hohem Ladestand.
- Aufschläge der Strategie und weiche Ziele wie „Netzdienlich“. Sie lenken den Plan, erscheinen
  aber nicht in den angezeigten Kosten.

Sunflow plant neu, sobald neue Preise oder Prognosen vorliegen, jede Viertelstunde, und nach jeder
Änderung in Steuerung oder Einstellungen.

## Strategien des Hausspeichers

| | Ausgewogen | Möglichst viel PV aufnehmen | Möglichst autark | Speichernutzung |
|---|---|---|---|---|
| Ziel | geringste Stromkosten | wie Ausgewogen, Platz für PV | so wenig Netzbezug wie möglich | wie Ausgewogen, Speicher reicht auch an starken Tagen |
| Laden aus dem Netz | wenn der Preisunterschied Verluste und Verschleiß übersteigt | kaum vor sonnigen Tagen | nie (außer überfällige Vollladung) | wie Ausgewogen, mit größerem Puffer |
| Im Plan | Kosten, Puffer für 4 von 5 Tagen | jede eingespeiste kWh zählt 5 ct weniger | jede kWh aus dem Netz zählt 1 € extra | Netzbezug in teuren Phasen zählt 10 ct extra, Puffer für 19 von 20 Tagen |
| Passt zu | dynamischen Tarifen und Zeittarifen | viel PV, wenig Speicher | festem Preis, Unabhängigkeit | Zeittarifen, schwankendem Verbrauch |

- **Ausgewogen** hält Energie für teure Stunden zurück und entlädt dort.
- **PV aufnehmen** unterscheidet sich von „Ausgewogen“ nur, wenn eingespeist würde, im Winter
  also kaum. Eine Einspeisebegrenzung kennt sie nicht; dafür gibt es „Netzdienlich“.
- **Autark** deckt jeden Bedarf sofort aus dem Speicher und gibt Verbrauchern bevorzugt
  PV-Überschuss. Bei Zeittarifen kostet sie mehr, weil günstiger Nachtstrom nicht gespeichert wird.
- **Speichernutzung** lädt in günstigen Stunden so weit nach, dass der Speicher auch an einem Tag
  mit deutlich mehr Verbrauch oder weniger Sonne bis zur nächsten günstigen Ladung reicht. Die
  Kosten sind dabei zweitrangig: Netzstrom in teuren Phasen zählt 10 ct extra, so lädt Sunflow
  auch dann nachts und deckt den Tag aus dem Speicher, wenn Verluste und Verschleiß den
  Preisvorteil auffressen.

### Puffer gegen einen Tag über der Prognose

Verbrauch und PV-Prognose stimmen nie genau. Würde Sunflow nachts nur für den erwarteten Tag laden,
wäre der Speicher an jedem Tag mit etwas mehr Verbrauch am Abend leer, und der Rest käme teuer
aus dem Netz. Eine zu viel geladene kWh kostet dagegen fast nichts: Sie bleibt im Speicher und
ersetzt einen Teil der nächsten günstigen Ladung.

Darum soll der Speicher bei „Ausgewogen“, „PV aufnehmen“ und „Speichernutzung“ am Ende jeder
teuren Phase (vor der nächsten günstigen) noch einen Puffer über der Entladegrenze haben:

- **Größe**: aus den Schwankungen deines Tagesverbrauchs (je Werktag/Wochenende, gelernt aus
  dem Verlauf; ohne Verlauf 15 %) und der Spanne der PV-Prognose bis p10, soweit diese PV den
  Hausverbrauch decken würde. „Ausgewogen“ reicht damit an etwa 4 von 5 Tagen,
  „Speichernutzung“ an etwa 19 von 20. Höchstens die Hälfte der nutzbaren Kapazität. Der Puffer
  liegt über der Notstromreserve, nicht in ihr.
- **Weich**: Ein fehlender Puffer zählt im Plan halb so viel, wie günstiges Laden je kWh spart.
  Läuft ein Tag über der Prognose, nutzt der Speicher den Puffer also, statt teuer nachzukaufen.
  Und Sunflow kauft für den Puffer nie zum teuren Preis.
- **Nur mit Zeittarif oder dynamischem Tarif** und erlaubtem Laden aus dem Netz. Bei festem Preis
  oder zu kleinem Preisunterschied gibt es keinen Puffer.

Im Plan steht unter „Ladestand“, wie viel zu welcher Zeit übrig bleiben soll.

### Platz für mehr Sonne als geplant

Geplant wird mit der mittleren PV-Prognose, je nach „PV-Risiko“ ein Stück in Richtung der
vorsichtigen (p10). Kommt mehr Sonne, wäre ein schon voller Speicher das Problem: Der Überschuss
ginge für die Einspeisevergütung ins Netz und müsste später zum günstigen Preis wieder gekauft
werden. Gibt es jede Nacht günstigen Strom, bringt es wenig, den Speicher nachts zu halten und
das Haus aus dem Netz zu versorgen, wenn er am nächsten Tag ohnehin voll würde.

Darum hält der Plan am Ende jedes sonnigen Abschnitts Platz frei für einen Teil der zusätzlichen
PV bis zur optimistischen Prognose (p90):

- **Anteil**: derselbe wie das „PV-Risiko“ in die andere Richtung (erwartet 0 %, ausgewogen 30 %,
  vorsichtig 70 %); bei „PV aufnehmen“ und „Speichernutzung“ mindestens 70 %. „Speichernutzung“
  lädt für einen deutlich schlechteren Tag nach; ohne ebenso viel Platz in die andere Richtung
  wäre der Speicher an jedem sonnigeren Tag schon mittags voll.
- **Weich**: Jede kWh, die dann zu viel im Speicher wäre, zählt so viel, wie diese PV ersetzen
  würde (günstigster Preis danach minus Einspeisevergütung, bei „PV aufnehmen“ plus 5 ct). Der
  Speicher deckt dafür eher nachts das Haus, statt zu halten.

Im Plan steht unter „Ladestand“, wie viel der Speicher dann höchstens halten soll.

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
- **Notstrom-Reserve**: In jeder Strategie bleibt dieser Ladestand für Stromausfälle frei. Liegt
  der Speicher darunter, lädt Sunflow ihn vorrangig wieder auf, aus dem Netz, wenn erlaubt.

## Mehrere Speicher

Hast du mehrere steuerbare Speicher, etwa einen Hybrid-Wechselrichter und einen Steckerspeicher,
plant Sunflow sie gemeinsam. Jeder Speicher hat seinen eigenen Ladestand, seine Leistungsgrenzen,
Wirkungsgrade und Verschleißkosten.

- Den Bedarf deckt zuerst der Speicher, bei dem eine entladene kWh am wenigsten kostet. Ist für
  einen Speicher „In der Planung berücksichtigen“ aus, zählen seine Zyklen als kostenlos.
- Energie wird nicht von einem Speicher in den anderen geschoben; das brächte nur Verluste.
- Jeder Speicher bekommt seine eigene Aktion. Soll nur einer den Bedarf decken, hält der andere,
  damit sich die beiden nicht gegenseitig in die Quere kommen.
- Ladegrenze im Alltag und Alterung bei hohem Ladestand gelten für alle Speicher. Vollladung und
  Notstrom-Reserve gelten für den ersten (in der Regel der Speicher am Notstromausgang).
- Ein Steckerspeicher am Hausnetz (AC-gekoppelt) wandelt auch PV-Strom zweimal um und verliert
  dabei mehr als ein Speicher am Hybrid-Wechselrichter. Das Profil kennt die Kopplung.

Der Plan zeigt den Ladestand aller Speicher zusammen und jeden einzeln; die Live-Ansicht zeigt je
Speicher Ladestand, Leistung und die gerade geplante Aktion.

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
- **Schnee, Laub und Säubern**: Bei zwei oder mehr Flächen vergleicht Sunflow jeden Tag, wie viel
  ihrer Prognose jede Fläche gegenüber den anderen liefert; Wetter und Prognosefehler heben sich
  dabei auf. Als verschmutzt gilt eine Fläche,
  - wenn sie an mindestens zwei Tagen um mehr als 30 % gegenüber den Tagen davor zurückfällt
    (z. B. Schnee), oder
  - wenn sie nach dem letzten Säubern über drei Tage unter 80 % dessen liefert, was sie in der
    ersten Woche danach lieferte (Laub, das sich nach und nach sammelt).

  Diese Tage lernt sie nicht mit, und Sunflow plant sie mit dem geringeren Anteil, bis sie wieder
  so viel liefert. Steigt sie sprunghaft (Laub geräumt, Schnee getaut), fallen die Tage davor aus
  dem Lernen. So bleibt das Gelernte für die saubere Fläche erhalten, auch wenn im Herbst immer
  wieder Laub fällt. Ein langsames Nachlassen ohne erkanntes Säubern davor erkennt nur das Modell
  nach Sonnenstand. Gibt es nur eine andere Fläche zum Vergleich (immer oder in diesem Zeitraum,
  z. B. bevor eine dritte dazukam), gilt die als verändert, deren eigener Anteil an der Prognose
  sich stärker bewegt hat.
- **Der Tag bisher**: Die gemessene PV der letzten Minuten setzt die nächste Stunde fort. Wie viel
  der Rohprognose in den letzten drei Stunden ankam, trägt den Rest des Tages, nach drei Stunden
  noch halb: Ein klarer Tag, der über dem Gelernten liegt, hebt auch den Nachmittag, ein trüber
  senkt ihn. Diese Stunden plant Sunflow dann auch weniger vorsichtig. Ab morgen gilt wieder
  das Gelernte.
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
