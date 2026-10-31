import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { poll } from "../lib/poll.js";
import { lineChart, priceChart, SERIES_COLORS, UNASSIGNED_COLOR } from "../components/charts.js";
import { formatValue, toDisplay } from "../lib/format.js";

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
// Chart window limits: statistics go back about a month, Solcast looks a week ahead
const MAX_BACK_DAYS = 31;
const MAX_AHEAD_DAYS = 7;
// The plan never reaches further than this; beyond it only the PV forecast is shown
const MAX_PLAN_HOURS = 48;
// The plan is kept by the background runner; reloading only fetches it and the statistics
const REFRESH_MS = 2 * 60 * 1000;
// Remembered per browser; storage may be unavailable (private mode, previews)
const TOTAL_KEY = "sunflow.plan.total";
const RAW_PV_KEY = "sunflow.plan.rawPv";

const time = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayTime = new Intl.DateTimeFormat("de-DE", { weekday: "short", hour: "2-digit", minute: "2-digit" });
const euro = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });

/** Name of the forecast source(s) in use, e.g. "Solcast" or "Solcast + Forecast.Solar". */
function rawLabel(plan) {
  return plan.pv_sources?.length ? plan.pv_sources.join(" + ") : "PV-Prognose";
}

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

/** A fixed color per controllable consumer, in the order of the plan; load and PV keep the first two. */
function consumerColors(flex) {
  return new Map((flex ?? []).map((f, i) => [f.device_id, SERIES_COLORS[i + 2] ?? UNASSIGNED_COLOR]));
}

