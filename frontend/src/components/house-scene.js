import { LitElement, svg, css, nothing } from "../../vendor/lit-all.min.js";
import { formatPower } from "../lib/format.js";
import { t } from "../lib/i18n.js";

const ACTIVE_W = 20;
const COLOR = {
  pv: "var(--sf-series-2)",
  grid: "var(--sf-series-1)",
  battery: "var(--sf-series-3)",
  home: "var(--sf-series-4)",
  heater: "var(--sf-series-5)",
  vehicle: "var(--sf-series-6)",
};
// Where the flows meet: the inverter in the house
const HUB = [262, 286];

const clamp = (v, low, high) => Math.min(Math.max(v, low), high);

/** The site as a small animated house: sun and panels, grid pole, battery, hot water and carport. */
class SunflowHouseScene extends LitElement {
  static properties = { live: { attribute: false }, data: { attribute: false } };

  render() {
    const live = this.live;
    const data = this.data;
    if (!live || !data) return nothing;
    const { battery, flows, homeBase, displays, pvDisplay, hasGrid } = data;
    const pv = Math.max(live.pv_w ?? 0, 0);
    const hour = new Date(live.time).getHours();
    const day = pv > ACTIVE_W || (hour >= 7 && hour < 18);
    const cloud = live.weather?.cloud ?? null;
    const vehicle = live.consumers.find((c) => c.kind === "vehicle");
    const heater = live.consumers.find((c) => c.kind === "heater");
    const imported = Math.max(live.grid_w ?? 0, 0);
    const exported = Math.max(-(live.grid_w ?? 0), 0);
    const charge = Math.max(battery?.power_w ?? 0, 0);
    const discharge = Math.max(-(battery?.power_w ?? 0), 0);
    return svg`<svg viewBox="0 0 640 380" class="scene ${day ? "day" : "night"}" role="img"
      aria-label=${t("Energiefluss: {summary}", { summary: data.summary })}>
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" class="sky-top"></stop><stop offset="1" class="sky-bottom"></stop>
        </linearGradient>
        <linearGradient id="panel" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#2b4a7a"></stop><stop offset="1" stop-color="#16294a"></stop>
        </linearGradient>
        <clipPath id="tank"><rect x="398" y="246" width="30" height="88" rx="14"></rect></clipPath>
      </defs>
      <rect x="0" y="0" width="640" height="340" fill="url(#sky)"></rect>
      ${day ? this._sun(pv) : this._moon()}
      ${cloud !== null && cloud >= 40 ? this._clouds(cloud) : nothing}
      <rect x="0" y="338" width="640" height="42" class="ground"></rect>

      ${this._pole(hasGrid)}
      ${this._house(pv, homeBase)}
      ${battery ? this._battery(battery) : nothing}
      ${heater ? this._tank(heater) : nothing}
      ${vehicle ? this._carport(vehicle) : nothing}
      <rect x=${HUB[0] - 14} y=${HUB[1] - 12} width="28" height="24" rx="4" class="hub"></rect>
      <text x=${HUB[0]} y=${HUB[1] + 4} class="hub-label">WR</text>

      ${this._flow("M352,170 C340,220 300,250 " + `${HUB[0] + 14},${HUB[1] - 6}`, pv, COLOR.pv)}
      ${hasGrid
        ? imported > exported
          ? this._flow(`M40,128 Q130,190 214,224 L${HUB[0] - 14},${HUB[1] - 6}`, imported, COLOR.grid)
          : this._flow(`M${HUB[0] - 14},${HUB[1] - 6} L214,224 Q130,190 40,128`, exported, COLOR.pv)
        : nothing}
      ${battery
        ? charge > discharge
          ? this._flow(`M${HUB[0] - 14},${HUB[1] + 6} L232,300`, charge, flows.gridToBattery > flows.pvToBattery ? COLOR.grid : COLOR.pv)
          : this._flow(`M232,300 L${HUB[0] - 14},${HUB[1] + 6}`, discharge, COLOR.battery)
        : nothing}
      ${this._flow(`M${HUB[0] + 14},${HUB[1]} C300,286 310,262 322,252`, homeBase, COLOR.home)}
      ${heater ? this._flow(`M${HUB[0] + 14},${HUB[1] + 6} C330,300 370,300 398,296`, heater.power_w, COLOR.heater) : nothing}
      ${vehicle ? this._flow(`M${HUB[0] + 14},${HUB[1] + 10} C360,330 430,330 488,300`, vehicle.power_w, COLOR.vehicle) : nothing}

      ${this._label(352, 92, "PV", formatPower(pv, pvDisplay), COLOR.pv)}
      ${hasGrid
        ? this._label(48, 196, t("Netz"), `${imported > exported ? "↓" : exported > ACTIVE_W ? "↑" : ""} ${formatPower(Math.abs(live.grid_w), displays.grid)}`, COLOR.grid)
        : nothing}
      ${this._label(320, 362, t("Haus"), formatPower(homeBase, displays.load), COLOR.home)}
      ${battery
        ? this._label(178, 362, battery.soc_pct === null ? t("Batterie") : `${t("Batterie")} ${Math.round(battery.soc_pct)} %`,
            `${charge > discharge ? "+" : discharge > ACTIVE_W ? "−" : ""}${formatPower(Math.abs(battery.power_w ?? 0), battery.display)}`, COLOR.battery)
        : nothing}
      ${heater
        ? this._label(450, 236, heater.level !== null && heater.level !== undefined ? `${Math.round(heater.level)} °C` : heater.name,
            formatPower(heater.power_w ?? 0, heater.display), COLOR.heater)
        : nothing}
      ${vehicle
        ? this._label(566, 362, vehicle.connected === false ? t("kein Auto") : vehicle.level !== null && vehicle.level !== undefined
            ? `${vehicle.vehicle ?? t("Auto")} ${Math.round(vehicle.level)} %` : (vehicle.vehicle ?? vehicle.name),
          formatPower(vehicle.power_w ?? 0, vehicle.display), COLOR.vehicle)
        : nothing}
    </svg>`;
  }

