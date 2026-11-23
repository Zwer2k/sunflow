import { svg, css, nothing } from "../../vendor/lit-all.min.js";
import { decimal, locale, t, tc } from "../lib/i18n.js";

/**
 * Diagrams of the heat pump view: the control chain (how the set value comes about), the plant (where the
 * heat flows), the history of the last hours and the heating curve. Plain SVG; colours through CSS
 * variables in style attributes (SVG presentation attributes do not resolve var()).
 */

const one = (v) => (v == null || !Number.isFinite(Number(v)) ? "–" : decimal(Number(v).toFixed(1)));

// ---------- Control chain ----------

// Position and width of each block in the chain (viewBox 1000 × 358)
const CHAIN_LAYOUT = {
  ist: [20, 20, 150], soll: [20, 125, 150], heat: [20, 230, 150],
  corr: [230, 60, 170], ff: [230, 220, 170],
  target: [470, 140, 150], limits: [650, 140, 130], guard: [810, 140, 170],
  writer: [810, 250, 170], hp: [590, 250, 190],
};
const BLOCK_H = 74;

/**
 * The control chain: room temperature and room setpoint into the correction, room setpoint and outside into
 * the heating curve, both into the flow target, then limits, protection, writing and the heat pump. blocks:
 * per id { title, value, sub, state } (state "frozen", "acting", "warn" or null); a click selects a block.
 * formula: lines of the heating curve with the current numbers, shown above the flow target.
 */
export function chainDiagram(blocks, { selected = null, onSelect = () => {}, live = false, corrFrozen = false, formula = [] } = {}) {
  const wire = (d, { frozen = false, on = false } = {}) => svg`<path class="wire ${frozen ? "frozen" : ""} ${on ? "live" : ""}" d=${d}
    marker-end=${on ? "url(#hp-arrow-on)" : "url(#hp-arrow)"}></path>`;
  const block = (id) => {
    const b = blocks[id];
    if (!b) return nothing;
    const [x, y, w] = CHAIN_LAYOUT[id];
    return svg`<g class="blk ${selected === id ? "sel" : ""} ${b.state ?? ""}" tabindex="0" role="button"
        aria-label=${`${b.title}: ${b.value}`} @click=${() => onSelect(id)}
        @keydown=${(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect(id))}>
      <rect x=${x} y=${y} width=${w} height=${BLOCK_H} rx="10"></rect>
      <text x=${x + 12} y=${y + 20} class="t-muted" font-size="12">${b.title}</text>
      <text x=${x + 12} y=${y + 46} font-size="21" font-weight="700">${b.value}</text>
      <text x=${x + 12} y=${y + 65} class="t-muted" font-size="11">${clip(b.sub ?? "", w)}</text>
    </g>`;
  };
  return svg`<svg class="diagram-svg chain" viewBox="0 0 1000 358" role="img" aria-label=${t("Regelkette")}>
    <defs>
      <marker id="hp-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" style="fill: var(--sf-muted)"></path></marker>
      <marker id="hp-arrow-on" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" style="fill: var(--sf-primary)"></path></marker>
    </defs>
    ${wire("M170,57 H215 V82 H230", { frozen: corrFrozen })}
    <text x="219" y="76" font-size="13" font-weight="700" class="t-muted">−</text>
    ${wire("M170,162 H200 V112 H230", { frozen: corrFrozen })}
    ${wire("M200,162 V245 H230")}
    <circle cx="200" cy="162" r="3" style="fill: var(--sf-muted)"></circle>
    ${wire("M170,267 H230")}
    <circle cx="430" cy="177" r="14" class="sum"></circle>
    <text x="430" y="183" font-size="18" font-weight="700" text-anchor="middle">+</text>
    ${wire("M400,97 H430 V163", { frozen: corrFrozen })}
    ${wire("M400,257 H430 V191")}
    ${wire("M444,177 H470")}
    ${wire("M620,177 H650")}
    ${wire("M780,177 H810")}
    ${wire("M895,214 V250")}
    ${wire("M810,287 H780", { frozen: !live, on: live })}
    <path class="wire feedback" d="M590,300 H560 Q540,300 540,320 V330 H95 V304" marker-end="url(#hp-arrow)"></path>
    <text x="110" y="326" font-size="11" class="t-muted">${t("Rückwirkung: Vorlauf → Räume → Raum-Ist (Stunden)")}</text>
    ${Object.keys(CHAIN_LAYOUT).map(block)}
    <g class="key">
      <rect x="640" y="340" width="22" height="12" rx="3" class="key-frozen"></rect>
      <text x="668" y="350" font-size="11" class="t-muted">${t("angehalten")}</text>
      <rect x="790" y="340" width="22" height="12" rx="3" class="key-acting"></rect>
      <text x="818" y="350" font-size="11" class="t-muted">${t("greift gerade ein")}</text>
    </g>
    ${formula.length
      ? svg`<g class="formula">
          <rect x="440" y="14" width="540" height=${16 + formula.length * 22} rx="8"></rect>
          ${formula.map((line, i) => svg`<text x="454" y=${35 + i * 22} font-size=${i === 0 ? 15 : i === formula.length - 1 ? 11 : 13.5}
            class=${i === formula.length - 1 ? "t-muted" : ""} font-weight=${i === 0 ? 700 : 400}>${line}</text>`)}
        </g>`
      : nothing}
  </svg>`;
}

