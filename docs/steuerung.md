# Steuerung übernehmen

**Deutsch** · [English](en/control.md)

Nach der Einrichtung plant Sunflow nur. Die Geräte regeln weiter selbst, und Sunflow zeichnet im
Schattenbetrieb auf, was es anders gemacht hätte (Analyse → Schattenbetrieb). Sieht der Plan für
dich stimmig aus, gibst du die Steuerung frei.

## Einschalten

Unter **Steuerung → Sunflow steuert**:

1. Den Abschnitt aufklappen. Jedes steuerbare Gerät hat einen eigenen Schalter: Speicher,
   Wallboxen, Warmwasser, eigene Verbraucher und Wechselrichter, die abregeln können.
2. Die Geräte einschalten, die Sunflow steuern soll.
3. Den Hauptschalter rechts oben einschalten und bestätigen.

Sunflow schreibt erst, wenn Hauptschalter **und** Geräteschalter an sind. Ein Speicher braucht
dazu einen bestandenen [Gerätetest](installation.md#3-gerätetest). Unter jedem Gerät steht sein
Zustand: gesteuert, nicht gesteuert, an anderer Stelle gesteuert, Fehler. Beim Speicher steht
auch das aktuelle Netz-Ziel.

Den Hauptschalter gibt es auch in Home Assistant (Entität „Sunflow steuert“), ebenso einen
Schalter je Gerät. Siehe [Sunflow-Integration](integration.md).

## Was Sunflow tut

- **Speicher**: Sunflow setzt jede Minute die geplante Aktion um, etwa Eigenverbrauch, Halten,
  Laden aus dem Netz oder begrenztes Laden bzw. Entladen. Beim Sofar geschieht das
  leistungsgenau über das Fenster des Passivmodus. Geplant wird nur, was das Gerät kann (siehe
  [Unterstützte Geräte](geraete.md)).
  Eine geplante Teilentladung ist Energie für die Viertelstunde: Kurze Verbrauchsspitzen deckt
  der Speicher ganz, bis diese Energie verbraucht ist, danach hält er. Das gilt nur für Geräte,
  deren Entladegrenze im RAM liegt (Sofar-Passivmodus); Geräte, die sie im Flash speichern,
  behalten die feste Grenze des Plans.
- **Wallbox**: In den geplanten Viertelstunden wird geladen, sonst gesperrt. Beim PV-Überschuss
  folgt der Ladestrom dem Überschuss. Die go-e schaltet Sunflow bei Bedarf zwischen 1 und 3 Phasen um.
- **Warmwasser und eigene Verbraucher**: ein- und ausschalten oder Leistungsstufe bzw. Strom
  setzen, wie geplant.
- **PV abregeln**: nur bei freigegebenen Anlagen und nur, wenn Einspeisen kostet (negative
  Vergütung oder über der netzdienlichen Grenze).

Werte, die das Gerät dauerhaft im Flash speichert (z. B. Betriebsmodus), schreibt Sunflow nur,
wenn sie sich ändern, und höchstens so oft am Tag, wie das Profil erlaubt.

## Rückgabe und Absicherung

- **Abschalten**: Wird ein Gerät oder der Hauptschalter ausgeschaltet, bekommt das Gerät seinen
  vorherigen Zustand zurück: Speicher auf Eigenverbrauch, Wallbox mit ihrem vorherigen Strom und
  Phasenmodus.
- **Kein aktueller Plan**: Ist der Plan älter als 30 Minuten oder wird die App beendet, stellt
  Sunflow auf Eigenverbrauch.
- **Watchdog**: Antwortet die App zehn Minuten lang nicht, gibt die Sunflow-Integration in Home
  Assistant die Geräte selbst zurück und meldet das unter „Reparaturen“.
- **Andere Steuerungen**: Steuert evcc gerade selbst den Hausspeicher, pausiert Sunflow. Ein
  Ladepunkt, der an anderer Stelle gesteuert wird, ist als solcher markiert.
- **Neustart des Wechselrichters**: Beim Sofar liegen die Steuerwerte nur im RAM. Nach einem
  Neustart ruht der Speicher, bis Sunflow die Werte in der nächsten Minute wieder schreibt.

## Protokoll

**Steuerung → Protokoll** zeigt jeden Befehl, den Sunflow an gesteuerte Geräte schickt: Zeit,
Aktion, Leistung, Grund, die geschriebenen Werte und Fehler, die Home Assistant meldet (z. B. eine
Modbus-Exception). Ein Fehler, der jede Minute wiederkommt, steht nur einmal da.

Ändert jemand anderes einen Stellwert an einem gesteuerten Gerät, z. B. in der App des
Herstellers, in einer Automation oder in Home Assistant, steht das ebenfalls im Protokoll, mit
dem, was Home Assistant über den Auslöser weiß. Das Protokoll reicht 14 Tage zurück und ist im
Datenexport enthalten.

## Vorübergehende Eingriffe

Strategie, Betriebsart und Ziel der Verbraucher lassen sich in der [Steuerung](bedienung.md#steuerung)
für eine Weile ändern, ohne die Einstellungen anzufassen. Aus Home Assistant geht das mit den
Aktionen `sunflow_ems.set_strategy` und `sunflow_ems.set_consumer`.
