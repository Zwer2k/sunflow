import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { confirmDialog } from "../components/dialog.js";
import { formatValue, fromDisplay, toDisplay } from "../lib/format.js";
import { poll } from "../lib/poll.js";
import "../components/picker.js";

// Live values follow the car's sensors; they change slowly
const REFRESH_MS = 30000;

// Defaults of the 12 V battery guard (as in the backend)
const AUX_DEFAULTS = { min_voltage_v: 12.2, charge_min: 5, pause_min: 60 };
const EMPTY = { name: "", capacity_kwh: "", efficiency: "", max_1p_kw: "", max_3p_kw: "", soc_entity: "", plugged_entity: "", aux: auxForm({}) };

/** The 12 V guard as form strings. */
function auxForm(guard) {
  const value = (key) => toDisplay(guard[key] ?? AUX_DEFAULTS[key], key === "min_voltage_v" ? "V" : "min");
  return {
    voltage_entity: guard.voltage_entity ?? "",
    min_voltage_v: value("min_voltage_v"),
    charge_min: value("charge_min"),
    pause_min: value("pause_min"),
    one_phase: guard.one_phase ?? true,
    current_a: guard.current_a ? String(guard.current_a) : "",
  };
}

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
    if (vehicle.aux_battery?.voltage_entity) {
      const voltage = live.aux_voltage;
      values.push({
        label: "12-V-Batterie",
        value: voltage === null ? "–" : `${toDisplay(voltage, "V")} V`,
        hint: voltage !== null && voltage < vehicle.aux_battery.min_voltage_v ? "unter der Grenze" : since(live.aux_updated),
      });
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
          <label class="field">
            <span class="field-label">Höchste Ladeleistung einphasig (optional)</span>
            <span class="input-unit"><input inputmode="decimal" placeholder="z. B. 7,4" .value=${form.max_1p_kw}
              @input=${(e) => set("max_1p_kw", e.target.value)}><span class="muted">kW</span></span>
          </label>
          <label class="field">
            <span class="field-label">Höchste Ladeleistung dreiphasig (optional)</span>
            <span class="input-unit"><input inputmode="decimal" placeholder="z. B. 11" .value=${form.max_3p_kw}
              @input=${(e) => set("max_3p_kw", e.target.value)}><span class="muted">kW</span></span>
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
        ${this._renderAux(form, options)}
        <div class="actions">
          <button class="primary" ?disabled=${this._busy} @click=${() => this._save()}>Speichern</button>
          <button class="secondary" @click=${() => this._cancel()}>Abbrechen</button>
          ${vehicle ? html`<span class="spacer"></span><button class="link danger" @click=${() => this._remove(vehicle)}>Entfernen</button>` : nothing}
        </div>
      </div>
    `;
  }

  _renderAux(form, options) {
    const aux = form.aux;
    const set = (field, value) => (this._form = { ...this._form, aux: { ...aux, [field]: value } });
    const number = (field, label, unit, placeholder) => html`<label class="field">
      <span class="field-label">${label}</span>
      <span class="input-unit"><input inputmode="decimal" placeholder=${placeholder} .value=${aux[field]}
        ?disabled=${!aux.voltage_entity} @input=${(e) => set(field, e.target.value)}><span class="muted">${unit}</span></span>
    </label>`;
    return html`<fieldset class="group">
      <legend>12-V-Batterie schützen</legend>
      <p class="small muted">Manche Autos (z. B. Fiat 500e) entladen ihre 12-V-Batterie, wenn sie lange angesteckt sind,
        ohne zu laden. Fällt die Spannung unter die Grenze, lädt die Wallbox für kurze Zeit mit der kleinsten Leistung –
        dabei lädt das Auto auch die 12-V-Batterie. Wirkt nur, solange Sunflow die Wallbox steuert.</p>
      <div class="fields">
        <div class="field">
          <span class="field-label">Spannung der 12-V-Batterie</span>
          <sf-picker label="12-V-Batterie" empty-label="nicht überwachen" placeholder="Sensor des Autos …" .value=${aux.voltage_entity}
            .options=${options(this._candidates?.voltage, (c) => `${c.state ?? "–"} V`)}
            @change=${(e) => set("voltage_entity", e.detail.value)}></sf-picker>
        </div>
        ${number("min_voltage_v", "Laden unter", "V", "12,2")}
        ${number("charge_min", "Laden für", "min", "5")}
        ${number("pause_min", "Frühestens wieder nach", "min", "60")}
        ${number("current_a", "Ladestrom (leer: kleinster der Wallbox)", "A", "6")}
        <label class="check">
          <input type="checkbox" .checked=${aux.one_phase} ?disabled=${!aux.voltage_entity}
            @change=${(e) => set("one_phase", e.target.checked)}>
          <span>einphasig laden (wenn die Wallbox umschalten kann)</span>
        </label>
      </div>
    </fieldset>`;
  }

  /** The 12 V guard from the form; null with an invalid value. */
  _auxFromForm(aux) {
    const parse = (field, unit) => (aux[field] === "" ? AUX_DEFAULTS[field] : fromDisplay(aux[field], unit));
    const guard = {
      voltage_entity: aux.voltage_entity || null,
      min_voltage_v: parse("min_voltage_v", "V"),
      charge_min: parse("charge_min", "min"),
      pause_min: parse("pause_min", "min"),
      one_phase: aux.one_phase,
      current_a: aux.current_a === "" ? null : Math.round(fromDisplay(aux.current_a, "A")),
    };
    const ok = guard.min_voltage_v >= 10 && guard.min_voltage_v <= 14 && guard.charge_min >= 1 && guard.charge_min <= 120
      && guard.pause_min >= 5 && guard.pause_min <= 1440 && (guard.current_a === null || (guard.current_a >= 6 && guard.current_a <= 32));
    return ok ? guard : null;
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
          max_1p_kw: vehicle.max_power_1p_w ? toDisplay(vehicle.max_power_1p_w / 1000, "kW") : "",
          max_3p_kw: vehicle.max_power_3p_w ? toDisplay(vehicle.max_power_3p_w / 1000, "kW") : "",
          soc_entity: vehicle.soc_entity ?? "",
          plugged_entity: vehicle.plugged_entity ?? "",
          aux: auxForm(vehicle.aux_battery ?? {}),
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
    const limit = (text) => (text === "" ? null : fromDisplay(text, "kW"));
    const [max1p, max3p] = [limit(form.max_1p_kw), limit(form.max_3p_kw)];
    if ([max1p, max3p].some((w) => w !== null && !(w > 0 && w <= 50))) {
      this._error = "Höchste Ladeleistung: leer lassen oder eine Leistung bis 50 kW angeben.";
      return;
    }
    const aux = this._auxFromForm(form.aux);
    if (aux === null) {
      this._error = "12-V-Batterie: Grenze 10–14 V, Ladezeit 1–120 min, Pause 5–1440 min, Strom 6–32 A.";
      return;
    }
    const previous = this._data.vehicles.find((v) => v.id === this._editId);
    const vehicle = {
      ...(previous ?? {}),
      name: form.name.trim(),
      capacity_wh: capacity * 1000,
      charge_efficiency_pct: efficiency,
      max_power_1p_w: max1p === null ? null : Math.round(max1p * 1000),
      max_power_3p_w: max3p === null ? null : Math.round(max3p * 1000),
      soc_entity: form.soc_entity || null,
      plugged_entity: form.plugged_entity || null,
      aux_battery: aux,
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
    .group { border: 1px solid var(--sf-border); border-radius: 8px; padding: 12px 14px; margin: 0; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
    .group legend { font-weight: 600; font-size: 14px; padding: 0 4px; }
    .group p { margin: 0; }
    .check { display: flex; gap: 8px; align-items: center; font-size: 14px; min-height: 44px; }
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
