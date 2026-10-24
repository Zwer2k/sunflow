import { svg, nothing } from "../vendor/lit-all.min.js";

// Validated categorical slots (light/dark) are CSS variables in base.css; SVG presentation
// attributes do not resolve var(), so colors are applied through style
export const SERIES_COLORS = [1, 2, 3, 4, 5, 6].map((i) => `var(--sf-series-${i})`);
export const UNASSIGNED_COLOR = "var(--sf-series-neutral)";

const W = 720;
const H = 200;
const PAD = { top: 12, right: 12, bottom: 28, left: 48 };
const timeFormat = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayFormat = new Intl.DateTimeFormat("de-DE", { weekday: "short" });

function niceMax(value) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((m) => m * magnitude).find((m) => m >= value);
}

function timeScale(start, end) {
  const t0 = start.getTime();
  const span = end.getTime() - t0 || 1;
  return (date) => PAD.left + ((date.getTime() - t0) / span) * (W - PAD.left - PAD.right);
}

function frame(start, end, yMax, formatY) {
  const x = timeScale(start, end);
  const y = (v) => H - PAD.bottom - (v / yMax) * (H - PAD.top - PAD.bottom);
  const ticks = [0, yMax / 2, yMax];
  const hours = [];
  const first = new Date(start);
  first.setMinutes(0, 0, 0);
  for (let t = new Date(first); t <= end; t = new Date(t.getTime() + 3600_000)) {
    if (t >= start && t.getHours() % 6 === 0) hours.push(t);
  }
  return {
    x,
    y,
    axes: svg`
      ${ticks.map(
        (v) => svg`
          <line x1=${PAD.left} x2=${W - PAD.right} y1=${y(v)} y2=${y(v)} class="grid"></line>
          <text x=${PAD.left - 6} y=${y(v) + 4} text-anchor="end" class="axis">${formatY(v)}</text>
        `,
      )}
      ${hours.map(
        (t) => svg`
          <line x1=${x(t)} x2=${x(t)} y1=${H - PAD.bottom} y2=${H - PAD.bottom + 4} class="grid"></line>
          <text x=${x(t)} y=${H - 8} text-anchor="middle" class="axis">
            ${t.getHours() === 0 ? dayFormat.format(t) : timeFormat.format(t)}
          </text>
        `,
      )}
    `,
  };
}

/** Step line of prices; points: [{start, end, price}] in €/kWh, shown in ct/kWh. */
export function priceChart(points, label) {
  if (!points.length) return nothing;
  const parsed = points.map((p) => ({ start: new Date(p.start), end: new Date(p.end), ct: p.price * 100 }));
  const start = parsed[0].start;
  const end = parsed.at(-1).end;
  const yMax = niceMax(Math.max(...parsed.map((p) => p.ct)) * 1.1);
  const { x, y, axes } = frame(start, end, yMax, (v) => `${Math.round(v)} ct`);
  const path = parsed.map((p, i) => `${i ? "L" : "M"}${x(p.start)},${y(p.ct)} L${x(p.end)},${y(p.ct)}`).join(" ");
  return svg`
    <svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label=${label}>
      ${axes}
      <path d=${path} fill="none" style="stroke: ${SERIES_COLORS[0]}" stroke-width="2" stroke-linejoin="round"></path>
      ${parsed.map(
        (p) => svg`<rect x=${x(p.start)} y=${PAD.top} width=${Math.max(x(p.end) - x(p.start), 1)}
          height=${H - PAD.top - PAD.bottom} class="hit">
          <title>${timeFormat.format(p.start)}–${timeFormat.format(p.end)}: ${p.ct.toFixed(1).replace(".", ",")} ct/kWh</title>
        </rect>`,
      )}
    </svg>
  `;
}

/**
 * Lines over time; series: [{name, color, dashed?, points: [{start, end, value}]}].
 * Points with value null leave a gap. `now` draws a marker separating measured
 * values (left) from forecasts (right).
 */
