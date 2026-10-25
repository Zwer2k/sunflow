import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { poll } from "./poll.js";
import { lineChart, priceChart, SERIES_COLORS, UNASSIGNED_COLOR } from "./charts.js";
import { formatValue, toDisplay } from "./format.js";

const ACTIONS = {
  auto: { label: "Eigenverbrauch", hint: "Speicher lädt aus PV-Überschuss und deckt den Bedarf" },
  hold: { label: "Speicher halten", hint: "Speicher wird geschont, Bedarf kommt aus dem Netz" },
  force_charge: { label: "Laden aus dem Netz", hint: "Speicher wird gezielt geladen" },
  force_discharge: { label: "Entladen ins Netz", hint: "Speicher speist gezielt ein" },
  limit_discharge: { label: "Entladen begrenzt", hint: "Speicher deckt den Bedarf nur teilweise, der Rest kommt aus dem Netz" },
  limit_charge: { label: "Laden begrenzt", hint: "Speicher nimmt nur einen Teil des PV-Überschusses auf, der Rest wird eingespeist" },
};
const LOAD_QUALITY = {
  day_type: "nach Wochentag",
  all_days: "Mittel aller Tage",
  mixed: "teils nach Wochentag",
  default: "Standardwert",
};
const REFRESH_MS = 5 * 60 * 1000;

const time = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayTime = new Intl.DateTimeFormat("de-DE", { weekday: "short", hour: "2-digit", minute: "2-digit" });
const euro = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });

/** Energy in Wh of a power column over 15 minute slots. */
function energy(slots, key) {
  return slots.reduce((sum, s) => sum + s[key] / 4, 0);
}

/** Consecutive slots in which a flexible load runs, with energy and main source. */
function flexWindows(slots, name) {
  const windows = [];
  for (const slot of slots) {
    const power = slot.flex_w?.[name] ?? 0;
    const last = windows.at(-1);
    if (power < 50) {
      if (last) last.closed = true;
      continue;
    }
    const surplus = Math.max(slot.pv_w - slot.load_w, 0);
    if (last && !last.closed) {
      last.end = slot.end;
      last.energy += power / 4;
      last.pv += Math.min(power, surplus) / 4;
    } else {
      windows.push({ start: slot.start, end: slot.end, energy: power / 4, pv: Math.min(power, surplus) / 4, closed: false });
    }
  }
  return windows.map((w) => ({
    ...w,
    source: w.pv >= w.energy * 0.8 ? "aus PV-Überschuss" : w.pv > 0 ? "teils PV, teils Netz/Speicher" : "aus Netz/Speicher",
  }));
}

/** Group consecutive slots with the same action. */
/** Reference lines for the SoC chart: full, backup reserve (if active) and discharge limit. */
function socLimits(battery) {
  if (!battery) return [];
  const pct = (wh) => `${Math.round((wh / battery.capacity_wh) * 100)} %`;
  const limits = [{ value: battery.capacity_wh, label: `voll · ${formatValue(battery.capacity_wh, "Wh")}` }];
  if (battery.reserve_wh > battery.min_soc_wh) {
    limits.push({ value: battery.reserve_wh, label: `Notstromreserve ${pct(battery.reserve_wh)}` });
  }
  if (battery.min_soc_wh > 0) {
    limits.push({ value: battery.min_soc_wh, label: `Entladegrenze ${pct(battery.min_soc_wh)}` });
  }
  return limits;
}

function segments(slots) {
  const result = [];
  for (const slot of slots) {
    const last = result.at(-1);
    if (last && last.action === slot.action) {
      last.end = slot.end;
      last.slots.push(slot);
    } else {
      result.push({ action: slot.action, start: slot.start, end: slot.end, slots: [slot] });
    }
  }
  return result;
}

class SunflowPlan extends LitElement {
  static properties = {
    _plan: { state: true },
    _loading: { state: true },
    _error: { state: true },
    _history: { state: true },
    _historyHours: { state: true },
  };