  /** An animated flow along a path from source to target; thicker and faster with more power. */
  _flow(path, power, color) {
    if ((power ?? 0) <= ACTIVE_W) return svg`<path d=${path} class="wire"></path>`;
    const width = 2 + 5 * clamp(power / 6000, 0, 1);
    const duration = clamp(2.2 - power / 3000, 0.45, 2.2);
    return svg`<path d=${path} class="wire" style="stroke: ${color}; stroke-width: ${width}; opacity: 0.3"></path>
      <path d=${path} class="spark" style="stroke: ${color}; stroke-width: ${width + 1}; animation-duration: ${duration}s">
        <title>${formatPower(power)}</title></path>`;
  }

  _label(x, y, title, value, color) {
    return svg`<g class="label"><text x=${x} y=${y - 18} class="label-title">${title}</text>
      <text x=${x} y=${y} class="label-value" style="fill: ${color}">${value}</text></g>`;
  }

  _sun(pv) {
    // Brighter and bigger with more PV
    const glow = clamp(pv / 4000, 0.15, 1);
    return svg`<g class="sun" style="opacity: ${0.55 + 0.45 * glow}">
      <g class="rays" style="animation-duration: ${clamp(30 - glow * 22, 8, 30)}s">
        ${Array.from({ length: 12 }, (_, i) => svg`<line x1="90" y1="${70 - 34 - 8 * glow}" x2="90" y2="${70 - 30}"
          transform="rotate(${i * 30} 90 70)"></line>`)}
      </g>
      <circle cx="90" cy="70" r="${20 + 4 * glow}" class="sun-disc"></circle>
    </g>`;
  }

  _moon() {
    return svg`<g class="moon">
      ${[[60, 40], [150, 30], [230, 70], [520, 40], [600, 90], [420, 30]].map(([x, y], i) => svg`<circle cx=${x} cy=${y} r="1.6"
        class="star" style="animation-delay: ${i * 0.7}s"></circle>`)}
      <path d="M100,46 a26,26 0 1,0 22,40 a20,20 0 1,1 -22,-40z" class="moon-disc"></path>
    </g>`;
  }

  _clouds(cloud) {
    const opacity = 0.35 + 0.5 * clamp((cloud - 40) / 60, 0, 1);
    const puff = (x, y, s) => svg`<g transform="translate(${x} ${y}) scale(${s})">
      <ellipse cx="0" cy="0" rx="34" ry="14"></ellipse><ellipse cx="-18" cy="-8" rx="18" ry="14"></ellipse>
      <ellipse cx="12" cy="-12" rx="22" ry="17"></ellipse></g>`;
    return svg`<g class="clouds" style="opacity: ${opacity}">${puff(150, 74, 1)}${puff(470, 54, 0.8)}${cloud > 75 ? puff(330, 40, 1.1) : nothing}</g>`;
  }

  _pole(hasGrid) {
    return svg`<g class="pole ${hasGrid ? "" : "off"}">
      <line x1="40" y1="120" x2="40" y2="338"></line><line x1="22" y1="128" x2="58" y2="128"></line>
      <line x1="26" y1="146" x2="54" y2="146"></line>
    </g>`;
  }