/** Group consecutive slots with the same action. */
/** Reference lines for the SoC chart: full, backup reserve (if active) and discharge limit. */
function socLimits(battery) {
  if (!battery) return [];
  const pct = (wh) => `${Math.round((wh / battery.capacity_wh) * 100)} %`;
  const limits = [{ value: battery.capacity_wh, label: `voll · ${formatValue(battery.capacity_wh, "Wh")}` }];
  if (battery.limit_wh && battery.limit_wh < battery.capacity_wh && !battery.full_charge_at) {
    limits.push({ value: battery.limit_wh, label: `Ladegrenze ${pct(battery.limit_wh)}` });
  }
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
    _shift: { state: true },
    _pvAhead: { state: true },
    _focus: { state: true },
    _showTotal: { state: true },
    _showRawPv: { state: true },
    _rawPast: { state: true },
    _historyError: { state: true },
    _scales: { state: true },
  };

  constructor() {
    super();
    this._plan = null;
    this._loading = false;
    this._error = null;
    this._history = null;
    // Width of the chart window; it starts a third of it in the past (shifted by _shift hours)
    this._historyHours = 48;
    this._shift = 0;
    this._pvAhead = [];
    this._pvAheadRaw = [];
    this._rawPast = [];
    // Chosen axis maximum per chart; peaks otherwise squeeze everything else to the bottom
    this._scales = {};
    this._timer = null;
    try {
      this._showTotal = localStorage.getItem(TOTAL_KEY) === "1";
      this._showRawPv = localStorage.getItem(RAW_PV_KEY) === "1";
    } catch {
      this._showTotal = false;
      this._showRawPv = false;
    }
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
    // The measured history comes from Home Assistant's database; without it the plan still shows
    const history = this._loadHistory();
    this._loadPvAhead();
    this._loadRawPast();
    try {
      this._plan = await (fresh ? api.plan() : api.latestPlan());
      this._error = null;
    } catch (err) {
      this._error = `Plan konnte nicht berechnet werden: ${err.message}`;
    } finally {
      await history;
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
    const curtailed = slots.some((s) => (s.curtail_w ?? 0) > 0);
    const hasLoadpoints =
      history.some((p) => p.loadpoint_w !== null && p.loadpoint_w !== undefined) || Boolean(plan.flex?.length);
    const colors = consumerColors(plan.flex);
    const consumers = (plan.flex ?? []).map((f) => ({ key: f.device_id, name: f.name, color: colors.get(f.device_id) }));
    return html`
      ${plan.warnings.map((w) => html`<div class="alert warn">⚠ ${w}</div>`)}
      <div class="tiles">
        <div class="tile now">
          <div class="small muted">Geplante Steuerung jetzt (bis ${time.format(new Date(now.end))})</div>
          <div class="value">${ACTIONS[now.action].label}</div>
          <div class="small muted">${ACTIONS[now.action].hint}</div>
          ${this._renderActual(plan, now)}
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
          <div class="window-nav">
            <div class="range" role="group" aria-label="Zeitraum">
              ${[24, 48, 168].map(
                (h) => html`<button class=${this._historyHours === h ? "active" : ""} aria-pressed=${this._historyHours === h}
                  @click=${() => this._setHistoryHours(h)}>${h === 168 ? "7 Tage" : `${h} h`}</button>`,
              )}
            </div>
            <div class="range" role="group" aria-label="Zeitraum verschieben">
              <button ?disabled=${!this._canShift(-1)} aria-label="früher" @click=${() => this._shiftWindow(-1)}>‹</button>
              <button class=${this._shift === 0 ? "active" : ""} @click=${() => this._shiftWindow(0)}>Jetzt</button>
              <button ?disabled=${!this._canShift(1)} aria-label="später" @click=${() => this._shiftWindow(1)}>›</button>
            </div>
          </div>
        </div>
        <p class="small muted window-label">${dayTime.format(this._range.start)} – ${dayTime.format(this._range.end)}${this._range.end > new Date(slots.at(-1).end)
          ? " · nach dem Planende nur die PV-Prognose"
          : ""}</p>
        ${this._historyError
          ? html`<div class="alert warn">⚠ Gemessener Verlauf nicht abrufbar (${this._historyError})${this._history
              ? " – angezeigt wird der zuletzt geladene."
              : " – die Diagramme zeigen nur die Planung."}</div>`
          : nothing}
        <div class="legend">
          <span class="legend-item"><span class="line solid"></span>gemessen</span>
          <span class="legend-item"><span class="line dashed"></span>Prognose / Planung</span>
          <span class="legend-item muted">Klick auf eine Zahl der Leistungsachse skaliert bis dorthin, erneuter Klick zeigt wieder alles</span>
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
            range: this._range,
            limits: socLimits(plan.battery),
          },
        )}
      </section>

      <section>
        <h3>PV-Erzeugung und Hausverbrauch</h3>
        <div class="legend">
          <span class="legend-item">${this._legendButton("pv", SERIES_COLORS[1], "PV-Erzeugung")}
            ${plan.pv_correction
              ? html`<span class="muted">(Prognose an die Anlage angepasst: Ø ${Math.round(plan.pv_correction.overall * 100)} %
                  aus ${plan.pv_correction.days} Tagen)</span>`
              : nothing}</span>
          ${consumers.length
            ? consumers.map((c) => this._legendButton(c.key, c.color, c.name))
            : hasLoadpoints
              ? this._legendButton("consumers", UNASSIGNED_COLOR, "steuerbare Verbraucher")
              : nothing}
          <span class="legend-item">${this._legendButton("load", SERIES_COLORS[0], hasLoadpoints ? "Hausverbrauch ohne steuerbare Verbraucher" : "Hausverbrauch")}
            <span class="muted">(Prognose: ${plan.load_model
              ? `gelerntes Modell mit Außentemperatur aus ${plan.load_model.days} Tagen, Abweichung ${formatValue(plan.load_model.error_model, "W")} statt ${formatValue(plan.load_model.error_average, "W")}`
              : html`${LOAD_QUALITY[plan.load_quality] ?? "–"}, ${plan.load_history_hours} h Historie${plan.load_scaled_to_kwh
              ? `, skaliert auf ${toDisplay(plan.load_scaled_to_kwh, "kWh")} kWh/Tag`
              : ""}${plan.load_separated_hours ? `, ohne steuerbare Verbraucher (${plan.load_separated_hours} h getrennt)` : ""}`})</span></span>
          <span class="legend-item total-toggle">
            <input type="checkbox" id="show-raw-pv" aria-label="Unkorrigierte Prognose einblenden" .checked=${this._showRawPv}
              @change=${(e) => this._setShowRawPv(e.target.checked)}>
            ${this._showRawPv
              ? this._legendButton("raw", SERIES_COLORS[1], `${rawLabel(plan)} (unkorrigiert)`)
              : html`<label for="show-raw-pv">${rawLabel(plan)} (unkorrigiert)</label>`}
          </span>
          ${hasLoadpoints
            ? html`<span class="legend-item total-toggle">
                <input type="checkbox" id="show-total" aria-label="Gesamtverbrauch einblenden" .checked=${this._showTotal}
                  @change=${(e) => this._setShowTotal(e.target.checked)}>
                ${this._showTotal
                  ? this._legendButton("total", UNASSIGNED_COLOR, "Gesamtverbrauch (mit steuerbaren Verbrauchern)")
                  : html`<label for="show-total">Gesamtverbrauch (mit steuerbaren Verbrauchern)</label>`}
              </span>`
            : nothing}
        </div>
        ${lineChart(
          this._focused([
            { key: "load", name: "Verbrauch gemessen", color: SERIES_COLORS[0], points: this._pastBaseLoad() },
            { key: "load", name: "Verbrauch Prognose", color: SERIES_COLORS[0], dashed: true, points: this._future(slots, "load_w") },
            { key: "pv", name: "PV gemessen", color: SERIES_COLORS[1], points: this._past("pv_w") },
            ...(this._showRawPv
              ? [{ key: "raw", name: `${rawLabel(plan)} unkorrigiert`, color: SERIES_COLORS[1], thin: true, points: this._rawPv(plan, slots) }]
              : []),
            ...(consumers.length
              ? consumers.flatMap((c) => [
                  { key: c.key, name: `${c.name} gemessen`, color: c.color, points: this._pastConsumer(c.key) },
                  {
                    key: c.key,
                    name: `${c.name} geplant`,
                    color: c.color,
                    dashed: true,
                    points: slots.map((s) => ({ start: s.start, end: s.end, value: s.flex_w?.[c.name] ?? 0 })),
                  },
                ])
              : hasLoadpoints
                ? [{ key: "consumers", name: "Verbraucher gemessen", color: UNASSIGNED_COLOR, points: this._past("loadpoint_w") }]
                : []),
            {
              key: "pv",
              name: "PV Prognose",
              color: SERIES_COLORS[1],
              dashed: true,
              points: [...this._future(slots, "pv_w"), ...this._pvAhead.filter((p) => new Date(p.start) >= new Date(slots.at(-1).end))],
            },
            ...(hasLoadpoints && this._showTotal
              ? [
                  { key: "total", name: "Gesamtverbrauch gemessen", color: UNASSIGNED_COLOR, points: this._past("load_w") },
                  {
                    key: "total",
                    name: "Gesamtverbrauch geplant",
                    color: UNASSIGNED_COLOR,
                    dashed: true,
                    points: slots.map((s) => ({
                      start: s.start,
                      end: s.end,
                      value: s.load_w + Object.values(s.flex_w ?? {}).reduce((a, b) => a + b, 0),
                    })),
                  },
                ]
              : []),
          ]),
          {
            label: "PV-Erzeugung und Hausverbrauch, gemessen und Prognose",
            formatY: (v) => formatValue(v, "W"),
            now: nowDate,
            range: this._range,
            ...this._scaling("power"),
          },
        )}
      </section>

      <section>
        <h3>Netzbezug und Einspeisung</h3>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>Netzbezug</span>
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[1]}"></span>Einspeisung</span>
          ${curtailed
            ? html`<span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[2]}"></span>PV abgeregelt</span>`
            : nothing}
          <span class="legend-item muted">erwartet: Bezug ${formatValue(energy(slots, "import_w"), "Wh")},
            Einspeisung ${formatValue(energy(slots, "export_w"), "Wh")}${curtailed
              ? `, abgeregelt ${formatValue(energy(slots, "curtail_w"), "Wh")}`
              : ""}</span>
        </div>
        ${lineChart(
          [
            { name: "Bezug gemessen", color: SERIES_COLORS[0], points: this._pastGrid((g) => Math.max(g, 0)) },
            { name: "Bezug erwartet", color: SERIES_COLORS[0], dashed: true, points: this._future(slots, "import_w") },
            { name: "Einspeisung gemessen", color: SERIES_COLORS[1], points: this._pastGrid((g) => Math.max(-g, 0)) },
            { name: "Einspeisung erwartet", color: SERIES_COLORS[1], dashed: true, points: this._future(slots, "export_w") },
            ...(curtailed
              ? [{ name: "PV abgeregelt", color: SERIES_COLORS[2], dashed: true, points: this._future(slots, "curtail_w") }]
              : []),
          ],
          {
            label: "Netzbezug und Einspeisung, gemessen und erwartet",
            formatY: (v) => formatValue(v, "W"),
            now: nowDate,
            range: this._range,
            ...this._scaling("grid"),
          },
        )}
      </section>

      ${this._renderCostCurve(plan, slots)}

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
    const colors = consumerColors(plan.flex);
    const fmt = (v, unit) => (v === null || v === undefined ? "–" : `${Math.round(v * 10) / 10} ${unit}`.replace(".", ","));
    return html`
      <section>
        <h2>Steuerbare Verbraucher <span class="tag">Vorschau</span></h2>
        <div class="flex-list">
          ${plan.flex.map((f) => {
            const windows = flexWindows(plan.slots, f.name);
            return html`
              <div class="flex-card">
                <div class="flex-head">
                  <strong><span class="swatch inline" style="background:${colors.get(f.device_id)}"></span>${f.name}</strong>
                  <span class="small muted">${f.kind === "vehicle" && f.vehicle ? f.vehicle : { heater: "Warmwasser", vehicle: "Fahrzeug" }[f.kind] ?? "Verbraucher"}</span>
                </div>
                <div class="small">
                  ${f.kind === "load" ? nothing : html`${fmt(f.current, f.unit)} → ${fmt(f.target, f.unit)}`}
                  ${f.need_wh > 0 ? html` · Bedarf ${formatValue(f.need_wh, "Wh")}` : nothing}
                  ${f.deadline
                    ? html` · bis ${dayTime.format(new Date(f.deadline))}${f.learned_departure ? " (übliche Abfahrt, gelernt)" : ""}`
                    : nothing}
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

  _renderCostCurve(plan, slots) {
    const baseline = plan.baseline_slot_costs ?? [];
    if (baseline.length !== slots.length) return nothing;
    let planned = 0;
    let without = 0;
    const withSunflow = [];
    const withoutSunflow = [];
    slots.forEach((s, i) => {
      planned += (s.price * s.import_w - s.feed_in * s.export_w) * 0.25 / 1000;
      without += baseline[i];
      withSunflow.push({ start: s.start, end: s.end, value: planned });
      withoutSunflow.push({ start: s.start, end: s.end, value: without });
    });
    const lowest = Math.min(0, ...withSunflow.map((p) => p.value), ...withoutSunflow.map((p) => p.value));
    return html`
      <section>
        <h3>Kostenverlauf <span class="tag">Vorschau</span></h3>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>mit Sunflow ${euro.format(planned)}</span>
          <span class="legend-item"><span class="swatch" style="background:${UNASSIGNED_COLOR}"></span>reiner Eigenverbrauch ${euro.format(without)}</span>
          <span class="muted small">aufsummierte Stromkosten (Bezug minus Einspeisung)${plan.alternative_cost || plan.baseline_alternative_cost
            ? `, ohne Ersatzheizung (${euro.format(plan.alternative_cost)} bzw. ${euro.format(plan.baseline_alternative_cost)})`
            : ""}</span>
        </div>
        ${lineChart(
          [
            { name: "mit Sunflow", color: SERIES_COLORS[0], points: withSunflow },
            { name: "reiner Eigenverbrauch", color: UNASSIGNED_COLOR, dashed: true, points: withoutSunflow },
          ],
          { label: "Aufsummierte Stromkosten mit und ohne Sunflow", formatY: (v) => euro.format(v), yMin: lowest },
        )}
      </section>
    `;
  }

  _renderActual(plan, now) {
    const actual = plan.actual;
    if (!actual || actual.battery_w === null || actual.battery_w === undefined) return nothing;
    const power = formatValue(Math.abs(actual.battery_w), "W");
    const what =
      actual.battery_w > 100 ? `lädt ${power}${(actual.grid_w ?? 0) > 100 ? " aus dem Netz" : " aus PV"}`
      : actual.battery_w < -100 ? `entlädt ${power}`
      : "ruht";
    // Limits count as their basic behaviour; the measurement cannot tell them apart
    const planned = { limit_charge: "auto", limit_discharge: "limit_discharge" }[now.action] ?? now.action;
    const differs = actual.action && actual.action !== planned;
    return html`<div class="small ${differs ? "warn-text" : "muted"}">
      Tatsächlich: Speicher ${what}${differs
        ? plan.controlling
          ? " – weicht seit einigen Minuten vom Plan ab (Sunflow steuert; Grenzen des Wechselrichters oder eine Viertelstunde Verzögerung)"
          : " – nicht von Sunflow, eine andere Steuerung ist aktiv (Sunflow plant nur)"
        : ""}
    </div>`;
  }

  _past(key) {
    const points = (this._history?.points ?? []).map((p) => ({ start: p.start, end: p.end, value: p[key] }));
    return this._withLive(points, this._plan?.actual?.[key]);
  }

  _scaling(chart) {
    return {
      scaleMax: this._scales[chart] ?? null,
      onScale: (value) => (this._scales = { ...this._scales, [chart]: value }),
    };
  }

  _setShowRawPv(show) {
    this._showRawPv = show;
    if (!show && this._focus === "raw") this._focus = null;
    try {
      localStorage.setItem(RAW_PV_KEY, show ? "1" : "0");
    } catch {
      // Not remembered, the switch still works for this visit
    }
    if (show) this._loadRawPast();
  }

  /** The forecast as its source delivers it: recorded in the past, from the plan ahead, the longer one after the plan. */
  _rawPv(plan, slots) {
    const ahead = slots.map((s, i) => ({ start: s.start, end: s.end, value: plan.raw_pv?.[i] ?? null }));
    const planStart = new Date(slots[0].start);
    const planEnd = new Date(slots.at(-1).end);
    return [
      ...this._rawPast.filter((p) => new Date(p.end) <= planStart),
      ...ahead,
      ...this._pvAheadRaw.filter((p) => new Date(p.start) >= planEnd),
    ];
  }

  /** The forecast the shadow mode recorded for each past slot (before learning). */
  async _loadRawPast() {
    const hours = Math.ceil((Date.now() - this._range.start) / 3600_000);
    if (!this._showRawPv || hours <= 0) return;
    try {
      const shadow = await api.shadow(Math.min(hours, 90 * 24));
      this._rawPast = shadow.rows
        .filter((r) => r.forecast_pv_w !== null && r.forecast_pv_w !== undefined)
        .map((r) => ({ start: r.start, end: new Date(new Date(r.start).getTime() + 15 * 60_000).toISOString(), value: r.forecast_pv_w }));
    } catch {
      this._rawPast = [];
    }
  }

  _setShowTotal(show) {
    this._showTotal = show;
    if (!show && this._focus === "total") this._focus = null;
    try {
      localStorage.setItem(TOTAL_KEY, show ? "1" : "0");
    } catch {
      // Not remembered, the switch still works for this visit
    }
  }

  /** Like the forecast: the house without the controllable consumers, which have their own lines. */
  _pastBaseLoad() {
    const base = (load, consumers) => (load === null || load === undefined ? null : Math.max(load - (consumers ?? 0), 0));
    const points = (this._history?.points ?? []).map((p) => ({ start: p.start, end: p.end, value: base(p.load_w, p.loadpoint_w) }));
    const actual = this._plan?.actual;
    return this._withLive(points, actual ? base(actual.load_w, actual.loadpoint_w) : null);
  }

  _pastConsumer(deviceId) {
    return (this._history?.points ?? []).map((p) => ({
      start: p.start,
      end: p.end,
      value: p.consumers ? (p.consumers[deviceId] ?? null) : null,
    }));
  }

  /** Legend entry that highlights its lines in the chart; a second click shows all again. */
  _legendButton(key, color, label) {
    const active = this._focus === key;
    return html`<button type="button" class="legend-item legend-button ${active ? "active" : ""}" aria-pressed=${active}
      title=${active ? "Alle Linien zeigen" : "Hervorheben"} @click=${() => (this._focus = active ? null : key)}>
      <span class="swatch" style="background:${color}"></span>${label}</button>`;
  }

  _focused(series) {
    if (!this._focus || !series.some((s) => s.key === this._focus)) return series;
    return series.map((s) => ({ ...s, dim: s.key !== this._focus }));
  }

  _pastGrid(transform) {
    const points = (this._history?.points ?? []).map((p) => ({
      start: p.start,
      end: p.end,
      value: p.grid_w === null ? null : transform(p.grid_w),
    }));
    const grid = this._plan?.actual?.grid_w;
    return this._withLive(points, grid === null || grid === undefined ? null : transform(grid));
  }

  /** The statistics lag behind by up to a quarter of an hour; the latest reading closes the gap to "now". */
  _withLive(points, value) {
    const time = this._plan?.actual?.time;
    if (value === null || value === undefined || !time) return points;
    const last = points.at(-1);
    const start = last ? new Date(last.end) : new Date(new Date(time).getTime() - 15 * 60_000);
    if (new Date(time) <= start) return points;
    return [...points, { start, end: new Date(time), value }];
  }

  _future(slots, key) {
    return slots.map((s) => ({ start: s.start, end: s.end, value: s[key] }));
  }

  /** The chart window: a third in the past, shifted by half windows. */
  get _range() {
    const hour = 3600_000;
    const start = new Date(Date.now() - (this._historyHours / 3) * hour + this._shift * hour);
    return { start, end: new Date(start.getTime() + this._historyHours * hour) };
  }

  _canShift(direction) {
    const step = this._historyHours / 2;
    const range = this._range;
    const hour = 3600_000;
    if (direction < 0) return range.start.getTime() - step * hour >= Date.now() - MAX_BACK_DAYS * 24 * hour;
    return range.end.getTime() + step * hour <= Date.now() + MAX_AHEAD_DAYS * 24 * hour;
  }

  async _shiftWindow(direction) {
    this._shift = direction === 0 ? 0 : this._shift + direction * (this._historyHours / 2);
    await Promise.all([this._loadHistory(), this._loadPvAhead(), this._loadRawPast()]);
  }

  async _setHistoryHours(hours) {
    this._historyHours = hours;
    this._shift = 0;
    await Promise.all([this._loadHistory(), this._loadPvAhead(), this._loadRawPast()]);
  }

  /** Measured values of the window's past part; keeps the last on failure (charts show it with a hint). */
  async _loadHistory() {
    const { start, end } = this._range;
    const until = new Date(Math.min(end.getTime(), Date.now()));
    const key = `${this._historyHours}/${this._shift}`;
    if (until <= start) {
      this._history = { points: [] };
      return;
    }
    try {
      const hours = Math.ceil((until - start) / 3600_000);
      const history = await api.history(hours, end.getTime() < Date.now() ? until : null);
      if (key === `${this._historyHours}/${this._shift}`) {
        this._history = history;
        this._historyError = null;
      }
    } catch (err) {
      if (key === `${this._historyHours}/${this._shift}`) this._historyError = err.message;
    }
  }

  /** PV forecast beyond the plan (prices end earlier than the forecast), with the learned correction. */
  async _loadPvAhead() {
    const end = this._range.end;
    const hours = Math.ceil((end - Date.now()) / 3600_000);
    if (hours <= MAX_PLAN_HOURS) {
      this._pvAhead = [];
      this._pvAheadRaw = [];
      return;
    }
    try {
      const data = await api.forecastSeries(Math.min(hours, MAX_AHEAD_DAYS * 24));
      const factor = this._plan?.pv_correction?.overall ?? 1;
      const sums = new Map();
      for (const plane of data.planes) {
        for (const p of plane.points) {
          const entry = sums.get(p.start) ?? { start: p.start, end: p.end, value: 0 };
          entry.value += p.power;
          sums.set(p.start, entry);
        }
      }
      this._pvAheadRaw = [...sums.values()].sort((a, b) => new Date(a.start) - new Date(b.start));
      this._pvAhead = this._pvAheadRaw.map((p) => ({ ...p, value: p.value * factor }));
    } catch {
      // Optional: the charts end with the plan then
      this._pvAhead = [];
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
    .chart .scalable { cursor: pointer; }
    .chart .scalable:hover { fill: var(--sf-text); text-decoration: underline; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; margin-bottom: 6px; }
    .window-nav { display: flex; flex-wrap: wrap; gap: 8px; }
    .window-label { margin: 0 0 6px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; flex: none; }
    .swatch.inline { display: inline-block; margin-right: 6px; vertical-align: -1px; }
    .legend-button { font: inherit; color: inherit; background: none; border: 1px solid transparent; border-radius: 999px;
      padding: 2px 8px; margin: -3px 0 -3px -9px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
    .total-toggle label, .total-toggle input { cursor: pointer; }
    .total-toggle input { margin: 0; }
    .legend-button:hover { background: var(--sf-subtle); }
    .legend-button.active { border-color: var(--sf-border, var(--sf-muted)); background: var(--sf-subtle); font-weight: 600; }
    .warn-text { color: var(--sf-warn-text); }
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
