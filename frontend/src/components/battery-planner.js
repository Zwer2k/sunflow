import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { fromDisplay, toDisplay } from "../lib/format.js";
import { decimal, locale, t } from "../lib/i18n.js";

// Numeric fields of a scenario: key, label, unit shown, optional
const FIELDS = [
  ["capacity_kwh", t("Kapazität"), "kWh"],
  ["usable_pct", t("Nutzbar"), "%"],
  ["max_charge_kw", t("Ladeleistung"), "kW"],
  ["max_discharge_kw", t("Entladeleistung"), "kW"],
  ["efficiency_pct", t("Wirkungsgrad Speicher (Zyklus)"), "%"],
  ["inverter_efficiency_pct", t("Wirkungsgrad Wechselrichter"), "%"],
  ["price_eur", t("Kaufpreis (optional)"), "€", true],
  ["cycles", t("Zyklen laut Hersteller"), ""],
  ["wear_ct", t("Verschleiß (optional, sonst aus Preis)"), "ct/kWh", true],
];
const DAYS = [[30, t("30 Tage")], [90, t("90 Tage")], [365, t("1 Jahr")]];
const RUNS = { self_consumption: t("Eigenverbrauch"), optimized: t("mit Preisoptimierung") };

function newScenario() {
  return {
    id: `s${Date.now().toString(36)}`, name: t("Neuer Speicher"), capacity_kwh: 10, usable_pct: 90, max_charge_kw: 5,
    max_discharge_kw: 5, efficiency_pct: 93, inverter_efficiency_pct: 96.5, mode: "add", price_eur: null, cycles: 6000,
    wear_ct: null, grid_charge: true,
  };
}

/** Plan a battery purchase: scenarios simulated over the measured history (Analysis tab). */
class SunflowBatteryPlanner extends LitElement {
  static properties = {
    _scenarios: { state: true },
    _edits: { state: true },
    _result: { state: true },
    _days: { state: true },
    _busy: { state: true },
    _error: { state: true },
  };

