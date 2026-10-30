import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { confirmDialog } from "../components/dialog.js";
import { formatValue, fromDisplay, toDisplay } from "../lib/format.js";
import { poll } from "../lib/poll.js";
import "../components/picker.js";

// Live values follow the car's sensors; they change slowly
const REFRESH_MS = 30000;

const EMPTY = { name: "", capacity_kwh: "", efficiency: "", soc_entity: "", plugged_entity: "" };

/** Vehicles as they are: battery and the sensors of their own integration (settings area "Fahrzeuge"). */
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
    // poll() waits a full interval before its first call
    this._load();
    this._stopPolling = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling?.();
  }

  async _load() {
    try {
      const data = await api.vehicles();
      // A poll must not overwrite what is being saved
      if (!this._busy) this._data = data;
    } catch (err) {
      this._error = `Fahrzeuge nicht verfügbar: ${err.message}`;
    }
  }

  /** Values of the car's sensors as tiles (open card) or one line (closed card). */
  _liveValues(vehicle) {
    const live = this._data?.live?.[vehicle.id];
    if (!live) return [];
    const values = [];
    if (vehicle.soc_entity) {
      values.push({
        label: "Ladestand",
        value: live.soc_pct === null ? "–" : formatValue(live.soc_pct, "%"),
        hint: live.soc_unavailable ? "Sensor gerade nicht lesbar" : since(live.soc_updated),
      });
      if (live.energy_wh !== null) {
        values.push({ label: "Im Akku", value: formatValue(live.energy_wh, "Wh"), hint: `von ${formatValue(vehicle.capacity_wh, "Wh")}` });
      }
    }
    if (vehicle.plugged_entity) {
      values.push({
        label: "Angesteckt / zuhause",
        value: live.plugged === null ? (live.plugged_state ?? "–") : live.plugged ? "ja" : "nein",
        hint: "",
      });
    }
    return values;
  }

  _renderTiles(vehicle) {
    const values = this._liveValues(vehicle);
    return values.length
      ? html`<div class="tiles">${values.map((v) => html`<div class="tile">
          <div class="small muted">${v.label}</div>
          <div class="value">${v.value}</div>
          <div class="small muted">${v.hint}</div>
        </div>`)}</div>`
      : nothing;
  }

  render() {
    const data = this._data;
    return html`
      <div class="heading">
        <h1>Fahrzeuge</h1>
        <p class="muted">Was das Auto ist: Akku und die Sensoren seiner eigenen Integration. An welcher Wallbox es mit welcher
          Karte und welchem Ziel lädt, steht unter „Laden“.</p>
      </div>
      ${this._error ? html`<div class="alert error">${this._error}</div>` : nothing}
      ${data === null ? html`<p class="muted">Lade …</p>` : nothing}
      ${(data?.vehicles ?? []).map((v) => {
        const open = this._form !== null && this._editId === v.id;
        const where = (data.charging ?? []).filter((c) => c.vehicle_id === v.id)
          .map((c) => data.wallboxes.find((w) => w.id === c.wallbox_id)?.name)
          .filter(Boolean);
        return html`
          <section class="device ${open ? "" : "compact"}">
            <button class="device-toggle" aria-expanded=${open} @click=${() => (open ? this._cancel() : this._edit(v))}>
              <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
              <span>
                <span class="device-title">${v.name}</span>
                <span class="small muted block">${toDisplay(v.capacity_wh / 1000, "kWh")} kWh${where.length
                  ? ` · lädt an ${where.join(", ")}`
                  : " · noch an keiner Wallbox (→ Laden)"}</span>
              </span>
            </button>
            ${v.soc_entity ? nothing : html`<span class="small warn-text">kein Ladestand-Sensor</span>`}
            ${open
              ? html`${this._renderTiles(v)}${this._renderForm(v)}`
              : this._liveValues(v).length
                ? html`<div class="summary small">${this._liveValues(v).map((x) => `${x.label} ${x.value}`).join(" · ")}</div>`
                : nothing}
          </section>`;
      })}
      ${this._form !== null && this._editId === null
        ? html`<section class="device">${this._renderForm(null)}</section>`
        : html`<div><button class="secondary" @click=${() => this._edit(null)}>Fahrzeug hinzufügen …</button></div>`}
    `;
  }

  _renderForm(vehicle) {
    const form = this._form;
    const set = (field, value) => (this._form = { ...this._form, [field]: value });
    const options = (list, detail) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: detail(c) }));
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
          <label class="field">
            <span class="field-label">Ladewirkungsgrad</span>
            <span class="input-unit"><input inputmode="decimal" placeholder="90" .value=${form.efficiency}
              @input=${(e) => set("efficiency", e.target.value)}><span class="muted">%</span></span>
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
        </div>
        <div class="actions">
          <button class="primary" ?disabled=${this._busy} @click=${() => this._save()}>Speichern</button>
          <button class="secondary" @click=${() => this._cancel()}>Abbrechen</button>
          ${vehicle ? html`<span class="spacer"></span><button class="link danger" @click=${() => this._remove(vehicle)}>Entfernen</button>` : nothing}
        </div>
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
          efficiency: toDisplay(vehicle.charge_efficiency_pct, "%"),
          soc_entity: vehicle.soc_entity ?? "",
          plugged_entity: vehicle.plugged_entity ?? "",
        }
      : { ...EMPTY };
  }

  _cancel() {
    this._form = null;
    this._editId = null;
  }

  async _save() {
    const form = this._form;
    const capacity = fromDisplay(form.capacity_kwh, "kWh");
    const efficiency = form.efficiency === "" ? 90 : fromDisplay(form.efficiency, "%");
    if (!form.name.trim() || !capacity || capacity <= 0 || !efficiency) {
      this._error = "Bitte Name, Akkukapazität und einen gültigen Wirkungsgrad angeben.";
      return;
    }
    const previous = this._data.vehicles.find((v) => v.id === this._editId);
    const vehicle = {
      ...(previous ?? {}),
      name: form.name.trim(),
      capacity_wh: capacity * 1000,
      charge_efficiency_pct: efficiency,
      soc_entity: form.soc_entity || null,
      plugged_entity: form.plugged_entity || null,
    };
    const vehicles = previous
      ? this._data.vehicles.map((v) => (v.id === previous.id ? vehicle : v))
      : [...this._data.vehicles, vehicle];
    await this._store(vehicles);
    if (!this._error) this._cancel();
  }

  async _remove(vehicle) {
    if (!(await confirmDialog({
      title: `„${vehicle.name}“ entfernen?`,
      text: "Mit dem Fahrzeug gehen auch seine Ladeprofile an den Wallboxen.",
      confirm: "Entfernen",
      danger: true,
    }))) return;
    await this._store(this._data.vehicles.filter((v) => v.id !== vehicle.id));
    this._cancel();
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
    .device.compact { padding: 12px 18px; gap: 6px; }
    .device-title { font-weight: 600; font-size: 16px; display: block; }
    .device-toggle { display: flex; gap: 8px; align-items: flex-start; border: 0; background: none; padding: 0; cursor: pointer; text-align: left; }
    .caret { width: 14px; color: var(--sf-muted); line-height: 22px; }
    .editor { display: flex; flex-direction: column; gap: 14px; padding: 14px; background: var(--sf-subtle); border-radius: 8px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; font-size: 13px; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .spacer { flex: 1; }
    .primary, .secondary { min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .link { border: 0; background: none; padding: 0; color: var(--sf-primary-text); text-decoration: underline; cursor: pointer; font-size: 13px; }
    .danger { color: var(--sf-error-text); }
    .warn-text { color: var(--sf-error-text); }
    .summary { padding-left: 22px; color: var(--sf-text); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 10px 12px; }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
  `;
}

/** "vor 3 min" for an ISO time, empty without one. */
function since(iso) {
  if (!iso) return "";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "gerade eben";
  if (minutes < 60) return `vor ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `vor ${hours} h` : `vor ${Math.round(hours / 24)} Tagen`;
}

customElements.define("sf-vehicles", SunflowVehicles);
