import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { formatSigned, formatValue } from "./format.js";

/** Batteries of the inverters with their modules (setup step "Geräte", category "Speicher"). */
class SunflowBatteries extends LitElement {
  static properties = {
    _batteries: { state: true },
    _open: { state: true },
    _error: { state: true },
    _busy: { state: true },
  };

  constructor() {
    super();
    this._batteries = null;
    this._open = new Set();
    this._error = null;
    this._busy = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._batteries = await api.batteries();
      this._error = null;
    } catch (err) {
      this._error = `Speicher nicht verfügbar: ${err.message}`;
    }
  }

  render() {
    if (this._error) return html`<div class="alert error">${this._error}</div>`;
    return (this._batteries ?? []).map((b) => this._renderBattery(b));
  }

  _renderBattery(b) {
    const open = this._open.has(b.device_id);
    const assigned = b.modules.filter((m) => m.assigned);
    const sum = assigned.reduce((total, m) => total + (m.capacity_wh ?? 0), 0);
    return html`
      <section class="device ${open ? "" : "compact"}">
        <div class="device-head">
          <button class="device-toggle" aria-expanded=${open} @click=${() => this._toggle(b.device_id)}>
            <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
            <span>
              <span class="device-title">Speicher von ${b.inverter}</span>
              <span class="muted small device-sub">${assigned.length
                ? `${assigned.length} Module${b.auto ? " (automatisch zugeordnet)" : ""}`
                : "keine Module erkannt"}</span>
            </span>
          </button>
          <span class="chip ok">gehört zu ${b.inverter}</span>
        </div>
        <div class="summary small">${[
          b.soc_pct !== null ? `Ladestand ${Math.round(b.soc_pct)} %` : null,
          b.power_w !== null ? `Leistung ${formatSigned(b.power_w, "W")}` : null,
          b.capacity_wh ? `Kapazität ${formatValue(b.capacity_wh, "Wh")}` : null,
        ].filter(Boolean).join(" · ")}</div>
        ${open ? this._renderModules(b, sum) : nothing}
      </section>
    `;
  }

  _renderModules(b, sum) {
    return html`
      <div class="editor">
        <label class="inline">
          <input type="checkbox" .checked=${b.auto} ?disabled=${this._busy}
            @change=${(e) => this._save(b, e.target.checked ? null : b.modules.filter((m) => m.assigned).map((m) => m.ha_device_id))}>
          Module automatisch zuordnen (Untergeräte des Wechselrichters in Home Assistant)
        </label>
        <ul class="modules">
          ${b.modules.map((m) => html`<li>
            <label class="inline">
              <input type="checkbox" .checked=${m.assigned} ?disabled=${b.auto || this._busy}
                @change=${(e) => this._save(b, b.modules
                  .filter((o) => (o.ha_device_id === m.ha_device_id ? e.target.checked : o.assigned))
                  .map((o) => o.ha_device_id))}>
              ${m.name}</label>
            <span class="small muted">${[m.model, m.capacity_wh ? formatValue(m.capacity_wh, "Wh") : null, m.native ? null : "Untergerät eines anderen Wechselrichters"]
              .filter(Boolean).join(" · ")}</span>
          </li>`)}
        </ul>
        ${sum
          ? html`<span class="small muted">Summe der Module: ${formatValue(sum, "Wh")}. Die nutzbare Kapazität (Kenndaten) wird daraus
              vorgeschlagen, solange keine eigene eingetragen ist${b.capacity_wh ? ` – aktuell ${formatValue(b.capacity_wh, "Wh")}` : ""}.</span>`
          : nothing}
      </div>
    `;
  }

  _toggle(id) {
    const open = new Set(this._open);
    if (open.has(id)) open.delete(id);
    else open.add(id);
    this._open = open;
  }

  async _save(battery, modules) {
    this._busy = true;
    try {
      this._batteries = await api.setBatteryModules(battery.device_id, modules);
      this._error = null;
    } catch (err) {
      this._error = `Module nicht gespeichert: ${err.message}`;
    } finally {
      this._busy = false;
    }
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 12px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 10px; }
    .device.compact { padding: 12px 18px; gap: 6px; }
    .device-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: center; }
    .device-title { font-weight: 600; font-size: 16px; display: block; }
    .device-sub { display: block; }
    .device-toggle { display: flex; gap: 8px; align-items: flex-start; border: 0; background: none; padding: 0; cursor: pointer; text-align: left; }
    .caret { width: 14px; color: var(--sf-muted); line-height: 22px; }
    .summary { color: var(--sf-muted); padding-left: 22px; }
    .chip { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: var(--sf-ok-bg); color: var(--sf-ok-text); }
    .editor { display: flex; flex-direction: column; gap: 10px; padding: 14px; background: var(--sf-subtle); border-radius: 8px; }
    .modules { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
    .modules li { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; }
    .inline { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 14px; }
    .inline input { width: 18px; height: 18px; margin: 0; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
  `;
}

customElements.define("sf-batteries", SunflowBatteries);
