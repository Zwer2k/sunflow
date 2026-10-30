import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./lib/api.js";
import { confirmDialog } from "./components/dialog.js";
import { poll } from "./lib/poll.js";
import "./components/picker.js";
import "./views/live-view.js";
import "./views/plan-view.js";
import "./views/control-view.js";
import "./views/analysis-view.js";
import "./settings/standards-view.js";
import "./settings/vehicles-view.js";
import "./settings/charging-view.js";
import "./settings/batteries-view.js";
import { forecastChart, priceChart, SERIES_COLORS, UNASSIGNED_COLOR } from "./components/charts.js";
import {
  CATEGORIES,
  CATEGORY_LABELS,
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
  paramHint,
  paramLabel,
  toDisplay,
} from "./lib/format.js";

// Main menu: view, label, icon (24 px line icon)
const VIEWS = [
  ["live", "Live", "M3 12h4l3-8 4 16 3-8h4"],
  ["control", "Steuerung", "M8 6h8a6 6 0 0 1 0 12H8A6 6 0 0 1 8 6zM16 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  ["plan", "Planung", "M3 3v18h18M7 15l4-4 3 3 5-6"],
  ["analysis", "Analyse", "M5 20V11M11 20V5M17 20v-7M3 20h18"],
  ["setup", "Einstellungen", "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M16 4v4M10 10v4M18 16v4"],
];

// Inside Home Assistant (Ingress panel) HA's own toolbar already shows name and icon
const EMBEDDED = window.self !== window.top || window.location.pathname.includes("/api/hassio_ingress/");

// Remembered per browser; storage may be unavailable (private mode, previews)
const ADVANCED_KEY = "sunflow.advanced";

const REQUIRED_PARAMS = new Set(["capacity_wh", "tank_l"]);

/** Category of a discovered device not taken over yet; a loadpoint measuring a temperature is a heater. */
function deviceCategory(device, preview) {
  const caps = device.capabilities ?? [];
  if (caps.includes("loadpoint") || caps.includes("ev_charger")) {
    const soc = preview?.roles?.find((r) => r.role === "vehicle_soc");
    return ["°C", "°F", "K"].includes(soc?.raw_unit) ? "heat" : "wallbox";
  }
  if (caps.includes("pv")) return "inverter";
  if (caps.includes("battery")) return "battery";
  if (caps.includes("grid_meter")) return "meter";
  return "load";
}

/** Configured devices by HA device id with what belongs to them, the switched off ones marked. */
function configuredDevices(config) {
  const withRelations = (d) => ({
    ...d,
    flex: config.planning?.[d.id] ?? { strategy: "cost", target: null, ready_by: null, uses_battery: true, alternative_cost_ct: null },
    feed_in: config.feed_in?.[d.id] ?? null,
    measured_by: config.measured_by?.[d.id] ?? null,
  });
  return Object.fromEntries([
    ...config.devices.map((d) => [d.ha_device_id, withRelations(d)]),
    ...(config.disabled_devices ?? []).map((d) => [d.ha_device_id, { ...withRelations(d), disabled: true }]),
  ]);
}

/** A few live values for the compact card. */
function summaryValues(preview) {
  return preview.roles
    .filter((r) => !r.missing && r.value !== null && r.value !== undefined && r.unit)
    .slice(0, 4)
    .map((r) => `${roleLabel(r.role)} ${SIGNED_ROLES.has(r.role) ? formatSigned(r.value, r.unit) : formatValue(r.value, r.unit)}`)
    .join(" · ");
}

function readSetting(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSetting(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Not remembered, the switch still works for this visit
  }
}

// Settings areas; the first setup walks through them in this order
const STEPS = [
  ["integrations", "Integrationen"],
  ["devices", "Geräte"],
  ["test", "Gerätetest"],
  ["site", "Bilanzprüfung"],
  ["vehicles", "Fahrzeuge"],
  ["charging", "Laden"],
  ["consumers", "Verbraucher"],
  ["sources", "Tarif & Prognose"],
  ["standards", "Planung"],
  ["summary", "Übersicht"],
];
const stepIndex = (step) => (typeof step === "number" ? step : Math.max(STEPS.findIndex(([key]) => key === step), 0));
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
// Own consumers: profile per way of control
const MANUAL_PREFIX = "generic/";
const MANUAL_TYPES = { switch: "Schalter (ein/aus)", stepped: "Leistungsstufen (Auswahl)", current: "Stromstärke (Zahl)" };
const EMPTY_DIRECT = { profile: "", name: "", host: "", port: "", unit_id: "" };

const EMPTY_MANUAL = {
  name: "", type: "switch", kind: "load", switch_entity: "", level_entity: "", current_entity: "", power_entity: "",
  energy_entity: "", temperature_entity: "", steps: {}, max_power: "", daily_energy: "", tank_l: "",
};
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
    _setupCompleted: { state: true },
    _advanced: { state: true },
    _integrations: { state: true },
    _selected: { state: true },
    _previews: { state: true },
    _configDevices: { state: true },
    _editing: { state: true },
    _expanded: { state: true },
    _categories: { state: true },
    _overrides: { state: true },
    _entities: { state: true },
    _dirty: { state: true },
    _params: { state: true },
    _paramEdits: { state: true },
    _feedEdits: { state: true },
    _site: { state: true },
    _discoveryError: { state: true },
    _pendingRestore: { state: true },
    _recommendationCount: { state: true },
    _usages: { state: true },
    _restoring: { state: true },
    _measurements: { state: true },
    _sources: { state: true },
    _priceSeries: { state: true },
    _forecastSeries: { state: true },
    _summary: { state: true },
    _view: { state: true },
    _nameEdits: { state: true },
    _efficiency: { state: true },
    _flexEdits: { state: true },
    _candidates: { state: true },
    _manual: { state: true },
    _direct: { state: true },
    _directProfiles: { state: true },
    _manualEditId: { state: true },
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
    this._advanced = readSetting(ADVANCED_KEY) === "1";
    this._integrations = null;
    this._selected = new Set();
    this._previews = {};
    // ha_device_id -> stored device config
    this._configDevices = {};
    this._editing = new Set();
    // Device cards opened by the user; configured and complete ones start compact
    this._expanded = new Set();
    this._categories = {};
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
    this._flexEdits = {};
    this._candidates = null;
    this._manual = { ...EMPTY_MANUAL };
    this._direct = null;
    this._directProfiles = null;
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
    this._loadRecommendationCount();
    this._stopRecommendations = poll(() => this._loadRecommendationCount(), 15 * 60 * 1000);
    // Views report changes of the count and ask to open another view
    this.addEventListener("sf-recommendations", (e) => (this._recommendationCount = e.detail.count));
    this.addEventListener("sf-navigate", (e) => {
      this._setView(e.detail.view);
      if (e.detail.step !== undefined) this._goTo(e.detail.step);
    });
  }

  async _loadRecommendationCount() {
    try {
      this._recommendationCount = (await api.recommendations()).length;
    } catch {
      // Only a badge
    }
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPreview();
    this._stopRecommendations?.();
  }

  async _load() {
    // Health and config decide the view and work without Home Assistant; discovery needs it
    try {
      const [health, config] = await Promise.all([api.health(), api.config()]);
      this._haConnected = health.ha_connected;
      this._configDevices = configuredDevices(config);
      this._loadCategories();
      this._overrides = Object.fromEntries(Object.values(this._configDevices).map((d) => [d.ha_device_id, d.role_overrides ?? {}]));
      // Completed setups open on Live, new ones in the wizard
      this._setupCompleted = config.setup_completed;
      this._view ??= config.setup_completed ? "live" : "setup";
      this._error = null;
    } catch (err) {
      this._view ??= "live";
      this._error = `Daten konnten nicht geladen werden: ${err.message}`;
      return;
    }
    this._loadPendingRestore();
    api.latestPlan().then((plan) => (this._usages = plan.usages ?? {})).catch(() => {});
    await this._loadDiscovery();
  }

  _setAdvanced(on) {
    this._advanced = on;
    writeSetting(ADVANCED_KEY, on ? "1" : "0");
  }

  async _loadPendingRestore() {
    try {
      this._pendingRestore = await api.pendingRestore();
    } catch {
      // Only a hint; the views report connection problems themselves
    }
  }

  async _restoreAfterTest() {
    const pending = this._pendingRestore;
    const mode = pending?.previous_mode ? ` und den Betriebsmodus „${pending.previous_mode}“` : "";
    if (!(await confirmDialog({
      title: "Wechselrichter zurücksetzen",
      text: `Sunflow stellt jetzt am Wechselrichter den Eigenverbrauch${mode} wieder her.`,
      confirm: "Zurücksetzen",
    }))) return;
    this._restoring = true;
    try {
      const result = await api.restoreAfterTest();
      this._error = result.restored ? null : "Zurücksetzen nicht vollständig – bitte am Wechselrichter prüfen.";
      await this._loadPendingRestore();
    } catch (err) {
      this._error = `Zurücksetzen fehlgeschlagen: ${err.message}`;
    } finally {
      this._restoring = false;
    }
  }

  _renderPendingRestore() {
    const pending = this._pendingRestore;
    if (!pending) return nothing;
    const since = pending.started ? ` (seit ${new Date(pending.started).toLocaleString("de-DE")})` : "";
    return html`
      <div class="restore-banner" role="alert">
        <div>
          <strong>Gerätetest wurde unterbrochen${since}.</strong>
          ${pending.device_name ?? "Der Wechselrichter"} steht möglicherweise noch im Testzustand
          (z. B. Laden aus dem Netz oder begrenzte Ladung)${pending.previous_mode ? `, Betriebsmodus vorher „${pending.previous_mode}“` : ""}.
        </div>
        <button class="primary" ?disabled=${this._restoring} @click=${() => this._restoreAfterTest()}>
          ${this._restoring ? "Stelle wieder her …" : "Selbstverbrauch wiederherstellen"}
        </button>
      </div>
    `;
  }

  async _loadDiscovery() {
    this._discoveryError = null;
    try {
      const integrations = await api.discovery();
      this._integrations = integrations;
      this._selected = new Set(
        integrations.filter((i) => i.supported && i.devices.some((d) => d.profile)).map((i) => i.entry_id),
      );
    } catch (err) {
      this._discoveryError = `Home Assistant nicht erreichbar: ${err.message}`;
    }
  }

  get _devices() {
    return (this._integrations ?? [])
      .filter((i) => this._selected.has(i.entry_id))
      .flatMap((i) => i.devices.map((d) => ({ ...d, integration: i.name ?? i.title })))
      .filter((d) => d.profile);
  }

  /** Save what the current step has edited but not saved yet; false keeps the user there. */
  async _savePending() {
    const area = STEPS[this._step][0];
    if (area === "devices") {
      const find = (devices) =>
        devices.flatMap((d) => [d, ...find(d.components ?? [])]);
      const discovered = find((this._integrations ?? []).flatMap((i) => i.devices ?? []));
      for (const device of discovered.filter((d) => this._dirty.has(d.device_id) && this._configDevices[d.device_id])) {
        this._error = null;
        await this._accept(device);
        if (this._error) return false;
      }
    }
    if (area === "devices" || area === "consumers" || area === "sources") {
      const changed = (d) =>
        Object.keys(this._paramEdits[d.id] ?? {}).length > 0 ||
        this._feedEdits[d.id] !== undefined ||
        this._nameEdits[d.id] !== undefined ||
        this._flexEdits[d.id] !== undefined;
      for (const device of this._activeDevices.filter(changed)) {
        if (!(await this._saveDevice(device))) return false;
      }
    }
    if (area === "sources" && this._fixedPriceMode && this._fixedPriceEdit !== undefined) {
      await this._saveFixedPrice();
      if (this._fixedPriceEdit !== undefined) return false;
    }
    if (area === "charging") {
      const charging = this.renderRoot.querySelector("sf-charging");
      if (charging && !(await charging.saveIfChanged())) return false;
    }
    if (area === "standards") {
      const standards = this.renderRoot.querySelector("sf-standards");
      if (standards && !(await standards.saveIfChanged())) return false;
    }
    return true;
  }

  async _goTo(step) {
    step = stepIndex(step);
    // Moving on keeps the edits of this area instead of dropping them silently
    if (step !== this._step && !(await this._savePending())) return;
    this._step = step;
    this._stopPreview();
    const area = STEPS[step][0];
    if (area === "devices") {
      this._startPreview();
      this._loadParameters();
    }
    if (area === "consumers") this._loadParameters();
    if (area === "site") this._startSite();
    if (area === "sources") this._loadSources();
    if (area === "summary") this._loadSummary();
    if (area === "test") this._startTestPolling();
  }

  /** Configured and not switched off: only these are planned, tested and given settings. */
  /** The stored category of a configured device, else one from its live values. */
  _categoryOf(device) {
    const stored = this._configDevices[device.device_id ?? device.ha_device_id];
    return (stored && this._categories[stored.id]) ?? deviceCategory(device, this._previews[device.device_id ?? device.ha_device_id]);
  }

  /** Category of a configured device: from the backend, else from its capabilities. */
  _configuredCategory(device) {
    return (
      this._categories[device.id] ??
      deviceCategory({ capabilities: this._capabilitiesOf(device) }, this._previews[device.ha_device_id])
    );
  }

  async _loadCategories() {
    try {
      this._categories = await api.categories();
    } catch {
      // Grouping falls back to the live values
    }
  }

  get _activeDevices() {
    return Object.values(this._configDevices).filter((d) => !d.disabled);
  }

  get _testableDevices() {
    return this._activeDevices.filter((d) => {
      const caps = this._capabilitiesByHaDevice[d.ha_device_id] ?? [];
      return caps.includes("battery_window") || caps.includes("battery_control");
    });
  }

  _startTestPolling() {
    this._refreshTests();
    this._stopTimer = poll(() => this._refreshTests(), 2000);
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
      this._loadPendingRestore();
      const config = await api.config();
      this._configDevices = configuredDevices(config);
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
    // Keep the typed price when saving fails, so it is neither lost nor skipped by "Weiter"
    if (await this._saveTariff({ import_entity_id: null, fixed_price_ct: value })) this._fixedPriceEdit = undefined;
  }

  async _saveTariff(body) {
    try {
      const tariff = await api.setTariff(body);
      this._sources = { ...this._sources, tariff };
      await this._loadPriceSeries();
      return true;
    } catch (err) {
      this._error = `Preisquelle konnte nicht gespeichert werden: ${err.message}`;
      this.requestUpdate();
      return false;
    }
  }

  async _assignPlane(plane, deviceId) {
    // Strings of another plant no longer fit
    await this._savePlanes(plane, { device_id: deviceId || null, strings: [] });
  }

  _renderPlaneStrings(plane, strings) {
    const own = strings.filter((s) => s.device_id === plane.device_id);
    if (!plane.device_id || !own.length) return nothing;
    const chosen = new Set(plane.strings ?? []);
    const toggle = (id, on) => {
      const next = new Set(chosen);
      if (on) next.add(id);
      else next.delete(id);
      this._savePlanes(plane, { strings: [...next] });
    };
    return html`<div class="small plane-strings">
      <span class="muted">Gemessen von:</span>
      ${own.map(
        (s) => html`<label class="inline"><input type="checkbox" .checked=${chosen.has(s.id)}
          @change=${(e) => toggle(s.id, e.target.checked)}> ${s.name}</label>`,
      )}
      <span class="muted">– dann lernt Sunflow diese Fläche einzeln (z. B. Verschattung).</span>
    </div>`;
  }

  async _renamePlane(plane, name) {
    const custom = name.trim() || null;
    await this._savePlanes(plane, { custom_name: custom, name: custom ?? plane.detected_name });
  }

  async _savePlanes(plane, changes) {
    // Apply locally first and save one after another, so a rename followed quickly by an
    // assignment does not overwrite the rename with the older list
    const planes = this._sources.forecast_planes.map((p) =>
      p.ref === plane.ref && p.source === plane.source ? { ...p, ...changes } : p,
    );
    this._sources = { ...this._sources, forecast_planes: planes };
    this._planeSave = (this._planeSave ?? Promise.resolve()).then(() => this._storePlanes());
    return this._planeSave;
  }

  async _storePlanes() {
    const planes = this._sources.forecast_planes;
    try {
      await api.setForecast(
        planes.map(({ source, ref, detected_name, device_id, custom_name, strings }) => ({
          source,
          ref,
          name: detected_name,
          device_id,
          custom_name,
          strings: strings ?? [],
        })),
      );
      await this._loadForecastSeries();
    } catch (err) {
      this._error = `Prognoseflächen konnten nicht gespeichert werden: ${err.message}`;
    }
  }

  _startSite() {
    this._loadMeasurements();
    this._refreshSite();
    this._stopTimer = poll(() => this._refreshSite(), PREVIEW_INTERVAL_MS);
  }

  async _loadMeasurements() {
    try {
      this._measurements = await api.measurements();
    } catch (err) {
      this._error = `Messquellen konnten nicht geladen werden: ${err.message}`;
    }
  }

  async _setMeasurement(quantity, deviceId) {
    try {
      this._measurements = await api.setMeasurements({ [quantity]: deviceId || null });
      this._error = null;
    } catch (err) {
      this._error = `Messquelle konnte nicht gespeichert werden: ${err.message}`;
    }
  }

  _renderMeasurements(byId) {
    const entries = Object.entries(this._measurements ?? {}).filter(([, m]) => m.options.length);
    if (!entries.length) return nothing;
    return html`
      <section class="device">
        <div class="device-title">Messquellen</div>
        <p class="small muted">Welches Gerät liefert Leistung und Zählerstände? Gilt für Live, Verlauf, Energiebilanz und
          Schattenbetrieb. „Automatisch“ nimmt einen eigenen Zähler (z. B. am Stromzähler) vor dem Wechselrichter.</p>
        ${entries.map(([quantity, m]) => {
          const auto = m.effective && !m.selected ? ` (${byId[m.effective]?.name ?? m.options.find((o) => o.id === m.effective)?.name})` : "";
          return html`
            <div class="field wide">
              <span class="field-label">${m.label}</span>
              <select aria-label=${m.label} @change=${(e) => this._setMeasurement(quantity, e.target.value)}>
                <option value="" ?selected=${!m.selected}>Automatisch${auto}</option>
                ${m.options.map((o) => html`<option value=${o.id} ?selected=${m.selected === o.id}>${o.name}</option>`)}
              </select>
            </div>
          `;
        })}
      </section>
    `;
  }

  async _refreshSite() {
    try {
      this._site = await api.site();
    } catch (err) {
      this._error = `Anlagenstatus konnte nicht geladen werden: ${err.message}`;
    }
  }

  /** Capabilities of a configured device; manual devices are not part of discovery. */
  _capabilitiesOf(device) {
    return (
      this._capabilitiesByHaDevice[device.ha_device_id] ?? (device.profile.startsWith(MANUAL_PREFIX) ? ["load"] : [])
    );
  }

  get _capabilitiesByHaDevice() {
    return Object.fromEntries(
      (this._integrations ?? []).flatMap((i) => i.devices.map((d) => [d.device_id, d.capabilities ?? []])),
    );
  }

  async _loadParameters() {
    this._loadCategories();
    const devices = this._activeDevices;
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

  async _removeDevice(device) {
    if (!(await confirmDialog({
      title: `„${device.name}“ entfernen?`,
      text: "Planung, Live und Steuerung berücksichtigen es dann nicht mehr. In Home Assistant bleibt das Gerät unverändert. " +
        "Zum vorübergehenden Abschalten genügt „aktiv“ – dann bleiben die Einstellungen erhalten.",
      confirm: "Entfernen",
      danger: true,
    }))) {
      return;
    }
    try {
      await api.removeDevice(device.id);
      const remaining = { ...this._configDevices };
      delete remaining[device.ha_device_id];
      this._configDevices = remaining;
      this._error = null;
    } catch (err) {
      this._error = `Gerät konnte nicht entfernt werden: ${err.message}`;
    }
  }

  async _saveDevice(device) {
    const params = this._params[device.id] ?? [];
    const parameters = {};
    for (const [name, text] of Object.entries(this._paramEdits[device.id] ?? {})) {
      const unit = params.find((p) => p.name === name)?.unit;
      const value = fromDisplay(text, unit);
      if (value === undefined) {
        this._error = `Ungültige Zahl bei „${paramLabel(name)}“.`;
        return false;
      }
      parameters[name] = value;
    }
    const changes = { parameters };
    if (this._nameEdits[device.id] !== undefined) changes.name = this._nameEdits[device.id];
    if (this._flexEdits[device.id] !== undefined) {
      const flex = { ...device.flex, ...this._flexEdits[device.id] };
      if (flex.target === undefined || flex.alternative_cost_ct === undefined) {
        this._error = "Bitte gültige Zahlen bei der Planung eingeben.";
        return false;
      }
      changes.flex = flex;
    }
    const feedEdit = this._feedEdits[device.id];
    if (feedEdit) {
      const rate = fromDisplay(feedEdit.rate ?? toDisplay(device.feed_in?.rate_ct_per_kwh, "ct"), "ct");
      if (rate === undefined) {
        this._error = "Ungültige Zahl bei der Vergütung.";
        return false;
      }
      changes.feed_in = { model: feedEdit.model ?? device.feed_in?.model ?? "eeg_fixed", rate_ct_per_kwh: rate };
    }
    this._busy = new Set([...this._busy, device.id]);
    try {
      const saved = await api.updateDevice(device.id, changes);
      this._configDevices = { ...this._configDevices, [saved.ha_device_id]: saved };
      this._loadCategories();
      const fresh = await api.parameters(device.id);
      this._params = { ...this._params, [device.id]: fresh };
      this._paramEdits = { ...this._paramEdits, [device.id]: {} };
      this._feedEdits = { ...this._feedEdits, [device.id]: undefined };
      this._nameEdits = { ...this._nameEdits, [device.id]: undefined };
      this._flexEdits = { ...this._flexEdits, [device.id]: undefined };
      this._error = null;
      return true;
    } catch (err) {
      this._error = `Kenndaten konnten nicht gespeichert werden: ${err.message}`;
      return false;
    } finally {
      this._busy = this._without(this._busy, device.id);
    }
  }

  async _setMeasuredBy(device, value) {
    const changes = value ? { measured_by: { device_id: value, role: "pv_ext_power" } } : { clear_measured_by: true };
    try {
      const saved = await api.updateDevice(device.id, changes);
      this._configDevices = { ...this._configDevices, [saved.ha_device_id]: saved };
      this._loadCategories();
      await this._refreshSite();
    } catch (err) {
      this._error = `Zuordnung konnte nicht gespeichert werden: ${err.message}`;
    }
  }

  _startPreview() {
    this._stopPreview();
    this._refreshPreviews();
    this._stopTimer = poll(() => this._refreshPreviews(), PREVIEW_INTERVAL_MS);
  }

  _stopPreview() {
    this._stopTimer?.();
    this._stopTimer = null;
  }

  async _refreshPreviews() {
    // Edits and the timer both refresh; an older, slower response must not win
    const sequence = (this._previewSequence = (this._previewSequence ?? 0) + 1);
    const results = await Promise.all(
      this._devices.map(async (d) => {
        try {
          return [d.device_id, await api.preview(d.device_id, d.profile, this._overrides[d.device_id])];
        } catch (err) {
          return [d.device_id, { error: err.message }];
        }
      }),
    );
    if (sequence !== this._previewSequence) return;
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
      <header class=${EMBEDDED ? "bare" : ""}>
        ${EMBEDDED ? nothing : html`<div class="brand">
          <svg width="32" height="32" viewBox="0 0 256 256" aria-hidden="true">
            <!-- Same mark as brand/sunflow.svg: the sun with three energy flows -->
            <g stroke="#F59E0B" stroke-width="11" stroke-linecap="round">
              <line x1="80" y1="12" x2="80" y2="26"></line><line x1="12" y1="80" x2="26" y2="80"></line>
              <line x1="31.9" y1="31.9" x2="41.8" y2="41.8"></line><line x1="128.1" y1="31.9" x2="118.2" y2="41.8"></line>
              <line x1="31.9" y1="128.1" x2="41.8" y2="118.2"></line>
            </g>
            <g fill="none" stroke-width="15" stroke-linecap="round">
              <path d="M80 80 C 150 80, 150 62, 212 62" stroke="#10B981"></path>
              <path d="M80 80 C 140 100, 150 136, 212 136" stroke="#0EA5E9"></path>
              <path d="M80 80 C 110 150, 140 210, 212 210" stroke="#8B5CF6"></path>
            </g>
            <circle cx="80" cy="80" r="40" fill="#F59E0B"></circle>
            <circle cx="224" cy="62" r="18" fill="#10B981"></circle>
            <circle cx="224" cy="136" r="18" fill="#0EA5E9"></circle>
            <circle cx="224" cy="210" r="18" fill="#8B5CF6"></circle>
          </svg>
          <div>
            <div class="title">Sunflow</div>
            <div class="subtitle">${{ live: "Live", control: "Steuerung", plan: "Planung", analysis: "Analyse" }[this._view] ?? "Einstellungen"}${this._haConnected === false ? " · keine Verbindung zu Home Assistant" : ""}</div>
          </div>
        </div>`}
        <nav class="tabs" aria-label="Hauptmenü">
          ${VIEWS.map(
            ([view, label, icon]) => html`<button class="tab ${this._view === view ? "active" : ""}" title=${label}
              aria-label=${label} aria-current=${this._view === view ? "page" : "false"} @click=${() => this._setView(view)}>
              <svg class="tab-icon" viewBox="0 0 24 24" aria-hidden="true"><path d=${icon}></path></svg>
              <span class="tab-label">${label}</span>
              ${view === "analysis" && this._recommendationCount
                ? html`<span class="badge" aria-label="${this._recommendationCount} Empfehlungen">${this._recommendationCount}</span>`
                : nothing}
            </button>`,
          )}
        </nav>
      </header>
      ${EMBEDDED && this._haConnected === false
        ? html`<div class="restore-banner" role="alert">Keine Verbindung zu Home Assistant – Werte sind evtl. veraltet.</div>`
        : nothing}
      ${this._renderPendingRestore()}
      ${{
        live: html`<div class="layout"><main><sunflow-live></sunflow-live></main></div>`,
        control: html`<div class="layout"><main><sunflow-control></sunflow-control></main></div>`,
        plan: html`<div class="layout"><main><sunflow-plan></sunflow-plan></main></div>`,
        analysis: html`<div class="layout"><main><sunflow-analysis></sunflow-analysis></main></div>`,
      }[this._view] ?? this._renderWizard()}
    `;
  }

  async _setView(view) {
    // Leaving the settings keeps their unsaved edits as well
    if (this._view === "setup" && view !== "setup" && !(await this._savePending())) return;
    this._view = view;
    if (view === "setup") this._goTo(this._step);
    else this._stopPreview();
  }

  _renderWizard() {
    return html`
      <div class="layout">
        <aside class="sidebar">
        <nav aria-label="Schritte">
          ${STEPS.map(
            ([, label], i) => html`
              <button class="step ${i === this._step ? "active" : ""}" aria-current=${i === this._step ? "step" : "false"}
                @click=${() => this._goTo(i)}>
                ${this._setupCompleted
                  ? nothing
                  : html`<span class="dot ${i < this._step ? "done" : ""}">${i < this._step ? "✓" : i + 1}</span>`}
                <span>${label}</span>
              </button>
            `,
          )}
        </nav>
        <button class="switch" aria-pressed=${this._advanced} @click=${() => this._setAdvanced(!this._advanced)}>
          <span class="track ${this._advanced ? "on" : ""}"><span class="knob"></span></span>
          Expertenmodus
        </button>
        </aside>
        <main>
          ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
          ${this._renderStep()}
          ${this._setupCompleted
            ? nothing
            : html`<div class="footer">
                ${this._step > 0 ? html`<button class="secondary" @click=${() => this._goTo(this._step - 1)}>Zurück</button>` : nothing}
                <span class="spacer"></span>
                ${this._step < STEPS.length - 1
                  ? html`<button class="primary" @click=${() => this._goTo(this._step + 1)}>Weiter</button>`
                  : nothing}
              </div>`}
        </main>
      </div>
    `;
  }

  _renderStep() {
    switch (STEPS[this._step][0]) {
      case "integrations":
        return this._renderIntegrations();
      case "devices":
        return this._renderDevices();
      case "test":
        return this._renderDeviceTest();
      case "site":
        return this._renderSite();
      case "vehicles":
        return html`<sf-vehicles></sf-vehicles>`;
      case "charging":
        return html`<sf-charging></sf-charging>`;
      case "consumers":
        return this._renderConsumers();
      case "sources":
        return this._renderSources();
      case "standards":
        return html`<sf-standards></sf-standards>`;
      default:
        return this._renderSummary();
    }
  }

  _renderIntegrations() {
    if (this._integrations === null) {
      return html`<h1>Gefundene Integrationen</h1>
        ${this._discoveryError
          ? html`<div class="alert error" role="alert">${this._discoveryError}</div>
              <button class="secondary" @click=${() => this._loadDiscovery()}>Erneut versuchen</button>`
          : html`<p class="muted">Suche in Home Assistant …</p>`}`;
    }
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
      ${this._renderDirectForm()}
    `;
  }

  /** Adding a device Sunflow talks to itself (Modbus TCP, HTTP), without an integration. */
  _renderDirectForm() {
    const form = this._direct;
    if (form === null) {
      return html`<section class="device compact">
        <div class="device-title">Ohne Home-Assistant-Integration</div>
        <p class="small muted">Manche Wechselrichter liest und steuert Sunflow selbst über das Netzwerk (Modbus TCP), z. B. Victron
          über das GX-Gerät.</p>
        <div><button class="secondary" @click=${() => this._openDirect()}>Gerät direkt anbinden …</button></div>
      </section>`;
    }
    const set = (field, value) => (this._direct = { ...this._direct, [field]: value });
    const profiles = this._directProfiles ?? [];
    const chosen = profiles.find((p) => p.profile === form.profile);
    return html`
      <section class="device">
        <div class="device-title">Gerät direkt anbinden</div>
        <div class="fields">
          <label class="field">
            <span class="field-label">Gerät</span>
            <select @change=${(e) => set("profile", e.target.value)}>
              <option value="" ?selected=${!form.profile}>bitte wählen …</option>
              ${profiles.map((p) => html`<option value=${p.profile} ?selected=${form.profile === p.profile}>
                ${[p.manufacturer, p.model].filter(Boolean).join(" ") || p.profile}</option>`)}
            </select>
          </label>
          <label class="field">
            <span class="field-label">Name</span>
            <span class="input-unit"><input placeholder=${chosen?.model ?? "z. B. Victron"} .value=${form.name}
              @input=${(e) => set("name", e.target.value)}></span>
          </label>
          <label class="field">
            <span class="field-label">Adresse (IP oder Hostname)</span>
            <span class="input-unit"><input placeholder="192.168.1.50" .value=${form.host} @input=${(e) => set("host", e.target.value)}></span>
          </label>
          <label class="field">
            <span class="field-label">Port</span>
            <span class="input-unit"><input inputmode="numeric" placeholder=${chosen?.port ?? 502} .value=${form.port}
              @input=${(e) => set("port", e.target.value)}></span>
          </label>
          ${chosen?.type === "modbus_tcp"
            ? html`<label class="field">
                <span class="field-label">Modbus-Unit-ID</span>
                <span class="input-unit"><input inputmode="numeric" placeholder=${chosen?.unit_id ?? 1} .value=${form.unit_id}
                  @input=${(e) => set("unit_id", e.target.value)}></span>
              </label>`
            : nothing}
        </div>
        <p class="small muted">Sunflow liest das Gerät sofort einmal; danach erscheint es unter „Geräte“ mit seinen Live-Werten.
          Gesteuert wird es erst, wenn du es unter „Steuerung“ freigibst.</p>
        <div class="actions">
          <button class="primary" ?disabled=${!form.profile || !form.host.trim() || this._busy.has("direct")}
            @click=${() => this._addDirect()}>Anbinden</button>
          <button class="secondary" @click=${() => (this._direct = null)}>Abbrechen</button>
        </div>
      </section>
    `;
  }

  async _openDirect() {
    this._direct = { ...EMPTY_DIRECT };
    if (this._directProfiles === null) {
      try {
        this._directProfiles = await api.directProfiles();
      } catch (err) {
        this._error = `Profile konnten nicht geladen werden: ${err.message}`;
      }
    }
  }

  async _addDirect() {
    const form = this._direct;
    const number = (text) => (String(text).trim() === "" ? null : Number(text));
    this._busy = new Set([...this._busy, "direct"]);
    try {
      const saved = await api.addDirect({
        profile: form.profile, name: form.name, host: form.host.trim(), port: number(form.port), unit_id: number(form.unit_id),
      });
      this._configDevices = { ...this._configDevices, [saved.ha_device_id]: saved };
      await this._loadDiscovery();
      this._direct = null;
      this._error = null;
    } catch (err) {
      this._error = `Gerät konnte nicht angebunden werden: ${err.message}`;
    } finally {
      this._busy = this._without(this._busy, "direct");
    }
  }

  _renderIntegration(integration) {
    const selected = this._selected.has(integration.entry_id);
    const matched = integration.devices.filter((d) => d.profile);
    const capabilities = matched.flatMap((d) => d.capabilities ?? []);
    let chip;
    if (!integration.supported) chip = html`<span class="chip warn">Nicht unterstützt – Schalter oder Steckdosen unten als eigene Verbraucher hinzufügen</span>`;
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
      ${CATEGORIES.map(([key, title]) => {
        const members = devices.filter((d) => this._categoryOf(d) === key);
        const hasBattery = devices.some((d) => (d.capabilities ?? []).includes("battery"));
        const extra =
          key === "battery" && hasBattery ? html`<sf-batteries></sf-batteries>`
          : key === "load" ? this._renderManualDevices()
          : nothing;
        if (!members.length && key !== "load" && !(key === "battery" && hasBattery)) return nothing;
        return html`<h2 class="category">${title}</h2>${members.map((d) => this._renderDevice(d))}${extra}`;
      })}
    `;
  }

  _renderManualDevices() {
    const manual = Object.values(this._configDevices).filter((d) => d.profile.startsWith(MANUAL_PREFIX));
    const form = this._manual;
    const set = (field, value) => (this._manual = { ...this._manual, [field]: value });
    const candidates = this._candidates;
    const entity = (list, detail) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: detail(c) }));
    const unitDetail = (c) => `${c.state ?? "–"} ${c.unit ?? ""}`;
    const control = { switch: form.switch_entity, stepped: form.level_entity, current: form.current_entity }[form.type];
    const selected = (candidates?.selects ?? []).find((c) => c.entity_id === form.level_entity);
    return html`
      <section class="device">
        <div class="device-title">Eigene Verbraucher</div>
        <p class="small muted">Geräte, die Sunflow nicht automatisch erkennt: ein- und ausschaltbar (Shelly, Steckdose), in
          Leistungsstufen (z. B. ESPHome-Heizstab mit Auswahl) oder mit Ziel-Stromstärke – jeweils mit oder ohne Leistungs-
          und Verbrauchsmessung. Ein Heizstab im Speicher wird als „Wärme“ mit Temperatur geplant.</p>
        ${manual.length
          ? html`<ul class="summary-list">${manual.map(
              (d) => html`<li class="manual-item"><div><strong>${d.name}</strong>
                <div class="small muted">${MANUAL_TYPES[d.profile.slice(MANUAL_PREFIX.length)] ?? ""}${d.kind === "heater" ? " · Wärme" : ""}
                  · ${Object.values(d.role_overrides).map((o) => o.entity_id).filter(Boolean).join(", ")}</div>
                ${this._usages?.[d.id]
                  ? html`<div class="small">gemessen: ${formatValue(this._usages[d.id].daily_wh, "Wh")} pro Tag${this._usages[d.id].running_w
                      ? html` · läuft mit ${formatValue(this._usages[d.id].running_w, "W")}`
                      : nothing}</div>`
                  : nothing}</div>
                <span class="item-actions">
                  <button class="secondary" ?disabled=${this._manualEditId === d.id} @click=${() => this._editManual(d)}>Bearbeiten</button>
                  <button class="secondary danger" @click=${() => this._removeDevice(d)}>Entfernen</button>
                </span></li>`,
            )}</ul>`
          : nothing}
        ${candidates === null
          ? html`<button class="secondary" @click=${() => this._loadCandidates()}>Verbraucher hinzufügen …</button>`
          : html`
              ${this._manualEditId
                ? html`<div class="field-label">„${Object.values(this._configDevices).find((d) => d.id === this._manualEditId)?.name ?? ""}“ bearbeiten</div>`
                : nothing}
              <div class="fields">
                <label class="field">
                  <span class="field-label">Name</span>
                  <span class="input-unit"><input placeholder="z. B. E-Bike" .value=${form.name} @input=${(e) => set("name", e.target.value)}></span>
                </label>
                <label class="field">
                  <span class="field-label">Gesteuert über</span>
                  <select @change=${(e) => set("type", e.target.value)}>
                    ${Object.entries(MANUAL_TYPES).map(([value, label]) => html`<option value=${value} ?selected=${form.type === value}>${label}</option>`)}
                  </select>
                </label>
                <label class="field">
                  <span class="field-label">Art</span>
                  <select @change=${(e) => set("kind", e.target.value)}>
                    <option value="load" ?selected=${form.kind === "load"}>Verbraucher (Energie pro Tag)</option>
                    <option value="heater" ?selected=${form.kind === "heater"}>Wärme (Speicher mit Temperatur)</option>
                  </select>
                </label>
                ${form.type === "switch" || form.type === "current"
                  ? html`<div class="field">
                      <span class="field-label">Schalter${form.type === "current" ? " (optional)" : ""}</span>
                      <sf-picker label="Schalter" empty-label=${form.type === "current" ? "keiner" : nothing} placeholder="Name oder Entität suchen …"
                        .value=${form.switch_entity} .options=${entity(candidates.switches, (c) => c.state)}
                        @change=${(e) => set("switch_entity", e.detail.value)}></sf-picker>
                    </div>`
                  : nothing}
                ${form.type === "stepped"
                  ? html`<div class="field">
                      <span class="field-label">Leistungsstufe (Auswahl)</span>
                      <sf-picker label="Leistungsstufe" placeholder="z. B. Heizstab Heizleistung …"
                        .value=${form.level_entity} .options=${entity(candidates.selects, (c) => c.state)}
                        @change=${(e) => this._setStepEntity(e.detail.value)}></sf-picker>
                    </div>`
                  : nothing}
                ${form.type === "current"
                  ? html`<div class="field">
                      <span class="field-label">Ziel-Stromstärke (Zahl)</span>
                      <sf-picker label="Stromstärke" placeholder="number-Entität in A …"
                        .value=${form.current_entity} .options=${entity(candidates.numbers, unitDetail)}
                        @change=${(e) => set("current_entity", e.detail.value)}></sf-picker>
                    </div>`
                  : nothing}
                <div class="field">
                  <span class="field-label">Leistungsmessung (optional)</span>
                  <sf-picker label="Leistungsmessung" empty-label="keine" placeholder="Name oder Entität suchen …"
                    .value=${form.power_entity} .options=${entity(candidates.power, unitDetail)}
                    @change=${(e) => set("power_entity", e.detail.value)}></sf-picker>
                </div>
                <div class="field">
                  <span class="field-label">Verbrauchsmessung (optional)</span>
                  <sf-picker label="Verbrauchsmessung" empty-label="keine" placeholder="Energiezähler (kWh) …"
                    .value=${form.energy_entity} .options=${entity(candidates.energy, unitDetail)}
                    @change=${(e) => set("energy_entity", e.detail.value)}></sf-picker>
                </div>
                ${form.type !== "stepped"
                  ? html`<label class="field">
                      <span class="field-label">Leistung</span>
                      <span class="input-unit"><input inputmode="decimal" placeholder="500" .value=${form.max_power}
                        @input=${(e) => set("max_power", e.target.value)}><span class="muted">W</span></span>
                    </label>`
                  : nothing}
                ${form.kind === "heater"
                  ? html`
                      <div class="field">
                        <span class="field-label">Temperatur im Speicher</span>
                        <sf-picker label="Temperatur" placeholder="z. B. Speicher Temperatur oben …"
                          .value=${form.temperature_entity} .options=${entity(candidates.temperature, unitDetail)}
                          @change=${(e) => set("temperature_entity", e.detail.value)}></sf-picker>
                      </div>
                      <label class="field">
                        <span class="field-label">Speichervolumen</span>
                        <span class="input-unit"><input inputmode="decimal" placeholder="300" .value=${form.tank_l}
                          @input=${(e) => set("tank_l", e.target.value)}><span class="muted">l</span></span>
                      </label>`
                  : html`<label class="field">
                      <span class="field-label">Energiebedarf pro Tag</span>
                      <span class="input-unit"><input inputmode="decimal" placeholder="z. B. 0,5" .value=${form.daily_energy}
                        @input=${(e) => set("daily_energy", e.target.value)}><span class="muted">kWh</span></span>
                    </label>`}
              </div>
              ${form.type === "stepped" && Object.keys(form.steps).length
                ? html`<div class="field">
                    <span class="field-label">Leistung je Stufe${selected ? ` (${selected.name})` : ""}</span>
                    <div class="steps">
                      ${Object.entries(form.steps).map(([option, watts]) => html`<label class="step-power">
                        <span>${option}</span>
                        <span class="input-unit"><input inputmode="decimal" .value=${toDisplay(watts, "W")}
                          @input=${(e) => set("steps", { ...form.steps, [option]: fromDisplay(e.target.value, "W") })}><span class="muted">W</span></span>
                      </label>`)}
                    </div>
                    <span class="small muted">Aus den Namen der Optionen gelesen; 0 W ist „aus“. Sunflow wählt die größte Stufe, die in die geplante Leistung passt.</span>
                  </div>`
                : nothing}
              <div class="actions">
                <button class="ok" ?disabled=${!control || !form.name.trim()} @click=${() => this._addManual()}>
                  ${this._manualEditId ? "Speichern" : "Hinzufügen"}
                </button>
                <button class="secondary" @click=${() => this._closeManualForm()}>Abbrechen</button>
              </div>
            `}
      </section>
    `;
  }

  /** A select chosen for the power steps: its options with the watts read from their names. */
  _setStepEntity(entityId) {
    const options = (this._candidates?.selects ?? []).find((c) => c.entity_id === entityId)?.options ?? [];
    const steps = {};
    for (const option of options) {
      const match = option.match(/(\d+(?:[.,]\d+)?)\s*(kw|w)\b/i);
      if (["off", "aus", "0", "none", "stop"].includes(option.trim().toLowerCase())) steps[option] = 0;
      else if (match) steps[option] = Number(match[1].replace(",", ".")) * (match[2].toLowerCase() === "kw" ? 1000 : 1);
    }
    this._manual = { ...this._manual, level_entity: entityId, steps };
  }

  async _loadCandidates() {
    try {
      this._candidates = await api.candidates();
    } catch (err) {
      this._error = `Entitäten konnten nicht geladen werden: ${err.message}`;
    }
  }

  async _editManual(device) {
    if (this._candidates === null) await this._loadCandidates();
    const role = (name) => device.role_overrides[name]?.entity_id ?? "";
    this._manualEditId = device.id;
    this._manual = {
      ...EMPTY_MANUAL,
      name: device.name,
      type: device.profile.slice(MANUAL_PREFIX.length),
      kind: device.kind === "heater" ? "heater" : "load",
      switch_entity: role("switch"),
      level_entity: role("level"),
      current_entity: role("current"),
      power_entity: role("power"),
      energy_entity: role("energy"),
      temperature_entity: role("temperature"),
      steps: { ...(device.power_steps ?? {}) },
      max_power: toDisplay(device.parameters.max_power, "W"),
      daily_energy: toDisplay(device.parameters.daily_energy_wh, "Wh"),
      tank_l: device.parameters.tank_l ? String(device.parameters.tank_l) : "",
    };
  }

  _closeManualForm() {
    this._candidates = null;
    this._manualEditId = null;
    this._manual = { ...EMPTY_MANUAL };
  }

  async _addManual() {
    const form = this._manual;
    const power = form.max_power === "" ? null : fromDisplay(form.max_power, "W");
    const daily = fromDisplay(form.daily_energy, "kWh");
    const tank = form.tank_l === "" ? null : fromDisplay(form.tank_l, "");
    if (power === undefined || daily === undefined || tank === undefined || Object.values(form.steps).some((w) => w === undefined)) {
      this._error = "Bitte gültige Zahlen eingeben.";
      return;
    }
    const body = {
      name: form.name.trim(),
      type: form.type,
      kind: form.kind,
      switch_entity: form.switch_entity || null,
      level_entity: form.type === "stepped" ? form.level_entity || null : null,
      current_entity: form.type === "current" ? form.current_entity || null : null,
      power_entity: form.power_entity || null,
      energy_entity: form.energy_entity || null,
      temperature_entity: form.kind === "heater" ? form.temperature_entity || null : null,
      tank_l: form.kind === "heater" ? tank : null,
      max_power: form.type === "stepped" ? null : power ?? 500,
      daily_energy_wh: daily === null || form.kind === "heater" ? null : daily * 1000,
      power_steps: form.type === "stepped" && Object.keys(form.steps).length ? form.steps : null,
    };
    try {
      const device = this._manualEditId ? await api.updateManual(this._manualEditId, body) : await api.addManual(body);
      this._configDevices = { ...this._configDevices, [device.ha_device_id]: device };
      this._loadCategories();
      this._closeManualForm();
      this._error = null;
    } catch (err) {
      this._error = `Verbraucher konnte nicht gespeichert werden: ${err.message}`;
    }
  }

  _renderDevice(device) {
    const id = device.device_id;
    const preview = this._previews[id];
    const stored = this._configDevices[id];
    const configured = Boolean(stored);
    const disabled = Boolean(stored?.disabled);
    const editing = this._editing.has(id);
    const dirty = this._dirty.has(id);
    // Planning cannot do without these Kenndaten (others, e.g. a learned daily need, may stay empty)
    const missing = Boolean(stored) && (this._params[stored.id] ?? []).some(
      (p) => REQUIRED_PARAMS.has(p.name) && (p.effective === null || p.effective === undefined),
    );
    const factsChanged = Boolean(stored) && (Object.keys(this._paramEdits[stored.id] ?? {}).length > 0 || this._nameEdits[stored.id] !== undefined);
    // Needs attention: not taken over yet, values missing or being edited
    const open = this._expanded.has(id) ||
      (!disabled && (!configured || dirty || editing || missing || factsChanged || (preview && !preview.complete)));
    const head = html`
      <div class="device-head">
        <button class="device-toggle" aria-expanded=${open} @click=${() => this._toggleExpanded(id, open)}>
          <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
          <span>
            <span class="device-title">${device.name}</span>
            <span class="muted small device-sub">${[device.manufacturer, device.model].filter(Boolean).join(" ")} · über ${device.integration}${open
              ? ` · Profil ${device.profile}`
              : ""}</span>
          </span>
        </button>
        <span class="device-chips">
          ${disabled
            ? html`<span class="chip neutral">deaktiviert</span>`
            : !configured || dirty
              ? html`<span class="chip warn">noch nicht übernommen</span>`
              : missing
                ? html`<span class="chip warn">Angabe fehlt</span>`
                : html`<span class="chip ok">✓ übernommen</span>`}
          ${configured
            ? html`<label class="inline enable" title=${disabled ? "Gerät wieder verwenden" : "Gerät mit allen Einstellungen behalten, aber nicht verwenden"}>
                <input type="checkbox" .checked=${!disabled} ?disabled=${this._busy.has(id)}
                  @change=${(e) => this._setEnabled(device, e.target.checked, e.target)}>aktiv</label>`
            : nothing}
        </span>
      </div>`;
    if (!open) {
      return html`
        <section class="device compact ${disabled ? "off" : ""}">
          ${head}
          ${preview?.roles && !disabled ? html`<div class="summary small">${summaryValues(preview)}</div>` : nothing}
        </section>
      `;
    }
    return html`
      <section class="device ${disabled ? "off" : ""}">
        ${head}
        ${device.components.length
          ? html`<span class="chip neutral">${device.components.length} Komponenten</span>`
          : nothing}
        ${preview === undefined ? html`<p class="muted">Lade Live-Werte …</p>` : nothing}
        ${preview?.error ? html`<div class="alert error">${preview.error}</div>` : nothing}
        ${preview?.roles ? this._renderTiles(preview) : nothing}
        ${preview?.roles ? this._renderVehicleSoc(device, preview) : nothing}
        ${preview?.balance ? this._renderBalance(device.device_id) : nothing}
        ${editing && preview?.roles ? this._renderRoleEditor(device, preview) : nothing}
        ${!editing && this._advanced && preview?.roles ? this._renderRoleTable(preview) : nothing}
        ${this._advanced && device.components.length ? this._renderComponents(device) : nothing}
        <div class="actions">
          ${disabled
            ? html`<span class="small muted">Deaktiviert – zum Bearbeiten wieder auf „aktiv“ stellen.</span>`
            : configured && !dirty
            ? html`<span class="chip ok">✓ Übernommen</span>`
            : html`<button class="ok" ?disabled=${this._busy.has(id) || !preview?.complete}
                @click=${() => this._accept(device)}>
                ${configured ? "Anpassungen speichern" : dirty ? "Mit Anpassungen übernehmen" : "Werte passen"}
              </button>`}
          ${this._advanced || editing || dirty || !preview?.complete
            ? html`<button class="secondary" aria-expanded=${editing} @click=${() => this._toggleEdit(device)}>
                ${editing ? "Zuordnung schließen" : "Entitäten zuordnen"}
              </button>`
            : nothing}
        </div>
        ${stored && !disabled ? this._renderDeviceFacts(stored) : nothing}
      </section>
    `;
  }

  /** A charger that does not know the car: its SoC comes from another integration (e.g. MQTT). */
  _renderVehicleSoc(device, preview) {
    const role = preview.roles.find((r) => r.role === "vehicle_soc");
    const chosen = this._overrides[device.device_id]?.vehicle_soc?.entity_id ?? "";
    // evcc knows the vehicle; only chargers without an own SoC entity get the choice
    if (!role || (role.entity_id && !role.missing && !chosen)) return nothing;
    if (this._candidates === null && !this._loadingCandidates) {
      this._loadingCandidates = true;
      this._loadCandidates().finally(() => (this._loadingCandidates = false));
    }
    return html`
      <div class="field vehicle-soc">
        <span class="field-label">Ladestand des Fahrzeugs</span>
        <sf-picker label="Ladestand des Fahrzeugs" empty-label="keiner" placeholder="z. B. MQTT-Sensor des Autos suchen …"
          .value=${chosen}
          .options=${(this._candidates?.percent ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} %` }))}
          @change=${(e) => this._setOverride(device.device_id, "vehicle_soc", "entity_id", e.detail.value)}></sf-picker>
        <span class="small muted">Die Wallbox kennt den Ladestand nicht. Ohne ihn plant Sunflow das Laden nicht ein; danach
          „Anpassungen speichern“.</span>
      </div>
    `;
  }

  async _setKind(device, kind) {
    try {
      await api.setDeviceKind(device.id, kind);
      this._configDevices = { ...this._configDevices, [device.ha_device_id]: { ...device, kind } };
      await Promise.all([this._loadCategories(), this._loadParameters()]);
    } catch (err) {
      this._error = `Art nicht gespeichert: ${err.message}`;
    }
  }

  _toggleExpanded(id, open) {
    const expanded = new Set(this._expanded);
    if (open) expanded.delete(id);
    else expanded.add(id);
    this._expanded = expanded;
  }

  async _setEnabled(device, enabled, input) {
    const stored = this._configDevices[device.device_id];
    if (!enabled && !(await confirmDialog({
      title: `„${device.name}“ deaktivieren?`,
      text: "Die Einstellungen bleiben erhalten; Sunflow plant, misst und steuert das Gerät nicht mehr, bis es wieder aktiv ist.",
      confirm: "Deaktivieren",
    }))) {
      input.checked = true;
      return;
    }
    this._busy = new Set([...this._busy, device.device_id]);
    try {
      await api.setDeviceEnabled(stored.id, enabled);
      this._configDevices = configuredDevices(await api.config());
      this._loadCategories();
      this._error = null;
    } catch (err) {
      this._error = `Gerät nicht umgeschaltet: ${err.message}`;
      input.checked = !enabled;
    } finally {
      const busy = new Set(this._busy);
      busy.delete(device.device_id);
      this._busy = busy;
    }
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
        ${latest
          ? html`<div class="small">
          Letzte Messung: ${BALANCE_FORMULAS[latest.method]} = ${formatValue(latest.computed_load, "W")},
          Hausverbrauch laut Gerät ${formatValue(latest.reported_load, "W")}
          (Abweichung ${formatValue(Math.abs(latest.deviation), "W")})
        </div>`
          : nothing}
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
                      <sf-picker inline label="Entität für ${roleLabel(r.role)}"
                        empty-label="Profil: ${r.profile_entity_id ?? "nicht gefunden"}"
                        .value=${override.entity_id ?? ""}
                        .options=${sensors.map((e) => ({ value: e.entity_id, label: e.name, detail: `${e.state ?? "–"} ${e.unit ?? ""}` }))}
                        @change=${(e) => this._setOverride(id, r.role, "entity_id", e.detail.value)}></sf-picker>
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

  /** Kenndaten of a configured device: what it is. Planning and earnings have their own areas. */
  _renderDeviceFacts(device) {
    const params = (this._params[device.id] ?? []).filter((p) => this._advanced || !p.advanced);
    const capabilities = this._capabilitiesOf(device);
    const edits = this._paramEdits[device.id] ?? {};
    const nameEdit = this._nameEdits[device.id];
    const changed = Object.keys(edits).length > 0 || nameEdit !== undefined;
    return html`
      <div class="facts">
        <div class="field-label">Kenndaten</div>
        <div class="fields">
          <label class="field">
            <span class="field-label">Bezeichnung</span>
            <span class="input-unit">
              <input .value=${nameEdit ?? device.name}
                @input=${(e) => (this._nameEdits = { ...this._nameEdits, [device.id]: e.target.value })}>
            </span>
          </label>
          ${capabilities.includes("loadpoint") && device.profile.startsWith("evcc_intg/")
            ? html`<label class="field">
                <span class="field-label">Art des Ladepunkts</span>
                <select @change=${(e) => this._setKind(device, e.target.value || null)}>
                  <option value="" ?selected=${!device.kind}>automatisch (aus der Einheit des Ladestands)</option>
                  <option value="vehicle" ?selected=${device.kind === "vehicle"}>Wallbox (Fahrzeug)</option>
                  <option value="heater" ?selected=${device.kind === "heater"}>Wärme (z. B. Heizstab im Warmwasserspeicher)</option>
                </select>
              </label>`
            : nothing}
          ${params.map((p) => this._renderParameterField(device, p, edits))}
        </div>
        ${this._params[device.id] === undefined ? html`<p class="muted small">Lade Kenndaten …</p>` : nothing}
        <div class="actions">
          <button class="ok" ?disabled=${!changed || this._busy.has(device.id)} @click=${() => this._saveDevice(device)}>
            Kenndaten speichern
          </button>
          <span class="spacer"></span>
          <button class="secondary danger" ?disabled=${this._busy.has(device.id)} @click=${() => this._removeDevice(device)}>
            Entfernen
          </button>
        </div>
      </div>
    `;
  }

  /** How each consumer is planned (strategy, target, ready by, alternative heat). */
  _renderConsumers() {
    const consumers = this._activeDevices.filter((d) => {
      const caps = this._capabilitiesOf(d);
      return caps.includes("loadpoint") || caps.includes("load");
    });
    return html`
      <div class="heading">
        <h1>Verbraucher</h1>
        <p class="muted">Wie Sunflow Warmwasser, eigene Verbraucher und Wallboxen einplant. Fahrzeuge an Wallboxen haben ihre
          eigenen Vorgaben unter „Laden“; die hier gelten für eine Wallbox ohne zugeordnete Fahrzeuge.</p>
      </div>
      ${consumers.length === 0 ? html`<p class="muted">Keine steuerbaren Verbraucher eingerichtet.</p>` : nothing}
      ${CATEGORIES.map(([key, title]) => {
        const members = consumers.filter((d) => this._configuredCategory(d) === key);
        return members.length
          ? html`<h2 class="category">${title}</h2>${members.map((d) => html`
              <section class="device">
                <div class="device-head"><div class="device-title">${d.name}</div>
                  ${this._flexEdits[d.id] !== undefined ? html`<span class="chip warn">nicht gespeichert</span>` : nothing}</div>
                ${this._renderFlexSettings(d, this._params[d.id] ?? [])}
                <div class="actions">
                  <button class="ok" ?disabled=${this._flexEdits[d.id] === undefined || this._busy.has(d.id)}
                    @click=${() => this._saveDevice(d)}>Speichern</button>
                </div>
              </section>`)}`
          : nothing;
      })}
    `;
  }

  /** What the feed-in of each PV plant earns (part of the tariff, not of the device). */
  _renderFeedIns() {
    const plants = this._activeDevices.filter((d) => this._capabilitiesOf(d).includes("pv"));
    if (!plants.length) return nothing;
    return html`
      <section class="device">
        <div class="device-title">Einspeisevergütung</div>
        ${plants.map((device) => {
          const feedEdit = this._feedEdits[device.id] ?? {};
          return html`
            <div class="fields">
              <div class="field">
                <span class="field-label">${device.name}</span>
                <select aria-label="Vergütung ${device.name}" @change=${(e) => this._editFeedIn(device.id, "model", e.target.value)}>
                  ${Object.entries(FEED_IN_MODELS).map(
                    ([value, label]) => html`<option value=${value}
                      ?selected=${(feedEdit.model ?? device.feed_in?.model ?? "eeg_fixed") === value}>${label}</option>`,
                  )}
                </select>
              </div>
              <label class="field">
                <span class="field-label">Vergütung</span>
                <span class="input-unit">
                  <input inputmode="decimal" placeholder="z. B. 8,2"
                    .value=${feedEdit.rate ?? toDisplay(device.feed_in?.rate_ct_per_kwh, "ct")}
                    @input=${(e) => this._editFeedIn(device.id, "rate", e.target.value)}>
                  <span class="muted">ct/kWh</span>
                </span>
              </label>
              <div class="field">
                <span class="field-label">&nbsp;</span>
                <button class="ok" ?disabled=${this._feedEdits[device.id] === undefined || this._busy.has(device.id)}
                  @click=${() => this._saveDevice(device)}>Speichern</button>
              </div>
            </div>`;
        })}
      </section>
    `;
  }

  _renderFlexSettings(device, params) {
    const names = new Set(params.map((p) => p.name));
    const kind = names.has("tank_l") ? "heater" : names.has("vehicle_capacity_wh") ? "vehicle" : "load";
    const flex = { ...device.flex, ...(this._flexEdits[device.id] ?? {}) };
    const set = (field, value) =>
      (this._flexEdits = { ...this._flexEdits, [device.id]: { ...(this._flexEdits[device.id] ?? {}), [field]: value } });
    const strategies = {
      cost: ["Kostenoptimal", "PV-Überschuss oder günstige Zeiten"],
      pv_only: ["Nur PV-Überschuss", "nie aus dem Netz oder dem Hausspeicher"],
      fast: ["Schnellstmöglich", "sofort mit voller Leistung"],
    };
    return html`
      <fieldset class="flex-settings">
        <legend class="field-label">Planung</legend>
        <div class="strategy-options">
          ${Object.entries(strategies).map(
            ([value, [label, hint]]) => html`
              <label class="option ${flex.strategy === value ? "selected" : ""}">
                <input type="radio" name="strategy-${device.id}" .checked=${flex.strategy === value}
                  @change=${() => set("strategy", value)}>
                <span><strong>${label}</strong><br><span class="small muted">${hint}</span></span>
              </label>
            `,
          )}
        </div>
        <div class="fields">
          ${kind !== "load"
            ? html`<label class="field">
                <span class="field-label">${kind === "heater" ? "Zieltemperatur" : "Ziel-Ladestand"}</span>
                <span class="input-unit">
                  <input inputmode="decimal" placeholder="aus evcc" .value=${flex.target === null || flex.target === undefined ? "" : toDisplay(flex.target, "")}
                    @input=${(e) => set("target", fromDisplay(e.target.value, ""))}>
                  <span class="muted">${kind === "heater" ? "°C" : "%"}</span>
                </span>
                <span class="small muted">Leer = Wert aus evcc. Ein aktiver evcc-Ladeplan hat Vorrang.</span>
              </label>`
            : nothing}
          <label class="field">
            <span class="field-label">Fertig bis (täglich)</span>
            <span class="input-unit">
              <input type="time" .value=${flex.ready_by ?? ""} @change=${(e) => set("ready_by", e.target.value || null)}>
            </span>
            <span class="small muted">${kind === "vehicle" ? "Leer = bis zum Ende des Planungszeitraums." : "Leer = innerhalb von 24 h."}</span>
          </label>
          ${kind === "heater"
            ? html`<label class="field">
                <span class="field-label">Alternative Wärmequelle (Fernwärme, Gas …)</span>
                <span class="input-unit">
                  <input inputmode="decimal" placeholder="keine"
                    .value=${flex.alternative_cost_ct === null || flex.alternative_cost_ct === undefined ? "" : toDisplay(flex.alternative_cost_ct, "ct")}
                    @input=${(e) => set("alternative_cost_ct", fromDisplay(e.target.value, "ct"))}>
                  <span class="muted">ct/kWh Wärme</span>
                </span>
                <span class="small muted">Elektrisch geheizt wird nur, wenn es günstiger ist; den Rest übernimmt die Alternative.</span>
              </label>`
            : nothing}
          <label class="field">
            <span class="field-label">Strom aus dem Hausspeicher</span>
            <span class="inline">
              <input type="checkbox" .checked=${flex.uses_battery} @change=${(e) => set("uses_battery", e.target.checked)}>
              Hausspeicher darf in diesen Verbraucher entladen
            </span>
            <span class="small muted">Aus: der Speicher versorgt nur das Haus; ${kind === "heater" ? "geheizt" : "geladen"} wird
              dann nur aus PV-Überschuss oder dem Netz. Gleichzeitiges Laden des Hausspeichers ist immer erlaubt.</span>
          </label>
        </div>
      </fieldset>
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
        ${paramHint(param.name) ? html`<span class="small muted">${paramHint(param.name)}</span>` : nothing}
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
    // The parameter is the round trip of the conversion; a constant standby draw does not
    // depend on how much is cycled and is left out when the history allows the split
    const split = estimate.conversion_round_trip !== null && estimate.conversion_round_trip !== undefined;
    const value = Math.round((split ? estimate.conversion_round_trip : estimate.round_trip) * 1000) / 10;
    return html`
      ${split
        ? html`<span class="small">
            Aufgeteilt: Umwandlung <strong>${pct(estimate.conversion_round_trip)}</strong> Zyklus und Eigenverbrauch der
            Batterie etwa <strong>${formatValue(estimate.standby_w, "W")}</strong> (unabhängig vom Laden) –
            für die Planung zählt die Umwandlung. Die Wandlung im Wechselrichter ist ein eigener Wert.
          </span>`
        : nothing}
      <span class="small">
        ${estimate.source === "counters" ? "aus Energiezählern" : "aus Historie"}: <strong>${pct(estimate.round_trip)}</strong> Zyklus (je Richtung ${pct(estimate.efficiency)}),
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
          ? html`<span class="small muted">Vorläufig – belastbar erst ab ${estimate.min_cycles} vollen Zyklen. Enthält auch den Eigenverbrauch des Speichers.</span>`
          : html`<span class="small muted">Enthält auch den Eigenverbrauch des Speichers (z. B. DC/DC-Wandler der Module).</span>`}
    `;
  }

  _renderSite() {
    const site = this._site;
    const devices = this._activeDevices;
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
                  <div class="field wide">
                    <span class="field-label">${d.name} wird zusätzlich gemessen von</span>
                    <select aria-label="${d.name} wird zusätzlich gemessen von" @change=${(e) => this._setMeasuredBy(d, e.target.value)}>
                      <option value="" ?selected=${!d.measured_by}>niemandem (eigene Einspeisung)</option>
                      ${measuring.map(
                        (m) => html`<option value=${m.id} ?selected=${d.measured_by?.device_id === m.id}>
                          ${m.name} als „PV extern“
                        </option>`,
                      )}
                    </select>
                  </div>
                `,
              )}
            </section>
          `
        : nothing}
      ${this._renderMeasurements(byId)}
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
    return html`${this._renderSourcesBody()}${this._renderFeedIns()}`;
  }

  _renderSourcesBody() {
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
              <div class="field wide">
                <span class="field-label">Quelle</span>
                <select aria-label="Quelle Bezugspreis" @change=${(e) => this._setTariff(e.target.value)}>
                  <option value="" ?selected=${!selected}>– keine (nur Eigenverbrauch) –</option>
                  <option value=${FIXED_PRICE} ?selected=${selected === FIXED_PRICE}>Fester Preis …</option>
                  ${sources.prices.map(
                    (p) => html`<option value=${p.entity_id} ?selected=${selected === p.entity_id}>
                      ${p.name} · ${PRICE_ADAPTERS[p.adapter] ?? p.adapter}
                      ${p.current !== null ? ` · ${formatCt(p.current)}` : ""}
                    </option>`,
                  )}
                </select>
              </div>
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
    const devices = this._activeDevices;
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
                      <span class="field-label">${FORECAST_SOURCES[plane.source] ?? plane.source} · ${plane.name}${plane.capacity_kw ? ` (${formatValue(plane.capacity_kw * 1000, "W")})` : ""}</span>
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
                      ${this._renderPlaneStrings(plane, sources.pv_strings ?? [])}
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
            ? "Sunflow ist eingerichtet und plant im Hintergrund. Ergebnisse siehst du unter Live, Steuerung und Planung."
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
                    <button class="link" @click=${() => this._goTo(i.step)}>zu „${STEPS[stepIndex(i.step)][1]}“</button>
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
    if (p.min_soc !== undefined) details.push(`min. ${formatValue(p.min_soc, "%")}`);
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
    h1 { margin: 0; font-size: 20px; line-height: 1.25; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }

    /* The main menu stays in view while scrolling */
    header {
      position: sticky; top: 0; z-index: 10;
      background: var(--sf-surface); border-bottom: 1px solid var(--sf-border);
      /* Menu centred, the brand (outside Home Assistant) on the left */
      padding: 8px 16px; display: grid; grid-template-columns: 1fr auto 1fr; gap: 8px 12px; align-items: center;
    }
    .brand { grid-column: 1; }
    .tabs { grid-column: 2; }
    .brand { display: flex; align-items: center; gap: 12px; color: var(--sf-primary); }
    .brand .title { font-weight: 700; font-size: 17px; color: var(--sf-text); }
    .brand .subtitle { font-size: 13px; color: var(--sf-muted); }

    .tabs {
      display: flex; gap: 4px; background: var(--sf-subtle); padding: 4px; border-radius: 10px;
      max-width: 100%; overflow-x: auto;
    }
    .tab {
      flex: none; display: flex; align-items: center; gap: 6px;
      min-height: 32px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent;
      cursor: pointer; font-size: 14px; color: var(--sf-muted);
    }
    .tab { position: relative; }
    .badge {
      min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; font-size: 11px; font-weight: 700; line-height: 18px;
      background: var(--sf-warn-text); color: var(--sf-surface); text-align: center;
    }
    .tab-icon { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .tab:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
    .tab.active { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.08); }
    .switch {
      display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 0 10px; flex: none;
      border: 0; background: transparent; cursor: pointer; font-size: 13px; color: var(--sf-muted); white-space: nowrap;
    }
    .track { position: relative; width: 36px; height: 20px; border-radius: 10px; background: var(--sf-muted); transition: background .15s; }
    .track.on { background: var(--sf-primary); }
    .knob { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 8px; background: #fff; transition: left .15s; }
    .track.on .knob { left: 18px; }

    .layout {
      max-width: 1240px; margin: 0 auto; padding: 16px;
      display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start;
    }
    /* Fixed width: long step names must not squeeze the switch below */
    .sidebar { flex: 0 0 240px; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
    .sidebar nav {
      background: var(--sf-surface);
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
    .step.active .dot { background: var(--sf-primary); color: var(--sf-on-primary); }
    .dot.done { background: var(--sf-ok); color: var(--sf-on-ok); }

    /* No frame around the content: on small screens every pixel of width counts */
    main { flex: 999 1 560px; min-width: 0; display: flex; flex-direction: column; gap: 16px; }
    @media (max-width: 700px) {
      /* Without the own brand (inside Home Assistant) the header is only the bottom bar */
      header { position: static; display: flex; padding: 4px 8px; border-bottom: 0; }
      header.bare { padding: 0; }
      .brand .subtitle { display: none; }
      /* Phones: icon bar fixed at the bottom, within thumb reach */
      .tabs {
        position: fixed; left: 0; right: 0; bottom: 0; z-index: 10; border-radius: 0; gap: 0;
        justify-content: space-around; padding: 4px 4px calc(4px + env(safe-area-inset-bottom));
        background: var(--sf-surface); border-top: 1px solid var(--sf-border); box-shadow: 0 -2px 8px rgb(0 0 0 / 0.08);
      }
      .tab { flex: 1; justify-content: center; min-height: 48px; padding: 0; }
      .tab-icon { width: 24px; height: 24px; }
      .tab-label { display: none; }
      .badge { position: absolute; top: 4px; left: calc(50% + 6px); }
      .tab.active { box-shadow: none; background: var(--sf-primary-bg); color: var(--sf-primary-text); }
      /* Room for the bottom bar */
      :host { padding-bottom: calc(64px + env(safe-area-inset-bottom)); }
      .layout { padding: 6px 8px; gap: 8px; }
      .restore-banner { margin: 8px 8px 0; }
      /* Steps as a compact row; only the current one shows its name */
      .sidebar { flex: 0 0 100%; }
      .sidebar nav { display: flex; align-items: center; gap: 4px; overflow-x: auto; min-width: 0; padding: 4px; }
      .step { width: auto; flex: none; min-height: 40px; padding: 4px 6px; }
      .step:not(.active) span:last-child { display: none; }
      /* Own row below the steps, always visible */
      .switch { align-self: flex-end; padding: 0 4px; }
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
    .check.on { background: var(--sf-primary); border-color: var(--sf-primary); color: var(--sf-on-primary); }

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
    .device-title { font-weight: 600; font-size: 16px; display: block; }
    .facts { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--sf-border); padding-top: 14px; }
    .device-sub { display: block; }
    .device-toggle { display: flex; gap: 8px; align-items: flex-start; border: 0; background: none; padding: 0; cursor: pointer; text-align: left; }
    .caret { width: 14px; color: var(--sf-muted); line-height: 22px; }
    .device-chips { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .device.compact { padding: 12px 18px; gap: 6px; }
    .device.off { opacity: .65; }
    .summary { color: var(--sf-muted); padding-left: 22px; }
    .category {
      font-size: 16px; margin: 22px 0 2px; padding-bottom: 6px; font-weight: 600; color: var(--sf-text);
      border-bottom: 2px solid var(--sf-border);
    }
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
    .vehicle-soc { margin: 10px 0; max-width: 520px; }
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
    .flex-settings { border: 1px solid var(--sf-border); border-radius: 8px; padding: 12px 14px; margin: 0; display: flex; flex-direction: column; gap: 12px; }
    .flex-settings legend { padding: 0 6px; }
    .strategy-options { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
    .option {
      display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--sf-border);
      border-radius: 8px; cursor: pointer; font-size: 14px;
    }
    .option.selected { border-color: var(--sf-primary); background: var(--sf-primary-bg); }
    .option input { margin-top: 3px; width: 18px; height: 18px; }

    .plan { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; }
    .confirm { white-space: normal; font-size: 14px; }
    .phases { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
    .phases li { padding: 4px 10px; border-radius: 12px; background: var(--sf-neutral-bg); color: var(--sf-neutral-text); }
    .phases li.active { background: var(--sf-primary); color: var(--sf-on-primary); }
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
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .ok { border: 0; background: var(--sf-ok); color: var(--sf-on-ok); }
    .danger { color: var(--sf-error-text); }
    .steps { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
    .step-power { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
    .plane-strings { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; }
    .manual-item { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: center; }
    .item-actions { display: flex; gap: 8px; }
    .restore-banner {
      display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between;
      margin: 16px max(16px, calc((100% - 1240px) / 2)) 0; padding: 12px 16px; border-radius: 10px;
      background: var(--sf-warn-bg); color: var(--sf-warn-text); font-size: 14px;
    }
    .restore-banner button { min-height: 40px; padding: 0 16px; border-radius: 8px; cursor: pointer; font: inherit; }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sunflow-app", SunflowApp);
