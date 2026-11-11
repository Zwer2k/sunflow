# Sunflow – Roadmap

Sammlung sinnvoller Erweiterungen, grob nach Nutzen geordnet. Noch nichts davon ist zugesagt.

## Leitlinien

- **Erst vergleichen, dann planen.** Ein neues Modell läuft zuerst im Schattenbetrieb neben dem
  bisherigen. Unter Analyse zeigt Sunflow, welches näher an der Messung lag. Planen wird erst damit,
  wenn es nachweislich besser ist.
- **Nichts ist Pflicht.** Zusätzliche Quellen wie Präsenz, Kalender oder Feiertage verbessern die
  Prognose. Ohne sie arbeitet Sunflow wie bisher.
- **Einfach vor komplex.** Erst physikalisch sinnvolle Merkmale und robuste Statistik. Aufwändige
  Modelle (Gradient Boosting, neuronale Netze) erst, wenn Monate an Daten vorliegen und der
  Vergleich zeigt, dass sie sich lohnen.
- **Kleine Hardware.** Training und Planung müssen auf einem Raspberry Pi laufen.

## Stand heute

| Bereich | Heute |
|---|---|
| PV-Prognose | Korrekturfaktor je Dachfläche, Stunde und Bewölkung (3 Klassen), neuere Tage zählen mehr (Halbwertszeit 30 Tage); Korrektur nach Sonnenstand (Test, abschaltbar); Verschmutzung einer Fläche (Schnee, Laub) im Vergleich mit den anderen |
| Hausverbrauch | Regression aus Uhrzeit, Wochentag und Temperatur, ab 14 Tagen Daten |
| Auto | Verbrauch je Stunde (Werktag und Wochenende), Ankunft und Abfahrt aus Steckzeiten oder Anwesenheit; Ladestand zwischen den Meldungen aus der Wallbox-Energie, Ladewirkungsgrad gelernt |
| Warmwasser | Verbrauch je Stunde (Werktag und Wochenende) |
| Speicher | Wirkungsgrad und Eigenverbrauch aus den Energiezählern |
| Planung | Gemischt-ganzzahlige Optimierung über bis zu 96 h; optimal für die gegebene Prognose. Der Ladestand des Plans von 6 h vorher wird zum Vergleich aufgezeichnet |

## 1. PV über das Jahr lernen

1. **Verschattung nach Sonnenstand statt nach Uhrzeit** (höchste Priorität). Je Dachfläche ein
   Korrekturfeld über Sonnenhöhe und -richtung, berechnet aus dem Standort von Home Assistant, mit
   Glättung zwischen benachbarten Feldern. Was im Herbst bei 20° Sonnenhöhe gelernt wurde, gilt im
   Frühjahr wieder; die Uhrzeit-Faktoren müssen nicht jedes Jahr neu lernen. Daten: Prognose je
   Fläche und Messung je String liegen in der Aufzeichnung seit dem 24.10.2026 vor, also auch
   rückwirkend nutzbar. Fehlt ein Feld noch, gilt der heutige Faktor.
   *Umgesetzt (0.75.29) als „Nach Sonnenstand korrigieren (Test)“: plant für Flächen mit mindestens
   60 Tagen, wenn der Backtest besser ist.*
2. **Wetter mit mehr Merkmalen**: Modultemperatur (Leistung steigt bei Kälte), Diffus- und
   Direktanteil, wo die Quelle sie liefert. Bewölkung als Zahl statt drei Klassen.
3. **Schnee auf den Modulen erkennen**: Nach Schneefall bei Frost liefert die Anlage trotz Sonne
   fast nichts. Sunflow erkennt das an Messung, Temperatur und Niederschlag und senkt die Prognose,
   bis die Module wieder liefern. Sonst plant es tagelang mit PV, die nicht kommt.
   *Teilweise umgesetzt (0.78.28): Bricht eine Fläche gegenüber den anderen ein, plant Sunflow sie
   mit dem geringeren Anteil. Liegt Schnee auf allen Flächen, fehlt noch der Vergleich, dafür
   braucht es Temperatur und Niederschlag.*
4. **Unsicherheit lernen**: Statt nur Solcast p10 die eigene Fehlerverteilung je Wetterlage, Fläche
   und Vorlauf lernen. Die Planung kann damit gezielt vorsichtig sein, wenn die Prognose bei dieser
   Wetterlage oft danebenliegt.
5. **Langzeitverlauf je String**: Leistung über Monate bei klarem Himmel vergleichen und
   Verschmutzung oder Alterung erkennen, z. B. die Empfehlung „String 2 liefert 8 % weniger als im
   Vorjahr – Module reinigen?“.
   *Teilweise umgesetzt (0.78.28): Laub, das sich nach einem Säubern sammelt, erkennt Sunflow im
   Vergleich mit den anderen Flächen. Alterung über Jahre und die Empfehlung fehlen noch.*
7. **Laub und Schnee nach Jahreszeit erwarten**: Aus den Episoden der Historie je Fläche und
   Kalenderwoche lernen, wie wahrscheinlich Verschmutzung ist und wie schnell Laub nach dem Räumen
   zurückkommt, und das für die kommenden Tage einplanen, statt erst nach drei schlechten Tagen zu
   reagieren. Erst im Schattenbetrieb. *Schritt 1 umgesetzt (0.78.59): Unter Analyse listet
   „Verschmutzung in der Historie“ die Episoden der ganzen Historie mit Tiefe und Frost.*
6. **Konfiguration prüfen**: Passt die gemessene Kurve einer Fläche zu Ausrichtung und Neigung in
   Solcast/Forecast.Solar? Ein Hinweis, wenn etwa eine Ost-Fläche als Süd eingetragen ist.

