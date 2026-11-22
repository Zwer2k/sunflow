import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { poll } from "../lib/poll.js";
import { lineChart, SERIES_COLORS, UNASSIGNED_COLOR } from "./charts.js";
import { isoDay } from "./day-nav.js";
import "./day-nav.js";
import { locale, t, tn } from "../lib/i18n.js";

const REFRESH_MS = 10 * 1000;
const VISIBLE_KEY = "sunflow.batteryModules.visible";
// A module's SoC this far from the others' mean is marked
const SOC_DEVIATION_PCT = 10;

const number = (value, digits) =>
  value.toLocaleString(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Values a module can report, in display order; spread is derived from the cell voltages. */
export const MODULE_VALUES = {
  soc: { label: t("Ladestand"), format: (v) => `${number(v, 0)} %` },
  voltage: { label: t("Spannung"), format: (v) => `${number(v, 1)} V` },
  current: { label: t("Strom"), format: (v) => `${number(v, 1)} A` },
  temperature: { label: t("Temperatur"), format: (v) => `${number(v, 0)} °C` },
  cell_spread: { label: t("Zellspreizung"), format: (v) => `${number(v * 1000, 0)} mV` },
  soh: { label: "SOH", format: (v) => `${number(v, 0)} %` },
  cycles: { label: t("Zyklen"), format: (v) => number(v, 0) },
  capacity: { label: t("Kapazität"), format: (v) => `${number(v, 1)} Ah` },
};

const STATUS_LABELS = { alarm: t("Alarm"), protection: t("Schutz"), fault: t("Fehler") };

/** A module's displayed values from what it reports; missing ones stay out. */
function displayed(values) {
  const v = values ?? {};
  const present = (x) => x !== null && x !== undefined;
  const result = {};
  for (const key of ["soc", "voltage", "current", "soh", "cycles"]) if (present(v[key])) result[key] = v[key];
  if (present(v.temperature_max)) result.temperature = v.temperature_max;
  if (present(v.cell_voltage_max) && present(v.cell_voltage_min)) result.cell_spread = v.cell_voltage_max - v.cell_voltage_min;
  if (present(v.capacity_full_ah)) result.capacity = v.capacity_full_ah;
  return result;
}

/** Names without what all share ("Sofar Battery 1/1", "…/2" → "1/1", "1/2"). */
function shortNames(names) {
  if (names.length < 2) return names;
  let prefix = names.reduce((a, b) => {
    let i = 0;
    while (i < a.length && a[i] === b[i]) i += 1;
    return a.slice(0, i);
  });
  // Cut at a word boundary, so "Battery 10" and "Battery 11" keep their numbers
  prefix = prefix.slice(0, prefix.lastIndexOf(" ") + 1);
  return names.map((n) => n.slice(prefix.length) || n);
}

function readVisible() {
  try {
    const stored = JSON.parse(localStorage.getItem(VISIBLE_KEY));
    return Array.isArray(stored) ? stored : ["soc", "temperature", "cell_spread"];
  } catch {
    return ["soc", "temperature", "cell_spread"];
  }
}

/**
 * The modules of the batteries: now as a tower per battery, and their course over a day.
 *
 * Shown as modal dialog; fires "close".
 */
class SunflowBatteryModulesView extends LitElement {
  static properties = {
    _data: { state: true },
    _error: { state: true },
    _visible: { state: true },
    _tab: { state: true },
    _device: { state: true },
    _day: { state: true },
    _history: { state: true },
    _historyError: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._error = null;
    this._visible = readVisible();
    this._tab = "live";
    this._device = null;
    this._day = isoDay(new Date());
    this._history = null;
    this._historyError = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stopPolling = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling?.();
  }

  firstUpdated() {
    this.renderRoot.querySelector("dialog").showModal();
  }

  async _load() {
    try {
      this._data = await api.batteryModules();
      this._error = null;
      this._device ??= this._data[0]?.device_id ?? null;
    } catch (err) {
      this._error = err.message;
    }
  }

  async _loadHistory() {
    const [device, day] = [this._device, this._day];
    this._history = null;
    this._historyError = null;
    try {
      const history = await api.batteryModuleHistory(device, day);
      if (device === this._device && day === this._day) this._history = history;
    } catch (err) {
      this._historyError = err.message;
    }
  }

  _setTab(tab) {
    this._tab = tab;
    if (tab === "day" && !this._history) this._loadHistory();
  }

  _toggle(key, on) {
    this._visible = Object.keys(MODULE_VALUES).filter((k) => (k === key ? on : this._visible.includes(k)));
    try {
      localStorage.setItem(VISIBLE_KEY, JSON.stringify(this._visible));
    } catch {
      // Kept for this visit only
    }
  }

  _close() {
    this.renderRoot.querySelector("dialog")?.close();
    this.dispatchEvent(new CustomEvent("close"));
  }

  render() {
    const batteries = this._data ?? [];
    const battery = batteries.find((b) => b.device_id === this._device) ?? batteries[0];
    return html`
      <dialog aria-labelledby="title" @cancel=${(e) => { e.preventDefault(); this._close(); }}
        @click=${(e) => e.target.tagName === "DIALOG" && this._close()}>
        <div class="box">
          <div class="head">
            <h2 id="title">${battery ? t("Speichermodule · {name}", { name: battery.name }) : t("Speichermodule")}</h2>
            <button class="close" aria-label=${t("Schließen")} @click=${() => this._close()}>✕</button>
          </div>
          ${this._error ? html`<div class="alert">${t("Module nicht abrufbar: {error}", { error: this._error })}</div>` : nothing}
          ${this._data === null && !this._error ? html`<p class="muted">${t("Lade …")}</p>` : nothing}
          ${this._data && !battery ? html`<p class="muted">${t("Kein Speicher meldet Werte je Modul.")}</p>` : nothing}
          ${battery
            ? html`
                <div class="bar">
                  <div class="segmented" role="tablist" aria-label=${t("Ansicht")}>
                    ${[["live", t("Jetzt")], ["day", t("Tagesverlauf")]].map(([key, label]) => html`<button role="tab"
                      aria-selected=${this._tab === key ? "true" : "false"} class=${this._tab === key ? "on" : ""}
                      @click=${() => this._setTab(key)}>${label}</button>`)}
                  </div>
                  ${batteries.length > 1
                    ? html`<select aria-label=${t("Speicher")} .value=${battery.device_id}
                        @change=${(e) => { this._device = e.target.value; this._history = null; if (this._tab === "day") this._loadHistory(); }}>
                        ${batteries.map((b) => html`<option value=${b.device_id}>${b.name}</option>`)}
                      </select>`
                    : nothing}
                </div>
                ${this._tab === "live" ? this._renderLive(battery) : this._renderDay(battery)}`
            : nothing}
        </div>
      </dialog>
    `;
  }

  _renderLive(battery) {
    const modules = battery.modules.map((m) => ({ ...m, shown: displayed(m.values) }));
    const names = shortNames(modules.map((m) => m.name));
    const socs = modules.map((m) => m.shown.soc).filter((v) => v !== undefined);
    const mean = socs.length ? socs.reduce((a, b) => a + b, 0) / socs.length : null;
    const available = Object.keys(MODULE_VALUES).filter((k) => modules.some((m) => k in m.shown));
    const capacity = modules.map((m) => m.shown.capacity).filter((v) => v !== undefined);
    return html`
      <p class="small muted">${tn(modules.length, "{count} Module", { count: modules.length })}${socs.length
        ? ` · ${t("Ladestand {min} bis {max} %", { min: number(Math.min(...socs), 0), max: number(Math.max(...socs), 0) })}`
        : ""}${capacity.length === modules.length ? ` · ${t("zusammen {capacity} Ah", { capacity: number(capacity.reduce((a, b) => a + b, 0), 1) })}` : ""}</p>
      <div class="chips" role="group" aria-label=${t("Angezeigte Werte")}>
        ${available.map((k) => html`<label class="chip ${this._visible.includes(k) ? "on" : ""}">
          <input type="checkbox" .checked=${this._visible.includes(k)} @change=${(e) => this._toggle(k, e.target.checked)}>
          ${MODULE_VALUES[k].label}</label>`)}
      </div>
      <ol class="tower">
        ${modules.map((m, i) => {
          const problems = Object.entries(m.status ?? {}).filter(([, text]) => text);
          const off = mean !== null && m.shown.soc !== undefined && Math.abs(m.shown.soc - mean) >= SOC_DEVIATION_PCT;
          const soc = Math.min(Math.max(m.shown.soc ?? 0, 0), 100);
          return html`<li class="module ${problems.length ? "problem" : ""}" style="--soc: ${soc}%" title=${m.model ? `${m.name} · ${m.model}` : m.name}>
            <span class="fill" aria-hidden="true"></span>
            <span class="name">${names[i]}</span>
            <span class="values">
              ${this._visible.filter((k) => k in m.shown).map((k) => html`<span class="value ${k === "soc" ? "main" : ""}">${k === "soc"
                ? "" : html`<span class="key">${MODULE_VALUES[k].label}</span> `}${MODULE_VALUES[k].format(m.shown[k])}</span>`)}
            </span>
            ${off || problems.length
              ? html`<span class="notes">
                  ${off ? html`<span class="note">⚠ ${t("Ladestand weicht ab (Ø {mean} %)", { mean: number(mean, 0) })}</span>` : nothing}
                  ${problems.map(([key, text]) => html`<span class="note">⚠ ${STATUS_LABELS[key]}: ${text}</span>`)}
                </span>`
              : nothing}
          </li>`;
        })}
      </ol>
    `;
  }

  _renderDay(battery) {
    const history = this._history;
    const names = shortNames(battery.modules.map((m) => m.name));
    const colored = battery.modules.length <= SERIES_COLORS.length;
    const color = (i) => (colored ? SERIES_COLORS[i] : UNASSIGNED_COLOR);
    const charts = [
      ["soc", t("Ladestand"), (v) => `${number(v, 0)} %`, { yMax: 100 }],
      ["current", t("Strom"), (v) => `${number(v, 0)} A`, { negative: true }],
      // Temperatures fill the chart instead of starting at 0 °C
      ["temperature_max", t("Temperatur (wärmster Fühler)"), (v) => `${number(v, 0)} °C`, { fit: true }],
      ["cell_spread_mv", t("Zellspreizung (höchste minus niedrigste Zelle)"), (v) => `${number(v, 0)} mV`, {}],
    ];
    let body = nothing;
    if (this._historyError) body = html`<div class="alert">${t("Verlauf nicht abrufbar: {error}", { error: this._historyError })}</div>`;
    else if (!history) body = html`<p class="muted">${t("Lade …")}</p>`;
    else {
      const start = new Date(history.start).getTime();
      const step = history.step_minutes * 60_000;
      const range = { start: new Date(start), end: new Date(history.end) };
      const byId = new Map(history.modules.map((m) => [m.id, m]));
      const series = (key) => battery.modules
        .map((m, i) => ({ module: byId.get(m.id), i }))
        .filter(({ module }) => module?.[key]?.some((v) => v !== null))
        .map(({ module, i }) => ({
          name: names[i],
          color: color(i),
          thin: !colored,
          points: module[key].map((value, j) => ({
            start: new Date(start + j * step).toISOString(), end: new Date(start + (j + 1) * step).toISOString(), value,
          })),
        }));
      const shown = charts.filter(([key]) => series(key).length);
      body = shown.length
        ? html`<p class="small muted legend">${battery.modules.map((m, i) => html`<span class="swatch" style="background: ${color(i)}"></span>${names[i]}`)}
            ${colored ? nothing : html`<span>· ${t("bei mehr als {count} Modulen ohne eigene Farben", { count: SERIES_COLORS.length })}</span>`}</p>
          ${shown.map(([key, title, formatY, options]) => {
            const lines = series(key);
            const values = lines.flatMap((l) => l.points.map((p) => p.value)).filter((v) => v !== null);
            const low = Math.min(...values);
            const yMin = options.fit ? Math.floor(low / 5) * 5 - 5 : options.negative ? Math.min(0, Math.floor(low)) : 0;
            return html`<h3>${title}</h3>
              <div class="chart-box">${lineChart(lines, { label: title, formatY, range, yMin, yMax: options.yMax })}</div>`;
          })}
          ${history.step_minutes >= 60 ? html`<p class="small muted">${t("stündliche Werte (ältere 5-Minuten-Werte hat Home Assistant gelöscht)")}</p>` : nothing}`
        : html`<p class="muted">${t("Für diesen Tag gibt es keine Werte.")}</p>`;
    }
    return html`
      <sf-day-nav .day=${this._day} @change=${(e) => { this._day = e.detail.day; this._loadHistory(); }}></sf-day-nav>
      ${body}
    `;
  }

  static styles = css`
    dialog {
      padding: 0; border: 0; border-radius: 12px; max-width: min(960px, calc(100vw - 16px)); width: max-content;
      background: var(--sf-surface); color: var(--sf-text); box-shadow: 0 12px 40px rgb(0 0 0 / 0.25);
    }
    dialog::backdrop { background: rgb(0 0 0 / 0.4); }
    .box { padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; width: min(720px, calc(100vw - 52px)); }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    h2 { margin: 0; font-size: 17px; }
    h3 { margin: 4px 0 -6px; font-size: 14px; font-weight: 600; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .alert { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    .close { border: 0; background: transparent; color: var(--sf-muted); font-size: 18px; min-width: 40px; min-height: 40px; cursor: pointer; }
    .bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
    select { font: inherit; min-height: 36px; padding: 0 8px; border-radius: 8px; border: 1px solid var(--sf-border-strong); background: var(--sf-surface); color: var(--sf-text); }
    .segmented { display: flex; gap: 2px; background: var(--sf-subtle); padding: 3px; border-radius: 9px; }
    .segmented button {
      min-height: 32px; padding: 0 12px; border: 0; border-radius: 7px; background: transparent;
      cursor: pointer; font: inherit; font-size: 13px; color: var(--sf-muted);
    }
    .segmented button.on { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.1); }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip {
      display: inline-flex; align-items: center; gap: 6px; min-height: 32px; padding: 0 10px; border-radius: 16px;
      border: 1px solid var(--sf-border-strong); font-size: 13px; cursor: pointer;
    }
    .chip.on { background: var(--sf-primary-bg); color: var(--sf-primary-text); border-color: var(--sf-primary); }
    .chip input { margin: 0; }
    /* Top module first, as they stand in the tower */
    .tower { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
    .module {
      position: relative; overflow: hidden; display: grid; grid-template-columns: minmax(48px, auto) 1fr; gap: 2px 12px;
      align-items: center; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--sf-border-strong); background: var(--sf-subtle);
    }
    .module.problem { border: 2px solid var(--sf-error-text); }
    .fill {
      position: absolute; inset: 0 auto 0 0; width: var(--soc);
      background: color-mix(in srgb, var(--sf-series-3) 22%, transparent); pointer-events: none;
    }
    .name, .values, .notes { position: relative; }
    .name { font-weight: 600; font-size: 14px; }
    .values { display: flex; flex-wrap: wrap; gap: 4px 16px; font-variant-numeric: tabular-nums; font-size: 13px; }
    .value.main { font-weight: 600; font-size: 15px; }
    .key { color: var(--sf-muted); }
    .notes { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
    .chart-box { width: 100%; }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .legend { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
    .swatch { display: inline-block; width: 16px; height: 3px; border-radius: 2px; margin-left: 4px; }
  `;
}

customElements.define("sf-battery-modules-view", SunflowBatteryModulesView);
