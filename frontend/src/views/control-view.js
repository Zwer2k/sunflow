import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { confirmDialog } from "../components/dialog.js";
import { poll } from "../lib/poll.js";
import { CATEGORY_LABELS, formatPower, formatValue } from "../lib/format.js";
import { plugInHints } from "../lib/plug-in.js";
import { STRATEGIES, strategyDetails } from "../lib/strategies.js";
import { decimal, locale, t } from "../lib/i18n.js";
import { isOpen, setOpen } from "../lib/collapse.js";

const REFRESH_MS = 30 * 1000;
// The plan is recomputed in the background after a change
const REPLAN_DELAY_MS = 2500;

const ACTION_LABELS = {
  auto: t("Eigenverbrauch"),
  hold: t("Speicher halten"),
  force_charge: t("Laden aus dem Netz"),
  force_discharge: t("Entladen ins Netz"),
  limit_discharge: t("Entladen begrenzt"),
  limit_charge: t("Laden begrenzt"),
  charge: t("Lädt"),
  follow: t("Lädt mit PV-Überschuss"),
  stop: t("Laden gesperrt"),
  release: t("Gerät regelt selbst"),
};
// Writes of the log that are no battery or charge action
const LOG_ACTIONS = {
  take_over: t("Steuerung übernommen"),
  mode: t("Modus zurückgestellt"),
  authorize: t("Ladevorgang freigegeben"),
  pv_set: t("PV begrenzt"),
  pv_release: t("PV-Begrenzung aufgehoben"),
  phases_one: t("auf 1 Phase"),
  phases_three: t("auf 3 Phasen"),
  phases_restore: t("Phasenmodus zurückgestellt"),
  warning: t("Warnung"),
};
const LOG_HOURS = { 24: t("24 Stunden"), 72: t("3 Tage"), 336: t("14 Tage") };
// More rows make the page slow and say nothing more
const LOG_ROWS = 300;
const MODES = {
  cost: t("Kostenoptimal"),
  pv_only: t("Nur PV"),
  fast: t("Sofort"),
  pause: t("Pause"),
};
const KIND_LABELS = { vehicle: t("Wallbox"), heater: t("Wärme"), load: t("Verbraucher") };
const TARGETS = { vehicle: [50, 80, 100], heater: [45, 55, 60] };
const DURATIONS = {
  midnight: t("bis Mitternacht"),
  hours4: t("4 Stunden"),
  open: t("bis auf Weiteres"),
};

