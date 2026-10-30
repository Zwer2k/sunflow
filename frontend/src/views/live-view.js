import { LitElement, html, svg, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { poll } from "../lib/poll.js";
import { dailyRatios, splitFlows } from "../components/flows.js";
import { formatPower, formatValue } from "../lib/format.js";

const REFRESH_MS = 5000;
const TODAY_REFRESH_MS = 5 * 60 * 1000;
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
const hourFormat = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });

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
const weekday = new Intl.DateTimeFormat("de-DE", { weekday: "short" });

// Home Assistant weather conditions: symbol and German name
const WEATHER = {
  "clear-night": ["🌙", "klar"],
  cloudy: ["☁️", "bedeckt"],
  exceptional: ["⚠️", "Unwetter"],
  fog: ["🌫️", "Nebel"],
  hail: ["🌨️", "Hagel"],
  lightning: ["🌩️", "Gewitter"],
  "lightning-rainy": ["⛈️", "Gewitter mit Regen"],
  partlycloudy: ["⛅", "teils bewölkt"],
  pouring: ["🌧️", "Starkregen"],
  rainy: ["🌦️", "Regen"],
  snowy: ["❄️", "Schnee"],
  "snowy-rainy": ["🌨️", "Schneeregen"],
  sunny: ["☀️", "sonnig"],
  windy: ["💨", "windig"],
  "windy-variant": ["💨", "windig, bewölkt"],
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
    _todayError: { state: true },
    _scroll: { state: true },
  };

  constructor() {
    super();
    this._live = null;
    this._today = null;
    this._error = null;
    try {
      this._weatherDays = localStorage.getItem(WEATHER_DAYS_KEY) === "1";
    } catch {
      this._weatherDays = false;
    }
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
      this._error = null;
    } catch (err) {
      this._error = `Live-Werte nicht verfügbar: ${err.message}`;
    }
  }

  async _loadToday() {
    try {
      // Energy counters are exact; integrated power means only stand in where counters are missing
      const energy = await api.energy("today").catch(() => null);
      const t = energy?.totals;
      if (t && t.pv !== null && t.grid_import !== null && t.grid_export !== null && energy.consumption !== null) {
        const kwh = (wh) => wh / 1000;
        this._today = {
          pv: kwh(t.pv),
          consumption: kwh(energy.consumption),
          imported: kwh(t.grid_import),
          exported: kwh(t.grid_export),
          autarky: energy.autarky,
          selfConsumption: energy.self_consumption,
          source: "Energiezähler",
        };
        this._todayError = null;
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
        source: "Leistungsmittelwerte (keine Energiezähler gefunden)",
      };
      this._todayError = null;
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
        <span class="small muted subtitle"><span>Aktuelle Energieflüsse</span>${live
          ? html`<span class="stand">Stand ${dayFormat.format(new Date(live.time))}</span>`
          : nothing}</span>
        ${live?.weather ? this._renderWeather(live.weather) : nothing}
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${live === null && !this._error ? html`<p class="muted">Lade Live-Werte …</p>` : nothing}
      ${live ? this._renderFlow(live) : nothing}
      ${this._renderToday()}
    `;
  }

  _renderWeather(weather) {
    const [symbol, name] = WEATHER[weather.condition] ?? ["", weather.condition ?? ""];
    const temp = (v) => (v === null || v === undefined ? "–" : `${Math.round(v)}${weather.temperature_unit ?? "°C"}`);
    const showDays = this._weatherDays && Boolean(weather.days?.length);
    return html`
      <div class="weather" aria-label="Wetter">
        <div class="weather-now">
          <span class="weather-symbol" aria-hidden="true">${symbol}</span>
          <div>
            <div><strong>${temp(weather.temperature)}</strong> ${name}</div>
            ${weather.cloud !== null && weather.cloud !== undefined
              ? html`<div class="small muted">${Math.round(weather.cloud)} % Bewölkung</div>`
              : nothing}
          </div>
        </div>
        ${weather.days?.length
          ? html`<div class="range" role="group" aria-label="Wettervorhersage">
              ${[[false, "Stunden"], [true, "Tage"]].map(
                ([days, label]) => html`<button class=${days === showDays ? "active" : ""} aria-pressed=${days === showDays}
                  @click=${() => this._setWeatherDays(days)}>${label}</button>`,
              )}
            </div>`
          : nothing}
        <div class="forecast">
        ${this._scroll?.left
          ? html`<button class="arrow left" aria-label="Früher" @click=${() => this._page(-1)}>‹</button>`
          : nothing}
        <ol class="weather-hours" @pointerdown=${dragScroll} @scroll=${() => this._updateScroll()}>
          ${(showDays ? weather.days : weather.hours).map((h) => {
            const [hourSymbol, hourName] = WEATHER[h.condition] ?? ["·", h.condition ?? ""];
            const when = new Date(h.time);
            return html`<li title="${hourName}${h.precipitation ? `, ${String(h.precipitation).replace(".", ",")} mm` : ""}">
              <span class="small muted">${showDays
                ? (when.toDateString() === new Date().toDateString() ? "heute" : weekday.format(when))
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
          ? html`<button class="arrow right" aria-label="Später" @click=${() => this._page(1)}>›</button>`
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

  _setWeatherDays(days) {
    this._weatherDays = days;
    try {
      localStorage.setItem(WEATHER_DAYS_KEY, days ? "1" : "0");
    } catch {
      // Not remembered, the switch still works for this visit
    }
  }

  _renderFlow(live) {
    const battery = live.battery;
    const displays = live.displays ?? {};
    const pvDisplay = live.pv.find((p) => p.display)?.display;
    const flows = splitFlows({ pv: live.pv_w, battery: battery?.power_w, grid: live.grid_w, load: live.load_w });
    const consumers = live.consumers.slice(0, MAX_CONSUMERS);
    const consumerPower = consumers.reduce((s, c) => s + Math.max(c.power_w ?? 0, 0), 0);
    const homeBase = Math.max(flows.house - consumerPower, 0);
    // Only configured components are drawn, together with the lines between them
    const hasPv = live.pv.length > 0;
    const hasBattery = Boolean(battery);
    // Consumers keep at least CONSUMER_GAP apart; without a battery row the diagram grows for them
    const spread = Math.max((consumers.length - 1) * CONSUMER_GAP, hasBattery ? 264 : 0);
    const height = Math.max(hasBattery ? 444 : 300, 90 + spread + 90);
    const consumerY = (i) =>
      consumers.length === 1 ? NODE.home[1] : 90 + (i * (spread / Math.max(consumers.length - 1, 1)));
    const width = consumers.length ? 760 : 600;
    // PV strings as a row of small nodes above the PV node, flowing into it
    const strings = live.pv.flatMap((plant) =>
      (plant.strings ?? []).map((string) => ({
        ...string,
        label: live.pv.length > 1 ? `${plant.name.split(" ")[0]} ${string.name.replace("String ", "S")}` : string.name,
      })),
    );
    const top = strings.length ? STRING_ROW : 0;
    const stringX = (i) =>
      Math.min(Math.max(NODE.pv[0] + (i - (strings.length - 1) / 2) * STRING_GAP, 40), width - 40);
    const hasGrid = live.grid_w !== null && live.grid_w !== undefined;
    const summary = [
      hasPv ? `PV ${formatPower(live.pv_w, pvDisplay)}` : null,
      battery
        ? `Batterie ${formatPower(battery.power_w, battery.display)}${battery.soc_pct === null ? "" : ` bei ${Math.round(battery.soc_pct)} %`}`
        : null,
      hasGrid ? `Netz ${formatPower(live.grid_w, displays.grid)}` : null,
      `Haus ${formatPower(flows.house, displays.load)}`,
      ...consumers.map((c) => `${c.name} ${formatPower(c.power_w, c.display)}`),
    ]
      .filter(Boolean)
      .join(", ");

    return html`
      <svg viewBox="0 0 ${width} ${height + top}" class="flow" role="img" aria-label="Energiefluss: ${summary}">
        ${strings.map((string, i) =>
          this._edge(
            `M${stringX(i)},${STRING_Y + STRING_R} C${stringX(i)},${top} ${NODE.pv[0]},${top - 10} ${NODE.pv[0]},${top + NODE.pv[1] - R}`,
            string.power_w,
            COLOR.pv,
          ),
        )}
        ${strings.map((string, i) => this._stringNode(string, stringX(i)))}
        <g transform="translate(0, ${top})">
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
          ? this._node("pv", NODE.pv, COLOR.pv, "PV", formatPower(live.pv_w, pvDisplay),
              // The string row above already shows the parts
              live.pv.length > 1 && !strings.length ? live.pv.map((p) => formatPower(p.power_w, p.display)).join(" + ") : null)
          : nothing}
        ${this._node("grid", NODE.grid, COLOR.grid, "Netz", hasGrid ? formatPower(Math.abs(live.grid_w), displays.grid) : "–",
          !hasGrid ? "keine Messung" : live.grid_w > ACTIVE_W ? "Bezug" : live.grid_w < -ACTIVE_W ? "Einspeisung" : "ausgeglichen")}
        ${battery
          ? this._batteryNode(battery)
          : nothing}
        ${this._node("home", NODE.home, COLOR.home, "Haus", formatPower(homeBase, displays.load),
          consumers.length ? `gesamt ${formatPower(flows.house, displays.load)}` : null)}
        ${consumers.map((c, i) => this._consumerNode(c, [680, consumerY(i)], COLOR.consumers[i]))}
        </g>
      </svg>
      ${battery?.parts?.length
        ? html`<p class="small muted">Speicher: ${battery.parts
            .map((b) => `${b.name} ${b.soc_pct === null ? "–" : `${Math.round(b.soc_pct)} %`} · ${formatPower(b.power_w, b.display)}`)
            .join(" · ")}</p>`
        : nothing}
      ${live.consumers.length > MAX_CONSUMERS
        ? html`<p class="small muted">Weitere Verbraucher: ${live.consumers
            .slice(MAX_CONSUMERS)
            .map((c) => `${c.name} ${formatPower(c.power_w, c.display)}`)
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

  _stringNode(string, x) {
    return svg`
      <g class="node">
        <circle cx=${x} cy=${STRING_Y} r=${STRING_R} class="disc thin" style="stroke: ${COLOR.pv}"></circle>
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
    const detail = consumer.kind === "vehicle" && consumer.connected === false ? "kein Auto" : level;
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
    const t = this._today;
    if (!t) {
      return this._todayError
        ? html`<section class="today"><h2>Heute</h2>
            <p class="small muted">Tageswerte gerade nicht abrufbar (${this._todayError}) – neuer Versuch in Kürze.</p></section>`
        : nothing;
    }
    const kwh = (v) => formatValue(v * 1000, "Wh");
    return html`
      <section class="today">
        <h2>Heute <span class="small muted source">aus ${t.source}${this._todayError
          ? html` · <span class="warn-text" title=${this._todayError}>nicht aktuell, Abruf fehlgeschlagen</span>`
          : nothing}</span></h2>
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
    /* Fills the screen: the diagram on top, today's figures at the bottom */
    :host { display: flex; flex-direction: column; gap: 16px; min-height: calc(100dvh - 110px); }
    .today { margin-top: auto; }
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
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 8px 12px; }
    .tile.highlight { background: var(--sf-primary-bg); }
    h2 .source { font-weight: 400; }
    .tile .value { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
  `;
}

customElements.define("sunflow-live", SunflowLive);
