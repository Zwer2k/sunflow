import { LitElement, svg, css, nothing } from "../../vendor/lit-all.min.js";
import { formatPower } from "../lib/format.js";
import { t } from "../lib/i18n.js";

const ACTIVE_W = 20;
const COLOR = {
  pv: "var(--sf-series-2)",
  grid: "var(--sf-series-1)",
  battery: "var(--sf-series-3)",
  home: "var(--sf-series-4)",
  heat: "var(--sf-series-5)",
  vehicle: "var(--sf-series-6)",
  load: "var(--sf-series-neutral)",
};

// Isometric projection: metres to drawing units
const K = 28;
const OX = 340;
const OY = 250;
const iso = (x, y, z = 0) => [OX + (x - y) * K * 0.866, OY + (x + y) * K * 0.5 - z * K];
const pts = (...corners) => corners.map((c) => iso(...c).join(",")).join(" ");
const clamp = (v, low, high) => Math.min(Math.max(v, low), high);
const lerp3 = (a, b, f) => a.map((v, i) => v + (b[i] - v) * f);

// Main house: 9 x 8 m, walls 4.5 m, a low gable (the north slope just shows behind the ridge)
const HOUSE = { x: 9, y: 8, wall: 4.5, ridge: 5.9 };
// Annex in front on the right, a mono-pitch roof falling to the front
const ANNEX = { x0: 9, x1: 14, y0: 3, y1: 8, back: 4.0, front: 3.0 };
// Carport with a flat roof falling to the front
const CARPORT = { x0: 15.5, x1: 21, y0: 2, y1: 8, back: 3.0, front: 2.7 };

// Roof faces for PV: corners (eave left, eave right, top right, top left) in metres
const ROOFS = {
  south: [[0.6, 8, HOUSE.wall], [8.4, 8, HOUSE.wall], [8.4, 4, HOUSE.ridge], [0.6, 4, HOUSE.ridge]],
  north: [[0.6, 0, HOUSE.wall], [8.4, 0, HOUSE.wall], [8.4, 4, HOUSE.ridge], [0.6, 4, HOUSE.ridge]],
  annex: [[9.3, 8, ANNEX.front], [13.7, 8, ANNEX.front], [13.7, 3, ANNEX.back], [9.3, 3, ANNEX.back]],
  carport: [[15.8, 8, CARPORT.front + 0.12], [20.7, 8, CARPORT.front + 0.12], [20.7, 2, CARPORT.back + 0.12], [15.8, 2, CARPORT.back + 0.12]],
};
// Free faces in this order; names of the forecast plane pick north and carport first
const ROOF_ORDER = ["south", "annex", "carport", "north"];
const ROOF_HINTS = [[/nord|north/i, "north"], [/carport|garage|vordach|port/i, "carport"]];
// Inverters on the annex front wall, their batteries below them
const INVERTERS = [10.0, 12.0];
// Point where grid, inverters and house loads meet: the meter on the house front
const HUB = [0.8, 8, 1.6];

/** Which known load a consumer is, from its kind and name; null: only part of the house load. */
function role(consumer) {
  const name = consumer.name ?? "";
  if (consumer.kind === "vehicle") return "vehicle";
  if (/wärmepumpe|waermepumpe|heat ?pump|\bwp\b/i.test(name)) return "heatpump";
  if (consumer.kind === "heater" || /warmwasser|boiler|heizstab|hot water/i.test(name)) return "tank";
  if (/e-?bike|fahrrad|pedelec|\bbike\b/i.test(name)) return "bike";
  if (/wasch|washer|washing/i.test(name)) return "washer";
  if (/trockner|dryer/i.test(name)) return "dryer";
  if (/herd|kochfeld|backofen|\bofen\b|stove|oven|cooktop/i.test(name)) return "stove";
  return null;
}

/** The site as an isometric house: up to four roof faces, two inverters with two batteries each and the measured loads. */
class SunflowIsoScene extends LitElement {
  static properties = { live: { attribute: false }, data: { attribute: false } };

