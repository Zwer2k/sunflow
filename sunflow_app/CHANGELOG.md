# Changelog

## 0.32.9

- Live: Die Tageskennzahlen verschwinden nicht mehr, wenn die Datenbank von Home Assistant nicht antwortet. Die zuletzt geladenen Werte bleiben stehen, mit dem Hinweis „nicht aktuell“; ohne frühere Werte erscheint der Grund.

## 0.32.8

- Live: „Aktuelle Energieflüsse“ und „Stand …“ linksbündig.

## 0.32.7

- Live: „Aktuelle Energieflüsse“ und „Stand …“ rechtsbündig, ohne Einzug vor „Stand“.

## 0.32.6

- Live: „Stand …“ ohne vorangestellten Punkt und etwas heller.

## 0.32.5

- Live: „Aktuelle Energieflüsse · Stand …“ bricht neben dem Wetter höchstens in zwei Zeilen um statt Wort für Wort.

## 0.32.4

- Live: Die Wetteranzeige ist kompakter und bleibt auf breiten Bildschirmen rechts neben dem Titel; die Vorhersage wird dafür schmaler und lässt sich weiterhin wischen oder per Pfeil blättern.

## 0.32.3

- Live: Pfeile links und rechts an der Wettervorhersage blättern weiter; sie erscheinen nur, wenn in die Richtung noch Stunden oder Tage verborgen sind.

## 0.32.2

- Live: Die Wettervorhersage scrollt ohne sichtbaren Scrollbalken.

## 0.32.1

- Live: Durch die Wettervorhersage lässt sich wischen (am PC mit gedrückter Maustaste ziehen): bis zu 48 Stunden bzw. 16 Tage, so weit die Wetter-Entität sie liefert.

## 0.32.0

- Live: Die Wetteranzeige lässt sich zwischen Stunden und Tagen umschalten. Die Tagesansicht zeigt bis zu 7 Tage mit Höchst- und Tiefsttemperatur (Regenmenge beim Darüberfahren). Die Auswahl wird im Browser gemerkt; liefert die Wetter-Entität keine Tagesvorhersage, bleibt es bei den Stunden.

## 0.31.2

- Die Änderungen aus 0.31.0 und 0.31.1 (Schonung der Home-Assistant-Datenbank, kein Protokoll pro Anfrage) waren mit 0.30.7 versehentlich zurückgenommen worden und sind wieder enthalten.
- Einstellungen: Das Prozentfeld der Notstrom-Reserve war auf Häkchengröße geschrumpft und ließ sich nicht bedienen.
- Einstellungen: Die Notstrom-Reserve steht jetzt nach „Spätestens nach … aus dem Netz“.

## 0.31.1

- Keine Protokollzeile mehr pro Anfrage der Oberfläche (die Live-Ansicht fragt alle 5 Sekunden ab); das Protokoll der App bleibt klein und zeigt nur noch Hinweise und Fehler.

## 0.31.0

Schonung der Home-Assistant-Datenbank:

- Vor Abfragen von Statistik und Verlauf prüft Sunflow, ob die Datenbank von Home Assistant hinterherkommt (Warteschlange, Recorder läuft). Ist sie ausgelastet oder antwortet sie nicht (Zeitüberschreitung, „unknown_error“), pausiert Sunflow alle Datenbankabfragen für 5 Minuten, statt weitere hinterherzuschicken.
- Verläufe für die Planung (Hausverbrauch, Verbraucher, letzte Vollladung) werden höchstens einmal pro Stunde abgefragt, gelernte Gewohnheiten (Abfahrtszeit, Verbrauch eigener Geräte) alle 6 Stunden. Bei einem Fehler rechnet Sunflow mit den zuletzt bekannten Werten weiter.
- Eine fehlgeschlagene Neuplanung wird mit wachsendem Abstand wiederholt: 5, 10, 20, höchstens 30 Minuten.

## 0.30.6