  constructor() {
    super();
    this._plan = null;
    this._loading = false;
    this._error = null;
    this._history = null;
    this._historyHours = 24;
    this._timer = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stopPolling = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling();
  }

  async _load(fresh = false) {
    this._loading = true;
    try {
      [this._plan, this._history] = await Promise.all([
        fresh ? api.plan() : api.latestPlan(),
        api.history(this._historyHours),
      ]);
      this._error = null;
    } catch (err) {
      this._error = `Plan konnte nicht berechnet werden: ${err.message}`;
    } finally {
      this._loading = false;
    }
  }

  render() {
    const plan = this._plan;
    return html`
      <div class="heading">
        <div>
          <h1>Planung</h1>
          <p class="muted">Links von „jetzt“ gemessene Werte, rechts Prognosen und die geplante Steuerung.
            Sunflow steuert noch nichts – die Planung zeigt, was es tun würde. Eingriffe im Tab „Steuerung“,
            Voreinstellungen unter „Einstellungen → Standards“.</p>
        </div>
        <button class="secondary" ?disabled=${this._loading} @click=${() => this._load(true)}>
          ${this._loading ? "Berechne …" : "Neu berechnen"}
        </button>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${plan === null && !this._error ? html`<p class="muted">Berechne Plan …</p>` : nothing}
      ${plan ? this._renderPlan(plan) : nothing}
    `;
  }