  render() {
    const live = this.live;
    const data = this.data;
    if (!live || !data) return nothing;
    const pv = Math.max(live.pv_w ?? 0, 0);
    const hour = new Date(live.time).getHours();
    const day = pv > ACTIVE_W || (hour >= 7 && hour < 18);
    const roofs = this._roofs(live);
    const inverters = this._inverters(live);
    // Only loads with a reading
    const loads = {};
    for (const consumer of live.consumers) {
      const which = role(consumer);
      if (which && !loads[which] && consumer.power_w !== null && consumer.power_w !== undefined) loads[which] = consumer;
    }
    const { displays, homeBase, hasGrid } = data;
    const imported = Math.max(live.grid_w ?? 0, 0);
    const exported = Math.max(-(live.grid_w ?? 0), 0);
    const hub = iso(...HUB);
    return svg`<svg viewBox="0 0 900 700" class="scene ${day ? "day" : "night"}" role="img"
      aria-label=${t("Energiefluss: {summary}", { summary: data.summary })}>
      <defs>
        <radialGradient id="iso-sky" cx="0.5" cy="0.35" r="0.8">
          <stop offset="0" class="sky-in"></stop><stop offset="1" class="sky-out"></stop>
        </radialGradient>
        <linearGradient id="iso-panel" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#2f5d9e"></stop><stop offset="1" stop-color="#173057"></stop>
        </linearGradient>
      </defs>
      <rect x="0" y="0" width="900" height="700" rx="18" fill="url(#iso-sky)"></rect>
      <polygon points=${pts([-4, -1, 0], [23, -1, 0], [23, 11, 0], [-4, 11, 0])} class="ground"></polygon>

      ${this._pole(hasGrid)}
      ${this._house(roofs, loads)}
      ${this._annex(roofs, inverters)}
      ${this._carport(roofs, loads.vehicle)}
      ${loads.heatpump ? this._heatPump(loads.heatpump) : nothing}
      ${loads.bike ? this._bike(loads.bike) : nothing}

      ${Object.entries(roofs).map(([face, roof]) => this._flow(this._roofCenter(face), iso(INVERTERS[roof.inverter] + 0.5, 8.2, 2.1), roof.power, COLOR.pv, true))}
      ${inverters.map((inverter, i) => svg`
        ${this._flow(iso(INVERTERS[i], 8.2, 1.4), hub, inverter.power, COLOR.pv)}
        ${inverter.batteries.map((battery, j) => {
          const power = battery.power_w ?? 0;
          const at = iso(INVERTERS[i] + 0.5, 8.3, 0.9 - j * 0.75);
          return power >= 0
            ? this._flow(iso(INVERTERS[i] + 0.5, 8.3, 1.6), at, power, COLOR.battery)
            : this._flow(at, iso(INVERTERS[i] + 0.5, 8.3, 1.6), -power, COLOR.battery);
        })}`)}
      ${hasGrid
        ? imported >= exported
          ? this._flow(this._poleTop(), hub, imported, COLOR.grid)
          : this._flow(hub, this._poleTop(), exported, COLOR.pv)
        : nothing}
      ${loads.vehicle ? this._flow(hub, iso(14.05, 6.6, 1.5), loads.vehicle.power_w, COLOR.vehicle) : nothing}
      ${loads.heatpump ? this._flow(hub, iso(7.0, 8.6, 1.2), loads.heatpump.power_w, COLOR.heat) : nothing}

      ${this._labels(live, roofs, inverters, loads, { displays, homeBase, hasGrid, imported, exported })}
    </svg>`;
  }

  /** Measured strings (or whole plants) on the roof faces, with the inverter they belong to. */
  _roofs(live) {
    const parts = live.pv.slice(0, 2).flatMap((plant, inverter) =>
      plant.strings?.length
        ? plant.strings.map((s) => ({ name: s.plane ?? `${plant.name} ${s.name}`, power: s.power_w ?? 0, inverter, display: s.display }))
        : [{ name: plant.name, power: plant.power_w ?? 0, inverter, display: plant.display }],
    );
    const roofs = {};
    for (const part of parts) {
      const hinted = ROOF_HINTS.find(([pattern]) => pattern.test(part.name))?.[1];
      const face = [hinted, ...ROOF_ORDER].find((f) => f && !roofs[f]) ?? ROOF_ORDER.findLast((f) => roofs[f]);
      if (roofs[face]) {
        roofs[face].power += part.power;
        roofs[face].names.push(part.name);
      } else {
        roofs[face] = { power: part.power, names: [part.name], inverter: part.inverter, display: part.display };
      }
    }
    return roofs;
  }

  /** Up to two inverters with up to two batteries each; a battery of the inverter's own device first. */
  _inverters(live) {
    const inverters = live.pv.slice(0, 2).map((plant) => ({ name: plant.name, power: plant.power_w ?? 0, display: plant.display, batteries: [] }));
    if (!inverters.length && live.batteries?.length) inverters.push({ name: t("Wechselrichter"), power: 0, batteries: [] });
    for (const battery of live.batteries ?? []) {
      const own = live.pv.slice(0, 2).findIndex((plant) => plant.device_id === battery.device_id);
      const target = own >= 0 && inverters[own].batteries.length < 2 ? own : inverters.findIndex((i) => i.batteries.length < 2);
      if (target >= 0) inverters[target].batteries.push(battery);
    }
    return inverters;
  }