  _house(pv, homeBase) {
    const lit = homeBase > 150;
    const shine = clamp(pv / 3000, 0, 1);
    // Panels on the right slope of the roof, 3 x 2
    const panels = [];
    for (let row = 0; row < 2; row++) {
      for (let col = 0; col < 3; col++) {
        const x = 336 + col * 28 + row * 14;
        const y = 132 + row * 22 + col * 15;
        panels.push(svg`<polygon points="${x},${y} ${x + 26},${y + 14} ${x + 39},${y + 34} ${x + 13},${y + 20}" class="panel"></polygon>`);
      }
    }
    return svg`<g class="house">
      <rect x="200" y="204" width="240" height="134" class="wall"></rect>
      <polygon points="186,208 320,112 454,208" class="roof"></polygon>
      ${panels}
      <g class="panel-shine" style="opacity: ${shine}">${panels}</g>
      <rect x="296" y="226" width="48" height="40" rx="3" class="window ${lit ? "lit" : ""}"></rect>
      <line x1="320" y1="226" x2="320" y2="266" class="frame"></line><line x1="296" y1="246" x2="344" y2="246" class="frame"></line>
      <rect x="350" y="282" width="30" height="56" rx="2" class="door"></rect>
    </g>`;
  }

  _battery(battery) {
    const level = clamp((battery.soc_pct ?? 0) / 100, 0, 1);
    const active = Math.abs(battery.power_w ?? 0) > ACTIVE_W;
    return svg`<g class="battery ${active ? "active" : ""}">
      <rect x="212" y="292" width="34" height="44" rx="4" class="battery-shell"></rect>
      <rect x="216" y="${296 + 36 * (1 - level)}" width="26" height="${36 * level}" rx="2" class="battery-fill"></rect>
      <rect x="224" y="288" width="10" height="4" rx="1" class="battery-cap"></rect>
    </g>`;
  }

  _tank(heater) {
    // Cold blue to hot red over 20 to 70 °C
    const temp = heater.level ?? 40;
    const warm = clamp((temp - 20) / 50, 0, 1);
    const heating = (heater.power_w ?? 0) > ACTIVE_W;
    return svg`<g class="tank ${heating ? "heating" : ""}">
      <rect x="398" y="246" width="30" height="88" rx="14" class="tank-shell"></rect>
      <rect x="398" y="${246 + 88 * (1 - warm)}" width="30" height="${88 * warm}" clip-path="url(#tank)"
        style="fill: color-mix(in srgb, #e8553d ${Math.round(warm * 100)}%, #4a90e2); opacity: 0.8"></rect>
      ${heating ? svg`<path d="M406,240 q4,-8 0,-14 M414,240 q4,-8 0,-14 M422,240 q4,-8 0,-14" class="steam"></path>` : nothing}
    </g>`;
  }

  _carport(vehicle) {
    const there = vehicle.connected !== false;
    const level = clamp((vehicle.level ?? 0) / 100, 0, 1);
    const charging = (vehicle.power_w ?? 0) > ACTIVE_W;
    return svg`<g class="carport">
      <polygon points="470,232 632,222 632,230 470,240" class="carport-roof"></polygon>
      <line x1="474" y1="240" x2="474" y2="338" class="post"></line><line x1="626" y1="230" x2="626" y2="338" class="post"></line>
      <rect x="480" y="284" width="16" height="26" rx="3" class="wallbox ${charging ? "charging" : ""}"></rect>
      ${there
        ? svg`<g class="car ${charging ? "charging" : ""}">
            <path d="M506,330 v-16 l14,-16 h58 l18,16 h12 v16 z" class="car-body"></path>
            <path d="M524,300 h22 v12 h-34 z M552,300 h22 l12,12 h-34 z" class="car-window"></path>
            <circle cx="530" cy="332" r="9" class="wheel"></circle><circle cx="590" cy="332" r="9" class="wheel"></circle>
            <rect x="530" y="318" width="50" height="5" rx="2" class="car-level"></rect>
            <rect x="530" y="318" width="${50 * level}" height="5" rx="2" class="car-level-fill"></rect>
          </g>`
        : nothing}
    </g>`;
  }