  _renderPlan(plan) {
    const slots = plan.slots;
    if (!slots.length) {
      return html`${plan.warnings.map((w) => html`<div class="alert warn">⚠ ${w}</div>`)}
        <p class="muted">Kein Plan möglich (${plan.status}).</p>`;
    }
    const segs = segments(slots);
    const now = segs[0];
    const history = this._history?.points ?? [];
    const capacity =
      plan.battery?.capacity_wh || Math.max(...slots.map((s) => s.soc_wh), ...history.map((p) => p.soc_wh ?? 0)) || 1;
    const nowDate = new Date(slots[0].start);
    const hasLoadpoints =
      history.some((p) => p.loadpoint_w !== null && p.loadpoint_w !== undefined) || Boolean(plan.flex?.length);
    return html`
      ${plan.warnings.map((w) => html`<div class="alert warn">⚠ ${w}</div>`)}
      <div class="tiles">
        <div class="tile now">
          <div class="small muted">Geplante Steuerung jetzt (bis ${time.format(new Date(now.end))})</div>
          <div class="value">${ACTIONS[now.action].label}</div>
          <div class="small muted">${ACTIONS[now.action].hint}</div>
        </div>
        <div class="tile">
          <div class="small muted">Erwartete Ersparnis (nächste ${Math.round(slots.length / 4)} h)</div>
          <div class="value">${euro.format(plan.savings ?? 0)}</div>
          <div class="small muted">gegenüber reinem Eigenverbrauch</div>
        </div>
        <div class="tile">
          <div class="small muted">${plan.alternative_cost || plan.baseline_alternative_cost ? "Erwartete Energiekosten" : "Erwartete Stromkosten"}</div>
          <div class="value">${euro.format(plan.cost)}</div>
          <div class="small muted">ohne Sunflow ${euro.format(plan.baseline_cost)}${plan.alternative_cost || plan.baseline_alternative_cost
            ? html` · inkl. Ersatzheizung ${euro.format(plan.alternative_cost)} bzw. ${euro.format(plan.baseline_alternative_cost)}`
            : nothing}</div>
        </div>
        <div class="tile">
          <div class="small muted">Erwarteter Ladestand am Ende</div>
          <div class="value">${formatValue(slots.at(-1).soc_wh, "Wh")}</div>
          <div class="small muted">${dayTime.format(new Date(slots.at(-1).end))}</div>
        </div>
      </div>

      <section>
        <h2>Geplante Steuerung <span class="tag">Vorschau</span></h2>
        <ol class="timeline">
          ${segs.map(
            (s) => html`
              <li class="seg ${s.action}">
                <span class="when">${dayTime.format(new Date(s.start))} – ${time.format(new Date(s.end))}</span>
                <span class="what">${ACTIONS[s.action].label}${s.slots[0].action_power_w
                  ? html` · ${formatValue(s.slots[0].action_power_w, "W")}`
                  : nothing}</span>
                <span class="soc small muted">Speicher ${formatValue(s.slots[0].soc_wh, "Wh")} → ${formatValue(s.slots.at(-1).soc_wh, "Wh")}</span>
              </li>
            `,
          )}
        </ol>
      </section>

      ${plan.flex?.length ? this._renderFlex(plan) : nothing}

      <section>
        <div class="section-head">
          <h2>Verlauf und Prognose</h2>
          <div class="range" role="group" aria-label="Zeitraum zurück">
            ${[24, 48, 168].map(
              (h) => html`<button class=${this._historyHours === h ? "active" : ""} aria-pressed=${this._historyHours === h}
                @click=${() => this._setHistoryHours(h)}>${h === 168 ? "7 Tage" : `${h} h`}</button>`,
            )}
          </div>
        </div>
        <div class="legend">
          <span class="legend-item"><span class="line solid"></span>gemessen</span>
          <span class="legend-item"><span class="line dashed"></span>Prognose / Planung</span>
        </div>
      </section>

      <section>
        <h3>Ladestand</h3>
        ${lineChart(
          [
            { name: "Gemessen", color: SERIES_COLORS[0], points: this._past("soc_wh") },
            { name: "Erwartet", color: SERIES_COLORS[0], dashed: true, points: this._future(slots, "soc_wh") },
          ],
          {
            label: "Ladestand gemessen und erwartet",
            formatY: (v) => formatValue(v, "Wh"),
            yMax: capacity * 1.05,
            now: nowDate,
            limits: socLimits(plan.battery),
          },
        )}
      </section>

      <section>
        <h3>PV-Erzeugung und Hausverbrauch</h3>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[1]}"></span>PV-Erzeugung</span>
          ${hasLoadpoints
            ? html`<span class="legend-item"><span class="swatch" style="background:${UNASSIGNED_COLOR}"></span>Ladepunkte (evcc)</span>`
            : nothing}
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>Hausverbrauch
            <span class="muted">(Prognose: ${LOAD_QUALITY[plan.load_quality] ?? "–"}, ${plan.load_history_hours} h Historie${plan.load_scaled_to_kwh
              ? `, skaliert auf ${toDisplay(plan.load_scaled_to_kwh, "kWh")} kWh/Tag`
              : ""}${plan.load_separated_hours ? `, ohne Ladepunkte (${plan.load_separated_hours} h getrennt)` : ""})</span></span>
        </div>
        ${lineChart(
          [
            { name: "Verbrauch gemessen", color: SERIES_COLORS[0], points: this._past("load_w") },
            { name: "Verbrauch Prognose", color: SERIES_COLORS[0], dashed: true, points: this._future(slots, "load_w") },
            { name: "PV gemessen", color: SERIES_COLORS[1], points: this._past("pv_w") },
            ...(hasLoadpoints
              ? [{ name: "Ladepunkte gemessen", color: UNASSIGNED_COLOR, points: this._past("loadpoint_w") }]
              : []),
            ...(plan.flex?.length
              ? [{
                  name: "Ladepunkte geplant",
                  color: UNASSIGNED_COLOR,
                  dashed: true,
                  points: slots.map((s) => ({ start: s.start, end: s.end, value: Object.values(s.flex_w ?? {}).reduce((a, b) => a + b, 0) })),
                }]
              : []),
            { name: "PV Prognose", color: SERIES_COLORS[1], dashed: true, points: this._future(slots, "pv_w") },
          ],
          { label: "PV-Erzeugung und Hausverbrauch, gemessen und Prognose", formatY: (v) => formatValue(v, "W"), now: nowDate },
        )}
      </section>

      <section>
        <h3>Netzbezug und Einspeisung</h3>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>Netzbezug</span>
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[1]}"></span>Einspeisung</span>
          <span class="legend-item muted">erwartet: Bezug ${formatValue(energy(slots, "import_w"), "Wh")},
            Einspeisung ${formatValue(energy(slots, "export_w"), "Wh")}</span>
        </div>
        ${lineChart(
          [
            { name: "Bezug gemessen", color: SERIES_COLORS[0], points: this._pastGrid((g) => Math.max(g, 0)) },
            { name: "Bezug erwartet", color: SERIES_COLORS[0], dashed: true, points: this._future(slots, "import_w") },
            { name: "Einspeisung gemessen", color: SERIES_COLORS[1], points: this._pastGrid((g) => Math.max(-g, 0)) },
            { name: "Einspeisung erwartet", color: SERIES_COLORS[1], dashed: true, points: this._future(slots, "export_w") },
          ],
          { label: "Netzbezug und Einspeisung, gemessen und erwartet", formatY: (v) => formatValue(v, "W"), now: nowDate },
        )}
      </section>

      <section>
        <h3>Bezugspreis <span class="tag">Vorschau</span></h3>
        ${priceChart(slots.map((s) => ({ start: s.start, end: s.end, price: s.price })), "Bezugspreis im Plan", {
          // Same time axis as the charts above (history plus plan)
          start: history.length ? new Date(history[0].start) : null,
          now: nowDate,
        })}
      </section>

    `;
  }

