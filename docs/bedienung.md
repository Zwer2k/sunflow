# Bedienung

**Deutsch** · [English](en/user-interface.md)

Oben wählst du eine der fünf Ansichten: **Live**, **Steuerung**, **Planung**, **Analyse** und
**Einstellungen**. Auf dem Handy liegen sie als Symbole am unteren Rand. Eine Zahl an „Analyse“
zeigt offene Empfehlungen.

## Live

![Live](images/live.png)

Der Energiefluss zeigt, was gerade fließt: PV (oben, mit einem Kreis je String), Netz,
Hausspeicher, Haus und die steuerbaren Verbraucher wie Wallbox und Warmwasser. Bewegte Linien
zeigen, wohin die Energie gerade fließt.

- **Haus** zeigt den Verbrauch ohne die steuerbaren Verbraucher, darunter „gesamt“ mit ihnen.
- **Speicher** zeigt Leistung, Richtung und Ladestand; der Ring ist der Ladestand.
- **Wallbox** zeigt Ladeleistung und Ladestand des erkannten Autos.
- **PV-Strings**: Die Zahl am Kreis ist die Anzahl der Optimierer. Ein Klick öffnet die
  Modul-Optimierer des Strings mit ihren aktuellen Werten (siehe [Optimierer](optimierer.md)).

  <details><summary>Beispiel: Optimierer eines Huawei-Strings</summary>

  ![Optimierer in der Live-Ansicht](images/live-optimizers.png)

  </details>