  static styles = css`
    :host { display: block; }
    .scene { width: 100%; max-width: 900px; height: auto; max-height: max(260px, calc(100dvh - 340px)); display: block; margin: 0 auto; border-radius: 14px; }
    .sky-top { stop-color: #8ec5f2; }
    .sky-bottom { stop-color: #e8f3fc; }
    .night .sky-top { stop-color: #0f1a33; }
    .night .sky-bottom { stop-color: #2a3a5c; }
    .ground { fill: #7cae5f; }
    .night .ground { fill: #2f4a2a; }
    .sun-disc { fill: #ffc93c; }
    .rays { transform-origin: 90px 70px; animation: spin linear infinite; }
    .rays line { stroke: #ffc93c; stroke-width: 4; stroke-linecap: round; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .moon-disc { fill: #f4f1de; }
    .star { fill: #fff; animation: twinkle 3s ease-in-out infinite; }
    @keyframes twinkle { 50% { opacity: 0.2; } }
    .clouds { fill: #fff; animation: drift 40s ease-in-out infinite alternate; }
    .night .clouds { fill: #6b7a96; }
    @keyframes drift { to { transform: translateX(40px); } }
    .pole line { stroke: #6b4f36; stroke-width: 5; stroke-linecap: round; }
    .pole.off { opacity: 0.4; }
    .wall { fill: #f3ede2; stroke: #c9bba3; stroke-width: 2; }
    .night .wall { fill: #cfc6b6; }
    .roof { fill: #a8493a; stroke: #7c3328; stroke-width: 2; stroke-linejoin: round; }
    .panel { fill: url(#panel); stroke: #9fb4d4; stroke-width: 1; }
    .panel-shine .panel { fill: #9fd3ff; stroke: none; mix-blend-mode: screen; animation: shimmer 3.5s ease-in-out infinite; }
    @keyframes shimmer { 50% { opacity: 0.45; } }
    .window { fill: #5b6b80; stroke: #8a7a63; stroke-width: 2; transition: fill 0.6s; }
    .window.lit { fill: #ffd97a; filter: drop-shadow(0 0 6px #ffcf5c); }
    .frame { stroke: #8a7a63; stroke-width: 2; }
    .door { fill: #8a6a4a; }
    .hub { fill: #e4e7eb; stroke: #8c959f; stroke-width: 1.5; }
    .hub-label { font-size: 9px; font-weight: 700; text-anchor: middle; fill: #57606a; }
    .battery-shell { fill: #fff; stroke: var(--sf-series-3); stroke-width: 2.5; }
    .battery-fill { fill: var(--sf-series-3); transition: y 1s, height 1s; }
    .battery-cap { fill: var(--sf-series-3); }
    .battery.active .battery-fill { animation: pulse 1.6s ease-in-out infinite; }
    @keyframes pulse { 50% { opacity: 0.65; } }
    .tank-shell { fill: #fff; stroke: var(--sf-series-5); stroke-width: 2.5; }
    .steam { fill: none; stroke: #c9d1d9; stroke-width: 2; stroke-linecap: round; animation: rise 2s ease-in-out infinite; }
    @keyframes rise { 0% { opacity: 0; transform: translateY(6px); } 50% { opacity: 1; } 100% { opacity: 0; transform: translateY(-8px); } }
    .carport-roof { fill: #6e7781; }
    .post { stroke: #6e7781; stroke-width: 4; }
    .wallbox { fill: #fff; stroke: var(--sf-series-6); stroke-width: 2.5; }
    .wallbox.charging { fill: var(--sf-series-6); animation: pulse 1.2s ease-in-out infinite; }
    .car-body { fill: #d0d7de; stroke: #57606a; stroke-width: 2; stroke-linejoin: round; }
    .car.charging .car-body { fill: #e3dcff; }
    .car-window { fill: #9cc3e6; }
    .wheel { fill: #24292f; }
    .car-level { fill: #fff; stroke: #57606a; stroke-width: 1; }
    .car-level-fill { fill: var(--sf-series-6); }
    .wire { fill: none; stroke: #8c959f; stroke-width: 1.5; stroke-linecap: round; opacity: 0.45; }
    .spark { fill: none; stroke-linecap: round; stroke-dasharray: 1 13; animation: spark linear infinite; filter: drop-shadow(0 0 3px currentColor); }
    @keyframes spark { to { stroke-dashoffset: -28; } }
    .label-title { font-size: 14px; text-anchor: middle; fill: #24292f; paint-order: stroke; stroke: rgb(255 255 255 / 0.85); stroke-width: 3px; }
    .label-value { font-size: 18px; font-weight: 700; text-anchor: middle; font-variant-numeric: tabular-nums;
      paint-order: stroke; stroke: rgb(255 255 255 / 0.9); stroke-width: 3.5px; }
    .night .label-title { fill: #f0f3f6; stroke: rgb(15 26 51 / 0.85); }
    .night .label-value { stroke: rgb(15 26 51 / 0.9); }
    @media (prefers-reduced-motion: reduce) {
      .rays, .star, .clouds, .panel-shine .panel, .battery.active .battery-fill, .steam, .wallbox.charging { animation: none; }
      .spark { animation: none; stroke-dasharray: none; opacity: 0.8; }
    }
  `;
}

customElements.define("sf-house-scene", SunflowHouseScene);