  _renderFlex(plan) {
    const fmt = (v, unit) => (v === null || v === undefined ? "–" : `${Math.round(v * 10) / 10} ${unit}`.replace(".", ","));
    return html`
      <section>
        <h2>Ladepunkte <span class="tag">Vorschau</span></h2>
        <div class="flex-list">
          ${plan.flex.map((f) => {
            const windows = flexWindows(plan.slots, f.name);
            return html`
              <div class="flex-card">
                <div class="flex-head">
                  <strong>${f.name}</strong>
                  <span class="small muted">${{ heater: "Warmwasser", vehicle: "Fahrzeug" }[f.kind] ?? "Verbraucher"}</span>
                </div>
                <div class="small">
                  ${f.kind === "load" ? nothing : html`${fmt(f.current, f.unit)} → ${fmt(f.target, f.unit)}`}
                  ${f.need_wh > 0 ? html` · Bedarf ${formatValue(f.need_wh, "Wh")}` : nothing}
                  ${f.deadline ? html` · bis ${dayTime.format(new Date(f.deadline))}` : nothing}
                  ${f.daily_wh > 0 ? html` · danach täglich ${formatValue(f.daily_wh, "Wh")}` : nothing}
                </div>
                ${f.reason
                  ? html`<div class="small muted">Nicht eingeplant: ${f.reason}</div>`
                  : html`
                      <div class="small">Geplant ${formatValue(f.planned_wh, "Wh")}${f.unmet_wh > 50
                        ? html` · <span class="warn-text">${formatValue(f.unmet_wh, "Wh")} nicht erfüllbar</span>`
                        : nothing}</div>
                      <ul class="windows small">
                        ${windows.map(
                          (w) => html`<li>${dayTime.format(new Date(w.start))}–${time.format(new Date(w.end))} ·
                            ${formatValue(w.energy, "Wh")} · ${w.source}</li>`,
                        )}
                      </ul>
                    `}
              </div>
            `;
          })}
        </div>
      </section>
    `;
  }

  _past(key) {
    return (this._history?.points ?? []).map((p) => ({ start: p.start, end: p.end, value: p[key] }));
  }

  _pastGrid(transform) {
    return (this._history?.points ?? []).map((p) => ({
      start: p.start,
      end: p.end,
      value: p.grid_w === null ? null : transform(p.grid_w),
    }));
  }

  _future(slots, key) {
    return slots.map((s) => ({ start: s.start, end: s.end, value: s[key] }));
  }

