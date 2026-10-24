import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { formatSigned, formatValue, roleHint, roleLabel, SIGNED_ROLES } from "./format.js";

const STEPS = ["Integrationen", "Geräte", "Kenndaten", "Gerätetest", "Bilanzprüfung", "Tarif & Prognose", "Fertig"];
const PREVIEW_INTERVAL_MS = 5000;
// Sensors are polled at different times, so single readings can disagree.
const BALANCE_SAMPLES = 6;
const BALANCE_MIN_SAMPLES = 3;
const UNAVAILABLE = new Set(["unavailable", "unknown", ""]);

// Non-numeric states such as a device status are shown as text
function isText(role) {
  return role.value === null && role.raw_state !== null && !UNAVAILABLE.has(role.raw_state) && isNaN(Number(role.raw_state));
}

const OPPOSITE_SIGN = {
  charge_positive: "discharge_positive",
  discharge_positive: "charge_positive",
  import_positive: "export_positive",
  export_positive: "import_positive",
};
const BALANCE_FORMULAS = {
  ac: "Netz + WR-Ausgang (AC) + PV extern",
  dc: "Netz + PV + PV extern − Batterie",
};

class SunflowApp extends LitElement {
  static properties = {
    _step: { state: true },
    _advanced: { state: true },
    _integrations: { state: true },
    _selected: { state: true },
    _previews: { state: true },
    _configDevices: { state: true },
    _editing: { state: true },
    _overrides: { state: true },
    _entities: { state: true },
    _dirty: { state: true },
    _busy: { state: true },
    _error: { state: true },
    _haConnected: { state: true },
  };

  constructor() {
    super();
    this._step = 0;
    this._advanced = false;
    this._integrations = null;
    this._selected = new Set();
    this._previews = {};
    // ha_device_id -> stored device config
    this._configDevices = {};
    this._editing = new Set();
    // ha_device_id -> {role: {entity_id?, sign?}}
    this._overrides = {};
    this._entities = {};
    this._dirty = new Set();
    this._busy = new Set();
    this._error = null;
    this._haConnected = null;
    this._timer = null;
    this._balanceHistory = {};
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPreview();
  }

  async _load() {
    try {
      const [health, integrations, config] = await Promise.all([api.health(), api.discovery(), api.config()]);
      this._haConnected = health.ha_connected;
      this._integrations = integrations;
      this._selected = new Set(
        integrations.filter((i) => i.supported && i.devices.some((d) => d.profile)).map((i) => i.entry_id),
      );
      this._configDevices = Object.fromEntries(config.devices.map((d) => [d.ha_device_id, d]));
      this._overrides = Object.fromEntries(config.devices.map((d) => [d.ha_device_id, d.role_overrides ?? {}]));
      this._error = null;
    } catch (err) {
      this._error = `Daten konnten nicht geladen werden: ${err.message}`;
    }
  }

  get _devices() {
    return (this._integrations ?? [])
      .filter((i) => this._selected.has(i.entry_id))
      .flatMap((i) => i.devices.map((d) => ({ ...d, integration: i.title })))
      .filter((d) => d.profile);
  }

  _goTo(step) {
    this._step = step;
    if (step === 1) this._startPreview();
    else this._stopPreview();
  }

  _startPreview() {
    this._stopPreview();
    this._refreshPreviews();
    this._timer = setInterval(() => this._refreshPreviews(), PREVIEW_INTERVAL_MS);
  }