- **Wetter**: aktuelle Bedingungen und Vorhersage, umschaltbar zwischen Stunden und Tagen;
  wischen oder mit den Pfeilen blättern. Unter [Einstellungen → Anzeige](einstellungen.md#anzeige)
  lässt es sich ausblenden.
- **Schnellsteuerung** (unter dem Energiefluss): je Verbraucher, den du unter Steuerung mit der
  Stecknadel angeheftet hast, eine Zeile mit Leistung, Ladestand und nächstem Ladefenster und den
  Betriebsarten als kleine Knöpfe: € Kostenoptimal, PV Nur PV, ⚡ Sofort, ⏸ Pause. Ein Wechsel gilt
  bis Mitternacht. Bei „Akku laden“ ohne Steckersensor schaltet 🔌 „jetzt eingesteckt“. Ohne
  angeheftete Verbraucher erscheint der Block nicht.
- **Heute** fasst die Energiezähler zusammen: PV erzeugt, verbraucht, Netzbezug, eingespeist,
  Verluste von Wechselrichter und Speicher, Autarkie und Eigenverbrauch.
- **Verluste Wechselrichter/Speicher** (unter dem Energiefluss, Mittel der letzten Minuten):
  Bilanz aus PV, Netz und Speicher minus gemessenem Hausverbrauch, also Wandlungsverluste und
  Eigenverbrauch der Geräte. Das Energie-Dashboard von Home Assistant zählt sie zum Hausverbrauch;
  deshalb zeigt es mehr Verbrauch und mehr Autarkie als Sunflow. Nur mit einem Verbrauchszähler
  bzw. einer Verbrauchsmessung.

<img src="images/live-mobile.png" alt="Live auf dem Handy" width="300">

## Steuerung

![Steuerung](images/control.png)

Hier greifst du vorübergehend in den Plan ein. Danach gilt wieder der Standard aus den
Einstellungen. **Eingriffe gelten** legt fest, wie lange: bis Mitternacht, 4 Stunden oder bis
auf Weiteres. Nach jeder Änderung plant Sunflow sofort neu.

- **Sunflow steuert**: Hauptschalter und Schalter je Gerät, dazu der Zustand jedes Geräts.
  Zugeklappt zeigt der Abschnitt, wie viele Geräte gesteuert werden und ob eines ein Problem hat.
  Ausführlich unter [Steuerung übernehmen](steuerung.md).
- **Hausspeicher**: Strategie für den Zeitraum (Ausgewogen, PV aufnehmen, Autarkie).
  „Was macht …?“ klappt die Erklärung der Strategie auf.
- **Verbraucher**: je Wallbox, Warmwasser oder eigenem Verbraucher die Betriebsart
  (Kostenoptimal, Nur PV, Sofort, Pause) und das Ziel (Ladestand oder Temperatur). Bei der
  Wallbox wählst du das Fahrzeug oder lässt es erkennen; die gescannte Karte steht in Klammern,
  z. B. „Fiat 500e (Karte 1)“. Mit eingerichtetem Sensor siehst du auch die Spannung der
  12-V-Batterie des Autos. Darunter steht, was der Plan vorsieht oder warum nichts eingeplant ist.
  Die **Stecknadel** oben rechts legt die Betriebsarten des Verbrauchers als Schnellsteuerung auf
  Live; für alle Browser und Geräte.
- **Priorität**: Reicht der PV-Überschuss nicht für alle, bekommt der obere Eintrag zuerst.
  Mit **Grenzen** wartet ein Verbraucher, bis höher stehende Speicher einen Stand erreicht haben,
  z. B. Warmwasser erst ab 90 % Hausspeicher.
- **Protokoll**: was Sunflow an Geräte geschrieben hat, mit Grund und Fehlern, und Änderungen
  von anderer Seite mit ihrem Auslöser. Filter nach Gerät, Zeitraum, „nur fremde Änderungen“
  und „nur Fehler und Warnungen“.

<img src="images/control-mobile.png" alt="Steuerung auf dem Handy" width="300">

## Planung

![Planung](images/plan.png)

Links von „jetzt“ stehen gemessene Werte, rechts die Prognose und der Plan.

- **Kopfzeile**: geplante Steuerung jetzt (und was tatsächlich passiert), erwartete Ersparnis
  gegenüber reinem Eigenverbrauch, erwartete Stromkosten und erwarteter Ladestand am Planende.
  **Neu berechnen** plant sofort neu.
- **Hinweise** in Gelb, z. B. wenn eine PV-Prognose nicht so weit reicht wie der Plan.
- **Geplante Steuerung**: die Abschnitte des Plans mit Aktion (Eigenverbrauch, Speicher halten,
  Laden aus dem Netz, Entladen begrenzt …) und Ladestand von → bis.
- **Steuerbare Verbraucher**: je Verbraucher Ziel, Zeitpunkt und was eingeplant ist, oder
  warum nicht.
- **Verlauf und Prognose**: Diagramme für Ladestand, PV-Erzeugung und Hausverbrauch,
  Netzbezug und Einspeisung, Kostenverlauf und Bezugspreis.
  - Zeitraum 24 h, 48 h oder 7 Tage; mit ‹ › blättern, **Jetzt** springt zurück.
  - Mit der Maus oder dem Finger seitlich ziehen verschiebt alle Diagramme gemeinsam.
  - Ein Klick auf eine Zahl der Leistungsachse skaliert bis dorthin; erneuter Klick zeigt alles.
  - Ein Klick auf einen Eintrag der Legende im Ladestand-Diagramm zeigt nur diesen in seiner
    Einheit (kWh oder °C) mit seinem Ziel.
  - Gestrichelt ist Prognose oder Plan, durchgezogen ist gemessen. Geschätzte Preise jenseits der
    veröffentlichten sind ebenfalls gestrichelt.
  - Per Häkchen lässt sich die unkorrigierte Prognose der Quelle (Solcast, Forecast.Solar)
    und der Gesamtverbrauch mit steuerbaren Verbrauchern einblenden.

## Analyse

![Analyse](images/analysis.png)

Wie gut stimmen Prognosen und Entscheidungen mit der Wirklichkeit überein?

- **Empfehlungen**: Hinweise, wo eine Einstellung nicht zu den Messwerten passt, z. B. ein zu
  hoch angesetzter Warmwasserbedarf oder ein gemessener Wirkungsgrad des Speichers.
  **Öffnen** führt zur Einstellung, **Ausblenden** verwirft den Hinweis.
- **Energiebilanz**: Autarkie, Eigenverbrauch, PV, Verbrauch, Netz, Speicher und Wirkungsgrad
  für heute, 7 oder 30 Tage, aus den Energiezählern der Geräte.
- **Kosten pro Tag**: Nettokosten (Bezug minus Einspeisung) gegenüber „ohne PV und Speicher“
  und die Ersparnis, über 30 oder 90 Tage.
- **Wirkungsgrade**: Speicher und Wechselrichter über 30 Tage, 90 Tage oder ein Jahr.
- **Lernen**: wie gut die PV-Prognose ankommt (gesamt, je Bewölkung, je Stunde, je Fläche) und
  wie weit das Modell für den Hausverbrauch ist. Je Dachfläche setzt „Neu lernen ab heute“ das
  Lernen zurück, z. B. nach einem Baumschnitt.
- **PV nach Sonnenstand (Test)**: lernt je Dachfläche aus den vergangenen Solcast-Prognosen und den
  gemessenen Strings, wie viel der Prognose je Sonnenhöhe und -richtung ankommt, getrennt nach klar
  und bewölkt. Dazu lernt es, wie weit die Messung bei unsicherer Prognose (p10 weit unter dem
  Mittelwert, etwa bei Hochnebel) Richtung p10 liegt; das nutzt die Planung nur, wo es im Backtest
  besser ist. Die Verschattungskarte zeigt orange, wo weniger ankommt (Schatten), blau, wo mehr.
  Der Backtest lernt ohne die letzten 3 Wochen und vergleicht auf diesen die Abweichung je Stunde
  mit der Rohprognose und mit der Korrektur nach Uhrzeit. Die Planung nutzt es erst, wenn es unter Einstellungen →
  Planung eingeschaltet ist, und nur für Flächen mit mindestens 60 Tagen und besserem Backtest.
  Liefert eine Fläche mehrere Tage am Stück deutlich weniger als sonst bei diesem Sonnenstand
  (ohne eigene Historie: weniger als die Hälfte der Prognose), erscheint ein Hinweis auf Laub,
  Schnee oder Schmutz; diese Tage lernt Sunflow nicht mit. Liefert eine Fläche mit Modell in den
  letzten Tagen unter 90 % des Gelernten (z. B. Herbstlaub), plant Sunflow mit dieser geringeren
  Leistung und stellt sich von selbst wieder um, sobald sie mehr liefert (alle 3 Stunden geprüft).
  „Neu lernen ab heute“ verwirft auch die Historie nach Sonnenstand, deshalb nur nach Baumschnitt
  oder Modultausch, nicht nach dem Säubern.
- **Datenbestand fürs Lernen**: wie weit Home Assistant zurück aufhebt, woraus Sunflow lernen
  kann: Verlauf in voller Auflösung (Aufbewahrung des Recorders), Langzeitstatistik, Wetter mit
  oder ohne Bewölkung und die vergangenen Solcast-Prognosen. Darunter je Messwert, seit wann er
  vorliegt und wie oft er sich ändert, auch je Optimierer (Leistung und Temperatur je Modul). Geprüft wird höchstens stündlich; **Neu prüfen** fragt sofort.
- **Schattenbetrieb**: Sunflow zeichnet alle 15 Minuten auf, was es tun würde und was
  tatsächlich passiert ist. Die Kacheln vergleichen PV- und Lastprognose mit der Messung, das
  Diagramm den geplanten mit dem tatsächlichen Ladestand. Die Tabelle zeigt die Abschnitte, in
  denen die Entscheidung abwich. Hier liegen auch **Daten exportieren** und **Export importieren**.
