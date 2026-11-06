import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { poll } from "../lib/poll.js";
import { formatPower, formatValue } from "../lib/format.js";
import { MODES, currentMode, modeChanges, overrideFor, untilFor, untilText } from "../lib/device-modes.js";
import { locale, t } from "../lib/i18n.js";

const REFRESH_MS = 30 * 1000;
// Overrides from the live view last until midnight, like the control tab's default
const DURATION = "midnight";
const SHORT = { cost: "€", pv_only: t("PV"), fast: "⚡", pause: "⏸" };
const time = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });

/** The consumers pinned in the control tab, one compact row each: mode and "plugged in". */
class SunflowQuickControls extends LitElement {
  static properties = {
    _devices: { state: true },
    _busy: { state: true },
    _error: { state: true },
  };

  constructor() {
    super();
    this._devices = [];
    this._busy = false;
    this._error = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stop = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stop?.();
  }

  async _load() {
    try {
      this._devices = (await api.control()).devices.filter((d) => d.live);
      this._error = null;
    } catch (err) {
      this._error = err.message;
    }
  }

  async _apply(action) {
    this._busy = true;
    try {
      await action();
      await this._load();
    } catch (err) {
      this._error = t("Änderung fehlgeschlagen: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  _setMode(device, mode) {
    const next = overrideFor(device, modeChanges(device, mode));
    return this._apply(() =>
      next === null ? api.clearDeviceOverride(device.id) : api.setDeviceOverride(device.id, { ...next, until: untilFor(DURATION) }),
    );
  }

  render() {
    if (!this._devices.length) return nothing;
    return html`<section aria-label=${t("Schnellsteuerung")}>
      ${this._error ? html`<p class="small error" role="alert">${this._error}</p>` : nothing}
      ${this._devices.map((d) => this._renderDevice(d))}
    </section>`;
  }

  _renderDevice(device) {
    const mode = currentMode(device);
    const unit = device.kind === "heater" ? "°C" : "%";
    const next = device.windows?.[0];
    const status = [
      formatPower(device.power_w, device.power_display),
      device.level !== null && device.level !== undefined ? `${Math.round(device.level)} ${unit}` : null,
      device.kind === "vehicle" && device.connected === false ? t("nicht angesteckt") : null,
      device.reason ? null : next ? t("ab {time}", { time: time.format(new Date(next.start)) }) : null,
    ].filter(Boolean);
    const plan = device.reason
      ? t("Nicht eingeplant: {reason}", { reason: device.reason })
      : next
        ? t("Geplant: {windows}", { windows: `${time.format(new Date(next.start))}–${time.format(new Date(next.end))} (${formatValue(next.energy_wh, "Wh")})` })
        : t("Kein Zeitfenster geplant");
    const charger = device.charger;
    return html`<div class="device" title=${plan}>
      <div class="name">
        <strong>${device.name}</strong>
        <span class="small muted">${status.join(" · ")}${device.override
          ? html` · <span class="changed">${t("angepasst")} ${untilText(device.override.until)}</span>`
          : nothing}</span>
      </div>
      <div class="controls">
        ${charger?.manual
          ? html`<button class="plug ${charger.ready ? "on" : ""}" aria-pressed=${charger.ready} ?disabled=${this._busy}
              title=${charger.ready ? t("Nicht mehr eingesteckt") : t("Jetzt eingesteckt")}
              aria-label=${t("{name} eingesteckt", { name: device.name })}
              @click=${() => this._apply(() => api.setPlugged(device.id, !charger.ready))}>🔌</button>`
          : nothing}
        <div class="segmented" role="group" aria-label=${t("Modus {name}", { name: device.name })}>
          ${Object.entries(MODES).map(
            ([value, label]) => html`<button class=${mode === value ? "on" : ""} aria-pressed=${mode === value} ?disabled=${this._busy}
              title=${value === device.default.strategy ? `${label} (${t("Standard")})` : label} aria-label=${label}
              @click=${() => this._setMode(device, value)}>${SHORT[value]}</button>`,
          )}
        </div>
      </div>
    </div>`;
  }

  static styles = css`
    :host { display: block; }
    section {
      display: flex; flex-direction: column; border: 1px solid var(--sf-border); border-radius: 12px;
      background: var(--sf-surface); padding: 2px 12px;
    }
    .device { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 12px; padding: 8px 0; }
    .device + .device { border-top: 1px solid var(--sf-border); }
    .name { display: flex; flex-direction: column; min-width: 0; gap: 1px; }
    .name strong { font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .small { font-size: 12px; }
    .muted { color: var(--sf-muted); }
    .changed { color: var(--sf-warn-text); }
    .error { color: var(--sf-error-text); margin: 6px 0 0; }
    .controls { display: flex; align-items: center; gap: 6px; margin-left: auto; }
    .segmented { display: flex; gap: 2px; background: var(--sf-subtle); padding: 3px; border-radius: 9px; }
    button { font: inherit; color: inherit; }
    .segmented button {
      min-width: 38px; min-height: 32px; padding: 0 8px; border: 0; border-radius: 7px; background: transparent;
      cursor: pointer; font-size: 13px; color: var(--sf-muted);
    }
    .segmented button.on { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.1); }
    .plug {
      min-width: 38px; min-height: 38px; border: 1px solid var(--sf-border); border-radius: 9px; background: transparent;
      cursor: pointer; font-size: 16px; filter: grayscale(1); opacity: 0.6;
    }
    .plug.on { filter: none; opacity: 1; border-color: var(--sf-ok); background: var(--sf-ok-bg); }
  `;
}

customElements.define("sf-quick-controls", SunflowQuickControls);
