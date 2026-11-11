import { LitElement, html, svg, css, nothing } from "../../vendor/lit-all.min.js";
import { api, renewIngressSession } from "../lib/api.js";
import { confirmDialog } from "../components/dialog.js";
import { poll } from "../lib/poll.js";
import { lineChart, SERIES_COLORS, UNASSIGNED_COLOR } from "../components/charts.js";
import { formatValue } from "../lib/format.js";
import { decimal, locale, t } from "../lib/i18n.js";
import { ACTIONS } from "../lib/actions.js";
import "../components/battery-planner.js";
import { toggleSheet } from "../lib/display-toggles.js";

const dayTime = new Intl.DateTimeFormat(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
const REFRESH_MS = 5 * 60 * 1000;
const PV_SUN_POLL_MS = 10_000;

/** Shadow mode evaluation: forecasts and decisions against reality, export/import. */
class SunflowAnalysis extends LitElement {
  static properties = {
    _shadow: { state: true },
    // Battery device id -> name, for the plan against actual per battery
    _batteryNames: { state: true },
    _error: { state: true },
    _importResult: { state: true },
    _energy: { state: true },
    _period: { state: true },
    _energyError: { state: true },
    _efficiency: { state: true },
    _efficiencyDays: { state: true },
    _pvLearning: { state: true },
    _inventory: { state: true },
    _pvSun: { state: true },
    _pvPlanes: { state: true },
    _pvPlaneDays: { state: true },
    _appliances: { state: true },
    _inventoryError: { state: true },
    _recommendations: { state: true },
    _costs: { state: true },
    _costDays: { state: true },
    _applying: { state: true },
    _importing: { state: true },
  };

  constructor() {
    super();
    this._shadow = null;
    this._error = null;
    this._energy = null;
    this._period = "7d";
    this._efficiency = null;
    this._efficiencyDays = 60;
    this._pvPlanes = null;
    this._pvPlaneDays = 7;
  }

  connectedCallback() {
    super.connectedCallback();
    // Cached for an hour in the backend; not part of the polling
    this._loadInventory();
    this._loadPvSun();
    this._loadPvPlanes();
    api.appliances().then((list) => (this._appliances = list)).catch(() => (this._appliances = null));
    this._load();
    this._stopPolling = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling();
    clearTimeout(this._pvSunTimer);
  }

  async _loadEnergy() {
    const period = this._period;
    try {
      const energy = await api.energy(period);
      // A slower answer for a period that is no longer selected must not win
      if (period === this._period) {
        this._energy = energy;
        this._energyError = null;
      }
    } catch (err) {
      if (period === this._period) this._energyError = t("Energiebilanz nicht verfügbar: {error}", { error: err.message });
    }
  }

  async _loadEfficiency() {
    const days = this._efficiencyDays;
    try {
      const series = await api.efficiencyHistory(days);
      if (days === this._efficiencyDays) this._efficiency = series;
    } catch {
      // Optional section; without energy counters there is nothing to show
      if (days === this._efficiencyDays) this._efficiency = [];
    }
  }

  async _loadPvPlanes() {
    const days = this._pvPlaneDays;
    try {
      const data = await api.pvPlanes(days);
      if (days === this._pvPlaneDays) this._pvPlanes = data.planes;
    } catch {
      // Optional section; without planes or shadow data there is nothing to show
      if (days === this._pvPlaneDays) this._pvPlanes = [];
    }
  }

  _setPvPlaneDays(days) {
    this._pvPlaneDays = days;
    this._loadPvPlanes();
  }

  _setEfficiencyDays(days) {
    this._efficiencyDays = days;
    this._loadEfficiency();
  }

  _setPeriod(period) {
    this._period = period;
    this._loadEnergy();
  }

  async _loadRecommendations() {
    try {
      this._recommendations = await api.recommendations();
    } catch {
      this._recommendations = null;
    }
    // The menu badge counts them
    this.dispatchEvent(new CustomEvent("sf-recommendations", {
      detail: { count: this._recommendations?.length ?? 0 }, bubbles: true, composed: true,
    }));
  }

  async _applyRecommendation(rec) {
    const action = rec.action;
    if (action.type === "navigate") {
      this.dispatchEvent(new CustomEvent("sf-navigate", { detail: action, bubbles: true, composed: true }));
      return;
    }
    this._applying = rec.id;
    try {
      if (action.type === "set_parameter") {
        await api.updateDevice(action.device_id, { parameters: { [action.name]: action.value } });
      } else if (action.type === "set_optimizer") {
        const settings = (await api.config()).optimizer;
        await api.setOptimizer({ ...settings, ...action.changes });
      }
      this._error = null;
      await this._loadRecommendations();
    } catch (err) {
      this._error = t("Empfehlung konnte nicht übernommen werden: {error}", { error: err.message });
    } finally {
      this._applying = null;
    }
  }

  async _dismissRecommendation(rec) {
    try {
      await api.dismissRecommendation(rec.id, rec.fingerprint);
      await this._loadRecommendations();
    } catch (err) {
      this._error = t("Ausblenden fehlgeschlagen: {error}", { error: err.message });
    }
  }

  _renderRecommendations() {
    const list = this._recommendations;
    if (!list?.length) return nothing;
    const actionLabel = (a) => (a.type === "navigate" ? t("Öffnen") : t("Übernehmen"));
    return html`
      <section class="suggestion">
        <h2>${t("Empfehlungen")}</h2>
        <div class="recommendations">
          ${list.map(
            (rec) => html`<article class="recommendation ${rec.severity}">
              <strong>${rec.title}</strong>
              <p class="small">${rec.text}</p>
              <div class="actions">
                ${rec.action
                  ? html`<button class="ok" ?disabled=${this._applying === rec.id} @click=${() => this._applyRecommendation(rec)}>
                      ${actionLabel(rec.action)}</button>`
                  : nothing}
                <button class="secondary" @click=${() => this._dismissRecommendation(rec)}>${t("Ausblenden")}</button>
              </div>
            </article>`,
          )}
        </div>
      </section>
    `;
  }

  async _loadCosts() {
    const days = this._costDays ?? 30;
    try {
      const costs = await api.costHistory(days);
      if (days === (this._costDays ?? 30)) this._costs = costs;
    } catch {
      this._costs = [];
    }
  }

  _renderCosts() {
    const days = this._costs ?? [];
    if (!days.length) return nothing;
    const euro = new Intl.NumberFormat(locale(), { style: "currency", currency: "EUR" });
    const sum = (key) => days.reduce((total, d) => total + (d[key] ?? 0), 0);
    const hasWithout = days.some((d) => d.without_pv_cost !== null);
    const point = (d, key) => {
      const [y, m, day] = d.date.split("-").map(Number);
      const start = new Date(y, m - 1, day);
      return { start, end: new Date(start.getTime() + 86_400_000), value: d[key] };
    };
    const lines = [
      { name: t("Nettokosten"), color: SERIES_COLORS[0], points: days.map((d) => point(d, "net_cost")) },
      ...(hasWithout
        ? [{ name: t("ohne PV und Speicher"), color: SERIES_COLORS[1], dashed: true, points: days.map((d) => point(d, "without_pv_cost")) }]
        : []),
    ];
    const lowest = Math.min(0, ...days.map((d) => d.net_cost));
    const incomplete = days.filter((d) => !d.complete).length;
    return html`
      <section>
        <div class="section-head">
          <h2>${t("Kosten pro Tag")}</h2>
          <div class="range" role="group" aria-label=${t("Zeitraum Kosten")}>
            ${[[30, t("30 Tage")], [90, t("90 Tage")]].map(
              ([value, label]) => html`<button class=${(this._costDays ?? 30) === value ? "active" : ""}
                aria-pressed=${(this._costDays ?? 30) === value}
                @click=${() => { this._costDays = value; this._loadCosts(); }}>${label}</button>`,
            )}
          </div>
        </div>
        <div class="tiles">
          <div class="tile now"><div class="small muted">${t("Nettokosten")}</div><div class="value">${euro.format(sum("net_cost"))}</div>
            <div class="small muted">${t("Bezug {import} − Einspeisung {export}", { import: euro.format(sum("import_cost")), export: euro.format(sum("export_revenue")) })}</div></div>
          ${hasWithout
            ? html`<div class="tile"><div class="small muted">${t("Ohne PV und Speicher")}</div><div class="value">${euro.format(sum("without_pv_cost"))}</div></div>
                <div class="tile"><div class="small muted">${t("Ersparnis durch PV und Speicher")}</div><div class="value">${euro.format(sum("savings"))}</div></div>`
            : nothing}
        </div>
        <div class="legend">
          ${lines.map((l) => html`<span class="legend-item"><span class="swatch" style="background:${l.color}"></span>${l === lines[0]
            ? t("Nettokosten (Bezug − Einspeisung)")
            : t("ohne PV und Speicher (alles aus dem Netz)")}</span>`)}
        </div>
        ${lineChart(lines, { label: t("Stromkosten pro Tag"), formatY: (v) => euro.format(v), yMin: lowest })}
        <p class="small muted help">${t("Aus Energiezählern und dem Preisverlauf der Tarif-Entität; Einspeisung zur eingestellten Vergütung.")}
          ${incomplete ? t("{count} Tag(e) mit Lücken in der Statistik.", { count: incomplete }) : ""}</p>
      </section>
    `;
  }

  async _load() {
    this._loadCosts();
    this._loadRecommendations();
    api.pvLearning().then((data) => (this._pvLearning = data)).catch(() => (this._pvLearning = null));
    this._loadEnergy();
    this._loadEfficiency();
    api.batteries().then((list) => (this._batteryNames = Object.fromEntries(list.map((b) => [b.device_id, b.inverter])))).catch(() => {});
    try {
      this._shadow = await api.shadow();
      this._error = null;
    } catch (err) {
      this._error = t("Analyse konnte nicht geladen werden: {error}", { error: err.message });
    }
  }

  render() {
    return html`
      <div class="heading">
        <div>
          <h1>${t("Analyse")}</h1>
          <p class="muted help">${t("Wie gut stimmen Prognosen und Entscheidungen mit der Wirklichkeit überein?")}</p>
        </div>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${this._renderRecommendations()}
      ${this._renderEnergy()}
      ${this._renderCosts()}
      <sf-battery-planner></sf-battery-planner>
      ${this._renderEfficiency()}
      ${this._renderPvPlanes()}
      ${this._renderPvLearning()}
      ${this._renderPvSun()}
      ${this._renderInventory()}
      ${this._shadow === null && !this._error ? html`<p class="muted">${t("Lade …")}</p>` : nothing}
      ${this._renderShadow()}
    `;
  }

  _renderEnergy() {
    const e = this._energy;
    const totals = e?.totals;
    if (!this._energyError && (!totals || Object.values(totals).every((v) => v === null))) return nothing;
    const wh = (v) => formatValue(v, "Wh");
    const pct = (v) => (v === null || v === undefined ? "–" : `${decimal((v * 100).toFixed(1))} %`);
    const tile = (label, value, hint = null, highlight = false) =>
      value === null || value === undefined || value === "–"
        ? nothing
        : html`<div class="tile ${highlight ? "now" : ""}">
            <div class="small muted">${label}</div><div class="value">${value}</div>
            ${hint ? html`<div class="small muted">${hint}</div>` : nothing}
          </div>`;
    const fromDashboard = Object.entries(e?.origin ?? {}).filter(([, o]) => o === "energy_dashboard").map(([k]) => k);
    return html`
      <section>
        <div class="section-head">
          <h2>${t("Energiebilanz")}</h2>
          <div class="range" role="group" aria-label=${t("Zeitraum")}>
            ${[["today", t("Heute")], ["7d", t("7 Tage")], ["30d", t("30 Tage")]].map(
              ([value, label]) => html`<button class=${this._period === value ? "active" : ""} aria-pressed=${this._period === value}
                @click=${() => this._setPeriod(value)}>${label}</button>`,
            )}
          </div>
        </div>
        ${this._energyError ? html`<div class="alert error" role="alert">${this._energyError}</div>` : nothing}
        ${totals ? html`<p class="small muted">${fromDashboard.length
          ? t("Aus den Energiezählern (Netz aus dem Energie-Dashboard) seit {time}", { time: dayTime.format(new Date(e.start)) })
          : t("Aus den Energiezählern der Wechselrichter seit {time}", { time: dayTime.format(new Date(e.start)) })}${e.consumption_origin === "balance"
          ? `; ${t("Verbrauch aus der Bilanz berechnet")}`
          : ""}.</p>
        <div class="tiles">
          ${tile(t("Autarkie"), pct(e.autarky), t("Verbrauch ohne Netzbezug"), true)}
          ${tile(t("Eigenverbrauch"), pct(e.self_consumption), t("PV selbst genutzt"), true)}
          ${tile(t("PV erzeugt"), totals.pv === null ? null : wh(totals.pv))}
          ${tile(t("Verbraucht"), e.consumption === null ? null : wh(e.consumption))}
          ${tile(t("Netzbezug"), totals.grid_import === null ? null : wh(totals.grid_import))}
          ${tile(t("Eingespeist"), totals.grid_export === null ? null : wh(totals.grid_export))}
          ${tile(t("Speicher geladen"), totals.battery_charge === null ? null : wh(totals.battery_charge))}
          ${tile(t("Speicher entladen"), totals.battery_discharge === null ? null : wh(totals.battery_discharge),
            e.battery_ratio === null ? null : t("{value} der geladenen Energie (ohne Ladestandsänderung)", { value: pct(e.battery_ratio) }))}
          ${e.inverters.map((i) => tile(t("Wirkungsgrad {name}", { name: i.name }), pct(i.efficiency),
            i.dc_wh === null ? null : t("{ac} AC aus {dc} DC", { ac: wh(i.ac_wh), dc: wh(i.dc_wh) })))}
        </div>` : nothing}
      </section>
    `;
  }

  _renderPvLearning() {
    const l = this._pvLearning;
    if (!l) return nothing;
    const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)} %`);
    const hours = Object.entries(l.by_hour ?? {});
    return html`
      <section>
        <h2>${t("Lernen")}</h2>
        <h3>${t("PV-Prognose")}</h3>
        <p class="small muted">
          ${l.enabled ? t("Die Planung nutzt die angepasste Prognose.") : t("Ausgeschaltet (Standards) – die Planung nutzt die Rohprognose.")}
          ${l.weather_entity
            ? html`${t("Gelernt wird aus dem Schattenbetrieb: Prognose gegen Messung je Stunde und Bewölkung laut")} <code>${l.weather_entity}</code>.`
            : t("Gelernt wird aus dem Schattenbetrieb: Prognose gegen Messung je Stunde (keine Wetter-Entität gefunden).")}
          ${t("Mit wenigen Tagen bleiben die Faktoren nahe 100 %.")}
        </p>
        ${l.slots
          ? html`
              <div class="tiles">
                <div class="tile now"><div class="small muted">${t("Gesamt")}</div><div class="value">${pct(l.overall)}</div>
                  <div class="small muted">${t("der Prognose kommt an")} · ${t("{days} Tage", { days: l.days })}</div></div>
                <div class="tile"><div class="small muted">${t("Klar")}</div><div class="value">${pct(l.by_cloud.clear)}</div></div>
                <div class="tile"><div class="small muted">${t("Wechselnd bewölkt")}</div><div class="value">${pct(l.by_cloud.mixed)}</div></div>
                <div class="tile"><div class="small muted">${t("Bedeckt")}</div><div class="value">${pct(l.by_cloud.overcast)}</div></div>
                <div class="tile"><div class="small muted">${t("Mittlere Abweichung")}</div>
                  <div class="value">${formatValue(l.error_after, "W")}</div>
                  <div class="small muted">${t("vorher {value}", { value: formatValue(l.error_before, "W") })}</div></div>
              </div>
              ${hours.length
                ? html`<p class="small muted">${t("Je Stunde: {hours}", {
                    hours: hours.map(([h, f]) => t("{hour} Uhr {factor}", { hour: h, factor: pct(f) })).join(" · "),
                  })}</p>`
                : nothing}`
          : html`<p class="muted">${t("Noch keine Tagesdaten – der Schattenbetrieb sammelt ab jetzt.")}</p>`}
        ${l.planes?.length
          ? html`<h3>${t("Je Dachfläche")}</h3>
              <ul class="small plane-list">
                ${l.planes.map(
                  (p) => html`<li><strong>${p.name}</strong>: ${p.slots ? pct(p.overall) : t("noch keine Daten")}
                    ${p.slots
                      ? html`<span class="muted">(${t("{days} Tage", { days: p.days })}${p.by_cloud.clear !== null ? ` · ${t("klar {value}", { value: pct(p.by_cloud.clear) })}` : ""}${p.by_cloud.overcast !== null
                          ? ` · ${t("bedeckt {value}", { value: pct(p.by_cloud.overcast) })}`
                          : ""})</span>`
                      : nothing}
                    ${p.learn_since
                      ? html`<span class="muted">· ${t("neu gelernt seit {date}", { date: new Date(p.learn_since).toLocaleDateString(locale()) })}</span>`
                      : nothing}
                    ${this._renderPlaneState(p.state, pct)}
                    <button class="link" @click=${() => this._restartPlane(p)}
                      title=${t("Nach Reinigung, Baumschnitt oder Modultausch: ältere Messwerte dieser Fläche nicht mehr verwenden")}>
                      ${t("Neu lernen ab heute")}</button></li>`,
                )}
              </ul>`
          : html`<p class="small muted help">${t("Tipp: Unter Einstellungen → Tarif & Prognose lassen sich Dachflächen ihren PV-Strings zuordnen; dann lernt Sunflow jede Fläche einzeln (z. B. Verschattung).")}</p>`}
        ${this._renderAppliances()}
        <h3>${t("Hausverbrauch")}</h3>
        ${l.load_model
          ? html`<p class="small">
              ${t("Modell aus {days} Tagen: je Stunde, Werktag/Wochenende und Außentemperatur", { days: l.load_model.days })}
              (${l.load_model.heating_w_per_k > 1
                ? t("+{power} W je Grad unter 15 °C", { power: Math.round(l.load_model.heating_w_per_k) })
                : t("kein Heizeinfluss erkennbar")}).
              ${t("Auf den letzten Tagen {model} Abweichung statt {average} mit dem Stundenmittel", {
                model: formatValue(l.load_model.error_model, "W"),
                average: formatValue(l.load_model.error_average, "W"),
              })}
              – ${l.load_model.useful ? t("die Planung nutzt das Modell.") : t("noch nicht besser, die Planung bleibt beim Stundenmittel.")}
            </p>`
          : html`<p class="small muted">${t("Sammelt Daten: Das Modell startet nach 14 Tagen mit Temperatur und Verbraucherleistung im Schattenbetrieb.")}
              ${t("Bis dahin plant Sunflow mit den Stundenmitteln der Historie.")}</p>`}
      </section>
    `;
  }

  async _loadPvSun(refresh = false) {
    clearTimeout(this._pvSunTimer);
    try {
      this._pvSun = await api.pvSun(refresh);
    } catch {
      this._pvSun = null;
      return;
    }
    // Learned in the background: ask again until it is done
    if (this._pvSun.computing) this._pvSunTimer = setTimeout(() => this._loadPvSun(), PV_SUN_POLL_MS);
  }

  /** Each plane's measured strings against Solcast's raw forecast, the past days and the plan ahead. */
  _renderPvPlanes() {
    const planes = this._pvPlanes?.filter((p) => p.history.length || p.forecast.length);
    if (!planes?.length) return nothing;
    const share = (value) => (value === null || value === undefined ? "–" : `${Math.round(value * 100)} %`);
    const kw = (v) => formatValue(v, "W");
    const colors = { raw: UNASSIGNED_COLOR, measured: SERIES_COLORS[1], expected: SERIES_COLORS[0] };
    return html`
      <section>
        <div class="section-head">
          <h2>${t("PV je Fläche")}</h2>
          <div class="range" role="group" aria-label=${t("Zeitraum PV je Fläche")}>
            ${[[3, t("3 Tage")], [7, t("7 Tage")], [14, t("14 Tage")]].map(
              ([value, label]) => html`<button class=${this._pvPlaneDays === value ? "active" : ""}
                aria-pressed=${this._pvPlaneDays === value} @click=${() => this._setPvPlaneDays(value)}>${label}</button>`,
            )}
          </div>
        </div>
        <p class="small muted help">${t("Je Dachfläche die gemessenen Strings gegen die rohe Prognose (Solcast), stündlich. Rechts der senkrechten Linie: die rohe Prognose mit ihrer Bandbreite (p10 bis p90) und was Sunflow nach dem Gelernten erwartet, bevor die Planung für Unsicherheit vorsichtiger rechnet.")}</p>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:${colors.raw}"></span>${t("Rohprognose")}</span>
          <span class="legend-item"><span class="swatch" style="background:${colors.measured}"></span>${t("Gemessen")}</span>
          <span class="legend-item"><span class="swatch" style="background:${colors.expected}"></span>${t("Erwartet (gelernt)")}</span>
        </div>
        ${planes.map((plane) => {
          const point = (name) => (p) => ({ start: p.start, end: p.end, value: p[name] });
          return html`
            <h3>${plane.name}</h3>
            <p class="small muted">${t("Gemessen {measured} der Rohprognose ({days} Tage) · erwartet {expected}", {
              measured: share(plane.measured_share),
              days: this._pvPlaneDays,
              expected: share(plane.expected_share),
            })}${plane.strings ? nothing : html` · ${t("keine Strings zugeordnet")}`}</p>
            ${lineChart(
              [
                { name: t("Rohprognose"), color: colors.raw, dashed: true, points: [...plane.history, ...plane.forecast].map(point("raw")) },
                { name: t("Gemessen"), color: colors.measured, points: plane.history.map(point("measured")) },
                { name: t("Erwartet (gelernt)"), color: colors.expected, points: plane.forecast.map(point("expected")) },
              ],
              {
                label: t("{name}: gemessen und Prognose", { name: plane.name }),
                formatY: kw,
                now: plane.forecast.length ? new Date(plane.forecast[0].start) : null,
                bands: [{ name: t("Bandbreite Rohprognose"), color: colors.raw, points: plane.forecast }],
              },
            )}
          `;
        })}
      </section>
    `;
  }

  _renderPvSun() {
    const data = this._pvSun;
    if (!data) return nothing;
    const learned = data.planes.filter((p) => p.correction);
    return html`
      <section>
        <div class="section-head">
          <h2>${t("PV nach Sonnenstand (Test)")}</h2>
          <button class="secondary" ?disabled=${data.computing} @click=${() => this._loadPvSun(true)}>
            ${data.computing ? t("Lernt …") : t("Neu lernen")}</button>
        </div>
        <p class="small muted help">${t("Lernt aus den vergangenen Solcast-Prognosen und den gemessenen Strings, wie viel der Prognose je Sonnenhöhe und -richtung ankommt. Der Backtest lernt ohne die letzten 3 Wochen und prüft auf diesen.")}
          ${data.enabled
            ? t("Die Planung nutzt es, wo es aus mindestens 60 Tagen gelernt hat und im Backtest besser ist.")
            : t("Läuft im Schattenbetrieb; einschalten unter Einstellungen → Planung → PV-Prognose.")}</p>
        ${data.error ? html`<div class="alert error" role="alert">${data.error}</div>` : nothing}
        ${data.computing && !learned.length ? html`<p class="muted">${t("Lernt aus der Historie …")}</p>` : nothing}
        ${data.planes.map((plane) => html`
          <h3>${plane.name}</h3>
          ${plane.soiling
            ? html`<p class="small warn-text">${t(plane.soiling.learned
                ? "Seit {date} nur {pct} % des Erwarteten – Laub, Schnee oder Schmutz? Diese Tage lernt Sunflow nicht mit."
                : "Seit {date} nur {pct} % der Prognose – Laub, Schnee oder Schmutz? Diese Tage lernt Sunflow nicht mit.", {
                date: new Date(plane.soiling.since).toLocaleDateString(locale()),
                pct: Math.round(plane.soiling.ratio * 100),
              })}</p>`
            : nothing}
          ${plane.level
            ? html`<p class="small warn-text">${t("Liefert in den letzten Tagen {pct} % des Gelernten – die Planung rechnet mit dieser Leistung, bis die Fläche wieder mehr liefert.", { pct: Math.round(plane.level * 100) })}</p>`
            : nothing}
          ${plane.missing
            ? html`<p class="small muted">${plane.missing === "no_history"
                ? t("Diese Prognosequelle hebt keine vergangenen Prognosen auf.")
                : t("Für diese Fläche fehlt die Leistung je String (z. B. keine Fläche zugeordnet oder nur Spannung und Strom).")}</p>`
            : nothing}
          ${plane.correction
            ? html`${data.enabled
                ? html`<p class="small ${plane.plannable ? "" : "muted"}">${plane.plannable
                    ? t("Die Planung nutzt diese Korrektur.")
                    : t("Die Planung bleibt hier bei der Korrektur nach Uhrzeit: zu wenig Historie oder im Backtest nicht besser.")}</p>`
                : nothing}${this._renderPvSunPlane(plane)}`
            : nothing}
        `)}
        ${this._renderEpisodes(data)}
        ${this._renderSeason(data)}
        ${data.computed_at ? html`<p class="small muted">${t("Gelernt {time}", { time: dayTime.format(new Date(data.computed_at)) })}</p>` : nothing}
      </section>
    `;
  }

  /** Soiling to expect by season per plane and how it compared with keeping the current level (shadow mode). */
  _renderSeason(data) {
    const season = data.season ?? {};
    const keys = Object.keys(season);
    if (!keys.length && !data.snow) return nothing;
    const names = Object.fromEntries(data.planes.map((p) => [p.key, p.name]));
    const pct = (v) => `${Math.round(v * 100)} %`;
    const month = (m) => new Date(2026, m - 1, 1).toLocaleDateString(locale(), { month: "short" });
    const weekday = (iso) => new Date(iso).toLocaleDateString(locale(), { weekday: "short" });
    const snow = data.snow;
    return html`
      <h3>${t("Erwartung nach Jahreszeit (Test)")}</h3>
      <p class="small muted help">${t("Aus der Verschmutzung in der Historie: je Monat der verschmutzte Anteil der Tage und wie viel die Fläche dann lieferte, und nach wie vielen Tagen Laub nach einer Reinigung zurückkam. Die Planung rechnet weiter mit dem aktuellen Stand; der Vergleich zeigt, welche Erwartung näher an der Messung lag.")}</p>
      ${snow
        ? html`<p class="small warn-text">${snow.bright !== null
            ? t("Schnee auf allen Flächen? Bei {temp} lieferten alle Flächen höchstens {pct} des Gelernten, obwohl es hell war.", {
                temp: formatValue(snow.min_temp, "°C"), pct: pct(Math.max(...Object.values(snow.levels))) })
            : t("Schnee auf allen Flächen? Bei {temp} lieferten alle Flächen höchstens {pct} des Gelernten (Helligkeit nicht gemessen).", {
                temp: formatValue(snow.min_temp, "°C"), pct: pct(Math.max(...Object.values(snow.levels))) })}</p>`
        : nothing}
      ${keys.length
        ? html`<div class="table-wrap">
            <table>
              <thead><tr><th>${t("Fläche")}</th><th>${t("Verschmutzt je Monat")}</th><th>${t("Laub zurück nach")}</th>
                <th>${t("Nächste Tage")}</th><th>${t("Vergleich")}</th></tr></thead>
              <tbody>
                ${keys.map((key) => {
                  const s = season[key];
                  const soiled = Object.entries(s.months).filter(([, m]) => m.soiled_days > 0);
                  return html`<tr>
                    <td>${names[key] ?? key}</td>
                    <td>${soiled.length
                      ? soiled.map(([m, v]) => `${month(Number(m))} ${pct(v.soiled_days / v.days)}${v.depth !== null ? ` (${pct(v.depth)})` : ""}`).join(" · ")
                      : t("nie")}</td>
                    <td>${s.regrowth_days !== null ? t("{days} Tagen", { days: s.regrowth_days }) : "–"}</td>
                    <td>${s.next_days.map((d) => `${weekday(d.day)} ${pct(d.expected)}`).join(" · ")}</td>
                    <td>${s.check
                      ? t("{days} Tage: Jahreszeit ±{season}, aktueller Stand ±{current}", {
                          days: s.check.days, season: pct(s.check.season), current: pct(s.check.current) })
                      : t("noch zu wenige Tage")}</td>
                  </tr>`;
                })}
              </tbody>
            </table>
          </div>
          <p class="small muted help">${t("Verschmutzt je Monat: Anteil der Tage, in Klammern der Ertrag in der Zeit. Nächste Tage: erwarteter Anteil des sauberen Ertrags.")}</p>`
        : nothing}
    `;
  }

  /** Soiled periods found in the whole history, the latest first. */
  _renderEpisodes(data) {
    const found = data.episodes ?? [];
    if (!data.planes.some((p) => p.correction)) return nothing;
    const names = Object.fromEntries(data.planes.map((p) => [p.key, p.name]));
    const kinds = {
      drop: t("plötzlich (Schnee?)"),
      gradual: t("nach und nach (Laub?)"),
      before_cleaning: t("bis zur Reinigung"),
      all_planes: t("alle Flächen bei Frost (Schnee)"),
    };
    const day = (iso) => new Date(iso).toLocaleDateString(locale());
    // The end is exclusive: the last soiled day is the one before
    const until = (iso) => day(new Date(new Date(iso).getTime() - 86_400_000).toISOString().slice(0, 10));
    return html`
      <h3>${t("Verschmutzung in der Historie")}</h3>
      <p class="small muted help">${t("Zeiträume, in denen eine Fläche gegenüber den anderen deutlich weniger lieferte, oder alle Flächen bei Frost fast nichts. Nur zur Auswertung: Daraus soll Sunflow später lernen, wann Laub oder Schnee zu erwarten ist.")}</p>
      ${found.length
        ? html`<div class="table-wrap">
            <table>
              <thead><tr><th>${t("Fläche")}</th><th>${t("Art")}</th><th>${t("Zeitraum")}</th><th>${t("Ertrag")}</th><th>${t("Tiefste Temperatur")}</th></tr></thead>
              <tbody>
                ${found.map((e) => html`<tr>
                  <td>${e.plane ? names[e.plane] ?? e.plane : t("alle")}</td>
                  <td>${kinds[e.kind] ?? e.kind}</td>
                  <td>${e.to ? `${day(e.from)} – ${until(e.to)}` : t("seit {date}", { date: day(e.from) })} · ${t("{days} Tage", { days: e.days })}</td>
                  <td>${e.depth !== null ? `${Math.round(e.depth * 100)} %` : "–"}</td>
                  <td>${e.min_temp !== null ? formatValue(e.min_temp, "°C") : "–"}${e.frost_days ? ` · ${t("{days} Frosttage", { days: e.frost_days })}` : ""}</td>
                </tr>`)}
              </tbody>
            </table>
          </div>`
        : html`<p class="small muted">${t("Keine gefunden.")}</p>`}
    `;
  }

  _renderPvSunPlane(plane) {
    const c = plane.correction;
    const b = plane.backtest;
    const pct = (v) => `${Math.round(v * 100)} %`;
    const wh = (v) => formatValue(v, "Wh");
    const best = b ? Math.min(b.by_sun, b.by_spread ?? b.by_sun) : null;
    const better = b && best < Math.min(b.raw, b.by_hour);
    const weights = c.spread_weights ?? {};
    return html`
      <div class="tiles">
        <div class="tile"><div class="small muted">${t("Klar")}</div><div class="value">${pct(c.clear)}</div>
          <div class="small muted">${t("{days} Tage seit {date}", { days: c.days, date: new Date(plane.first).toLocaleDateString(locale()) })}</div></div>
        <div class="tile"><div class="small muted">${t("Bewölkt")}</div><div class="value">${pct(c.cloudy)}</div></div>
        ${b
          ? html`<div class="tile ${better ? "now" : ""}"><div class="small muted">${t("Abweichung je Stunde (Backtest)")}</div>
              <div class="value">${wh(best)}</div>
              <div class="small muted">${t("Rohprognose {raw} · nach Uhrzeit {hour}", { raw: wh(b.raw), hour: wh(b.by_hour) })}</div>
              ${b.by_spread !== undefined
                ? html`<div class="small muted">${t("nach Sonnenstand {sun} · mit Unsicherheit {spread}", { sun: wh(b.by_sun), spread: wh(b.by_spread) })}</div>`
                : nothing}</div>`
          : nothing}
      </div>
      ${weights["0"] !== undefined
        ? html`<p class="small muted help">${t("Ist Solcast sehr unsicher (p10 unter 30 % des Mittelwerts), kommt erfahrungsgemäß p10 plus {pct} % des Abstands zum Mittelwert an; die Planung rechnet so.", { pct: Math.round(weights["0"] * 100) })}</p>`
        : nothing}
      ${this._shadeMap(c)}
    `;
  }

  /** Factor per sun cell (clear hours): orange where less arrives than forecast, blue where more. */
  _shadeMap(c) {
    if (!c.cells.length) return nothing;
    const cellW = 14;
    const cellH = 14;
    const azimuths = c.cells.map((x) => x.azimuth);
    const minA = Math.min(...azimuths);
    const maxA = Math.max(...azimuths) + c.azimuth_step;
    const maxE = Math.max(...c.cells.map((x) => x.elevation)) + c.elevation_step;
    const left = 30;
    const width = left + ((maxA - minA) / c.azimuth_step) * cellW;
    const height = (maxE / c.elevation_step) * cellH + 18;
    const x = (a) => left + ((a - minA) / c.azimuth_step) * cellW;
    const y = (e) => (maxE - e - c.elevation_step) / c.elevation_step * cellH;
    const color = (f) => {
      const share = Math.round(Math.min(Math.abs(1 - f) / 0.6, 1) * 100);
      return `color-mix(in oklab, ${f < 1 ? "var(--sf-shade-low)" : "var(--sf-shade-high)"} ${share}%, var(--sf-subtle))`;
    };
    const directions = [[90, t("O")], [135, t("SO")], [180, t("S")], [225, t("SW")], [270, t("W")]].filter(([a]) => a >= minA && a <= maxA);
    return html`
      <div class="shade-map">
        <svg viewBox="0 0 ${width} ${height}" width=${width} height=${height} role="img"
          aria-label=${t("Verschattungskarte: Anteil der Prognose je Sonnenhöhe und -richtung bei klarem Himmel")}>
          ${c.cells.map((cell) => svg`<rect x=${x(cell.azimuth) + 1} y=${y(cell.elevation) + 1} width=${cellW - 2} height=${cellH - 2} rx="2"
            fill=${color(cell.factor)}><title>${t("{elevation}° hoch, {azimuth}°: {factor} der Prognose", {
              elevation: cell.elevation, azimuth: cell.azimuth, factor: `${Math.round(cell.factor * 100)} %`,
            })}</title></rect>`)}
          ${[0, 20, 40, 60].filter((e) => e < maxE).map((e) => svg`<text class="axis" x=${left - 4} y=${y(e) + cellH - 2} text-anchor="end">${e}°</text>`)}
          ${directions.map(([a, label]) => svg`<text class="axis" x=${x(a)} y=${height - 4} text-anchor="middle">${label}</text>`)}
        </svg>
        <div class="legend small">
          <span class="legend-item"><span class="swatch" style="background:${color(0.4)}"></span>${t("weniger als prognostiziert (Schatten)")}</span>
          <span class="legend-item"><span class="swatch" style="background:${color(1)}"></span>${t("wie prognostiziert")}</span>
          <span class="legend-item"><span class="swatch" style="background:${color(1.4)}"></span>${t("mehr")}</span>
        </div>
        <p class="small muted help">${t("Nach rechts die Sonnenrichtung, nach oben die Sonnenhöhe; nur klare Stunden.")}</p>
      </div>
    `;
  }

  async _loadInventory(refresh = false) {
    try {
      this._inventory = await api.dataInventory(refresh);
      this._inventoryError = null;
    } catch (err) {
      this._inventoryError = t("Datenbestand nicht verfügbar: {error}", { error: err.message });
    }
  }

  _renderInventory() {
    const inv = this._inventory;
    if (!inv && !this._inventoryError) return nothing;
    const days = (d) => (d === null || d === undefined ? "–" : t("{days} Tage", { days: d }));
    const since = (iso) => (iso ? new Date(iso).toLocaleDateString(locale(), { month: "short", year: "numeric" }) : "–");
    const statistics = inv?.entities.map((e) => e.statistics_since).filter(Boolean).sort()[0];
    const weather = inv?.weather;
    const solcast = inv?.forecasts.filter((f) => f.source === "solcast") ?? [];
    const modules = inv?.entities.filter((e) => e.role === "optimizer_power") ?? [];
    const moduleDays = modules.map((e) => e.states_days).filter((d) => d !== null).sort((a, b) => a - b);
    return html`
      <section>
        <div class="section-head">
          <h2>${t("Datenbestand fürs Lernen")}</h2>
          <button class="secondary" @click=${() => this._loadInventory(true)}>${t("Neu prüfen")}</button>
        </div>
        <p class="small muted help">${t("Wie weit Home Assistant zurück Messwerte, Wetter und Prognosen aufhebt. Daraus kann Sunflow rückwirkend lernen, statt erst Wochen zu sammeln.")}</p>
        ${this._inventoryError ? html`<div class="alert error" role="alert">${this._inventoryError}</div>` : nothing}
        ${inv
          ? html`
              <div class="tiles">
                <div class="tile now"><div class="small muted">${t("Verlauf in voller Auflösung")}</div><div class="value">${days(inv.states_days)}</div>
                  <div class="small muted">${t("Aufbewahrung des Recorders")}</div></div>
                <div class="tile"><div class="small muted">${t("Langzeitstatistik (Stundenwerte)")}</div><div class="value">${since(statistics)}</div>
                  <div class="small muted">${t("ältester Monat")}</div></div>
                ${weather
                  ? html`<div class="tile"><div class="small muted">${t("Wetter")}</div><div class="value">${days(weather.states_days)}</div>
                      <div class="small muted">${weather.attributes.includes("cloud_coverage")
                        ? t("mit Bewölkung")
                        : t("ohne Bewölkung aufgezeichnet")}</div></div>`
                  : nothing}
                ${modules.length
                  ? html`<div class="tile"><div class="small muted">${t("Optimierer je Modul")}</div>
                      <div class="value">${moduleDays.length ? days(moduleDays[Math.floor(moduleDays.length / 2)]) : "–"}</div>
                      <div class="small muted">${t("{count} Module · Statistik seit {date}", {
                        count: modules.length,
                        date: since(modules.map((e) => e.statistics_since).filter(Boolean).sort()[0]),
                      })}</div></div>`
                  : nothing}
                ${solcast.map((f) => html`<div class="tile"><div class="small muted">${t("Solcast-Prognose {name}", { name: f.name })}</div>
                  <div class="value">${f.error ? "–" : days(f.days)}</div>
                  <div class="small muted" title=${f.error ?? ""}>${f.error ? t("Abfrage fehlgeschlagen") : t("vergangene Prognosen")}</div></div>`)}
              </div>
              <details>
                <summary class="small">${t("Je Messwert ({count})", { count: inv.entities.length })}</summary>
                <div class="table-wrap">
                  <table>
                    <thead><tr><th>${t("Gerät")}</th><th>${t("Messwert")}</th><th>${t("Verlauf")}</th>
                      <th title=${t("Aufgezeichnete Änderungen in der letzten Stunde")}>${t("Änderungen/h")}</th><th>${t("Statistik seit")}</th></tr></thead>
                    <tbody>
                      ${inv.entities.map((e) => html`<tr>
                        <td>${e.device}</td>
                        <td><code title=${e.entity_id}>${e.role}</code></td>
                        <td>${days(e.states_days)}</td>
                        <td>${Math.round(e.changes_per_hour)}</td>
                        <td>${since(e.statistics_since)}</td>
                      </tr>`)}
                    </tbody>
                  </table>
                </div>
              </details>`
          : nothing}
      </section>
    `;
  }

  /** Leaves or cleaning found against the other planes. */
  _renderPlaneState(state, pct) {
    const last = state?.soiled.at(-1);
    if (!last) return nothing;
    const day = (iso) => new Date(iso).toLocaleDateString(locale());
    return html`<span class="muted">· ${last.to === null
      ? t("liefert seit {date} nur {value} des sauberen Zustands (Schnee, Laub o. Ä., gemessen an den anderen Flächen): plant damit und lernt diese Tage nicht mit", {
          date: day(last.from),
          value: pct(state.level),
        })
      : t("seit {date} wieder sauber (gemessen an den anderen Flächen): die Tage davor mit Schnee, Laub o. Ä. lernt Sunflow nicht mit", { date: day(last.to) })}</span>`;
  }

  _renderAppliances() {
    const list = this._appliances ?? [];
    if (!list.length) return nothing;
    const kwh = (wh) => formatValue(wh, "Wh");
    const hour = (h) => (h === null || h === undefined ? null : t("{hour} Uhr", { hour: h }));
    return html`
      <h3>${t("Gemessene Geräte")}</h3>
      <ul class="small plane-list">
        ${list.map((d) => {
          const u = d.usage;
          if (!u) return html`<li><strong>${d.name}</strong>: <span class="muted">${t("noch zu wenig Stundenwerte (mindestens eine Woche)")}</span></li>`;
          const usual = [
            hour(u.usual_workday_hour) ? t("werktags meist um {hour}", { hour: hour(u.usual_workday_hour) }) : null,
            hour(u.usual_weekend_hour) ? t("am Wochenende meist um {hour}", { hour: hour(u.usual_weekend_hour) }) : null,
          ].filter(Boolean);
          return html`<li><strong>${d.name}</strong>: ${t("Ø {energy} am Tag, läuft an {pct} % der Tage", {
            energy: kwh(u.daily_wh), pct: Math.round(u.active_share * 100),
          })}${usual.length ? ` · ${usual.join(" · ")}` : ""} <span class="muted">(${t("{days} Tage", { days: u.days })})</span></li>`;
        })}
      </ul>
      <p class="small muted help">${t("Aus den Stundenwerten der letzten 8 Wochen. Sunflow schaltet diese Geräte nicht; die Planung nutzt das noch nicht.")}</p>
    `;
  }

  async _restartPlane(plane) {
    if (!(await confirmDialog({
      title: t("„{name}“ ab heute neu lernen?", { name: plane.name }),
      text: t("Die bisherigen Messwerte dieser Fläche werden nicht mehr verwendet. Bis genug neue Tage da sind, plant Sunflow sie mit der Korrektur der ganzen Anlage."),
      confirm: t("Neu lernen"),
    }))) return;
    try {
      await api.restartPlaneLearning(plane.key);
      this._pvLearning = await api.pvLearning();
    } catch (err) {
      this._error = t("Neu lernen fehlgeschlagen: {error}", { error: err.message });
    }
  }

  _renderEfficiency() {
    const series = (this._efficiency ?? []).filter((s) => s.points.some((p) => p.value !== null));
    if (!series.length) return nothing;
    const pct = (v) => `${decimal((v * 100).toFixed(1))} %`;
    const day = (iso) => {
      const [y, m, d] = iso.split("-").map(Number);
      return new Date(y, m - 1, d);
    };
    const lines = series.map((s, i) => ({
      name: s.name,
      color: SERIES_COLORS[i % SERIES_COLORS.length],
      // Days with little energy are merged into one value spanning them
      points: s.points.map((p) => ({ start: day(p.date), end: new Date(day(p.date).getTime() + (p.days ?? 1) * 86_400_000), value: p.value })),
    }));
    const values = series.flatMap((s) => s.points.filter((p) => p.value !== null).map((p) => p.value));
    const lowest = Math.min(...values);
    // Energy-weighted mean: days with more throughput count more
    const mean = (s) => {
      const valid = s.points.filter((p) => p.value !== null && p.energy_wh);
      const energy = valid.reduce((sum, p) => sum + p.energy_wh, 0);
      return energy ? valid.reduce((sum, p) => sum + p.value * p.energy_wh, 0) / energy : null;
    };
    return html`
      <section>
        <div class="section-head">
          <h2>${t("Wirkungsgrade")}</h2>
          <div class="range" role="group" aria-label=${t("Zeitraum Wirkungsgrade")}>
            ${[[30, t("30 Tage")], [90, t("90 Tage")], [365, t("1 Jahr")]].map(
              ([value, label]) => html`<button class=${this._efficiencyDays === value ? "active" : ""}
                aria-pressed=${this._efficiencyDays === value} @click=${() => this._setEfficiencyDays(value)}>${label}</button>`,
            )}
          </div>
        </div>
        <p class="small muted help">${t("Pro Tag aus den Energiezählern.")}
          ${t("Speicher: Zyklus einschließlich Eigenverbrauch, um die Änderung des Ladestands bereinigt.")}
          ${t("Wechselrichter: Wechselstrom-Ertrag geteilt durch Gleichstrom-Eingang.")}
          ${t("Tage mit wenig Energie (Speicher unter 20 % der Kapazität geladen, Wechselrichter unter 0,5 kWh) werden mit den folgenden zu einem Wert zusammengefasst; Tage mit Lücken in den Daten bleiben leer.")}</p>
        <div class="legend">
          ${series.map((s, i) => {
            const average = mean(s);
            return html`<span class="legend-item"><span class="swatch" style="background:${lines[i].color}"></span>
              ${s.name} (${s.kind === "battery" ? t("Speicher") : t("Wechselrichter")})${average !== null ? ` · Ø ${pct(average)}` : ""}</span>`;
          })}
        </div>
        ${lineChart(lines, {
          label: t("Wirkungsgrade pro Tag"),
          formatY: (v) => `${Math.round(v * 100)} %`,
          yMin: Math.max(Math.floor(lowest * 10) / 10 - 0.1, 0),
          // Noise can push single days above 100 %; the axis grows instead of cutting them off
          yMax: Math.max(1, ...values),
        })}
      </section>
    `;
  }

  _renderShadow() {
    const shadow = this._shadow;
    if (!shadow) return nothing;
    const e = shadow.evaluation;
    const since = shadow.status.since ? dayTime.format(new Date(shadow.status.since)) : "–";
    const rows = shadow.rows.filter((r) => r.actual_action !== null && r.planned_action !== null);
    const pct = (v) => (v === null ? "–" : `${Math.round(v * 100)} %`);
    // How far reality was off the forecast: 98 Wh actual vs 162 Wh forecast is "−40 % ggü. Prognose"
    const deviation = (forecast, actual) => {
      if (forecast < 0.01) return "–";
      const pct = Math.round(((actual - forecast) / forecast) * 100);
      return t("{value} % ggü. Prognose", { value: `${pct > 0 ? "+" : pct < 0 ? "−" : "±"}${Math.abs(pct)}` });
    };
    const kwh = (v) => formatValue(v * 1000, "Wh");
    return html`
      <section>
        <h2>${t("Schattenbetrieb")}</h2>
        <p class="small muted">
          ${t("Seit {since} protokolliert Sunflow alle 15 Minuten, was es tun würde und was tatsächlich passiert ist – {compared} vollständige Zeitabschnitte (letzte 48 h).", { since, compared: e.compared })}
          ${shadow.status.last_error ? html`<br><span class="warn-text">${t("Letzter Fehler: {error}", { error: shadow.status.last_error })}</span>` : nothing}
        </p>
        <div class="actions">
          <a class="secondary button-link" href="api/v1/export" download>${t("Daten exportieren")}</a>
          <button class="secondary button-link" ?disabled=${this._importing}
            @click=${(e) => e.currentTarget.nextElementSibling.click()}>
            ${this._importing ? t("Importiere …") : t("Export importieren")}
          </button>
          <input type="file" accept=".zip" hidden @change=${(e) => this._import(e)}>
          ${this._importResult ? html`<span class="small muted">${this._importResult}</span>` : nothing}
        </div>
        ${e.compared === 0
          ? html`<p class="muted">${t("Noch keine abgeschlossenen Zeitabschnitte – der erste erscheint nach dem nächsten Viertelstundenwechsel.")}</p>`
          : html`
              <div class="tiles">
                <div class="tile">
                  <div class="small muted">${t("PV-Prognose")}</div>
                  <div class="value">${kwh(e.pv_forecast_kwh)}</div>
                  <div class="small muted">${t("tatsächlich {actual} ({deviation})", { actual: kwh(e.pv_actual_kwh), deviation: deviation(e.pv_forecast_kwh, e.pv_actual_kwh) })}</div>
                </div>
                <div class="tile">
                  <div class="small muted">${t("Lastprognose (ohne steuerbare Verbraucher)")}</div>
                  <div class="value">${kwh(e.load_forecast_kwh)}</div>
                  <div class="small muted">${t("tatsächlich {actual} ({deviation})", { actual: kwh(e.load_actual_kwh), deviation: deviation(e.load_forecast_kwh, e.load_actual_kwh) })}</div>
                </div>
                <div class="tile">
                  <div class="small muted">${t("Ladestand-Abweichung")}</div>
                  <div class="value">${e.soc_mae_wh === null ? "–" : formatValue(e.soc_mae_wh, "Wh")}</div>
                  <div class="small muted">${t("mittlerer Abstand des Plans von vor {hours} h zu Ist", { hours: e.soc_ahead_hours })}</div>
                </div>
                <div class="tile">
                  <div class="small muted">${t("Gleiche Entscheidung")}</div>
                  <div class="value">${pct(e.agreement)}</div>
                  <div class="small muted">${t("{count} Abschnitte hätte Sunflow anders gesteuert", { count: e.differing_slots })}</div>
                </div>
              </div>
              <div class="legend">
                <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>${t("Ladestand tatsächlich")}</span>
                <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[1]}"></span>${t("Ladestand laut Plan von vor {hours} h", { hours: e.soc_ahead_hours })}</span>
              </div>
              ${lineChart(
                [
                  { name: t("Ist"), color: SERIES_COLORS[0], points: rows.map((r) => this._point(r, "actual_soc_wh")) },
                  { name: t("Plan"), color: SERIES_COLORS[1], points: rows.map((r) => this._point(r, "ahead_soc_wh")) },
                ],
                { label: t("Ladestand Plan gegen Ist"), formatY: (v) => formatValue(v, "Wh") },
              )}
              ${this._renderBatteries(rows)}
              ${this._renderDifferences(rows)}
            `}
      </section>
    `;
  }


  async _import(event) {
    const file = event.target.files[0];
    if (!file) return;
    // Moving to another App (e.g. Sunflow Edge) takes the configuration along; cancelling keeps it
    const withConfig = await confirmDialog({
      title: t("Konfiguration übernehmen?"),
      text: t("Die Aufzeichnung wird immer zusammengeführt. Beim Umzug in eine andere Sunflow-App kann auch die Konfiguration übernommen werden: Sie ersetzt die jetzige, die Steuerung bleibt danach aus, bis du sie einschaltest."),
      confirm: t("Mit Konfiguration"),
      cancel: t("Nur Aufzeichnung"),
    });
    const body = new FormData();
    body.append("file", file);
    const url = `api/v1/import?config=${withConfig}`;
    this._importing = true;
    try {
      let response = await fetch(url, { method: "POST", body });
      if (response.status === 401 && (await renewIngressSession())) {
        response = await fetch(url, { method: "POST", body });
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? response.statusText);
      this._importResult = data.config
        ? t("{count} Zeitabschnitte und die Konfiguration importiert. Die Seite lädt neu.", { count: data.imported_slots })
        : t("{count} Zeitabschnitte importiert.", { count: data.imported_slots });
      if (data.config) setTimeout(() => window.location.reload(), 1500);
      this._shadow = await api.shadow();
    } catch (err) {
      this._error = t("Import fehlgeschlagen: {error}", { error: err.message });
    } finally {
      this._importing = false;
    }
    event.target.value = "";
  }


  /** Plan against actual for each battery, when several are planned together. */
  _renderBatteries(rows) {
    const ids = [...new Set(rows.flatMap((r) => Object.keys(r.planned_batteries ?? {})))];
    // The plan from hours before: the latest one starts from the measured SoC
    const planned = (row, id) => row.ahead_batteries?.[id] ?? null;
    if (ids.length < 2) return nothing;
    const name = (id) => this._batteryNames?.[id] ?? id;
    const soc = (row, column, id) => row[column]?.[id]?.soc_wh ?? null;
    const point = (row, value) => {
      const start = new Date(row.start);
      return { start: start.toISOString(), end: new Date(start.getTime() + 15 * 60_000).toISOString(), value };
    };
    const deviation = (id) => {
      const pairs = rows.map((r) => [planned(r, id), soc(r, "actual_batteries", id)]).filter(([a, b]) => a !== null && b !== null);
      return pairs.length ? pairs.reduce((sum, [a, b]) => sum + Math.abs(a - b), 0) / pairs.length : null;
    };
    return html`
      <h3>${t("Je Speicher")}</h3>
      <div class="legend">
        ${ids.map((id, i) => html`<span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[i] ?? UNASSIGNED_COLOR}"></span>${name(id)}: ${t("Abweichung {value}", { value: deviation(id) === null ? "–" : formatValue(deviation(id), "Wh") })}</span>`)}
        <span class="legend-item muted">${t("durchgezogen Ist, gestrichelt Plan")}</span>
      </div>
      ${lineChart(
        ids.flatMap((id, i) => [
          { name: t("{name} Ist", { name: name(id) }), color: SERIES_COLORS[i] ?? UNASSIGNED_COLOR, points: rows.map((r) => point(r, soc(r, "actual_batteries", id))) },
          { name: t("{name} Plan", { name: name(id) }), color: SERIES_COLORS[i] ?? UNASSIGNED_COLOR, dashed: true, points: rows.map((r) => point(r, planned(r, id))) },
        ]),
        { label: t("Ladestand je Speicher, Plan gegen Ist"), formatY: (v) => formatValue(v, "Wh") },
      )}
    `;
  }

  _point(row, key) {
    const start = new Date(row.start);
    return { start: start.toISOString(), end: new Date(start.getTime() + 15 * 60_000).toISOString(), value: row[key] ?? 0 };
  }


  _renderDifferences(rows) {
    const differing = rows.filter((r) => r.planned_action !== r.actual_action).slice(-12).reverse();
    if (!differing.length) return html`<p class="small muted">${t("Bisher hätte Sunflow genauso gehandelt wie der Wechselrichter.")}</p>`;
    return html`
      <div class="table-wrap">
        <table>
          <thead><tr><th>${t("Zeit")}</th><th>${t("Sunflow hätte")}</th><th>${t("Tatsächlich")}</th><th>${t("PV / Last (Ist)")}</th></tr></thead>
          <tbody>
            ${differing.map(
              (r) => html`<tr>
                <td>${dayTime.format(new Date(r.start))}</td>
                <td>${ACTIONS[r.planned_action]?.label ?? r.planned_action}${r.planned_power_w ? ` · ${formatValue(r.planned_power_w, "W")}` : ""}</td>
                <td>${ACTIONS[r.actual_action]?.label ?? r.actual_action}</td>
                <td>${formatValue(r.actual_pv_w, "W")} / ${formatValue(r.actual_load_w, "W")}</td>
              </tr>`,
            )}
          </tbody>
        </table>
      </div>
    `;
  }

  static styles = [toggleSheet, css`
    :host { display: flex; flex-direction: column; gap: 16px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0 0 8px; font-size: 16px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .heading { display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; align-items: flex-start; }
    .alert { padding: 10px 14px; border-radius: 8px; font-size: 14px; }
    .alert.warn { background: var(--sf-warn-bg); color: var(--sf-warn-text); }
    .alert.error { background: var(--sf-error-bg); color: var(--sf-error-text); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 12px 14px; }
    .tile.now { background: var(--sf-primary-bg); }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .seg {
      display: grid; grid-template-columns: minmax(150px, auto) 1fr auto; gap: 4px 12px; align-items: baseline;
      padding: 8px 12px; border-radius: 8px; background: var(--sf-subtle); border-left: 4px solid var(--sf-border);
    }
    .seg.hold { border-left-color: var(--sf-muted); }
    .seg.auto { border-left-color: var(--sf-ok); }
    .seg.force_charge { border-left-color: var(--sf-primary); }
    .seg.force_discharge { border-left-color: var(--sf-warn-text); }
    .seg.limit_discharge, .seg.limit_charge { border-left-color: var(--sf-neutral-text); }
    .seg .when { font-variant-numeric: tabular-nums; font-size: 14px; }
    .seg .what { font-weight: 600; }
    @media (max-width: 560px) { .seg { grid-template-columns: 1fr; } }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .recommendations { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr)); gap: 10px; }
    .recommendation {
      display: flex; flex-direction: column; gap: 6px; padding: 12px 14px; border-radius: 10px;
      background: var(--sf-subtle); border-left: 4px solid var(--sf-neutral-text);
    }
    .recommendation.warn { border-left-color: var(--sf-warn-text); }
    .recommendation p { margin: 0; }
    .recommendation .actions { display: flex; gap: 8px; margin-top: 4px; }
    .legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; margin-bottom: 6px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; }
    .warn-text { color: var(--sf-error-text); }
    .flex-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
    .flex-card { background: var(--sf-subtle); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
    .flex-head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
    .windows { margin: 4px 0 0; padding-left: 18px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    .tag {
      font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; margin-left: 6px;
      background: var(--sf-neutral-bg); color: var(--sf-neutral-text); vertical-align: middle;
    }
    .section-head { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: center; }
    .range { display: flex; gap: 4px; background: var(--sf-subtle); padding: 4px; border-radius: 10px; }
    .range button {
      min-height: 36px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent; cursor: pointer;
      font-size: 13px; color: var(--sf-muted);
    }
    .range button.active { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; }
    .line { display: inline-block; width: 22px; height: 0; border-top: 2px solid var(--sf-text); }
    .line.dashed { border-top-style: dashed; }
    .chart .now { stroke: var(--sf-text); stroke-width: 1; stroke-dasharray: 2 3; }
    :host { --sf-shade-low: #c2410c; --sf-shade-high: #0b5cad; }
    .shade-map { overflow-x: auto; margin-top: 10px; }
    .shade-map .swatch { border: 1px solid var(--sf-border-strong); }
    .shade-map svg { display: block; max-width: none; }
    .shade-map .axis { fill: var(--sf-muted); font-size: 10px; }
    details summary { cursor: pointer; margin-top: 12px; min-height: 32px; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--sf-border); border-radius: 8px; margin-top: 10px; }
    table { border-collapse: collapse; width: 100%; min-width: 520px; font-size: 13px; }
    th { text-align: left; font-weight: 600; background: var(--sf-subtle); padding: 8px 10px; }
    td { padding: 6px 10px; border-top: 1px solid var(--sf-border); }
    .settings { border-top: 1px solid var(--sf-border); padding-top: 16px; display: flex; flex-direction: column; gap: 16px; }
    .strategy { border: 0; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; font-size: 14px; }
    .strategy legend { margin-bottom: 8px; padding: 0; }
    .option {
      display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--sf-border);
      border-radius: 8px; cursor: pointer;
    }
    .option.selected { border-color: var(--sf-primary); background: var(--sf-primary-bg); }
    .option input { margin-top: 3px; width: 18px; height: 18px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .inline { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; font-size: 14px; }
    .inline input { width: 18px; height: 18px; }
    .actions { display: flex; gap: 10px; margin-top: 12px; }
    .secondary, .ok { min-height: 44px; padding: 0 18px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    button.link {
      border: 0; background: none; padding: 0 0 0 6px; font: inherit; color: var(--sf-primary); cursor: pointer;
      text-decoration: underline;
    }
    .button-link {
      display: inline-flex; align-items: center; min-height: 44px; padding: 0 18px; border-radius: 8px;
      text-decoration: none; color: var(--sf-text); cursor: pointer; font-size: 14px;
    }
    .ok { border: 0; background: var(--sf-ok); color: var(--sf-on-ok); }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `];
}

customElements.define("sunflow-analysis", SunflowAnalysis);
