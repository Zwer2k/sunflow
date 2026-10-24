import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { lineChart, priceChart, SERIES_COLORS } from "./charts.js";
import { formatValue, fromDisplay, toDisplay } from "./format.js";

const ACTIONS = {
  auto: { label: "Eigenverbrauch", hint: "Speicher lädt aus PV-Überschuss und deckt den Bedarf" },
  hold: { label: "Speicher halten", hint: "Speicher wird geschont, Bedarf kommt aus dem Netz" },
  force_charge: { label: "Laden aus dem Netz", hint: "Speicher wird gezielt geladen" },
  force_discharge: { label: "Entladen ins Netz", hint: "Speicher speist gezielt ein" },
};
const STRATEGIES = {
  balanced: { label: "Ausgewogen", hint: "Geringste Stromkosten über den Planungszeitraum." },
  pv_absorption: {
    label: "Möglichst viel PV aufnehmen",
    hint: "Hält Platz für Solarstrom frei und lädt vor sonnigen Tagen nicht aus dem Netz – sinnvoll bei Einspeisebegrenzung.",
  },
  backup: { label: "Notstrom sicherstellen", hint: "Hält eine Reserve für Stromausfälle (EPS) im Speicher." },
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

/** Group consecutive slots with the same action. */
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
    _wear: { state: true },
    _reserve: { state: true },
    _shadow: { state: true },
    _saving: { state: true },
  };

  constructor() {
    super();
    this._plan = null;
    this._loading = false;
    this._error = null;
    this._wear = undefined;
    this._reserve = undefined;
    this._shadow = null;
    this._saving = false;
    this._timer = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._timer = setInterval(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._timer);
  }

  async _load() {
    this._loading = true;
    try {
      [this._plan, this._shadow] = await Promise.all([api.plan(), api.shadow()]);
      this._error = null;
    } catch (err) {
      this._error = `Plan konnte nicht berechnet werden: ${err.message}`;
    } finally {
      this._loading = false;
    }
  }

  async _saveSettings(changes) {
    this._saving = true;
    try {
      await api.setOptimizer({ ...this._plan.settings, ...changes });
      this._wear = undefined;
      this._reserve = undefined;
      await this._load();
    } catch (err) {
      this._error = `Einstellungen konnten nicht gespeichert werden: ${err.message}`;
    } finally {
      this._saving = false;
    }
  }

  _saveNumbers(settings) {
    const changes = {};
    if (this._wear !== undefined) {
      const wear = fromDisplay(this._wear, "ct");
      if (wear === undefined || wear === null || wear < 0) {
        this._error = "Bitte gültige Verschleißkosten eingeben.";
        return;
      }
      changes.wear_ct_per_kwh = wear;
    }
    if (this._reserve !== undefined) {
      const reserve = fromDisplay(this._reserve, "%");
      if (reserve === undefined || reserve === null || reserve < 0 || reserve > 100) {
        this._error = "Bitte eine Reserve zwischen 0 und 100 % eingeben.";
        return;
      }
      changes.backup_reserve_pct = reserve;
    }
    this._saveSettings({ ...settings, ...changes });
  }

  render() {
    const plan = this._plan;
    return html`
      <div class="heading">
        <div>
          <h1>Plan</h1>
          <p class="muted">Vorschau für die nächsten Stunden. Sunflow steuert noch nichts – der Plan zeigt, was es tun würde.</p>
        </div>
        <button class="secondary" ?disabled=${this._loading} @click=${() => this._load()}>
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
    const capacity = Math.max(...slots.map((s) => s.soc_wh)) || 1;
    return html`
      ${plan.warnings.map((w) => html`<div class="alert warn">⚠ ${w}</div>`)}
      <div class="tiles">
        <div class="tile now">
          <div class="small muted">Jetzt bis ${time.format(new Date(now.end))}</div>
          <div class="value">${ACTIONS[now.action].label}</div>
          <div class="small muted">${ACTIONS[now.action].hint}</div>
        </div>
        <div class="tile">
          <div class="small muted">Ersparnis im Planungszeitraum</div>
          <div class="value">${euro.format(plan.savings ?? 0)}</div>
          <div class="small muted">gegenüber reinem Eigenverbrauch</div>
        </div>
        <div class="tile">
          <div class="small muted">Stromkosten geplant</div>
          <div class="value">${euro.format(plan.cost)}</div>
          <div class="small muted">ohne Sunflow ${euro.format(plan.baseline_cost)}</div>
        </div>
        <div class="tile">
          <div class="small muted">Ladestand am Ende</div>
          <div class="value">${formatValue(slots.at(-1).soc_wh, "Wh")}</div>
          <div class="small muted">${dayTime.format(new Date(slots.at(-1).end))}</div>
        </div>
      </div>

      <section>
        <h2>Ablauf</h2>
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

      <section>
        <h2>Ladestand</h2>
        ${lineChart(
          [{ name: "Ladestand", color: SERIES_COLORS[0], points: slots.map((s) => ({ start: s.start, end: s.end, value: s.soc_wh })) }],
          { label: "Geplanter Ladestand", formatY: (v) => formatValue(v, "Wh"), yMax: capacity * 1.05 },
        )}
      </section>

      <section>
        <h2>PV-Erzeugung und Hausverbrauch</h2>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[1]}"></span>PV-Prognose</span>
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>
            Hausverbrauch (${LOAD_QUALITY[plan.load_quality] ?? "–"}, ${plan.load_history_hours} h Historie)</span>
        </div>
        ${lineChart(
          [
            { name: "Verbrauch", color: SERIES_COLORS[0], points: slots.map((s) => ({ start: s.start, end: s.end, value: s.load_w })) },
            { name: "PV", color: SERIES_COLORS[1], points: slots.map((s) => ({ start: s.start, end: s.end, value: s.pv_w })) },
          ],
          { label: "PV-Prognose und Lastprognose", formatY: (v) => formatValue(v, "W") },
        )}
      </section>

      <section>
        <h2>Bezugspreis</h2>
        ${priceChart(slots.map((s) => ({ start: s.start, end: s.end, price: s.price })), "Bezugspreis im Plan")}
      </section>

      ${this._renderShadow()}
      ${this._renderSettings(plan.settings)}
    `;
  }

  _renderShadow() {
    const shadow = this._shadow;
    if (!shadow) return nothing;
    const e = shadow.evaluation;
    const since = shadow.status.since ? dayTime.format(new Date(shadow.status.since)) : "–";
    const rows = shadow.rows.filter((r) => r.actual_action !== null && r.planned_action !== null);
    const pct = (v) => (v === null ? "–" : `${Math.round(v * 100)} %`);
    const deviation = (forecast, actual) =>
      actual > 0.05 ? `${forecast >= actual ? "+" : "−"}${Math.round((Math.abs(forecast - actual) / actual) * 100)} %` : "–";
    const kwh = (v) => formatValue(v * 1000, "Wh");
    return html`
      <section>
        <h2>Schattenbetrieb: Prognose vs. Wirklichkeit</h2>
        <p class="small muted">
          Seit ${since} protokolliert Sunflow alle 15 Minuten, was es tun würde und was tatsächlich passiert ist –
          ${e.compared} vollständige Zeitabschnitte (letzte 48 h).
          ${shadow.status.last_error ? html`<br><span class="warn-text">Letzter Fehler: ${shadow.status.last_error}</span>` : nothing}
        </p>
        ${e.compared === 0
          ? html`<p class="muted">Noch keine abgeschlossenen Zeitabschnitte – der erste erscheint nach dem nächsten Viertelstundenwechsel.</p>`
          : html`
              <div class="tiles">
                <div class="tile">
                  <div class="small muted">PV-Prognose</div>
                  <div class="value">${kwh(e.pv_forecast_kwh)}</div>
                  <div class="small muted">tatsächlich ${kwh(e.pv_actual_kwh)} (${deviation(e.pv_forecast_kwh, e.pv_actual_kwh)})</div>
                </div>
                <div class="tile">
                  <div class="small muted">Lastprognose</div>
                  <div class="value">${kwh(e.load_forecast_kwh)}</div>
                  <div class="small muted">tatsächlich ${kwh(e.load_actual_kwh)} (${deviation(e.load_forecast_kwh, e.load_actual_kwh)})</div>
                </div>
                <div class="tile">
                  <div class="small muted">Ladestand-Abweichung</div>
                  <div class="value">${e.soc_mae_wh === null ? "–" : formatValue(e.soc_mae_wh, "Wh")}</div>
                  <div class="small muted">mittlerer Abstand Plan zu Ist</div>
                </div>
                <div class="tile">
                  <div class="small muted">Gleiche Entscheidung</div>
                  <div class="value">${pct(e.agreement)}</div>
                  <div class="small muted">${e.differing_slots} Abschnitte hätte Sunflow anders gesteuert</div>
                </div>
              </div>
              <div class="legend">
                <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>Ladestand tatsächlich</span>
                <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[1]}"></span>Ladestand laut Plan</span>
              </div>
              ${lineChart(
                [
                  { name: "Ist", color: SERIES_COLORS[0], points: rows.map((r) => this._point(r, "actual_soc_wh")) },
                  { name: "Plan", color: SERIES_COLORS[1], points: rows.map((r) => this._point(r, "planned_soc_wh")) },
                ],
                { label: "Ladestand Plan gegen Ist", formatY: (v) => formatValue(v, "Wh") },
              )}
              ${this._renderDifferences(rows)}
            `}
      </section>
    `;
  }

  _point(row, key) {
    const start = new Date(row.start);
    return { start: start.toISOString(), end: new Date(start.getTime() + 15 * 60_000).toISOString(), value: row[key] ?? 0 };
  }

  _renderDifferences(rows) {
    const differing = rows.filter((r) => r.planned_action !== r.actual_action).slice(-12).reverse();
    if (!differing.length) return html`<p class="small muted">Bisher hätte Sunflow genauso gehandelt wie der Wechselrichter.</p>`;
    return html`
      <div class="table-wrap">
        <table>
          <thead><tr><th>Zeit</th><th>Sunflow hätte</th><th>Tatsächlich</th><th>PV / Last (Ist)</th></tr></thead>
          <tbody>
            ${differing.map(
              (r) => html`<tr>
                <td>${dayTime.format(new Date(r.start))}</td>
                <td>${ACTIONS[r.planned_action]?.label ?? r.planned_action}${r.planned_power_w ? ` · ${formatValue(r.planned_power_w, "W")}` : ""}</td>
                <td>${ACTIONS[r.actual_action]?.label ?? r.actual_action}</td>
                <td>${formatValue(r.actual_pv_w, "W")} / ${formatValue(r.actual_load_w, "W")}</td>
              </tr>`,
            )}
          </tbody>
        </table>
      </div>
    `;
  }

  _renderSettings(settings) {
    return html`
      <section class="settings">
        <h2>Einstellungen</h2>
        <fieldset class="strategy">
          <legend class="field-label">Strategie</legend>
          ${Object.entries(STRATEGIES).map(
            ([value, { label, hint }]) => html`
              <label class="option ${settings.strategy === value ? "selected" : ""}">
                <input type="radio" name="strategy" .checked=${settings.strategy === value} ?disabled=${this._saving}
                  @change=${() => this._saveSettings({ strategy: value })}>
                <span><strong>${label}</strong><br><span class="small muted">${hint}</span></span>
              </label>
            `,
          )}
        </fieldset>
        <div class="fields">
          ${settings.strategy === "backup"
            ? html`<label class="field">
                <span class="field-label">Notstrom-Reserve</span>
                <span class="input-unit">
                  <input inputmode="decimal" .value=${this._reserve ?? toDisplay(settings.backup_reserve_pct, "%")}
                    @input=${(e) => (this._reserve = e.target.value)}>
                  <span class="muted">%</span>
                </span>
                <span class="small muted">Wird nie unterschritten – zusätzlich zum Mindest-Ladestand.</span>
              </label>`
            : nothing}
          <label class="field">
            <span class="field-label">Verschleißkosten Speicher</span>
            <span class="input-unit">
              <input inputmode="decimal" .value=${this._wear ?? toDisplay(settings.wear_ct_per_kwh, "ct")}
                @input=${(e) => (this._wear = e.target.value)}>
              <span class="muted">ct/kWh</span>
            </span>
            <span class="small muted">Je entladener kWh – verhindert Zyklen für Kleinstbeträge.</span>
          </label>
          <label class="field">
            <span class="field-label">Laden aus dem Netz</span>
            <span class="inline">
              <input type="checkbox" .checked=${settings.allow_grid_charge} ?disabled=${this._saving}
                @change=${(e) => this._saveSettings({ allow_grid_charge: e.target.checked })}>
              erlaubt, wenn es sich rechnet
            </span>
          </label>
        </div>
        ${this._wear !== undefined || this._reserve !== undefined
          ? html`<div class="actions">
              <button class="ok" ?disabled=${this._saving} @click=${() => this._saveNumbers(settings)}>
                Speichern und neu planen
              </button>
            </div>`
          : html`<p class="small muted">Strategie und Netzladen gelten sofort. Zahlenwerte werden nach einer Änderung mit „Speichern und neu planen“ übernommen.</p>`}
      </section>
    `;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 20px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 24px; }
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
    .ok { border: 0; background: var(--sf-ok); color: #fff; }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sunflow-plan", SunflowPlan);
