import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import "./plan-view.js";
import { forecastChart, priceChart, SERIES_COLORS, UNASSIGNED_COLOR } from "./charts.js";
import {
  devicePurpose,
  formatSigned,
  formatValue,
  isControllable,
  roleHint,
  roleLabel,
  SIGNED_ROLES,
  FEED_IN_MODELS,
  displayUnit,
  fromDisplay,
  paramLabel,
  toDisplay,
} from "./format.js";

const STEPS = ["Integrationen", "Geräte", "Kenndaten", "Gerätetest", "Bilanzprüfung", "Tarif & Prognose", "Fertig"];
const PREVIEW_INTERVAL_MS = 5000;
// Sensors are polled at different times, so single readings can disagree.
const BALANCE_SAMPLES = 6;
const BALANCE_MIN_SAMPLES = 3;
const UNAVAILABLE = new Set(["unavailable", "unknown", ""]);

const ISSUE_STYLES = {
  error: { css: "error", icon: "✕" },
  warning: { css: "warn", icon: "⚠" },
  info: { css: "neutral", icon: "ℹ" },
};

const FIXED_PRICE = "__fixed__";
const FORECAST_SOURCES = { solcast: "Solcast", forecast_solar: "Forecast.Solar" };

const PRICE_ADAPTERS = {
  timeslots: "Zeittarif",
  nordpool: "Börsenpreis (Nord Pool)",
  epex_spot: "Börsenpreis (EPEX Spot)",
  fixed: "nur aktueller Preis",
};
const weekdayTimeFormat = new Intl.DateTimeFormat("de-DE", { weekday: "short", hour: "2-digit", minute: "2-digit" });
const timeOnlyFormat = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });

function formatCt(eurPerKwh) {
  return `${(eurPerKwh * 100).toFixed(1).replace(".", ",")} ct`;
}

function weekdayTime(iso) {
  return weekdayTimeFormat.format(new Date(iso));
}