- Schattenbetrieb: Schlägt die Neuplanung fehl, wird trotzdem weiter gemessen. Vorher fehlten dann alle Messwerte, bis wieder ein Plan gelang.
- Eine fehlgeschlagene Neuplanung wird nach 5 Minuten wiederholt statt jede Minute; Home Assistant wird bei einem Problem nicht mehr minütlich mit allen Abfragen belastet.
- Planung: Ist die Neuberechnung fehlgeschlagen, sagt eine Warnung, von wann der angezeigte Plan stammt und warum es keinen neueren gibt.

## 0.30.5

- Planung: Schlägt in Home Assistant eine einzelne Abfrage fehl (z. B. die PV-Prognose einer Fläche, der Verlauf des Hausverbrauchs oder eines Verbrauchers, die Statistik zur letzten Vollladung), wird der Plan trotzdem berechnet. Eine Warnung nennt, welche Quelle fehlte und mit welcher Fehlermeldung. Vorher brach der Plan mit „unknown_error: Unknown error“ ohne Hinweis auf die Ursache ab.

## 0.30.4

- Planung: Der eingeblendete Gesamtverbrauch lässt sich über seinen Legendeneintrag hervorheben wie die anderen Linien; ein- und ausgeblendet wird nur über das Häkchen.

## 0.30.3

- Planung: Im Diagramm „PV-Erzeugung und Hausverbrauch“ lässt sich zusätzlich der Gesamtverbrauch einblenden (Hausverbrauch mit steuerbaren Verbrauchern, gemessen und geplant). Die Auswahl wird im Browser gemerkt.

## 0.30.2

- Planung: Die gemessene Linie „Hausverbrauch“ enthält die steuerbaren Verbraucher (Wallbox, Heizstab, E-Bike) nicht mehr doppelt – sie haben eigene Linien, und die Prognose rechnet ebenfalls ohne sie.
- Analyse → Schattenbetrieb: Die Lastprognose wird mit dem gemessenen Verbrauch ohne steuerbare Verbraucher verglichen; vorher ließ ein Ladevorgang die Prognose zu niedrig aussehen.

## 0.30.1

- Analyse → Wirkungsgrade: Tage mit wenig Energie (im Herbst und Winter häufig) bleiben nicht mehr leer, sondern werden mit den folgenden Tagen zu einem Wert zusammengefasst. Die Linien reichen dadurch wieder bis an die letzten Tage.
- Diagramme: Einzelne Werte ohne Nachbarn werden als Punkt gezeichnet statt unsichtbar zu bleiben.

## 0.30.0

- Notstrom-Reserve gilt jetzt in jeder Strategie (Ausgewogen, PV aufnehmen, Autarkie): eigener Schalter mit Prozentwert unter Einstellungen → Planung. Im Alltag wird der Speicher nie unter die Reserve geplant; sie bleibt dem Notstrom bei Stromausfall vorbehalten.
- Die Strategie „Notstrom sicherstellen“ entfällt. Bestehende Einstellungen werden automatisch umgestellt (Strategie „Ausgewogen“, Reserve eingeschaltet mit dem bisherigen Wert); das Original bleibt als `config.v1.json` erhalten.

## 0.29.1

- Einstellungen: „Netzdienlich“ erklärt genauer, wie die Grenzen wirken (weiches Ziel, keine Abregelung, kein Ersatz für die Begrenzung im Wechselrichter).

## 0.29.0

- Datenmigration zwischen Versionen: Die Konfiguration wird bei einem Update schrittweise auf den neuen Stand gebracht; das Original bleibt als `config.v<n>.json` erhalten.
- Nach einem Downgrade startet Sunflow auch mit einer Konfiguration der neueren Version: Werte, die die ältere Version nicht kennt, nehmen den Standardwert an (Hinweis im Log).
- Die Schattendaten-Datenbank hat eine Schema-Version und wird ebenfalls schrittweise aktualisiert. Exporte älterer und neuerer Versionen lassen sich importieren.

## 0.28.2

- Analyse: Das Diagramm „Kosten pro Tag“ hat eine Legende.

