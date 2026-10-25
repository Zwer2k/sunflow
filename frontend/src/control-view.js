import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { formatValue } from "./format.js";

const REFRESH_MS = 30 * 1000;
// The plan is recomputed in the background after a change
const REPLAN_DELAY_MS = 2500;

const STRATEGIES = {
  balanced: "Ausgewogen",
  pv_absorption: "PV aufnehmen",
  backup: "Notstrom",
};
const MODES = {
  cost: "Kostenoptimal",
  pv_only: "Nur PV",
  fast: "Sofort",
  pause: "Pause",
};
const KIND_LABELS = { vehicle: "Fahrzeug", heater: "Warmwasser", load: "Verbraucher" };
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
  };

  constructor() {
    super();
    this._data = null;
    this._error = null;
    this._duration = "midnight";
    this._busy = false;
    this._editing = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._timer = setInterval(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._timer);
  }

  async _load() {
    try {
      this._data = await api.control();
      this._error = null;
    } catch (err) {
      this._error = `Steuerung nicht verfügbar: ${err.message}`;
    }
  }

  async _apply(action) {
    this._busy = true;
    try {
      await action();
      await this._load();
      // Show the windows of the new plan once the background replan is done
      setTimeout(() => this._load(), REPLAN_DELAY_MS);
      this._error = null;
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
    `;
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

  _renderDevice(device) {
    const mode = device.paused ? "pause" : device.override?.strategy ?? device.default.strategy;
    const unit = device.kind === "heater" ? "°C" : "%";
    const targets = TARGETS[device.kind];
    const targetOverride = device.override?.target ?? null;
    const status = [
      formatValue(device.power_w ?? 0, "W"),
      device.level !== null && device.level !== undefined ? `${Math.round(device.level)} ${unit}` : null,
      device.kind === "vehicle" && device.connected === false ? "nicht angesteckt" : null,
    ].filter(Boolean);
    return html`
      <article class="card">
        <div class="card-head">
          <div>
            <h3>${device.name}</h3>
            <span class="small muted">${KIND_LABELS[device.kind] ?? "Verbraucher"} · ${status.join(" · ")}</span>
          </div>
          ${device.override ? html`<span class="chip">angepasst · ${untilText(device.override.until)}</span>` : nothing}
        </div>
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
    const vehicles = new Set(sources.filter((s) => s.id !== "battery").map((s) => s.id));
    return html`
      <section class="card">
        <h2>Priorität</h2>
        <p class="small muted">Reicht der PV-Überschuss nicht für alle, bekommt der obere Eintrag zuerst.
          Verbraucher unterhalb des Hausspeichers erhalten Überschuss erst, wenn der Speicher ihn nicht mehr aufnimmt.
          Mit „Grenzen“ wartet ein Verbraucher, bis andere Speicher einen Ladestand erreicht haben – bis dahin nutzt er
          nur Netzstrom, falls sein Ziel das verlangt.</p>
        <ol class="priorities">
          ${priorities.map((p, i) => {
            const own = sources.filter((s) => s.id !== p.id);
            const editing = this._editing === p.id;
            return html`<li class=${p.id === "battery" ? "battery" : ""}>
              <div class="entry">
                <span class="rank">${i + 1}</span>
                <span class="name">
                  ${p.name}
                  ${p.conditions.length
                    ? html`<span class="small muted">ab ${p.conditions
                        .map((c) => `${names[c.device_id] ?? c.device_id} ${c.min_pct} %`)
                        .join(" und ")}</span>`
                    : nothing}
                </span>
                ${p.id !== "battery" && own.length
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
                    <span class="small muted">${p.name} bekommt PV- und Speicherenergie erst ab:</span>
                    ${own.map((s) => {
                      const current = p.conditions.find((c) => c.device_id === s.id);
                      return html`<label class="condition">
                        <span>${s.name}${vehicles.has(s.id) ? html` <span class="small muted">(nur wenn angesteckt)</span>` : nothing}</span>
                        <span class="pct"><input type="number" name=${s.id} min="0" max="100" step="5" placeholder="–"
                          .value=${current ? String(current.min_pct) : ""}> %</span>
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
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
    .card {
      border: 1px solid var(--sf-border); border-radius: 10px; padding: 14px 16px;
      display: flex; flex-direction: column; gap: 12px;
    }
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
    .priorities button.text, .conditions button.text { width: auto; padding: 0 10px; font-size: 13px; }
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
    .conditions button.primary { padding: 0 14px; background: var(--sf-primary); border-color: var(--sf-primary); color: #fff; }
    .priorities button {
      width: 40px; height: 36px; border: 1px solid var(--sf-border-strong); border-radius: 8px;
      background: var(--sf-surface); cursor: pointer;
    }
    button:disabled { cursor: not-allowed; opacity: 0.5; }
    button:focus-visible, select:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sunflow-control", SunflowControl);
