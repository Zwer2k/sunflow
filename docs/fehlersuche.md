# Fehlersuche

**Deutsch** · [English](en/troubleshooting.md)

## Der Speicher lädt bei günstigen Preisen nicht aus dem Netz

- Steht die Strategie auf **Möglichst autark**? Sie lädt nie aus dem Netz.
- Ist unter Einstellungen → Planung **Laden aus dem Netz** erlaubt?
- Ist der Preisunterschied groß genug? Netzladen muss Verluste und Verschleiß decken (siehe
  [Lohnt sich Laden aus dem Netz?](planung.md#lohnt-sich-laden-aus-dem-netz)). Hohe
  Verschleißkosten oder ein niedriger Wirkungsgrad verhindern es oft.

## Die Wallbox lädt nicht nach Plan

- In der Steuerung steht **„Karte 1 gehört zu keinem Fahrzeug dieser Wallbox – Wallbox regelt
  selbst“**: Ordne die Karte unter Einstellungen → Laden dem Fahrzeug zu, oder wähle das Fahrzeug
  in der Steuerung.
- Steuert Sunflow die Wallbox? Siehe [Steuerung übernehmen](steuerung.md). evcc darf denselben
  Ladepunkt dann nicht zusätzlich steuern.
- Erlaubt die Wallbox weniger Strom als vorgegeben, zeigen Steuerung und Protokoll nach zwei
  Minuten eine Warnung.

## „Nicht eingeplant: Temperatur oder Ziel nicht lesbar“

Ein Sensor des Verbrauchers ist gerade `unavailable` oder `unknown`. Prüfe ihn in Home Assistant;
Sunflow plant ihn wieder ein, sobald er Werte liefert.

## „PV-Prognose … reicht nur bis …“

Eine Prognosequelle (z. B. Forecast.Solar) reicht weniger weit als der Plan. Danach fehlt ihr
Ertrag im Plan. Nutze nach Möglichkeit eine Quelle mit längerer Reichweite (Solcast), oder nimm
den Hinweis hin: Er betrifft nur das Planende.

## Die Steuerung lässt sich nicht einschalten

Ein Speicher braucht einen bestandenen [Gerätetest](installation.md#3-gerätetest). Schlägt der
Test fehl, nennt er den Grund, z. B. vertauschte Vorzeichen (im Expertenmodus unter „Entitäten
zuordnen“ korrigierbar) oder einen Wechselrichter, der nicht im Steuermodus steht.

## Im Protokoll stehen Schreibfehler

Die Meldung kommt von Home Assistant bzw. der Integration des Geräts. Häufig steht der
Wechselrichter nicht im Steuermodus, weil er etwa in der App des Herstellers auf „Self Use“
umgestellt wurde. Die Meldung nennt dann den aktuellen Modus. Ein Fehler, der jede Minute
wiederkommt, steht nur einmal im Protokoll.

## Im Diagramm fehlen Messwerte

Sunflow nimmt Messwerte aus der Statistik von Home Assistant. Hat ein Sensor keine Statistik (kein
`state_class`) oder ist er vom Recorder ausgeschlossen, verwendet Sunflow seine eigenen
Minutenwerte. Die gibt es erst ab dem Zeitpunkt, an dem das Gerät in Sunflow eingerichtet wurde.

## „Keine Verbindung zu Home Assistant“

Home Assistant startet gerade neu, oder die App hat die Verbindung verloren. Sunflow verbindet
sich selbst neu und plant dann sofort. Steuert Sunflow, stellt es ohne aktuellen Plan nach
30 Minuten auf Eigenverbrauch.

## Logs und Hilfe

- **App-Log**: Home Assistant → Einstellungen → Apps → Sunflow → Protokoll.
- **KI-Assistent**: Mit dem [MCP-Server](mcp.md) kann z. B. Claude Code Plan, Protokoll und
  Log lesen und erklären.
- **Fehler melden**: als Issue auf [GitHub](https://github.com/Zwer2k/sunflow/issues), mit
  Version, Gerät und wenn möglich dem Datenexport (Analyse → Schattenbetrieb → Daten exportieren).
  Der Export enthält deine Konfiguration und Messwerte; teile ihn nur, wenn du damit einverstanden bist.