## 0.28.1

- Planung: Jeder steuerbare Verbraucher hat im Diagramm „PV-Erzeugung und Hausverbrauch“ eine eigene Farbe (gemessen und geplant), passend zur Karte unter „Steuerbare Verbraucher“.
- Ein Klick auf einen Legendeneintrag hebt dessen Linien hervor und blendet die anderen ab; ein zweiter Klick zeigt wieder alle.

## 0.28.0

- PV-Prognose lernt je Dachfläche: Unter Einstellungen → Tarif & Prognose lässt sich jeder Prognose-Fläche zuordnen, von welchen PV-Strings sie gemessen wird. Sunflow lernt dann die Abweichung jeder Fläche einzeln (z. B. Verschattung am Vormittag) und korrigiert die Planung flächengenau. Anzeige unter Analyse → Lernen.

## 0.27.1

- Energy values entered in kWh keep three decimals: small daily needs such as 0,04 kWh
  were shown as 0 and could be overwritten with 0 when saving again

## 0.27.0

- Analyse → "Kosten pro Tag" (30/90 days): net electricity cost per day (import minus
  feed-in) next to what it would have cost without PV and battery, from the energy
  counters and the price history of the tariff entity; totals for net cost, savings
  and feed-in revenue
- Planung → "Kostenverlauf": accumulated electricity cost over the plan with Sunflow
  against plain self-consumption

## 0.26.0

- Grid friendly option (Standards), combinable with every strategy: export and import
  above set powers cost 20 ct/kWh extra in the plan. The battery then charges in the
  midday PV peak instead of filling up in the morning, and large consumers run one
  after another instead of together, as long as that costs little

## 0.25.0

- New battery strategy "Autarkie": as little grid import as possible instead of the
  lowest cost. Every imported kWh counts far above price differences, no grid
  charging, the battery covers demand right away, loads go to the PV surplus.
  Selectable in Standards and Steuerung

## 0.24.0

- Own consumers with a power sensor learn their use from the last 10 days: daily
  energy (mean) and running power (90th percentile while on). Without a set daily
  energy the plan uses the learned one; when the settings differ by more than 30 %, a
  recommendation offers the measured values. The consumer list shows them

## 0.23.1

- "Hausverbrauch pro Tag" is a start value: it scales the load forecast only while less
  than 7 days of consumption history exist; afterwards the measured hourly means count
  (and from 14 days the learned model). Standards shows a suggestion from the history
  (base load without large loads) to take over. The recommendation to change the daily
  consumption is dropped

## 0.23.0

- Learned departure of the car: from the last 14 days of the loadpoint's "connected"
  history, the typical unplug time per workday/weekend (median of stays of at least
  2 h, from 3 sessions on) becomes the deadline when the car is plugged in without an
  evcc plan or a "ready by" time. Planung marks it as "übliche Abfahrt, gelernt"

## 0.22.0

- PV forecast uncertainty: the plan can lean towards the pessimistic forecast (Solcast
  p10). Standards → "Unsichere PV-Prognose": expected value, balanced (default, 30 %
  of the way to p10) or cautious (70 %). Forecast.Solar planes take the relative
  range of Solcast in the same interval instead of counting as 0 in p10. The learned
  PV correction applies to both values

## 0.21.0

- Learned load forecast: shadow mode now stores the forecast outside temperature per
  slot. From 14 days of such data a regression model learns the base load per hour,
  the weekend difference and the heating (and cooling) share of the temperature;
  recent days weigh more. The plan uses it once it beats the hourly average on held
  out days. Analyse → "Lernen" shows its state and accuracy

## 0.20.1

- Shadow mode records the power of the controllable consumers; the load recommendation
  compares the forecast with the measured base load without them (a heating rod below
  the large-load threshold no longer counts as household consumption). It appears once
  three days of such data exist

## 0.20.0