  _roofCenter(face) {
    const [a, b, c, d] = ROOFS[face];
    return iso(...lerp3(lerp3(a, b, 0.5), lerp3(d, c, 0.5), 0.5));
  }

  _poleTop() {
    return iso(-2.5, 10, 6.4);
  }

  /** Panels on a roof face, brighter with more power; the north face only shows behind the ridge. */
  _panels(face, roof) {
    const [a, b, c, d] = ROOFS[face];
    const cols = face === "carport" ? 5 : face === "annex" ? 4 : 7;
    const rows = face === "north" || face === "south" ? 3 : 3;
    const point = (u, v) => iso(...lerp3(lerp3(a, b, u), lerp3(d, c, u), v));
    const gap = 0.04;
    const shine = clamp(roof.power / 1500, 0, 1);
    const panels = [];
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const u0 = col / cols + gap / 2, u1 = (col + 1) / cols - gap / 2;
        const v0 = row / rows + gap, v1 = (row + 1) / rows - gap;
        panels.push(svg`<polygon class="panel" points=${[point(u0, v0), point(u1, v0), point(u1, v1), point(u0, v1)].map((p) => p.join(",")).join(" ")}></polygon>`);
      }
    }
    return svg`<g class="panels ${face}">${panels}<g class="shine" style="opacity: ${shine}">${panels}</g></g>`;
  }

  _house(roofs, loads) {
    const { x, y, wall, ridge } = HOUSE;
    const mid = y / 2;
    const lit = (this.data?.homeBase ?? 0) > 150;
    // Through the big front window: the measured appliances inside
    const inside = ["tank", "washer", "dryer", "stove"].filter((r) => loads[r]);
    return svg`<g class="house">
      <polygon class="roof back" points=${pts([0, 0, wall], [x, 0, wall], [x, mid, ridge], [0, mid, ridge])}></polygon>
      ${roofs.north ? this._panels("north", roofs.north) : nothing}
      <polygon class="wall front" points=${pts([0, y, 0], [x, y, 0], [x, y, wall], [0, y, wall])}></polygon>
      <polygon class="wall side" points=${pts([x, y, 0], [x, 0, 0], [x, 0, wall], [x, mid, ridge], [x, y, wall])}></polygon>
      <polygon class="roof front" points=${pts([-0.3, y + 0.4, wall - 0.2], [x + 0.3, y + 0.4, wall - 0.2], [x + 0.3, mid, ridge], [-0.3, mid, ridge])}></polygon>
      ${roofs.south ? this._panels("south", roofs.south) : nothing}
      <polygon class="window big ${lit ? "lit" : ""}" points=${pts([1.8, y, 0.5], [6.4, y, 0.5], [6.4, y, 3.5], [1.8, y, 3.5])}></polygon>
      ${inside.map((which, i) => this._appliance(which, loads[which], iso(2.4 + i * 1.15, y, 0.6)))}
      <polygon class="door" points=${pts([7.0, y, 0], [8.1, y, 0], [8.1, y, 2.3], [7.0, y, 2.3])}></polygon>
      <polygon class="window" points=${pts([x, 5.5, 2.0], [x, 2.5, 2.0], [x, 2.5, 3.6], [x, 5.5, 3.6])}></polygon>
      <polygon class="meter" points=${pts([0.4, y + 0.02, 1.1], [1.2, y + 0.02, 1.1], [1.2, y + 0.02, 2.1], [0.4, y + 0.02, 2.1])}></polygon>
    </g>`;
  }

  /** An appliance seen through the window, standing on the floor at the point. */
  _appliance(which, consumer, [px, py]) {
    const on = (consumer.power_w ?? 0) > ACTIVE_W;
    const shape = {
      tank: svg`<rect x="-11" y="-50" width="22" height="50" rx="10" class="app-body"></rect>
        <rect x="-11" y="-50" width="22" height="50" rx="10" class="tank-heat"
          style="opacity: ${clamp(((consumer.level ?? 40) - 20) / 50, 0.15, 1)}"></rect>`,
      washer: svg`<rect x="-13" y="-30" width="26" height="30" rx="3" class="app-body"></rect>
        <circle cx="0" cy="-14" r="8" class="app-door ${on ? "spin" : ""}"></circle>`,
      dryer: svg`<rect x="-13" y="-30" width="26" height="30" rx="3" class="app-body"></rect>
        <circle cx="0" cy="-14" r="8" class="app-door ${on ? "spin" : ""}"></circle><rect x="-9" y="-28" width="18" height="3" class="app-panel"></rect>`,
      stove: svg`<rect x="-13" y="-28" width="26" height="28" rx="2" class="app-body"></rect>
        <rect x="-10" y="-18" width="20" height="14" rx="2" class="app-door"></rect>
        <line x1="-8" y1="-25" x2="-3" y2="-25" class="burner ${on ? "hot" : ""}"></line><line x1="3" y1="-25" x2="8" y2="-25" class="burner ${on ? "hot" : ""}"></line>`,
    }[which];
    return svg`<g class="appliance ${on ? "on" : ""}" transform="translate(${px + 14} ${py}) scale(1.3)">${shape}<title>${consumer.name}</title></g>`;
  }

  _annex(roofs, inverters) {
    const { x0, x1, y0, y1, back, front } = ANNEX;
    return svg`<g class="annex">
      <polygon class="wall front" points=${pts([x0, y1, 0], [x1, y1, 0], [x1, y1, front], [x0, y1, front])}></polygon>
      <polygon class="wall side" points=${pts([x1, y1, 0], [x1, y0, 0], [x1, y0, back], [x1, y1, front])}></polygon>
      <polygon class="roof flat" points=${pts([x0 - 0.2, y1 + 0.3, front - 0.1], [x1 + 0.2, y1 + 0.3, front - 0.1], [x1 + 0.2, y0, back], [x0 - 0.2, y0, back])}></polygon>
      ${roofs.annex ? this._panels("annex", roofs.annex) : nothing}
      ${inverters.map((inverter, i) => this._inverter(inverter, INVERTERS[i]))}
    </g>`;
  }

  _inverter(inverter, x) {
    const active = inverter.power > ACTIVE_W;
    return svg`<g class="inverter ${active ? "active" : ""}">
      <polygon class="box" points=${pts([x, 8.25, 1.5], [x + 1.0, 8.25, 1.5], [x + 1.0, 8.25, 2.7], [x, 8.25, 2.7])}></polygon>
      <polygon class="box-side" points=${pts([x + 1.0, 8.25, 1.5], [x + 1.0, 8.0, 1.5], [x + 1.0, 8.0, 2.7], [x + 1.0, 8.25, 2.7])}></polygon>
      <polygon class="box-top" points=${pts([x, 8.25, 2.7], [x + 1.0, 8.25, 2.7], [x + 1.0, 8.0, 2.7], [x, 8.0, 2.7])}></polygon>
      <polygon class="display" points=${pts([x + 0.25, 8.26, 2.15], [x + 0.75, 8.26, 2.15], [x + 0.75, 8.26, 2.45], [x + 0.25, 8.26, 2.45])}></polygon>
      ${inverter.batteries.map((battery, j) => this._batteryBox(battery, x, 0.75 - j * 0.75))}
      <title>${inverter.name}</title>
    </g>`;
  }

  /** A battery module on the wall below its inverter; the fill shows the level. */
  _batteryBox(battery, x, z) {
    const level = clamp((battery.soc_pct ?? 0) / 100, 0, 1);
    const active = Math.abs(battery.power_w ?? 0) > ACTIVE_W;
    const top = z + 0.65;
    return svg`<g class="battery ${active ? "active" : ""}">
      <polygon class="bat-front" points=${pts([x, 8.35, z], [x + 1.0, 8.35, z], [x + 1.0, 8.35, top], [x, 8.35, top])}></polygon>
      <polygon class="bat-side" points=${pts([x + 1.0, 8.35, z], [x + 1.0, 8.0, z], [x + 1.0, 8.0, top], [x + 1.0, 8.35, top])}></polygon>
      <polygon class="bat-top" points=${pts([x, 8.35, top], [x + 1.0, 8.35, top], [x + 1.0, 8.0, top], [x, 8.0, top])}></polygon>
      <polygon class="bat-fill" points=${pts([x + 0.12, 8.36, z + 0.25], [x + 0.12 + 0.76 * level, 8.36, z + 0.25], [x + 0.12 + 0.76 * level, 8.36, z + 0.4], [x + 0.12, 8.36, z + 0.4])}></polygon>
      <title>${battery.name}${battery.soc_pct === null || battery.soc_pct === undefined ? "" : ` ${Math.round(battery.soc_pct)} %`}</title>
    </g>`;
  }

  _carport(roofs, vehicle) {
    const { x0, x1, y0, y1, back, front } = CARPORT;
    const post = (x, y, h) => svg`<line class="post" x1=${iso(x, y, 0)[0]} y1=${iso(x, y, 0)[1]} x2=${iso(x, y, h)[0]} y2=${iso(x, y, h)[1]}></line>`;
    return svg`<g class="carport">
      ${post(x0 + 0.2, y0 + 0.2, back)}${post(x1 - 0.2, y0 + 0.2, back)}
      ${post(x0 + 0.2, y1 - 0.2, front)}${post(x1 - 0.2, y1 - 0.2, front)}
      <polygon class="slab" points=${pts([x0, y1, front], [x1, y1, front], [x1, y1, front + 0.12], [x0, y1, front + 0.12])}></polygon>
      <polygon class="slab-side" points=${pts([x1, y1, front], [x1, y0, back], [x1, y0, back + 0.12], [x1, y1, front + 0.12])}></polygon>
      <polygon class="roof flat" points=${pts([x0, y1, front + 0.12], [x1, y1, front + 0.12], [x1, y0, back + 0.12], [x0, y0, back + 0.12])}></polygon>
      ${roofs.carport ? this._panels("carport", roofs.carport) : nothing}
      ${vehicle ? this._wallbox(vehicle) : nothing}
      ${vehicle && vehicle.connected !== false ? this._car(vehicle) : nothing}
    </g>`;
  }

  _car(vehicle) {
    const level = clamp((vehicle.level ?? 0) / 100, 0, 1);
    const charging = (vehicle.power_w ?? 0) > ACTIVE_W;
    const box = (xa, xb, ya, yb, za, zb, cls) => svg`
      <polygon class="${cls} top" points=${pts([xa, yb, zb], [xb, yb, zb], [xb, ya, zb], [xa, ya, zb])}></polygon>
      <polygon class="${cls} front" points=${pts([xa, yb, za], [xb, yb, za], [xb, yb, zb], [xa, yb, zb])}></polygon>
      <polygon class="${cls} side" points=${pts([xb, yb, za], [xb, ya, za], [xb, ya, zb], [xb, yb, zb])}></polygon>`;
    const wheel = (x, y) => {
      const [cx, cy] = iso(x, y, 0.35);
      return svg`<ellipse cx=${cx} cy=${cy} rx="9" ry="11" class="wheel" transform="rotate(-30 ${cx} ${cy})"></ellipse>`;
    };
    return svg`<g class="car ${charging ? "charging" : ""}">
      ${wheel(16.9, 8.3)}${wheel(19.7, 8.3)}
      ${box(16.3, 20.4, 6.2, 8.3, 0.3, 1.05, "car-body")}
      ${box(17.2, 19.4, 6.4, 8.1, 1.05, 1.65, "car-cabin")}
      <polygon class="car-level" points=${pts([16.9, 8.31, 0.55], [19.9, 8.31, 0.55], [19.9, 8.31, 0.75], [16.9, 8.31, 0.75])}></polygon>
      <polygon class="car-level-fill" points=${pts([16.9, 8.32, 0.55], [16.9 + 3 * level, 8.32, 0.55], [16.9 + 3 * level, 8.32, 0.75], [16.9, 8.32, 0.75])}></polygon>
    </g>`;
  }

  _wallbox(vehicle) {
    const charging = (vehicle.power_w ?? 0) > ACTIVE_W;
    return svg`<polygon class="wallbox ${charging ? "charging" : ""}"
      points=${pts([14.05, 7.0, 1.0], [14.05, 6.3, 1.0], [14.05, 6.3, 1.9], [14.05, 7.0, 1.9])}></polygon>`;
  }

  _heatPump(consumer) {
    const on = (consumer.power_w ?? 0) > ACTIVE_W;
    const [x0, x1, y0, y1, h] = [6.1, 7.9, 8.5, 9.3, 1.2];
    const [fx, fy] = iso((x0 + x1) / 2, y1, h / 2);
    return svg`<g class="heatpump ${on ? "on" : ""}">
      <polygon class="hp front" points=${pts([x0, y1, 0], [x1, y1, 0], [x1, y1, h], [x0, y1, h])}></polygon>
      <polygon class="hp side" points=${pts([x1, y1, 0], [x1, y0, 0], [x1, y0, h], [x1, y1, h])}></polygon>
      <polygon class="hp top" points=${pts([x0, y1, h], [x1, y1, h], [x1, y0, h], [x0, y0, h])}></polygon>
      <g transform="translate(${fx} ${fy}) skewY(-30)"><circle r="10" class="fan"></circle>
        <g class="blades"><line x1="-9" y1="0" x2="9" y2="0"></line><line x1="0" y1="-9" x2="0" y2="9"></line></g></g>
      <title>${consumer.name}</title>
    </g>`;
  }

  _bike(consumer) {
    const charging = (consumer.power_w ?? 0) > ACTIVE_W;
    const [x, y] = iso(14.6, 9.6, 0);
    return svg`<g class="bike ${charging ? "charging" : ""}" transform="translate(${x} ${y})">
      <circle cx="-14" cy="-10" r="10" class="bike-wheel"></circle><circle cx="14" cy="-10" r="10" class="bike-wheel"></circle>
      <path d="M-14,-10 L-3,-26 L10,-26 L14,-10 M-3,-26 L2,-10 L-14,-10 M2,-10 L10,-26 M8,-31 L12,-31 M-6,-30 L0,-30" class="bike-frame"></path>
      <rect x="-6" y="-24" width="9" height="5" rx="1" class="bike-battery"></rect>
      <title>${consumer.name}</title>
    </g>`;
  }

  _pole(hasGrid) {
    const [bx, by] = iso(-2.5, 10, 0);
    const [tx, ty] = iso(-2.5, 10, 7);
    return svg`<g class="pole ${hasGrid ? "" : "off"}">
      <line x1=${bx} y1=${by} x2=${tx} y2=${ty}></line>
      <line x1=${tx - 18} y1=${ty + 14} x2=${tx + 18} y2=${ty + 4}></line>
      <line x1=${tx - 14} y1=${ty + 30} x2=${tx + 14} y2=${ty + 22}></line>
    </g>`;
  }

  /** A cable from a to b; with power, sparks run along it (faster and thicker with more). */
  _flow([ax, ay], [bx, by], power, color, curved = false) {
    const path = curved ? `M${ax},${ay} Q${(ax + bx) / 2 + 30},${Math.min(ay, by) - 10} ${bx},${by}` : `M${ax},${ay} L${bx},${by}`;
    if ((power ?? 0) <= ACTIVE_W) return svg`<path d=${path} class="wire idle"></path>`;
    const width = 2 + 4 * clamp(power / 6000, 0, 1);
    const duration = clamp(2.2 - power / 3000, 0.45, 2.2);
    return svg`<path d=${path} class="wire" style="stroke: ${color}; stroke-width: ${width}"></path>
      <path d=${path} class="spark" style="stroke: ${color}; stroke-width: ${width + 1.5}; animation-duration: ${duration}s">
        <title>${formatPower(power)}</title></path>`;
  }

  /** Callouts like a product drawing: a dot on the thing, a leader line and the value; in fixed rows and columns. */
  _labels(live, roofs, inverters, loads, { displays, homeBase, hasGrid, imported, exported }) {
    // Long names (e.g. "Warmwasserspeicher") would run into the neighbouring label
    const short = (text) => (text.length > 16 ? `${text.slice(0, 15)}…` : text);
    const callout = (anchor, [lx, ly], title, value, color, align = "middle") => {
      const [ax, ay] = anchor;
      return svg`<g class="callout">
        <line x1=${ax} y1=${ay} x2=${lx} y2=${ly + 6} class="leader"></line>
        <circle cx=${ax} cy=${ay} r="3.5" class="dot" style="fill: ${color}"></circle>
        <text x=${lx} y=${ly - 22} class="c-title" text-anchor=${align}>${short(title)}<title>${title}</title></text>
        <text x=${lx} y=${ly} class="c-value" text-anchor=${align} style="fill: ${color}">${value}</text>
      </g>`;
    };
    const signed = (w) => `${w > ACTIVE_W ? "+" : w < -ACTIVE_W ? "−" : ""}`;
    const level = (c, unit) => (c.level !== null && c.level !== undefined ? ` · ${Math.round(c.level)} ${unit}` : "");
    const soc = (b) => (b.soc_pct === null || b.soc_pct === undefined ? "" : ` ${Math.round(b.soc_pct)} %`);
    const pvTitle = (face, roof) => (face === "north" ? `${t("Nordseite")}: ${roof.names[0]}` : roof.names.join(" + "));
    // Roof faces along the top
    const top = { south: [24, 52], north: [440, 40], annex: [690, 52] };
    // Right column, top to bottom: carport, each inverter with its batteries, car, e-bike
    const right = [];
    if (roofs.carport) {
      right.push([this._roofCenter("carport"), pvTitle("carport", roofs.carport), formatPower(roofs.carport.power, roofs.carport.display), COLOR.pv]);
    }
    inverters.forEach((inverter, i) => {
      right.push([iso(INVERTERS[i] + 1.0, 8.1, 2.4), inverter.name, formatPower(inverter.power, inverter.display), COLOR.pv]);
      inverter.batteries.forEach((battery, j) => right.push([iso(INVERTERS[i] + 1.0, 8.2, 1.05 - j * 0.75),
        battery.short_name ?? battery.name, `${signed(battery.power_w ?? 0)}${formatPower(Math.abs(battery.power_w ?? 0), battery.display)}${soc(battery) ? ` ·${soc(battery)}` : ""}`,
        COLOR.battery]));
    });
    if (loads.vehicle) {
      const car = loads.vehicle;
      right.push([iso(20.4, 7.2, 1.0), car.connected === false ? t("kein Auto") : (car.vehicle ?? car.name),
        `${formatPower(car.power_w ?? 0, car.display)}${level(car, "%")}`, COLOR.vehicle]);
    }
    if (loads.bike) right.push([iso(14.6, 9.6, 1.2), loads.bike.name, formatPower(loads.bike.power_w, loads.bike.display), COLOR.load]);
    const step = Math.min(64, 560 / Math.max(right.length, 1));
    // Left column: grid and house; bottom row: what is inside and the heat pump
    const bottom = ["tank", "washer", "dryer", "stove"].filter((r) => loads[r]).map((which, i) => {
      const consumer = loads[which];
      return [iso(2.4 + i * 1.15, 8, 1.4).map((v, k) => v + (k ? 0 : 14)),
        consumer.name, `${formatPower(consumer.power_w, consumer.display)}${which === "tank" ? level(consumer, "°C") : ""}`,
        which === "tank" ? COLOR.heat : COLOR.load];
    });
    if (loads.heatpump) bottom.push([iso(7.0, 9.3, 1.2), loads.heatpump.name, formatPower(loads.heatpump.power_w, loads.heatpump.display), COLOR.heat]);
    return svg`
      ${Object.entries(roofs).filter(([face]) => face in top).map(([face, roof]) =>
        callout(this._roofCenter(face), top[face], pvTitle(face, roof), formatPower(roof.power, roof.display), COLOR.pv, face === "south" ? "start" : "middle"))}
      ${right.map(([anchor, title, value, color], i) => callout(anchor, [880, 120 + i * step], title, value, color, "end"))}
      ${hasGrid
        ? callout(this._poleTop(), [24, 250], t("Netz"),
            `${imported >= exported ? (imported > ACTIVE_W ? "↓ " : "") : "↑ "}${formatPower(Math.abs(live.grid_w), displays.grid)}`, COLOR.grid, "start")
        : nothing}
      ${callout(iso(4.5, 8, 4.0), [24, 420], t("Haus"), formatPower(homeBase, displays.load), COLOR.home, "start")}
      ${bottom.map(([anchor, title, value, color], i) => callout(anchor, [112 + i * 160, 668], title, value, color))}
    `;
  }

  static styles = css`
    :host { display: block; }
    .scene { width: 100%; max-width: 980px; height: auto; max-height: max(300px, calc(100dvh - 300px)); display: block; margin: 0 auto; }
    .sky-in { stop-color: #e9f2fb; }
    .sky-out { stop-color: #c7dbef; }
    .night .sky-in { stop-color: #1f2d4d; }
    .night .sky-out { stop-color: #0d1529; }
    .ground { fill: #d9e4d0; stroke: #b7c7aa; stroke-width: 1.5; }
    .night .ground { fill: #2b3a33; stroke: #3c4d44; }
    .wall.front { fill: #f7f5f1; stroke: #c9c2b5; stroke-width: 1.2; }
    .wall.side { fill: #e4ded3; stroke: #c9c2b5; stroke-width: 1.2; }
    .night .wall.front { fill: #c9c4ba; }
    .night .wall.side { fill: #aaa59c; }
    .roof.front, .roof.back { fill: #59606b; stroke: #3d434c; stroke-width: 1.2; stroke-linejoin: round; }
    .roof.back { fill: #474d57; }
    .roof.flat { fill: #8b929c; stroke: #6c737d; stroke-width: 1.2; }
    .panel { fill: url(#iso-panel); stroke: #a9c0e0; stroke-width: 0.8; }
    .panels.north .panel { opacity: 0.85; }
    .shine .panel { fill: #a8d8ff; stroke: none; mix-blend-mode: screen; animation: shimmer 3.5s ease-in-out infinite; }
    @keyframes shimmer { 50% { opacity: 0.4; } }
    .window { fill: #6f7f93; stroke: #9c9283; stroke-width: 1.5; }
    .window.big { fill: #50606f; }
    .window.lit { fill: #f6d98a; }
    .door { fill: #8a6a4a; }
    .meter { fill: #fff; stroke: var(--sf-series-1); stroke-width: 2; }
    .app-body { fill: #f2f4f7; stroke: #57606a; stroke-width: 1.5; }
    .app-door { fill: #b9c6d4; stroke: #57606a; stroke-width: 1.2; }
    .app-door.spin { stroke-dasharray: 6 4; animation: turn 1.4s linear infinite; transform-box: fill-box; transform-origin: center; }
    @keyframes turn { to { transform: rotate(360deg); } }
    .app-panel { fill: #57606a; }
    .tank-heat { fill: var(--sf-series-5); }
    .burner { stroke: #57606a; stroke-width: 3; stroke-linecap: round; }
    .burner.hot { stroke: #e8553d; animation: pulse 1.2s ease-in-out infinite; }
    .box { fill: #f4f6f8; stroke: #8c959f; stroke-width: 1.2; }
    .box-side { fill: #d0d7de; stroke: #8c959f; stroke-width: 1.2; }
    .box-top { fill: #e6eaee; stroke: #8c959f; stroke-width: 1; }
    .display { fill: #24292f; }
    .inverter.active .display { fill: var(--sf-series-3); }
    .bat-front { fill: #fff; stroke: var(--sf-series-3); stroke-width: 1.6; }
    .bat-side { fill: #dfe7e3; stroke: var(--sf-series-3); stroke-width: 1.2; }
    .bat-top { fill: #eef3f0; stroke: var(--sf-series-3); stroke-width: 1; }
    .bat-fill { fill: var(--sf-series-3); }
    .battery.active .bat-fill { animation: pulse 1.6s ease-in-out infinite; }
    @keyframes pulse { 50% { opacity: 0.55; } }
    .post { stroke: #6e7781; stroke-width: 5; stroke-linecap: round; }
    .slab, .slab-side { fill: #6e7781; }
    .car-body.top { fill: #e6eaef; } .car-body.front { fill: #c4ccd5; } .car-body.side { fill: #aab4bf; }
    .car-cabin.top { fill: #9cc3e6; } .car-cabin.front { fill: #86b0d6; } .car-cabin.side { fill: #7aa3c8; }
    .car-body, .car-cabin { stroke: #57606a; stroke-width: 1; stroke-linejoin: round; }
    .wheel { fill: #24292f; }
    .car-level { fill: #fff; stroke: #57606a; stroke-width: 0.8; }
    .car-level-fill { fill: var(--sf-series-6); }
    .wallbox { fill: #fff; stroke: var(--sf-series-6); stroke-width: 2; }
    .wallbox.charging { fill: var(--sf-series-6); animation: pulse 1.2s ease-in-out infinite; }
    .hp.front { fill: #f4f6f8; } .hp.side { fill: #d0d7de; } .hp.top { fill: #e6eaee; }
    .hp { stroke: #8c959f; stroke-width: 1.2; }
    .fan { fill: #c9d1d9; stroke: #6e7781; stroke-width: 1.5; }
    .blades line { stroke: #57606a; stroke-width: 2.5; stroke-linecap: round; }
    .heatpump.on .blades { animation: turn 1s linear infinite; }
    .bike-wheel { fill: none; stroke: #24292f; stroke-width: 2.5; }
    .bike-frame { fill: none; stroke: #57606a; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
    .bike-battery { fill: #8c959f; }
    .bike.charging .bike-battery { fill: var(--sf-series-3); animation: pulse 1.2s ease-in-out infinite; }
    .night .bike-wheel { stroke: #c9d1d9; }
    .pole line { stroke: #6b4f36; stroke-width: 5; stroke-linecap: round; }
    .pole.off { opacity: 0.4; }
    .wire { fill: none; stroke-linecap: round; opacity: 0.35; }
    .wire.idle { stroke: #8c959f; stroke-width: 1.4; opacity: 0.4; stroke-dasharray: 3 4; }
    .spark { fill: none; stroke-linecap: round; stroke-dasharray: 1 12; animation: spark linear infinite; }
    @keyframes spark { to { stroke-dashoffset: -26; } }
    .leader { stroke: #8c959f; stroke-width: 1; stroke-dasharray: 2 3; }
    .dot { stroke: #fff; stroke-width: 1.5; }
    .c-title { font-size: 15px; fill: #57606a; }
    .c-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; paint-order: stroke; stroke: rgb(255 255 255 / 0.85); stroke-width: 3px; }
    .night .c-title { fill: #c9d1d9; }
    .night .c-value { stroke: rgb(13 21 41 / 0.85); }
    .night .leader { stroke: #6e7781; }
    /* The drawing scales down to about half on phones; larger text keeps it readable */
    @media (max-width: 600px) {
      .c-title { font-size: 19px; }
      .c-value { font-size: 28px; }
      .scene { max-height: none; }
    }
    @media (prefers-reduced-motion: reduce) {
      .shine .panel, .battery.active .bat-fill, .wallbox.charging, .heatpump.on .blades, .app-door.spin, .burner.hot,
      .bike.charging .bike-battery { animation: none; }
      .spark { animation: none; stroke-dasharray: none; opacity: 0.8; }
    }
  `;
}

customElements.define("sf-iso-scene", SunflowIsoScene);
