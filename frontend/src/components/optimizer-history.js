import { LitElement, html, svg, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { formatPower } from "../lib/format.js";
import { lineChart } from "./charts.js";
import "./roof.js";
import { isoDay } from "./day-nav.js";
import { locale, t } from "../lib/i18n.js";

// Below this share of the roof part's best module the reference is too weak to compare (dawn, dusk)
const MIN_REFERENCE_SHARE = 0.03;
const MIN_REFERENCE_W = 10;
const ROW_H = 16;
const LABEL_W = 64;
const ENERGY_W = 92;
const HEAT_W = 900;
const HOUR_STEPS = [1, 2, 3, 4, 6];
const MIN_LABEL_SPACING = 44;
const AXIS_H = 18;
// Relative shares are cubed, so 80–95 % of the best module visibly fall off instead of looking like 100 %
const RELATIVE_GAMMA = 3;
const RELATIVE_TICKS = [0, 0.5, 0.8, 1];

const mix = (intensity) =>
  `color-mix(in srgb, var(--sf-series-2) ${Math.round(Math.min(Math.max(intensity, 0), 1) * 100)}%, var(--sf-subtle))`;

const timeFormat = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });
const dayFormat = new Intl.DateTimeFormat(locale(), { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" });
const percent = new Intl.NumberFormat(locale(), { maximumFractionDigits: 0 });
const kwh = new Intl.NumberFormat(locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** Roof part of a module: modules set apart by separators may face another way. */
function roofPart(optimizer, breaks, colBreaks) {
  if (optimizer.row === null || optimizer.col === null) return "free";
  return `${breaks.filter((b) => b <= optimizer.row).length}:${colBreaks.filter((c) => c <= optimizer.col).length}`;
}

/**
 * Power of the optimizers of one string over a day: heatmap (one row per module), the string's
 * median with min–max band, and the roof at a chosen time.
 *
 * optimizers: those of the string (with name, row, col); breaks / colBreaks: the roof layout.
 */
class SunflowOptimizerHistory extends LitElement {
  static properties = {
    string: {},
    optimizers: { attribute: false },
    breaks: { attribute: false },
    colBreaks: { attribute: false },
    _day: { state: true },
    _data: { state: true },
    _error: { state: true },
    _relative: { state: true },
    _hover: { state: true },
    _slot: { state: true },
  };

  constructor() {
    super();
    this.optimizers = [];
    this.breaks = [];
    this.colBreaks = [];
    this._day = isoDay(new Date());
    this._data = null;
    this._error = null;
    this._relative = true;
    this._hover = null;
    this._slot = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._resized = () => this.requestUpdate();
    window.addEventListener("resize", this._resized);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener("resize", this._resized);
  }

  async _load() {
    const day = this._day;
    this._data = null;
    this._error = null;
    try {
      const data = await api.optimizerHistory(this.string, day);
      if (day !== this._day) return;
      this._data = data;
      this._slot = null;
    } catch (err) {
      this._error = err.message;
    }
  }

  _setDay(day) {
    this._day = day;
    this._hover = null;
    this._load();
  }

  /** Rows in roof order with their power, relative share and energy; slots cropped to daylight. */
  _model() {
    const byId = new Map((this._data?.optimizers ?? []).map((o) => [o.id, o]));
    const rows = this.optimizers
      .filter((o) => byId.has(o.id))
      .map((o) => ({ ...o, power: byId.get(o.id).power, energy: byId.get(o.id).energy_wh, part: roofPart(o, this.breaks, this.colBreaks) }))
      .sort((a, b) => (a.row ?? Infinity) - (b.row ?? Infinity) || (a.col ?? Infinity) - (b.col ?? Infinity) || a.label.localeCompare(b.label));
    if (!rows.length) return null;
    const count = rows[0].power.length;
    const step = this._data.step_minutes * 60_000;
    const start = new Date(this._data.start).getTime();
    const sunny = [...Array(count).keys()].filter((i) => rows.some((r) => (r.power[i] ?? 0) > 0));
    const first = sunny.length ? Math.max(sunny[0] - 1, 0) : 0;
    const last = sunny.length ? Math.min(sunny.at(-1) + 1, count - 1) : count - 1;
    const slots = [];
    for (let i = first; i <= last; i += 1) slots.push({ index: i, start: new Date(start + i * step), end: new Date(start + (i + 1) * step) });
    const parts = Map.groupBy(rows, (r) => r.part);
    const peak = Math.max(1, ...rows.flatMap((r) => r.power.map((p) => p ?? 0)));
    for (const members of parts.values()) {
      const partPeak = Math.max(0, ...members.flatMap((r) => r.power.map((p) => p ?? 0)));
      const best = [...Array(count).keys()].map((i) => Math.max(0, ...members.map((r) => r.power[i] ?? 0)));
      const energies = members.map((r) => r.energy).filter((e) => e !== null);
      const typical = energies.length ? median(energies) : null;
      for (const r of members) {
        r.share = r.power.map((p, i) =>
          p === null || best[i] < Math.max(MIN_REFERENCE_W, partPeak * MIN_REFERENCE_SHARE) ? null : p / best[i]);
        r.deviation = typical && r.energy !== null ? r.energy / typical - 1 : null;
      }
    }
    return { rows, slots, peak, multipleParts: parts.size > 1 };
  }

  render() {
    const model = this._data ? this._model() : null;
    return html`
      <div class="bar">
        <sf-day-nav .day=${this._day} @change=${(e) => this._setDay(e.detail.day)}></sf-day-nav>
        <div class="segmented" role="group" aria-label=${t("Farbskala")}>
          <button class=${this._relative ? "on" : ""} aria-pressed=${this._relative ? "true" : "false"}
            title=${t("Anteil am besten Modul zur selben Zeit: Wolken fallen heraus, Verschattung und schwache Module bleiben")}
            @click=${() => (this._relative = true)}>${t("relativ")}</button>
          <button class=${this._relative ? "" : "on"} aria-pressed=${this._relative ? "false" : "true"}
            @click=${() => (this._relative = false)}>${t("Watt")}</button>
        </div>
      </div>
      ${this._error ? html`<div class="alert">${t("Verlauf nicht abrufbar: {error}", { error: this._error })}</div>` : nothing}
      ${!this._data && !this._error ? html`<p class="muted">${t("Lade …")}</p>` : nothing}
      ${this._data && !model ? html`<p class="muted">${t("Für diesen Tag gibt es keine Werte.")}</p>` : nothing}
      ${model ? this._renderModel(model) : nothing}
    `;
  }

  _renderModel(model) {
    const { rows, slots } = model;
    const hovered = this._hover ? rows[this._hover.row] : null;
    const range = { start: slots[0].start, end: slots.at(-1).end };
    const point = (slot, value) => ({ start: slot.start.toISOString(), end: slot.end.toISOString(), value });
    const measured = (slot) => rows.map((r) => r.power[slot.index]).filter((p) => p !== null);
    const series = [{ name: t("Median"), color: "var(--sf-series-1)", points: slots.map((s) => point(s, measured(s).length ? median(measured(s)) : null)) }];
    if (hovered) series.push({ name: hovered.name, color: "var(--sf-series-2)", dim: false, points: slots.map((s) => point(s, hovered.power[s.index])) });
    const bands = [{
      name: t("schwächstes bis stärkstes Modul"),
      color: "var(--sf-series-1)",
      points: slots.map((s) => {
        const values = measured(s);
        return { start: s.start.toISOString(), end: s.end.toISOString(), low: values.length ? Math.min(...values) : null, high: values.length ? Math.max(...values) : null };
      }),
    }];
    const slot = slots.find((s) => s.index === this._slot) ?? slots.reduce((best, s) =>
      measured(s).reduce((a, b) => a + b, 0) > measured(best).reduce((a, b) => a + b, 0) ? s : best, slots[0]);
    return html`
      <p class="small muted">${dayFormat.format(new Date(`${this._day}T12:00:00`))} · ${t("zusammen {energy} kWh", {
        energy: kwh.format(rows.reduce((sum, r) => sum + (r.energy ?? 0), 0) / 1000),
      })}${this._data.step_minutes >= 60 ? ` · ${t("stündliche Werte (ältere 5-Minuten-Werte hat Home Assistant gelöscht)")}` : ""}</p>
      <div class="chart-box">
        ${lineChart(series, { label: t("Leistung der Module"), formatY: (v) => formatPower(v), range, bands })}
      </div>
      <p class="small muted legend">
        <span class="swatch line"></span>${t("Median")}
        <span class="swatch band"></span>${t("schwächstes bis stärkstes Modul")}
        ${hovered ? html`<span class="swatch hover"></span>${hovered.name}` : html`<span>· ${t("Zeile in der Heatmap berühren, um ein Modul hervorzuheben")}</span>`}
      </p>
      <div class="readout small" aria-live="polite">${this._readout(rows, slots)}</div>
      ${this._heatmap(model)}
      ${this._scale(model)}
      <h3>${t("Dach um {time}", { time: timeFormat.format(slot.start) })}</h3>
      <input class="slider" type="range" min="0" max=${slots.length - 1} .value=${String(slots.indexOf(slot))}
        aria-label=${t("Uhrzeit")} aria-valuetext=${timeFormat.format(slot.start)}
        @input=${(e) => (this._slot = slots[Number(e.target.value)].index)}>
      <sf-roof .optimizers=${rows.map((r) => ({ ...r, values: { power: r.power[slot.index] } }))} .visible=${["power"]}
        .breaks=${this.breaks} .colBreaks=${this.colBreaks}></sf-roof>
    `;
  }

  _readout(rows, slots) {
    if (!this._hover) return html`<span class="muted">${t("Über die Heatmap fahren für Werte; antippen setzt die Uhrzeit des Dachs.")}</span>`;
    const row = rows[this._hover.row];
    const slot = slots[this._hover.slot];
    const power = row.power[slot.index];
    const share = row.share[slot.index];
    return html`<strong>${row.name}</strong> · ${timeFormat.format(slot.start)} · ${formatPower(power)}${share !== null
      ? ` · ${t("{share} % des besten Moduls", { share: percent.format(share * 100) })}`
      : ""}`;
  }

  _heatmap({ rows, slots, peak, multipleParts }) {
    // Drawn at the real width, so the labels keep their size on phones
    const full = Math.round(Math.min(HEAT_W, this.clientWidth || HEAT_W));
    const width = full - LABEL_W - ENERGY_W;
    const cell = width / slots.length;
    const height = rows.length * ROW_H + AXIS_H;
    const perHour = (60 / this._data.step_minutes) * cell;
    const every = HOUR_STEPS.find((h) => h * perHour >= MIN_LABEL_SPACING) ?? 12;
    // Labels centred on their hour must not be cut off at the edges
    const hours = slots.filter((s, j) => s.start.getMinutes() === 0 && s.start.getHours() % every === 0 && j * cell > 16 && j * cell < width - 16);
    const fill = (r, s) => {
      if (this._relative) return r.share[s.index] === null ? "var(--sf-subtle)" : mix(r.share[s.index] ** RELATIVE_GAMMA);
      return r.power[s.index] === null ? "var(--sf-subtle)" : mix(r.power[s.index] / peak);
    };
    const locate = (event) => {
      const box = event.currentTarget.getBoundingClientRect();
      const x = ((event.clientX - box.left) / box.width) * full - LABEL_W;
      const y = ((event.clientY - box.top) / box.height) * height;
      const row = Math.floor(y / ROW_H);
      const slot = Math.floor(x / cell);
      return row >= 0 && row < rows.length && slot >= 0 && slot < slots.length ? { row, slot } : null;
    };
    return html`<svg class="heat" shape-rendering="crispEdges" viewBox="0 0 ${full} ${height}" role="img"
      aria-label=${t("Leistung je Modul über den Tag")}
      @pointermove=${(e) => (this._hover = locate(e) ?? this._hover)}
      @pointerdown=${(e) => {
        const at = locate(e);
        if (at) {
          this._hover = at;
          this._slot = slots[at.slot].index;
        }
      }}>
      ${rows.map((r, i) => svg`
        <text x=${LABEL_W - 6} y=${i * ROW_H + ROW_H - 4} text-anchor="end" class=${this._hover?.row === i ? "label on" : "label"}>${r.name}</text>
        ${slots.map((s, j) => svg`<rect x=${LABEL_W + j * cell} y=${i * ROW_H + 1} width=${cell + 0.3} height=${ROW_H - 2}
          style="fill: ${fill(r, s)}"></rect>`)}
        <text x=${full - 4} y=${i * ROW_H + ROW_H - 4} text-anchor="end" class="label">${r.energy === null ? "–" : kwh.format(r.energy / 1000)}${r.deviation === null
          ? ""
          : ` (${r.deviation >= 0 ? "+" : "−"}${percent.format(Math.abs(r.deviation * 100))} %)`}</text>
        ${multipleParts && i > 0 && rows[i - 1].part !== r.part
          ? svg`<line x1="0" x2=${full} y1=${i * ROW_H} y2=${i * ROW_H} class="part"></line>`
          : nothing}
      `)}
      ${this._hover
        ? svg`<rect x=${LABEL_W + this._hover.slot * cell} y="0" width=${Math.max(cell, 1.5)} height=${rows.length * ROW_H} class="cursor"></rect>
            <rect x=${LABEL_W} y=${this._hover.row * ROW_H} width=${width} height=${ROW_H} class="cursor"></rect>`
        : nothing}
      ${hours.map((s) => svg`<text x=${LABEL_W + slots.indexOf(s) * cell} y=${height - 4} text-anchor="middle" class="label">${timeFormat.format(s.start)}</text>`)}
    </svg>`;
  }

  _scale({ peak, multipleParts }) {
    const stops = this._relative
      ? [0, 0.25, 0.5, 0.65, 0.8, 0.9, 1].map((v) => `${mix(v ** RELATIVE_GAMMA)} ${v * 100}%`)
      : [`${mix(0)} 0%`, `${mix(1)} 100%`];
    return html`<div class="scale small muted">
      <span class="ramp-box">
        <span class="ramp" style="background: linear-gradient(to right, ${stops.join(", ")})"></span>
        ${(this._relative ? RELATIVE_TICKS : [0, 1]).map((v) => html`<span class="tick" style="left: ${v * 100}%">${this._relative
          ? `${percent.format(v * 100)} %`
          : v ? formatPower(peak) : "0 W"}</span>`)}
      </span>
      ${this._relative ? html`<span>${t("100 % = bestes Modul")}</span>` : nothing}
      <span>${t("rechts: Tagesertrag und Abweichung vom Median")}</span>
      ${this._relative && multipleParts ? html`<span>${t("Vergleich je Dachteil (durch Trenner abgesetzt)")}</span>` : nothing}
    </div>`;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 10px; width: min(900px, calc(100vw - 52px)); }
    p { margin: 0; }
    h3 { margin: 6px 0 0; font-size: 15px; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .alert { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    .bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
    .segmented { display: flex; gap: 2px; background: var(--sf-subtle); padding: 3px; border-radius: 9px; }
    .segmented button {
      min-height: 32px; padding: 0 10px; border: 0; border-radius: 7px; background: transparent;
      cursor: pointer; font: inherit; font-size: 13px; color: var(--sf-muted);
    }
    .segmented button.on { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.1); }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .legend { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
    .swatch { display: inline-block; width: 16px; height: 3px; border-radius: 2px; margin-left: 4px; }
    .swatch.line { background: var(--sf-series-1); }
    .swatch.band { height: 10px; background: color-mix(in srgb, var(--sf-series-1) 18%, transparent); }
    .swatch.hover { background: var(--sf-series-2); }
    .readout { min-height: 20px; font-variant-numeric: tabular-nums; }
    .heat { width: 100%; height: auto; display: block; touch-action: pan-y; user-select: none; cursor: crosshair; }
    .heat .label { fill: var(--sf-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
    .heat .label.on { fill: var(--sf-text); font-weight: 600; }
    .heat .part { stroke: var(--sf-border-strong); stroke-width: 2; stroke-dasharray: 6 4; }
    .heat .cursor { fill: none; stroke: var(--sf-text); stroke-width: 1; pointer-events: none; }
    .scale { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 14px; }
    .ramp-box { position: relative; width: 220px; height: 28px; margin: 0 18px 0 14px; }
    .ramp { position: absolute; inset: 0 0 auto; height: 10px; border-radius: 3px; border: 1px solid var(--sf-border); }
    .tick { position: absolute; top: 12px; transform: translateX(-50%); font-size: 11px; white-space: nowrap; font-variant-numeric: tabular-nums; }
    .slider { width: 100%; accent-color: var(--sf-series-2); }
  `;
}

customElements.define("sf-optimizer-history", SunflowOptimizerHistory);