- Recommendations (Analyse, count as badge on the menu): Sunflow checks settings, plan,
  shadow mode and energy counters and suggests changes, where possible with one click:
  battery efficiency from the counters, missing daily energy or heating price, priority
  limits that keep PV from hot water, plan more expensive than self-consumption, base
  load forecast off by more than 10 % (large loads left out), implausible hot water
  demand, PV learning off or no weather entity with cloud coverage. Dismissed
  recommendations return when their numbers change

## 0.19.5

- Entity assignment: German names for all roles (PV strings, energy counters, plan
  values); values rounding to zero show as "0 W" instead of "−0 W"

## 0.19.4

- Sofar battery and PV power in 10 W steps: the live value is the sum of the per input
  values (0.01 kW) instead of the totals (0.1 kW); statistics keep using the totals
- Units as before: W below 1 kW, kW above with two decimals (never finer than the
  source entity resolves)

## 0.19.3

- Powers in W are always shown without decimals (e.g. Tibber Pulse "75 W" instead
  of "75,00 W"); kW keeps the entity's precision

## 0.19.2

- Live: weather on the right in the title line (wraps below on narrow screens)

## 0.19.1

- Live and Steuerung show powers like their source entity in Home Assistant: in the
  entity's unit (W or kW) with its display precision (the user's setting, else the
  integration's). Values coming from a coarse entity (e.g. Sofar battery power total
  in 0.1 kW steps) therefore look exactly like in the integration

## 0.19.0

- Live: weather strip below the title with the current condition, temperature and
  cloud coverage and the next 8 hours (symbol, temperature, cloud coverage;
  precipitation as tooltip), from the weather entity chosen in Standards. The forecast
  is cached for 10 minutes

## 0.18.1

- Weather choice: shows the provider ("Home (Open-Meteo)") and checks the cloud
  coverage in the hourly forecast that Sunflow uses. Open-Meteo was wrongly marked
  "ohne Bewölkungsangabe" because its current state has no cloud coverage

## 0.18.0

- PV forecast learns from this site: shadow mode now also stores the uncorrected
  forecast and the forecast cloud coverage of a weather entity (e.g. met.no). From
  forecast vs. measurement, correction factors are learned per hour and cloud class
  (clear, mixed, overcast), cautiously with little data, and applied to the plan
- Standards: switch "PV-Prognose an die Anlage anpassen" and choice of the weather
  entity; Analyse: learned factors and mean deviation before/after; Planung: hint
  how much the forecast was adapted

## 0.17.2

- Fix "401: Unauthorized" after a while inside Home Assistant: the Supervisor expires
  the Ingress session when HA's panel is in the background. Sunflow now renews it
  through HA's connection in the surrounding page and repeats the request, and renews
  it right away when the tab comes back after more than 5 minutes. If renewing is not
  possible, a clear message asks to reload

## 0.17.1

- Battery care: "Spätestens nach … Tagen aus dem Netz". When the battery was not full
  for that long (e.g. in winter), the full charge is planned from the grid by the end of
  the cheapest price window of the next 24 h, also when grid charging is otherwise off;
  PV available before is still used first

## 0.17.0

- Battery care (Standards): everyday charge limit (e.g. 90 %) and a full charge every
  N days for balancing and calibration. The last full charge is read from the SoC
  statistics; when due, the limit is lifted and the full charge is aimed at the end of
  the PV surplus of that day (soft target, no forced grid charging). At the limit the
  plan limits charging instead of self-consumption. The limit appears in the SoC chart
- Weekdays in plan hints in German ("Mo 16:30" instead of "Mon 16:30")

## 0.16.0

- Standards: battery wear either directly in ct/kWh or calculated from purchase price,
  rated cycles, capacity, depth of discharge and remaining capacity at end of life
  (price / (cycles x mean capacity x depth of discharge)); the inputs are kept

## 0.15.3

- Planung: the measured lines (SoC, PV, consumption, grid import and export) reach up
  to "now": the latest live reading closes the gap left by Home Assistant's statistics,
  which lag up to a quarter of an hour. The view refreshes every 2 minutes

