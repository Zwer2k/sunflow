import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import "../components/picker.js";
import { api } from "../lib/api.js";
import { t } from "../lib/i18n.js";
import { toggleSheet } from "../lib/display-toggles.js";
import { confirmDialog } from "../components/dialog.js";

const EMPTY = { name: "", power_entity: "", export_power_entity: "", export_positive: false, import_energy_entity: "", export_energy_entity: "" };

/**
 * Grid meters added by hand (devices area, "Zähler"): any sensors of Home Assistant as the grid
 * measurement, one signed power or import and export apart. Fires "changed" with the stored
 * device or {removed: device}.
 */
class SunflowGridMeters extends LitElement {
  static properties = {
    devices: { attribute: false },
    _form: { state: true },
    _editId: { state: true },
    _candidates: { state: true },
    _error: { state: true },
  };

  constructor() {
    super();
    this.devices = [];
    this._form = null;
    this._editId = null;
    this._candidates = null;
    this._error = null;
  }

  async _open(device = null) {
    try {
      this._candidates ??= await api.candidates();
    } catch (err) {
      this._error = t("Entitäten konnten nicht geladen werden: {error}", { error: err.message });
      return;
    }
    const role = (name) => device?.role_overrides[name]?.entity_id ?? "";
    this._editId = device?.id ?? null;
    this._form = device
      ? {
          name: device.name,
          power_entity: role("grid_power"),
          export_power_entity: role("grid_export_power"),
          export_positive: device.role_overrides.grid_power?.sign === "export_positive",
          import_energy_entity: role("grid_import_energy"),
          export_energy_entity: role("grid_export_energy"),
        }
      : { ...EMPTY, name: t("Netzzähler") };
  }

  _close() {
    this._form = null;
    this._editId = null;
  }

  async _save() {
    const form = this._form;
    const body = {
      name: form.name.trim(),
      type: "grid_meter",
      power_entity: form.power_entity || null,
      export_power_entity: form.export_power_entity || null,
      export_positive: !form.export_power_entity && form.export_positive,
      import_energy_entity: form.import_energy_entity || null,
      export_energy_entity: form.export_energy_entity || null,
    };
    try {
      const device = this._editId ? await api.updateManual(this._editId, body) : await api.addManual(body);
      this.dispatchEvent(new CustomEvent("changed", { detail: { device } }));
      this._close();
      this._error = null;
    } catch (err) {
      this._error = t("Netzzähler nicht gespeichert: {error}", { error: err.message });
    }
  }

  async _remove(device) {
    if (!(await confirmDialog({
      title: t("„{name}“ entfernen?", { name: device.name }),
      text: t("Die Netzmessung kommt dann wieder vom Wechselrichter oder einem anderen Zähler."),
      confirm: t("Entfernen"),
      danger: true,
    }))) {
      return;
    }
    try {
      await api.removeDevice(device.id);
      this.dispatchEvent(new CustomEvent("changed", { detail: { removed: device } }));
    } catch (err) {
      this._error = t("Gerät konnte nicht entfernt werden: {error}", { error: err.message });
    }
  }

  render() {
    return html`<section class="device">
      <div class="device-title">${t("Netzzähler von Hand")}</div>
      <p class="small muted help">${t("Für Zähler, die Sunflow nicht selbst erkennt (z. B. Tibber über die Cloud, DSMR, Shelly 3EM oder ein Template-Sensor): beliebige Sensoren als Netzmessung, entweder eine Leistung mit Vorzeichen oder Bezug und Einspeisung getrennt. Ein Netzzähler hat Vorrang vor der Messung des Wechselrichters; welche Quelle gilt, lässt sich unter „Bilanzprüfung“ ändern.")}</p>
      ${this._error ? html`<div class="alert error">${this._error}</div>` : nothing}
      ${this.devices.length
        ? html`<ul class="list">${this.devices.map((d) => html`<li>
            <div><strong>${d.name}</strong>
              <div class="small muted">${Object.values(d.role_overrides).map((o) => o.entity_id).filter(Boolean).join(", ")}</div></div>
            <span class="actions">
              <button class="secondary" ?disabled=${this._editId === d.id} @click=${() => this._open(d)}>${t("Bearbeiten")}</button>
              <button class="secondary danger" @click=${() => this._remove(d)}>${t("Entfernen")}</button>
            </span>
          </li>`)}</ul>`
        : nothing}
      ${this._form ? this._renderForm() : html`<div><button class="secondary" @click=${() => this._open()}>${t("Netzzähler hinzufügen …")}</button></div>`}
    </section>`;
  }

  _renderForm() {
    const form = this._form;
    const set = (field, value) => (this._form = { ...this._form, [field]: value });
    const options = (list) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} ${c.unit ?? ""}` }));
    const picker = (field, label, list, empty) => html`<div class="field">
      <span class="field-label">${label}</span>
      <sf-picker label=${label} empty-label=${empty ?? nothing} placeholder=${t("Name oder Entität suchen …")}
        .value=${form[field]} .options=${options(list)} @change=${(e) => set(field, e.detail.value)}></sf-picker>
    </div>`;
    const separate = Boolean(form.export_power_entity);
    return html`
      <div class="fields">
        <label class="field">
          <span class="field-label">${t("Name")}</span>
          <input .value=${form.name} @input=${(e) => set("name", e.target.value)}>
        </label>
        ${picker("power_entity", separate ? t("Netzbezug (Leistung)") : t("Netzleistung"), this._candidates.power)}
        ${picker("export_power_entity", t("Einspeisung (Leistung, optional)"), this._candidates.power, t("keine – Netzleistung hat ein Vorzeichen"))}
        ${picker("import_energy_entity", t("Netzbezug (Zähler, optional)"), this._candidates.energy, t("keiner"))}
        ${picker("export_energy_entity", t("Einspeisung (Zähler, optional)"), this._candidates.energy, t("keiner"))}
      </div>
      ${separate
        ? html`<p class="small muted help">${t("Sunflow rechnet Bezug minus Einspeisung. Beide Sensoren zeigen positive Werte.")}</p>`
        : html`<label class="inline">
            <input type="checkbox" .checked=${form.export_positive} @change=${(e) => set("export_positive", e.target.checked)}>
            ${t("Sensor zeigt Einspeisung positiv (sonst: Bezug positiv, Einspeisung negativ)")}
          </label>`}
      <div class="actions">
        <button class="ok" ?disabled=${!form.power_entity || !form.name.trim()} @click=${() => this._save()}>
          ${this._editId ? t("Speichern") : t("Hinzufügen")}
        </button>
        <button class="secondary" @click=${() => this._close()}>${t("Abbrechen")}</button>
      </div>`;
  }

  static styles = [toggleSheet, css`
    :host { display: block; }
    * { box-sizing: border-box; }
    button, select, input { font: inherit; color: inherit; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 12px; }
    .device-title { font-weight: 600; font-size: 16px; display: block; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; font-size: 13px; }
    .field input { min-height: 44px; font-size: 14px; border: 1px solid var(--sf-border-strong); border-radius: 8px;
      background: var(--sf-surface); color: var(--sf-text); padding: 0 10px; }
    .inline { display: flex; align-items: center; gap: 8px; font-size: 14px; }
    .list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
    .list li { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;
      padding: 10px 12px; border: 1px solid var(--sf-border); border-radius: 8px; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .secondary, button.ok { min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    button.ok { border: 0; background: var(--sf-ok); color: var(--sf-on-ok); }
    .danger { color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `];
}

customElements.define("sf-grid-meters", SunflowGridMeters);