function timeOnly(iso) {
  return timeOnlyFormat.format(new Date(iso));
}

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
    _params: { state: true },
    _paramEdits: { state: true },
    _feedEdits: { state: true },
    _site: { state: true },
    _sources: { state: true },
    _priceSeries: { state: true },
    _forecastSeries: { state: true },
    _summary: { state: true },
    _view: { state: true },
    _nameEdits: { state: true },
    _efficiency: { state: true },
    _tests: { state: true },
    _testConfirm: { state: true },
    _fixedPriceMode: { state: true },
    _fixedPriceEdit: { state: true },
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
    // Sunflow device id -> parameter list from the backend
    this._params = {};
    this._paramEdits = {};
    this._feedEdits = {};
    this._site = null;
    this._sources = null;
    this._priceSeries = null;
    this._forecastSeries = null;
    this._summary = null;
    this._view = null;
    this._nameEdits = {};
    this._efficiency = {};
    // Sunflow device id -> device test state
    this._tests = {};
    this._testConfirm = new Set();
    this._fixedPriceMode = false;
    this._fixedPriceEdit = undefined;
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
      // Completed setups open on the plan, new ones in the wizard
      this._view ??= config.setup_completed ? "plan" : "setup";
      this._error = null;
    } catch (err) {
      this._error = `Daten konnten nicht geladen werden: ${err.message}`;
    }
  }

  get _devices() {
    return (this._integrations ?? [])
      .filter((i) => this._selected.has(i.entry_id))
      .flatMap((i) => i.devices.map((d) => ({ ...d, integration: i.name ?? i.title })))
      .filter((d) => d.profile);
  }

  _goTo(step) {
    this._step = step;
    this._stopPreview();
    if (step === 1) this._startPreview();
    if (step === 2) this._loadParameters();
    if (step === 4) this._startSite();
    if (step === 5) this._loadSources();
    if (step === 6) this._loadSummary();
    if (step === 3) this._startTestPolling();
  }

  get _testableDevices() {
    return Object.values(this._configDevices).filter((d) =>
      (this._capabilitiesByHaDevice[d.ha_device_id] ?? []).includes("battery_window"),
    );
  }

  _startTestPolling() {
    this._refreshTests();
    this._timer = setInterval(() => this._refreshTests(), 2000);
  }

  async _refreshTests() {
    const entries = await Promise.all(
      this._testableDevices.map(async (d) => {
        try {
          return [d.id, await api.testState(d.id)];
        } catch {
          return [d.id, this._tests[d.id] ?? null];
        }
      }),
    );
    const tests = Object.fromEntries(entries);
    // A finished test may have stored test_passed_at; reload config once it ends
    const finished = entries.some(([id, t]) => t && !t.running && this._tests[id]?.running);
    this._tests = tests;
    if (finished) {
      const config = await api.config();
      this._configDevices = Object.fromEntries(config.devices.map((d) => [d.ha_device_id, d]));
    }
  }

  async _startTest(device) {
    try {
      this._tests = { ...this._tests, [device.id]: await api.startTest(device.id) };
      this._testConfirm = this._without(this._testConfirm, device.id);
      this._error = null;
    } catch (err) {
      this._error = `Test konnte nicht gestartet werden: ${err.message}`;
    }
  }

  async _abortTest(device) {
    try {
      this._tests = { ...this._tests, [device.id]: await api.abortTest(device.id) };
    } catch (err) {
      this._error = `Abbruch fehlgeschlagen: ${err.message}`;
    }
  }

  async _loadSummary() {
    try {
      this._summary = await api.summary();
      this._error = null;
    } catch (err) {
      this._error = `Zusammenfassung konnte nicht geladen werden: ${err.message}`;
    }
  }

  async _completeSetup() {
    try {
      this._summary = await api.completeSetup();
      this._error = null;
    } catch (err) {
      this._error = `Einrichtung konnte nicht abgeschlossen werden: ${err.message}`;
    }
  }

  async _loadSources() {
    try {
      this._sources = await api.sources();
      this._error = null;
    } catch (err) {
      this._error = `Quellen konnten nicht geladen werden: ${err.message}`;
      return;
    }
    await Promise.all([this._loadPriceSeries(), this._loadForecastSeries()]);
  }

  async _loadPriceSeries() {
    const tariff = this._sources?.tariff;
    if (!tariff?.import_entity_id && tariff?.fixed_price_ct == null) {
      this._priceSeries = null;
      return;
    }
    try {
      this._priceSeries = await api.tariffSeries(tariff.import_entity_id);
    } catch (err) {
      this._error = `Preisverlauf konnte nicht geladen werden: ${err.message}`;
    }
  }

  async _loadForecastSeries() {
    try {
      this._forecastSeries = await api.forecastSeries();
    } catch (err) {
      this._error = `PV-Prognose konnte nicht geladen werden: ${err.message}`;
    }
  }

  async _setTariff(entityId) {
    if (entityId === FIXED_PRICE) {
      this._fixedPriceMode = true;
      return;
    }
    this._fixedPriceMode = false;
    await this._saveTariff({ import_entity_id: entityId || null, fixed_price_ct: null });
  }

  async _saveFixedPrice() {
    const value = fromDisplay(this._fixedPriceEdit ?? "", "ct");
    if (value === undefined || value === null || value < 0) {
      this._error = "Bitte einen gültigen Preis in ct/kWh eingeben.";
      return;
    }
    await this._saveTariff({ import_entity_id: null, fixed_price_ct: value });
    this._fixedPriceEdit = undefined;
  }

  async _saveTariff(body) {
    try {
      const tariff = await api.setTariff(body);
      this._sources = { ...this._sources, tariff };
      await this._loadPriceSeries();
    } catch (err) {
      this._error = `Preisquelle konnte nicht gespeichert werden: ${err.message}`;
    }
  }

  async _assignPlane(plane, deviceId) {
    await this._savePlanes(plane, { device_id: deviceId || null });
  }

  async _renamePlane(plane, name) {
    const custom = name.trim() || null;
    await this._savePlanes(plane, { custom_name: custom, name: custom ?? plane.detected_name });
  }

  async _savePlanes(plane, changes) {
    const planes = this._sources.forecast_planes.map((p) =>
      p.ref === plane.ref && p.source === plane.source ? { ...p, ...changes } : p,
    );
    try {
      await api.setForecast(
        planes.map(({ source, ref, detected_name, device_id, custom_name }) => ({
          source,
          ref,
          name: detected_name,
          device_id,
          custom_name,
        })),
      );
      this._sources = { ...this._sources, forecast_planes: planes };
      await this._loadForecastSeries();
    } catch (err) {
      this._error = `Prognoseflächen konnten nicht gespeichert werden: ${err.message}`;
    }
  }

  _startSite() {
    this._refreshSite();
    this._timer = setInterval(() => this._refreshSite(), PREVIEW_INTERVAL_MS);
  }

  async _refreshSite() {
    try {
      this._site = await api.site();
    } catch (err) {
      this._error = `Anlagenstatus konnte nicht geladen werden: ${err.message}`;
    }
  }

  get _capabilitiesByHaDevice() {
    return Object.fromEntries(
      (this._integrations ?? []).flatMap((i) => i.devices.map((d) => [d.device_id, d.capabilities ?? []])),
    );
  }

  async _loadParameters() {
    const devices = Object.values(this._configDevices);
    try {
      const results = await Promise.all(devices.map(async (d) => [d.id, await api.parameters(d.id)]));
      this._params = Object.fromEntries(results);
      const estimates = await Promise.all(
        devices
          .filter((d) => this._params[d.id].some((p) => p.name === "efficiency_pct"))
          .map(async (d) => [d.id, await api.efficiency(d.id).catch(() => null)]),
      );
      this._efficiency = Object.fromEntries(estimates);
      this._paramEdits = {};
      this._feedEdits = {};
    } catch (err) {
      this._error = `Kenndaten konnten nicht geladen werden: ${err.message}`;
    }
  }

  _editParam(deviceId, name, text) {
    this._paramEdits = { ...this._paramEdits, [deviceId]: { ...(this._paramEdits[deviceId] ?? {}), [name]: text } };
  }

  _editFeedIn(deviceId, field, value) {
    this._feedEdits = { ...this._feedEdits, [deviceId]: { ...(this._feedEdits[deviceId] ?? {}), [field]: value } };
  }

  async _saveDevice(device) {
    const params = this._params[device.id] ?? [];
    const parameters = {};
    for (const [name, text] of Object.entries(this._paramEdits[device.id] ?? {})) {
      const unit = params.find((p) => p.name === name)?.unit;
      const value = fromDisplay(text, unit);
      if (value === undefined) {
        this._error = `Ungültige Zahl bei „${paramLabel(name)}“.`;
        return;
      }
      parameters[name] = value;
    }
    const changes = { parameters };
    if (this._nameEdits[device.id] !== undefined) changes.name = this._nameEdits[device.id];
    const feedEdit = this._feedEdits[device.id];
    if (feedEdit) {
      const rate = fromDisplay(feedEdit.rate ?? toDisplay(device.feed_in?.rate_ct_per_kwh, "ct"), "ct");
      if (rate === undefined) {
        this._error = "Ungültige Zahl bei der Vergütung.";
        return;
      }
      changes.feed_in = { model: feedEdit.model ?? device.feed_in?.model ?? "eeg_fixed", rate_ct_per_kwh: rate };
    }
    this._busy = new Set([...this._busy, device.id]);
    try {
      const saved = await api.updateDevice(device.id, changes);
      this._configDevices = { ...this._configDevices, [saved.ha_device_id]: saved };
      const fresh = await api.parameters(device.id);
      this._params = { ...this._params, [device.id]: fresh };
      this._paramEdits = { ...this._paramEdits, [device.id]: {} };
      this._feedEdits = { ...this._feedEdits, [device.id]: undefined };
      this._nameEdits = { ...this._nameEdits, [device.id]: undefined };
      this._error = null;
    } catch (err) {
      this._error = `Kenndaten konnten nicht gespeichert werden: ${err.message}`;
    } finally {
      this._busy = this._without(this._busy, device.id);
    }
  }

  async _setMeasuredBy(device, value) {
    const changes = value ? { measured_by: { device_id: value, role: "pv_ext_power" } } : { clear_measured_by: true };
    try {
      const saved = await api.updateDevice(device.id, changes);
      this._configDevices = { ...this._configDevices, [saved.ha_device_id]: saved };
      await this._refreshSite();
    } catch (err) {
      this._error = `Zuordnung konnte nicht gespeichert werden: ${err.message}`;
    }
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
            <div class="subtitle">${this._view === "plan" ? "Übersicht" : "Einrichtung"}${this._haConnected === false ? " · keine Verbindung zu Home Assistant" : ""}</div>
          </div>
        </div>
        <div class="tabs" role="tablist">
          ${[
            ["plan", "Übersicht"],
            ["setup", "Einrichtung"],
          ].map(
            ([view, label]) => html`<button role="tab" class="tab ${this._view === view ? "active" : ""}"
              aria-selected=${this._view === view} @click=${() => this._setView(view)}>${label}</button>`,
          )}
        </div>
        <button class="switch" aria-pressed=${this._advanced} @click=${() => (this._advanced = !this._advanced)}>
          <span class="track ${this._advanced ? "on" : ""}"><span class="knob"></span></span>
          Expertenmodus
        </button>
      </header>
      ${this._view === "plan"
        ? html`<div class="layout"><main><sunflow-plan></sunflow-plan></main></div>`
        : this._renderWizard()}
    `;
  }

  _setView(view) {
    this._view = view;
    if (view === "plan") this._stopPreview();
    else this._goTo(this._step);
  }

  _renderWizard() {
    return html`
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
      case 2:
        return this._renderParameters();
      case 4:
        return this._renderSite();
      case 5:
        return this._renderSources();
      case 6:
        return this._renderSummary();
      case 3:
        return this._renderDeviceTest();
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
    const matched = integration.devices.filter((d) => d.profile);
    const capabilities = matched.flatMap((d) => d.capabilities ?? []);
    let chip;
    if (!integration.supported) chip = html`<span class="chip warn">Nicht unterstützt – im Expertenmodus manuell</span>`;
    else if (matched.length === 0) chip = html`<span class="chip warn">Kein passendes Profil</span>`;
    else if (isControllable(capabilities)) chip = html`<span class="chip ok">Unterstützt · steuerbar</span>`;
    else chip = html`<span class="chip neutral">Unterstützt · nur lesen</span>`;
    return html`
      <button class="card ${selected ? "selected" : ""}" aria-pressed=${selected}
        ?disabled=${!integration.supported} @click=${() => this._toggle(integration)}>
        <div class="card-head">
          <span class="card-title">${integration.name ?? integration.title}</span>
          <span class="check ${selected ? "on" : ""}" aria-hidden="true">${selected ? "✓" : ""}</span>
        </div>
        ${matched.map(
          (d) => html`<span class="model">
            <strong>${devicePurpose(d.capabilities ?? [])}</strong> · ${[d.manufacturer, d.model].filter(Boolean).join(" ") || d.name}
          </span>`,
        )}
        <span class="muted small">${[
          integration.title !== integration.name ? `Eintrag „${integration.title}“` : null,
          integration.in_energy_dashboard ? "im Energie-Dashboard" : null,
        ].filter(Boolean).join(" · ")}</span>
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

  _renderParameters() {
    const devices = Object.values(this._configDevices);
    return html`
      <div class="heading">
        <h1>Kenndaten</h1>
        <p class="muted">Was sich aus Home Assistant ermitteln ließ, ist vorgeschlagen. Ergänze oder überschreibe nur, was nicht stimmt.</p>
      </div>
      ${devices.length === 0
        ? html`<p class="muted">Noch kein Gerät übernommen – bitte in Schritt 2 „Werte passen“ wählen.</p>`
        : devices.map((d) => this._renderDeviceParameters(d))}
    `;
  }

  _renderDeviceParameters(device) {
    const params = (this._params[device.id] ?? []).filter((p) => this._advanced || !p.advanced);
    const capabilities = this._capabilitiesByHaDevice[device.ha_device_id] ?? [];
    const edits = this._paramEdits[device.id] ?? {};
    const feedEdit = this._feedEdits[device.id] ?? {};
    const nameEdit = this._nameEdits[device.id];
    const changed =
      Object.keys(edits).length > 0 || this._feedEdits[device.id] !== undefined || nameEdit !== undefined;
    return html`
      <section class="device">
        <div class="device-head">
          <div>
            <div class="device-title">${device.name}</div>
            <div class="muted small">${devicePurpose(capabilities)} · Profil ${device.profile}</div>
          </div>
        </div>
        <label class="field wide">
          <span class="field-label">Bezeichnung</span>
          <span class="input-unit">
            <input .value=${nameEdit ?? device.name}
              @input=${(e) => (this._nameEdits = { ...this._nameEdits, [device.id]: e.target.value })}>
          </span>
        </label>
        ${this._params[device.id] === undefined ? html`<p class="muted">Lade Kenndaten …</p>` : nothing}
        ${params.length
          ? html`<div class="fields">${params.map((p) => this._renderParameterField(device, p, edits))}</div>`
          : nothing}
        ${capabilities.includes("pv")
          ? html`
              <div class="fields">
                <label class="field">
                  <span class="field-label">Vergütung</span>
                  <select @change=${(e) => this._editFeedIn(device.id, "model", e.target.value)}>
                    ${Object.entries(FEED_IN_MODELS).map(
                      ([value, label]) => html`<option value=${value}
                        ?selected=${(feedEdit.model ?? device.feed_in?.model ?? "eeg_fixed") === value}>${label}</option>`,
                    )}
                  </select>
                </label>
                <label class="field">
                  <span class="field-label">Einspeisevergütung</span>
                  <span class="input-unit">
                    <input inputmode="decimal" placeholder="z. B. 8,2"
                      .value=${feedEdit.rate ?? toDisplay(device.feed_in?.rate_ct_per_kwh, "ct")}
                      @input=${(e) => this._editFeedIn(device.id, "rate", e.target.value)}>
                    <span class="muted">ct/kWh</span>
                  </span>
                </label>
              </div>
            `
          : nothing}
        <div class="actions">
          <button class="ok" ?disabled=${!changed || this._busy.has(device.id)} @click=${() => this._saveDevice(device)}>
            Speichern
          </button>
          ${!changed ? html`<span class="muted small">Keine ungespeicherten Änderungen</span>` : nothing}
        </div>
      </section>
    `;
  }

  _renderParameterField(device, param, edits) {
    const edited = edits[param.name];
    const value = edited ?? toDisplay(param.stored, param.unit);
    const placeholder = toDisplay(param.effective, param.unit);
    let source;
    if (param.stored !== null && edited === undefined) source = html`<span class="muted">eigener Wert</span>`;
    else if (param.derived !== null)
      source = html`<span class="ok-text">aus Sensoren: ${toDisplay(param.derived, param.unit)} ${displayUnit(param.unit)}</span>`;
    else if (param.default !== null)
      source = html`<span class="muted">Vorgabe: ${toDisplay(param.default, param.unit)} ${displayUnit(param.unit)}</span>`;
    return html`
      <label class="field">
        <span class="field-label">${paramLabel(param.name)}</span>
        <span class="input-unit">
          <input inputmode="decimal" .value=${value} placeholder=${placeholder}
            @input=${(e) => this._editParam(device.id, param.name, e.target.value)}>
          <span class="muted">${displayUnit(param.unit)}</span>
        </span>
        ${param.name === "efficiency_pct" ? this._renderEfficiencyHint(device, param) : nothing}
        <span class="small">
          ${source}
          ${param.stored !== null
            ? html` · <button class="link" @click=${() => this._editParam(device.id, param.name, "")}>zurücksetzen</button>`
            : nothing}
        </span>
      </label>
    `;
  }

  _renderEfficiencyHint(device, param) {
    const estimate = this._efficiency[device.id];
    if (estimate === undefined) return nothing;
    if (estimate === null) return html`<span class="small muted">Noch keine Historie für eine Schätzung.</span>`;
    const pct = (v) => `${(v * 100).toFixed(1).replace(".", ",")} %`;
    const value = Math.round(estimate.efficiency * 1000) / 10;
    return html`
      <span class="small">
        aus Historie: <strong>${pct(estimate.efficiency)}</strong> je Richtung (Zyklus ${pct(estimate.round_trip)}),
        ${estimate.cycles.toFixed(1).replace(".", ",")} Zyklen in ${Math.round(estimate.hours)} h
        ${estimate.plausible
          ? html` · <button class="link" @click=${() => this._editParam(device.id, param.name, String(value).replace(".", ","))}>
              ${estimate.reliable ? "übernehmen" : "vorläufig übernehmen"}</button>`
          : nothing}
      </span>
      ${!estimate.plausible
        ? html`<span class="small warn-text">Unplausibel – vermutlich Messfehler der Batterieleistung oder falsche Kapazität.
            Wird nicht angeboten.</span>`
        : !estimate.reliable
          ? html`<span class="small muted">Vorläufig – belastbar erst ab 2 vollen Zyklen. Enthält auch den Eigenverbrauch des Speichers.</span>`
          : html`<span class="small muted">Enthält auch den Eigenverbrauch des Speichers (z. B. DC/DC-Wandler der Module).</span>`}
    `;
  }

  _renderSite() {
    const site = this._site;
    const devices = Object.values(this._configDevices);
    const byId = Object.fromEntries(devices.map((d) => [d.id, d]));
    const capabilities = this._capabilitiesByHaDevice;
    const measuring = devices.filter((d) => (capabilities[d.ha_device_id] ?? []).includes("pv_ext"));
    const pvOnly = devices.filter((d) => {
      const caps = capabilities[d.ha_device_id] ?? [];
      return caps.includes("pv") && !caps.includes("battery");
    });
    return html`
      <div class="heading">
        <h1>Bilanzprüfung</h1>
        <p class="muted">Wie hängen deine Geräte zusammen? Sunflow prüft damit die Messwerte gegeneinander und zählt keine PV-Leistung doppelt.</p>
      </div>
      ${pvOnly.length && measuring.length
        ? html`
            <section class="device">
              <div class="device-title">Zusammenhang der Anlagen</div>
              ${pvOnly.map(
                (d) => html`
                  <label class="field wide">
                    <span class="field-label">${d.name} wird zusätzlich gemessen von</span>
                    <select @change=${(e) => this._setMeasuredBy(d, e.target.value)}>
                      <option value="" ?selected=${!d.measured_by}>niemandem (eigene Einspeisung)</option>
                      ${measuring.map(
                        (m) => html`<option value=${m.id} ?selected=${d.measured_by?.device_id === m.id}>
                          ${m.name} als „PV extern“
                        </option>`,
                      )}
                    </select>
                  </label>
                `,
              )}
            </section>
          `
        : nothing}
      ${site === null ? html`<p class="muted">Lade Anlagenstatus …</p>` : this._renderSiteStatus(site, byId)}
    `;
  }

  _renderSiteStatus(site, byId) {
    return html`
      ${site.hints.map((h) => html`<div class="alert warn">⚠ ${h}</div>`)}
      ${site.cross_checks.map((c) => {
        const own = byId[c.device_id]?.name ?? "?";
        const other = byId[c.measured_by]?.name ?? "?";
        const state = c.plausible === null ? "neutral" : c.plausible ? "ok" : "warn";
        return html`
          <div class="alert ${state}">
            <div>${c.plausible === null ? "Gegenprobe nicht möglich" : c.plausible ? "✓ Gegenprobe passt" : "⚠ Gegenprobe weicht ab"}:
              ${own} ${formatValue(c.own_value, "W")} · gemessen von ${other} ${formatValue(c.measured_value, "W")}</div>
            ${c.deviation !== null ? html`<div class="small">Abweichung ${formatValue(Math.abs(c.deviation), "W")}</div>` : nothing}
          </div>
        `;
      })}
      <div class="tiles">
        ${site.devices.map(
          (d) => html`
            <div class="tile">
              <div class="small muted">${d.name}</div>
              <div class="value">${d.pv_output === null ? "–" : formatValue(d.pv_output, "W")}</div>
              <div class="small muted">
                ${d.balance ? (d.balance.plausible ? "✓ Gerätebilanz plausibel" : "⚠ Gerätebilanz unplausibel") : "PV-Leistung"}
              </div>
            </div>
          `,
        )}
        <div class="tile total">
          <div class="small muted">PV gesamt</div>
          <div class="value">${formatValue(site.pv_total, "W")}</div>
          <div class="small muted">ohne Doppelzählung</div>
        </div>
      </div>
    `;
  }

  _renderSources() {
    const sources = this._sources;
    if (sources === null) return html`<h1>Tarif und Prognose</h1><p class="muted">Suche Quellen …</p>`;
    return html`
      <div class="heading">
        <h1>Tarif und Prognose</h1>
        <p class="muted">Strompreis und PV-Prognose kommen aus deinen vorhandenen Integrationen.</p>
      </div>
      ${this._renderPriceSource(sources)}
      ${this._renderForecastSource(sources)}
    `;
  }

  _renderPriceSource(sources) {
    const fixed = this._fixedPriceMode || (!sources.tariff.import_entity_id && sources.tariff.fixed_price_ct != null);
    const selected = fixed ? FIXED_PRICE : (sources.tariff.import_entity_id ?? "");
    const points = this._priceSeries?.points ?? [];
    return html`
      <section class="device">
        <div class="device-title">Bezugspreis</div>
        ${sources.prices.length === 0
          ? html`<p class="small muted">Keine Preis-Entität gefunden (Einheit €/kWh oder ct/kWh) – du kannst einen festen Preis angeben.</p>`
          : nothing}
        ${html`
              <label class="field wide">
                <span class="field-label">Quelle</span>
                <select @change=${(e) => this._setTariff(e.target.value)}>
                  <option value="" ?selected=${!selected}>– keine (nur Eigenverbrauch) –</option>
                  <option value=${FIXED_PRICE} ?selected=${selected === FIXED_PRICE}>Fester Preis …</option>
                  ${sources.prices.map(
                    (p) => html`<option value=${p.entity_id} ?selected=${selected === p.entity_id}>
                      ${p.name} · ${PRICE_ADAPTERS[p.adapter] ?? p.adapter}
                      ${p.current !== null ? ` · ${formatCt(p.current)}` : ""}
                    </option>`,
                  )}
                </select>
              </label>
            `}
        ${fixed
          ? html`
              <div class="fields">
                <label class="field">
                  <span class="field-label">Arbeitspreis brutto</span>
                  <span class="input-unit">
                    <input inputmode="decimal" placeholder="z. B. 32,5"
                      .value=${this._fixedPriceEdit ?? toDisplay(sources.tariff.fixed_price_ct, "ct")}
                      @input=${(e) => (this._fixedPriceEdit = e.target.value)}>
                    <span class="muted">ct/kWh</span>
                  </span>
                </label>
              </div>
              <div class="actions">
                <button class="ok" ?disabled=${this._fixedPriceEdit === undefined} @click=${() => this._saveFixedPrice()}>
                  Preis speichern
                </button>
              </div>
            `
          : nothing}
        ${points.length && !fixed ? this._renderPriceSummary(points) : nothing}
        ${points.length && !fixed
          ? html`<figure>
              <figcaption class="small muted">Bezugspreis nächste ${Math.round(points.length / 4)} h in ct/kWh</figcaption>
              ${priceChart(points, "Bezugspreis-Verlauf")}
            </figure>`
          : nothing}
        ${this._priceSeries?.adapter === "fixed" && !fixed
          ? html`<div class="alert warn">⚠ Diese Quelle liefert nur den aktuellen Preis, keinen Verlauf. Die Planung nimmt ihn als konstant an.</div>`
          : nothing}
      </section>
    `;
  }

  _renderPriceSummary(points) {
    const now = points[0];
    const min = Math.min(...points.map((p) => p.price));
    const firstMin = points.findIndex((p) => p.price === min);
    let lastMin = firstMin;
    while (lastMin + 1 < points.length && points[lastMin + 1].price === min) lastMin += 1;
    const window = `${weekdayTime(points[firstMin].start)}–${timeOnly(points[lastMin].end)}`;
    return html`
      <div class="tiles">
        <div class="tile"><div class="small muted">Jetzt</div><div class="value">${formatCt(now.price)}</div></div>
        <div class="tile"><div class="small muted">Günstigster Preis</div><div class="value">${formatCt(min)}</div>
          <div class="small muted">${window}</div></div>
        <div class="tile"><div class="small muted">Höchster Preis</div>
          <div class="value">${formatCt(Math.max(...points.map((p) => p.price)))}</div></div>
      </div>
    `;
  }

  _renderForecastSource(sources) {
    const devices = Object.values(this._configDevices);
    const plants = devices.filter((d) => (this._capabilitiesByHaDevice[d.ha_device_id] ?? []).includes("pv"));
    // Color follows the plane (its position among all planes), never its rank in the chart
    const colorOf = (plane) => {
      const index = sources.forecast_planes.findIndex((p) => p.source === plane.source && p.ref === plane.ref);
      return index >= 0 && index < SERIES_COLORS.length ? SERIES_COLORS[index] : UNASSIGNED_COLOR;
    };
    const series = (this._forecastSeries?.planes ?? []).map((p) => ({
      name: `${FORECAST_SOURCES[p.source] ?? p.source} · ${p.name}`,
      plant: p.plant,
      color: colorOf(p),
      points: p.points,
      kwh: p.points.reduce((s, x) => s + (x.power * (new Date(x.end) - new Date(x.start))) / 3600_000, 0) / 1000,
    }));
    return html`
      <section class="device">
        <div class="device-title">PV-Prognose</div>
        ${sources.forecast_planes.length === 0
          ? html`<p class="muted">Keine Prognose gefunden. Unterstützt werden Solcast und Forecast.Solar.</p>`
          : html`
              <p class="small muted">Ordne jede Prognosefläche der Anlage zu, deren Module dort liegen.
                Gibt es eine Dachfläche bei mehreren Anbietern, verwende sie nur einmal – sonst wird sie doppelt gezählt.</p>
              <div class="fields">
                ${sources.forecast_planes.map(
                  (plane) => html`
                    <div class="field">
                      <span class="field-label">${FORECAST_SOURCES[plane.source] ?? plane.source} · ${plane.name}${plane.capacity_kw ? ` (${plane.capacity_kw} kW)` : ""}</span>
                      <span class="small muted">${plane.today_kwh !== null ? `heute ${toDisplay(Math.round(plane.today_kwh * 10) / 10, "kWh")} kWh` : "keine Tagesprognose lesbar"}</span>
                      <span class="input-unit">
                        <input aria-label="Name der Fläche" placeholder=${plane.detected_name} .value=${plane.custom_name ?? ""}
                          @change=${(e) => this._renamePlane(plane, e.target.value)}>
                      </span>
                      <select aria-label="Anlage für ${plane.name}" @change=${(e) => this._assignPlane(plane, e.target.value)}>
                        <option value="" ?selected=${!plane.device_id}>nicht verwenden</option>
                        ${plants.map(
                          (p) => html`<option value=${p.id} ?selected=${plane.device_id === p.id}>${p.name}</option>`,
                        )}
                      </select>
                    </div>
                  `,
                )}
              </div>
            `}
        ${series.length
          ? html`
              <figure>
                <figcaption class="small muted">PV-Prognose nächste 48 h je Fläche (gestapelt)</figcaption>
                <div class="legend">
                  ${series.map(
                    (s) => html`<span class="legend-item"><span class="swatch" style="background:${s.color}"></span>
                      ${s.name} → ${s.plant} · ${s.kwh.toFixed(1).replace(".", ",")} kWh</span>`,
                  )}
                </div>
                ${forecastChart(series, "PV-Prognose je Fläche")}
              </figure>
            `
          : nothing}
      </section>
    `;
  }

  _renderDeviceTest() {
    const devices = this._testableDevices;
    return html`
      <div class="heading">
        <h1>Gerätetest</h1>
        <p class="muted">Ein kurzer Test prüft, ob die Steuerung beim Wechselrichter ankommt und die Vorzeichen stimmen.
          Danach geht der Speicher zurück auf Eigenverbrauch.</p>
      </div>
      ${devices.length === 0
        ? html`<p class="muted">Kein steuerbares Gerät übernommen – für reine PV-Wechselrichter ist kein Test nötig.</p>`
        : devices.map((d) => this._renderDeviceTestCard(d))}
    `;
  }

  _renderDeviceTestCard(device) {
    const test = this._tests[device.id];
    const running = test?.running;
    const confirmed = this._testConfirm.has(device.id);
    const passedAt = device.test_passed_at ? new Date(device.test_passed_at) : null;
    return html`
      <section class="device">
        <div class="device-head">
          <div>
            <div class="device-title">${device.name}</div>
            <div class="muted small">Profil ${device.profile}</div>
          </div>
          ${passedAt ? html`<span class="chip ok">✓ Bestanden ${weekdayTime(passedAt.toISOString())}</span>` : nothing}
        </div>
        ${running ? nothing : this._renderTestPlan(device, confirmed)}
        ${test ? this._renderTestProgress(device, test) : nothing}
      </section>
    `;
  }

  _renderTestPlan(device, confirmed) {
    return html`
      <ol class="plan small">
        <li>Ausgangszustand 20 s messen (nur lesen).</li>
        <li>Falls der Wechselrichter nicht im passiven Modus ist: dorthin umschalten
          (einmaliger EEPROM-Schreibzugriff).</li>
        <li>Je nach Zustand: Laden bzw. Entladen auf 300 W begrenzen oder 500 W aus dem Netz laden – 60 s lang.
          Dafür werden nur die Werte des passiven Modus geschrieben (RAM).</li>
        <li>Prüfen, ob die Batterie dem Befehl in der erwarteten Richtung folgt.</li>
        <li>Zurück auf Eigenverbrauch, ggf. zurück in den vorherigen Modus, 30 s beobachten.
          Bei Abbruch oder Fehler wird sofort zurückgesetzt.</li>
      </ol>
      <label class="inline confirm">
        <input type="checkbox" .checked=${confirmed}
          @change=${(e) =>
            (this._testConfirm = e.target.checked
              ? new Set([...this._testConfirm, device.id])
              : this._without(this._testConfirm, device.id))}>
        Automationen anderer Systeme, die den passiven Modus schreiben, sind pausiert.
      </label>
      <div class="actions">
        <button class="primary" ?disabled=${!confirmed} @click=${() => this._startTest(device)}>
          Test starten (schreibt in den Wechselrichter)
        </button>
      </div>
    `;
  }

  _renderTestProgress(device, test) {
    const phases = ["baseline", "command", "restore", "verify_restore"];
    const current = phases.indexOf(test.phase);
    const finished = !test.running;
    const lastSamples = test.samples.slice(-6);
    const result = (ok, good, bad) =>
      ok === null ? nothing : html`<li class=${ok ? "ok-text" : "warn-text"}>${ok ? "✓" : "✕"} ${ok ? good : bad}</li>`;
    return html`
      <div class="alert ${test.phase === "done" ? "ok" : test.running ? "neutral" : "warn"}" role="status">
        <div><strong>${test.message}</strong></div>
        ${test.expectation ? html`<div class="small">Erwartung: ${test.expectation}</div>` : nothing}
      </div>
      ${test.running
        ? html`
            <ol class="phases small">
              ${["Ausgangszustand", "Testbefehl", "Zurücksetzen", "Beobachten"].map(
                (label, i) => html`<li class=${i < current ? "done" : i === current ? "active" : ""}>${label}</li>`,
              )}
            </ol>
            <div class="actions">
              <button class="secondary" @click=${() => this._abortTest(device)}>Abbrechen und zurücksetzen</button>
            </div>
          `
        : nothing}
      ${lastSamples.length
        ? html`<div class="small muted">Batterie zuletzt:
            ${lastSamples.map((s) => (s.battery_power === null ? "–" : formatSigned(s.battery_power, "W"))).join(" · ")}
          </div>`
        : nothing}
      ${finished
        ? html`<ul class="results">
            ${result(test.control_works, "Steuerung wirkt", "Batterie folgt dem Befehl nicht")}
            ${result(test.sign_correct, "Vorzeichen korrekt (+ = Laden)", "Vorzeichen falsch – in Schritt 2 „umkehren“ setzen")}
            ${result(test.restored, "Zurück auf Eigenverbrauch", "Zurücksetzen fehlgeschlagen – bitte in HA prüfen!")}
            ${test.previous_mode
              ? result(
                  test.previous_mode_restored === true,
                  `Modus zurück auf „${test.previous_mode}“`,
                  `Modus nicht zurückgesetzt – bitte in HA auf „${test.previous_mode}“ stellen!`,
                )
              : nothing}
          </ul>`
        : nothing}
      ${test.error ? html`<div class="alert error">${test.error}</div>` : nothing}
      ${this._advanced && test.calls.length
        ? html`<div class="table-wrap"><table>
            <thead><tr><th>Aktion</th><th>Entität</th><th>Daten</th><th>Status</th></tr></thead>
            <tbody>${test.calls.map(
              (c) => html`<tr><td class="mono">${c.service}</td><td class="mono">${c.entity_id}</td>
                <td class="mono">${JSON.stringify(c.data)}</td><td>${c.status}</td></tr>`,
            )}</tbody>
          </table></div>`
        : nothing}
    `;
  }

  _renderSummary() {
    const summary = this._summary;
    if (summary === null) return html`<h1>Fertig</h1><p class="muted">Lade Zusammenfassung …</p>`;
    const counts = { error: 0, warning: 0, info: 0 };
    for (const issue of summary.issues) counts[issue.level] += 1;
    const priceName = summary.tariff
      ? (this._sources?.prices.find((p) => p.entity_id === summary.tariff)?.name ?? summary.tariff)
      : summary.fixed_price_ct != null
        ? `Fester Preis ${toDisplay(summary.fixed_price_ct, "ct")} ct/kWh`
        : "keiner – nur Eigenverbrauch";
    return html`
      <div class="heading">
        <h1>${summary.completed ? "Einrichtung abgeschlossen" : "Fast fertig"}</h1>
        <p class="muted">
          ${summary.completed
            ? "Sunflow ist eingerichtet. Die Planung folgt im nächsten Ausbauschritt."
            : counts.error
              ? "Bitte behebe die markierten Punkte, dann kannst du die Einrichtung abschließen."
              : "Prüfe die Übersicht und schließe die Einrichtung ab."}
        </p>
      </div>
      ${summary.issues.length
        ? html`
            <ul class="issues">
              ${summary.issues.map(
                (i) => html`
                  <li class="alert ${ISSUE_STYLES[i.level].css}">
                    <span>${ISSUE_STYLES[i.level].icon} ${i.message}</span>
                    <button class="link" @click=${() => this._goTo(i.step)}>zu „${STEPS[i.step]}“</button>
                  </li>
                `,
              )}
            </ul>
          `
        : html`<div class="alert ok">✓ Alles eingerichtet.</div>`}
      <ul class="summary-list">
        ${summary.devices.map(
          (d) => html`
            <li>
              <strong>${d.name}</strong> · ${devicePurpose(d.capabilities)}
              <div class="small muted">${this._summaryDetails(d).join(" · ")}</div>
            </li>
          `,
        )}
        <li>
          <strong>Bezugspreis</strong>
          <div class="small muted">${priceName}</div>
        </li>
        <li>
          <strong>PV-Prognose</strong>
          <div class="small muted">
            ${summary.forecast_planes.length
              ? summary.forecast_planes.map((p) => `${p.name} → ${p.plant ?? "nicht zugeordnet"}`).join(" · ")
              : "keine"}
          </div>
        </li>
      </ul>
      ${summary.completed
        ? html`<span class="chip ok">✓ Abgeschlossen</span>`
        : html`<div class="actions">
            <button class="ok" ?disabled=${!summary.can_complete} @click=${() => this._completeSetup()}>
              Einrichtung abschließen
            </button>
            ${counts.warning ? html`<span class="small muted">${counts.warning} Hinweise kannst du auch später erledigen.</span>` : nothing}
          </div>`}
    `;
  }

  _summaryDetails(device) {
    const details = [];
    const p = device.parameters;
    if (p.capacity_wh) details.push(`${toDisplay(p.capacity_wh, "Wh")} kWh`);
    if (p.min_soc !== undefined) details.push(`min. ${p.min_soc} %`);
    if (p.max_charge) details.push(`Laden ≤ ${formatValue(p.max_charge, "W")}`);
    if (p.max_discharge) details.push(`Entladen ≤ ${formatValue(p.max_discharge, "W")}`);
    if (device.feed_in) {
      const rate = device.feed_in.rate_ct_per_kwh;
      details.push(`${FEED_IN_MODELS[device.feed_in.model]}${rate !== null ? ` ${toDisplay(rate, "ct")} ct/kWh` : ""}`);
    }
    if (device.measured_by) details.push(`gemessen von ${device.measured_by} (PV extern)`);
    if (device.overrides.length) details.push(`${device.overrides.length} angepasste Rollen`);
    details.push(`Profil ${device.profile}`);
    return details;
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

    .tabs { display: flex; gap: 4px; background: var(--sf-subtle); padding: 4px; border-radius: 10px; }
    .tab {
      min-height: 36px; padding: 0 16px; border: 0; border-radius: 8px; background: transparent;
      cursor: pointer; font-size: 14px; color: var(--sf-muted);
    }
    .tab.active { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.08); }
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
      display: flex; flex-direction: column; justify-content: flex-start; gap: 8px; align-items: flex-start; text-align: left; width: 100%;
      padding: 14px 16px; border-radius: 10px; cursor: pointer; border: 1px solid var(--sf-border);
      background: var(--sf-surface); margin: 1px;
    }
    .card.selected { border: 2px solid var(--sf-primary); background: var(--sf-primary-bg); margin: 0; }
    .card:disabled { cursor: default; opacity: .85; }
    .card-head { display: flex; justify-content: space-between; gap: 8px; width: 100%; }
    .card-title { font-weight: 600; }
    .model { font-size: 14px; }
    .check {
      flex: none; width: 22px; height: 22px; border-radius: 6px; display: flex; align-items: center; justify-content: center;
      border: 1.5px solid var(--sf-muted); font-size: 14px; font-weight: 700;
    }
    .check.on { background: var(--sf-primary); border-color: var(--sf-primary); color: #fff; }

    .chip {
      display: inline-block; flex: none; align-self: flex-start; line-height: 1.4;
      font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 12px;
    }
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

    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field.wide { max-width: 520px; }
    .field-label { font-weight: 500; }
    .field select { min-height: 44px; font-size: 14px; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .ok-text { color: var(--sf-ok-text); }
    .link {
      border: 0; background: none; padding: 0; color: var(--sf-primary-text); text-decoration: underline;
      cursor: pointer; font-size: 13px;
    }
    .tile.total { background: var(--sf-primary-bg); }

    figure { margin: 0; display: flex; flex-direction: column; gap: 6px; }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; }

    .issues, .summary-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
    .issues li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: center; }
    .issues .link { color: inherit; }
    .summary-list li { padding: 12px 14px; border: 1px solid var(--sf-border); border-radius: 8px; }

    .plan { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; }
    .confirm { white-space: normal; font-size: 14px; }
    .phases { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
    .phases li { padding: 4px 10px; border-radius: 12px; background: var(--sf-neutral-bg); color: var(--sf-neutral-text); }
    .phases li.active { background: var(--sf-primary); color: #fff; }
    .phases li.done { background: var(--sf-ok-bg); color: var(--sf-ok-text); }
    .results { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; font-weight: 500; }
    .warn-text { color: var(--sf-error-text); }

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