## 0.15.2

- Planung: below "Geplante Steuerung jetzt" the tile shows what the battery really does
  ("lädt 2,9 kW aus dem Netz"); when that differs from the plan, a hint says that
  another control is active (Sunflow only plans so far)
- Live: no "0 W + 0 W" in the PV node when the string row is shown

## 0.15.1

- Planning no longer stops with "Ladestand nicht lesbar" when the SoC sensor is
  briefly "unknown" right after an app start: the last valid value from the Home
  Assistant history (up to 6 h old) is used; a hint appears only when it is older
  than 30 minutes
- Live: details inside the circles ("kein Auto", "ausgeglichen", sums) shrink to fit
  instead of overlapping the circle

## 0.15.0

- Live: PV and battery stay in the diagram at 0 W (inverters reporting "unavailable"
  at night count as 0 W)
- Live: PV per string as a row of small nodes flowing into the PV total (Sofar: power
  per MPPT; Huawei: voltage x current per string; DC values)

## 0.14.2

- Own consumers can be edited after adding them ("Bearbeiten": name, switch, power
  sensor, power, daily energy) and removed directly in the list; planning settings
  stay unchanged. Adding or editing them replans right away

## 0.14.1

- Short "unknown"/"unavailable" phases of sensors (e.g. single failed Modbus reads of
  the Sofar SoC, every ~20 minutes) are bridged with the last valid value for up to
  15 minutes, so the plan no longer fails with "Ladestand nicht lesbar"
- Live: the battery stays in the diagram when only its SoC is missing; consumers
  always fit into the diagram (it grows without a battery row); today's figures sit
  at the bottom of the screen
- PC: main menu centred

## 0.14.0

- Analyse: new chart "Wirkungsgrade" (30 days, 90 days, 1 year) with one value per
  day for every battery (round trip incl. standby, corrected by the SoC change) and
  every inverter with AC and DC counters (e.g. Huawei); energy-weighted average in the
  legend. Days with little energy or gaps in the statistics stay empty
- Charts support an axis not starting at zero and label long ranges with dates

## 0.13.0

- New parameter "Wirkungsgrad Wechselrichter" (per direction, default 96,5 %). Grid
  charging and discharging to the house pass the inverter's AC/DC conversion, PV
  charging on the DC side of a hybrid inverter does not, so the plan now tells PV
  charging and grid charging apart
- "Wirkungsgrad Speicher" now means the battery at its terminals (cells and DC/DC),
  exactly what the battery energy counters measure; the suggestion from the counters
  can be taken over as is. Check the value after the update: a value entered as
  overall round trip before now counts the inverter twice

## 0.12.1

- Fix: the "Expertenmodus" switch text was cut off when the step list needed much
  room; the step column has a fixed width and its styles no longer leak into the
  main menu

## 0.12.0

- Main menu with icons: on the PC at the top with icon and text, stays in view while
  scrolling; on phones a fixed icon bar at the bottom (names as tooltip and for
  screen readers)

## 0.11.4

- Expertenmodus only for technical details: battery efficiency, vehicle charging
  efficiency and daily hot water demand are always visible in Kenndaten. Manual entity
  assignment ("Entitäten zuordnen"), role table, sub-devices, the grid offset and the
  device test's service calls stay in expert mode; the assignment is offered anyway
  when a required value was not found

## 0.11.3

- "Expertenmodus" switch in its own row below the setup steps, always visible on
  phones (was at the end of the scrolling step row)

## 0.11.2

- Charts: value labels sit on their grid lines inside the plot, so there is no empty
  margin on the left; all charts keep a common time axis
- Phone: outer margins halved (8 px)

## 0.11.1

- Inside Home Assistant (Ingress panel) the own logo and name are hidden: HA's toolbar
  already shows them. A lost connection is then shown as a hint below the tabs
- Phone: smaller tabs so all five fit better

## 0.11.0

