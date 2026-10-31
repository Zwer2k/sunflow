import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { confirmDialog } from "../components/dialog.js";
import { poll } from "../lib/poll.js";
import { CATEGORY_LABELS, formatPower, formatValue } from "../lib/format.js";

const REFRESH_MS = 30 * 1000;
// The plan is recomputed in the background after a change
const REPLAN_DELAY_MS = 2500;

const ACTION_LABELS = {
  auto: "Eigenverbrauch",
  hold: "Speicher halten",
  force_charge: "Laden aus dem Netz",
  force_discharge: "Entladen ins Netz",
  limit_discharge: "Entladen begrenzt",
  limit_charge: "Laden begrenzt",
  charge: "Lädt",
  follow: "Lädt mit PV-Überschuss",
  stop: "Laden gesperrt",
  release: "Gerät regelt selbst",
};
// Writes of the log that are no battery or charge action
const LOG_ACTIONS = {
  take_over: "Steuerung übernommen",
  mode: "Modus zurückgestellt",
  authorize: "Ladevorgang freigegeben",
  pv_set: "PV begrenzt",
  pv_release: "PV-Begrenzung aufgehoben",
  phases_one: "auf 1 Phase",
  phases_three: "auf 3 Phasen",
  phases_restore: "Phasenmodus zurückgestellt",
};
const LOG_HOURS = { 24: "24 Stunden", 72: "3 Tage", 336: "14 Tage" };
// More rows make the page slow and say nothing more
const LOG_ROWS = 300;
const STRATEGIES = {
  balanced: "Ausgewogen",
  pv_absorption: "PV aufnehmen",
  autarky: "Autarkie",
};
const MODES = {
  cost: "Kostenoptimal",
  pv_only: "Nur PV",
  fast: "Sofort",
  pause: "Pause",
};
const KIND_LABELS = { vehicle: "Wallbox", heater: "Wärme", load: "Verbraucher" };
const TARGETS = { vehicle: [50, 80, 100], heater: [45, 55, 60] };
const DURATIONS = {
  midnight: "bis Mitternacht",
  hours4: "4 Stunden",
  open: "bis auf Weiteres",
};

const time = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });
const dayTime = new Intl.DateTimeFormat("de-DE", { weekday: "short", hour: "2-digit", minute: "2-digit" });

function untilFor(duration) {
  if (duration === "open") return null;
  const until = new Date();
  if (duration === "hours4") until.setHours(until.getHours() + 4);
  else until.setHours(24, 0, 0, 0);
  return until.toISOString();
}

