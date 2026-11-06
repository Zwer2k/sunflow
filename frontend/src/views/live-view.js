import { LitElement, html, svg, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { poll } from "../lib/poll.js";
import { dailyRatios, splitFlows } from "../components/flows.js";
import { formatPower, formatValue } from "../lib/format.js";
import "../components/optimizer-view.js";
import "../components/quick-controls.js";
import "../components/house-scene.js";
import { recall, remember } from "../lib/view-cache.js";
import { decimal, locale, t, tc } from "../lib/i18n.js";
import { ACTIONS } from "../lib/actions.js";

const REFRESH_MS = 5000;
const TODAY_REFRESH_MS = 5 * 60 * 1000;
const KEPT_LIVE_MS = 10 * 60 * 1000;
const KEPT_TODAY_MS = 30 * 60 * 1000;
// Below this a flow is drawn as idle
const ACTIVE_W = 20;
const MAX_CONSUMERS = 4;
const CONSUMER_GAP = 88;
const DETAIL_DY = 22;

/** Largest font size (SVG units) at which text fits the circle's width at the detail line. */
function fitFont(text, r) {
  const chord = 2 * Math.sqrt(r * r - DETAIL_DY * DETAIL_DY) - 8;
  // Average glyph width is about 0.56 of the font size
  return Math.max(chord / (String(text).length * 0.56), 6).toFixed(1);
}
// Row of PV strings above the diagram
const STRING_ROW = 110;
const STRING_Y = 56;
const STRING_R = 32;
const STRING_GAP = 100;

// Identity colors (validated categorical slots); values and labels stay in text colors
const COLOR = {
  pv: "var(--sf-series-2)",
  grid: "var(--sf-series-1)",
  battery: "var(--sf-series-3)",
  home: "var(--sf-text)",
  consumers: ["var(--sf-series-5)", "var(--sf-series-6)", "var(--sf-series-4)", "var(--sf-series-neutral)"],
};

// Grid, PV/battery and house close together: a narrower diagram is taller at the same width (phones)
const GAP = 160;
const GRID_X = 90;
const MID_X = GRID_X + GAP;
const HOME_X = MID_X + GAP;
const CONSUMER_X = HOME_X + 170;
const NODE = { pv: [MID_X, 72], grid: [GRID_X, 222], battery: [MID_X, 372], home: [HOME_X, 222] };
const R = 46;
// Edges leave the PV and battery nodes bending this far towards their target
const BEND = GAP / 2;

const ICONS = {
  pv: "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10z",
  grid: "M8 22l4-20 4 20M6 8h12M5 14h14M9 14l6 8M15 14l-6 8",
  battery: "M7 4h10v18H7zM10 2h4v2M10 12h4M12 10v4",
  home: "M3 11l9-8 9 8M5 10v11h14V10M10 21v-6h4v6",
  vehicle: "M3 16v-4l2-5h14l2 5v4zM3 16v3h3v-3M18 16v3h3v-3M7 12h0M17 12h0",
  heater: "M12 3c3 4 5 6 5 10a5 5 0 0 1-10 0c0-4 2-6 5-10zM12 13v4",
  load: "M13 2L4 14h7l-1 8 9-12h-7z",
  // Heat rising: what is lost in conversion
  losses: "M8 20c-2-3 2-5 0-8s2-5 0-8M12 20c-2-3 2-5 0-8s2-5 0-8M16 20c-2-3 2-5 0-8s2-5 0-8",
};

const dayFormat = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const hourFormat = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });

/** Mouse drag scrolls the forecast sideways like a swipe does on touch screens. */
function dragScroll(event) {
  if (event.pointerType !== "mouse" || event.button !== 0) return;
  const list = event.currentTarget;
  const startX = event.clientX;
  const startScroll = list.scrollLeft;
  const move = (e) => {
    list.scrollLeft = startScroll - (e.clientX - startX);
    list.classList.add("dragging");
  };
  const up = () => {
    list.classList.remove("dragging");
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

// Remembered per browser; storage may be unavailable (private mode, previews)
const WEATHER_DAYS_KEY = "sunflow.live.weatherDays";
const FLOW_VIEW_KEY = "sunflow.live.flowView";
const FLOW_VIEWS = {
  diagram: [t("Diagramm"), "M12 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM5 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM10.5 8.5l-4 7M13.5 8.5l4 7M8 18h8"],
  bars: [t("Balken"), "M3 7h10M15 7h6M3 17h5M10 17h11"],
  tiles: [t("Kacheln"), "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"],
  house: [t("Haus"), "M3 11l9-8 9 8M5 10v11h14V10M10 21v-6h4v6"],
};
const weekday = new Intl.DateTimeFormat(locale(), { weekday: "short" });

// Home Assistant weather conditions: symbol and German name
const WEATHER = {
  "clear-night": ["🌙", t("klar")],
  cloudy: ["☁️", t("bedeckt")],
  exceptional: ["⚠️", t("Unwetter")],
  fog: ["🌫️", t("Nebel")],
  hail: ["🌨️", t("Hagel")],
  lightning: ["🌩️", t("Gewitter")],
  "lightning-rainy": ["⛈️", t("Gewitter mit Regen")],
  partlycloudy: ["⛅", t("teils bewölkt")],
  pouring: ["🌧️", t("Starkregen")],
  rainy: ["🌦️", t("Regen")],
  snowy: ["❄️", t("Schnee")],
  "snowy-rainy": ["🌨️", t("Schneeregen")],
  sunny: ["☀️", t("sonnig")],
  windy: ["💨", t("windig")],
  "windy-variant": ["💨", t("windig, bewölkt")],
};

function pct(value) {
  return value === null || value === undefined ? "–" : `${Math.round(value * 100)} %`;
}

class SunflowLive extends LitElement {
  static properties = {
    _live: { state: true },
    _today: { state: true },
    _error: { state: true },
    _weatherDays: { state: true },
    // String whose optimizers are shown: {key, name}
    _optimizers: { state: true },
    _todayError: { state: true },
    _scroll: { state: true },
    _flowView: { state: true },
  };

  constructor() {
    super();
    // The last values of this tab at once (with their time), fresh ones follow
    this._live = recall("live", KEPT_LIVE_MS);
    this._today = recall("today", KEPT_TODAY_MS);
    this._error = null;
    try {
      this._weatherDays = localStorage.getItem(WEATHER_DAYS_KEY) === "1";
    } catch {
      this._weatherDays = false;
    }
    try {
      this._flowView = localStorage.getItem(FLOW_VIEW_KEY);
    } catch {
      this._flowView = null;
    }
    if (!(this._flowView in FLOW_VIEWS)) this._flowView = "diagram";
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadLive();
    this._loadToday();
    this._stopLive = poll(() => this._loadLive(), REFRESH_MS);
    this._stopToday = poll(() => this._loadToday(), TODAY_REFRESH_MS);
    this._onResize = () => this._updateScroll();
    window.addEventListener("resize", this._onResize);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopLive();
    this._stopToday();
    window.removeEventListener("resize", this._onResize);
  }

  async _loadLive() {
    try {
      this._live = await api.live();
      remember("live", this._live);
      this._error = null;
    } catch (err) {
      this._error = t("Live-Werte nicht verfügbar: {error}", { error: err.message });
    }
  }

  async _loadToday() {
    try {
      // Energy counters are exact; integrated power means only stand in where counters are missing
      const energy = await api.energy("today").catch(() => null);
      const totals = energy?.totals;
      if (totals && totals.pv !== null && totals.grid_import !== null && totals.grid_export !== null && energy.consumption !== null) {
        const kwh = (wh) => wh / 1000;
        this._today = {
          pv: kwh(totals.pv),
          consumption: kwh(energy.consumption),
          imported: kwh(totals.grid_import),
          exported: kwh(totals.grid_export),
          autarky: energy.autarky,
          selfConsumption: energy.self_consumption,
          losses: energy.losses === null || energy.losses === undefined ? null : kwh(energy.losses),
          source: t("Energiezähler"),
        };
        this._todayError = null;
        remember("today", this._today);
        return;
      }
      const history = await api.history(24);
      const midnight = new Date();
      midnight.setHours(0, 0, 0, 0);
      const today = history.points.filter((p) => new Date(p.start) >= midnight);
      const sum = (fn) => today.reduce((total, p) => total + (fn(p) ?? 0), 0) / 4 / 1000;
      const pv = sum((p) => p.pv_w);
      const consumption = sum((p) => p.load_w);
      const imported = sum((p) => (p.grid_w === null ? 0 : Math.max(p.grid_w, 0)));
      const exported = sum((p) => (p.grid_w === null ? 0 : Math.max(-p.grid_w, 0)));
      this._today = {
        pv, consumption, imported, exported, ...dailyRatios({ pv, consumption, imported, exported }),
        source: t("Leistungsmittelwerte (keine Energiezähler gefunden)"),
      };
      this._todayError = null;
      remember("today", this._today);
    } catch (err) {
      // The last values stay (e.g. while Home Assistant's database is paused), with a hint
      this._todayError = err.message;
    }
  }

  render() {
    const live = this._live;
    return html`
      <div class="heading">
        <h1>Live</h1>
        <span class="small muted subtitle"><span>${t("Aktuelle Energieflüsse")}</span>${live
          ? html`<span class="stand">${t("Stand {time}", { time: dayFormat.format(new Date(live.time)) })}</span>`
          : nothing}</span>
        ${live?.weather && (live.display?.weather_mobile || live.display?.weather_desktop)
          ? this._renderWeather(live.weather, live.display)
          : nothing}
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${live === null && !this._error ? html`<p class="muted">${t("Lade Live-Werte …")}</p>` : nothing}
      ${live ? this._renderFlow(live) : nothing}
      <div class="dock">
        ${live ? this._renderFlowFoot(live) : nothing}
        <sf-quick-controls></sf-quick-controls>
        ${this._renderToday()}
      </div>
      ${this._optimizers
        ? html`<sf-optimizer-view .string=${this._optimizers.key} .heading=${t("Optimierer · {name}", { name: this._optimizers.name })}
            @close=${() => (this._optimizers = null)}></sf-optimizer-view>`
        : nothing}
    `;
  }

  _renderWeather(weather, display) {
    const [symbol, name] = WEATHER[weather.condition] ?? ["", weather.condition ?? ""];
    const temp = (v) => (v === null || v === undefined ? "–" : `${Math.round(v)}${weather.temperature_unit ?? "°C"}`);
    const showDays = this._weatherDays && Boolean(weather.days?.length);
    return html`
      <div class="weather ${display?.weather_mobile === false ? "hide-mobile" : ""} ${display?.weather_desktop === false
        ? "hide-desktop"
        : ""}" aria-label=${t("Wetter")}>
        <div class="weather-now">
          <span class="weather-symbol" aria-hidden="true">${symbol}</span>
          <div>
            <div><strong>${temp(weather.temperature)}</strong> ${name}</div>
            ${weather.cloud !== null && weather.cloud !== undefined
              ? html`<div class="small muted">${t("{cloud} % Bewölkung", { cloud: Math.round(weather.cloud) })}</div>`
              : nothing}
          </div>
        </div>
        ${weather.days?.length
          ? html`<div class="range" role="group" aria-label=${t("Wettervorhersage")}>
              ${[[false, t("Stunden")], [true, tc("tab", "Tage")]].map(
                ([days, label]) => html`<button class=${days === showDays ? "active" : ""} aria-pressed=${days === showDays}
                  @click=${() => this._setWeatherDays(days)}>${label}</button>`,
              )}
            </div>`
          : nothing}
        <div class="forecast">
        ${this._scroll?.left
          ? html`<button class="arrow left" aria-label=${t("Früher")} @click=${() => this._page(-1)}>‹</button>`
          : nothing}
        <ol class="weather-hours" @pointerdown=${dragScroll} @scroll=${() => this._updateScroll()}>
          ${(showDays ? weather.days : weather.hours).map((h) => {
            const [hourSymbol, hourName] = WEATHER[h.condition] ?? ["·", h.condition ?? ""];
            const when = new Date(h.time);
            return html`<li title="${hourName}${h.precipitation ? `, ${decimal(h.precipitation)} mm` : ""}">
              <span class="small muted">${showDays
                ? (when.toDateString() === new Date().toDateString() ? t("heute") : weekday.format(when))
                : hourFormat.format(when)}</span>
              <span aria-label=${hourName}>${hourSymbol}</span>
              <span class="small">${temp(h.temperature)}</span>
              ${showDays && h.templow !== null && h.templow !== undefined
                ? html`<span class="small muted">${temp(h.templow)}</span>`
                : h.cloud !== null && h.cloud !== undefined ? html`<span class="small muted">${Math.round(h.cloud)} %</span>` : nothing}
            </li>`;
          })}
        </ol>
        ${this._scroll?.right
          ? html`<button class="arrow right" aria-label=${t("Später")} @click=${() => this._page(1)}>›</button>`
          : nothing}
        </div>
      </div>
    `;
  }

  updated() {
    this._updateScroll();
  }

  /** Arrows only where more forecast is hidden. */
  _updateScroll() {
    const list = this.renderRoot.querySelector(".weather-hours");
    const left = Boolean(list) && list.scrollLeft > 2;
    const right = Boolean(list) && list.scrollLeft + list.clientWidth < list.scrollWidth - 2;
    if (left !== Boolean(this._scroll?.left) || right !== Boolean(this._scroll?.right)) this._scroll = { left, right };
  }

  _page(direction) {
    const list = this.renderRoot.querySelector(".weather-hours");
    list?.scrollBy({ left: direction * list.clientWidth * 0.8, behavior: "smooth" });
  }

  _setFlowView(view) {
    this._flowView = view;
    try {
      localStorage.setItem(FLOW_VIEW_KEY, view);
    } catch {
      // Not remembered, the switch still works for this visit
    }
  }

  _setWeatherDays(days) {
    this._weatherDays = days;
    try {
      localStorage.setItem(WEATHER_DAYS_KEY, days ? "1" : "0");
    } catch {
      // Not remembered, the switch still works for this visit
    }
  }

  /** What all views of the energy flow show. */
  _flowData(live) {
    const battery = live.battery;
    const displays = live.displays ?? {};
    const pvDisplay = live.pv.find((p) => p.display)?.display;
    const flows = splitFlows({ pv: live.pv_w, battery: battery?.power_w, grid: live.grid_w, load: live.load_w });
    const consumers = live.consumers.slice(0, MAX_CONSUMERS);
    const consumerPower = consumers.reduce((s, c) => s + Math.max(c.power_w ?? 0, 0), 0);
    const hasGrid = live.grid_w !== null && live.grid_w !== undefined;
    const summary = [
      live.pv.length ? `PV ${formatPower(live.pv_w, pvDisplay)}` : null,
      battery
        ? `${t("Batterie")} ${formatPower(battery.power_w, battery.display)}${battery.soc_pct === null ? "" : ` ${t("bei {soc} %", { soc: Math.round(battery.soc_pct) })}`}`
        : null,
      hasGrid ? `${t("Netz")} ${formatPower(live.grid_w, displays.grid)}` : null,
      `${t("Haus")} ${formatPower(flows.house, displays.load)}`,
      ...consumers.map((c) => `${c.name} ${formatPower(c.power_w, c.display)}`),
    ]
      .filter(Boolean)
      .join(", ");
    return {
      battery, displays, pvDisplay, flows, consumers, hasGrid, summary,
      homeBase: Math.max(flows.house - consumerPower, 0),
      hasPv: live.pv.length > 0,
      gridDetail: !hasGrid ? t("keine Messung") : live.grid_w > ACTIVE_W ? t("Bezug") : live.grid_w < -ACTIVE_W ? t("Einspeisung") : t("ausgeglichen"),
      batteryState: (battery?.power_w ?? 0) > ACTIVE_W ? t("lädt") : (battery?.power_w ?? 0) < -ACTIVE_W ? t("entlädt") : t("ruht"),
    };
  }

  /** Losses and the switch between diagram, bars and tiles; docked above today's figures. */
  _renderFlowFoot(live) {
    const view = this._flowView;
    return html`
      <div class="flow-foot">
        ${live.losses_w !== null && live.losses_w !== undefined
          ? html`<p class="small muted" title=${t("Bilanz minus gemessener Hausverbrauch: Wandlungsverluste und Eigenverbrauch von Wechselrichter und Speicher")}>${t("Verluste Wechselrichter/Speicher: {power} (Ø letzte Minuten)", { power: formatValue(live.losses_w, "W") })}</p>`
          : nothing}
        <div class="flow-switch range" role="group" aria-label=${t("Ansicht Energiefluss")}>
          ${Object.entries(FLOW_VIEWS).map(
            ([key, [label, icon]]) => html`<button class=${key === view ? "active" : ""} aria-pressed=${key === view}
              title=${label} aria-label=${label} @click=${() => this._setFlowView(key)}>
              <svg viewBox="0 0 24 24" class="mini-icon" aria-hidden="true"><path d=${icon}></path></svg></button>`,
          )}
        </div>
      </div>
    `;
  }

  _renderFlow(live) {
    const data = this._flowData(live);
    const view = this._flowView;
    return html`
      ${view === "bars" ? this._renderBars(live, data)
        : view === "tiles" ? this._renderTiles(live, data)
        : view === "house" ? html`<sf-house-scene .live=${live} .data=${data}></sf-house-scene>`
        : this._renderDiagram(live, data)}
      ${view !== "tiles" && live.consumers.length > MAX_CONSUMERS
        ? html`<p class="small muted">${t("Weitere Verbraucher:")} ${live.consumers
            .slice(MAX_CONSUMERS)
            .map((c) => `${c.name} ${formatPower(c.power_w, c.display)}`)
            .join(", ")}</p>`
        : nothing}
    `;
  }

  /** Where the power comes from and where it goes, as two stacked bars on one scale. */
  _renderBars(live, { battery, displays, pvDisplay, flows, consumers, homeBase, summary, batteryState }) {
    const charge = Math.max(battery?.power_w ?? 0, 0);
    const discharge = Math.max(-(battery?.power_w ?? 0), 0);
    const imported = Math.max(live.grid_w ?? 0, 0);
    const exported = Math.max(-(live.grid_w ?? 0), 0);
    const soc = battery?.soc_pct === null || battery?.soc_pct === undefined ? "" : ` ${Math.round(battery.soc_pct)} %`;
    const sources = [
      ["pv", "PV", Math.max(live.pv_w ?? 0, 0), COLOR.pv, pvDisplay],
      battery ? ["battery", `${t("Batterie")}${soc}`, discharge, COLOR.battery, battery.display] : null,
      ["grid", t("Netzbezug"), imported, COLOR.grid, displays.grid],
    ].filter(Boolean);
    const sinks = [
      ["home", t("Haus"), homeBase, COLOR.home, displays.load],
      ...consumers.map((c, i) => [c.kind in ICONS ? c.kind : "load", c.name, Math.max(c.power_w ?? 0, 0), COLOR.consumers[i], c.display]),
      battery ? ["battery", `${t("Batterie")}${soc}`, charge, COLOR.battery, battery.display] : null,
      ["grid", t("Einspeisung"), exported, COLOR.grid, displays.grid],
    ].filter(Boolean);
    const sum = (parts) => parts.reduce((s, [, , w]) => s + w, 0);
    // What comes in but is not measured going out: mostly conversion losses, partly readings taken
    // at different moments; the other way round (more out than in) it can only be the readings
    const rest = sum(sources) - sum(sinks);
    if (rest > ACTIVE_W) sinks.push(["losses", t("Verluste / Messdifferenz"), rest, "var(--sf-muted)", null, true]);
    if (-rest > ACTIVE_W) sources.push(["losses", t("Messdifferenz"), -rest, "var(--sf-muted)", null, true]);
    const total = Math.max(sum(sources), sum(sinks), ACTIVE_W);
    const restTitle = t("Bilanz minus gemessener Abgänge: Wandlungsverluste von Wechselrichter und Speicher und Zeitversatz der Messungen");
    const row = (heading, parts) => {
      const active = parts.filter(([, , w]) => w > ACTIVE_W);
      return html`<div class="bar-row">
        <div class="bar-head small muted">${heading}</div>
        <div class="bar" aria-hidden="true">
          ${active.map(([, label, w, color, , unknown]) => html`<span class="segment ${unknown ? "unknown" : ""}"
            style="flex-grow: ${w}; ${unknown ? "" : `background: ${color}`}" title="${label}: ${formatValue(w, "W")}"></span>`)}
          <span class="segment" style="flex-grow: ${Math.max(total - sum(active), 0)}"></span>
        </div>
        <ul class="bar-legend">
          ${parts.map(([icon, label, w, color, display, unknown]) => html`<li class=${w > ACTIVE_W ? "" : "idle"}
            title=${unknown ? restTitle : nothing}>
            <svg viewBox="0 0 24 24" class="mini-icon" style="stroke: ${color}" aria-hidden="true"><path d=${ICONS[icon]}></path></svg>
            <span>${label}</span> <strong>${formatPower(w, display)}</strong>
          </li>`)}
        </ul>
      </div>`;
    };
    return html`<section class="bars" role="img" aria-label=${t("Energiefluss: {summary}", { summary })}>
      ${row(t("Woher"), sources)}
      ${row(t("Wohin"), sinks)}
      ${battery ? html`<p class="small muted">${t("Batterie")}${soc} · ${batteryState}${flows.house ? html` · ${t("Haus gesamt {power}", { power: formatPower(flows.house, displays.load) })}` : nothing}</p>` : nothing}
    </section>`;
  }

  /** One small tile per component and consumer: fits beside many controls. */
  _renderTiles(live, { battery, displays, pvDisplay, flows, homeBase, hasPv, hasGrid, gridDetail, batteryState }) {
    const tile = (icon, color, label, value, detail, level = null) => html`<div class="flow-tile" style="--tile-color: ${color}">
      <div class="flow-tile-head">
        <svg viewBox="0 0 24 24" class="mini-icon" style="stroke: ${color}" aria-hidden="true"><path d=${ICONS[icon]}></path></svg>
        <span class="small">${label}</span>
      </div>
      <div class="value">${value}</div>
      ${detail ? html`<div class="small muted">${detail}</div>` : nothing}
      ${level === null ? nothing : html`<div class="level" aria-hidden="true"><span style="width: ${Math.min(Math.max(level, 0), 100)}%"></span></div>`}
    </div>`;
    const consumerColor = (i) => COLOR.consumers[Math.min(i, COLOR.consumers.length - 1)];
    return html`<section class="flow-tiles" aria-label=${t("Energiefluss")}>
      ${hasPv
        ? tile("pv", COLOR.pv, "PV", formatPower(live.pv_w, pvDisplay),
            live.pv.length > 1 ? live.pv.map((p) => formatPower(p.power_w, p.display)).join(" + ") : null)
        : nothing}
      ${battery
        ? tile("battery", COLOR.battery,
            `${battery.count > 1 ? t("{count} Speicher", { count: battery.count }) : t("Batterie")}${battery.soc_pct === null ? "" : ` ${Math.round(battery.soc_pct)} %`}`,
            formatPower(Math.abs(battery.power_w ?? 0), battery.display), batteryState, battery.soc_pct)
        : nothing}
      ${tile("grid", COLOR.grid, t("Netz"), hasGrid ? formatPower(Math.abs(live.grid_w), displays.grid) : "–", gridDetail)}
      ${tile("home", COLOR.home, t("Haus"), formatPower(homeBase, displays.load),
        live.consumers.length ? t("gesamt {power}", { power: formatPower(flows.house, displays.load) }) : null)}
      ${live.consumers.map((c, i) => {
        const level = c.level !== null && c.level !== undefined ? c.level : null;
        const levelText = level === null ? null : `${Math.round(level)}${c.level_unit === "°C" ? " °C" : " %"}`;
        const detail = c.kind === "vehicle" && c.connected === false ? t("kein Auto") : [c.vehicle, levelText].filter(Boolean).join(" ") || null;
        return tile(c.kind in ICONS ? c.kind : "load", consumerColor(i), c.name, formatPower(c.power_w ?? 0, c.display), detail,
          c.level_unit === "°C" ? null : level);
      })}
    </section>`;
  }

  _renderDiagram(live, { battery, displays, pvDisplay, flows, consumers, homeBase, hasPv, hasGrid, summary, gridDetail }) {
    const hasBattery = Boolean(battery);
    // Consumers keep at least CONSUMER_GAP apart; without a battery row the diagram grows for them
    const spread = Math.max((consumers.length - 1) * CONSUMER_GAP, hasBattery ? 264 : 0);
    const height = Math.max(hasBattery ? 444 : 300, 90 + spread + 90);
    const consumerY = (i) =>
      consumers.length === 1 ? NODE.home[1] : 90 + (i * (spread / Math.max(consumers.length - 1, 1)));
    const width = consumers.length ? CONSUMER_X + 80 : HOME_X + 100;
    // PV strings as a row of small nodes above the PV node, flowing into it
    const strings = live.pv.flatMap((plant) =>
      (plant.strings ?? []).map((string) => ({
        ...string,
        label: live.pv.length > 1 ? `${plant.name.split(" ")[0]} ${string.name.replace("String ", "S")}` : string.name,
        key: `${plant.device_id}:${string.index}`,
        title: `${plant.name} ${string.name}`,
      })),
    );
    const top = strings.length ? STRING_ROW : 0;
    const stringX = (i) =>
      Math.min(Math.max(NODE.pv[0] + (i - (strings.length - 1) / 2) * STRING_GAP, 40), width - 40);

    return html`
      <svg viewBox="0 0 ${width} ${height + top}" class="flow" role="img" aria-label=${t("Energiefluss: {summary}", { summary })}>
        ${strings.map((string, i) =>
          this._edge(
            `M${stringX(i)},${STRING_Y + STRING_R} C${stringX(i)},${top} ${NODE.pv[0]},${top - 10} ${NODE.pv[0]},${top + NODE.pv[1] - R}`,
            string.power_w,
            COLOR.pv,
          ),
        )}
        ${strings.map((string, i) => this._stringNode(string, stringX(i)))}
        <g transform="translate(0, ${top})">
        ${hasPv ? this._edge(`M${MID_X},118 C${MID_X},190 ${MID_X + BEND},222 ${HOME_X - R},222`, flows.pvToHome, COLOR.pv) : nothing}
        ${hasPv && hasGrid ? this._edge(`M${MID_X},118 C${MID_X},190 ${MID_X - BEND},222 ${GRID_X + R},222`, flows.pvToGrid, COLOR.pv) : nothing}
        ${hasPv && hasBattery ? this._edge(`M${MID_X},118 L${MID_X},326`, flows.pvToBattery, COLOR.pv) : nothing}
        ${hasGrid ? this._edge(`M${GRID_X + R},222 L${HOME_X - R},222`, flows.gridToHome, COLOR.grid) : nothing}
        ${hasGrid && hasBattery
          ? this._edge(`M${GRID_X + R},222 C${MID_X - BEND},222 ${MID_X},260 ${MID_X},326`, flows.gridToBattery, COLOR.grid)
          : nothing}
        ${hasBattery ? this._edge(`M${MID_X},326 C${MID_X},260 ${MID_X + BEND},222 ${HOME_X - R},222`, flows.batteryToHome, COLOR.battery) : nothing}
        ${hasBattery && hasGrid
          ? this._edge(`M${MID_X},326 C${MID_X},260 ${MID_X - BEND},222 ${GRID_X + R},222`, flows.batteryToGrid, COLOR.battery)
          : nothing}
        ${consumers.map((c, i) =>
          this._edge(
            `M${HOME_X + R},222 C${HOME_X + R + 64},222 ${HOME_X + R + 54},${consumerY(i)} ${CONSUMER_X - 44},${consumerY(i)}`,
            c.power_w,
            COLOR.consumers[i],
          ),
        )}

        ${hasPv
          ? this._node("pv", NODE.pv, COLOR.pv, "PV", formatPower(live.pv_w, pvDisplay),
              // The string row above already shows the parts
              live.pv.length > 1 && !strings.length ? live.pv.map((p) => formatPower(p.power_w, p.display)).join(" + ") : null)
          : nothing}
        ${this._node("grid", NODE.grid, COLOR.grid, t("Netz"), hasGrid ? formatPower(Math.abs(live.grid_w), displays.grid) : "–", gridDetail)}
        ${battery
          ? this._batteryNode(battery)
          : nothing}
        ${battery?.parts?.length ? this._batteryParts(battery.parts) : nothing}
        ${this._node("home", NODE.home, COLOR.home, t("Haus"), formatPower(homeBase, displays.load),
          consumers.length ? t("gesamt {power}", { power: formatPower(flows.house, displays.load) }) : null)}
        ${consumers.map((c, i) => this._consumerNode(c, [CONSUMER_X, consumerY(i)], COLOR.consumers[i]))}
        </g>
      </svg>
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

  _stringNode(string, x) {
    // With optimizers the string opens them as they sit on the roof
    const open = string.optimizers > 0;
    return svg`
      <g class="node ${open ? "clickable" : ""}" role=${open ? "button" : "img"} tabindex=${open ? "0" : "-1"}
        aria-label=${open ? t("{name}: {count} Optimierer anzeigen", { name: string.title, count: string.optimizers }) : string.title}
        @click=${() => open && (this._optimizers = { key: string.key, name: string.title })}
        @keydown=${(e) => open && (e.key === "Enter" || e.key === " ") && (this._optimizers = { key: string.key, name: string.title })}>
        <circle cx=${x} cy=${STRING_Y} r=${STRING_R} class="disc thin" style="stroke: ${COLOR.pv}"></circle>
        ${open ? svg`<circle cx=${x + STRING_R * 0.72} cy=${STRING_Y - STRING_R * 0.72} r="9" class="badge"></circle>
          <text x=${x + STRING_R * 0.72} y=${STRING_Y - STRING_R * 0.72 + 4} class="badge-text" text-anchor="middle">${string.optimizers}</text>` : nothing}
        <text x=${x} y=${STRING_Y + 5} class="string-value" text-anchor="middle">${formatPower(string.power_w, string.display)}</text>
        <text x=${x} y=${STRING_Y - STRING_R - 6} class="detail" text-anchor="middle">${string.label}</text>
      </g>
    `;
  }

  _node(icon, [x, y], color, label, value, detail, r = R) {
    return svg`
      <g class="node">
        <circle cx=${x} cy=${y} r=${r} class="disc" style="stroke: ${color}"></circle>
        <g transform="translate(${x - 11}, ${y - r + 10}) scale(0.92)" class="icon" style="stroke: ${color}">
          <path d=${ICONS[icon]}></path>
        </g>
        <text x=${x} y=${y + 6} class="value" text-anchor="middle">${value}</text>
        ${detail
          ? svg`<text x=${x} y=${y + DETAIL_DY} class="detail" text-anchor="middle"
              style="font-size: min(var(--detail-size), ${fitFont(detail, r)}px)">${detail}</text>`
          : nothing}
        <text x=${x} y=${y + r + 18} class="label" text-anchor="middle">${label}</text>
      </g>
    `;
  }

  _batteryNode(battery) {
    const [x, y] = NODE.battery;
    const soc = Math.min(Math.max((battery.soc_pct ?? 0) / 100, 0), 1);
    const circumference = 2 * Math.PI * (R + 6);
    const power = battery.power_w ?? 0;
    const state = power > ACTIVE_W ? t("lädt") : power < -ACTIVE_W ? t("entlädt") : t("ruht");
    return svg`
      <circle cx=${x} cy=${y} r=${R + 6} class="soc-track"></circle>
      <circle cx=${x} cy=${y} r=${R + 6} class="soc-ring" style="stroke: ${COLOR.battery}"
        stroke-dasharray="${circumference * soc} ${circumference}" transform="rotate(-90 ${x} ${y})"></circle>
      ${this._node(
        "battery",
        [x, y],
        COLOR.battery,
        `${battery.count > 1 ? t("{count} Speicher", { count: battery.count }) : t("Batterie")} ${battery.soc_pct === null ? "" : `${Math.round(battery.soc_pct)} %`}`,
        formatPower(Math.abs(power), battery.display),
        state,
      )}
    `;
  }

  _consumerNode(consumer, position, color) {
    const level =
      consumer.level !== null && consumer.level !== undefined
        ? `${Math.round(consumer.level)}${consumer.level_unit === "°C" ? " °C" : " %"}`
        : null;
    // A wallbox with assigned cars names the one it recognized
    const detail =
      consumer.kind === "vehicle" && consumer.connected === false
        ? t("kein Auto")
        : [consumer.vehicle, level].filter(Boolean).join(" ") || null;
    return this._node(
      consumer.kind in ICONS ? consumer.kind : "load",
      position,
      color,
      consumer.name,
      formatPower(consumer.power_w ?? 0, consumer.display),
      detail,
      38,
    );
  }

  _renderToday() {
    const today = this._today;
    if (!today) {
      return this._todayError
        ? html`<p class="small muted">${t("Tageswerte gerade nicht abrufbar ({error}) – neuer Versuch in Kürze.", { error: this._todayError })}</p>`
        : nothing;
    }
    const kwh = (v) => formatValue(v * 1000, "Wh");
    return html`
      <section class="today" aria-label=${t("Heute")} title=${t("Heute aus {source}", { source: today.source })}>
        ${this._todayError
          ? html`<p class="small warn-text" title=${this._todayError}>${t("Tageswerte nicht aktuell, Abruf fehlgeschlagen")}</p>`
          : nothing}
        <div class="tiles">
          <div class="tile"><div class="small muted">${t("PV erzeugt")}</div><div class="value">${kwh(today.pv)}</div></div>
          <div class="tile"><div class="small muted">${t("Verbraucht")}</div><div class="value">${kwh(today.consumption)}</div></div>
          <div class="tile"><div class="small muted">${t("Netzbezug")}</div><div class="value">${kwh(today.imported)}</div></div>
          <div class="tile"><div class="small muted">${t("Eingespeist")}</div><div class="value">${kwh(today.exported)}</div></div>
          ${today.losses !== null && today.losses !== undefined
            ? html`<div class="tile" title=${t("Bilanz minus gemessener Hausverbrauch: Wandlungsverluste und Eigenverbrauch von Wechselrichter und Speicher")}>
                <div class="small muted">${t("Verluste WR/Speicher")}</div><div class="value">${kwh(today.losses)}</div>
              </div>`
            : nothing}
          <div class="tile highlight" title=${t("Anteil des Verbrauchs ohne Netzbezug")}>
            <div class="small muted">${t("Autarkie")}</div><div class="value">${pct(today.autarky)}</div>
          </div>
          <div class="tile highlight" title=${t("Anteil der PV-Erzeugung, der selbst genutzt wird")}>
            <div class="small muted">${t("Eigenverbrauch")}</div><div class="value">${pct(today.selfConsumption)}</div>
          </div>
        </div>
      </section>
    `;
  }

  /** Each battery of the combined node as a small battery beside it: short name, level, power; the plan's action as tooltip. */
  _batteryParts(parts) {
    const [x, y] = NODE.battery;
    return parts.map((b, i) => {
      // Up to four on the right, further ones on the left of the node
      const right = i < 4;
      const row = right ? i : i - 4;
      const left = right ? x + R + 20 : x - R - 130;
      const top = y - 38 + row * 25;
      const soc = Math.min(Math.max((b.soc_pct ?? 0) / 100, 0), 1);
      const power = b.power_w ?? 0;
      const arrow = power > ACTIVE_W ? "↑" : power < -ACTIVE_W ? "↓" : "";
      const action = ACTIONS[b.action];
      return svg`<g class="part" role="img" aria-label=${t("{name}: {soc}, {power}", {
        name: b.name, soc: b.soc_pct === null ? "–" : `${Math.round(b.soc_pct)} %`, power: formatPower(power, b.display) })}>
        <title>${b.name}${action ? ` – ${action.label}` : ""}</title>
        <rect x=${left + 3} y=${top - 1} width="4" height="2" class="part-cap"></rect>
        <rect x=${left} y=${top + 1} width="10" height="18" rx="2" class="part-shell" style="stroke: ${COLOR.battery}"></rect>
        <rect x=${left + 2} y=${top + 3 + 14 * (1 - soc)} width="6" height=${14 * soc} style="fill: ${COLOR.battery}"></rect>
        <text x=${left + 16} y=${top + 9} class="part-label">${b.short_name} ${b.soc_pct === null ? "–" : `${Math.round(b.soc_pct)} %`}</text>
        <text x=${left + 16} y=${top + 21} class="part-power">${arrow}${formatPower(Math.abs(power), b.display)}</text>
      </g>`;
    });
  }

  static styles = css`
    .part-shell { fill: none; stroke-width: 1.5; }
    .part-cap { fill: var(--sf-muted); }
    .part-label { font-size: 11px; font-weight: 600; fill: var(--sf-text); }
    .part-power { font-size: 10px; fill: var(--sf-muted); font-variant-numeric: tabular-nums; }
    /* Fills the screen: the diagram on top, today's figures at the bottom */
    :host { display: flex; flex-direction: column; gap: 16px; min-height: calc(100dvh - 110px); }
    /* Losses, view switch, quick controls and today's figures sit together at the bottom */
    .dock { margin-top: auto; display: flex; flex-direction: column; gap: 8px; }
    @media (max-width: 700px) {
      /* Below the content sits the fixed menu bar */
      :host { min-height: calc(100dvh - 96px - env(safe-area-inset-bottom)); }
    }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0 0 8px; font-size: 16px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .warn-text { color: var(--sf-warn-text); }
    .small { font-size: 13px; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    .heading { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; }
    /* Weather on the right of the title line; on narrow screens it wraps below */
    /* Breaks only between its two parts: at most two lines next to the weather */
    /* Gap only between parts on the same line; a wrapped line starts flush left */
    .subtitle { flex: 0 1 auto; display: flex; flex-wrap: wrap; column-gap: 6px; }
    .subtitle > span { white-space: nowrap; }
    .subtitle .stand { opacity: 0.7; }
    .heading .weather { margin-left: auto; min-width: 0; max-width: 100%; }
    /* Wide screens: the forecast shrinks (scrolls) instead of pushing the weather below the title */
    @media (min-width: 601px) {
      .heading { flex-wrap: nowrap; }
      .heading .weather { flex: 0 1 auto; flex-wrap: nowrap; justify-content: flex-end; }
    }
    /* Header, title and today's tiles stay visible below the diagram without scrolling */
    .flow { width: 100%; max-width: 900px; height: auto; max-height: max(260px, calc(100dvh - 340px)); display: block; margin: 0 auto; }
    .edge { fill: none; stroke-linecap: round; }
    .edge.idle { stroke: var(--sf-border); stroke-width: 1.5; }
    .edge.base { opacity: 0.25; }
    .edge.flow { stroke-dasharray: 2 14; animation: flow linear infinite; }
    @keyframes flow { to { stroke-dashoffset: -32; } }
    @media (prefers-reduced-motion: reduce) { .edge.flow { animation: none; stroke-dasharray: none; opacity: 0.8; } }
    .disc { fill: var(--sf-surface); stroke-width: 3; }
    .disc.thin { stroke-width: 2; }
    .node.clickable { cursor: pointer; }
    .node.clickable:hover .disc, .node.clickable:focus-visible .disc { stroke-width: 4; }
    .node.clickable:focus { outline: none; }
    .badge { fill: var(--sf-series-2); }
    .badge-text { fill: var(--sf-surface); font-size: 11px; font-weight: 700; }
    .string-value { fill: var(--sf-text); font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .icon path { fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .value { fill: var(--sf-text); font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
    :host { --detail-size: 11px; }
    .detail { fill: var(--sf-muted); font-size: var(--detail-size); }
    .label { fill: var(--sf-text); font-size: 13px; font-weight: 500; }
    /* The diagram scales down to about half on phones; larger text keeps it readable */
    @media (max-width: 600px) {
      .string-value { font-size: 18px; }
      .value { font-size: 26px; }
      :host { --detail-size: 17px; }
      .label { font-size: 20px; }
      .flow { max-height: none; }
    }
    .soc-track { fill: none; stroke: var(--sf-border); stroke-width: 4; }
    .weather { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; font-size: 13px; }
    /* Hidden per screen size in the settings; same breakpoint as the layout above */
    @media (max-width: 600px) { .weather.hide-mobile { display: none; } }
    @media (min-width: 601px) { .heading .weather.hide-desktop { display: none; } }
    .weather-now { display: flex; align-items: center; gap: 6px; flex: none; white-space: nowrap; }
    .weather-symbol { font-size: 24px; line-height: 1; }
    .range { display: flex; flex: none; gap: 2px; background: var(--sf-subtle); padding: 2px; border-radius: 8px; }
    .range button {
      min-height: 28px; padding: 0 8px; border: 0; border-radius: 6px; background: transparent; cursor: pointer;
      font-size: 12px; color: var(--sf-muted);
    }
    .range button.active { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; }
    /* Next hours scroll sideways on narrow screens instead of wrapping */
    .weather-hours {
      display: flex; gap: 3px; margin: 0; padding: 0; list-style: none; flex: 0 1 auto; min-width: 0;
      /* Swipe (touch) or drag (mouse) through all forecast hours or days */
      overflow-x: auto; overscroll-behavior-x: contain; scroll-snap-type: x proximity; scrollbar-width: none;
      cursor: grab; user-select: none; touch-action: pan-x pan-y;
    }
    .weather-hours::-webkit-scrollbar { display: none; }
    .forecast { position: relative; display: flex; min-width: 0; flex: 0 1 auto; max-width: min(100%, 420px); }
    .forecast .weather-hours { max-width: 100%; }
    .arrow {
      position: absolute; top: 50%; transform: translateY(-50%); z-index: 1; width: 24px; height: 24px; padding: 0;
      border: 1px solid var(--sf-border); border-radius: 50%; background: var(--sf-surface); color: var(--sf-text);
      font-size: 16px; line-height: 1; cursor: pointer; box-shadow: 0 1px 3px rgb(0 0 0 / 0.15);
    }
    .arrow.left { left: -6px; }
    .arrow.right { right: -6px; }
    .weather-hours.dragging { cursor: grabbing; scroll-snap-type: none; }
    .weather-hours li {
      scroll-snap-align: start; flex: none; display: flex; flex-direction: column; align-items: center; gap: 0; min-width: 42px;
      padding: 2px 4px; border-radius: 6px; background: var(--sf-subtle); font-size: 12px; line-height: 1.3;
    }
    .weather-hours li .small { font-size: 11px; }
    .soc-ring { fill: none; stroke-width: 4; stroke-linecap: round; }
    .flow-foot { display: flex; align-items: center; gap: 8px; }
    .flow-switch { margin-left: auto; flex: none; }
    .flow-switch button { display: flex; align-items: center; min-width: 32px; justify-content: center; }
    .bars { display: flex; flex-direction: column; gap: 12px; }
    .bar-row { display: grid; grid-template-columns: 52px 1fr; align-items: center; gap: 4px 10px; }
    .bar { display: flex; gap: 2px; height: 16px; }
    .segment { min-width: 0; border-radius: 4px; }
    .segment.unknown {
      background: repeating-linear-gradient(135deg, var(--sf-muted) 0 3px, transparent 3px 6px);
      box-shadow: inset 0 0 0 1px var(--sf-muted);
    }
    .bar-legend {
      grid-column: 2; display: flex; flex-wrap: wrap; gap: 2px 14px; margin: 0; padding: 0; list-style: none; font-size: 13px;
    }
    .bar-legend li { display: flex; align-items: center; gap: 4px; white-space: nowrap; }
    .bar-legend li.idle { color: var(--sf-muted); }
    .bar-legend strong { font-weight: 600; font-variant-numeric: tabular-nums; }
    .mini-icon { width: 16px; height: 16px; flex: none; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .flow-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 8px; }
    .flow-tile {
      display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border-radius: 10px; min-width: 0;
      background: var(--sf-surface); border: 1px solid var(--sf-border); border-top: 3px solid var(--tile-color);
    }
    .flow-tile-head { display: flex; align-items: center; gap: 5px; min-width: 0; }
    .flow-tile-head span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .flow-tile .value { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .flow-tile .small { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .level { height: 4px; border-radius: 2px; background: var(--sf-subtle); margin-top: 3px; overflow: hidden; }
    .level span { display: block; height: 100%; background: var(--tile-color); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 8px 12px; }
    .tile.highlight { background: var(--sf-primary-bg); }
    h2 .source { font-weight: 400; }
    .tile .value { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
  `;
}

customElements.define("sunflow-live", SunflowLive);