  async _setHistoryHours(hours) {
    this._historyHours = hours;
    try {
      this._history = await api.history(hours);
    } catch (err) {
      this._error = `Verlauf konnte nicht geladen werden: ${err.message}`;
    }
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 16px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0 0 8px; font-size: 16px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .heading { display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; align-items: flex-start; }
    .alert { padding: 10px 14px; border-radius: 8px; font-size: 14px; }
    .alert.warn { background: var(--sf-warn-bg); color: var(--sf-warn-text); }
    .alert.error { background: var(--sf-error-bg); color: var(--sf-error-text); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 12px 14px; }
    .tile.now { background: var(--sf-primary-bg); }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .seg {
      display: grid; grid-template-columns: minmax(150px, auto) 1fr auto; gap: 4px 12px; align-items: baseline;
      padding: 8px 12px; border-radius: 8px; background: var(--sf-subtle); border-left: 4px solid var(--sf-border);
    }
    .seg.hold { border-left-color: var(--sf-muted); }
    .seg.auto { border-left-color: var(--sf-ok); }
    .seg.force_charge { border-left-color: var(--sf-primary); }
    .seg.force_discharge { border-left-color: var(--sf-warn-text); }
    .seg.limit_discharge, .seg.limit_charge { border-left-color: var(--sf-neutral-text); }
    .seg .when { font-variant-numeric: tabular-nums; font-size: 14px; }
    .seg .what { font-weight: 600; }
    @media (max-width: 560px) { .seg { grid-template-columns: 1fr; } }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; margin-bottom: 6px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; }
    .warn-text { color: var(--sf-error-text); }
    .flex-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
    .flex-card { background: var(--sf-subtle); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
    .flex-head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
    .windows { margin: 4px 0 0; padding-left: 18px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    .tag {
      font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; margin-left: 6px;
      background: var(--sf-neutral-bg); color: var(--sf-neutral-text); vertical-align: middle;
    }
    .section-head { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: center; }
    .range { display: flex; gap: 4px; background: var(--sf-subtle); padding: 4px; border-radius: 10px; }
    .range button {
      min-height: 36px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent; cursor: pointer;
      font-size: 13px; color: var(--sf-muted);
    }
    .range button.active { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; }
    .line { display: inline-block; width: 22px; height: 0; border-top: 2px solid var(--sf-text); }
    .line.dashed { border-top-style: dashed; }
    .chart .now { stroke: var(--sf-text); stroke-width: 1; stroke-dasharray: 2 3; }
    .chart .limit { stroke: var(--sf-muted); stroke-width: 1; stroke-dasharray: 6 4; }
    .chart .limit-label { paint-order: stroke; stroke: var(--sf-surface); stroke-width: 3px; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--sf-border); border-radius: 8px; margin-top: 10px; }
    table { border-collapse: collapse; width: 100%; min-width: 520px; font-size: 13px; }
    th { text-align: left; font-weight: 600; background: var(--sf-subtle); padding: 8px 10px; }
    td { padding: 6px 10px; border-top: 1px solid var(--sf-border); }
    .settings { border-top: 1px solid var(--sf-border); padding-top: 16px; display: flex; flex-direction: column; gap: 16px; }
    .strategy { border: 0; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; font-size: 14px; }
    .strategy legend { margin-bottom: 8px; padding: 0; }
    .option {
      display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--sf-border);
      border-radius: 8px; cursor: pointer;
    }
    .option.selected { border-color: var(--sf-primary); background: var(--sf-primary-bg); }
    .option input { margin-top: 3px; width: 18px; height: 18px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .inline { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; font-size: 14px; }
    .inline input { width: 18px; height: 18px; }
    .actions { display: flex; gap: 10px; margin-top: 12px; }
    .secondary, .ok { min-height: 44px; padding: 0 18px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .button-link {
      display: inline-flex; align-items: center; min-height: 44px; padding: 0 18px; border-radius: 8px;
      text-decoration: none; color: var(--sf-text); cursor: pointer; font-size: 14px;
    }
    .ok { border: 0; background: var(--sf-ok); color: var(--sf-on-ok); }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sunflow-plan", SunflowPlan);
