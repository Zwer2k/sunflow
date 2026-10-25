import { LitElement, html, svg, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { dailyRatios, splitFlows } from "./flows.js";
import { formatValue } from "./format.js";

const REFRESH_MS = 5000;
const TODAY_REFRESH_MS = 5 * 60 * 1000;
// Below this a flow is drawn as idle
const ACTIVE_W = 20;
const MAX_CONSUMERS = 4;

// Identity colors (validated categorical slots); values and labels stay in text colors
const COLOR = {
  pv: "var(--sf-series-2)",
  grid: "var(--sf-series-1)",
  battery: "var(--sf-series-3)",
  home: "var(--sf-text)",
  consumers: ["var(--sf-series-5)", "var(--sf-series-6)", "var(--sf-series-4)", "var(--sf-series-neutral)"],
};

const NODE = { pv: [300, 72], grid: [100, 222], battery: [300, 372], home: [500, 222] };
const R = 46;

const ICONS = {
  pv: "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10z",
  grid: "M8 22l4-20 4 20M6 8h12M5 14h14M9 14l6 8M15 14l-6 8",
  battery: "M7 4h10v18H7zM10 2h4v2M10 12h4M12 10v4",
  home: "M3 11l9-8 9 8M5 10v11h14V10M10 21v-6h4v6",
  vehicle: "M3 16v-4l2-5h14l2 5v4zM3 16v3h3v-3M18 16v3h3v-3M7 12h0M17 12h0",
  heater: "M12 3c3 4 5 6 5 10a5 5 0 0 1-10 0c0-4 2-6 5-10zM12 13v4",
  load: "M13 2L4 14h7l-1 8 9-12h-7z",
};

const dayFormat = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function pct(value) {
  return value === null || value === undefined ? "–" : `${Math.round(value * 100)} %`;
}

class SunflowLive extends LitElement {
  static properties = {
    _live: { state: true },
    _today: { state: true },
    _error: { state: true },
  };

  constructor() {
    super();
    this._live = null;
    this._today = null;
    this._error = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadLive();
    this._loadToday();
    this._liveTimer = setInterval(() => this._loadLive(), REFRESH_MS);
    this._todayTimer = setInterval(() => this._loadToday(), TODAY_REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._liveTimer);
    clearInterval(this._todayTimer);
  }

  async _loadLive() {
    try {
      this._live = await api.live();
      this._error = null;
    } catch (err) {
      this._error = `Live-Werte nicht verfügbar: ${err.message}`;
    }
  }

  async _loadToday() {
    try {
      const history = await api.history(24);
      const midnight = new Date();
      midnight.setHours(0, 0, 0, 0);
      const today = history.points.filter((p) => new Date(p.start) >= midnight);
      const sum = (fn) => today.reduce((total, p) => total + (fn(p) ?? 0), 0) / 4 / 1000;
      const pv = sum((p) => p.pv_w);
      const consumption = sum((p) => p.load_w);
      const imported = sum((p) => (p.grid_w === null ? 0 : Math.max(p.grid_w, 0)));
      const exported = sum((p) => (p.grid_w === null ? 0 : Math.max(-p.grid_w, 0)));
      this._today = { pv, consumption, imported, exported, ...dailyRatios({ pv, consumption, imported, exported }) };
    } catch {
      // The daily summary is optional; the live diagram still works
      this._today = null;
    }
  }

  render() {
    const live = this._live;
    return html`
      <div class="heading">
        <h1>Live</h1>
        <span class="small muted">Aktuelle Energieflüsse${live ? html` · Stand ${dayFormat.format(new Date(live.time))}` : nothing}</span>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${live === null && !this._error ? html`<p class="muted">Lade Live-Werte …</p>` : nothing}
      ${live ? this._renderFlow(live) : nothing}
      ${this._renderToday()}
    `;
  }

  _renderFlow(live) {
    const battery = live.battery;
    const flows = splitFlows({ pv: live.pv_w, battery: battery?.power_w, grid: live.grid_w, load: live.load_w });
    const consumers = live.consumers.slice(0, MAX_CONSUMERS);
    const consumerPower = consumers.reduce((s, c) => s + Math.max(c.power_w ?? 0, 0), 0);
    const homeBase = Math.max(flows.house - consumerPower, 0);
    const consumerY = (i) =>
      consumers.length === 1 ? NODE.home[1] : 90 + (i * (264 / Math.max(consumers.length - 1, 1)));
    const width = consumers.length ? 760 : 600;
    // Only configured components are drawn, together with the lines between them
    const hasPv = live.pv.length > 0;
    const hasBattery = Boolean(battery);
    const hasGrid = live.grid_w !== null && live.grid_w !== undefined;
    const summary = [
      hasPv ? `PV ${formatValue(live.pv_w, "W")}` : null,
      battery ? `Batterie ${formatValue(battery.power_w, "W")} bei ${Math.round(battery.soc_pct ?? 0)} %` : null,
      hasGrid ? `Netz ${formatValue(live.grid_w, "W")}` : null,
      `Haus ${formatValue(flows.house, "W")}`,
      ...consumers.map((c) => `${c.name} ${formatValue(c.power_w, "W")}`),
    ]
      .filter(Boolean)
      .join(", ");

    return html`
      <svg viewBox="0 0 ${width} ${hasBattery ? 444 : 300}" class="flow" role="img" aria-label="Energiefluss: ${summary}">
        ${hasPv ? this._edge("M300,118 C300,190 400,222 454,222", flows.pvToHome, COLOR.pv) : nothing}
        ${hasPv && hasGrid ? this._edge("M300,118 C300,190 200,222 146,222", flows.pvToGrid, COLOR.pv) : nothing}
        ${hasPv && hasBattery ? this._edge("M300,118 L300,326", flows.pvToBattery, COLOR.pv) : nothing}
        ${hasGrid ? this._edge("M146,222 L454,222", flows.gridToHome, COLOR.grid) : nothing}
        ${hasGrid && hasBattery ? this._edge("M146,222 C200,222 300,260 300,326", flows.gridToBattery, COLOR.grid) : nothing}
        ${hasBattery ? this._edge("M300,326 C300,260 400,222 454,222", flows.batteryToHome, COLOR.battery) : nothing}
        ${hasBattery && hasGrid ? this._edge("M300,326 C300,260 200,222 146,222", flows.batteryToGrid, COLOR.battery) : nothing}
        ${consumers.map((c, i) =>
          this._edge(`M546,222 C610,222 600,${consumerY(i)} 636,${consumerY(i)}`, c.power_w, COLOR.consumers[i]),
        )}

        ${hasPv
          ? this._node("pv", NODE.pv, COLOR.pv, "PV", formatValue(live.pv_w, "W"),
              live.pv.length > 1 ? live.pv.map((p) => formatValue(p.power_w, "W")).join(" + ") : null)
          : nothing}
        ${this._node("grid", NODE.grid, COLOR.grid, "Netz", hasGrid ? formatValue(Math.abs(live.grid_w), "W") : "–",
          !hasGrid ? "keine Messung" : live.grid_w > ACTIVE_W ? "Bezug" : live.grid_w < -ACTIVE_W ? "Einspeisung" : "ausgeglichen")}
        ${battery
          ? this._batteryNode(battery)
          : nothing}
        ${this._node("home", NODE.home, COLOR.home, "Haus", formatValue(homeBase, "W"),
          consumers.length ? `gesamt ${formatValue(flows.house, "W")}` : null)}
        ${consumers.map((c, i) => this._consumerNode(c, [680, consumerY(i)], COLOR.consumers[i]))}
      </svg>
      ${battery?.parts?.length
        ? html`<p class="small muted">Speicher: ${battery.parts
            .map((b) => `${b.name} ${Math.round(b.soc_pct)} % · ${formatValue(b.power_w ?? 0, "W")}`)
            .join(" · ")}</p>`
        : nothing}
      ${live.consumers.length > MAX_CONSUMERS
        ? html`<p class="small muted">Weitere Verbraucher: ${live.consumers
            .slice(MAX_CONSUMERS)
            .map((c) => `${c.name} ${formatValue(c.power_w, "W")}`)
            .join(", ")}</p>`
        : nothing}
    `;
  }

  _edge(path, power, color) {
    const active = (power ?? 0) > ACTIVE_W;
    if (!active) return svg`<path d=${path} class="edge idle"></path>`;
    const width = 1.5 + 6 * Math.min(power / 8000, 1);
    // More power, faster flow
    const duration = Math.max(0.5, 2 - power / 4000);
    return svg`
      <path d=${path} class="edge base" style="stroke: ${color}; stroke-width: ${width}"></path>
      <path d=${path} class="edge flow" style="stroke: ${color}; stroke-width: ${width}; animation-duration: ${duration}s">
        <title>${formatValue(power, "W")}</title>
      </path>
    `;
  }

  _node(icon, [x, y], color, label, value, detail, r = R) {
    return svg`
      <g class="node">
        <circle cx=${x} cy=${y} r=${r} class="disc" style="stroke: ${color}"></circle>
        <g transform="translate(${x - 11}, ${y - r + 10}) scale(0.92)" class="icon" style="stroke: ${color}">
          <path d=${ICONS[icon]}></path>
        </g>
        <text x=${x} y=${y + 8} class="value" text-anchor="middle">${value}</text>
        ${detail ? svg`<text x=${x} y=${y + 26} class="detail" text-anchor="middle">${detail}</text>` : nothing}
        <text x=${x} y=${y + r + 18} class="label" text-anchor="middle">${label}</text>
      </g>
    `;
  }

  _batteryNode(battery) {
    const [x, y] = NODE.battery;
    const soc = Math.min(Math.max((battery.soc_pct ?? 0) / 100, 0), 1);
    const circumference = 2 * Math.PI * (R + 6);
    const power = battery.power_w ?? 0;
    const state = power > ACTIVE_W ? "lädt" : power < -ACTIVE_W ? "entlädt" : "ruht";
    return svg`
      <circle cx=${x} cy=${y} r=${R + 6} class="soc-track"></circle>
      <circle cx=${x} cy=${y} r=${R + 6} class="soc-ring" style="stroke: ${COLOR.battery}"
        stroke-dasharray="${circumference * soc} ${circumference}" transform="rotate(-90 ${x} ${y})"></circle>
      ${this._node(
        "battery",
        [x, y],
        COLOR.battery,
        `${battery.count > 1 ? `${battery.count} Speicher` : "Batterie"} ${battery.soc_pct === null ? "" : `${Math.round(battery.soc_pct)} %`}`,
        formatValue(Math.abs(power), "W"),
        state,
      )}
    `;
  }

  _consumerNode(consumer, position, color) {
    const level =
      consumer.level !== null && consumer.level !== undefined
        ? `${Math.round(consumer.level)}${consumer.level_unit === "°C" ? " °C" : " %"}`
        : null;
    const detail = consumer.kind === "vehicle" && consumer.connected === false ? "nicht angesteckt" : level;
    return this._node(
      consumer.kind in ICONS ? consumer.kind : "load",
      position,
      color,
      consumer.name,
      formatValue(consumer.power_w ?? 0, "W"),
      detail,
      38,
    );
  }

  _renderToday() {
    const t = this._today;
    if (!t) return nothing;
    const kwh = (v) => formatValue(v * 1000, "Wh");
    return html`
      <section>
        <h2>Heute</h2>
        <div class="tiles">
          <div class="tile"><div class="small muted">PV erzeugt</div><div class="value">${kwh(t.pv)}</div></div>
          <div class="tile"><div class="small muted">Verbraucht</div><div class="value">${kwh(t.consumption)}</div></div>
          <div class="tile"><div class="small muted">Netzbezug</div><div class="value">${kwh(t.imported)}</div></div>
          <div class="tile"><div class="small muted">Eingespeist</div><div class="value">${kwh(t.exported)}</div></div>
          <div class="tile highlight" title="Anteil des Verbrauchs ohne Netzbezug">
            <div class="small muted">Autarkie</div><div class="value">${pct(t.autarky)}</div>
          </div>
          <div class="tile highlight" title="Anteil der PV-Erzeugung, der selbst genutzt wird">
            <div class="small muted">Eigenverbrauch</div><div class="value">${pct(t.selfConsumption)}</div>
          </div>
        </div>
      </section>
    `;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 16px; }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0 0 8px; font-size: 16px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    .heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; }
    /* Header, title and today's tiles stay visible below the diagram without scrolling */
    .flow { width: 100%; max-width: 900px; height: auto; max-height: max(260px, calc(100dvh - 330px)); display: block; margin: 0 auto; }
    .edge { fill: none; stroke-linecap: round; }
    .edge.idle { stroke: var(--sf-border); stroke-width: 1.5; }
    .edge.base { opacity: 0.25; }
    .edge.flow { stroke-dasharray: 2 14; animation: flow linear infinite; }
    @keyframes flow { to { stroke-dashoffset: -32; } }
    @media (prefers-reduced-motion: reduce) { .edge.flow { animation: none; stroke-dasharray: none; opacity: 0.8; } }
    .disc { fill: var(--sf-surface); stroke-width: 3; }
    .icon path { fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .value { fill: var(--sf-text); font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .detail { fill: var(--sf-muted); font-size: 11px; }
    .label { fill: var(--sf-text); font-size: 13px; font-weight: 500; }
    .soc-track { fill: none; stroke: var(--sf-border); stroke-width: 4; }
    .soc-ring { fill: none; stroke-width: 4; stroke-linecap: round; }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 8px 12px; }
    .tile.highlight { background: var(--sf-primary-bg); }
    .tile .value { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
  `;
}

customElements.define("sunflow-live", SunflowLive);