- Fix: the Forecast.Solar forecast was one hour late. Its watt_hours_period is the
  energy of the interval ending at the timestamp; Home Assistant's hourly aggregation
  files it under the following hour. Sunflow now reads the raw series and spreads each
  interval onto its 30 minute grid (independent of daylight saving time)
- Battery efficiency: with at least 7 days of energy counters, the losses are split
  into conversion (per kWh, used for planning) and the battery's constant standby
  draw (BMS, DC/DC converters), shown in W
- Phone layout: no frame around the content, compact header and step row, charts sized
  to the screen with readable labels, larger text in the Live diagram
- "Expertenmodus" moved from the header to the end of the setup steps and is
  remembered in the browser

## 0.10.2

- "Selbstverbrauch wiederherstellen": the device test stores a marker in /data from
  its first write until the restore succeeded. If the app crashed, the connection to
  Home Assistant was lost or the restore failed, every view shows a hint with a button
  that sets self-consumption and the previous operating mode again (after
  confirmation; nothing is written automatically)

## 0.10.1

- Kenndaten: devices can be removed ("Entfernen", with confirmation); overrides,
  priority entry, limits and measurement choice referring to them are cleaned up
- Wording: "Steuerbare Verbraucher" instead of "Ladepunkte" in Planung; the finished
  setup points to Live, Steuerung and Planung; German decimals for plane capacity and
  minimum SoC

## 0.10.0

Robustness and safety
- Commands to Home Assistant time out after 60 s; a malformed message no longer stops
  the connection reader; a dead connection is detected and re-established; caches are
  refreshed after a reconnect. Before, one unanswered command could block the whole UI
- The background runner never stops on unexpected errors, retries a failed replan on
  the next minute and replans after every settings change
- Runner, samples and /plan use the shared snapshot cache; without a plan yet, all
  tabs wait for one shared computation instead of each computing one
- As a Home Assistant App the API only answers the Ingress proxy (health excepted)
- The action and setup endpoints only show service calls; real writes remain
  exclusively in the confirmed device test
- config.json is written with fsync and keeps config.json.bak; fields of newer versions
  survive; a broken file gives a clear message; NaN/Infinity are rejected
- Shadow database access is serialized; unreadable imports give a clear error

Planning
- New actions "Entladen begrenzt" and "Laden begrenzt": partial discharges were
  shown as "Speicher halten", which would have left the planned discharge unused
- Calmer export: no feed-in before the battery is full when that costs nothing
- Priority limits no longer block the battery for the whole horizon when a car cannot
  reach its level (e.g. unplugged before); planning with limits takes ~1 s instead of
  up to 11 s
- Fair comparison "ohne Sunflow": optional loads (Nur PV, or with an alternative heat
  source) only use PV surplus there; costs include energy covered by the alternative
- Warning when the hot water tank has no cost of its usual heating
- Without grid charging / battery export, limits apply to the surplus over the house load
- Load forecast scales each calendar day to the daily consumption

## 0.9.2

- Priority limits can refer to the hot water tank (in °C), e.g. wallbox only from
  55 °C hot water, home battery only from 60 °C hot water and 80 % car
- Setup opens even when Home Assistant is unreachable, with "Erneut versuchen"
- Fixed: a failed fixed-price save lost the typed price and "Weiter" moved on;
  renaming a forecast plane could be overwritten by a quick assignment; typed numbers
  in Standards were reverted when changing the strategy; the device step could fail
  to render after changing a role; older previews could overwrite newer ones
- Analyse: period switch keeps the selected period, errors are shown, import is a
  keyboard reachable button with progress
- Steuerung: cards fit phone width, unknown power shows "–", errors are kept
- Dark mode: text on filled buttons and step dots has sufficient contrast
- Wizard timers pause in hidden tabs like the views

## 0.9.1

- New icon: the sun with three energy flows to storage, house and car. Used for the
  app, the integration (local brand folder, no brands repository entry needed) and
  the header of the UI

## 0.9.0

- New profile: Tibber Pulse via the tibber_local integration (grid power 16.7.0,
  meter readings 1.8.0 / 2.8.0)