/** Shorten a sub line to the block width (about 6 px per character at 11 px). */
function clip(text, width) {
  const max = Math.floor((width - 18) / 5.4);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

// ---------- Plant ----------

/**
 * The plant: outdoor unit, indoor unit with flow and return, three-way valve to the hot water tank or the
 * heating, and the house with its rooms coloured by their difference to the setpoint. p: { outside,
 * compressor, heatKw, flow, ret, maxSetpoint, bypass, activity, rooms: [{ name, value, fast }], room,
 * setpoint, blocked }.
 */
export function plantDiagram(p) {
  const running = p.compressor === true;
  const hotWater = p.activity === "hot_water";
  const defrost = p.activity === "defrost";
  const heating = running && !hotWater && !defrost && !p.blocked;
  const rooms = p.rooms ?? [];
  const perRow = 3;
  const rows = Math.max(1, Math.ceil(rooms.length / perRow));
  const houseH = 40 + rows * 92;
  const height = Math.max(300, houseH + 60);
  const pipe = (d, kind, on, reverse = false) => svg`<path class="pipe ${kind} ${on ? "" : "off"}" d=${d}></path>${on
    ? svg`<path class="pipe-flow ${reverse ? "rev" : ""}" d=${d}></path>` : nothing}`;
  const roomBox = (r, i) => {
    const x = 640 + (i % perRow) * 112;
    const y = 60 + Math.floor(i / perRow) * 92;
    const diff = r.value == null || p.setpoint == null ? null : r.value - p.setpoint;
    const colour = diff == null ? "var(--sf-muted)" : Math.abs(diff) < 0.5 ? "var(--sf-ok-text)" : diff > 0 ? "var(--hp-hot)" : "var(--hp-cold)";
    return svg`<g>
      <rect x=${x} y=${y} width="104" height="80" rx="6" class="room ${r.fast ? "fast" : ""}"></rect>
      <text x=${x + 8} y=${y + 18} font-size="12" class="t-muted">${clip(r.name, 112)}</text>
      <text x=${x + 8} y=${y + 46} font-size="20" font-weight="700" style=${`fill: ${colour}`}>${one(r.value)}°</text>
      <text x=${x + 8} y=${y + 66} font-size="11" class="t-muted">${r.fast
        ? t("zählt gerade nicht")
        : diff == null ? "" : `${diff >= 0 ? "+" : "−"}${decimal(Math.abs(diff).toFixed(1))} K`}</text>
    </g>`;
  };
  return svg`<svg class="diagram-svg plant" viewBox=${`0 0 1000 ${height}`} role="img" aria-label=${t("Anlage")}>
    <rect x="20" y="70" width="130" height="130" rx="10" class="unit"></rect>
    <circle cx="85" cy="125" r="34" class="fan-ring"></circle>
    <g class="fan ${running ? "spin" : ""}">
      <path d="M85,95 Q95,120 85,125 Q75,120 85,95 M115,125 Q90,135 85,125 Q90,115 115,125 M85,155 Q75,130 85,125 Q95,130 85,155 M55,125 Q80,115 85,125 Q80,135 55,125"></path>
    </g>
    <text x="20" y="58" font-size="12" class="t-muted">${t("Außengerät · außen {temp} °C", { temp: one(p.outside) })}</text>
    <text x="30" y="188" font-size="12">${p.compressor == null ? t("Kompressor ?") : running ? t("Kompressor an") : t("Kompressor aus")}</text>
    <text x="20" y="222" font-size="12" class="t-muted">${defrost ? t("Abtauen") : p.heatKw != null ? t("{kw} kW Wärme", { kw: decimal(p.heatKw.toFixed(2)) }) : ""}</text>
    <rect x="220" y="40" width="150" height="220" rx="10" class="unit"></rect>
    <text x="232" y="62" font-size="12" class="t-muted">${t("Innengerät")}</text>
    <text x="232" y="90" font-size="12">${t("Vorlauf")}</text>
    <text x="232" y="112" font-size="20" font-weight="700" style="fill: var(--hp-hot)">${one(p.flow)}°</text>
    <text x="232" y="140" font-size="12">${t("Rücklauf")}</text>
    <text x="232" y="162" font-size="20" font-weight="700" style="fill: var(--hp-cold)">${one(p.ret)}°</text>
    <text x="232" y="196" font-size="11" class="t-muted">${t("Max T-Vorlauf")}</text>
    <text x="232" y="214" font-size="13" font-weight="600">${one(p.maxSetpoint)} °C</text>
    ${pipe("M150,110 H220", "vl", running)}${pipe("M220,160 H150", "rl", running)}
    ${pipe("M370,100 H400", "vl", running && !defrost)}
    <path d="M400,100 l16,-10 v20 z M432,100 l-16,-10 v20 z" class="valve"></path>
    <text x="424" y="132" font-size="11" class="t-muted">${p.bypass == null ? "" : t("Bypass {pct} %", { pct: Math.round(p.bypass) })}</text>
    ${pipe("M416,110 V200 H470", "vl", hotWater && running)}${pipe("M470,245 H370", "rl", hotWater && running)}
    <rect x="470" y="170" width="90" height="110" rx="40" class="tank ${hotWater ? "on" : ""}"></rect>
    <text x="515" y="218" font-size="12" text-anchor="middle">${tc("hot_water", "Speicher")}</text>
    <text x="515" y="236" font-size="12" text-anchor="middle" class="t-dhw">${hotWater ? tc("hot_water", "lädt") : ""}</text>
    ${pipe("M432,100 H600 V70 H630", "vl", heating)}${pipe("M630,140 H600 V150 H370", "rl", heating)}
    <text x="440" y="90" font-size="11" class="t-muted">${p.blocked ? t("Heizung gesperrt") : heating ? t("Heizung") : t("Heizung (gerade nicht)")}</text>
    <rect x="630" y="40" width="352" height=${houseH} rx="10" class="house"></rect>
    <text x="640" y="32" font-size="12" class="t-muted">${t("Haus · Raum-Ist {room} °C, Raum-Soll {setpoint} °C", { room: one(p.room), setpoint: one(p.setpoint) })}</text>
    ${rooms.map(roomBox)}
  </svg>`;
}

// ---------- History ----------

const HOUR = 3600e3;
const GAP_MS = 11 * 60e3;

/** Points split where samples are missing, as polyline point lists. */
function segments(items, x, y) {
  const parts = [];
  let current = [];
  let last = null;
  for (const [time, value] of items) {
    if (value == null || !Number.isFinite(value)) continue;
    if (last != null && time - last > GAP_MS && current.length) {
      parts.push(current);
      current = [];
    }
    current.push(`${x(time).toFixed(1)},${y(value).toFixed(1)}`);
    last = time;
  }
  if (current.length) parts.push(current);
  return parts;
}

/** Steps of a value that holds until the next change: [[time, value], ...] to points. */
function stepPoints(changes, end) {
  const points = [];
  changes.forEach(([time, value], i) => {
    if (i) points.push([time, changes[i - 1][1]]);
    points.push([time, value]);
  });
  if (changes.length) points.push([end, changes[changes.length - 1][1]]);
  return points;
}

/** Runs of rows where test holds, as [from, to] times (a row covers 5 minutes). */
function runs(rows, test) {
  const result = [];
  let open = null;
  let last = null;
  for (const row of rows) {
    const time = Date.parse(row.start);
    const hit = test(row);
    if (hit && open != null && last != null && time - last > GAP_MS) {
      result.push([open, last + 5 * 60e3]);
      open = null;
    }
    if (hit && open == null) open = time;
    if (!hit && open != null) {
      result.push([open, time]);
      open = null;
    }
    last = time;
  }
  if (open != null) result.push([open, (last ?? open) + 5 * 60e3]);
  return result;
}

function range(values, pad, minSpan) {
  const known = values.filter((v) => v != null && Number.isFinite(v));
  if (!known.length) return null;
  let low = Math.min(...known) - pad;
  let high = Math.max(...known) + pad;
  if (high - low < minSpan) {
    const mid = (low + high) / 2;
    low = mid - minSpan / 2;
    high = mid + minSpan / 2;
  }
  return [Math.floor(low), Math.ceil(high)];
}

function ticks([low, high], count = 4) {
  const span = high - low;
  const step = [1, 2, 5, 10, 20].find((s) => span / s <= count) ?? 50;
  const result = [];
  for (let v = Math.ceil(low / step) * step; v <= high; v += step) result.push(v);
  return result;
}

/**
 * The last hours: above flow, return, flow target, the set value in the heat pump (steps, a dot per write)
 * and what Sunflow would have written (dashed); below room temperature, single rooms and room setpoint.
 * Bands: hot water, defrost, block (above), fast changes (below); a strip for the compressor.
 */
export function historyChart(history, { names = {} } = {}) {
  const rows = history?.rows ?? [];
  if (!rows.length) return nothing;
  const W = 960;
  const L = 40;
  const R = 12;
  const top1 = 24;
  const h1 = 190;
  const strip = top1 + h1 + 6;
  const top2 = strip + 30;
  const h2 = 120;
  const H = top2 + h2 + 26;
  const since = Date.parse(history.since);
  const until = Date.parse(history.until);
  const x = (time) => L + ((time - since) / (until - since)) * (W - L - R);
  const mid = (row) => Date.parse(row.start) + 2.5 * 60e3;
  const events = history.events ?? [];
  const writes = events.filter((e) => e.kind === "write" && e.value_c != null);
  const real = writes.filter((e) => e.shadow === false);
  const virtual = writes.filter((e) => e.shadow !== false);
  // Compressor stops in heating (a cross at Tv)
  const stops = events.filter((e) => e.kind === "stop" && (e.activity == null || e.activity === "heating") && (e.tv_c != null || e.tv_max_c != null));
  const flowRange = range(
    rows.flatMap((r) => [r.tv_c, r.tr_c, r.target_c, r.max_setpoint_c]).concat(writes.map((e) => e.value_c)), 1, 8,
  ) ?? [20, 40];
  const roomValues = rows.flatMap((r) => [r.room_c, r.room_setpoint_c, ...Object.values(r.rooms ?? {})]);
  const roomRange = range(roomValues, 0.3, 3) ?? [19, 23];
  const y1 = (v) => top1 + h1 - ((v - flowRange[0]) / (flowRange[1] - flowRange[0])) * h1;
  const y2 = (v) => top2 + h2 - ((v - roomRange[0]) / (roomRange[1] - roomRange[0])) * h2;
  const line = (items, y, cls) => segments(items, x, y).map((pts) => svg`<polyline class=${cls} points=${pts.join(" ")}></polyline>`);
  const band = (from, to, top, height, cls) => svg`<rect class=${cls} x=${x(Math.max(from, since))} y=${top}
    width=${Math.max(1, x(Math.min(to, until)) - x(Math.max(from, since)))} height=${height}></rect>`;
  // Time axis: every 3 hours on the full hour (local time), more often for short spans
  const stepH = (until - since) / HOUR > 30 ? 12 : (until - since) / HOUR > 12 ? 3 : 1;
  const first = new Date(since);
  first.setMinutes(0, 0, 0);
  const grid = [];
  for (let time = first.getTime() + HOUR; time < until; time += HOUR) {
    if (new Date(time).getHours() % stepH === 0) grid.push(time);
  }
  const clock = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });
  const roomIds = [...new Set(rows.flatMap((r) => Object.keys(r.rooms ?? {})))];
  const hits = rows.map((row) => {
    const parts = [
      clock.format(new Date(Date.parse(row.start))),
      row.tv_c != null ? `${t("Vorlauf")} ${one(row.tv_c)} °C` : null,
      row.tr_c != null ? `${t("Rücklauf")} ${one(row.tr_c)} °C` : null,
      row.target_c != null ? `${t("Vorlauf-Ziel")} ${one(row.target_c)} °C` : null,
      row.max_setpoint_c != null ? `${t("Max T-Vorlauf")} ${one(row.max_setpoint_c)} °C` : null,
      row.room_c != null ? `${t("Raum-Ist")} ${one(row.room_c)} °C` : null,
      row.room_setpoint_c != null ? `${t("Raum-Soll")} ${one(row.room_setpoint_c)} °C` : null,
      ...roomIds.filter((e) => row.rooms?.[e] != null).map((e) => `${names[e] ?? e} ${one(row.rooms[e])} °C`),
    ].filter(Boolean);
    const start = Date.parse(row.start);
    return svg`<rect class="hit" x=${x(start)} y=${top1} width=${Math.max(1, x(start + 5 * 60e3) - x(start))} height=${top2 + h2 - top1}><title>${parts.join("\n")}</title></rect>`;
  });
  return svg`<svg class="diagram-svg history" viewBox=${`0 0 ${W} ${H}`} role="img" aria-label=${t("Verlauf der Wärmepumpe")}>
    ${runs(rows, (r) => r.activity === "hot_water").map(([a, b]) => band(a, b, top1, h1, "band dhw"))}
    ${runs(rows, (r) => r.activity === "defrost").map(([a, b]) => band(a, b, top1, h1, "band defrost"))}
    ${runs(rows, (r) => r.blocked).map(([a, b]) => band(a, b, top1, h1, "band block"))}
    ${runs(rows, (r) => (r.ventilation ?? []).length > 0).map(([a, b]) => band(a, b, top2, h2, "band fast"))}
    ${runs(rows, (r) => (r.compressor ?? 0) >= 0.5).map(([a, b]) => band(a, b, strip, 8, "band compressor"))}
    ${grid.map((time) => svg`<line class="grid" x1=${x(time)} x2=${x(time)} y1=${top1} y2=${top2 + h2}></line>
      <text class="axis" x=${x(time)} y=${H - 6} text-anchor="middle">${clock.format(new Date(time))}</text>`)}
    ${ticks(flowRange).map((v) => svg`<line class="grid dotted" x1=${L} x2=${W - R} y1=${y1(v)} y2=${y1(v)}></line>
      <text class="axis" x="4" y=${y1(v) + 4}>${v}°</text>`)}
    ${ticks(roomRange, 3).map((v) => svg`<line class="grid dotted" x1=${L} x2=${W - R} y1=${y2(v)} y2=${y2(v)}></line>
      <text class="axis" x="4" y=${y2(v) + 4}>${v}°</text>`)}
    <text class="axis strong" x=${L + 4} y=${top1 - 8}>${t("Vorlauf")}</text>
    <text class="axis" x=${W - R} y=${strip + 20} text-anchor="end">${t("Kompressor")}</text>
    <text class="axis strong" x=${L + 4} y=${top2 - 6}>${t("Räume")}</text>
    ${line(rows.map((r) => [mid(r), r.tr_c]), y1, "ln ret")}
    ${line(rows.map((r) => [mid(r), r.tv_c]), y1, "ln flow")}
    ${line(rows.map((r) => [mid(r), r.target_c]), y1, "ln target")}
    ${virtual.length
      ? line(stepPoints(virtual.map((e) => [Date.parse(e.at), e.value_c]), until), y1, "ln virtual")
      : nothing}
    ${line(rows.map((r) => [mid(r), r.max_setpoint_c]), y1, "ln setpoint")}
    ${real.map((e) => svg`<circle class="dot" cx=${x(Date.parse(e.at))} cy=${y1(e.value_c)} r="3.5"><title>${clock.format(new Date(e.at))}: ${one(e.from_c)} → ${one(e.value_c)} °C</title></circle>`)}
    ${virtual.map((e) => svg`<circle class="dot hollow" cx=${x(Date.parse(e.at))} cy=${y1(e.value_c)} r="3.5"><title>${clock.format(new Date(e.at))}: ${t("würde schreiben")} ${one(e.value_c)} °C</title></circle>`)}
    ${stops.map((e) => {
      const at = Date.parse(e.at);
      const v = Math.max(...[e.tv_c, e.tv_max_c].filter((x) => x != null));
      const cx = x(at);
      const cy = y1(Math.min(flowRange[1], Math.max(flowRange[0], v)));
      return svg`<path class="stop-mark" d=${`M${cx - 4},${cy - 4} L${cx + 4},${cy + 4} M${cx - 4},${cy + 4} L${cx + 4},${cy - 4}`}><title>${clock.format(new Date(at))}: ${t("Kompressor aus bei {tv} °C (Soll {setpoint} °C)", { tv: one(v), setpoint: one(e.setpoint_c) })}</title></path>`;
    })}
    ${roomIds.map((e) => line(rows.map((r) => [mid(r), r.rooms?.[e]]), y2, "ln single"))}
    ${line(rows.map((r) => [mid(r), r.room_setpoint_c]), y2, "ln room-set")}
    ${line(rows.map((r) => [mid(r), r.room_c]), y2, "ln room")}
    <line class="now" x1=${x(until)} x2=${x(until)} y1=${top1} y2=${top2 + h2}></line>
    ${hits}
  </svg>`;
}