## 2. Verbrauch mit Kontext

1. **Anwesenheit**: Zahl der Personen zu Hause (`person`, `device_tracker`), optional Präsenzmelder.
2. **Kalender**: Arbeit, Homeoffice, Urlaub aus einer Kalender-Entität; Ereignisse in der Zukunft
   gehen direkt in die Prognose ein (z. B. „Urlaub ab Freitag“).
3. **Feiertage und Ferien**: Integration *Workday*, Schulferien als Kalender.
4. **Urlaub automatisch erkennen**: Ist tagelang niemand zu Hause, plant Sunflow mit dem
   Grundverbrauch statt mit dem gewohnten Alltag.
5. **Die nächsten Stunden genauer**: Der Verbrauch der letzten Stunden als Eingang
   (Kurzfristprognose); bringt viel für die nächsten 1–3 Stunden, wenig für den Tag danach.
6. **Wärmepumpe**: Verbrauch aus Außentemperatur und Heizgradtagen, später ein einfaches
   Wärmemodell des Hauses, damit Sunflow die Wärmepumpe in günstige oder sonnige Stunden schieben
   kann.
7. **Verbraucher, die nur gemessen werden**: Herd, Waschmaschine, Trockner, Spülmaschine mit
   eigenem Leistungssensor als Typ „Nur messen“. Sunflow lernt je Gerät, wann und wie viel es
   läuft, und den Grundverbrauch ohne diese Geräte; die Geräte gehen als eigene Erwartung in die
   Prognose ein. Später Hinweise wie „Waschmaschine jetzt starten – die nächsten 2 h Überschuss“,
   mit Fernstart (z. B. Home Connect) als verschiebbare Last.

## 3. Auto und Warmwasser

1. **Wahrscheinlichkeiten statt Einzelwerte**: „Fährt mit 70 % vor 8 Uhr weg“. Die Planung lädt
   dann so, dass es mit hoher Wahrscheinlichkeit reicht, ohne immer vom frühesten Fall auszugehen.
2. **Fahrten aus dem Kalender**: Ein Termin mit Ort ergibt Abfahrt und nötigen Ladestand.
3. **Ladekurve lernen**: Wie stark das Auto kurz vor voll die Leistung drosselt, und der
   Wirkungsgrad je Strom und Phasenzahl.
4. **Ladestand ohne Sensor schätzen**: Aus geladener Energie und gelerntem Verbrauch, für Autos
   ohne Integration.
5. **Legionellenschutz**: Die wöchentliche Aufheizung auf 60 °C zum günstigsten Zeitpunkt
   einplanen.

## 4. Speicher

1. **Kapazität und Alterung (SoH)** aus Ladestandsänderung und geladener Energie über Monate; die
   Planung nutzt die tatsächliche Kapazität.
2. **Wirkungsgrad je Leistung**: Bei kleinen Leistungen sind die Verluste anteilig hoch (z. B.
   DC/DC-Wandler je Modul). Sunflow meidet dann sehr kleine Lade- und Entladeleistungen, wo sie sich
   nicht lohnen.
3. **Temperatur**: Im Winter begrenzen manche Speicher die Ladeleistung; aus den Messwerten lernen
   und einplanen.

## 5. Preise und Netz

1. **Zeitvariable Netzentgelte (§ 14a EnWG, Modul 3)**: Netzentgelt je Zeitfenster als Aufschlag
   auf den Bezugspreis.
2. **Solarspitzengesetz**: Bei negativen Preisen keine Vergütung, bei Neuanlagen ohne
   Smart-Meter-Gateway 60 % Einspeisegrenze; beides in der Planung berücksichtigen.
3. **Preisprognose verbessern**: Statt Mittel der letzten zwei Wochen ein Modell aus Wochentag,
   Uhrzeit sowie Wind- und PV-Prognose (Energy-Charts), für die Stunden nach den veröffentlichten
   Preisen.

## 6. Messen, was es bringt

1. **Rückblick**: Was haben Prognosefehler gekostet, und wie viel hätte ein besseres Modell
   gespart? Rechnet den Plan mit den tatsächlichen Werten nach.
2. **Modellvergleich als Grundgerüst**: Mehrere Prognosemodelle parallel im Schattenbetrieb, mit
   Fehler je Modell unter Analyse; Grundlage für alle Punkte oben.
3. **Verschattungskarte**: Je Dachfläche eine Grafik über Sonnenhöhe und -richtung, wo die Anlage
   weniger liefert als prognostiziert.
4. **Entscheidungen erklären**: „Warum lädt Sunflow jetzt aus dem Netz?“ mit den Gründen aus dem
   Plan (Preisunterschied, erwarteter Bedarf, fehlende PV).

## 7. Betrieb und Pflege

1. **Datenlücken sichtbar machen**: Hinweis, wenn ein Sensor dem Lernen dauerhaft Lücken liefert
   (z. B. der Ladestand des Autos), mit dem, was dadurch nicht gelernt werden kann.
2. **Gelernte Modelle sichern**: Export und Import enthalten auch gelernte Faktoren, damit ein Umzug
   nicht von vorn beginnt.
3. **Aufzeichnung über Jahre**: Größe der Datenbank im Blick behalten, ältere Viertelstunden zu
   Stunden verdichten.
4. **Integration übersetzen**: Eintragstitel und Gerätemodelle der Home-Assistant-Integration auf
   Englisch.
5. **CI**: Image bei jedem Push probeweise bauen (ohne Veröffentlichung); GitHub Actions auf
   Node.js 24 heben.