- Messquellen (setup step "Bilanzprüfung"): choose which device measures the grid and
  the house consumption. "Automatisch" prefers a dedicated meter over the inverter.
  Applies to Live, history, energy balance and shadow mode. Before, grid counters of
  several devices would have been added up in the energy balance

## 0.8.0

- The home battery can have priority limits too: ranked below a vehicle, it only
  charges from PV once the car reached the set level (grid charging stays possible)
- Settings: "Weiter", "Zurück", the step list and leaving the tab save unsaved
  edits of the current step (Geräte, Kenndaten, fixed price, Standards). Invalid
  values keep you on the step with an error message

## 0.7.7

- Much less load on Home Assistant: registries (devices, entities, integrations) are
  cached for 2 minutes and all states for 4 seconds, shared by all requests. Before,
  every poll of every open tab fetched several MB from Home Assistant (every 5 s in Live)
- Views stop polling while the browser tab is hidden and never overlap requests;
  requests time out after 60 s instead of hanging

## 0.7.6

- Priority limits pointing to a lower entry are no longer deleted when reordering:
  they are hidden, not used, and apply again once the entry moves up

## 0.7.5

- Priority limits only refer to higher entries: the top entry has none, the second
  can wait for the first, and so on. Reordering drops limits that would point to a
  lower entry

## 0.7.4

- Steuerung → Priorität: buttons in the limits form ("Übernehmen", "Abbrechen") were
  cut off to the width of the arrow buttons

## 0.7.3

- Planung: the price chart uses the same time axis as the charts above (history
  plus plan) and marks "now"; all charts share the same left margin so the time
  axes line up

## 0.7.2

- Fix: planning failed with an internal error as soon as a priority limit on the
  home battery was set (since 0.6.0)
- Server errors show the HTTP status instead of a JSON parse error

## 0.7.1

- Battery efficiency suggestion counts as reliable only from 10 full cycles (was 2);
  the counter based estimate looks back 90 days instead of 30

## 0.7.0

- Energy counters (kWh) are used where available: Sofar PV, consumption, import,
  export, battery charge/discharge; Huawei AC yield and DC input. Grid counters of
  the HA energy dashboard (utility meter) take precedence over inverter counters
- Live "Heute" and the new Analyse section "Energiebilanz" (today, 7 days, 30 days)
  come from the counters: autarky, self-consumption, battery in/out, inverter
  efficiency (AC/DC). Integrated power means remain the fallback
- Battery efficiency suggestion uses the charge/discharge counters of the last 30 days

## 0.6.0

- Priority limits: a consumer in the priority list can wait until the home battery
  and/or a vehicle reached a level (e.g. hot water only from 80 % home battery and
  90 % car). Until then it gets no PV or battery energy and only runs on grid power
  if its target requires it. A vehicle only holds others back while it is plugged in
  and charging; a limit above its target counts as met at the target. "Sofort"
  ignores the limits

## 0.5.4

- Steuerung: consumers offer Kostenoptimal, Nur PV, Sofort and Pause; the default from
  the settings is marked "Standard". Before, a device set to "Nur PV" by default could
  not be switched to cost optimal

## 0.5.3

- Hot water and daily consumers are planned for every day of the horizon: after the
  first need, each further 24 h window gets its own daily need. Before, a tank heated
  on the first day got no PV surplus on the second day although energy was exported
- New advanced heater parameter "Energiebedarf pro Tag" (default: tank cools by 10 K
  per day, about 3.5 kWh for 300 l)

## 0.5.2

- Live view fits the window without scrolling: the flow diagram scales to the
  available height, title and today's tiles are more compact
- More compact layout everywhere: smaller header, spacing and headings, flatter charts

## 0.5.1

- Ladestand chart shows the limits: full capacity, discharge limit (min SoC)
  and the backup reserve while the backup strategy is active

## 0.5.0

