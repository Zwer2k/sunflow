import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { lineChart, priceChart, SERIES_COLORS, UNASSIGNED_COLOR } from "./charts.js";
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
    _daily: { state: true },
    _largeLoad: { state: true },
    _shadow: { state: true },
    _history: { state: true },
    _historyHours: { state: true },
    _importResult: { state: true },
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
    this._history = null;
    this._historyHours = 24;
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
      [this._plan, this._shadow, this._history] = await Promise.all([
        api.plan(),
        api.shadow(),
        api.history(this._historyHours),
      ]);
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
      this._daily = undefined;
      this._largeLoad = undefined;
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
    if (this._daily !== undefined) {
      const daily = fromDisplay(this._daily, "kWh");
      if (daily === undefined || (daily !== null && daily <= 0)) {
        this._error = "Bitte einen gültigen Tagesverbrauch in kWh eingeben oder das Feld leeren.";
        return;
      }
      changes.daily_consumption_kwh = daily;
    }
    if (this._largeLoad !== undefined) {
      const kw = fromDisplay(this._largeLoad, "kW");
      if (kw === undefined || (kw !== null && kw <= 0)) {
        this._error = "Bitte eine gültige Leistung in kW eingeben oder das Feld leeren.";
        return;
      }
      changes.large_load_threshold_w = kw === null ? null : kw * 1000;
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
          <h1>Übersicht</h1>
          <p class="muted">Links von „jetzt“ gemessene Werte, rechts Prognosen und die geplante Steuerung.
            Sunflow steuert noch nichts – die Planung zeigt, was es tun würde.</p>
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
    const history = this._history?.points ?? [];
    const capacity = Math.max(...slots.map((s) => s.soc_wh), ...history.map((p) => p.soc_wh ?? 0)) || 1;
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
          <div class="small muted">Erwartete Stromkosten</div>
          <div class="value">${euro.format(plan.cost)}</div>
          <div class="small muted">ohne Sunflow ${euro.format(plan.baseline_cost)}</div>
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
          { label: "Ladestand gemessen und erwartet", formatY: (v) => formatValue(v, "Wh"), yMax: capacity * 1.05, now: nowDate },
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
        ${priceChart(slots.map((s) => ({ start: s.start, end: s.end, price: s.price })), "Bezugspreis im Plan")}
      </section>

      ${this._renderShadow()}
      ${this._renderSettings(plan.settings)}
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
                  <span class="small muted">${f.kind === "heater" ? "Warmwasser" : "Fahrzeug"}</span>
                </div>
                <div class="small">
                  ${fmt(f.current, f.unit)} → ${fmt(f.target, f.unit)}
                  ${f.need_wh > 0 ? html` · Bedarf ${formatValue(f.need_wh, "Wh")}` : nothing}
                  ${f.deadline ? html` · bis ${dayTime.format(new Date(f.deadline))}` : nothing}
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

  _renderShadow() {
    const shadow = this._shadow;
    if (!shadow) return nothing;
    const e = shadow.evaluation;
    const since = shadow.status.since ? dayTime.format(new Date(shadow.status.since)) : "–";
    const rows = shadow.rows.filter((r) => r.actual_action !== null && r.planned_action !== null);
    const pct = (v) => (v === null ? "–" : `${Math.round(v * 100)} %`);
    // How far reality was off the forecast: 98 Wh actual vs 162 Wh forecast is "−40 % ggü. Prognose"
    const deviation = (forecast, actual) => {
      if (forecast < 0.01) return "–";
      const pct = Math.round(((actual - forecast) / forecast) * 100);
      return `${pct > 0 ? "+" : pct < 0 ? "−" : "±"}${Math.abs(pct)} % ggü. Prognose`;
    };
    const kwh = (v) => formatValue(v * 1000, "Wh");
    return html`
      <section>
        <h2>Schattenbetrieb: Prognose vs. Wirklichkeit</h2>
        <p class="small muted">
          Seit ${since} protokolliert Sunflow alle 15 Minuten, was es tun würde und was tatsächlich passiert ist –
          ${e.compared} vollständige Zeitabschnitte (letzte 48 h).
          ${shadow.status.last_error ? html`<br><span class="warn-text">Letzter Fehler: ${shadow.status.last_error}</span>` : nothing}
        </p>
        <div class="actions">
          <a class="secondary button-link" href="api/v1/export" download>Daten exportieren</a>
          <label class="secondary button-link">
            Export importieren
            <input type="file" accept=".zip" hidden @change=${(e) => this._import(e)}>
          </label>
          ${this._importResult ? html`<span class="small muted">${this._importResult}</span>` : nothing}
        </div>
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

  async _import(event) {
    const file = event.target.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
      const response = await fetch("api/v1/import", { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? response.statusText);
      this._importResult = `${data.imported_slots} Zeitabschnitte importiert.`;
      this._shadow = await api.shadow();
    } catch (err) {
      this._error = `Import fehlgeschlagen: ${err.message}`;
    }
    event.target.value = "";
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
            <span class="field-label">Durchschnittlicher Hausverbrauch</span>
            <span class="input-unit">
              <input inputmode="decimal" placeholder="z. B. 14"
                .value=${this._daily ?? toDisplay(settings.daily_consumption_kwh, "kWh")}
                @input=${(e) => (this._daily = e.target.value)}>
              <span class="muted">kWh/Tag</span>
            </span>
            <span class="small muted">Die Tagesform kommt aus der Historie, die Summe aus diesem Wert. Leer lassen = nur Historie.</span>
          </label>
          <label class="field">
            <span class="field-label">Großverbraucher ausklammern ab</span>
            <span class="input-unit">
              <input inputmode="decimal"
                .value=${this._largeLoad ?? toDisplay(settings.large_load_threshold_w === null ? null : settings.large_load_threshold_w / 1000, "kW")}
                @input=${(e) => (this._largeLoad = e.target.value)}>
              <span class="muted">kW</span>
            </span>
            <span class="small muted">Stunden darüber (z. B. Wallbox) zählen nicht zur Lastprognose. Leer = nichts ausklammern.</span>
          </label>
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
        ${this._wear !== undefined || this._reserve !== undefined || this._daily !== undefined || this._largeLoad !== undefined
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
    .ok { border: 0; background: var(--sf-ok); color: #fff; }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sunflow-plan", SunflowPlan);