  _stopPreview() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  }

  async _refreshPreviews() {
    const results = await Promise.all(
      this._devices.map(async (d) => {
        try {
          return [d.device_id, await api.preview(d.device_id, d.profile, this._overrides[d.device_id])];
        } catch (err) {
          return [d.device_id, { error: err.message }];
        }
      }),
    );
    for (const [deviceId, preview] of results) {
      if (!preview.balance) continue;
      const history = [...(this._balanceHistory[deviceId] ?? []), preview.balance];
      this._balanceHistory[deviceId] = history.slice(-BALANCE_SAMPLES);
    }
    this._previews = Object.fromEntries(results);
  }

  _toggle(integration) {
    if (!integration.supported) return;
    const selected = new Set(this._selected);
    if (selected.has(integration.entry_id)) selected.delete(integration.entry_id);
    else selected.add(integration.entry_id);
    this._selected = selected;
  }

  async _accept(device) {
    const id = device.device_id;
    const overrides = this._overrides[id] ?? {};
    this._busy = new Set([...this._busy, id]);
    try {
      const stored = this._configDevices[id];
      const saved = stored
        ? await api.setOverrides(stored.id, overrides)
        : await api.addDevice(id, device.profile, overrides);
      this._configDevices = { ...this._configDevices, [id]: saved };
      this._editing = this._without(this._editing, id);
      this._dirty = this._without(this._dirty, id);
      this._error = null;
    } catch (err) {
      this._error = `Gerät konnte nicht gespeichert werden: ${err.message}`;
    } finally {
      this._busy = this._without(this._busy, id);
    }
  }

  _without(set, value) {
    const copy = new Set(set);
    copy.delete(value);
    return copy;
  }

  async _toggleEdit(device) {
    const id = device.device_id;
    if (this._editing.has(id)) {
      this._editing = this._without(this._editing, id);
      return;
    }
    this._editing = new Set([...this._editing, id]);
    if (!this._entities[id]) {
      try {
        this._entities = { ...this._entities, [id]: await api.entities(id) };
      } catch (err) {
        this._error = `Entitäten konnten nicht geladen werden: ${err.message}`;
      }
    }
  }

  _setOverride(deviceId, role, field, value) {
    const deviceOverrides = { ...(this._overrides[deviceId] ?? {}) };
    const roleOverride = { ...(deviceOverrides[role] ?? {}) };
    if (value === null || value === "") delete roleOverride[field];
    else roleOverride[field] = value;
    if (Object.keys(roleOverride).length) deviceOverrides[role] = roleOverride;
    else delete deviceOverrides[role];
    this._overrides = { ...this._overrides, [deviceId]: deviceOverrides };
    this._dirty = new Set([...this._dirty, deviceId]);
    this._balanceHistory[deviceId] = [];
    this._refreshPreviews();
  }

  _resetOverrides(deviceId) {
    this._overrides = { ...this._overrides, [deviceId]: {} };
    this._dirty = new Set([...this._dirty, deviceId]);
    this._balanceHistory[deviceId] = [];
    this._refreshPreviews();
  }

  render() {
    return html`
      <header>
        <div class="brand">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2 4 14h7l-1 8 9-12h-7z"></path></svg>
          <div>
            <div class="title">Sunflow</div>
            <div class="subtitle">Einrichtung${this._haConnected === false ? " · keine Verbindung zu Home Assistant" : ""}</div>
          </div>
        </div>
        <button class="switch" aria-pressed=${this._advanced} @click=${() => (this._advanced = !this._advanced)}>
          <span class="track ${this._advanced ? "on" : ""}"><span class="knob"></span></span>
          Expertenmodus
        </button>
      </header>
      <div class="layout">
        <nav aria-label="Schritte">
          ${STEPS.map(
            (label, i) => html`
              <button class="step ${i === this._step ? "active" : ""}" aria-current=${i === this._step ? "step" : "false"}
                @click=${() => this._goTo(i)}>
                <span class="dot ${i < this._step ? "done" : ""}">${i < this._step ? "✓" : i + 1}</span>
                <span>${label}</span>
              </button>
            `,
          )}
        </nav>
        <main>
          ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
          ${this._renderStep()}
          <div class="footer">
            ${this._step > 0 ? html`<button class="secondary" @click=${() => this._goTo(this._step - 1)}>Zurück</button>` : nothing}
            <span class="spacer"></span>
            ${this._step < STEPS.length - 1
              ? html`<button class="primary" @click=${() => this._goTo(this._step + 1)}>Weiter</button>`
              : nothing}
          </div>
        </main>
      </div>
    `;
  }

  _renderStep() {
    switch (this._step) {
      case 0:
        return this._renderIntegrations();
      case 1:
        return this._renderDevices();
      default:
        return html`
          <h1>${STEPS[this._step]}</h1>
          <p class="muted">Dieser Schritt ist noch nicht umgesetzt.</p>
        `;
    }
  }

  _renderIntegrations() {
    if (this._integrations === null) return html`<h1>Gefundene Integrationen</h1><p class="muted">Suche in Home Assistant …</p>`;
    return html`
      <div class="heading">
        <h1>Gefundene Integrationen</h1>
        <p class="muted">In deinem Home Assistant laufen diese Integrationen. Wähle aus, was Sunflow nutzen soll.</p>
      </div>
      ${this._integrations.length === 0
        ? html`<p class="muted">Keine unterstützte Integration gefunden.</p>`
        : html`
            <div class="cards">
              ${this._integrations.map((i) => this._renderIntegration(i))}
            </div>
          `}
    `;
  }

  _renderIntegration(integration) {
    const selected = this._selected.has(integration.entry_id);
    const matched = integration.devices.filter((d) => d.profile).length;
    let chip;
    if (!integration.supported) chip = html`<span class="chip warn">Nicht unterstützt – im Expertenmodus manuell</span>`;
    else if (matched === 0) chip = html`<span class="chip warn">Kein passendes Profil</span>`;
    else chip = html`<span class="chip ok">Unterstützt · ${matched} ${matched === 1 ? "Gerät" : "Geräte"}</span>`;
    return html`
      <button class="card ${selected ? "selected" : ""}" aria-pressed=${selected}
        ?disabled=${!integration.supported} @click=${() => this._toggle(integration)}>
        <div class="card-head">
          <span class="card-title">${integration.title}</span>
          <span class="check ${selected ? "on" : ""}" aria-hidden="true">${selected ? "✓" : ""}</span>
        </div>
        <span class="muted small">${integration.domain}${integration.in_energy_dashboard ? " · im Energie-Dashboard" : ""}</span>
        ${chip}
      </button>
    `;
  }

  _renderDevices() {
    const devices = this._devices;
    return html`
      <div class="heading">
        <h1>Geräte</h1>
        <p class="muted">Profile wurden automatisch erkannt. Prüfe die Live-Werte – passen sie zu dem, was deine Anlage gerade tut?</p>
      </div>
      ${devices.length === 0 ? html`<p class="muted">Keine Geräte mit passendem Profil ausgewählt.</p>` : nothing}
      ${devices.map((d) => this._renderDevice(d))}
    `;
  }

  _renderDevice(device) {
    const id = device.device_id;
    const preview = this._previews[id];
    const configured = Boolean(this._configDevices[id]);
    const editing = this._editing.has(id);
    const dirty = this._dirty.has(id);
    return html`
      <section class="device">
        <div class="device-head">
          <div>
            <div class="device-title">${device.name}</div>
            <div class="muted small">${[device.manufacturer, device.model].filter(Boolean).join(" ")} · über ${device.integration} · Profil ${device.profile}</div>
          </div>
          ${device.components.length
            ? html`<span class="chip neutral">${device.components.length} Komponenten</span>`
            : nothing}
        </div>
        ${preview === undefined ? html`<p class="muted">Lade Live-Werte …</p>` : nothing}
        ${preview?.error ? html`<div class="alert error">${preview.error}</div>` : nothing}
        ${preview?.roles ? this._renderTiles(preview) : nothing}
        ${preview?.balance ? this._renderBalance(device.device_id) : nothing}
        ${editing && preview?.roles ? this._renderRoleEditor(device, preview) : nothing}
        ${!editing && this._advanced && preview?.roles ? this._renderRoleTable(preview) : nothing}
        ${this._advanced && device.components.length ? this._renderComponents(device) : nothing}
        <div class="actions">
          ${configured && !dirty
            ? html`<span class="chip ok">✓ Übernommen</span>`
            : html`<button class="ok" ?disabled=${this._busy.has(id) || !preview?.complete}
                @click=${() => this._accept(device)}>
                ${configured ? "Anpassungen speichern" : dirty ? "Mit Anpassungen übernehmen" : "Werte passen"}
              </button>`}
          <button class="secondary" aria-expanded=${editing} @click=${() => this._toggleEdit(device)}>
            ${editing ? "Anpassen schließen" : "Anpassen"}
          </button>
        </div>
      </section>
    `;
  }

  _renderTiles(preview) {
    return html`
      <div class="tiles">
        ${preview.roles
          .filter((r) => r.unit !== null || r.missing || isText(r))
          .map(
            (r) => html`
              <div class="tile ${r.missing ? "missing" : ""}">
                <div class="small muted">${roleLabel(r.role)}</div>
                <div class="value ${isText(r) ? "text" : ""}">
                  ${isText(r) ? r.raw_state : SIGNED_ROLES.has(r.role) ? formatSigned(r.value, r.unit) : formatValue(r.value, r.unit)}
                </div>
                <div class="small muted">${r.missing ? "nicht gefunden" : roleHint(r.role, r.value)}</div>
              </div>
            `,
          )}
      </div>
    `;
  }

  _renderBalance(deviceId) {
    const history = this._balanceHistory[deviceId] ?? [];
    const latest = history.at(-1);
    const passed = history.filter((b) => b.plausible).length;
    let state, text;
    if (history.length < BALANCE_MIN_SAMPLES) {
      state = "neutral";
      text = `Bilanz wird geprüft … (${history.length}/${BALANCE_MIN_SAMPLES} Messungen)`;
    } else if (passed * 2 > history.length) {
      state = "ok";
      text = `✓ Bilanz plausibel (${passed} von ${history.length} Messungen)`;
    } else {
      state = "warn";
      text = `⚠ Bilanz unplausibel (${passed} von ${history.length} Messungen)`;
    }
    return html`
      <div class="alert ${state}">
        <div>${text}</div>
        <div class="small">
          Letzte Messung: ${BALANCE_FORMULAS[latest.method]} = ${formatValue(latest.computed_load, "W")},
          Hausverbrauch laut Gerät ${formatValue(latest.reported_load, "W")}
          (Abweichung ${formatValue(Math.abs(latest.deviation), "W")})
        </div>
      </div>
    `;
  }

  _renderRoleTable(preview) {
    return html`
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Rolle</th><th>Entität</th><th>Rohwert</th><th>Normalisiert</th></tr>
          </thead>
          <tbody>
            ${preview.roles.map(
              (r) => html`
                <tr>
                  <td>${roleLabel(r.role)}<div class="small muted mono">${r.key}</div></td>
                  <td class="mono">${r.entity_id ?? "–"}</td>
                  <td>${r.raw_state ?? "–"} ${r.raw_unit ?? ""}</td>
                  <td>${r.value === null ? "–" : `${r.value} ${r.unit ?? ""}`}</td>
                </tr>
              `,
            )}
          </tbody>
        </table>
      </div>
    `;
  }

  _renderRoleEditor(device, preview) {
    const id = device.device_id;
    const overrides = this._overrides[id] ?? {};
    const entities = this._entities[id];
    const sensors = (entities ?? []).filter((e) => e.entity_id.startsWith("sensor."));
    return html`
      <div class="editor">
        <div class="editor-head">
          <div>
            <div class="editor-title">Zuordnung anpassen</div>
            <div class="small muted">Wähle eine andere Entität oder kehre das Vorzeichen um. Die Vorschau aktualisiert sich sofort.</div>
          </div>
          <button class="secondary" ?disabled=${!Object.keys(overrides).length} @click=${() => this._resetOverrides(id)}>
            Auf Profil zurücksetzen
          </button>
        </div>
        ${entities === undefined ? html`<p class="muted small">Lade Entitäten …</p>` : nothing}
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Rolle</th><th>Entität</th><th>Vorzeichen</th><th>Wert</th></tr>
            </thead>
            <tbody>
              ${preview.roles.map((r) => {
                const override = overrides[r.role] ?? {};
                const inverted = Boolean(override.sign) && override.sign !== r.profile_sign;
                return html`
                  <tr class=${Object.keys(override).length ? "overridden" : ""}>
                    <td>
                      ${roleLabel(r.role)}
                      ${Object.keys(override).length ? html`<span class="chip warn">angepasst</span>` : nothing}
                    </td>
                    <td>
                      <select aria-label="Entität für ${roleLabel(r.role)}"
                        @change=${(e) => this._setOverride(id, r.role, "entity_id", e.target.value)}>
                        <option value="" ?selected=${!override.entity_id}>
                          Profil: ${r.profile_entity_id ?? "nicht gefunden"}
                        </option>
                        ${sensors.map(
                          (e) => html`<option value=${e.entity_id} ?selected=${override.entity_id === e.entity_id}>
                            ${e.entity_id} (${e.state ?? "–"} ${e.unit ?? ""})
                          </option>`,
                        )}
                      </select>
                    </td>
                    <td>
                      ${r.profile_sign
                        ? html`<label class="inline">
                            <input type="checkbox" .checked=${inverted}
                              @change=${(e) =>
                                this._setOverride(id, r.role, "sign", e.target.checked ? OPPOSITE_SIGN[r.profile_sign] : null)}>
                            umkehren
                          </label>`
                        : html`<span class="muted small">–</span>`}
                    </td>
                    <td class="nowrap">
                      ${SIGNED_ROLES.has(r.role) ? formatSigned(r.value, r.unit) : formatValue(r.value, r.unit)}
                      <div class="small muted">${roleHint(r.role, r.value)}</div>
                    </td>
                  </tr>
                `;
              })}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  _renderComponents(device) {
    return html`
      <div class="small muted">
        Komponenten: ${device.components.map((c) => `${c.name} (${c.model ?? "?"})`).join(", ")}
      </div>
    `;
  }

  static styles = css`
    :host { display: block; min-height: 100vh; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 24px; line-height: 1.25; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }

    header {
      background: var(--sf-surface); border-bottom: 1px solid var(--sf-border);
      padding: 12px 24px; display: flex; flex-wrap: wrap; gap: 12px;
      align-items: center; justify-content: space-between;
    }
    .brand { display: flex; align-items: center; gap: 12px; color: var(--sf-primary); }
    .brand .title { font-weight: 700; font-size: 17px; color: var(--sf-text); }
    .brand .subtitle { font-size: 13px; color: var(--sf-muted); }

    .switch {
      display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 0 14px;
      border: 1px solid var(--sf-border); border-radius: 22px; background: var(--sf-surface); cursor: pointer; font-size: 14px;
    }
    .track { position: relative; width: 36px; height: 20px; border-radius: 10px; background: var(--sf-muted); transition: background .15s; }
    .track.on { background: var(--sf-primary); }
    .knob { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 8px; background: #fff; transition: left .15s; }
    .track.on .knob { left: 18px; }

    .layout {
      max-width: 1240px; margin: 0 auto; padding: 24px 16px;
      display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start;
    }
    nav {
      flex: 1 1 220px; max-width: 280px; background: var(--sf-surface);
      border: 1px solid var(--sf-border); border-radius: 12px; padding: 8px;
    }
    .step {
      display: flex; align-items: center; gap: 12px; width: 100%; min-height: 44px; padding: 6px 10px;
      border: 0; border-radius: 8px; background: transparent; cursor: pointer; text-align: left; font-size: 14px;
    }
    .step.active { background: var(--sf-primary-bg); color: var(--sf-primary-text); font-weight: 600; }
    .dot {
      flex: none; width: 26px; height: 26px; border-radius: 13px; display: flex; align-items: center; justify-content: center;
      font-size: 13px; font-weight: 600; background: var(--sf-neutral-bg); color: var(--sf-neutral-text);
    }
    .step.active .dot { background: var(--sf-primary); color: #fff; }
    .dot.done { background: var(--sf-ok); color: #fff; }

    main {
      flex: 999 1 560px; min-width: 0; background: var(--sf-surface); border: 1px solid var(--sf-border);
      border-radius: 12px; padding: 28px; display: flex; flex-direction: column; gap: 20px;
    }
    .heading { display: flex; flex-direction: column; gap: 6px; }

    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
    .card {
      display: flex; flex-direction: column; gap: 8px; align-items: flex-start; text-align: left; width: 100%;
      padding: 14px 16px; border-radius: 10px; cursor: pointer; border: 1px solid var(--sf-border);
      background: var(--sf-surface); margin: 1px;
    }
    .card.selected { border: 2px solid var(--sf-primary); background: var(--sf-primary-bg); margin: 0; }
    .card:disabled { cursor: default; opacity: .85; }
    .card-head { display: flex; justify-content: space-between; gap: 8px; width: 100%; }
    .card-title { font-weight: 600; }
    .check {
      flex: none; width: 22px; height: 22px; border-radius: 6px; display: flex; align-items: center; justify-content: center;
      border: 1.5px solid var(--sf-muted); font-size: 14px; font-weight: 700;
    }
    .check.on { background: var(--sf-primary); border-color: var(--sf-primary); color: #fff; }

    .chip { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 12px; }
    .chip.ok { background: var(--sf-ok-bg); color: var(--sf-ok-text); }
    .chip.warn { background: var(--sf-warn-bg); color: var(--sf-warn-text); }
    .chip.neutral { background: var(--sf-neutral-bg); color: var(--sf-neutral-text); }

    .device {
      border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px;
      display: flex; flex-direction: column; gap: 14px;
    }
    .device-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: center; }
    .device-title { font-weight: 600; font-size: 16px; }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 10px 12px; }
    .tile.missing { outline: 1px dashed var(--sf-warn-text); }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .tile .value.text { font-size: 15px; padding-top: 4px; }

    .alert { padding: 10px 14px; border-radius: 8px; font-size: 14px; }
    .alert.ok { background: var(--sf-ok-bg); color: var(--sf-ok-text); }
    .alert.warn { background: var(--sf-warn-bg); color: var(--sf-warn-text); }
    .alert.error { background: var(--sf-error-bg); color: var(--sf-error-text); }
    .alert.neutral { background: var(--sf-neutral-bg); color: var(--sf-neutral-text); }

    .editor { display: flex; flex-direction: column; gap: 10px; padding: 14px; background: var(--sf-subtle); border-radius: 8px; }
    .editor-head { display: flex; flex-wrap: wrap; gap: 10px; justify-content: space-between; align-items: center; }
    .editor-title { font-weight: 600; }
    .editor .table-wrap { background: var(--sf-surface); }
    tr.overridden td:first-child { box-shadow: inset 3px 0 0 var(--sf-warn-text); }
    select {
      font: inherit; font-size: 13px; min-height: 36px; max-width: 100%; padding: 0 8px;
      border: 1px solid var(--sf-border-strong); border-radius: 6px; background: var(--sf-surface); color: var(--sf-text);
    }
    .inline { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; cursor: pointer; }
    .inline input { width: 18px; height: 18px; }
    .nowrap { white-space: nowrap; }
    td .chip { margin-left: 6px; }

    .table-wrap { overflow-x: auto; border: 1px solid var(--sf-border); border-radius: 8px; }
    table { border-collapse: collapse; width: 100%; min-width: 640px; font-size: 13px; }
    th { text-align: left; font-weight: 600; background: var(--sf-subtle); padding: 8px 10px; }
    td { padding: 6px 10px; border-top: 1px solid var(--sf-border); vertical-align: top; }

    .actions, .footer { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .footer { border-top: 1px solid var(--sf-border); padding-top: 18px; }
    .spacer { flex: 1; }
    .primary, .secondary, .ok {
      min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600;
    }
    .primary { border: 0; background: var(--sf-primary); color: #fff; }
    .ok { border: 0; background: var(--sf-ok); color: #fff; }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sunflow-app", SunflowApp);
