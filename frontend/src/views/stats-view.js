import { LitElement, html, svg, css, nothing } from "../../vendor/lit-all.min.js";
import "../components/display-toggles.js";
import { api } from "../lib/api.js";
import { confirmDialog } from "../components/dialog.js";
import { formatValue } from "../lib/format.js";
import { decimal, locale, t } from "../lib/i18n.js";
import { toggleSheet } from "../lib/display-toggles.js";

const PERIODS = [
  ["30d", t("30 Tage")],
  ["365d", t("12 Monate")],
  ["this_year", t("Dieses Jahr")],
  ["last_year", t("Letztes Jahr")],
  ["all", t("Gesamt")],
];
// Origin of the energy in the bars: PV, home battery, grid
const ORIGINS = [
  ["pv_wh", t("Sonne"), "var(--sf-series-4)"],
  ["battery_wh", t("Hausspeicher"), "var(--sf-series-3)"],
  ["grid_wh", t("Netz"), "var(--sf-series-1)"],
];
const SESSIONS_SHOWN = 30;
const dayTime = new Intl.DateTimeFormat(locale(), { weekday: "short", day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
const monthName = new Intl.DateTimeFormat(locale(), { month: "short" });
const monthYear = new Intl.DateTimeFormat(locale(), { month: "short", year: "2-digit" });

// From 100 kWh on whole kWh: "1.761 kWh" fits a tile, "1.761,0 kWh" does not
const whole = new Intl.NumberFormat(locale(), { maximumFractionDigits: 0 });
const kwh = (wh) => (Math.abs(wh) >= 100_000 ? `${whole.format(wh / 1000)} kWh` : formatValue(wh, "Wh"));
// Axis steps are round: no decimals needed
const axisKwh = (wh) => (wh >= 1000 ? `${new Intl.NumberFormat(locale()).format(Math.round(wh / 1000))} kWh` : `${decimal(wh / 1000)} kWh`);
const pct = (share) => (share === null || share === undefined ? "–" : `${decimal(Math.round(share * 100))} %`);
const euro = (value) => (value === null || value === undefined ? "–" : new Intl.NumberFormat(locale(), { style: "currency", currency: "EUR" }).format(value));
const ct = (perKwh) => (perKwh === null || perKwh === undefined ? "–" : `${decimal((perKwh * 100).toFixed(1))} ct/kWh`);

/** Statistics of the consumers' sessions (charging, heating), like evcc's, and the one-time import from evcc. */
class SunflowStats extends LitElement {
  static properties = {
    _data: { state: true },
    _error: { state: true },
    _period: { state: true },
    _device: { state: true },
    _vehicle: { state: true },
    _shown: { state: true },
    _import: { state: true },
    _chart: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._error = null;
    this._period = "365d";
    this._device = "";
    this._vehicle = "";
    this._shown = SESSIONS_SHOWN;
    // The chart as evcc's: what, grouped by what, per month or year
    this._chart = { metric: "energy", group: "origin", step: "month" };
    // The import's steps: source given, preview with matching, busy, result
    this._import = { url: "", content: null, file: null, preview: null, loadpoints: {}, vehicles: {}, busy: false, error: null, result: null };
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    // The month chart is drawn in screen pixels: a turned phone or a resized window draws it anew
    this._onResize = () => this.requestUpdate();
    window.addEventListener("resize", this._onResize);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener("resize", this._onResize);
  }

  async _load() {
    try {
      this._data = await api.consumerStats(this._period, this._device || null, this._vehicle || null);
      this._error = null;
    } catch (err) {
      this._error = t("Statistik nicht verfügbar: {error}", { error: err.message });
    }
  }

  _set(changes) {
    Object.assign(this, changes);
    this._shown = SESSIONS_SHOWN;
    this._load();
  }

  render() {
    const data = this._data;
    return html`
      <div class="heading">
        <div class="title-row">
          <h1>${t("Statistik")}</h1>
          <sf-display-toggles placement="title" help></sf-display-toggles>
        </div>
        <p class="muted help">${t("Ladevorgänge und Läufe der Verbraucher: wie viel Energie, woher sie kam und was sie gekostet hat. Sonnenanteil und Preis wie bei evcc: PV und Hausspeicher zählen als eigene Energie, sie kosten die entgangene Einspeisevergütung.")}</p>
      </div>
      ${this._renderFilters()}
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${data === null && !this._error ? html`<p class="muted" role="status">${t("Lade …")}</p>` : nothing}
      ${data ? this._renderData(data) : nothing}
      ${this._renderImport()}
    `;
  }

  _renderFilters() {
    const filters = this._data?.filters;
    return html`<div class="filters">
      <div class="range" role="group" aria-label=${t("Zeitraum")}>
        ${PERIODS.map(([value, label]) => html`<button class=${this._period === value ? "active" : ""} aria-pressed=${this._period === value}
          @click=${() => this._set({ _period: value })}>${label}</button>`)}
      </div>
      ${filters?.consumers.length > 1
        ? html`<select aria-label=${t("Verbraucher")} @change=${(e) => this._set({ _device: e.target.value })}>
            <option value="">${t("Alle Verbraucher")}</option>
            ${filters.consumers.map((c) => html`<option value=${c.id} ?selected=${this._device === c.id}>${c.name}</option>`)}
          </select>`
        : nothing}
      ${filters?.vehicles.length
        ? html`<select aria-label=${t("Fahrzeug")} @change=${(e) => this._set({ _vehicle: e.target.value })}>
            <option value="">${t("Alle Fahrzeuge")}</option>
            ${filters.vehicles.map((v) => html`<option value=${v.id} ?selected=${this._vehicle === v.id}>${v.name}</option>`)}
          </select>`
        : nothing}
    </div>`;
  }

  _renderData(data) {
    const s = data.totals;
    if (!s.sessions) {
      return html`<p class="muted">${t("Keine Ladevorgänge in diesem Zeitraum.")}${data.first
        ? ` ${t("Aufgezeichnet seit {date}.", { date: new Date(data.first).toLocaleDateString(locale()) })}`
        : ""}</p>`;
    }
    const tile = (label, value, hint = null, highlight = false) => html`<div class="tile ${highlight ? "now" : ""}">
      <div class="small muted">${label}</div><div class="value">${value}</div>
      ${hint ? html`<div class="small muted">${hint}</div>` : nothing}</div>`;
    return html`
      <div class="tiles">
        ${tile(t("Geladen"), kwh(s.energy_wh), t("{count} Ladevorgänge", { count: s.sessions }), true)}
        ${tile(t("Sonnenanteil"), pct(s.solar_share), s.battery_wh ? t("davon Hausspeicher {energy}", { energy: kwh(s.battery_wh) }) : null, true)}
        ${tile(t("Ø Preis"), ct(s.price_per_kwh), t("mit entgangener Einspeisung"))}
        ${tile(t("Kosten"), euro(s.cost_eur), t("Netz und entgangene Einspeisung"))}
        ${s.paid_eur !== null
          ? tile(t("Bezahlt"), euro(s.paid_eur), s.paid_sessions < s.sessions
            ? t("Netzbezug, nur Sunflows eigene Aufzeichnung")
            : t("Netzbezug zum Preis seiner Zeit"))
          : nothing}
        ${s.saved_eur !== null ? tile(t("Gespart"), euro(s.saved_eur), t("gegenüber allem aus dem Netz")) : nothing}
        ${tile(t("Ø Leistung"), s.mean_power_w ? formatValue(s.mean_power_w, "W") : "–", t("{hours} h", { hours: decimal(s.hours) }))}
      </div>
      ${data.imported ? html`<p class="small muted">${t("Enthält {count} aus evcc übernommene Ladevorgänge (vor der eigenen Aufzeichnung).", { count: data.imported })}</p>` : nothing}
      ${this._renderChart(data.sessions)}
      ${this._renderShares(data.sessions)}
      ${data.consumers.length > 1 ? this._renderGroup(t("Je Verbraucher"), data.consumers) : nothing}
      ${data.vehicles.length ? this._renderGroup(t("Je Fahrzeug"), data.vehicles) : nothing}
      ${this._renderSessions(data.sessions)}
    `;
  }

  /** evcc's chart of the sessions page: energy, solar share or price per month or year, energy grouped as chosen. */
  _renderChart(sessions) {
    const { metric, group, step } = this._chart;
    const periods = periodKeys(sessions, step);
    if (!periods.length) return nothing;
    const set = (changes) => (this._chart = { ...this._chart, ...changes });
    const seg = (key, options, value) => html`<div class="range" role="group">
      ${options.map(([v, label]) => html`<button class=${value === v ? "active" : ""} aria-pressed=${value === v} @click=${() => set({ [key]: v })}>${label}</button>`)}
    </div>`;
    let stacks;
    let format;
    if (metric === "energy") {
      stacks = energyStacks(sessions, periods, step, group);
      format = { axis: axisKwh, value: kwh };
    } else {
      const totals = periods.map((p) => summarize(sessions.filter((s) => periodOf(s, step) === p)));
      const values = totals.map((s) => (metric === "solar" ? s.solar_share : s.price_per_kwh));
      stacks = [{ name: metric === "solar" ? t("Sonnenanteil") : t("Ø Preis"), color: metric === "solar" ? "var(--sf-series-4)" : "var(--sf-series-6)", values }];
      format = metric === "solar"
        ? { axis: (v) => `${Math.round(v * 100)} %`, value: pct, max: 1 }
        : { axis: (v) => `${decimal(Math.round(v * 100))} ct`, value: ct };
    }
    return html`<section>
      <div class="section-head">
        <h2>${step === "year" ? t("Je Jahr") : t("Je Monat")}</h2>
        <div class="chart-controls">
          ${seg("metric", [["energy", t("Energie")], ["solar", t("Sonnenanteil")], ["price", t("Preis")]], metric)}
          ${metric === "energy" ? seg("group", [["origin", t("Herkunft")], ["consumer", t("Verbraucher")], ["vehicle", t("Fahrzeug")]], group) : nothing}
          ${seg("step", [["month", t("Monat")], ["year", t("Jahr")]], step)}
        </div>
      </div>
      ${this._barChart(periods.map((p) => periodLabel(p, step, periods)), stacks, format)}
      ${stacks.length > 1 || metric === "energy"
        ? html`<div class="legend small">${stacks.map((s) => html`<span class="legend-item"><span class="swatch" style="background:${s.color}"></span>${s.name}</span>`)}</div>`
        : nothing}
    </section>`;
  }

  /** Bars per period; several stacks are drawn on top of each other. labels: [{text, show, title}]. */
  _barChart(labels, stacks, { axis, value, max = null }) {
    // In screen pixels, so the 11 px text stays 11 px on a PC and a phone
    const W = Math.min(Math.max((this.getBoundingClientRect().width || 720) - 2, 300), 1200);
    const H = 220;
    const pad = { left: 62, right: 8, top: 12, bottom: 26 };
    const sums = labels.map((_, i) => stacks.reduce((sum, s) => sum + (s.values[i] ?? 0), 0));
    const top = max ?? (() => {
      const highest = Math.max(...sums, 1e-9);
      const step = niceStep(highest / 4);
      return Math.ceil(highest / step) * step;
    })();
    const tickStep = max ?? niceStep(top / 4);
    const ticks = max ? [0, 0.25, 0.5, 0.75, 1] : Array.from({ length: Math.round(top / tickStep) + 1 }, (_, i) => i * tickStep);
    const width = (W - pad.left - pad.right) / labels.length;
    const y = (v) => H - pad.bottom - (v / top) * (H - pad.top - pad.bottom);
    return html`<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label=${stacks.map((s) => s.name).join(", ")}>
      ${ticks.map((v) => svg`<line x1=${pad.left} x2=${W - pad.right} y1=${y(v)} y2=${y(v)} class="grid"></line>
        <text x=${pad.left - 6} y=${y(v) + 4} class="axis" text-anchor="end">${axis(v)}</text>`)}
      ${labels.map((label, i) => {
        const x = pad.left + i * width + width * 0.15;
        let base = 0;
        return svg`<g>
          ${stacks.map((s) => {
            const v = s.values[i];
            if (v === null || v === undefined || v <= 0) return nothing;
            const y0 = y(base);
            base += v;
            return svg`<rect x=${x} y=${y(base)} width=${width * 0.7} height=${Math.max(y0 - y(base), 0.5)} rx="2" style="fill: ${s.color}"></rect>`;
          })}
          <rect x=${pad.left + i * width} y=${pad.top} width=${width} height=${H - pad.top - pad.bottom} class="hit">
            <title>${label.title}: ${stacks.filter((s) => s.values[i]).map((s) => `${stacks.length > 1 ? `${s.name} ` : ""}${value(s.values[i])}`).join(" · ") || "–"}</title>
          </rect>
          ${label.show ? svg`<text x=${pad.left + (i + 0.5) * width} y=${H - 8} class="axis" text-anchor="middle">${label.text}</text>` : nothing}
        </g>`;
      })}
    </svg>`;
  }

  /** How the energy is shared among the consumers and the vehicles, as evcc's rings. */
  _renderShares(sessions) {
    const rings = [
      [t("Je Verbraucher"), shares(sessions, (s) => s.consumer)],
      [t("Je Fahrzeug"), shares(sessions.filter((s) => s.vehicle), (s) => s.vehicle)],
    ].filter(([, parts]) => parts.length > 1);
    if (!rings.length) return nothing;
    return html`<section>
      <h2>${t("Aufteilung der Energie")}</h2>
      <div class="rings">${rings.map(([title, parts]) => html`<figure class="ring">
        ${donut(parts, title)}
        <figcaption>
          <strong class="small">${title}</strong>
          ${parts.map((p) => html`<span class="legend-item small"><span class="swatch" style="background:${p.color}"></span>${p.name}
            <span class="muted">${pct(p.share)} · ${kwh(p.energy_wh)}</span></span>`)}
        </figcaption>
      </figure>`)}</div>
    </section>`;
  }

  _renderGroup(title, rows) {
    return html`<section>
      <h2>${title}</h2>
      <div class="table-wrap"><table>
        <thead><tr><th></th><th>${t("Geladen")}</th><th>${t("Vorgänge")}</th><th>${t("Sonnenanteil")}</th><th>${t("Ø Preis")}</th><th>${t("Bezahlt")}</th></tr></thead>
        <tbody>${rows.map((r) => html`<tr><td>${r.name}</td><td>${kwh(r.energy_wh)}</td><td>${r.sessions}</td><td>${pct(r.solar_share)}</td>
          <td>${ct(r.price_per_kwh)}</td><td>${euro(r.paid_eur)}</td></tr>`)}</tbody>
      </table></div>
    </section>`;
  }

  _renderSessions(sessions) {
    const shown = sessions.slice(0, this._shown);
    return html`<section>
      <h2>${t("Ladevorgänge")}</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>${t("Beginn")}</th><th>${t("Verbraucher")}</th><th>${t("Fahrzeug")}</th><th>${t("Dauer")}</th><th>${t("Geladen")}</th>
          <th>${t("Sonne")}</th><th>${t("Kosten")}</th></tr></thead>
        <tbody>${shown.map((s) => html`<tr>
          <td>${dayTime.format(new Date(s.start))}${s.source !== "sunflow" ? html` <span class="chip">${s.source}</span>` : nothing}</td>
          <td>${s.consumer}</td>
          <td>${[s.vehicle, s.card].filter(Boolean).join(" · ") || "–"}${s.soc_start !== null && s.soc_end !== null
            ? html`<span class="small muted"> ${Math.round(s.soc_start)} → ${Math.round(s.soc_end)} %</span>`
            : nothing}</td>
          <td>${t("{hours} h", { hours: decimal(s.duration_h.toFixed(1)) })}</td>
          <td>${kwh(s.energy_wh)}</td>
          <td>${pct(s.solar_share)}</td>
          <td>${euro(s.cost_eur)}</td>
        </tr>`)}</tbody>
      </table></div>
      ${sessions.length > shown.length
        ? html`<div><button class="secondary" @click=${() => (this._shown += SESSIONS_SHOWN)}>${t("Weitere {count} anzeigen", { count: Math.min(SESSIONS_SHOWN, sessions.length - shown.length) })}</button></div>`
        : nothing}
    </section>`;
  }

  _renderImport() {
    const im = this._import;
    const p = im.preview;
    const imported = this._data?.imported ?? 0;
    const consumers = this._data?.filters?.all_consumers ?? [];
    // Needed once: folded away at the bottom, open while something is going on
    return html`<details class="import" ?open=${Boolean(p || im.busy || im.error || im.result)}>
      <summary class="small muted">${t("Daten aus evcc übernehmen …")}</summary>
      <p class="small muted help">${t("Einmalig: Sunflow liest die Ladevorgänge von evcc (Adresse wie http://evcc.local:7070) oder aus einer Datei – der Antwort von /api/sessions oder dem CSV-Export der Ladevorgänge-Seite. Übernommen wird, was vor Sunflows eigener Aufzeichnung lag; doppelt Übernommenes wird erkannt.")}</p>
      ${imported ? html`<p class="small">${t("Bisher übernommen: {count} Ladevorgänge.", { count: imported })}
          <button class="link" @click=${() => this._removeImport()}>${t("Übernommene entfernen …")}</button></p>` : nothing}
      <div class="import-source">
        <label class="field"><span class="field-label">${t("Adresse von evcc")}</span>
          <input type="url" placeholder="http://evcc.local:7070" .value=${im.url}
            @input=${(e) => (this._import = { ...im, url: e.target.value, content: null, file: null, preview: null, result: null })}></label>
        <span class="small muted or">${t("oder")}</span>
        <label class="field"><span class="field-label">${t("Datei (JSON oder CSV)")}</span>
          <input type="file" accept=".json,.csv,text/csv,application/json" @change=${(e) => this._readFile(e)}></label>
        <button class="secondary" ?disabled=${im.busy || (!im.url && !im.content)} @click=${() => this._preview()}>${t("Vorschau")}</button>
      </div>
      ${im.error ? html`<div class="alert error" role="alert">${im.error}</div>` : nothing}
      ${im.result ? html`<div class="alert ok" role="status">${im.result}</div>` : nothing}
      ${p
        ? html`<div class="preview">
            <p>${t("{count} Ladevorgänge mit {energy} vom {first} bis {last}.", {
              count: p.count, energy: kwh(p.energy_wh),
              first: new Date(p.first).toLocaleDateString(locale()), last: new Date(p.last).toLocaleDateString(locale()),
            })}</p>
            <div class="mapping">
              ${p.loadpoints.map((l) => html`<label class="field"><span class="field-label">${t("Ladepunkt „{name}“ ist", { name: l.name })}</span>
                <select @change=${(e) => (this._import = { ...this._import, loadpoints: { ...this._import.loadpoints, [l.name]: e.target.value || null } })}>
                  <option value="" ?selected=${!im.loadpoints[l.name]}>${t("keinem Gerät zugeordnet")}</option>
                  ${consumers.map((c) => html`<option value=${c.id} ?selected=${im.loadpoints[l.name] === c.id}>${c.name}</option>`)}
                </select></label>`)}
              ${p.vehicles.map((v) => html`<label class="field"><span class="field-label">${t("Fahrzeug „{name}“ ist", { name: v.name })}</span>
                <select @change=${(e) => (this._import = { ...this._import, vehicles: { ...this._import.vehicles, [v.name]: e.target.value || null } })}>
                  <option value="" ?selected=${!im.vehicles[v.name]}>${t("Name von evcc behalten")}</option>
                  ${(this._data?.filters?.vehicles ?? []).map((c) => html`<option value=${c.id} ?selected=${im.vehicles[v.name] === c.id}>${c.name}</option>`)}
                </select></label>`)}
            </div>
            <div><button class="primary" ?disabled=${im.busy} @click=${() => this._runImport()}>${t("Übernehmen")}</button></div>
          </div>`
        : nothing}
    </details>`;
  }

  async _readFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const content = await file.text();
    this._import = { ...this._import, content, file: file.name, preview: null, result: null, error: null };
  }

  _source() {
    const im = this._import;
    return im.content ? { content: im.content } : { url: im.url };
  }

  async _preview() {
    this._import = { ...this._import, busy: true, error: null, result: null };
    try {
      const preview = await api.evccPreview(this._source());
      this._import = {
        ...this._import, preview, busy: false,
        loadpoints: Object.fromEntries(preview.loadpoints.map((l) => [l.name, l.device_id])),
        vehicles: Object.fromEntries(preview.vehicles.map((v) => [v.name, v.vehicle_id])),
      };
    } catch (err) {
      this._import = { ...this._import, busy: false, preview: null, error: err.message };
    }
  }

  async _runImport() {
    const im = this._import;
    this._import = { ...im, busy: true, error: null };
    try {
      const result = await api.evccImport({ ...this._source(), loadpoints: im.loadpoints, vehicles: im.vehicles });
      this._import = {
        ...this._import, busy: false, preview: null,
        result: t("{count} Ladevorgänge übernommen, {skipped} waren schon da.", { count: result.imported, skipped: result.skipped }),
      };
      this._load();
    } catch (err) {
      this._import = { ...this._import, busy: false, error: err.message };
    }
  }

  async _removeImport() {
    const ok = await confirmDialog({
      title: t("Übernommene Ladevorgänge entfernen?"),
      text: t("Die aus evcc übernommenen Ladevorgänge werden gelöscht; Sunflows eigene Aufzeichnung bleibt. Danach lässt sich erneut übernehmen."),
      confirm: t("Entfernen"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api.evccRemove();
      this._load();
    } catch (err) {
      this._import = { ...this._import, error: err.message };
    }
  }

  static styles = [toggleSheet, css`
    :host { display: flex; flex-direction: column; gap: 16px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0 0 8px; font-size: 17px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .heading { display: flex; flex-direction: column; gap: 4px; }
    .title-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    section { border-top: 1px solid var(--sf-border); padding-top: 16px; display: flex; flex-direction: column; gap: 8px; }
    .filters { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
    .range { display: flex; flex-wrap: wrap; gap: 2px; background: var(--sf-subtle); padding: 3px; border-radius: 10px; }
    .range button { min-height: 34px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent; color: var(--sf-muted); cursor: pointer; font-size: 13px; }
    .range button.active { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.1); }
    select, input[type="url"] {
      min-height: 40px; padding: 0 12px; font: inherit; font-size: 14px; border: 1px solid var(--sf-border-strong); border-radius: 8px;
      background: var(--sf-surface); color: var(--sf-text);
    }
    .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; }
    .tile.now { background: var(--sf-primary-bg); }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .chart { width: 100%; height: auto; display: block; max-height: 260px; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-subtle); fill-opacity: 0.4; }
    .section-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; }
    .section-head h2 { margin: 0; }
    .chart-controls { display: flex; flex-wrap: wrap; gap: 6px; }
    .chart-controls .range button { min-height: 30px; padding: 0 10px; font-size: 12px; }
    .rings { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); gap: 16px; }
    .ring { margin: 0; display: flex; align-items: center; gap: 16px; }
    .donut { width: 120px; height: 120px; flex: none; }
    .ring figcaption { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; color: var(--sf-muted); }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--sf-border); border-radius: 8px; }
    table { border-collapse: collapse; width: 100%; min-width: 560px; font-size: 13px; }
    th { text-align: left; font-weight: 600; background: var(--sf-subtle); padding: 8px 10px; white-space: nowrap; }
    /* Rows stay one line; a narrow screen scrolls the table sideways */
    td { padding: 6px 10px; border-top: 1px solid var(--sf-border); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .chip { font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 8px; background: var(--sf-neutral-bg); color: var(--sf-neutral-text); }
    .import { display: flex; flex-direction: column; gap: 8px; }
    .import summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
    .import[open] { border-top: 1px solid var(--sf-border); padding-top: 12px; }
    .import[open] > * + * { margin-top: 8px; }
    .import-source { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: flex-end; }
    .import-source .field { flex: 1 1 220px; }
    .or { padding-bottom: 12px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; }
    .preview { display: flex; flex-direction: column; gap: 10px; border: 1px solid var(--sf-border); border-radius: 10px; padding: 12px 14px; }
    .mapping { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px 14px; }
    .primary, .secondary { min-height: 40px; padding: 0 18px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .link { border: 0; background: none; padding: 0; color: var(--sf-primary-text); text-decoration: underline; cursor: pointer; font-size: 13px; }
    .alert { padding: 10px 14px; border-radius: 8px; font-size: 14px; }
    .alert.error { background: var(--sf-error-bg); color: var(--sf-error-text); }
    .alert.ok { background: var(--sf-ok-bg); color: var(--sf-ok-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
    @media (pointer: coarse) { .range button { min-height: 40px; } }
  `];
}

/** A round step for the axis (1, 2 or 5 times a power of ten) of at least the given size. */
function niceStep(raw) {
  // Also below 1 (prices in €/kWh)
  const power = 10 ** Math.floor(Math.log10(raw > 0 ? raw : 1));
  return [1, 2, 5, 10].map((f) => f * power).find((s) => s >= raw) ?? 10 * power;
}

// Series beyond these become "other"
const MAX_SERIES = 5;
const OTHER_COLOR = "var(--sf-series-neutral)";

function periodOf(session, step) {
  const start = new Date(session.start);
  return step === "year" ? `${start.getFullYear()}` : `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}`;
}

/** Every month (or year) from the first to the last session, empty ones too. */
function periodKeys(sessions, step) {
  if (!sessions.length) return [];
  const keys = sessions.map((s) => periodOf(s, step)).sort();
  const [first, last] = [keys[0], keys.at(-1)];
  const result = [];
  if (step === "year") {
    for (let y = Number(first); y <= Number(last); y += 1) result.push(`${y}`);
    return result;
  }
  let [y, m] = first.split("-").map(Number);
  for (;;) {
    const key = `${y}-${String(m).padStart(2, "0")}`;
    if (key > last) break;
    result.push(key);
    [y, m] = m === 12 ? [y + 1, 1] : [y, m + 1];
  }
  return result;
}

function periodLabel(key, step, all) {
  if (step === "year") return { text: key, show: true, title: key };
  const date = new Date(`${key}-01T12:00:00`);
  const many = all.length > 14;
  const show = !many || date.getMonth() % 3 === 0;
  const text = date.getMonth() === 0 || key === all[0] ? monthYear.format(date) : monthName.format(date);
  return { text, show, title: monthYear.format(date) };
}

/** Energy per period: by origin, or by the largest consumers or vehicles (the rest as "other"). */
function energyStacks(sessions, periods, step, group) {
  const index = new Map(periods.map((p, i) => [p, i]));
  if (group === "origin") {
    return ORIGINS.map(([key, name, color]) => {
      const values = periods.map(() => 0);
      for (const s of sessions) values[index.get(periodOf(s, step))] += s[key];
      return { name, color, values };
    });
  }
  const nameOf = group === "consumer" ? (s) => s.consumer : (s) => s.vehicle || t("ohne Fahrzeug");
  const ranked = shares(sessions, nameOf);
  return ranked.map((part) => {
    const values = periods.map(() => 0);
    for (const s of sessions) if (part.members.has(nameOf(s))) values[index.get(periodOf(s, step))] += s.energy_wh;
    return { name: part.name, color: part.color, values };
  });
}

/** Energy per name, the largest first with their colours; the smallest together as "other". */
function shares(sessions, nameOf) {
  const sums = new Map();
  for (const s of sessions) sums.set(nameOf(s), (sums.get(nameOf(s)) ?? 0) + s.energy_wh);
  const total = [...sums.values()].reduce((a, b) => a + b, 0) || 1;
  const sorted = [...sums.entries()].sort((a, b) => b[1] - a[1]);
  const kept = sorted.length > MAX_SERIES + 1 ? sorted.slice(0, MAX_SERIES) : sorted;
  const parts = kept.map(([name, wh], i) => ({ name, energy_wh: wh, share: wh / total, color: `var(--sf-series-${i + 1})`, members: new Set([name]) }));
  if (kept.length < sorted.length) {
    const rest = sorted.slice(kept.length);
    const wh = rest.reduce((sum, [, v]) => sum + v, 0);
    parts.push({ name: t("Andere"), energy_wh: wh, share: wh / total, color: OTHER_COLOR, members: new Set(rest.map(([n]) => n)) });
  }
  return parts;
}

/** Solar share and price of some sessions, counted as the backend does. */
function summarize(sessions) {
  const energy = sessions.reduce((sum, s) => sum + s.energy_wh, 0);
  const own = sessions.reduce((sum, s) => sum + (s.solar_share ?? 0) * s.energy_wh, 0);
  const priced = sessions.filter((s) => s.cost_eur || s.grid_only_eur);
  const pricedWh = priced.reduce((sum, s) => sum + s.energy_wh, 0);
  return {
    solar_share: energy ? own / energy : null,
    price_per_kwh: pricedWh ? priced.reduce((sum, s) => sum + s.cost_eur, 0) / (pricedWh / 1000) : null,
  };
}

/** A ring of the parts' shares. */
function donut(parts, label) {
  const r = 52;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return html`<svg viewBox="0 0 140 140" class="donut" role="img" aria-label=${label}>
    <g transform="rotate(-90 70 70)">
      ${parts.map((p) => {
        const length = p.share * c;
        const segment = svg`<circle cx="70" cy="70" r=${r} fill="none" stroke-width="22" style="stroke: ${p.color}"
          stroke-dasharray="${Math.max(length - 1.5, 0)} ${c}" stroke-dashoffset=${-offset}><title>${p.name}: ${pct(p.share)}</title></circle>`;
        offset += length;
        return segment;
      })}
    </g>
  </svg>`;
}

customElements.define("sunflow-stats", SunflowStats);