const time = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });
const dayTime = new Intl.DateTimeFormat(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
const andList = new Intl.ListFormat(locale(), { type: "conjunction" });

function untilFor(duration) {
  if (duration === "open") return null;
  const until = new Date();
  if (duration === "hours4") until.setHours(until.getHours() + 4);
  else until.setHours(24, 0, 0, 0);
  return until.toISOString();
}

function untilText(until) {
  if (!until) return t("bis auf Weiteres");
  const date = new Date(until);
  return date.toDateString() === new Date().toDateString() || date.getHours() === 0
    ? date.getHours() === 0 && date.getMinutes() === 0 ? t("bis Mitternacht") : t("bis {time}", { time: time.format(date) })
    : t("bis {time}", { time: dayTime.format(date) });
}

/** Quick control: battery strategy, per-consumer mode and target, priorities. */
class SunflowControl extends LitElement {
  static properties = {
    _data: { state: true },
    _error: { state: true },
    _duration: { state: true },
    _busy: { state: true },
    _editing: { state: true },
    _takeover: { state: true },
    _takeoverOpen: { state: true },
    _log: { state: true },
    _logDevice: { state: true },
    _logHours: { state: true },
    _logForeign: { state: true },
    _logErrors: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._error = null;
    this._duration = "midnight";
    this._busy = false;
    this._editing = null;
    this._takeoverOpen = isOpen("control.takeover");
    this._log = null;
    this._logDevice = "";
    this._logHours = 24;
    this._logForeign = false;
    this._logErrors = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stopPolling = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling();
    clearTimeout(this._replanTimer);
    clearTimeout(this._takeoverTimer);
  }

  async _load() {
    try {
      [this._data, this._takeover] = await Promise.all([api.control(), api.takeover(), this._loadLog()]);
      this._error = null;
    } catch (err) {
      this._error = t("Steuerung nicht verfügbar: {error}", { error: err.message });
    }
  }

  async _loadLog() {
    try {
      this._log = (await api.controlLog(this._logHours, this._logDevice)).entries;
    } catch {
      // Not essential: the rest of the page still works
      this._log = null;
    }
  }

  _setLogFilter(changes) {
    Object.assign(this, changes);
    this._loadLog();
  }

  async _apply(action) {
    this._busy = true;
    try {
      await action();
      this._error = null;
      await this._load();
      // Show the windows of the new plan once the background replan is done
      clearTimeout(this._replanTimer);
      this._replanTimer = setTimeout(() => this._load(), REPLAN_DELAY_MS);
    } catch (err) {
      this._error = t("Änderung fehlgeschlagen: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  _setStrategy(strategy) {
    const data = this._data.strategy;
    if (strategy === data.default) return this._apply(() => api.clearStrategyOverride());
    return this._apply(() => api.setStrategyOverride({ strategy, until: untilFor(this._duration) }));
  }

  _setDevice(device, changes) {
    const current = device.override ?? { strategy: null, target: null, paused: false };
    const next = { ...current, ...changes };
    if (next.strategy === null && next.target === null && !next.paused) {
      return this._apply(() => api.clearDeviceOverride(device.id));
    }
    return this._apply(() => api.setDeviceOverride(device.id, { ...next, until: untilFor(this._duration) }));
  }

  _setMode(device, mode) {
    if (mode === "pause") return this._setDevice(device, { paused: true, strategy: null });
    // Choosing the default strategy drops the strategy override instead of storing a copy
    return this._setDevice(device, { paused: false, strategy: mode === device.default.strategy ? null : mode });
  }

  _move(index, step) {
    const order = this._data.priorities.map((p) => p.id);
    const target = index + step;
    if (target < 0 || target >= order.length) return undefined;
    [order[index], order[target]] = [order[target], order[index]];
    return this._apply(() => api.setPriorities(order));
  }

  render() {
    const data = this._data;
    return html`
      <div class="heading">
        <div>
          <h1>${t("Steuerung")}</h1>
          <p class="muted">${t("Vorübergehende Eingriffe – danach gilt wieder der Standard aus den Einstellungen.")}
            ${t("Sunflow plant nach jeder Änderung sofort neu.")}</p>
        </div>
        <label class="duration">
          <span class="small muted">${t("Eingriffe gelten")}</span>
          <select aria-label=${t("Eingriffe gelten")} @change=${(e) => (this._duration = e.target.value)}>
            ${Object.entries(DURATIONS).map(
              ([value, label]) => html`<option value=${value} ?selected=${this._duration === value}>${label}</option>`,
            )}
          </select>
        </label>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${data === null && !this._error ? html`<p class="muted">${t("Lade …")}</p>` : nothing}
      ${plugInHints(data?.plug_in_hints)}
      ${this._takeover?.devices.length ? this._renderTakeover(this._takeover) : nothing}
      ${data ? html`
          ${data.strategy.available ? this._renderBattery(data.strategy) : nothing}
          ${data.devices.length
            ? html`<section>
                <h2>${t("Verbraucher")}</h2>
                <div class="cards">${this._orderedDevices(data).map((d) => this._renderDevice(d))}</div>
              </section>`
            : html`<p class="muted">${t("Noch keine Verbraucher eingerichtet (Wallbox, Warmwasser, eigene Geräte).")}</p>`}
          ${data.priorities.length > 1 ? this._renderPriorities(data.priorities, data.condition_sources) : nothing}
        ` : nothing}
      ${this._takeover?.devices.length && this._log ? this._renderLog(this._log, this._takeover.devices) : nothing}
    `;
  }

  _renderLog(log, devices) {
    const entries = log
      .filter((e) => (!this._logForeign || e.kind === "extern") && (!this._logErrors || e.error || e.action === "warning"))
      .slice(0, LOG_ROWS);
    const today = new Date().toDateString();
    const when = (iso) => (new Date(iso).toDateString() === today ? time : dayTime).format(new Date(iso));
    return html`
      <section class="card">
        <div class="card-head">
          <div>
            <h2>${t("Protokoll")}</h2>
            <p class="small muted">${t("Was Sunflow an den gesteuerten Geräten geschrieben hat, mit Schreibfehlern (ein Fehler, der jede Minute wiederkommt, steht einmal da) – und Änderungen, die nicht von Sunflow kamen, mit dem, was Home Assistant über den Auslöser weiß.")}
              ${t("Geprüft wird jede Minute.")}</p>
          </div>
        </div>
        <div class="log-filters">
          <select aria-label=${t("Gerät")} @change=${(e) => this._setLogFilter({ _logDevice: e.target.value })}>
            <option value="" ?selected=${!this._logDevice}>${t("Alle Geräte")}</option>
            ${devices.map((d) => html`<option value=${d.device_id} ?selected=${this._logDevice === d.device_id}>${d.name}</option>`)}
          </select>
          <select aria-label=${t("Zeitraum")} @change=${(e) => this._setLogFilter({ _logHours: Number(e.target.value) })}>
            ${Object.entries(LOG_HOURS).map(
              ([hours, label]) => html`<option value=${hours} ?selected=${this._logHours === Number(hours)}>${label}</option>`,
            )}
          </select>
          <label class="switch small">
            <input type="checkbox" .checked=${this._logForeign} @change=${(e) => (this._logForeign = e.target.checked)}>
            <span>${t("nur fremde Änderungen")}</span>
          </label>
          <label class="switch small">
            <input type="checkbox" .checked=${this._logErrors} @change=${(e) => (this._logErrors = e.target.checked)}>
            <span>${t("nur Fehler und Warnungen")}</span>
          </label>
        </div>
        ${entries.length
          ? html`<ul class="log">
              ${entries.map((e) => html`<li class=${e.kind === "extern" ? "foreign" : ""}>
                <span class="small muted when">${when(e.time)}</span>
                <span class="small"><strong>${e.device}</strong> · ${e.kind === "extern"
                  ? html`<span class="warn-text">${t("nicht von Sunflow geändert: {name} {old} → {new}", { name: e.name ?? e.entity_id, old: e.old, new: e.new })}</span>
                      <span class="muted">· ${t("durch {source}", { source: e.source })}</span>`
                  : html`${ACTION_LABELS[e.action] ?? LOG_ACTIONS[e.action] ?? e.action}${e.power_w ? ` · ${formatPower(e.power_w)}` : ""}${e.reason
                      ? html` <span class="muted">· ${e.reason}</span>`
                      : nothing}${e.calls?.length
                      ? html`<span class="muted calls">${e.calls.map((c) => `${c.entity_id} → ${Object.values(c.data ?? {}).join(", ") || c.service}${c.status === "done" ? "" : ` (${c.status})`}`).join(" · ")}</span>`
                      : nothing}${e.error ? html`<span class="warn-text"> · ${t("Fehler: {error}", { error: e.error })}</span>` : nothing}`}</span>
              </li>`)}
            </ul>`
          : html`<p class="small muted">${this._logErrors
              ? t("Keine Fehler im gewählten Zeitraum.")
              : this._logForeign
                ? t("Keine fremden Änderungen im gewählten Zeitraum.")
                : t("Keine Einträge im gewählten Zeitraum.")}</p>`}
      </section>
    `;
  }

  _renderTakeover(takeover) {
    const open = this._takeoverOpen;
    const controlled = takeover.enabled ? takeover.devices.filter((d) => d.enabled && !d.blocked).length : 0;
    const summary = takeover.enabled
      ? t("{controlled} von {total} Geräten gesteuert", { controlled, total: takeover.devices.length })
      : t("Aus: Sunflow plant nur");
    const problems = takeover.devices.filter((d) => d.status?.error || d.status?.warning).length;
    return html`
      <section class="card takeover ${takeover.enabled ? "on" : ""}">
        <div class="card-head">
          <div>
            <button class="device-toggle" aria-expanded=${open} @click=${() => setOpen("control.takeover", (this._takeoverOpen = !open))}>
              <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
              <h2>${t("Sunflow steuert")}</h2>
            </button>
            ${open
              ? html`<p class="small muted">${takeover.enabled
                  ? t("Sunflow setzt den Plan an den Geräten um. Ausschalten gibt sie zurück: Speicher in Eigenverbrauch und vorherigen Modus, Ladepunkte an ihre eigene Logik.")
                  : t("Aus: Sunflow plant nur, die Geräte arbeiten wie bisher.")}</p>`
              : html`<p class="small muted">${summary}${problems
                  ? html` · <span class="warn-text">${problems === 1 ? t("1 Gerät mit Fehler oder Warnung") : t("{count} Geräte mit Fehler oder Warnung", { count: problems })}</span>`
                  : nothing}</p>`}
          </div>
          <label class="switch">
            <input type="checkbox" .checked=${takeover.enabled} ?disabled=${this._busy}
              @change=${(e) => this._switchTakeover({ enabled: e.target.checked }, e.target)}>
            <span>${takeover.enabled ? t("An") : t("Aus")}</span>
          </label>
        </div>
        ${takeover.paused ? html`<div class="alert warn">⚠ ${takeover.paused}</div>` : nothing}
        ${open ? this._renderTakeoverDevices(takeover) : nothing}
      </section>
    `;
  }

  _renderTakeoverDevices(takeover) {
    const time = (iso) => new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
    return html`
        <ul class="takeover-devices">
          ${takeover.devices.map((d) => {
            const s = d.status;
            return html`<li>
              <label class="switch">
                <input type="checkbox" .checked=${d.enabled} ?disabled=${this._busy || Boolean(d.blocked)}
                  @change=${(e) => this._switchTakeover({ device_id: d.device_id, device_enabled: e.target.checked }, e.target)}>
                <strong>${d.name}</strong>
                <span class="small muted">${d.kind === "battery" ? t("Speicher") : CATEGORY_LABELS[d.category] ?? ""}</span>
              </label>
              <span class="small status">${d.blocked
                ? t("nicht möglich: {reason}", { reason: d.blocked })
                : !d.enabled
                  ? t("aus – Sunflow plant nur")
                  : !takeover.enabled
                    ? t("nicht gesteuert: Hauptschalter „Sunflow steuert“ (oben rechts) ist aus")
                  : s?.active
                    ? `${ACTION_LABELS[s.action] ?? s.action}${s.power_w ? ` · ${Math.round(s.power_w)} W` : ""}${s.grid_target_w !== null && s.grid_target_w !== undefined
                      ? ` · ${t("Netz-Ziel {power} W", { power: Math.round(s.grid_target_w) })}`
                      : ""}${s.phases ? ` · ${s.phases === 1 ? t("1 Phase") : t("{count} Phasen", { count: s.phases })}` : ""}${s.limit_w !== null && s.limit_w !== undefined
                      ? ` · ${t("PV begrenzt auf {power} W", { power: Math.round(s.limit_w) })}`
                      : ""} · ${s.reason}${s.last_write ? ` · ${t("zuletzt geschrieben {time}", { time: time(s.last_write) })}` : ""}`
                    : s?.reason ?? t("startet mit der nächsten Minute")}</span>
              ${d.kind === "loadpoint" && d.enabled && takeover.enabled
                ? html`<span class="small muted">${t("Wird der Ladepunkt auch an anderer Stelle gesteuert (z. B. eine Automation oder ein Zeitplan), dort ausschalten – sonst schalten zwei Steuerungen gegeneinander.")}</span>`
                : nothing}
              ${s?.error ? html`<span class="small warn-text">${t("Fehler: {error} – nächster Versuch in einer Minute", { error: s.error })}</span>` : nothing}
              ${s?.note ? html`<span class="small muted">${s.note}</span>` : nothing}
              ${s?.warning ? html`<span class="small warn-text">⚠ ${s.warning}</span>` : nothing}
            </li>`;
          })}
        </ul>
    `;
  }

  async _switchTakeover(body, input) {
    const takeover = this._takeover;
    // A device switched on while the main switch is off would still not be controlled
    if (body.device_enabled === true && !takeover.enabled) body = { ...body, enabled: true };
    const on = body.enabled === true || body.device_enabled === true;
    const willControl = on && (body.enabled ?? takeover.enabled) && takeover.devices.some((d) => (d.device_id === body.device_id ? body.device_enabled : d.enabled));
    if (willControl && !(await confirmDialog({
      title: t("Sunflow steuern lassen?"),
      text: (body.enabled && !takeover.enabled && body.device_id ? `${t("Der Hauptschalter „Sunflow steuert“ wird mit eingeschaltet.")}\n\n` : "") +
        `${t("Sunflow schaltet dann laufend an den Geräten: am Wechselrichter Passiv-Modus mit Lade- und Entladefenster (der Betriebsmodus wird einmalig umgestellt), an Ladepunkten Laden starten und sperren.")}\n\n${t("Ausschalten gibt alles zurück.")}`,
      confirm: t("Einschalten"),
    }))) {
      input.checked = !input.checked;
      return;
    }
    this._busy = true;
    try {
      this._takeover = await api.setTakeover(body, willControl);
      this._error = null;
      // The background loop applies it within a minute; show the result then
      clearTimeout(this._takeoverTimer);
      this._takeoverTimer = setTimeout(() => this._load(), 65_000);
    } catch (err) {
      this._error = t("Steuerung nicht umgeschaltet: {error}", { error: err.message });
      input.checked = !input.checked;
    } finally {
      this._busy = false;
    }
  }

  _orderedDevices(data) {
    const rank = Object.fromEntries(data.priorities.map((p, i) => [p.id, i]));
    return [...data.devices].sort((a, b) => (rank[a.id] ?? 99) - (rank[b.id] ?? 99));
  }

  _renderBattery(strategy) {
    return html`
      <section class="card">
        <div class="card-head">
          <div>
            <h2>${t("Hausspeicher")}</h2>
            <span class="small muted">${t("Standard: {strategy}", { strategy: STRATEGIES[strategy.default].short })}</span>
          </div>
          ${strategy.override
            ? html`<span class="chip">${STRATEGIES[strategy.effective].short} · ${untilText(strategy.override.until)}</span>`
            : nothing}
        </div>
        <div class="segmented" role="group" aria-label=${t("Strategie Hausspeicher")}>
          ${Object.entries(STRATEGIES).map(
            ([value, { short }]) => html`<button aria-pressed=${strategy.effective === value} ?disabled=${this._busy}
              class=${strategy.effective === value ? "on" : ""} @click=${() => this._setStrategy(value)}>${short}</button>`,
          )}
        </div>
        ${strategyDetails(strategy.effective)}
        ${strategy.override
          ? html`<button class="link" @click=${() => this._apply(() => api.clearStrategyOverride())}>${t("Zurück zum Standard")}</button>`
          : nothing}
      </section>
    `;
  }

  /** Wallbox with several vehicles: which one is plugged in, and the choice by hand. */
  _renderVehicle(device) {
    const v = device.vehicle;
    // The scanned card in the brackets, also when it belongs to no vehicle
    const card = device.card !== null && device.card !== undefined ? t("Karte {card}", { card: device.card }) : null;
    const how = v?.method === "Karte" && card ? card : [v?.method, card].filter(Boolean).join(", ");
    return html`
      <div class="row">
        <span class="small muted">${t("Fahrzeug")}</span>
        ${device.manual_choice
          ? html`<select aria-label=${t("Fahrzeug an {name}", { name: device.name })} ?disabled=${this._busy}
              @change=${(e) => this._apply(() => api.chooseVehicle(device.id, e.target.value || null))}>
              <option value="" ?selected=${!device.selected_vehicle}>${t("automatisch erkennen")}</option>
              ${device.vehicles.map((o) => html`<option value=${o.id} ?selected=${device.selected_vehicle === o.id}>${o.name}</option>`)}
            </select>`
          : nothing}
        <span class="small">${v ? html`<strong>${v.name}</strong>` : html`<span class="muted">${t("nicht erkannt")}</span>`}${how
          ? html` <span class="muted">(${how})</span>`
          : nothing}</span>
        ${device.aux_battery ? this._renderAuxBattery(device.aux_battery) : nothing}
      </div>
    `;
  }

  /** A load with a battery of its own: ready or not, and "plugged in now" by hand. */
  _renderCharger(device) {
    const charger = device.charger;
    // The last result already says ready or not (e.g. "Not ready to charge (test drew no power)")
    const state = charger.testing ? t("Probeladung läuft …") : charger.note ?? (charger.ready ? t("Ladebereit") : t("Nicht ladebereit"));
    return html`
      <div class="row">
        <span class="small ${charger.ready ? "" : "muted"}">${state}</span>
        ${charger.manual
          ? html`<button class="plugged" ?disabled=${this._busy}
              @click=${() => this._apply(() => api.setPlugged(device.id, !charger.ready))}>
              ${charger.ready ? t("Nicht mehr eingesteckt") : t("Jetzt eingesteckt")}</button>`
          : nothing}
      </div>
    `;
  }

  /** Voltage of the car's 12 V battery and a running top-up. */
  _renderAuxBattery(aux) {
    const low = aux.voltage !== null && aux.voltage < aux.min_voltage;
    const until = aux.charging_until
      ? new Date(aux.charging_until).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" })
      : null;
    return html`<span class="small">· ${t("12-V-Batterie")}
      <span class=${low ? "warn-text" : ""}>${aux.voltage !== null ? `${aux.voltage.toLocaleString(locale(), { minimumFractionDigits: 1, maximumFractionDigits: 2 })} V` : t("nicht lesbar")}</span>${until
        ? html` <span class="muted">· ${t("wird bis {time} nachgeladen", { time: until })}</span>`
        : nothing}</span>`;
  }

  _renderDevice(device) {
    const mode = device.paused ? "pause" : device.override?.strategy ?? device.default.strategy;
    const unit = device.kind === "heater" ? "°C" : "%";
    const targets = TARGETS[device.kind];
    const targetOverride = device.override?.target ?? null;
    const status = [
      formatPower(device.power_w, device.power_display),
      device.level !== null && device.level !== undefined ? `${Math.round(device.level)} ${unit}` : null,
      device.kind === "vehicle" && device.connected === false ? t("nicht angesteckt") : null,
    ].filter(Boolean);
    return html`
      <article class="card">
        <div class="card-head">
          <div>
            <h3>${device.name}</h3>
            <span class="small muted">${CATEGORY_LABELS[device.category] ?? KIND_LABELS[device.kind] ?? t("Verbraucher")} · ${status.join(" · ")}</span>
          </div>
          ${device.override ? html`<span class="chip">${t("angepasst")} · ${untilText(device.override.until)}</span>` : nothing}
        </div>
        ${device.vehicles?.length ? this._renderVehicle(device) : nothing}
        ${device.charger ? this._renderCharger(device) : nothing}
        <div class="segmented" role="group" aria-label=${t("Modus {name}", { name: device.name })}>
          ${Object.entries(MODES).map(
            ([value, label]) => html`<button aria-pressed=${mode === value} ?disabled=${this._busy}
              class=${mode === value ? "on" : ""} @click=${() => this._setMode(device, value)}>
              ${label}${value === device.default.strategy ? html`<span class="std">${t("Standard")}</span>` : nothing}
            </button>`,
          )}
        </div>
        ${targets
          ? html`<div class="row">
              <span class="small muted">${t("Ziel")}</span>
              <div class="segmented small-buttons" role="group" aria-label=${t("Ziel {name}", { name: device.name })}>
                <button class=${targetOverride === null ? "on" : ""} aria-pressed=${targetOverride === null} ?disabled=${this._busy}
                  @click=${() => this._setDevice(device, { target: null })}>
                  ${t("Standard")}${device.default.target !== null ? ` (${device.default.target} ${unit})` : ""}
                </button>
                ${targets.map(
                  (target) => html`<button class=${targetOverride === target ? "on" : ""} aria-pressed=${targetOverride === target} ?disabled=${this._busy}
                    @click=${() => this._setDevice(device, { target })}>${target} ${unit}</button>`,
                )}
              </div>
            </div>`
          : nothing}
        <div class="plan small">
          ${device.reason
            ? html`<span class="muted">${t("Nicht eingeplant: {reason}", { reason: device.reason })}</span>`
            : device.windows.length
              ? t("Geplant: {windows}", {
                  windows: device.windows
                    .slice(0, 3)
                    .map((w) => `${dayTime.format(new Date(w.start))}–${time.format(new Date(w.end))} (${formatValue(w.energy_wh, "Wh")})`)
                    .join(" · "),
                })
              : html`<span class="muted">${t("Kein Zeitfenster geplant")}</span>`}
        </div>
        ${device.override
          ? html`<button class="link" @click=${() => this._apply(() => api.clearDeviceOverride(device.id))}>${t("Zurück zum Standard")}</button>`
          : nothing}
      </article>
    `;
  }

  _saveConditions(deviceId, form) {
    const conditions = [...form.querySelectorAll("input")]
      .filter((input) => input.value !== "")
      .map((input) => ({ device_id: input.name, min_pct: Number(input.value) }));
    this._editing = null;
    return this._apply(() => api.setPriorityConditions(deviceId, conditions));
  }

  _renderPriorities(priorities, sources) {
    const names = Object.fromEntries(priorities.map((p) => [p.id, p.name]));
    const units = Object.fromEntries(sources.map((s) => [s.id, s.unit ?? "%"]));
    const limit = (c) => `${names[c.device_id] ?? c.device_id} ${decimal(c.min_pct)} ${units[c.device_id] ?? "%"}`;
    return html`
      <section class="card">
        <h2>${t("Priorität")}</h2>
        <p class="small muted">${t("Reicht der PV-Überschuss nicht für alle, bekommt der obere Eintrag zuerst.")}
          ${t("Verbraucher unterhalb des Hausspeichers erhalten Überschuss erst, wenn der Speicher ihn nicht mehr aufnimmt.")}
          ${t("Mit „Grenzen“ wartet ein Eintrag, bis höher eingestufte Speicher (Hausspeicher, Auto, Warmwasser) einen Stand erreicht haben – bis dahin nutzt er nur Netzstrom, falls sein Ziel das verlangt.")}</p>
        <ol class="priorities">
          ${priorities.map((p, i) => {
            // Limits can only refer to entries ranked higher
            const above = new Set(priorities.slice(0, i).map((q) => q.id));
            const own = sources.filter((s) => above.has(s.id));
            const editing = this._editing === p.id;
            return html`<li class=${p.id === "battery" ? "battery" : ""}>
              <div class="entry">
                <span class="rank">${i + 1}</span>
                <span class="name">
                  ${p.name}
                  ${p.conditions.length
                    ? html`<span class="small muted">${t("ab {limits}", { limits: andList.format(p.conditions.map(limit)) })}</span>`
                    : nothing}
                  ${p.hidden_conditions?.length
                    ? html`<span class="small muted">${t("ausgeblendet (steht tiefer): {limits}", { limits: p.hidden_conditions.map(limit).join(", ") })}</span>`
                    : nothing}
                </span>
                ${own.length
                  ? html`<button class="text" aria-expanded=${editing} ?disabled=${this._busy}
                      @click=${() => (this._editing = editing ? null : p.id)}>${t("Grenzen")}</button>`
                  : nothing}
                <button aria-label=${t("{name} nach oben", { name: p.name })} ?disabled=${i === 0 || this._busy} @click=${() => this._move(i, -1)}>▲</button>
                <button aria-label=${t("{name} nach unten", { name: p.name })} ?disabled=${i === priorities.length - 1 || this._busy}
                  @click=${() => this._move(i, 1)}>▼</button>
              </div>
              ${editing
                ? html`<form class="conditions" @submit=${(e) => {
                    e.preventDefault();
                    this._saveConditions(p.id, e.target);
                  }}>
                    <span class="small muted">${p.id === "battery"
                      ? t("Der Hausspeicher lädt aus PV erst ab:")
                      : t("{name} bekommt PV- und Speicherenergie erst ab:", { name: p.name })}</span>
                    ${own.map((s) => {
                      const current = p.conditions.find((c) => c.device_id === s.id);
                      return html`<label class="condition">
                        <span>${s.name}${s.kind === "vehicle"
                          ? html` <span class="small muted">(${t("nur wenn angesteckt")})</span>`
                          : s.kind === "heater"
                            ? html` <span class="small muted">(${t("Wassertemperatur")})</span>`
                            : nothing}</span>
                        <span class="pct"><input type="number" name=${s.id} min="0" max="100" step=${s.unit === "°C" ? 1 : 5}
                          placeholder="–" aria-label=${t("{name} in {unit}", { name: s.name, unit: s.unit ?? "%" })}
                          .value=${current ? String(current.min_pct) : ""}> ${s.unit ?? "%"}</span>
                      </label>`;
                    })}
                    <div class="actions">
                      <button type="submit" class="primary">${t("Übernehmen")}</button>
                      <button type="button" class="text" @click=${() => (this._editing = null)}>${t("Abbrechen")}</button>
                    </div>
                  </form>`
                : nothing}
            </li>`;
          })}
        </ol>
      </section>
    `;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 16px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0; font-size: 16px; }
    h3 { margin: 0; font-size: 15px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .heading { display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; align-items: flex-end; }
    .duration { display: flex; flex-direction: column; gap: 4px; }
    select {
      min-height: 40px; padding: 0 10px; font: inherit; font-size: 14px; border: 1px solid var(--sf-border-strong);
      border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    section { display: flex; flex-direction: column; gap: 10px; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr)); gap: 12px; }
    .card {
      border: 1px solid var(--sf-border); border-radius: 10px; padding: 14px 16px;
      display: flex; flex-direction: column; gap: 12px;
    }
    .takeover.on { border-color: var(--sf-primary); }
    .strategy-details summary { cursor: pointer; color: var(--sf-primary-text); }
    .strategy-details ul { margin: 6px 0 0; padding-left: 18px; display: grid; gap: 4px; }
    .device-toggle { display: flex; gap: 8px; align-items: baseline; border: 0; background: none; padding: 0; cursor: pointer; text-align: left; color: inherit; font: inherit; }
    .caret { width: 14px; color: var(--sf-muted); }
    .takeover-devices { list-style: none; margin: 10px 0 0; padding: 0; display: grid; gap: 8px; }
    .takeover-devices li { display: flex; flex-direction: column; gap: 2px; }
    .takeover-devices .status { padding-left: 26px; }
    .switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; }
    .switch input { width: 18px; height: 18px; margin: 0; }
    .alert.warn { padding: 10px 14px; border-radius: 8px; background: var(--sf-warn-bg); color: var(--sf-warn-text); margin-top: 8px; }
    .alert.info { padding: 10px 14px; border-radius: 8px; background: var(--sf-primary-bg); color: var(--sf-primary-text); }
    .warn-text { color: var(--sf-warn-text); }
    .log-filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .log { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; max-height: 480px; overflow-y: auto; }
    .log li { display: grid; grid-template-columns: 5.5em 1fr; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--sf-border); }
    .log li.foreign { background: var(--sf-warn-bg); border-radius: 6px; padding: 4px 6px; }
    .log .calls { display: block; overflow-wrap: anywhere; }
    .card-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: flex-start; }
    .chip {
      font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 12px;
      background: var(--sf-warn-bg); color: var(--sf-warn-text);
    }
    .segmented { display: flex; flex-wrap: wrap; gap: 4px; background: var(--sf-subtle); padding: 4px; border-radius: 10px; }
    .segmented button {
      flex: 1 1 auto; min-height: 40px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent;
      cursor: pointer; font-size: 14px; color: var(--sf-muted);
    }
    .segmented button.on { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.1); }
    .segmented button .std { display: block; font-size: 11px; font-weight: 400; color: var(--sf-muted); }
    .segmented.small-buttons button { min-height: 36px; font-size: 13px; }
    .row { display: flex; flex-direction: column; gap: 6px; }
    button.plugged {
      align-self: flex-start; min-height: 40px; padding: 0 14px; border: 1px solid var(--sf-border-strong); border-radius: 8px;
      background: var(--sf-surface); cursor: pointer; font-weight: 500;
    }
    .plan { padding: 8px 10px; border-radius: 8px; background: var(--sf-subtle); }
    .link {
      align-self: flex-start; border: 0; background: none; padding: 0; color: var(--sf-primary-text);
      text-decoration: underline; cursor: pointer; font-size: 13px;
    }
    .priorities { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .priorities li { padding: 6px 10px; border-radius: 8px; background: var(--sf-subtle); }
    .priorities .entry { display: flex; align-items: center; gap: 10px; }
    .priorities li.battery { background: var(--sf-primary-bg); }
    .priorities .rank { width: 24px; font-weight: 600; color: var(--sf-muted); }
    .priorities .name { flex: 1; display: flex; flex-direction: column; }
    .priorities .entry button.text, .conditions button.text { width: auto; padding: 0 10px; font-size: 13px; }
    .conditions { display: flex; flex-direction: column; gap: 8px; padding: 10px 0 4px 34px; }
    .condition { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; font-size: 14px; }
    .condition .pct { display: flex; align-items: center; gap: 6px; }
    .condition input {
      width: 80px; min-height: 36px; padding: 0 8px; font: inherit; font-size: 14px; border: 1px solid var(--sf-border-strong);
      border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .conditions .actions { display: flex; gap: 8px; }
    .conditions button {
      width: auto; height: 36px; border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); cursor: pointer;
    }
    .conditions button.primary { padding: 0 14px; background: var(--sf-primary); border-color: var(--sf-primary); color: var(--sf-on-primary); }
    .priorities .entry button {
      width: 40px; height: 36px; border: 1px solid var(--sf-border-strong); border-radius: 8px;
      background: var(--sf-surface); cursor: pointer;
    }
    button:disabled { cursor: not-allowed; opacity: 0.5; }
    button:focus-visible, select:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sunflow-control", SunflowControl);
