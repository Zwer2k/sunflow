# Sunflow-Integration in Home Assistant

Die Integration verbindet Home Assistant mit der Sunflow-App. Sie bringt die gelernte
PV-Prognose ins Energie-Dashboard, zeigt Plan und Steuerung als Entitäten und lässt
Sunflow aus Automationen bedienen. Gerechnet und gesteuert wird weiter in der App.

## Einrichtung

- **Automatisch:** Die App meldet sich beim Supervisor. Home Assistant zeigt die
  Integration unter „Einstellungen → Geräte & Dienste“ als gefunden an; ein Klick
  verbindet sie.
- **Von Hand:** In Sunflow unter „Integrationen → Sunflow-Integration in Home Assistant →
  Zugangsdaten anzeigen“ stehen Adresse und Schlüssel. Beides beim Hinzufügen der
  Integration eintragen.

## Geräte

| Gerät | Inhalt |
|---|---|
| Sunflow | Plan, Steuerung, Verbraucher, Kalender |
| Sunflow PV-Prognose · Gesamt | Prognose der ganzen PV |
| Sunflow PV-Prognose · *Anlage* | eine je Wechselrichter |
| Sunflow PV-Prognose · *Fläche* | eine je Prognose-Fläche (z. B. Dach Süd-Ost, Carport) |
| Sunflow PV-Strings | Energie und Leistung je PV-String |

Jede Prognose ist ein eigener Eintrag der Integration, weil das Energie-Dashboard
Prognosen nach Eintrag auswählt. Neue Flächen und Anlagen legt die Integration selbst an;
ein gelöschter Prognose-Eintrag kommt nicht wieder.

## Prognose im Energie-Dashboard

1. Unter „Einstellungen → Dashboards → Energie → Solarmodule“ die Solarquelle bearbeiten.
2. Bei „Prognose der Solarproduktion“ den passenden Sunflow-Eintrag wählen: „Gesamt“ für
   eine einzige Quelle, die Anlage für einen Wechselrichter oder die Fläche für einen String.
3. Für Strings ohne eigenen Energiezähler die Sensoren „*String* Energie“ des Geräts
   „Sunflow PV-Strings“ als Solarquelle nehmen.

Die Prognose ist die der gewählten Quelle (Solcast, Forecast.Solar) mit der gelernten
Korrektur je Fläche, stündlich ab Tagesbeginn und über die volle Reichweite der Quelle.
Je Prognose gibt es zusätzlich die Sensoren „Prognose heute“, „Prognose Rest heute“,
„Prognose morgen“ und „Prognose Leistung jetzt“.

## Entitäten am Gerät „Sunflow“

| Entität | Bedeutung |
|---|---|
| Aktion Speicher | was der Plan gerade mit dem Hausspeicher tut |
| Strompreis jetzt, Günstigstes Fenster, Günstigster Preis | aus dem Tarif des Plans |
| Speicher in einer Stunde | geplanter Ladestand |
| Ersparnis des Plans | gegenüber reinem Eigenverbrauch, über den Planzeitraum |
| PV-Überschuss (Sensor und Binärsensor) | PV minus Verbrauch; an ab 300 W, für eigene Automationen |
| Laden aus dem Netz, PV begrenzt | was der Plan gerade tut |
| Plan | Problem, wenn der Plan älter als 30 Minuten ist oder fehlschlug |
| Plan aktualisiert, Nächste einmalige Last | Zeitpunkte |
| *Gerät* Aktion | was Sunflow mit einem steuerbaren Gerät tut (Grund, Leistung, Phasen als Attribute) |
| *Verbraucher* nächster Start, geplante Energie, jetzt eingeplant | je Wallbox, Heizstab, eigener Verbraucher |
| Sunflow steuert, *Gerät* steuern | Hauptschalter und Schalter je Gerät |
| Strategie | Ausgewogen, PV aufnehmen, Autarkie |
| Neu planen | Taste |
| Plan (Kalender) | Lade-, Heiz-, Netzlade- und Abregelfenster, einmalige Lasten |

## Aktionen

| Aktion | Wofür |
|---|---|
| `sunflow_ems.set_strategy` | Strategie bis zu einer Uhrzeit; `default` kehrt zur eingestellten zurück |
| `sunflow_ems.set_consumer` | Ziel, Strategie oder Pause eines Verbrauchers für eine Weile; `reset` hebt das auf |
| `sunflow_ems.plan_load` | einmalige Last einplanen; antwortet mit Start, Ende und Kosten |
| `sunflow_ems.cancel_load` | einmalige Last abbrechen |
| `sunflow_ems.replan` | sofort neu planen |

## Ereignisse

| Ereignis | Wann |
|---|---|
| `sunflow_ems_plan_updated` | ein neuer Plan liegt vor |
| `sunflow_ems_consumer_start` | ein Verbraucher ist ab jetzt eingeplant |
| `sunflow_ems_load_start` | eine einmalige Last soll jetzt starten |

### Beispiel: Waschmaschine zum günstigsten Zeitpunkt

```yaml
# Beim Einschalten der Bereitschaft einplanen, fertig bis 18 Uhr
- alias: Waschmaschine einplanen
  triggers:
    - trigger: state
      entity_id: input_boolean.waschmaschine_bereit
      to: "on"
  actions:
    - action: sunflow_ems.plan_load
      data:
        name: Waschmaschine
        energy_kwh: 1.2
        power_kw: 2
        finish_by: "{{ today_at('18:00') }}"

# Zum geplanten Start einschalten
- alias: Waschmaschine starten
  triggers:
    - trigger: event
      event_type: sunflow_ems_load_start
      event_data:
        name: Waschmaschine
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.waschmaschine
```

Sunflow sucht eine zusammenhängende Laufzeit, in der die Energie am wenigsten kostet:
zuerst geplante Einspeisung (dann entgeht nur die Vergütung), sonst der Netzpreis.

## Watchdog

Steuert Sunflow Geräte und antwortet die App zehn Minuten lang nicht, gibt die
Integration die Geräte selbst zurück: Speicher auf Eigenverbrauch, Wallboxen und
Verbraucher an ihre eigene Logik, PV-Begrenzung aufgehoben. Dafür hält die App die nötigen
Home-Assistant-Aufrufe bereit. Unter „Reparaturen“ erscheint ein Hinweis, bis die App
wieder antwortet; danach steuert sie wie zuvor weiter.