export function lineChart(series, { label, formatY, yMax: fixedMax, now = null }) {
  const all = series.flatMap((s) => s.points).filter((p) => p.value !== null && p.value !== undefined);
  if (!all.length) return nothing;
  const start = new Date(Math.min(...series.flatMap((s) => s.points.map((p) => new Date(p.start)))));
  const end = new Date(Math.max(...series.flatMap((s) => s.points.map((p) => new Date(p.end)))));
  const yMax = fixedMax ?? niceMax(Math.max(...all.map((p) => p.value)) * 1.1);
  const { x, y, axes } = frame(start, end, yMax, formatY);
  const pathOf = (points) => {
    let pen = "M";
    return points
      .map((p) => {
        if (p.value === null || p.value === undefined) {
          pen = "M";
          return "";
        }
        const segment = `${pen}${(x(new Date(p.start)) + x(new Date(p.end))) / 2},${y(p.value)}`;
        pen = "L";
        return segment;
      })
      .join(" ");
  };
  // One hover target per distinct interval, listing every series that has a value there
  const slots = new Map();
  for (const s of series) {
    for (const p of s.points) {
      if (p.value === null || p.value === undefined) continue;
      const slot = slots.get(p.start) ?? { start: new Date(p.start), end: new Date(p.end), values: [] };
      slot.values.push(`${s.name} ${formatY(p.value)}`);
      slots.set(p.start, slot);
    }
  }
  const nowX = now ? x(now) : null;
  return svg`
    <svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label=${label}>
      ${axes}
      ${nowX !== null && nowX > PAD.left && nowX < W - PAD.right
        ? svg`
            <line x1=${nowX} x2=${nowX} y1=${PAD.top} y2=${H - PAD.bottom} class="now"></line>
            <text x=${nowX - 4} y=${PAD.top + 10} text-anchor="end" class="axis">gemessen</text>
            <text x=${nowX + 4} y=${PAD.top + 10} text-anchor="start" class="axis">Prognose</text>
          `
        : nothing}
      ${series.map(
        (s) => svg`<path d=${pathOf(s.points)} fill="none" style="stroke: ${s.color}" stroke-width="2"
          stroke-dasharray=${s.dashed ? "5 4" : "none"} stroke-linejoin="round" stroke-linecap="round"></path>`,
      )}
      ${[...slots.values()].map(
        (slot) => svg`<rect x=${x(slot.start)} y=${PAD.top}
          width=${Math.max(x(slot.end) - x(slot.start), 1)} height=${H - PAD.top - PAD.bottom} class="hit">
          <title>${dayFormat.format(slot.start)} ${timeFormat.format(slot.start)}: ${slot.values.join(", ")}</title>
        </rect>`,
      )}
    </svg>
  `;
}

/** Stacked bars of PV forecasts; series: [{name, color, points: [{start, end, power}]}] in W. */
export function forecastChart(series, label) {
  const all = series.flatMap((s) => s.points.map((p) => ({ ...p, series: s })));
  if (!all.length) return nothing;
  const slots = new Map();
  for (const p of all) {
    const key = p.start;
    if (!slots.has(key)) slots.set(key, { start: new Date(p.start), end: new Date(p.end), parts: [] });
    slots.get(key).parts.push({ name: p.series.name, color: p.series.color, power: p.power });
  }
  const bars = [...slots.values()].sort((a, b) => a.start - b.start);
  const start = bars[0].start;
  const end = bars.at(-1).end;
  const yMax = niceMax(Math.max(...bars.map((b) => b.parts.reduce((s, p) => s + p.power, 0))) * 1.1);
  const kw = (v) => `${(v / 1000).toFixed(v >= 10000 || v === 0 ? 0 : 1).replace(".", ",")} kW`;
  const { x, y, axes } = frame(start, end, yMax, kw);
  return svg`
    <svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label=${label}>
      ${axes}
      ${bars.map((b) => {
        let base = 0;
        const x0 = x(b.start) + 0.5;
        const width = Math.max(x(b.end) - x(b.start) - 1, 1);
        const total = b.parts.reduce((s, p) => s + p.power, 0);
        return svg`
          <g>
            ${b.parts.map((p) => {
              const top = y(base + p.power);
              const height = y(base) - top;
              base += p.power;
              return height > 0.5
                ? svg`<rect x=${x0} y=${top} width=${width} height=${Math.max(height - 1, 0.5)} style="fill: ${p.color}"></rect>`
                : nothing;
            })}
            <rect x=${x0} y=${PAD.top} width=${width} height=${H - PAD.top - PAD.bottom} class="hit">
              <title>${timeFormat.format(b.start)}: ${b.parts.map((p) => `${p.name} ${kw(p.power)}`).join(", ")} – gesamt ${kw(total)}</title>
            </rect>
          </g>
        `;
      })}
    </svg>
  `;
}