// ---------- Heating curve ----------

/**
 * The heating curve: flow over the outside temperature at the room setpoint, the start curve dashed, the one in
 * use solid, the band of the set value's limits, the hours that were enough as dots and a mark at "now".
 * curve: { slope, shift_k, start_slope }.
 */
export function curveChart(curve, { roomC, minC, maxC, points = [], outsideC = null }) {
  if (!curve || roomC == null) return nothing;
  const W = 480;
  const H = 230;
  const L = 36;
  const B = 30;
  const top = 12;
  const flow = (slope, shift, a) => roomC + slope * (roomC - a) + shift;
  const xs = points.map((p) => p.outside);
  const low = Math.floor(Math.min(-10, ...xs) / 5) * 5;
  const high = Math.ceil(Math.max(15, ...xs) / 5) * 5;
  const line = (slope, shift) => [low, high].map((a) => [a, flow(slope, shift, a)]);
  const used = line(curve.slope, curve.shift_k);
  const start = line(curve.start_slope, 0);
  const yRange = range([...used, ...start].map(([, v]) => v).concat(points.map((p) => p.flow), minC, maxC), 1, 10) ?? [20, 45];
  const x = (a) => L + ((a - low) / (high - low)) * (W - L - 10);
  const y = (v) => top + (H - B - top) - ((v - yRange[0]) / (yRange[1] - yRange[0])) * (H - B - top);
  const pts = (items) => items.map(([a, v]) => `${x(a).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const xTicks = [];
  for (let a = low; a <= high; a += 5) xTicks.push(a);
  const now = outsideC != null ? flow(curve.slope, curve.shift_k, outsideC) : null;
  return svg`<svg class="diagram-svg curve" viewBox=${`0 0 ${W} ${H}`} role="img" aria-label=${t("Heizkurve")}>
    ${minC != null && maxC != null
      ? svg`<rect class="limits" x=${L} y=${y(Math.min(maxC, yRange[1]))} width=${W - L - 10}
          height=${Math.max(0, y(Math.max(minC, yRange[0])) - y(Math.min(maxC, yRange[1])))}></rect>
        <text class="axis" x=${W - 14} y=${y(Math.min(maxC, yRange[1])) + 13} text-anchor="end">${t("Grenzen {min}–{max} °C", { min: one(minC), max: one(maxC) })}</text>`
      : nothing}
    ${ticks(yRange).map((v) => svg`<line class="grid dotted" x1=${L} x2=${W - 10} y1=${y(v)} y2=${y(v)}></line>
      <text class="axis" x="4" y=${y(v) + 4}>${v}°</text>`)}
    ${xTicks.map((a) => svg`<text class="axis" x=${x(a)} y=${H - 17} text-anchor="middle">${a}°</text>`)}
    <text class="axis" x=${(W + L) / 2} y=${H - 4} text-anchor="middle">${t("Außentemperatur")}</text>
    ${points.map((p) => svg`<circle class="dot point" cx=${x(p.outside)} cy=${y(p.flow)} r="2.5"></circle>`)}
    <polyline class="ln curve unsure" points=${pts(start)}></polyline>
    <polyline class="ln curve" points=${pts(used)}></polyline>
    ${now != null
      ? svg`<circle class="dot typical" cx=${x(outsideC)} cy=${y(now)} r="5"></circle>
        <text class="axis" x=${x(outsideC) + 9} y=${y(now) - 7}>${t("jetzt: außen {outside} °C → {flow} °C", { outside: one(outsideC), flow: one(now) })}</text>`
      : nothing}
  </svg>`;
}

export const diagramStyles = css`
  .diagram { width: 100%; overflow-x: auto; }
  .diagram-svg { display: block; width: 100%; height: auto; }
  .diagram-svg.chain, .diagram-svg.plant { min-width: 680px; }
  .diagram-svg.history { min-width: 560px; }
  .diagram-svg text { fill: var(--sf-text); font-family: inherit; }
  .diagram-svg .t-muted { fill: var(--sf-muted); }
  .diagram-svg .t-dhw { fill: var(--hp-dhw); }
  .blk { cursor: pointer; outline: none; }
  .blk rect { fill: var(--sf-subtle); stroke: var(--sf-border-strong); stroke-width: 1.5; }
  .blk:hover rect, .blk:focus-visible rect { stroke: var(--sf-primary); }
  .blk.sel rect { stroke: var(--sf-primary); stroke-width: 3; fill: var(--sf-primary-bg); }
  .blk.frozen rect { stroke-dasharray: 5 4; opacity: .75; }
  .blk.acting rect { stroke: var(--sf-warn-text); stroke-width: 2.5; fill: var(--sf-warn-bg); }
  .blk.warn rect { stroke: var(--sf-error-text); stroke-width: 2.5; }
  .key-frozen { fill: var(--sf-subtle); stroke: var(--sf-border-strong); stroke-dasharray: 4 3; }
  .key-acting { fill: var(--sf-warn-bg); stroke: var(--sf-warn-text); }
  .sum { fill: var(--sf-surface); stroke: var(--sf-muted); stroke-width: 2; }
  .formula rect { fill: var(--sf-subtle); stroke: var(--sf-border); }
  .wire { fill: none; stroke: var(--sf-muted); stroke-width: 2; }
  .wire.frozen { stroke-dasharray: 4 4; opacity: .6; }
  .wire.live { stroke: var(--sf-primary); stroke-width: 2.5; }
  .wire.feedback { stroke-dasharray: 2 5; }
  .unit, .room { fill: var(--sf-subtle); stroke: var(--sf-border-strong); }
  .room.fast { stroke: var(--hp-cold); stroke-dasharray: 4 3; stroke-width: 2; }
  .house { fill: none; stroke: var(--sf-border); stroke-dasharray: 4 4; }
  .tank { fill: var(--sf-subtle); stroke: var(--sf-border-strong); }
  .tank.on { stroke: var(--hp-dhw); stroke-width: 3; }
  .fan-ring { fill: none; stroke: var(--sf-border-strong); stroke-width: 2; }
  .fan path { fill: var(--sf-muted); }
  .fan.spin { transform-box: fill-box; transform-origin: center; animation: hp-spin 1.2s linear infinite; }
  .valve { fill: var(--sf-text); }
  .pipe { fill: none; stroke-width: 7; stroke-linecap: round; }
  .pipe.vl { stroke: var(--hp-hot); }
  .pipe.rl { stroke: var(--hp-cold); }
  .pipe.off { opacity: .18; }
  .pipe-flow { fill: none; stroke: var(--sf-surface); stroke-width: 2; stroke-dasharray: 3 14; animation: hp-flow 1s linear infinite; }
  .pipe-flow.rev { animation-direction: reverse; }
  @media (prefers-reduced-motion: reduce) { .fan.spin, .pipe-flow { animation: none; } }
  @keyframes hp-flow { to { stroke-dashoffset: -17; } }
  @keyframes hp-spin { to { transform: rotate(360deg); } }
  .grid { stroke: var(--sf-border); stroke-width: 1; }
  .grid.dotted { stroke-dasharray: 2 4; }
  .axis { fill: var(--sf-muted); font-size: 11px; }
  .axis.strong { fill: var(--sf-text); font-weight: 600; font-size: 12px; }
  .band.dhw { fill: var(--hp-dhw); opacity: .16; }
  .band.defrost { fill: var(--hp-cold); opacity: .14; }
  .band.block { fill: var(--sf-muted); opacity: .16; }
  .band.fast { fill: var(--hp-cold); opacity: .12; }
  .band.compressor { fill: var(--sf-ok); opacity: .7; }
  .ln { fill: none; stroke-width: 2; stroke-linejoin: round; }
  .ln.flow { stroke: var(--hp-hot); }
  .ln.ret { stroke: var(--hp-cold); opacity: .6; }
  .ln.target { stroke: var(--sf-series-3); stroke-width: 2.5; }
  .ln.setpoint { stroke: var(--sf-text); stroke-width: 2.5; }
  .ln.virtual { stroke: var(--sf-series-6); stroke-dasharray: 5 4; }
  .ln.room { stroke: var(--sf-series-1); stroke-width: 2.5; }
  .ln.single { stroke: var(--sf-series-1); stroke-width: 1; opacity: .25; }
  .ln.room-set { stroke: var(--sf-series-4); stroke-dasharray: 6 3; }
  .ln.curve { stroke: var(--sf-series-2); stroke-width: 2.5; }
  .ln.curve.unsure { stroke-dasharray: 6 3; }
  .dot { fill: var(--sf-text); }
  .stop-mark { stroke: var(--sf-error-text); stroke-width: 2; fill: none; }
  .dot.hollow { fill: var(--sf-surface); stroke: var(--sf-series-6); stroke-width: 1.5; }
  .dot.point { fill: var(--sf-series-1); opacity: .45; }
  .dot.typical { fill: var(--sf-series-2); }
  .limits { fill: var(--sf-ok); opacity: .07; }
  .now { stroke: var(--sf-primary); stroke-width: 1.5; }
  .hit { fill: transparent; }
  .hit:hover { fill: var(--sf-text); fill-opacity: .06; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 13px; color: var(--sf-muted); }
  .legend i { display: inline-block; width: 18px; height: 3px; vertical-align: middle; margin-right: 5px; border-radius: 2px; }
  .legend i.dash { height: 0; border-top: 2px dashed; border-radius: 0; }
  .legend i.box { height: 10px; opacity: .4; }
`;