function untilText(until) {
  if (!until) return "bis auf Weiteres";
  const date = new Date(until);
  return date.toDateString() === new Date().toDateString() || date.getHours() === 0
    ? `bis ${date.getHours() === 0 && date.getMinutes() === 0 ? "Mitternacht" : time.format(date)}`
    : `bis ${dayTime.format(date)}`;
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
    _log: { state: true },
    _logDevice: { state: true },
    _logHours: { state: true },
    _logForeign: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._error = null;
    this._duration = "midnight";
    this._busy = false;
    this._editing = null;
    this._log = null;
    this._logDevice = "";
    this._logHours = 24;
    this._logForeign = false;
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
      this._error = `Steuerung nicht verfügbar: ${err.message}`;
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
      this._error = `Änderung fehlgeschlagen: ${err.message}`;
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
          <h1>Steuerung</h1>
          <p class="muted">Vorübergehende Eingriffe – danach gilt wieder der Standard aus den Einstellungen.
            Sunflow plant nach jeder Änderung sofort neu.</p>
        </div>
        <label class="duration">
          <span class="small muted">Eingriffe gelten</span>
          <select aria-label="Eingriffe gelten" @change=${(e) => (this._duration = e.target.value)}>
            ${Object.entries(DURATIONS).map(
              ([value, label]) => html`<option value=${value} ?selected=${this._duration === value}>${label}</option>`,
            )}
          </select>
        </label>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${data === null && !this._error ? html`<p class="muted">Lade …</p>` : nothing}
      ${this._takeover?.devices.length ? this._renderTakeover(this._takeover) : nothing}
      ${data ? html`
          ${data.strategy.available ? this._renderBattery(data.strategy) : nothing}
          ${data.devices.length
            ? html`<section>
                <h2>Verbraucher</h2>
                <div class="cards">${this._orderedDevices(data).map((d) => this._renderDevice(d))}</div>
              </section>`
            : html`<p class="muted">Noch keine Verbraucher eingerichtet (Wallbox, Warmwasser, eigene Geräte).</p>`}
          ${data.priorities.length > 1 ? this._renderPriorities(data.priorities, data.condition_sources) : nothing}
        ` : nothing}
      ${this._takeover?.devices.length && this._log ? this._renderLog(this._log, this._takeover.devices) : nothing}
    `;
  }

  _renderLog(log, devices) {
    const entries = (this._logForeign ? log.filter((e) => e.kind === "extern") : log).slice(0, LOG_ROWS);
    const today = new Date().toDateString();
    const when = (iso) => (new Date(iso).toDateString() === today ? time : dayTime).format(new Date(iso));
    return html`
      <section class="card">
        <div class="card-head">
          <div>
            <h2>Protokoll</h2>
            <p class="small muted">Was Sunflow an den gesteuerten Geräten geschrieben hat – und Änderungen, die nicht von
              Sunflow kamen, mit dem, was Home Assistant über den Auslöser weiß. Geprüft wird jede Minute.</p>
          </div>
        </div>
        <div class="log-filters">
          <select aria-label="Gerät" @change=${(e) => this._setLogFilter({ _logDevice: e.target.value })}>
            <option value="" ?selected=${!this._logDevice}>Alle Geräte</option>
            ${devices.map((d) => html`<option value=${d.device_id} ?selected=${this._logDevice === d.device_id}>${d.name}</option>`)}
          </select>
          <select aria-label="Zeitraum" @change=${(e) => this._setLogFilter({ _logHours: Number(e.target.value) })}>
            ${Object.entries(LOG_HOURS).map(
              ([hours, label]) => html`<option value=${hours} ?selected=${this._logHours === Number(hours)}>${label}</option>`,
            )}
          </select>
          <label class="switch small">
            <input type="checkbox" .checked=${this._logForeign} @change=${(e) => (this._logForeign = e.target.checked)}>
            <span>nur fremde Änderungen</span>
          </label>
        </div>
        ${entries.length
          ? html`<ul class="log">
              ${entries.map((e) => html`<li class=${e.kind === "extern" ? "foreign" : ""}>
                <span class="small muted when">${when(e.time)}</span>
                <span class="small"><strong>${e.device}</strong> · ${e.kind === "extern"
                  ? html`<span class="warn-text">nicht von Sunflow geändert: ${e.name ?? e.entity_id} ${e.old} → ${e.new}</span>
                      <span class="muted">· durch ${e.source}</span>`
                  : html`${ACTION_LABELS[e.action] ?? LOG_ACTIONS[e.action] ?? e.action}${e.power_w ? ` · ${formatPower(e.power_w)}` : ""}${e.reason
                      ? html` <span class="muted">· ${e.reason}</span>`
                      : nothing}${e.calls?.length
                      ? html`<span class="muted calls">${e.calls.map((c) => `${c.entity_id} → ${Object.values(c.data ?? {}).join(", ") || c.service}${c.status === "done" ? "" : ` (${c.status})`}`).join(" · ")}</span>`
                      : nothing}${e.error ? html`<span class="warn-text"> · Fehler: ${e.error}</span>` : nothing}`}</span>
              </li>`)}
            </ul>`
          : html`<p class="small muted">${this._logForeign ? "Keine fremden Änderungen" : "Keine Einträge"} im gewählten Zeitraum.</p>`}
      </section>
    `;
  }

  _renderTakeover(t) {
    const time = (iso) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    return html`
      <section class="card takeover ${t.enabled ? "on" : ""}">
        <div class="card-head">
          <div>
            <h2>Sunflow steuert</h2>
            <p class="small muted">${t.enabled
              ? "Sunflow setzt den Plan an den Geräten um. Ausschalten gibt sie zurück: Speicher in Eigenverbrauch und vorherigen Modus, Ladepunkte an ihre eigene Logik."
              : "Aus: Sunflow plant nur, die Geräte arbeiten wie bisher."}</p>
          </div>
          <label class="switch">
            <input type="checkbox" .checked=${t.enabled} ?disabled=${this._busy}
              @change=${(e) => this._switchTakeover({ enabled: e.target.checked }, e.target)}>
            <span>${t.enabled ? "An" : "Aus"}</span>
          </label>
        </div>
        ${t.paused ? html`<div class="alert warn">⚠ ${t.paused}</div>` : nothing}
        <ul class="takeover-devices">
          ${t.devices.map((d) => {
            const s = d.status;
            return html`<li>
              <label class="switch">
                <input type="checkbox" .checked=${d.enabled} ?disabled=${this._busy || Boolean(d.blocked)}
                  @change=${(e) => this._switchTakeover({ device_id: d.device_id, device_enabled: e.target.checked }, e.target)}>
                <strong>${d.name}</strong>
                <span class="small muted">${d.kind === "battery" ? "Speicher" : CATEGORY_LABELS[d.category] ?? ""}</span>
              </label>
              <span class="small status">${d.blocked
                ? `nicht möglich: ${d.blocked}`
                : !d.enabled
                  ? "aus – Sunflow plant nur"
                  : !t.enabled
                    ? "nicht gesteuert: Hauptschalter „Sunflow steuert“ (oben rechts) ist aus"
                  : s?.active
                    ? `${ACTION_LABELS[s.action] ?? s.action}${s.power_w ? ` · ${Math.round(s.power_w)} W` : ""}${s.grid_target_w !== null && s.grid_target_w !== undefined
                      ? ` · Netz-Ziel ${Math.round(s.grid_target_w)} W`
                      : ""}${s.phases ? ` · ${s.phases === 1 ? "1 Phase" : `${s.phases} Phasen`}` : ""}${s.limit_w !== null && s.limit_w !== undefined
                      ? ` · PV begrenzt auf ${Math.round(s.limit_w)} W`
                      : ""} · ${s.reason}${s.last_write ? ` · zuletzt geschrieben ${time(s.last_write)}` : ""}`
                    : s?.reason ?? "startet mit der nächsten Minute"}</span>
              ${d.kind === "loadpoint" && d.enabled && t.enabled
                ? html`<span class="small muted">Wird der Ladepunkt auch an anderer Stelle gesteuert (z. B. eine Automation oder ein
                    Zeitplan), dort ausschalten – sonst schalten zwei Steuerungen gegeneinander.</span>`
                : nothing}
              ${s?.error ? html`<span class="small warn-text">Fehler: ${s.error} – nächster Versuch in einer Minute</span>` : nothing}
              ${s?.note ? html`<span class="small muted">${s.note}</span>` : nothing}
            </li>`;
          })}
        </ul>
      </section>
    `;
  }

  async _switchTakeover(body, input) {
    const t = this._takeover;
    // A device switched on while the main switch is off would still not be controlled
    if (body.device_enabled === true && !t.enabled) body = { ...body, enabled: true };
    const on = body.enabled === true || body.device_enabled === true;
    const willControl = on && (body.enabled ?? t.enabled) && t.devices.some((d) => (d.device_id === body.device_id ? body.device_enabled : d.enabled));
    if (willControl && !(await confirmDialog({
      title: "Sunflow steuern lassen?",
      text: (body.enabled && !t.enabled && body.device_id ? "Der Hauptschalter „Sunflow steuert“ wird mit eingeschaltet.\n\n" : "") +
        "Sunflow schaltet dann laufend an den Geräten: am Wechselrichter Passiv-Modus mit Lade- und Entladefenster " +
        "(der Betriebsmodus wird einmalig umgestellt), an Ladepunkten Laden starten und sperren.\n\nAusschalten gibt alles zurück.",
      confirm: "Einschalten",
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
      this._error = `Steuerung nicht umgeschaltet: ${err.message}`;
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
            <h2>Hausspeicher</h2>
            <span class="small muted">Standard: ${STRATEGIES[strategy.default]}</span>
          </div>
          ${strategy.override
            ? html`<span class="chip">${STRATEGIES[strategy.effective]} · ${untilText(strategy.override.until)}</span>`
            : nothing}
        </div>
        <div class="segmented" role="group" aria-label="Strategie Hausspeicher">
          ${Object.entries(STRATEGIES).map(
            ([value, label]) => html`<button aria-pressed=${strategy.effective === value} ?disabled=${this._busy}
              class=${strategy.effective === value ? "on" : ""} @click=${() => this._setStrategy(value)}>${label}</button>`,
          )}
        </div>
        ${strategy.override
          ? html`<button class="link" @click=${() => this._apply(() => api.clearStrategyOverride())}>Zurück zum Standard</button>`
          : nothing}
      </section>
    `;
  }

  /** Wallbox with several vehicles: which one is plugged in, and the choice by hand. */
  _renderVehicle(device) {
    const v = device.vehicle;
    return html`
      <div class="row">
        <span class="small muted">Fahrzeug</span>
        ${device.manual_choice
          ? html`<select aria-label="Fahrzeug an ${device.name}" ?disabled=${this._busy}
              @change=${(e) => this._apply(() => api.chooseVehicle(device.id, e.target.value || null))}>
              <option value="" ?selected=${!device.selected_vehicle}>automatisch erkennen</option>
              ${device.vehicles.map((o) => html`<option value=${o.id} ?selected=${device.selected_vehicle === o.id}>${o.name}</option>`)}
            </select>`
          : nothing}
        <span class="small">${v ? html`<strong>${v.name}</strong> <span class="muted">(${v.method})</span>` : html`<span class="muted">nicht erkannt</span>`}</span>
      </div>
    `;
  }

  _renderDevice(device) {
    const mode = device.paused ? "pause" : device.override?.strategy ?? device.default.strategy;
    const unit = device.kind === "heater" ? "°C" : "%";
    const targets = TARGETS[device.kind];
    const targetOverride = device.override?.target ?? null;
    const status = [
      formatPower(device.power_w, device.power_display),
      device.level !== null && device.level !== undefined ? `${Math.round(device.level)} ${unit}` : null,
      device.kind === "vehicle" && device.connected === false ? "nicht angesteckt" : null,
    ].filter(Boolean);
    return html`
      <article class="card">
        <div class="card-head">
          <div>
            <h3>${device.name}</h3>
            <span class="small muted">${CATEGORY_LABELS[device.category] ?? KIND_LABELS[device.kind] ?? "Verbraucher"} · ${status.join(" · ")}</span>
          </div>
          ${device.override ? html`<span class="chip">angepasst · ${untilText(device.override.until)}</span>` : nothing}
        </div>
        ${device.vehicles?.length ? this._renderVehicle(device) : nothing}
        <div class="segmented" role="group" aria-label="Modus ${device.name}">
          ${Object.entries(MODES).map(
            ([value, label]) => html`<button aria-pressed=${mode === value} ?disabled=${this._busy}
              class=${mode === value ? "on" : ""} @click=${() => this._setMode(device, value)}>
              ${label}${value === device.default.strategy ? html`<span class="std">Standard</span>` : nothing}
            </button>`,
          )}
        </div>
        ${targets
          ? html`<div class="row">
              <span class="small muted">Ziel</span>
              <div class="segmented small-buttons" role="group" aria-label="Ziel ${device.name}">
                <button class=${targetOverride === null ? "on" : ""} aria-pressed=${targetOverride === null} ?disabled=${this._busy}
                  @click=${() => this._setDevice(device, { target: null })}>
                  Standard${device.default.target !== null ? ` (${device.default.target} ${unit})` : ""}
                </button>
                ${targets.map(
                  (t) => html`<button class=${targetOverride === t ? "on" : ""} aria-pressed=${targetOverride === t} ?disabled=${this._busy}
                    @click=${() => this._setDevice(device, { target: t })}>${t} ${unit}</button>`,
                )}
              </div>
            </div>`
          : nothing}
        <div class="plan small">
          ${device.reason
            ? html`<span class="muted">Nicht eingeplant: ${device.reason}</span>`
            : device.windows.length
              ? html`Geplant: ${device.windows
                  .slice(0, 3)
                  .map((w) => `${dayTime.format(new Date(w.start))}–${time.format(new Date(w.end))} (${formatValue(w.energy_wh, "Wh")})`)
                  .join(" · ")}`
              : html`<span class="muted">Kein Zeitfenster geplant</span>`}
        </div>
        ${device.override
          ? html`<button class="link" @click=${() => this._apply(() => api.clearDeviceOverride(device.id))}>Zurück zum Standard</button>`
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
    const limit = (c) => `${names[c.device_id] ?? c.device_id} ${String(c.min_pct).replace(".", ",")} ${units[c.device_id] ?? "%"}`;
    return html`
      <section class="card">
        <h2>Priorität</h2>
        <p class="small muted">Reicht der PV-Überschuss nicht für alle, bekommt der obere Eintrag zuerst.
          Verbraucher unterhalb des Hausspeichers erhalten Überschuss erst, wenn der Speicher ihn nicht mehr aufnimmt.
          Mit „Grenzen“ wartet ein Eintrag, bis höher eingestufte Speicher (Hausspeicher, Auto, Warmwasser) einen Stand erreicht haben – bis dahin nutzt er
          nur Netzstrom, falls sein Ziel das verlangt.</p>
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
                    ? html`<span class="small muted">ab ${p.conditions.map(limit).join(" und ")}</span>`
                    : nothing}
                  ${p.hidden_conditions?.length
                    ? html`<span class="small muted">ausgeblendet (steht tiefer): ${p.hidden_conditions.map(limit).join(", ")}</span>`
                    : nothing}
                </span>
                ${own.length
                  ? html`<button class="text" aria-expanded=${editing} ?disabled=${this._busy}
                      @click=${() => (this._editing = editing ? null : p.id)}>Grenzen</button>`
                  : nothing}
                <button aria-label="${p.name} nach oben" ?disabled=${i === 0 || this._busy} @click=${() => this._move(i, -1)}>▲</button>
                <button aria-label="${p.name} nach unten" ?disabled=${i === priorities.length - 1 || this._busy}
                  @click=${() => this._move(i, 1)}>▼</button>
              </div>
              ${editing
                ? html`<form class="conditions" @submit=${(e) => {
                    e.preventDefault();
                    this._saveConditions(p.id, e.target);
                  }}>
                    <span class="small muted">${p.id === "battery"
                      ? "Der Hausspeicher lädt aus PV erst ab:"
                      : `${p.name} bekommt PV- und Speicherenergie erst ab:`}</span>
                    ${own.map((s) => {
                      const current = p.conditions.find((c) => c.device_id === s.id);
                      return html`<label class="condition">
                        <span>${s.name}${s.kind === "vehicle"
                          ? html` <span class="small muted">(nur wenn angesteckt)</span>`
                          : s.kind === "heater"
                            ? html` <span class="small muted">(Wassertemperatur)</span>`
                            : nothing}</span>
                        <span class="pct"><input type="number" name=${s.id} min="0" max="100" step=${s.unit === "°C" ? 1 : 5}
                          placeholder="–" aria-label="${s.name} in ${s.unit ?? "%"}"
                          .value=${current ? String(current.min_pct) : ""}> ${s.unit ?? "%"}</span>
                      </label>`;
                    })}
                    <div class="actions">
                      <button type="submit" class="primary">Übernehmen</button>
                      <button type="button" class="text" @click=${() => (this._editing = null)}>Abbrechen</button>
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
    .takeover-devices { list-style: none; margin: 10px 0 0; padding: 0; display: grid; gap: 8px; }
    .takeover-devices li { display: flex; flex-direction: column; gap: 2px; }
    .takeover-devices .status { padding-left: 26px; }
    .switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; }
    .switch input { width: 18px; height: 18px; margin: 0; }
    .alert.warn { padding: 10px 14px; border-radius: 8px; background: var(--sf-warn-bg); color: var(--sf-warn-text); margin-top: 8px; }
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
