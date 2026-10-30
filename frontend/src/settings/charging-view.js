import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { fromDisplay, toDisplay } from "../lib/format.js";

const DETECTION = [
  ["card", "Karte an der Wallbox", "Die gescannte Karte bestimmt das Fahrzeug."],
  ["manual", "Auswahl in der Steuerung", "Gilt bis zum Abstecken."],
  ["sensor", "Sensor des Fahrzeugs", "„Angesteckt“ oder „zuhause“ aus der Integration des Autos."],
  ["single", "Einziges zugeordnetes Fahrzeug", "Ist nur eines zugeordnet, gilt immer dieses."],
];
const STRATEGIES = { cost: "Kostenoptimal", pv_only: "Nur PV-Überschuss", fast: "Schnellstmöglich" };

/**
 * Which vehicle charges at which wallbox and how (settings area "Laden"): a charging
 * profile per vehicle and wallbox with card, target, ready-by time and strategy, plus
 * how the wallbox recognizes the connected vehicle.
 */
class SunflowCharging extends LitElement {
  static properties = {
    _data: { state: true },
    _profiles: { state: true },
    _open: { state: true },
    _dirty: { state: true },
    _error: { state: true },
    _busy: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._profiles = [];
    this._open = new Set();
    this._dirty = false;
    this._error = null;
    this._busy = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._data = await api.vehicles();
      this._profiles = this._data.charging.map((c) => ({ ...c, flex: { ...c.flex } }));
      this._dirty = false;
    } catch (err) {
      this._error = `Ladeprofile nicht verfügbar: ${err.message}`;
    }
  }

  render() {
    const data = this._data;
    return html`
      <div class="heading">
        <h1>Laden</h1>
        <p class="muted">Je Wallbox: welche Fahrzeuge dort laden, mit welcher Karte, bis zu welchem Ladestand und wann sie
          fertig sein sollen – und wie die Wallbox das angesteckte Fahrzeug erkennt.</p>
      </div>
      ${this._error ? html`<div class="alert error">${this._error}</div>` : nothing}
      ${data === null ? html`<p class="muted">Lade …</p>` : nothing}
      ${data && !data.wallboxes.length ? html`<p class="muted">Keine Wallbox eingerichtet (Geräte).</p>` : nothing}
      ${data && data.wallboxes.length && !data.vehicles.length
        ? html`<p class="muted">Noch keine Fahrzeuge – zuerst unter „Fahrzeuge“ anlegen. Ohne Fahrzeuge plant Sunflow die Wallbox
            mit ihren Kenndaten und den Vorgaben unter „Verbraucher“.</p>`
        : nothing}
      ${data?.vehicles.length ? data.wallboxes.map((w) => this._renderWallbox(w)) : nothing}
      ${this._dirty
        ? html`<div class="actions sticky">
            <button class="primary" ?disabled=${this._busy} @click=${() => this._save()}>Ladeprofile speichern</button>
            <button class="secondary" @click=${() => this._load()}>Verwerfen</button>
          </div>`
        : nothing}
    `;
  }

  _renderWallbox(wallbox) {
    const vehicles = this._data.vehicles;
    const allowed = this._profiles.filter((p) => p.wallbox_id === wallbox.id);
    return html`
      <section class="device">
        <div class="device-title">${wallbox.name}</div>
        <div class="small muted">${allowed.length
          ? `${allowed.length} Fahrzeug${allowed.length > 1 ? "e" : ""} darf hier laden`
          : "Noch kein Fahrzeug – ein angestecktes Auto gilt als unbekannt, die Wallbox regelt dann selbst."}</div>
        ${vehicles.map((v) => this._renderProfile(wallbox, v))}
        ${this._renderDetection(wallbox)}
      </section>
    `;
  }

  _renderProfile(wallbox, vehicle) {
    const profile = this._profiles.find((p) => p.wallbox_id === wallbox.id && p.vehicle_id === vehicle.id);
    const update = (changes) => {
      this._profiles = this._profiles.map((p) => (p === profile ? { ...p, ...changes, flex: { ...p.flex, ...(changes.flex ?? {}) } } : p));
      this._dirty = true;
    };
    const toggle = (on) => {
      this._profiles = on
        ? [...this._profiles, { vehicle_id: vehicle.id, wallbox_id: wallbox.id, card: null, flex: { strategy: "cost", target: null, ready_by: null, uses_battery: true } }]
        : this._profiles.filter((p) => p !== profile);
      this._dirty = true;
    };
    return html`
      <div class="sub ${profile ? "" : "off"}">
        <label class="inline">
          <input type="checkbox" .checked=${Boolean(profile)} @change=${(e) => toggle(e.target.checked)}>
          <strong>${vehicle.name}</strong>
        </label>
        ${profile
          ? html`<div class="fields">
              ${wallbox.cards
                ? html`<label class="field">
                    <span class="field-label">Karte an dieser Wallbox</span>
                    <select @change=${(e) => update({ card: e.target.value ? Number(e.target.value) : null })}>
                      <option value="" ?selected=${!profile.card}>keine</option>
                      ${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => html`<option value=${n} ?selected=${profile.card === n}>Karte ${n}</option>`)}
                    </select>
                  </label>`
                : nothing}
              <label class="field">
                <span class="field-label">Ziel-Ladestand</span>
                <span class="input-unit"><input inputmode="decimal" placeholder="100"
                  .value=${profile.flex.target === null || profile.flex.target === undefined ? "" : toDisplay(profile.flex.target, "%")}
                  @input=${(e) => update({ flex: { target: e.target.value === "" ? null : fromDisplay(e.target.value, "%") } })}>
                  <span class="muted">%</span></span>
              </label>
              <label class="field">
                <span class="field-label">Fertig bis (täglich)</span>
                <span class="input-unit"><input type="time" .value=${profile.flex.ready_by ?? ""}
                  @change=${(e) => update({ flex: { ready_by: e.target.value || null } })}></span>
              </label>
              <label class="field">
                <span class="field-label">Laden</span>
                <select @change=${(e) => update({ flex: { strategy: e.target.value } })}>
                  ${Object.entries(STRATEGIES).map(([value, label]) => html`<option value=${value} ?selected=${profile.flex.strategy === value}>${label}</option>`)}
                </select>
              </label>
              <label class="field">
                <span class="field-label">Hausspeicher</span>
                <span class="inline"><input type="checkbox" .checked=${profile.flex.uses_battery !== false}
                  @change=${(e) => update({ flex: { uses_battery: e.target.checked } })}>darf ins Auto entladen</span>
              </label>
            </div>`
          : nothing}
      </div>
    `;
  }

  _renderDetection(wallbox) {
    const d = wallbox.detection;
    const set = (field, value) => this._saveDetection(wallbox, { ...d, [field]: value });
    const open = this._open.has(wallbox.id);
    const methods = DETECTION.filter(([key]) => d[key] && (key !== "card" || wallbox.cards)).map(([, label]) => label);
    const toggle = () => {
      const next = new Set(this._open);
      if (open) next.delete(wallbox.id);
      else next.add(wallbox.id);
      this._open = next;
    };
    return html`
      <div class="detection">
        <button class="device-toggle" aria-expanded=${open} @click=${toggle}>
          <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
          <span><strong>Erkennung des Fahrzeugs</strong>
            ${open ? nothing : html`<span class="small muted block">${methods.join(" · ") || "aus"}${wallbox.cards && d.authorize ? " · gibt ohne Karte frei" : ""}</span>`}</span>
        </button>
        ${open
          ? html`
              <div class="checks">
                ${DETECTION.filter(([key]) => key !== "card" || wallbox.cards).map(([key, label, hint]) => html`
                  <label class="inline" title=${hint}>
                    <input type="checkbox" .checked=${d[key]} @change=${(e) => set(key, e.target.checked)}>${label}</label>`)}
              </div>
              ${wallbox.cards
                ? html`<label class="inline">
                    <input type="checkbox" .checked=${d.authorize} @change=${(e) => set("authorize", e.target.checked)}>
                    Ladevorgang ohne Karte freigeben, wenn das Fahrzeug sicher erkannt ist (Sensor oder Auswahl)</label>`
                : nothing}
              <span class="small muted">Unbekanntes Fahrzeug (fremde Karte, nichts erkannt): Die Wallbox regelt selbst.</span>`
          : nothing}
      </div>
    `;
  }

  async _save() {
    if (this._profiles.some((p) => p.flex.target === undefined)) {
      this._error = "Bitte einen gültigen Ziel-Ladestand eingeben.";
      return;
    }
    this._busy = true;
    try {
      this._data = await api.setCharging(this._profiles);
      this._profiles = this._data.charging.map((c) => ({ ...c, flex: { ...c.flex } }));
      this._dirty = false;
      this._error = null;
    } catch (err) {
      this._error = `Ladeprofile nicht gespeichert: ${err.message}`;
    } finally {
      this._busy = false;
    }
  }

  async _saveDetection(wallbox, detection) {
    try {
      await api.setVehicleDetection(wallbox.id, detection);
      this._data = { ...this._data, wallboxes: this._data.wallboxes.map((w) => (w.id === wallbox.id ? { ...w, detection } : w)) };
    } catch (err) {
      this._error = `Erkennung nicht gespeichert: ${err.message}`;
    }
  }

  /** Called by the settings when leaving the area: unsaved profiles are kept. */
  async saveIfChanged() {
    if (!this._dirty) return true;
    await this._save();
    return !this._dirty;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 12px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    .heading { display: flex; flex-direction: column; gap: 4px; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .block { display: block; }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 12px; }
    .device-title { font-weight: 600; font-size: 16px; }
    .sub { display: flex; flex-direction: column; gap: 10px; border: 1px solid var(--sf-border); border-radius: 8px; padding: 12px 14px; }
    .sub.off { padding: 8px 14px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; font-size: 13px; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input, select {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .checks { display: flex; flex-wrap: wrap; gap: 6px 18px; }
    .inline { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 14px; }
    .inline input { width: 18px; height: 18px; margin: 0; }
    .detection { display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--sf-border); padding-top: 12px; }
    .device-toggle { display: flex; gap: 8px; align-items: flex-start; border: 0; background: none; padding: 0; cursor: pointer; text-align: left; font-size: 14px; }
    .caret { width: 14px; color: var(--sf-muted); line-height: 20px; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .actions.sticky { position: sticky; bottom: 0; padding: 12px 0; background: var(--sf-bg); }
    .primary, .secondary { min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
  `;
}

customElements.define("sf-charging", SunflowCharging);
