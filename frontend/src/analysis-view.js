import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api, renewIngressSession } from "./api.js";
import { poll } from "./poll.js";
import { lineChart, SERIES_COLORS } from "./charts.js";
import { formatValue } from "./format.js";

const ACTIONS = {
  auto: { label: "Eigenverbrauch", hint: "Speicher lädt aus PV-Überschuss und deckt den Bedarf" },
  hold: { label: "Speicher halten", hint: "Speicher wird geschont, Bedarf kommt aus dem Netz" },
  force_charge: { label: "Laden aus dem Netz", hint: "Speicher wird gezielt geladen" },
  force_discharge: { label: "Entladen ins Netz", hint: "Speicher speist gezielt ein" },
  limit_discharge: { label: "Entladen begrenzt", hint: "Speicher deckt den Bedarf nur teilweise, der Rest kommt aus dem Netz" },
  limit_charge: { label: "Laden begrenzt", hint: "Speicher nimmt nur einen Teil des PV-Überschusses auf, der Rest wird eingespeist" },
};
const dayTime = new Intl.DateTimeFormat("de-DE", { weekday: "short", hour: "2-digit", minute: "2-digit" });
const REFRESH_MS = 5 * 60 * 1000;

/** Shadow mode evaluation: forecasts and decisions against reality, export/import. */
class SunflowAnalysis extends LitElement {
  static properties = {
    _shadow: { state: true },
    _error: { state: true },
    _importResult: { state: true },
    _energy: { state: true },
    _period: { state: true },
    _energyError: { state: true },
    _efficiency: { state: true },
    _efficiencyDays: { state: true },
    _pvLearning: { state: true },
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
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stopPolling = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling();
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
      if (period === this._period) this._energyError = `Energiebilanz nicht verfügbar: ${err.message}`;
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
      this._error = `Empfehlung konnte nicht übernommen werden: ${err.message}`;
    } finally {
      this._applying = null;
    }
  }

  async _dismissRecommendation(rec) {
    try {
      await api.dismissRecommendation(rec.id, rec.fingerprint);
      await this._loadRecommendations();
    } catch (err) {
      this._error = `Ausblenden fehlgeschlagen: ${err.message}`;
    }
  }

  _renderRecommendations() {
    const list = this._recommendations;
    if (!list?.length) return nothing;
    const actionLabel = (a) => (a.type === "navigate" ? "Öffnen" : "Übernehmen");
    return html`
      <section>
        <h2>Empfehlungen</h2>
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
                <button class="secondary" @click=${() => this._dismissRecommendation(rec)}>Ausblenden</button>
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
    const euro = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
    const sum = (key) => days.reduce((total, d) => total + (d[key] ?? 0), 0);
    const hasWithout = days.some((d) => d.without_pv_cost !== null);
    const point = (d, key) => {
      const [y, m, day] = d.date.split("-").map(Number);
      const start = new Date(y, m - 1, day);
      return { start, end: new Date(start.getTime() + 86_400_000), value: d[key] };
    };
    const lines = [
      { name: "Nettokosten", color: SERIES_COLORS[0], points: days.map((d) => point(d, "net_cost")) },
      ...(hasWithout
        ? [{ name: "ohne PV und Speicher", color: SERIES_COLORS[1], dashed: true, points: days.map((d) => point(d, "without_pv_cost")) }]
        : []),
    ];
    const lowest = Math.min(0, ...days.map((d) => d.net_cost));
    const incomplete = days.filter((d) => !d.complete).length;
    return html`
      <section>
        <div class="section-head">
          <h2>Kosten pro Tag</h2>
          <div class="range" role="group" aria-label="Zeitraum Kosten">
            ${[[30, "30 Tage"], [90, "90 Tage"]].map(
              ([value, label]) => html`<button class=${(this._costDays ?? 30) === value ? "active" : ""}
                aria-pressed=${(this._costDays ?? 30) === value}
                @click=${() => { this._costDays = value; this._loadCosts(); }}>${label}</button>`,
            )}
          </div>
        </div>
        <div class="tiles">
          <div class="tile now"><div class="small muted">Nettokosten</div><div class="value">${euro.format(sum("net_cost"))}</div>
            <div class="small muted">Bezug ${euro.format(sum("import_cost"))} − Einspeisung ${euro.format(sum("export_revenue"))}</div></div>
          ${hasWithout
            ? html`<div class="tile"><div class="small muted">Ohne PV und Speicher</div><div class="value">${euro.format(sum("without_pv_cost"))}</div></div>
                <div class="tile"><div class="small muted">Ersparnis durch PV und Speicher</div><div class="value">${euro.format(sum("savings"))}</div></div>`
            : nothing}
        </div>
        <div class="legend">
          ${lines.map((l) => html`<span class="legend-item"><span class="swatch" style="background:${l.color}"></span>${l.name === "Nettokosten"
            ? "Nettokosten (Bezug − Einspeisung)"
            : "ohne PV und Speicher (alles aus dem Netz)"}</span>`)}
        </div>
        ${lineChart(lines, { label: "Stromkosten pro Tag", formatY: (v) => euro.format(v), yMin: lowest })}
        <p class="small muted">Aus Energiezählern und dem Preisverlauf der Tarif-Entität; Einspeisung zur eingestellten Vergütung.
          ${incomplete ? `${incomplete} Tag(e) mit Lücken in der Statistik.` : ""}</p>
      </section>
    `;
  }

  async _load() {
    this._loadCosts();
    this._loadRecommendations();
    api.pvLearning().then((data) => (this._pvLearning = data)).catch(() => (this._pvLearning = null));
    this._loadEnergy();
    this._loadEfficiency();
    try {
      this._shadow = await api.shadow();
      this._error = null;
    } catch (err) {
      this._error = `Analyse konnte nicht geladen werden: ${err.message}`;
    }
  }

  render() {
    return html`
      <div class="heading">
        <div>
          <h1>Analyse</h1>
          <p class="muted">Wie gut stimmen Prognosen und Entscheidungen mit der Wirklichkeit überein?</p>
        </div>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${this._renderRecommendations()}
      ${this._renderEnergy()}
      ${this._renderCosts()}
      ${this._renderEfficiency()}
      ${this._renderPvLearning()}
      ${this._shadow === null && !this._error ? html`<p class="muted">Lade …</p>` : nothing}
      ${this._renderShadow()}
    `;
  }

  _renderEnergy() {
    const e = this._energy;
    const t = e?.totals;
    if (!this._energyError && (!t || Object.values(t).every((v) => v === null))) return nothing;
    const wh = (v) => formatValue(v, "Wh");
    const pct = (v) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(1).replace(".", ",")} %`);
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
          <h2>Energiebilanz</h2>
          <div class="range" role="group" aria-label="Zeitraum">
            ${[["today", "Heute"], ["7d", "7 Tage"], ["30d", "30 Tage"]].map(
              ([value, label]) => html`<button class=${this._period === value ? "active" : ""} aria-pressed=${this._period === value}
                @click=${() => this._setPeriod(value)}>${label}</button>`,
            )}
          </div>
        </div>
        ${this._energyError ? html`<div class="alert error" role="alert">${this._energyError}</div>` : nothing}
        ${t ? html`<p class="small muted">Aus den Energiezählern${fromDashboard.length ? " (Netz aus dem Energie-Dashboard)" : " der Wechselrichter"}
          seit ${dayTime.format(new Date(e.start))}${e.consumption_origin === "balance" ? "; Verbrauch aus der Bilanz berechnet" : ""}.</p>
        <div class="tiles">
          ${tile("Autarkie", pct(e.autarky), "Verbrauch ohne Netzbezug", true)}
          ${tile("Eigenverbrauch", pct(e.self_consumption), "PV selbst genutzt", true)}
          ${tile("PV erzeugt", t.pv === null ? null : wh(t.pv))}
          ${tile("Verbraucht", e.consumption === null ? null : wh(e.consumption))}
          ${tile("Netzbezug", t.grid_import === null ? null : wh(t.grid_import))}
          ${tile("Eingespeist", t.grid_export === null ? null : wh(t.grid_export))}
          ${tile("Speicher geladen", t.battery_charge === null ? null : wh(t.battery_charge))}
          ${tile("Speicher entladen", t.battery_discharge === null ? null : wh(t.battery_discharge),
            e.battery_ratio === null ? null : `${pct(e.battery_ratio)} der geladenen Energie (ohne Ladestandsänderung)`)}
          ${e.inverters.map((i) => tile(`Wirkungsgrad ${i.name}`, pct(i.efficiency),
            i.dc_wh === null ? null : `${wh(i.ac_wh)} AC aus ${wh(i.dc_wh)} DC`))}
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
        <h2>Lernen</h2>
        <h3>PV-Prognose</h3>
        <p class="small muted">
          ${l.enabled ? "Die Planung nutzt die angepasste Prognose." : "Ausgeschaltet (Standards) – die Planung nutzt die Rohprognose."}
          Gelernt wird aus dem Schattenbetrieb: Prognose gegen Messung je Stunde${l.weather_entity
            ? html` und Bewölkung laut <code>${l.weather_entity}</code>`
            : " (keine Wetter-Entität gefunden)"}.
          Mit wenigen Tagen bleiben die Faktoren nahe 100 %.
        </p>
        ${l.slots
          ? html`
              <div class="tiles">
                <div class="tile now"><div class="small muted">Gesamt</div><div class="value">${pct(l.overall)}</div>
                  <div class="small muted">der Prognose kommt an · ${l.days} Tage</div></div>
                <div class="tile"><div class="small muted">Klar</div><div class="value">${pct(l.by_cloud.clear)}</div></div>
                <div class="tile"><div class="small muted">Wechselnd bewölkt</div><div class="value">${pct(l.by_cloud.mixed)}</div></div>
                <div class="tile"><div class="small muted">Bedeckt</div><div class="value">${pct(l.by_cloud.overcast)}</div></div>
                <div class="tile"><div class="small muted">Mittlere Abweichung</div>
                  <div class="value">${formatValue(l.error_after, "W")}</div>
                  <div class="small muted">vorher ${formatValue(l.error_before, "W")}</div></div>
              </div>
              ${hours.length
                ? html`<p class="small muted">Je Stunde: ${hours.map(([h, f]) => `${h} Uhr ${pct(f)}`).join(" · ")}</p>`
                : nothing}`
          : html`<p class="muted">Noch keine Tagesdaten – der Schattenbetrieb sammelt ab jetzt.</p>`}
        ${l.planes?.length
          ? html`<h3>Je Dachfläche</h3>
              <ul class="small plane-list">
                ${l.planes.map(
                  (p) => html`<li><strong>${p.name}</strong>: ${p.slots ? pct(p.overall) : "noch keine Daten"}
                    ${p.slots
                      ? html`<span class="muted">(${p.days} Tage${p.by_cloud.clear !== null ? ` · klar ${pct(p.by_cloud.clear)}` : ""}${p.by_cloud.overcast !== null
                          ? ` · bedeckt ${pct(p.by_cloud.overcast)}`
                          : ""})</span>`
                      : nothing}</li>`,
                )}
              </ul>`
          : html`<p class="small muted">Tipp: Unter Einstellungen → Tarif & Prognose lassen sich Dachflächen ihren PV-Strings
              zuordnen; dann lernt Sunflow jede Fläche einzeln (z. B. Verschattung).</p>`}
        <h3>Hausverbrauch</h3>
        ${l.load_model
          ? html`<p class="small">
              Modell aus ${l.load_model.days} Tagen: je Stunde, Werktag/Wochenende und Außentemperatur
              (${l.load_model.heating_w_per_k > 1 ? `+${Math.round(l.load_model.heating_w_per_k)} W je Grad unter 15 °C` : "kein Heizeinfluss erkennbar"}).
              Auf den letzten Tagen ${formatValue(l.load_model.error_model, "W")} Abweichung statt ${formatValue(l.load_model.error_average, "W")}
              mit dem Stundenmittel – ${l.load_model.useful ? "die Planung nutzt das Modell." : "noch nicht besser, die Planung bleibt beim Stundenmittel."}
            </p>`
          : html`<p class="small muted">Sammelt Daten: Das Modell startet nach 14 Tagen mit Temperatur und Verbraucherleistung im
              Schattenbetrieb. Bis dahin plant Sunflow mit den Stundenmitteln der Historie.</p>`}
      </section>
    `;
  }

  _renderEfficiency() {
    const series = (this._efficiency ?? []).filter((s) => s.points.some((p) => p.value !== null));
    if (!series.length) return nothing;
    const pct = (v) => `${(v * 100).toFixed(1).replace(".", ",")} %`;
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
          <h2>Wirkungsgrade</h2>
          <div class="range" role="group" aria-label="Zeitraum Wirkungsgrade">
            ${[[30, "30 Tage"], [90, "90 Tage"], [365, "1 Jahr"]].map(
              ([value, label]) => html`<button class=${this._efficiencyDays === value ? "active" : ""}
                aria-pressed=${this._efficiencyDays === value} @click=${() => this._setEfficiencyDays(value)}>${label}</button>`,
            )}
          </div>
        </div>
        <p class="small muted">Pro Tag aus den Energiezählern. Speicher: Zyklus einschließlich Eigenverbrauch, um die
          Änderung des Ladestands bereinigt. Wechselrichter: Wechselstrom-Ertrag geteilt durch Gleichstrom-Eingang.
          Tage mit wenig Energie (Speicher unter 20 % der Kapazität geladen, Wechselrichter unter 0,5 kWh) werden mit
          den folgenden zu einem Wert zusammengefasst; Tage mit Lücken in den Daten bleiben leer.</p>
        <div class="legend">
          ${series.map((s, i) => {
            const average = mean(s);
            return html`<span class="legend-item"><span class="swatch" style="background:${lines[i].color}"></span>
              ${s.name} (${s.kind === "battery" ? "Speicher" : "Wechselrichter"})${average !== null ? ` · Ø ${pct(average)}` : ""}</span>`;
          })}
        </div>
        ${lineChart(lines, {
          label: "Wirkungsgrade pro Tag",
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
      return `${pct > 0 ? "+" : pct < 0 ? "−" : "±"}${Math.abs(pct)} % ggü. Prognose`;
    };
    const kwh = (v) => formatValue(v * 1000, "Wh");
    return html`
      <section>
        <h2>Schattenbetrieb</h2>
        <p class="small muted">
          Seit ${since} protokolliert Sunflow alle 15 Minuten, was es tun würde und was tatsächlich passiert ist –
          ${e.compared} vollständige Zeitabschnitte (letzte 48 h).
          ${shadow.status.last_error ? html`<br><span class="warn-text">Letzter Fehler: ${shadow.status.last_error}</span>` : nothing}
        </p>
        <div class="actions">
          <a class="secondary button-link" href="api/v1/export" download>Daten exportieren</a>
          <button class="secondary button-link" ?disabled=${this._importing}
            @click=${(e) => e.currentTarget.nextElementSibling.click()}>
            ${this._importing ? "Importiere …" : "Export importieren"}
          </button>
          <input type="file" accept=".zip" hidden @change=${(e) => this._import(e)}>
          ${this._importResult ? html`<span class="small muted">${this._importResult}</span>` : nothing}
        </div>
        ${e.compared === 0
          ? html`<p class="muted">Noch keine abgeschlossenen Zeitabschnitte – der erste erscheint nach dem nächsten Viertelstundenwechsel.</p>`
          : html`
              <div class="tiles">
                <div class="tile">
                  <div class="small muted">PV-Prognose</div>
                  <div class="value">${kwh(e.pv_forecast_kwh)}</div>
                  <div class="small muted">tatsächlich ${kwh(e.pv_actual_kwh)} (${deviation(e.pv_forecast_kwh, e.pv_actual_kwh)})</div>
                </div>
                <div class="tile">
                  <div class="small muted">Lastprognose (ohne steuerbare Verbraucher)</div>
                  <div class="value">${kwh(e.load_forecast_kwh)}</div>
                  <div class="small muted">tatsächlich ${kwh(e.load_actual_kwh)} (${deviation(e.load_forecast_kwh, e.load_actual_kwh)})</div>
                </div>
                <div class="tile">
                  <div class="small muted">Ladestand-Abweichung</div>
                  <div class="value">${e.soc_mae_wh === null ? "–" : formatValue(e.soc_mae_wh, "Wh")}</div>
                  <div class="small muted">mittlerer Abstand Plan zu Ist</div>
                </div>
                <div class="tile">
                  <div class="small muted">Gleiche Entscheidung</div>
                  <div class="value">${pct(e.agreement)}</div>
                  <div class="small muted">${e.differing_slots} Abschnitte hätte Sunflow anders gesteuert</div>
                </div>
              </div>
              <div class="legend">
                <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>Ladestand tatsächlich</span>
                <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[1]}"></span>Ladestand laut Plan</span>
              </div>
              ${lineChart(
                [
                  { name: "Ist", color: SERIES_COLORS[0], points: rows.map((r) => this._point(r, "actual_soc_wh")) },
                  { name: "Plan", color: SERIES_COLORS[1], points: rows.map((r) => this._point(r, "planned_soc_wh")) },
                ],
                { label: "Ladestand Plan gegen Ist", formatY: (v) => formatValue(v, "Wh") },
              )}
              ${this._renderDifferences(rows)}
            `}
      </section>
    `;
  }


  async _import(event) {
    const file = event.target.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    this._importing = true;
    try {
      let response = await fetch("api/v1/import", { method: "POST", body });
      if (response.status === 401 && (await renewIngressSession())) {
        response = await fetch("api/v1/import", { method: "POST", body });
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? response.statusText);
      this._importResult = `${data.imported_slots} Zeitabschnitte importiert.`;
      this._shadow = await api.shadow();
    } catch (err) {
      this._error = `Import fehlgeschlagen: ${err.message}`;
    } finally {
      this._importing = false;
    }
    event.target.value = "";
  }


  _point(row, key) {
    const start = new Date(row.start);
    return { start: start.toISOString(), end: new Date(start.getTime() + 15 * 60_000).toISOString(), value: row[key] ?? 0 };
  }


  _renderDifferences(rows) {
    const differing = rows.filter((r) => r.planned_action !== r.actual_action).slice(-12).reverse();
    if (!differing.length) return html`<p class="small muted">Bisher hätte Sunflow genauso gehandelt wie der Wechselrichter.</p>`;
    return html`
      <div class="table-wrap">
        <table>
          <thead><tr><th>Zeit</th><th>Sunflow hätte</th><th>Tatsächlich</th><th>PV / Last (Ist)</th></tr></thead>
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

  static styles = css`
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
    .button-link {
      display: inline-flex; align-items: center; min-height: 44px; padding: 0 18px; border-radius: 8px;
      text-decoration: none; color: var(--sf-text); cursor: pointer; font-size: 14px;
    }
    .ok { border: 0; background: var(--sf-ok); color: var(--sf-on-ok); }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sunflow-analysis", SunflowAnalysis);
