import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { poll } from "../lib/poll.js";
import { formatPower, formatValue } from "../lib/format.js";
import "./target-dial.js";
import { MODES, currentMode, defaultTarget, targetRange, modeChanges, overrideFor, untilFor, untilText } from "../lib/device-modes.js";
import { locale, t } from "../lib/i18n.js";
import { recall, remember } from "../lib/view-cache.js";

const REFRESH_MS = 30 * 1000;
const KEPT_MS = 10 * 60 * 1000;
const MENU_WIDTH = 190;
// The plan is recomputed in the background after a change
const REPLAN_DELAY_MS = 2500;
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
    _menu: { state: true },
  };

  constructor() {
    super();
    this._devices = recall("quick", KEPT_MS) ?? [];
    this._busy = false;
    this._error = null;
    this._menu = null;
    this._outside = (e) => {
      if (!e.composedPath().some((el) => el.classList?.contains("vehicle"))) this._closeMenu();
    };
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stop = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stop?.();
    clearTimeout(this._replanTimer);
    document.removeEventListener("pointerdown", this._outside);
  }

  _toggleMenu(device, e) {
    if (this._menu === device.id) return this._closeMenu();
    // Opens towards the side with room
    this._menuLeft = e.currentTarget.getBoundingClientRect().left + MENU_WIDTH < window.innerWidth;
    this._menu = device.id;
    document.addEventListener("pointerdown", this._outside);
  }

  _closeMenu() {
    this._menu = null;
    document.removeEventListener("pointerdown", this._outside);
  }

  _chooseVehicle(device, vehicleId) {
    this._closeMenu();
    return this._apply(() => api.chooseVehicle(device.id, vehicleId));
  }

  async _load() {
    try {
      this._devices = (await api.control()).devices.filter((d) => d.live);
      remember("quick", this._devices);
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
      clearTimeout(this._replanTimer);
      this._replanTimer = setTimeout(() => this._load(), REPLAN_DELAY_MS);
    } catch (err) {
      this._error = t("Änderung fehlgeschlagen: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  _setTarget(device, target) {
    const next = overrideFor(device, { target });
    return this._apply(() =>
      next === null ? api.clearDeviceOverride(device.id) : api.setDeviceOverride(device.id, { ...next, until: untilFor(DURATION) }),
    );
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
        <span class="small muted status">${device.override
          ? html`<span class="changed" role="img" title=${`${t("angepasst")} ${untilText(device.override.until)}`}
              aria-label=${`${t("angepasst")} ${untilText(device.override.until)}`}>🕐</span> `
          : nothing}${status.join(" · ")}</span>
      </div>
      <div class="controls">
        ${device.vehicles?.length && device.manual_choice ? this._renderVehicle(device) : nothing}
        ${targetRange(device) ? this._renderTarget(device, targetRange(device)) : nothing}
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

  /** Which car is at the wallbox: the recognized one on the button, the choice in a dropdown. */
  _renderVehicle(device) {
    const open = this._menu === device.id;
    const selected = device.selected_vehicle ?? null;
    const options = [{ id: null, name: t("automatisch erkennen") }, ...device.vehicles];
    return html`<div class="vehicle">
      <button class="menu-button" aria-haspopup="listbox" aria-expanded=${open} ?disabled=${this._busy}
        title=${t("Fahrzeug an {name}", { name: device.name })} @click=${(e) => this._toggleMenu(device, e)}
        @keydown=${(e) => e.key === "Escape" && this._closeMenu()}>
        <svg class="car" viewBox="0 0 24 24" role="img" aria-label=${t("Fahrzeug")}><path d="M3 16v-4l2-5h14l2 5v4zM3 16v3h3v-3M18 16v3h3v-3M7 12h0M17 12h0"></path></svg>
        ${selected ? device.vehicles.find((v) => v.id === selected)?.name ?? selected : t("erkennen")}
        <span aria-hidden="true">▾</span>
      </button>
      ${open
        ? html`<ul class="menu ${this._menuLeft ? "left" : ""}" role="listbox" aria-label=${t("Fahrzeug an {name}", { name: device.name })}
            @keydown=${(e) => e.key === "Escape" && this._closeMenu()}>
            ${options.map(
              (o) => html`<li role="option" aria-selected=${o.id === selected}>
                <button class=${o.id === selected ? "on" : ""} @click=${() => this._chooseVehicle(device, o.id)}>${o.name}</button>
              </li>`,
            )}
          </ul>`
        : nothing}
    </div>`;
  }

  _renderTarget(device, targets) {
    const own = device.override?.target ?? null;
    const target = own ?? defaultTarget(device);
    return html`<sf-target-dial .value=${own} .defaultValue=${defaultTarget(device)} .min=${targets.min} .max=${targets.max}
      unit=${targets.unit} ?active=${own !== null} ?disabled=${this._busy}
      text=${target !== null && target !== undefined ? `🎯 ${Math.round(target)} ${targets.unit}` : "🎯"}
      label=${t("Ziel {name}", { name: device.name })} @change=${(e) => this._setTarget(device, e.detail.value)}></sf-target-dial>`;
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
    .status { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .changed { font-size: 12px; cursor: help; }
    .error { color: var(--sf-error-text); margin: 6px 0 0; }
    .controls { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 6px; margin-left: auto; }
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
    .vehicle { position: relative; }
    .menu-button { display: inline-flex; align-items: center; gap: 5px; }
    .car { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; flex: none; }
    .menu-button, sf-target-dial::part(button) {
      min-height: 38px; padding: 0 10px; border: 1px solid var(--sf-border); border-radius: 9px; background: transparent;
      cursor: pointer; font-size: 13px; white-space: nowrap; max-width: 140px; overflow: hidden; text-overflow: ellipsis;
    }
    sf-target-dial::part(button) { color: var(--sf-text); font-weight: 400; box-shadow: none; }
    sf-target-dial[active]::part(button) { border-color: var(--sf-primary); background: var(--sf-primary-bg); font-weight: 600; }
    .menu {
      position: absolute; top: calc(100% + 4px); right: 0; z-index: 20; min-width: 170px; margin: 0; padding: 4px; list-style: none;
      border: 1px solid var(--sf-border); border-radius: 10px; background: var(--sf-surface); box-shadow: 0 6px 24px rgb(0 0 0 / 0.18);
    }
    .menu.left { right: auto; left: 0; }
    .menu button {
      width: 100%; min-height: 36px; padding: 0 10px; border: 0; border-radius: 7px; background: transparent;
      text-align: left; cursor: pointer; font-size: 14px;
    }
    .menu button:hover { background: var(--sf-subtle); }
    .menu button.on { font-weight: 600; background: var(--sf-primary-bg); }
    .plug.on { filter: none; opacity: 1; border-color: var(--sf-ok); background: var(--sf-ok-bg); }
  `;
}

customElements.define("sf-quick-controls", SunflowQuickControls);