  constructor() {
    super();
    this._scenarios = null;
    this._edits = null;
    this._result = null;
    this._days = 30;
    this._busy = false;
    this._error = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._scenarios = await api.batteryScenarios();
      this._error = null;
    } catch (err) {
      this._error = err.message;
      return;
    }
    if (this._scenarios.length) this._simulate();
  }

  async _simulate() {
    const days = this._days;
    this._busy = true;
    try {
      const result = await api.batterySimulation(days);
      if (days === this._days) this._result = result;
      this._error = null;
    } catch (err) {
      this._error = t("Simulation fehlgeschlagen: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  _startEditing() {
    this._edits = (this._scenarios ?? []).map((s) => ({ ...s }));
  }

  _set(index, key, value) {
    this._edits = this._edits.map((s, i) => (i === index ? { ...s, [key]: value } : s));
  }

  async _addExisting() {
    try {
      const existing = await api.existingBatteryScenario();
      if (!existing) {
        this._error = t("Kein Speicher mit Kapazität eingerichtet.");
        return;
      }
      this._edits = [...(this._edits ?? []), { ...existing, id: `${existing.id}-${Date.now().toString(36)}` }];
    } catch (err) {
      this._error = err.message;
    }
  }

  async _save() {
    const scenarios = [];
    for (const edit of this._edits) {
      const scenario = { ...edit };
      for (const [key, label, , optional] of FIELDS) {
        const value = typeof edit[key] === "string" ? fromDisplay(edit[key], "") : edit[key];
        if (value === undefined || (value === null && !optional)) {
          this._error = t("Ungültige Zahl bei „{name}“ ({field}).", { name: edit.name, field: label });
          return;
        }
        scenario[key] = value;
      }
      scenarios.push(scenario);
    }
    this._busy = true;
    try {
      this._scenarios = await api.setBatteryScenarios(scenarios);
      this._edits = null;
      this._error = null;
    } catch (err) {
      this._error = t("Speichern fehlgeschlagen: {error}", { error: err.message });
      return;
    } finally {
      this._busy = false;
    }
    if (this._scenarios.length) this._simulate();
    else this._result = null;
  }

  render() {
    if (this._scenarios === null && !this._error) return nothing;
    return html`<section>
      <div class="section-head">
        <h2>${t("Speicher planen")}</h2>
        ${this._scenarios?.length && !this._edits
          ? html`<div class="range" role="group" aria-label=${t("Zeitraum der Simulation")}>
              ${DAYS.map(([value, label]) => html`<button class=${this._days === value ? "active" : ""} aria-pressed=${this._days === value}
                ?disabled=${this._busy} @click=${() => { this._days = value; this._simulate(); }}>${label}</button>`)}
            </div>`
          : nothing}
      </div>
      <p class="small muted">${t("Was ein Speicher in den letzten Tagen gespart hätte – mit deinen gemessenen Netz- und Speicherwerten und dem Preisverlauf. Zum Vergleich vor einem Kauf, für eine Erweiterung oder um den vorhandenen Speicher mit anderen Einstellungen durchzurechnen. Sunflow steuert dabei nichts.")}</p>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${this._edits ? this._renderEditor() : this._renderResult()}
    </section>`;
  }

  _renderResult() {
    const scenarios = this._scenarios ?? [];
    if (!scenarios.length) {
      return html`<div class="actions"><button class="primary" @click=${() => { this._startEditing(); this._edits = [newScenario()]; }}>
        ${t("Variante anlegen")}</button>
        <button class="secondary" @click=${() => { this._startEditing(); this._addExisting(); }}>${t("Vorhandenen Speicher übernehmen")}</button></div>`;
    }
    const result = this._result;
    const euro = new Intl.NumberFormat(locale(), { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
    const kwh = (value) => `${decimal(Math.round(value).toString())} kWh`;
    return html`
      ${this._busy ? html`<p class="small muted">${t("Rechnet … (bei einem Jahr bis zu einer Minute)")}</p>` : nothing}
      ${result
        ? html`<div class="table-wrap"><table>
            <thead><tr>
              <th>${t("Variante")}</th><th>${t("Fahrweise")}</th><th>${t("Bezug")}</th><th>${t("Einspeisung")}</th>
              <th>${t("Stromkosten")}</th><th>${t("Ersparnis")}</th><th>${t("pro Jahr")}</th><th>${t("Zyklen/Jahr")}</th><th>${t("Amortisation")}</th>
            </tr></thead>
            <tbody>
              <tr class="measured"><td>${t("Gemessen (heute)")}</td><td></td><td>${kwh(result.measured.imported_kwh)}</td>
                <td>${kwh(result.measured.exported_kwh)}</td><td>${euro.format(result.measured.cost)}</td><td></td><td></td><td></td><td></td></tr>
              ${result.scenarios.flatMap((s) => Object.entries(RUNS).map(([key, label], i) => {
                const run = s[key];
                return html`<tr>
                  <td>${i === 0 ? html`<strong>${s.name}</strong><div class="small muted">${s.mode === "replace" ? t("statt des vorhandenen") : t("zusätzlich")}</div>` : ""}</td>
                  <td>${label}</td><td>${kwh(run.imported_kwh)}</td><td>${kwh(run.exported_kwh)}</td><td>${euro.format(run.cost)}</td>
                  <td class=${run.saving < 0 ? "worse" : ""}>${euro.format(run.saving)}</td><td>${euro.format(run.saving_per_year)}</td>
                  <td>${run.cycles_per_year !== null ? decimal(Math.round(run.cycles_per_year).toString()) : "–"}</td>
                  <td>${run.payback_years !== null ? t("{years} Jahre", { years: decimal(run.payback_years.toFixed(1)) }) : "–"}</td>
                </tr>`;
              }))}
            </tbody>
          </table></div>
          <p class="small muted">${t("{days} Tage mit Zählerwerten und Preis. Hochgerechnet aufs Jahr ohne Jahreszeiten – im Winter nutzt ein Speicher weniger PV als im Sommer. Stundenwerte gleichen Bezug und Einspeisung innerhalb einer Stunde aus, die Ersparnis ist daher etwas zu hoch. „Mit Preisoptimierung“ kennt die Preise und den Verbrauch im Voraus – so gut wird keine Planung.", { days: decimal(result.days.toFixed(0)) })}</p>`
        : nothing}
      <div class="actions"><button class="secondary" ?disabled=${this._busy} @click=${() => this._startEditing()}>${t("Varianten bearbeiten")}</button></div>
    `;
  }

  _renderEditor() {
    return html`
      ${this._edits.map((s, index) => html`<fieldset class="scenario">
        <div class="scenario-head">
          <label class="field name"><span class="field-label">${t("Name")}</span>
            <input .value=${s.name} maxlength="60" @input=${(e) => this._set(index, "name", e.target.value)}></label>
          <button class="link danger" @click=${() => (this._edits = this._edits.filter((_, i) => i !== index))}>${t("Entfernen")}</button>
        </div>
        <div class="modes" role="radiogroup" aria-label=${t("Einsatz")}>
          ${[["add", t("zusätzlich zum vorhandenen Speicher")], ["replace", t("statt des vorhandenen Speichers")]].map(
            ([value, label]) => html`<label class="radio"><input type="radio" name="mode-${s.id}" .checked=${s.mode === value}
              @change=${() => this._set(index, "mode", value)}> ${label}</label>`,
          )}
          <label class="radio"><input type="checkbox" .checked=${s.grid_charge} @change=${(e) => this._set(index, "grid_charge", e.target.checked)}>
            ${t("Laden aus dem Netz erlaubt (Preisoptimierung)")}</label>
        </div>
        <div class="fields">
          ${FIELDS.map(([key, label, unit, optional]) => html`<label class="field"><span class="field-label">${label}</span>
            <span class="input-unit"><input inputmode="decimal" .value=${typeof s[key] === "string" ? s[key] : toDisplay(s[key], "")}
              placeholder=${optional ? "–" : ""} @input=${(e) => this._set(index, key, e.target.value)}><span class="muted">${unit}</span></span></label>`)}
        </div>
      </fieldset>`)}
      <div class="actions">
        <button class="secondary" @click=${() => (this._edits = [...this._edits, newScenario()])}>${t("Variante hinzufügen")}</button>
        <button class="secondary" @click=${() => this._addExisting()}>${t("Vorhandenen Speicher übernehmen")}</button>
        <span class="spacer"></span>
        <button class="secondary" @click=${() => (this._edits = null)}>${t("Abbrechen")}</button>
        <button class="primary" ?disabled=${this._busy} @click=${() => this._save()}>${t("Speichern und rechnen")}</button>
      </div>
      <p class="small muted">${t("„Vorhandenen Speicher übernehmen“ legt deinen Speicher mit seinen Werten als Variante „statt des vorhandenen“ an – ändere dann z. B. Netzladen, Verschleiß oder Leistung, um zu sehen, was das gebracht hätte.")}</p>
    `;
  }

  static styles = css`
    :host { display: block; }
    * { box-sizing: border-box; }
    section { display: flex; flex-direction: column; gap: 10px; }
    h2 { margin: 0; font-size: 16px; }
    p { margin: 0; }
    .small { font-size: 13px; }
    .muted { color: var(--sf-muted); }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); font-size: 14px; }
    .section-head { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: center; }
    .range { display: flex; gap: 4px; background: var(--sf-subtle); padding: 4px; border-radius: 10px; }
    .range button {
      min-height: 36px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent; cursor: pointer;
      font: inherit; font-size: 13px; color: var(--sf-muted);
    }
    .range button.active { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--sf-border); border-radius: 8px; }
    table { border-collapse: collapse; width: 100%; min-width: 760px; font-size: 13px; font-variant-numeric: tabular-nums; }
    th { text-align: left; font-weight: 600; background: var(--sf-subtle); padding: 8px 10px; white-space: nowrap; }
    td { padding: 6px 10px; border-top: 1px solid var(--sf-border); vertical-align: top; }
    tr.measured td { background: var(--sf-primary-bg); }
    td.worse { color: var(--sf-error-text); }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .spacer { flex: 1; }
    button { font: inherit; }
    button.primary, button.secondary {
      min-height: 40px; padding: 0 14px; border-radius: 8px; cursor: pointer; font-size: 14px;
    }
    button.primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    button.secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); color: var(--sf-text); }
    button.link { border: 0; background: none; padding: 0; cursor: pointer; color: var(--sf-primary-text); font-size: 13px; }
    button.link.danger { color: var(--sf-error-text); }
    .scenario { border: 1px solid var(--sf-border); border-radius: 10px; padding: 12px 14px; margin: 0; display: flex; flex-direction: column; gap: 10px; }
    .scenario-head { display: flex; gap: 12px; align-items: flex-end; justify-content: space-between; }
    .field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; min-width: 0; }
    .field.name { flex: 1; max-width: 360px; }
    .field-label { font-weight: 600; }
    .fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }
    .input-unit { display: flex; align-items: center; gap: 6px; }
    input:not([type="radio"]):not([type="checkbox"]) {
      width: 100%; min-height: 38px; padding: 0 10px; border: 1px solid var(--sf-border-strong); border-radius: 8px;
      background: var(--sf-surface); color: var(--sf-text); font: inherit; font-size: 14px;
    }
    .modes { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 14px; }
    .radio { display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }
  `;
}

customElements.define("sf-battery-planner", SunflowBatteryPlanner);