- New tab layout: Live | Steuerung | Planung | Analyse | Einstellungen
- Steuerung: switch the battery strategy and each consumer (Standard, Nur PV,
  Sofort, Pause) or its target with one click, for today, 4 hours or until
  further notice; the plan is recomputed right away
- Priorities: order consumers and the home battery; consumers below the battery
  only get surplus the battery cannot take
- Defaults for strategy, wear, grid charging, backup reserve and consumption moved
  to the new setup step "Standards"
- Analyse holds the shadow mode comparison and data export/import
- Planung shows the last computed plan instantly instead of recomputing on open

## 0.4.3

- evcc loadpoints are recognized when Home Assistant runs in German: the evcc
  integration names them "Ladepunkt …" instead of "Loadpoint …"

## 0.4.2

- Live view adapts to the configuration: components that are not configured are
  hidden with their lines (no PV, no battery, no grid measurement)
- Several batteries are shown as one node (summed power, capacity-weighted SoC)
  with the single values below
- Grid and house consumption come from any device that measures them

## 0.4.1

- Plans fill the battery first and export afterwards (export early only paid off if
  the PV forecast held; a small risk premium now prefers late export)
- Export is limited to the current PV: a fixed feed-in tariff does not pay for
  stored grid energy. This also removes a slow solver case with cheap night prices
- Solver stops at 0.1 % from the optimum and keeps the best plan on its time limit

## 0.4.0

- New "Live" tab: animated power flows between PV, grid, battery (with SoC ring),
  house and consumers (wallbox, hot water, own consumers), refreshed every 5 s
- Today: PV, consumption, grid import, export, autarky and self-consumption

## 0.3.6

- Battery efficiency is now the round trip (charge + discharge), as usually stated;
  the planner splits it evenly. Stored values are read as round trip (default 90 %)
- The history estimate shows and applies the round-trip value

## 0.3.5

- Shadow mode classifies what the battery did from the grid meter and battery power;
  coarse DC PV readings caused false "Entladen ins Netz" entries (also for old data)
- The data export contains the current plan (plan.json) for analysis

## 0.3.4

- Clearer wording for whether the home battery may discharge into a consumer

## 0.3.3

- Chart axes: round steps (1, 2 or 5 x 10^n, up to six intervals), no decimals for
  whole numbers and values from 10 on

## 0.3.2

- Searchable entity choice (type to filter by name or entity id, keyboard support)
  for own consumers and for adjusting role mappings

## 0.3.1

- Dropdowns no longer close right after opening (selects were wrapped in labels)

## 0.3.0

- Planning strategy per loadpoint and consumer: cost-optimal, PV surplus only or
  as fast as possible; own target, daily ready-by time, home battery allowed or not
- Alternative heat source (district heating, gas) for hot water: electric heating
  only when cheaper than the alternative
- Own consumers (Shelly, ESPHome, any switch) with daily energy need, planned like
  loadpoints; their measured power is separated from the base load

## 0.2.6

- Calmer plans: when several schedules cost the same (e.g. full battery, PV surplus
  split between export and the heater), the smooth one is chosen instead of a zigzag

## 0.2.5

- Shadow mode: deviation is shown as actual relative to the forecast
  (e.g. "−40 % ggü. Prognose" when less was produced than forecast)

## 0.2.4

- Chart y-axis labels are no longer cut off on the left

## 0.2.3

- PV forecast chart in the setup wizard: one color per forecast plane (stacked),
  legend with provider, plane, plant and energy

## 0.2.2

- Forecast.Solar planes without a specific device name get numbered names
- Each forecast plane shows today's forecast to tell planes apart
- Forecast planes can be renamed in the setup wizard

## 0.2.1

- Fix "Connection lost" on larger installations: websocket messages above 4 MB
  (states, registries) are now accepted
- Log why the connection to Home Assistant closes

## 0.2.0

- Installable as Home Assistant App (code is fetched from GitHub at build time)
- Setup wizard, overview with forecasts, planning and shadow mode
- Data export for analysis on another machine

## 0.1.0

- Initial project structure
