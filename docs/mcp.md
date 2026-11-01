# MCP-Server für KI-Assistenten

Sunflow hat einen eingebauten MCP-Server ([Model Context Protocol](https://modelcontextprotocol.io)).
Darüber kann ein KI-Assistent wie Claude Code die Daten deiner Sunflow-Installation lesen, etwa
um zu erklären, warum der Plan so aussieht, warum die Wallbox nicht geladen hat oder wo eine
Prognose danebenlag.

**Er liest nur.** Kein Werkzeug kann die Konfiguration ändern, Geräte steuern oder etwas an
Home Assistant schicken. Die SQL-Abfrage öffnet die Aufzeichnung schreibgeschützt und lässt
nur lesende Anweisungen zu.

## Einrichten

1. In Home Assistant unter **Einstellungen → Apps → Sunflow → Konfiguration → Netzwerk** einen
   Port für „MCP-Server für KI-Assistenten“ eintragen (z. B. `8099`) und die App neu starten.
   Ohne Port ist der Server von außen nicht erreichbar.
2. In Sunflow unter **Einstellungen → Geräte**, Abschnitt „MCP-Server für KI-Assistenten“,
   beim Schlüssel auf **Erzeugen** klicken. Schlüssel und fertiger Befehl werden **nur dieses eine Mal**
   angezeigt; Sunflow speichert nur eine Prüfsumme davon.
3. Den Befehl dort ausführen, wo der Assistent läuft, z. B.:

   ```bash
   claude mcp add --transport http sunflow http://homeassistant.local:8099/mcp \
     --header "Authorization: Bearer <Schlüssel>"
   ```

Ohne Schlüssel ist der Server geschlossen. **Zurückziehen** macht den Schlüssel sofort ungültig;
**Neu …** ersetzt ihn (z. B. wenn er verloren ging). Der Schlüssel gilt nur für
`/mcp`, nicht für die übrige Oberfläche von Sunflow, und steht nicht im Datenexport.

Der Port ist im Heimnetz ohne Verschlüsselung (HTTP) erreichbar. Gib ihn nicht ins Internet frei.

## Werkzeuge

| Werkzeug | Inhalt |
|---|---|
| `get_status` | Version, Verbindung zu Home Assistant |
| `get_live` | aktuelle Leistungen |
| `get_plan` | aktueller Plan mit Slots, Preisen, Prognosen, Verbrauchern, Warnungen |
| `get_control`, `get_control_log` | Steuerung und was Sunflow an Geräte geschrieben hat (mit Fehlern) |
| `get_history` | gemessene Werte aus der Statistik von Home Assistant |
| `get_shadow`, `query_shadow_db` | Aufzeichnung geplant gegen tatsächlich; freie SQL-Abfrage darauf |
| `get_energy`, `get_costs`, `get_efficiency` | Bilanzen, Kosten, Wirkungsgrad |
| `get_config`, `get_vehicles`, `get_batteries`, `get_site` | Konfiguration |
| `get_sources`, `get_tariff_series`, `get_forecast_series`, `get_pv_learning` | Preise und PV-Prognose |
| `get_device_parameters`, `get_device_efficiency`, `get_direct_status` | einzelne Geräte |
| `get_recommendations`, `get_integration_state` | Empfehlungen, Daten für die Integration |
| `get_log` | Fehler, Warnungen und Hinweise der App seit dem letzten Start, mit Traceback |

## Berechtigungen

Unter „Darf lesen“ legst du je Bereich fest, was der Assistent sehen darf: Live, Plan, Preise und
Prognosen · Verlauf, Aufzeichnung und Auswertung (mit SQL) · Konfiguration und Geräte ·
Steuerung und Steuerungsprotokoll · Fehler und Warnungen der App. Version und Verbindung sind
immer lesbar. Ein abgeschalteter Bereich fehlt in der Werkzeugliste und wird abgewiesen; manche
Assistenten laden die Liste erst beim nächsten Start neu.

Der Plan über 96 Stunden ist groß. Kürzt Claude Code die Antwort, hilft
`MAX_MCP_OUTPUT_TOKENS=50000` als Umgebungsvariable, oder gezielte Abfragen über `query_shadow_db`.
