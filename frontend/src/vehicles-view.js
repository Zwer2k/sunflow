import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { fromDisplay, toDisplay } from "./format.js";
import "./picker.js";

const EMPTY = { name: "", capacity_kwh: "", soc_entity: "", plugged_entity: "", card: "", target: "", ready_by: "", wallboxes: [] };
const DETECTION = [
  ["card", "Karte an der Wallbox", "Die gescannte Karte bestimmt das Fahrzeug."],
  ["manual", "Auswahl in der Steuerung", "Gilt bis zum Abstecken."],
  ["sensor", "Sensor des Fahrzeugs", "„Angesteckt“ oder „zuhause“ aus der Integration des Autos."],
  ["single", "Einziges zugeordnetes Fahrzeug", "Ist nur eines zugeordnet, gilt immer dieses."],
];

/** Vehicles, their wallboxes and cards, and how each wallbox recognizes them (setup step "Geräte"). */
class SunflowVehicles extends LitElement {
  static properties = {
    _data: { state: true },
    _candidates: { state: true },
    _form: { state: true },
    _editId: { state: true },
    _error: { state: true },
    _busy: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._candidates = null;
    this._form = null;
    this._editId = null;
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
    } catch (err) {
      this._error = `Fahrzeuge nicht verfügbar: ${err.message}`;
    }
  }

  render() {
    const data = this._data;
    if (!data || !data.wallboxes.length) return nothing;
    return html`
      <section class="device">
        <div class="device-head">
          <div>
            <div class="device-title">Fahrzeuge</div>
            <div class="small muted">Mehrere Autos an einer Wallbox: Sunflow erkennt das angesteckte und plant mit dessen
              Ladestand, Akku und Ziel. Ohne Fahrzeuge gelten die Kenndaten der Wallbox.</div>
          </div>
        </div>
        ${this._error ? html`<div class="alert error">${this._error}</div>` : nothing}
        ${data.vehicles.map((v) => html`
          <div class="item">
            <div>
              <strong>${v.name}</strong>
              <div class="small muted">${toDisplay(v.capacity_wh / 1000, "kWh")} kWh${v.card ? ` · Karte ${v.card}` : ""}
                · ${v.wallboxes.map((id) => data.wallboxes.find((w) => w.id === id)?.name ?? "?").join(", ") || "keiner Wallbox zugeordnet"}
                ${v.soc_entity ? html`· Ladestand ${v.soc_entity}` : html`· <span class="warn-text">kein Ladestand-Sensor</span>`}</div>
            </div>
            <div class="item-actions">
              <button class="link" @click=${() => this._edit(v)}>Bearbeiten</button>
              <button class="link danger" @click=${() => this._remove(v)}>Entfernen</button>
            </div>
          </div>`)}
        ${this._form ? this._renderForm(data) : html`<div><button class="secondary" @click=${() => this._edit(null)}>Fahrzeug hinzufügen …</button></div>`}
        ${data.vehicles.length ? data.wallboxes.map((w) => this._renderDetection(w)) : nothing}
      </section>
    `;
  }

  _renderForm(data) {
    const form = this._form;
    const set = (field, value) => (this._form = { ...this._form, [field]: value });
    const options = (list, detail) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: detail(c) }));
    const cards = data.wallboxes.some((w) => w.cards);
    return html`
      <div class="editor">
        <div class="fields">
          <label class="field">
            <span class="field-label">Name</span>
            <span class="input-unit"><input placeholder="z. B. Fiat 500e" .value=${form.name} @input=${(e) => set("name", e.target.value)}></span>
          </label>
          <label class="field">
            <span class="field-label">Akkukapazität (nutzbar)</span>
            <span class="input-unit"><input inputmode="decimal" placeholder="37,5" .value=${form.capacity_kwh}
              @input=${(e) => set("capacity_kwh", e.target.value)}><span class="muted">kWh</span></span>
          </label>
          <div class="field">
            <span class="field-label">Ladestand</span>
            <sf-picker label="Ladestand" empty-label="keiner" placeholder="z. B. MQTT-Sensor des Autos …" .value=${form.soc_entity}
              .options=${options(this._candidates?.percent, (c) => `${c.state ?? "–"} %`)}
              @change=${(e) => set("soc_entity", e.detail.value)}></sf-picker>
          </div>
          <div class="field">
            <span class="field-label">Angesteckt / zuhause (optional)</span>
            <sf-picker label="Angesteckt" empty-label="keiner" placeholder="Sensor des Autos …" .value=${form.plugged_entity}
              .options=${options(this._candidates?.presence, (c) => c.state ?? "–")}
              @change=${(e) => set("plugged_entity", e.detail.value)}></sf-picker>
          </div>
          ${cards
            ? html`<label class="field">
                <span class="field-label">Karte an der Wallbox (optional)</span>
                <select @change=${(e) => set("card", e.target.value)}>
                  <option value="" ?selected=${!form.card}>keine</option>
                  ${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => html`<option value=${n} ?selected=${String(form.card) === String(n)}>Karte ${n}</option>`)}
                </select>
              </label>`
            : nothing}
          <label class="field">
            <span class="field-label">Ziel-Ladestand (optional)</span>
            <span class="input-unit"><input inputmode="decimal" placeholder="80" .value=${form.target}
              @input=${(e) => set("target", e.target.value)}><span class="muted">%</span></span>
          </label>
          <label class="field">
            <span class="field-label">Fertig bis (optional)</span>
            <span class="input-unit"><input type="time" .value=${form.ready_by} @input=${(e) => set("ready_by", e.target.value)}></span>
          </label>
        </div>
        <div class="field">
          <span class="field-label">Lädt an</span>
          <div class="checks">
            ${data.wallboxes.map((w) => html`<label class="inline">
              <input type="checkbox" .checked=${form.wallboxes.includes(w.id)}
                @change=${(e) => set("wallboxes", e.target.checked ? [...form.wallboxes, w.id] : form.wallboxes.filter((id) => id !== w.id))}>
              ${w.name}</label>`)}
          </div>
        </div>
        <div class="actions">
          <button class="primary" ?disabled=${this._busy} @click=${() => this._save()}>Speichern</button>
          <button class="secondary" @click=${() => (this._form = null)}>Abbrechen</button>
        </div>
      </div>
    `;
  }

  _renderDetection(wallbox) {
    const d = wallbox.detection;
    const set = (field, value) => this._saveDetection(wallbox, { ...d, [field]: value });
    return html`
      <div class="detection">
        <div class="field-label">${wallbox.name}: Fahrzeug erkennen über</div>
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
        <span class="small muted">Unbekanntes Fahrzeug (fremde Karte, nichts erkannt): Die Wallbox regelt selbst.</span>
      </div>
    `;
  }

  async _edit(vehicle) {
    if (this._candidates === null) {
      try {
        this._candidates = await api.candidates();
      } catch (err) {
        this._error = `Entitäten konnten nicht geladen werden: ${err.message}`;
      }
    }
    this._editId = vehicle?.id ?? null;
    this._form = vehicle
      ? {
          name: vehicle.name,
          capacity_kwh: toDisplay(vehicle.capacity_wh / 1000, "kWh"),
          soc_entity: vehicle.soc_entity ?? "",
          plugged_entity: vehicle.plugged_entity ?? "",
          card: vehicle.card ?? "",
          target: vehicle.flex.target ?? "",
          ready_by: vehicle.flex.ready_by ?? "",
          wallboxes: [...vehicle.wallboxes],
        }
      : { ...EMPTY, wallboxes: this._data.wallboxes.length === 1 ? [this._data.wallboxes[0].id] : [] };
  }

  async _save() {
    const form = this._form;
    const capacity = fromDisplay(form.capacity_kwh, "kWh");
    const target = form.target === "" ? null : fromDisplay(form.target, "%");
    if (!form.name.trim() || !capacity || capacity <= 0) {
      this._error = "Bitte Name und Akkukapazität angeben.";
      return;
    }
    const previous = this._data.vehicles.find((v) => v.id === this._editId);
    const vehicle = {
      ...(previous ?? {}),
      name: form.name.trim(),
      capacity_wh: capacity * 1000,
      soc_entity: form.soc_entity || null,
      plugged_entity: form.plugged_entity || null,
      card: form.card ? Number(form.card) : null,
      wallboxes: form.wallboxes,
      flex: { ...(previous?.flex ?? {}), target, ready_by: form.ready_by || null },
    };
    const vehicles = previous
      ? this._data.vehicles.map((v) => (v.id === previous.id ? vehicle : v))
      : [...this._data.vehicles, vehicle];
    await this._store(vehicles);
    if (!this._error) this._form = null;
  }

  async _remove(vehicle) {
    if (!window.confirm(`„${vehicle.name}“ entfernen?`)) return;
    await this._store(this._data.vehicles.filter((v) => v.id !== vehicle.id));
  }

  async _store(vehicles) {
    this._busy = true;
    try {
      this._data = await api.setVehicles(vehicles);
      this._error = null;
    } catch (err) {
      this._error = `Fahrzeuge nicht gespeichert: ${err.message}`;
    } finally {
      this._busy = false;
    }
  }

  async _saveDetection(wallbox, detection) {
    try {
      await api.setVehicleDetection(wallbox.id, detection);
      await this._load();
    } catch (err) {
      this._error = `Erkennung nicht gespeichert: ${err.message}`;
    }
  }

  static styles = css`
    :host { display: block; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 14px; }
    .device-title { font-weight: 600; font-size: 16px; }
    .item { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: center; }
    .item-actions { display: flex; gap: 12px; }
    .editor { display: flex; flex-direction: column; gap: 14px; padding: 14px; background: var(--sf-subtle); border-radius: 8px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
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
    .detection { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid var(--sf-border); padding-top: 12px; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .primary, .secondary { min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .link { border: 0; background: none; padding: 0; color: var(--sf-primary-text); text-decoration: underline; cursor: pointer; font-size: 13px; }
    .danger { color: var(--sf-error-text); }
    .warn-text { color: var(--sf-error-text); }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
  `;
}

customElements.define("sf-vehicles", SunflowVehicles);
