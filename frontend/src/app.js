import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./lib/api.js";
import { confirmDialog } from "./components/dialog.js";
import { poll } from "./lib/poll.js";
import "./components/picker.js";
import { withSuggestedName } from "./lib/names.js";
import { findLabel, searchSettings } from "./lib/settings-search.js";
import "./views/live-view.js";
import "./views/plan-view.js";
import "./views/control-view.js";
import "./views/analysis-view.js";
import "./settings/standards-view.js";
import "./settings/vehicles-view.js";
import "./settings/charging-view.js";
import "./settings/batteries-view.js";
import "./settings/optimizers-view.js";
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
import { decimal, locale, t, tn } from "./lib/i18n.js";
import { isOpen, setOpen } from "./lib/collapse.js";

/** What the MCP server may read; matches the areas in the backend. */
const MCP_AREAS = [
  ["live", t("Live, Plan, Preise und Prognosen")],
  ["history", t("Verlauf, Aufzeichnung und Auswertung (mit SQL)")],
  ["config", t("Konfiguration und Geräte")],
  ["control", t("Steuerung und Steuerungsprotokoll")],
  ["log", t("Fehler und Warnungen der App")],
];

// Main menu: view, label, icon (24 px line icon)
const VIEWS = [
  ["live", t("Live"), "M3 12h4l3-8 4 16 3-8h4"],
  ["control", t("Steuerung"), "M8 6h8a6 6 0 0 1 0 12H8A6 6 0 0 1 8 6zM16 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  ["plan", t("Planung"), "M3 3v18h18M7 15l4-4 3 3 5-6"],
  ["analysis", t("Analyse"), "M5 20V11M11 20V5M17 20v-7M3 20h18"],
  ["setup", t("Einstellungen"), "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M16 4v4M10 10v4M18 16v4"],
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
    flex: config.planning?.[d.id] ?? { strategy: "cost", target: null, target_max: null, ready_by: null, uses_battery: true, alternative_cost_ct: null },
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
  ["integrations", t("Integrationen")],
  ["devices", t("Geräte")],
  ["test", t("Gerätetest")],
  ["site", t("Bilanzprüfung")],
  ["optimizers", t("Optimierer")],
  ["vehicles", t("Fahrzeuge")],
  ["charging", t("Laden")],
  ["consumers", t("Verbraucher")],
  ["sources", t("Tarif & Prognose")],
  ["standards", t("Planung")],
  ["display", t("Anzeige")],
  ["summary", t("Übersicht")],
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
const MANUAL_TYPES = {
  switch: t("Schalter (ein/aus)"),
  stepped: t("Leistungsstufen (Auswahl)"),
  current: t("Stromstärke (Zahl)"),
  meter: t("nicht steuerbar – nur messen"),
};
// Capabilities of the hand-made profiles (they are not part of discovery): a meter is only measured
const MANUAL_CAPABILITIES = { meter: ["appliance"] };
const EMPTY_DIRECT = { profile: "", name: "", host: "", port: "", unit_id: "" };

const EMPTY_MANUAL = {
  name: "", type: "switch", kind: "load", switch_entity: "", level_entity: "", current_entity: "", power_entity: "",
  energy_entity: "", temperature_entity: "", steps: {}, max_power: "", daily_energy: "", tank_l: "",
};
const FORECAST_SOURCES = { solcast: "Solcast", forecast_solar: "Forecast.Solar" };

const PRICE_ADAPTERS = {
  timeslots: t("Zeittarif"),
  tibber: "Tibber",
  nordpool: t("Börsenpreis (Nord Pool)"),
  nordpool_core: t("Börsenpreis netto (Nord Pool)"),
  epex_spot: t("dynamischer Preis (EPEX Spot)"),
  epex_market: t("Börsenpreis netto (EPEX Spot)"),
  entsoe: t("Börsenpreis (ENTSO-e)"),
  fixed: t("nur aktueller Preis"),
};
const weekdayTimeFormat = new Intl.DateTimeFormat(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
const timeOnlyFormat = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });

function formatCt(eurPerKwh) {
  return `${decimal((eurPerKwh * 100).toFixed(1))} ct`;
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
  ac: t("Netz + WR-Ausgang (AC) + PV extern"),
  dc: t("Netz + PV + PV extern − Batterie"),
};

class SunflowApp extends LitElement {
  static properties = {
    _step: { state: true },
    _navMore: { state: true },
    _setupCompleted: { state: true },
    _advanced: { state: true },
    _searchQuery: { state: true },
    _searchActive: { state: true },
    _integrations: { state: true },
    _selected: { state: true },
    _previews: { state: true },
    _configDevices: { state: true },
    _editing: { state: true },
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
    _link: { state: true },
    // Integrations not used by Sunflow are hidden until asked for
    _showUnusedIntegrations: { state: true },
    _mcp: { state: true },
    _integrationSettings: { state: true },
    _display: { state: true },
    _manualEditId: { state: true },
    _tests: { state: true },
    _testConfirm: { state: true },
    _fixedPriceMode: { state: true },
    _fixedPriceEdit: { state: true },
    _surchargeEdit: { state: true },
    _busy: { state: true },
    _reloads: { state: true },
    _chargers: { state: true },
    _chargerEdits: { state: true },
    _error: { state: true },
    _haConnected: { state: true },
  };

  constructor() {
    super();
    this._step = 0;
    this._advanced = readSetting(ADVANCED_KEY) === "1";
    this._searchQuery = "";
    this._searchActive = 0;
    this._integrations = null;
    this._selected = new Set();
    this._showUnusedIntegrations = false;
    // Chosen or unchosen in this visit: stays visible while the user decides
    this._touchedIntegrations = new Set();
    this._previews = {};
    // ha_device_id -> stored device config
    this._configDevices = {};
    this._editing = new Set();
    // Device cards opened by the user; configured and complete ones start compact
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
    this._chargerEdits = {};
    this._manual = { ...EMPTY_MANUAL };
    this._direct = null;
    this._directProfiles = null;
    this._link = null;
    // Undefined while loading, null until first shown
    this._mcp = null;
    this._integrationSettings = null;
    this._display = null;
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
    // Areas hidden left/right of the sideways scrolling row (phones)
    this._navMore = { left: false, right: false };
    this._onResize = () => this._updateNavMore();
  }

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener("resize", this._onResize);
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
    window.removeEventListener("resize", this._onResize);
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
      this._error = t("Daten konnten nicht geladen werden: {error}", { error: err.message });
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
    if (!(await confirmDialog({
      title: t("Wechselrichter zurücksetzen"),
      text: pending?.previous_mode
        ? t("Sunflow stellt jetzt am Wechselrichter den Eigenverbrauch und den Betriebsmodus „{mode}“ wieder her.", { mode: pending.previous_mode })
        : t("Sunflow stellt jetzt am Wechselrichter den Eigenverbrauch wieder her."),
      confirm: t("Zurücksetzen"),
    }))) return;
    this._restoring = true;
    try {
      const result = await api.restoreAfterTest();
      this._error = result.restored ? null : t("Zurücksetzen nicht vollständig – bitte am Wechselrichter prüfen.");
      await this._loadPendingRestore();
    } catch (err) {
      this._error = t("Zurücksetzen fehlgeschlagen: {error}", { error: err.message });
    } finally {
      this._restoring = false;
    }
  }

  _renderPendingRestore() {
    const pending = this._pendingRestore;
    if (!pending) return nothing;
    const device = pending.device_name ?? t("Der Wechselrichter");
    return html`
      <div class="restore-banner" role="alert">
        <div>
          <strong>${pending.started
            ? t("Gerätetest wurde unterbrochen (seit {time}).", { time: new Date(pending.started).toLocaleString(locale()) })
            : t("Gerätetest wurde unterbrochen.")}</strong>
          ${pending.previous_mode
            ? t("{device} steht möglicherweise noch im Testzustand (z. B. Laden aus dem Netz oder begrenzte Ladung), Betriebsmodus vorher „{mode}“.", { device, mode: pending.previous_mode })
            : t("{device} steht möglicherweise noch im Testzustand (z. B. Laden aus dem Netz oder begrenzte Ladung).", { device })}
        </div>
        <button class="primary" ?disabled=${this._restoring} @click=${() => this._restoreAfterTest()}>
          ${this._restoring ? t("Stelle wieder her …") : t("Selbstverbrauch wiederherstellen")}
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
        integrations
          .filter((i) => i.supported && (i.devices.some((d) => d.profile) || i.optimizers_used))
          .map((i) => i.entry_id),
      );
    } catch (err) {
      this._discoveryError = t("Home Assistant nicht erreichbar: {error}", { error: err.message });
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
    // On phones the areas scroll sideways: keep the chosen one in sight
    this.updateComplete.then(() =>
      this.renderRoot.querySelector(".step.active")?.scrollIntoView({ block: "nearest", inline: "nearest" }),
    );
    const area = STEPS[step][0];
    if (area === "devices") {
      this._startPreview();
      this._loadParameters();
      api.integrationReloads().then((r) => (this._reloads = r.devices)).catch(() => (this._reloads = {}));
      api.chargers().then((r) => (this._chargers = r.devices)).catch(() => (this._chargers = {}));
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
      this._error = t("Test konnte nicht gestartet werden: {error}", { error: err.message });
    }
  }

  async _abortTest(device) {
    try {
      this._tests = { ...this._tests, [device.id]: await api.abortTest(device.id) };
    } catch (err) {
      this._error = t("Abbruch fehlgeschlagen: {error}", { error: err.message });
    }
  }

  async _loadSummary() {
    try {
      this._summary = await api.summary();
      this._error = null;
    } catch (err) {
      this._error = t("Zusammenfassung konnte nicht geladen werden: {error}", { error: err.message });
    }
  }

  async _completeSetup() {
    try {
      this._summary = await api.completeSetup();
      this._error = null;
    } catch (err) {
      this._error = t("Einrichtung konnte nicht abgeschlossen werden: {error}", { error: err.message });
    }
  }

  async _loadSources() {
    try {
      this._sources = await api.sources();
      this._error = null;
    } catch (err) {
      this._error = t("Quellen konnten nicht geladen werden: {error}", { error: err.message });
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
      this._error = t("Preisverlauf konnte nicht geladen werden: {error}", { error: err.message });
    }
  }

  async _loadForecastSeries() {
    try {
      this._forecastSeries = await api.forecastSeries();
    } catch (err) {
      this._error = t("PV-Prognose konnte nicht geladen werden: {error}", { error: err.message });
    }
  }

  async _setTariff(entityId) {
    if (entityId === FIXED_PRICE) {
      this._fixedPriceMode = true;
      return;
    }
    this._fixedPriceMode = false;
    await this._saveTariff({ ...this._sources.tariff, import_entity_id: entityId || null, fixed_price_ct: null });
  }

  async _saveFixedPrice() {
    const value = fromDisplay(this._fixedPriceEdit ?? "", "ct");
    if (value === undefined || value === null || value < 0) {
      this._error = t("Bitte einen gültigen Preis in ct/kWh eingeben.");
      return;
    }
    // Keep the typed price when saving fails, so it is neither lost nor skipped by "Weiter"
    if (await this._saveTariff({ ...this._sources.tariff, import_entity_id: null, fixed_price_ct: value })) this._fixedPriceEdit = undefined;
  }

  async _saveSurcharges() {
    const edit = this._surchargeEdit ?? {};
    const value = (key, unit) => (edit[key] === undefined ? this._sources.tariff[key] : fromDisplay(edit[key], unit));
    const surcharge = value("exchange_surcharge_ct", "ct");
    const vat = value("exchange_vat_pct", "%");
    if (surcharge === undefined || vat === undefined || (vat !== null && (vat < 0 || vat > 50))) {
      this._error = t("Bitte Aufschlag in ct/kWh und Mehrwertsteuer in % (0–50) eingeben.");
      return;
    }
    if (await this._saveTariff({ ...this._sources.tariff, exchange_surcharge_ct: surcharge, exchange_vat_pct: vat })) {
      this._surchargeEdit = undefined;
      await this._loadPriceSeries();
    }
  }

  async _saveTariff(body) {
    try {
      const tariff = await api.setTariff(body);
      this._sources = { ...this._sources, tariff };
      await this._loadPriceSeries();
      return true;
    } catch (err) {
      this._error = t("Preisquelle konnte nicht gespeichert werden: {error}", { error: err.message });
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
      <span class="muted">${t("Gemessen von:")}</span>
      ${own.map(
        (s) => html`<label class="inline"><input type="checkbox" .checked=${chosen.has(s.id)}
          @change=${(e) => toggle(s.id, e.target.checked)}> ${s.name}</label>`,
      )}
      <span class="muted">${t("– dann lernt Sunflow diese Fläche einzeln (z. B. Verschattung).")}</span>
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
      this._error = t("Prognoseflächen konnten nicht gespeichert werden: {error}", { error: err.message });
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
      this._error = t("Messquellen konnten nicht geladen werden: {error}", { error: err.message });
    }
  }

  async _setMeasurement(quantity, deviceId) {
    try {
      this._measurements = await api.setMeasurements({ [quantity]: deviceId || null });
      this._error = null;
    } catch (err) {
      this._error = t("Messquelle konnte nicht gespeichert werden: {error}", { error: err.message });
    }
  }

  _renderMeasurements(byId) {
    const entries = Object.entries(this._measurements ?? {}).filter(([, m]) => m.options.length);
    if (!entries.length) return nothing;
    return html`
      <section class="device">
        <div class="device-title">${t("Messquellen")}</div>
        <p class="small muted">${t("Welches Gerät liefert Leistung und Zählerstände? Gilt für Live, Verlauf, Energiebilanz und Schattenbetrieb. „Automatisch“ nimmt einen eigenen Zähler (z. B. am Stromzähler) vor dem Wechselrichter.")}</p>
        ${entries.map(([quantity, m]) => {
          const auto = m.effective && !m.selected
            ? t("Automatisch ({name})", { name: byId[m.effective]?.name ?? m.options.find((o) => o.id === m.effective)?.name })
            : t("Automatisch");
          return html`
            <div class="field wide">
              <span class="field-label">${m.label}</span>
              <sf-picker label=${m.label} hide-values empty-label=${auto} .value=${m.selected ?? ""}
                .options=${m.options.map((o) => ({ value: o.id, label: o.name }))}
                @change=${(e) => this._setMeasurement(quantity, e.detail.value)}></sf-picker>
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
      this._error = t("Anlagenstatus konnte nicht geladen werden: {error}", { error: err.message });
    }
  }

  /** Capabilities of a configured device; manual devices are not part of discovery. */
  _capabilitiesOf(device) {
    return (
      this._capabilitiesByHaDevice[device.ha_device_id]
      ?? (device.profile.startsWith(MANUAL_PREFIX) ? MANUAL_CAPABILITIES[device.profile.slice(MANUAL_PREFIX.length)] ?? ["load"] : [])
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
      this._error = t("Kenndaten konnten nicht geladen werden: {error}", { error: err.message });
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
      title: t("„{name}“ entfernen?", { name: device.name }),
      text: t("Planung, Live und Steuerung berücksichtigen es dann nicht mehr. In Home Assistant bleibt das Gerät unverändert. Zum vorübergehenden Abschalten genügt „aktiv“ – dann bleiben die Einstellungen erhalten."),
      confirm: t("Entfernen"),
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
      this._error = t("Gerät konnte nicht entfernt werden: {error}", { error: err.message });
    }
  }

  async _saveDevice(device) {
    const params = this._params[device.id] ?? [];
    const parameters = {};
    for (const [name, text] of Object.entries(this._paramEdits[device.id] ?? {})) {
      const param = params.find((p) => p.name === name);
      const unit = param?.unit;
      const value = fromDisplay(text, unit);
      if (value === undefined) {
        this._error = t("Ungültige Zahl bei „{name}“.", { name: paramLabel(name) });
        return false;
      }
      const shown = (v) => `${toDisplay(v, unit)}${unit ? ` ${displayUnit(unit)}` : ""}`;
      if (value !== null && ((param?.min != null && value < param.min) || (param?.max != null && value > param.max))) {
        this._error = t("„{name}“: erlaubt sind {min} bis {max}.", {
          name: paramLabel(name),
          min: param.min != null ? shown(param.min) : "…",
          max: param.max != null ? shown(param.max) : "…",
        });
        return false;
      }
      parameters[name] = value;
    }
    const changes = { parameters };
    if (this._nameEdits[device.id] !== undefined) changes.name = this._nameEdits[device.id];
    if (this._flexEdits[device.id] !== undefined) {
      const flex = { ...device.flex, ...this._flexEdits[device.id] };
      if (flex.target === undefined || flex.target_max === undefined || flex.alternative_cost_ct === undefined) {
        this._error = t("Bitte gültige Zahlen bei der Planung eingeben.");
        return false;
      }
      changes.flex = flex;
    }
    const feedEdit = this._feedEdits[device.id];
    if (feedEdit) {
      const rate = fromDisplay(feedEdit.rate ?? toDisplay(device.feed_in?.rate_ct_per_kwh, "ct"), "ct");
      if (rate === undefined) {
        this._error = t("Ungültige Zahl bei der Vergütung.");
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
      this._error = t("Kenndaten konnten nicht gespeichert werden: {error}", { error: err.message });
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
      this._error = t("Zuordnung konnte nicht gespeichert werden: {error}", { error: err.message });
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
    this._touchedIntegrations = new Set([...this._touchedIntegrations, integration.entry_id]);
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
      this._error = t("Gerät konnte nicht gespeichert werden: {error}", { error: err.message });
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
        this._error = t("Entitäten konnten nicht geladen werden: {error}", { error: err.message });
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
            <div class="subtitle">${{ live: t("Live"), control: t("Steuerung"), plan: t("Planung"), analysis: t("Analyse") }[this._view] ?? t("Einstellungen")}${this._haConnected === false ? ` · ${t("keine Verbindung zu Home Assistant")}` : ""}</div>
          </div>
        </div>`}
        <nav class="tabs" aria-label=${t("Hauptmenü")}>
          ${VIEWS.map(
            ([view, label, icon]) => html`<button class="tab ${this._view === view ? "active" : ""}" title=${label}
              aria-label=${label} aria-current=${this._view === view ? "page" : "false"} @click=${() => this._setView(view)}>
              <svg class="tab-icon" viewBox="0 0 24 24" aria-hidden="true"><path d=${icon}></path></svg>
              <span class="tab-label">${label}</span>
              ${view === "analysis" && this._recommendationCount
                ? html`<span class="badge" aria-label=${t("{count} Empfehlungen", { count: this._recommendationCount })}>${this._recommendationCount}</span>`
                : nothing}
            </button>`,
          )}
        </nav>
      </header>
      ${EMBEDDED && this._haConnected === false
        ? html`<div class="restore-banner" role="alert">${t("Keine Verbindung zu Home Assistant – Werte sind evtl. veraltet.")}</div>`
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

  updated() {
    this._updateNavMore();
  }

  _updateNavMore() {
    const nav = this.renderRoot?.querySelector(".sidebar nav");
    const more = nav
      ? { left: nav.scrollLeft > 2, right: nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2 }
      : { left: false, right: false };
    // Only a real change renders again
    if (more.left !== this._navMore.left || more.right !== this._navMore.right) this._navMore = more;
  }

  _scrollNav(direction) {
    const nav = this.renderRoot.querySelector(".sidebar nav");
    nav?.scrollBy({ left: direction * nav.clientWidth * 0.7, behavior: "smooth" });
  }

  _renderWizard() {
    return html`
      <div class="layout">
        <aside class="sidebar">
        ${this._renderSearch()}
        <div class="nav-wrap">
        ${this._navMore.left
          ? html`<button class="nav-more left" aria-label=${t("Weitere Bereiche links")} @click=${() => this._scrollNav(-1)}>‹</button>`
          : nothing}
        <nav aria-label=${t("Schritte")} @scroll=${() => this._updateNavMore()}>
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
        ${this._navMore.right
          ? html`<button class="nav-more right" aria-label=${t("Weitere Bereiche rechts")} @click=${() => this._scrollNav(1)}>›</button>`
          : nothing}
        </div>
        <button class="switch" aria-pressed=${this._advanced} @click=${() => this._setAdvanced(!this._advanced)}>
          <span class="track ${this._advanced ? "on" : ""}"><span class="knob"></span></span>
          ${t("Expertenmodus")}
        </button>
        </aside>
        <main>
          ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
          ${this._renderStep()}
          ${this._setupCompleted
            ? nothing
            : html`<div class="footer">
                ${this._step > 0 ? html`<button class="secondary" @click=${() => this._goTo(this._step - 1)}>${t("Zurück")}</button>` : nothing}
                <span class="spacer"></span>
                ${this._step < STEPS.length - 1
                  ? html`<button class="primary" @click=${() => this._goTo(this._step + 1)}>${t("Weiter")}</button>`
                  : nothing}
              </div>`}
        </main>
      </div>
    `;
  }

  /** Results for the search box: settings by name, synonym or a typo, and devices by name. */
  _searchResults() {
    const devices = (this._devices ?? []).map((d) => ({ step: "devices", label: d.name, keywords: "gerät device", hint: t("Gerät") }));
    return searchSettings(this._searchQuery, STEPS, devices);
  }

  _renderSearch() {
    const results = this._searchQuery.trim() ? this._searchResults() : [];
    const active = Math.min(this._searchActive, results.length - 1);
    const area = (step) => STEPS.find(([key]) => key === step)?.[1] ?? "";
    const onKey = (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : -1;
        this._searchActive = (active + step + results.length) % Math.max(results.length, 1);
      } else if (e.key === "Enter" && results[active]) {
        this._openSearchResult(results[active]);
      } else if (e.key === "Escape") {
        this._searchQuery = "";
      }
    };
    return html`<div class="settings-search" role="search">
      <input type="search" .value=${this._searchQuery} placeholder=${t("Einstellung suchen …")} aria-label=${t("Einstellung suchen")}
        role="combobox" aria-expanded=${results.length > 0} aria-controls="settings-search-results" autocomplete="off"
        aria-activedescendant=${results.length ? `search-result-${active}` : nothing}
        @input=${(e) => {
          this._searchQuery = e.target.value;
          this._searchActive = 0;
        }}
        @keydown=${onKey}>
      ${this._searchQuery.trim()
        ? html`<ul class="search-results" id="settings-search-results" role="listbox">
            ${results.length
              ? results.map((r, i) => html`<li id="search-result-${i}" role="option" aria-selected=${i === active}
                  class=${i === active ? "active" : ""} @mousedown=${(e) => e.preventDefault()} @click=${() => this._openSearchResult(r)}>
                  <span class="search-label">${r.label}</span>
                  <span class="search-area">${r.area ? t("Bereich") : [area(r.step), r.hint].filter(Boolean).join(" · ")}</span>
                </li>`)
              : html`<li class="search-empty">${t("Nichts gefunden – versuch ein anderes Wort.")}</li>`}
          </ul>`
        : nothing}
    </div>`;
  }

  /** Opens the area of a result and shows the setting: scrolled into view, opened and highlighted. */
  async _openSearchResult(result) {
    this._searchQuery = "";
    await this._goTo(result.step);
    if (STEPS[this._step][0] !== result.step) return;
    if (result.area) {
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    // Areas load their data after opening; some fields only show in expert mode
    const started = Date.now();
    let element = null;
    while (!element && Date.now() - started < 4000) {
      await this.updateComplete;
      await new Promise((resolve) => setTimeout(resolve, 150));
      element = findLabel(this.renderRoot, result.label);
      if (!element && !this._advanced && Date.now() - started > 1500) this._setAdvanced(true);
    }
    if (!element) return;
    for (let node = element; node; node = node.parentNode ?? node.host) {
      if (node.tagName === "DETAILS") node.open = true;
    }
    const target = element.closest(".field, fieldset, label, .card, details") ?? element;
    await new Promise((resolve) => requestAnimationFrame(resolve));
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    target.animate(
      [{ boxShadow: "0 0 0 3px var(--sf-primary)", borderRadius: "8px" }, { boxShadow: "0 0 0 3px transparent", borderRadius: "8px" }],
      { duration: 2400, easing: "ease-in" },
    );
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
      case "display":
        return this._renderDisplay();
      case "optimizers":
        return html`<sf-optimizers></sf-optimizers>`;
      default:
        return this._renderSummary();
    }
  }

  _renderIntegrations() {
    if (this._integrations === null) {
      return html`<h1>${t("Gefundene Integrationen")}</h1>
        ${this._discoveryError
          ? html`<div class="alert error" role="alert">${this._discoveryError}</div>
              <button class="secondary" @click=${() => this._loadDiscovery()}>${t("Erneut versuchen")}</button>`
          : html`<p class="muted">${t("Suche in Home Assistant …")}</p>`}`;
    }
    return html`
      <div class="heading">
        <h1>${t("Gefundene Integrationen")}</h1>
        <p class="muted">${t("In deinem Home Assistant laufen diese Integrationen. Wähle aus, was Sunflow nutzen soll.")}</p>
      </div>
      ${this._integrations.length === 0
        ? html`<p class="muted">${t("Keine unterstützte Integration gefunden.")}</p>`
        : this._renderIntegrationCards()}
      ${this._renderDirectForm()}
      ${this._renderIntegrationLink()}
      ${this._renderMcp()}
    `;
  }

  _renderIntegrationCards() {
    const used = (i) => this._selected.has(i.entry_id) || this._touchedIntegrations.has(i.entry_id);
    const visible = this._showUnusedIntegrations ? this._integrations : this._integrations.filter(used);
    const unused = this._integrations.filter((i) => !used(i)).length;
    return html`
      ${visible.length ? html`<div class="cards">${visible.map((i) => this._renderIntegration(i))}</div>` : nothing}
      ${unused
        ? html`<div><button class="secondary" aria-expanded=${this._showUnusedIntegrations}
            @click=${() => (this._showUnusedIntegrations = !this._showUnusedIntegrations)}>
            ${this._showUnusedIntegrations
              ? t("Nicht verwendete ausblenden")
              : t("Nicht verwendete anzeigen ({count})", { count: unused })}
          </button></div>`
        : nothing}
    `;
  }

  /** How the Sunflow integration in Home Assistant connects to the App. */
  _renderIntegrationLink() {
    const link = this._link;
    return html`<section class="device compact">
      <div class="device-title">${t("Sunflow-Integration in Home Assistant")}</div>
      <p class="small muted">${t("Sie bringt die gelernte PV-Prognose ins Energie-Dashboard (je Fläche, Anlage und gesamt), Zähler je PV-String und Sensoren für eigene Automationen. Home Assistant bietet sie unter „Einstellungen → Geräte & Dienste“ als gefunden an; sonst von Hand mit Adresse und Schlüssel einrichten.")}</p>
      ${this._renderForecastKinds()}
      ${link
        ? html`<div class="fields">
            <label class="field"><span class="field-label">${t("Adresse")}</span>
              <span class="input-unit"><input readonly .value=${link.url}></span></label>
            <label class="field"><span class="field-label">${t("Schlüssel")}</span>
              <span class="input-unit"><input readonly .value=${link.token}></span></label>
          </div>`
        : html`<div><button class="secondary" @click=${() => this._showLink()}>${t("Zugangsdaten anzeigen …")}</button></div>`}
    </section>`;
  }

  _renderDisplay() {
    const display = this._display;
    if (!display) {
      // Loaded once when the area is first shown; undefined while loading
      if (display === null) {
        this._display = undefined;
        api.display().then((loaded) => (this._display = loaded)).catch(() => (this._display = null));
      }
      return html`<h1>${t("Anzeige")}</h1><p class="muted">${t("Lade …")}</p>`;
    }
    const option = (key, label) => html`<label class="inline">
      <input type="checkbox" .checked=${display[key]} @change=${(e) => this._setDisplay({ [key]: e.target.checked })}> ${label}</label>`;
    return html`
      <div class="heading">
        <h1>${t("Anzeige")}</h1>
        <p class="muted">${t("Was die Seiten zeigen – für Handy und größere Bildschirme getrennt.")}</p>
      </div>
      <section class="device compact">
        <div class="device-title">${t("Wetter in der Live-Ansicht")}</div>
        <span class="choices">${option("weather_mobile", t("auf dem Handy"))} ${option("weather_desktop", t("auf Tablet und Computer"))}</span>
        <p class="small muted">${t("Ausgeblendet bleibt mehr Platz für den Energiefluss. Ist es überall aus, fragt Sunflow die Wettervorhersage für die Live-Ansicht gar nicht erst ab.")}</p>
      </section>
      <section class="device compact">
        <div class="device-title">${t("Sprache")}</div>
        <select aria-label=${t("Sprache")} @change=${(e) => this._setLanguage(e.target.value)}>
          ${[["auto", t("Wie Home Assistant")], ["de", "Deutsch"], ["en", "English"]].map(
            ([value, label]) => html`<option value=${value} ?selected=${(display.language ?? "auto") === value}>${label}</option>`,
          )}
        </select>
        <p class="small muted">${t("Gilt für die Oberfläche und für Meldungen wie Planhinweise und Empfehlungen. Die Seite lädt danach neu.")}</p>
      </section>
    `;
  }

  async _setDisplay(changes) {
    try {
      this._display = await api.setDisplay({ ...this._display, ...changes });
      return true;
    } catch (err) {
      this._error = t("Einstellung nicht gespeichert: {error}", { error: err.message });
      return false;
    }
  }

  async _setLanguage(language) {
    // Texts and formats are fixed when the modules load
    if (await this._setDisplay({ language })) window.location.reload();
  }

  _renderForecastKinds() {
    const settings = this._integrationSettings;
    if (!settings) {
      // Loaded once when the section is first shown; undefined while loading
      if (settings === null) {
        this._integrationSettings = undefined;
        api.integrationSettings().then((loaded) => (this._integrationSettings = loaded)).catch(() => (this._integrationSettings = null));
      }
      return nothing;
    }
    const kinds = [
      ["total", t("Gesamt")],
      ["plant", t("je Wechselrichter")],
      ["plane", t("je Fläche / String")],
    ];
    return html`<div class="field">
      <span class="field-label">${t("PV-Prognosen fürs Energie-Dashboard")}</span>
      <span class="choices">${kinds.map(([kind, label]) => html`<label class="inline">
        <input type="checkbox" .checked=${settings.forecast_kinds.includes(kind)}
          @change=${(e) => this._setForecastKind(kind, e.target.checked)}> ${label}</label>`)}</span>
      <span class="small muted">${t("Jede gewählte Prognose wird ein eigenes Gerät in Home Assistant. Abgewählte verschwinden dort beim nächsten Abgleich (bis zu 15 Minuten).")}</span>
    </div>`;
  }

  async _setForecastKind(kind, on) {
    const current = this._integrationSettings.forecast_kinds.filter((k) => k !== kind);
    const forecast_kinds = on ? [...current, kind] : current;
    try {
      this._integrationSettings = await api.setIntegrationSettings({ ...this._integrationSettings, forecast_kinds });
    } catch (err) {
      this._error = t("Einstellung nicht gespeichert: {error}", { error: err.message });
    }
  }

  /** The read-only MCP server, so an AI assistant can analyse Sunflow's data. */
  _renderMcp() {
    const mcp = this._mcp;
    if (mcp === null) {
      // Loaded once when the area is first shown; undefined while loading
      this._mcp = undefined;
      api.mcpLink().then((loaded) => (this._mcp = loaded)).catch(() => (this._mcp = false));
    }
    // The App's mapped port is on the Home Assistant host; the browser's address may be the external one
    const url = mcp?.port ? `http://${mcp.host ?? location.hostname}:${mcp.port}${mcp.path}` : null;
    const created = mcp?.key_created ? new Date(mcp.key_created).toLocaleString(locale()) : null;
    return html`<section class="device compact">
      <div class="device-title">${t("MCP-Server für KI-Assistenten")}</div>
      <p class="small muted">${t("Damit kann ein KI-Assistent (z. B. Claude Code) Plan, Verlauf, Aufzeichnung, Steuerungsprotokoll und Konfiguration lesen, um Fragen zu beantworten oder Fehler zu finden. Er kann nichts ändern oder steuern. Von außen erreichbar erst, wenn in der App unter „Konfiguration → Netzwerk“ ein Port eingetragen ist und ein Schlüssel erzeugt wurde.")}</p>
      ${mcp
        ? html`<div class="fields">
              <label class="field"><span class="field-label">${t("Adresse")}</span>
                <span class="input-unit"><input readonly .value=${url ?? t("kein Port freigegeben")}></span></label>
              <div class="field"><span class="field-label">${t("Schlüssel")}</span>
                <span class="input-unit">
                  <input readonly aria-label=${t("Schlüssel")}
                    .value=${mcp.key ?? (created ? t("erzeugt am {time}", { time: created }) : t("keiner – Server geschlossen"))}>
                  <button class="secondary" @click=${() => this._createMcpKey()}>${created ? t("Neu …") : t("Erzeugen")}</button>
                  ${created ? html`<button class="secondary" @click=${() => this._revokeMcpKey()}>${t("Zurückziehen …")}</button>` : nothing}
                </span></div>
            </div>
            <div class="field"><span class="field-label">${t("Darf lesen")}</span>
              <span class="choices">${MCP_AREAS.map(([area, label]) => html`<label class="inline">
                <input type="checkbox" .checked=${mcp.areas.includes(area)} @change=${(e) => this._setMcpArea(area, e.target.checked)}>
                ${label}</label>`)}</span>
              <span class="small muted">${t("Version und Verbindung sind immer lesbar. Nicht freigegebene Bereiche fehlen in der Werkzeugliste des Assistenten.")}</span></div>
            ${mcp.key
              ? html`<p class="small">${t("Der Schlüssel wird nur jetzt angezeigt. Sunflow speichert ihn nicht, nur eine Prüfsumme.")}</p>
                  <label class="field"><span class="field-label">${t("Einrichten in Claude Code")}</span>
                    <span class="input-unit"><input readonly
                      .value=${`claude mcp add --transport http sunflow ${url ?? t("http://<Adresse>:<Port>/mcp")} --header "Authorization: Bearer ${mcp.key}"`}></span></label>`
              : nothing}`
        : html`<p class="muted">${mcp === false ? t("Nicht verfügbar.") : t("Lade …")}</p>`}
    </section>`;
  }

  async _setMcpArea(area, on) {
    const areas = on ? [...new Set([...this._mcp.areas, area])] : this._mcp.areas.filter((a) => a !== area);
    try {
      this._mcp = { ...this._mcp, ...(await api.setMcpSettings({ areas })) };
    } catch (err) {
      this._error = t("Einstellung nicht gespeichert: {error}", { error: err.message });
    }
  }

  async _createMcpKey() {
    if (this._mcp.key_created && !confirm(t("Der bisherige Schlüssel wird sofort ungültig. Neuen erzeugen?"))) return;
    try {
      this._mcp = { ...this._mcp, ...(await api.createMcpKey()) };
    } catch (err) {
      this._error = t("Schlüssel nicht erzeugt: {error}", { error: err.message });
    }
  }

  async _revokeMcpKey() {
    if (!confirm(t("Schlüssel zurückziehen? Der MCP-Server ist danach geschlossen."))) return;
    try {
      this._mcp = { ...this._mcp, ...(await api.revokeMcpKey()), key: undefined };
    } catch (err) {
      this._error = t("Schlüssel nicht zurückgezogen: {error}", { error: err.message });
    }
  }

  async _showLink() {
    try {
      this._link = await api.integrationLink();
    } catch (err) {
      this._error = t("Zugangsdaten nicht verfügbar: {error}", { error: err.message });
    }
  }

  /** Adding a device Sunflow talks to itself (Modbus TCP, HTTP), without an integration. */
  _renderDirectForm() {
    const form = this._direct;
    if (form === null) {
      return html`<section class="device compact">
        <div class="device-title">${t("Ohne Home-Assistant-Integration")}</div>
        <p class="small muted">${t("Manche Wechselrichter liest und steuert Sunflow selbst über das Netzwerk (Modbus TCP), z. B. Victron über das GX-Gerät.")}</p>
        <div><button class="secondary" @click=${() => this._openDirect()}>${t("Gerät direkt anbinden …")}</button></div>
      </section>`;
    }
    const set = (field, value) => (this._direct = { ...this._direct, [field]: value });
    const profiles = this._directProfiles ?? [];
    const chosen = profiles.find((p) => p.profile === form.profile);
    return html`
      <section class="device">
        <div class="device-title">${t("Gerät direkt anbinden")}</div>
        <div class="fields">
          <label class="field">
            <span class="field-label">${t("Gerät")}</span>
            <sf-picker label=${t("Gerät")} hide-values empty-label=${t("bitte wählen …")} .value=${form.profile ?? ""}
              .options=${profiles.map((p) => ({ value: p.profile, label: [p.manufacturer, p.model].filter(Boolean).join(" ") || p.profile }))}
              @change=${(e) => set("profile", e.detail.value)}></sf-picker>
          </label>
          <label class="field">
            <span class="field-label">${t("Name")}</span>
            <span class="input-unit"><input placeholder=${chosen?.model ?? t("z. B. Victron")} .value=${form.name}
              @input=${(e) => set("name", e.target.value)}></span>
          </label>
          <label class="field">
            <span class="field-label">${t("Adresse (IP oder Hostname)")}</span>
            <span class="input-unit"><input placeholder="192.168.1.50" .value=${form.host} @input=${(e) => set("host", e.target.value)}></span>
          </label>
          <label class="field">
            <span class="field-label">${t("Port")}</span>
            <span class="input-unit"><input inputmode="numeric" placeholder=${chosen?.port ?? 502} .value=${form.port}
              @input=${(e) => set("port", e.target.value)}></span>
          </label>
          ${chosen?.type === "modbus_tcp"
            ? html`<label class="field">
                <span class="field-label">${t("Modbus-Unit-ID")}</span>
                <span class="input-unit"><input inputmode="numeric" placeholder=${chosen?.unit_id ?? 1} .value=${form.unit_id}
                  @input=${(e) => set("unit_id", e.target.value)}></span>
              </label>`
            : nothing}
        </div>
        <p class="small muted">${t("Sunflow liest das Gerät sofort einmal; danach erscheint es unter „Geräte“ mit seinen Live-Werten. Gesteuert wird es erst, wenn du es unter „Steuerung“ freigibst.")}</p>
        <div class="actions">
          <button class="primary" ?disabled=${!form.profile || !form.host.trim() || this._busy.has("direct")}
            @click=${() => this._addDirect()}>${t("Anbinden")}</button>
          <button class="secondary" @click=${() => (this._direct = null)}>${t("Abbrechen")}</button>
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
        this._error = t("Profile konnten nicht geladen werden: {error}", { error: err.message });
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
      this._error = t("Gerät konnte nicht angebunden werden: {error}", { error: err.message });
    } finally {
      this._busy = this._without(this._busy, "direct");
    }
  }

  _renderIntegration(integration) {
    const selected = this._selected.has(integration.entry_id);
    const matched = integration.devices.filter((d) => d.profile);
    const capabilities = matched.flatMap((d) => d.capabilities ?? []);
    let chip;
    if (!integration.supported) chip = html`<span class="chip warn">${t("Nicht unterstützt – Schalter oder Steckdosen unten als eigene Verbraucher hinzufügen")}</span>`;
    else if (matched.length === 0 && integration.optimizers) chip = html`<span class="chip ok">${t("Unterstützt · Optimierer")}</span>`;
    else if (matched.length === 0) chip = html`<span class="chip warn">${t("Kein passendes Profil")}</span>`;
    else if (isControllable(capabilities)) chip = html`<span class="chip ok">${t("Unterstützt · steuerbar")}</span>`;
    else chip = html`<span class="chip neutral">${t("Unterstützt · nur lesen")}</span>`;
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
        ${integration.optimizers
          ? html`<span class="model"><strong>${t("Optimierer")}</strong> · ${tn(integration.optimizers, "{count} Module", { count: integration.optimizers })}</span>`
          : nothing}
        <span class="muted small">${[
          integration.title !== integration.name ? t("Eintrag „{title}“", { title: integration.title }) : null,
          integration.in_energy_dashboard ? t("im Energie-Dashboard") : null,
        ].filter(Boolean).join(" · ")}</span>
        ${chip}
      </button>
    `;
  }

  _renderDevices() {
    const devices = this._devices;
    return html`
      <div class="heading">
        <h1>${t("Geräte")}</h1>
        <p class="muted">${t("Profile wurden automatisch erkannt. Prüfe die Live-Werte – passen sie zu dem, was deine Anlage gerade tut?")}</p>
      </div>
      ${devices.length === 0 ? html`<p class="muted">${t("Keine Geräte mit passendem Profil ausgewählt.")}</p>` : nothing}
      ${CATEGORIES.map(([key, title]) => {
        const members = devices.filter((d) => this._categoryOf(d) === key);
        const hasBattery = devices.some((d) => (d.capabilities ?? []).includes("battery"));
        const optimizerSources = (this._integrations ?? []).filter((i) => this._selected.has(i.entry_id) && i.optimizers);
        const extra =
          key === "battery" && hasBattery ? html`<sf-batteries></sf-batteries>`
          : key === "load" ? this._renderManualDevices()
          : key === "inverter" && optimizerSources.length ? optimizerSources.map((i) => this._renderOptimizerSource(i))
          : nothing;
        if (!members.length && key !== "load" && !(key === "battery" && hasBattery) && !(key === "inverter" && optimizerSources.length)) {
          return nothing;
        }
        return html`<h2 class="category">${title}</h2>${members.map((d) => this._renderDevice(d))}${extra}`;
      })}
    `;
  }

  /** The optimizers of a chosen integration: taken over as a whole, placed under "Optimierer". */
  _renderOptimizerSource(integration) {
    const used = integration.optimizers_used;
    return html`<section class="device compact">
      <div class="device-title">${t("Optimierer")} · ${integration.name ?? integration.title}</div>
      <span class="small muted">${tn(integration.optimizers, "{count} Module", { count: integration.optimizers })}${integration.title !== integration.name ? ` · ${t("Eintrag „{title}“", { title: integration.title })}` : ""}</span>
      <label class="inline">
        <input type="checkbox" .checked=${used} ?disabled=${this._busy.has(integration.entry_id)}
          @change=${(e) => this._useOptimizers(integration, e.target.checked)}>
        ${t("von Sunflow nutzen")}
      </label>
      <p class="small muted">${used
        ? t("Strings und Anordnung auf dem Dach legst du unter „Optimierer“ fest; die Werte zeigt die Live-Ansicht je Modul.")
        : t("Nicht genutzt: Sunflow zeigt die Module weder auf dem Dach noch in der Live-Ansicht. Zugeordnete Plätze bleiben gespeichert.")}</p>
    </section>`;
  }

  async _useOptimizers(integration, used) {
    this._busy = new Set([...this._busy, integration.entry_id]);
    try {
      await api.useOptimizers(integration.entry_id, used);
      this._integrations = this._integrations.map((i) => (i.entry_id === integration.entry_id ? { ...i, optimizers_used: used } : i));
    } catch (err) {
      this._error = t("Optimierer nicht gespeichert: {error}", { error: err.message });
    } finally {
      this._busy = this._without(this._busy, integration.entry_id);
    }
  }

  _renderManualDevices() {
    const manual = Object.values(this._configDevices).filter((d) => d.profile.startsWith(MANUAL_PREFIX));
    const form = this._manual;
    const set = (field, value) => (this._manual = { ...this._manual, [field]: value });
    const candidates = this._candidates;
    // An entity chosen for the consumer suggests its name
    const setEntity = (field, value, list) =>
      (this._manual = withSuggestedName({ ...this._manual, [field]: value }, (list ?? []).find((c) => c.entity_id === value)));
    const entity = (list, detail) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: detail(c) }));
    const unitDetail = (c) => `${c.state ?? "–"} ${c.unit ?? ""}`;
    const control = { switch: form.switch_entity, stepped: form.level_entity, current: form.current_entity, meter: form.power_entity }[form.type];
    const measureOnly = form.type === "meter";
    const selected = (candidates?.selects ?? []).find((c) => c.entity_id === form.level_entity);
    return html`
      <section class="device">
        <div class="device-title">${t("Eigene Verbraucher")}</div>
        <p class="small muted">${t("Geräte, die Sunflow nicht automatisch erkennt: ein- und ausschaltbar (Shelly, Steckdose), in Leistungsstufen (z. B. ESPHome-Heizstab mit Auswahl) oder mit Ziel-Stromstärke – jeweils mit oder ohne Leistungs- und Verbrauchsmessung. Ein Heizstab im Speicher wird als „Wärme“ mit Temperatur geplant. Nicht steuerbare Geräte mit eigener Leistungsmessung (Herd, Waschmaschine …) lassen sich zum Lernen „nur messen“.")}</p>
        ${manual.length
          ? html`<ul class="summary-list">${manual.map(
              (d) => html`<li class="manual-item"><div><strong>${d.name}</strong>
                <div class="small muted">${MANUAL_TYPES[d.profile.slice(MANUAL_PREFIX.length)] ?? ""}${d.kind === "heater" ? ` · ${t("Wärme")}` : ""}
                  · ${Object.values(d.role_overrides).map((o) => o.entity_id).filter(Boolean).join(", ")}</div>
                ${this._usages?.[d.id]
                  ? html`<div class="small">${t("gemessen: {energy} pro Tag", { energy: formatValue(this._usages[d.id].daily_wh, "Wh") })}${this._usages[d.id].running_w
                      ? html` · ${t("läuft mit {power}", { power: formatValue(this._usages[d.id].running_w, "W") })}`
                      : nothing}</div>`
                  : nothing}</div>
                <span class="item-actions">
                  <button class="secondary" ?disabled=${this._manualEditId === d.id} @click=${() => this._editManual(d)}>${t("Bearbeiten")}</button>
                  <button class="secondary danger" @click=${() => this._removeDevice(d)}>${t("Entfernen")}</button>
                </span>
                ${d.kind === "heater" || d.disabled || d.profile === "generic/meter" ? nothing : this._renderCharger(d)}</li>`,
            )}</ul>`
          : nothing}
        ${candidates === null
          ? html`<button class="secondary" @click=${() => this._loadCandidates()}>${t("Verbraucher hinzufügen …")}</button>`
          : html`
              ${this._manualEditId
                ? html`<div class="field-label">${t("„{name}“ bearbeiten", { name: Object.values(this._configDevices).find((d) => d.id === this._manualEditId)?.name ?? "" })}</div>`
                : nothing}
              <div class="fields">
                <label class="field">
                  <span class="field-label">${t("Name")}</span>
                  <span class="input-unit"><input placeholder=${t("z. B. E-Bike")} .value=${form.name} @input=${(e) => set("name", e.target.value)}></span>
                </label>
                <label class="field">
                  <span class="field-label">${t("Gesteuert über")}</span>
                  <select @change=${(e) => set("type", e.target.value)}>
                    ${Object.entries(MANUAL_TYPES).map(([value, label]) => html`<option value=${value} ?selected=${form.type === value}>${label}</option>`)}
                  </select>
                </label>
                ${measureOnly
                  ? html`<p class="small muted field">${t("Sunflow schaltet das Gerät nie. Es lernt aus der Leistungsmessung, wann und wie viel es läuft (Analyse → Lernen).")}</p>`
                  : html`<label class="field">
                      <span class="field-label">${t("Art")}</span>
                      <select @change=${(e) => set("kind", e.target.value)}>
                        <option value="load" ?selected=${form.kind === "load"}>${t("Verbraucher (Energie pro Tag)")}</option>
                        <option value="heater" ?selected=${form.kind === "heater"}>${t("Wärme (Speicher mit Temperatur)")}</option>
                      </select>
                    </label>`}
                ${form.type === "switch" || form.type === "current"
                  ? html`<div class="field">
                      <span class="field-label">${form.type === "current" ? t("Schalter (optional)") : t("Schalter")}</span>
                      <sf-picker label=${t("Schalter")} empty-label=${form.type === "current" ? t("keiner") : nothing} placeholder=${t("Name oder Entität suchen …")}
                        .value=${form.switch_entity} .options=${entity(candidates.switches, (c) => c.state)}
                        @change=${(e) => setEntity("switch_entity", e.detail.value, candidates?.switches)}></sf-picker>
                    </div>`
                  : nothing}
                ${form.type === "stepped"
                  ? html`<div class="field">
                      <span class="field-label">${t("Leistungsstufe (Auswahl)")}</span>
                      <sf-picker label=${t("Leistungsstufe")} placeholder=${t("z. B. Heizstab Heizleistung …")}
                        .value=${form.level_entity} .options=${entity(candidates.selects, (c) => c.state)}
                        @change=${(e) => this._setStepEntity(e.detail.value)}></sf-picker>
                    </div>`
                  : nothing}
                ${form.type === "current"
                  ? html`<div class="field">
                      <span class="field-label">${t("Ziel-Stromstärke (Zahl)")}</span>
                      <sf-picker label=${t("Stromstärke")} placeholder=${t("number-Entität in A …")}
                        .value=${form.current_entity} .options=${entity(candidates.numbers, unitDetail)}
                        @change=${(e) => setEntity("current_entity", e.detail.value, candidates?.numbers)}></sf-picker>
                    </div>`
                  : nothing}
                <div class="field">
                  <span class="field-label">${measureOnly ? t("Leistungsmessung") : t("Leistungsmessung (optional)")}</span>
                  <sf-picker label=${t("Leistungsmessung")} empty-label=${measureOnly ? nothing : t("keine")} placeholder=${t("Name oder Entität suchen …")}
                    .value=${form.power_entity} .options=${entity(candidates.power, unitDetail)}
                    @change=${(e) => setEntity("power_entity", e.detail.value, candidates?.power)}></sf-picker>
                </div>
                <div class="field">
                  <span class="field-label">${t("Verbrauchsmessung (optional)")}</span>
                  <sf-picker label=${t("Verbrauchsmessung")} empty-label=${t("keine")} placeholder=${t("Energiezähler (kWh) …")}
                    .value=${form.energy_entity} .options=${entity(candidates.energy, unitDetail)}
                    @change=${(e) => setEntity("energy_entity", e.detail.value, candidates?.energy)}></sf-picker>
                </div>
                ${form.type !== "stepped" && !measureOnly
                  ? html`<label class="field">
                      <span class="field-label">${t("Leistung")}</span>
                      <span class="input-unit"><input inputmode="decimal" placeholder="500" .value=${form.max_power}
                        @input=${(e) => set("max_power", e.target.value)}><span class="muted">W</span></span>
                    </label>`
                  : nothing}
                ${measureOnly
                  ? nothing
                  : form.kind === "heater"
                  ? html`
                      <div class="field">
                        <span class="field-label">${t("Temperatur im Speicher")}</span>
                        <sf-picker label=${t("Temperatur")} placeholder=${t("z. B. Speicher Temperatur oben …")}
                          .value=${form.temperature_entity} .options=${entity(candidates.temperature, unitDetail)}
                          @change=${(e) => set("temperature_entity", e.detail.value)}></sf-picker>
                      </div>
                      <label class="field">
                        <span class="field-label">${t("Speichervolumen")}</span>
                        <span class="input-unit"><input inputmode="decimal" placeholder="300" .value=${form.tank_l}
                          @input=${(e) => set("tank_l", e.target.value)}><span class="muted">l</span></span>
                      </label>`
                  : html`<label class="field">
                      <span class="field-label">${t("Energiebedarf pro Tag")}</span>
                      <span class="input-unit"><input inputmode="decimal" placeholder=${t("z. B. 0,5")} .value=${form.daily_energy}
                        @input=${(e) => set("daily_energy", e.target.value)}><span class="muted">kWh</span></span>
                    </label>`}
              </div>
              ${form.type === "stepped" && Object.keys(form.steps).length
                ? html`<div class="field">
                    <span class="field-label">${selected ? t("Leistung je Stufe ({name})", { name: selected.name }) : t("Leistung je Stufe")}</span>
                    <div class="steps">
                      ${Object.entries(form.steps).map(([option, watts]) => html`<label class="step-power">
                        <span>${option}</span>
                        <span class="input-unit"><input inputmode="decimal" .value=${toDisplay(watts, "W")}
                          @input=${(e) => set("steps", { ...form.steps, [option]: fromDisplay(e.target.value, "W") })}><span class="muted">W</span></span>
                      </label>`)}
                    </div>
                    <span class="small muted">${t("Aus den Namen der Optionen gelesen; 0 W ist „aus“. Sunflow wählt die größte Stufe, die in die geplante Leistung passt.")}</span>
                  </div>`
                : nothing}
              <div class="actions">
                <button class="ok" ?disabled=${!control || !form.name.trim()} @click=${() => this._addManual()}>
                  ${this._manualEditId ? t("Speichern") : t("Hinzufügen")}
                </button>
                <button class="secondary" @click=${() => this._closeManualForm()}>${t("Abbrechen")}</button>
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
    const chosen = (this._candidates?.selects ?? []).find((c) => c.entity_id === entityId);
    this._manual = withSuggestedName({ ...this._manual, level_entity: entityId, steps }, chosen);
  }

  async _loadCandidates() {
    try {
      this._candidates = await api.candidates();
    } catch (err) {
      this._error = t("Entitäten konnten nicht geladen werden: {error}", { error: err.message });
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
      this._error = t("Bitte gültige Zahlen eingeben.");
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
      max_power: form.type === "stepped" || form.type === "meter" ? null : power ?? 500,
      daily_energy_wh: daily === null || form.kind === "heater" || form.type === "meter" ? null : daily * 1000,
      power_steps: form.type === "stepped" && Object.keys(form.steps).length ? form.steps : null,
    };
    try {
      const device = this._manualEditId ? await api.updateManual(this._manualEditId, body) : await api.addManual(body);
      this._configDevices = { ...this._configDevices, [device.ha_device_id]: device };
      this._loadCategories();
      this._closeManualForm();
      this._error = null;
    } catch (err) {
      this._error = t("Verbraucher konnte nicht gespeichert werden: {error}", { error: err.message });
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
    const open = (!disabled && (!configured || dirty || editing || missing || factsChanged || (preview && !preview.complete))) ||
      isOpen(`device:${id}`, !disabled);
    const head = html`
      <div class="device-head">
        <button class="device-toggle" aria-expanded=${open} @click=${() => this._toggleExpanded(id, open)}>
          <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
          <span>
            <span class="device-title">${device.name}</span>
            <span class="muted small device-sub">${[device.manufacturer, device.model].filter(Boolean).join(" ")} · ${t("über {integration}", { integration: device.integration })}${open
              ? ` · ${t("Profil {profile}", { profile: device.profile })}`
              : ""}</span>
          </span>
        </button>
        <span class="device-chips">
          ${disabled
            ? html`<span class="chip neutral">${t("deaktiviert")}</span>`
            : !configured || dirty
              ? html`<span class="chip warn">${t("noch nicht übernommen")}</span>`
              : missing
                ? html`<span class="chip warn">${t("Angabe fehlt")}</span>`
                : html`<span class="chip ok">✓ ${t("übernommen")}</span>`}
          ${configured
            ? html`<label class="inline enable" title=${disabled ? t("Gerät wieder verwenden") : t("Gerät mit allen Einstellungen behalten, aber nicht verwenden")}>
                <input type="checkbox" .checked=${!disabled} ?disabled=${this._busy.has(id)}
                  @change=${(e) => this._setEnabled(device, e.target.checked, e.target)}>${t("aktiv")}</label>`
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
          ? html`<span class="chip neutral">${t("{count} Komponenten", { count: device.components.length })}</span>`
          : nothing}
        ${device.disabled_entities?.length && !configured
          ? html`<p class="small muted" title=${device.disabled_entities.join(", ")}>${t("{count} benötigte Entitäten sind in Home Assistant deaktiviert. Sunflow aktiviert sie beim Übernehmen; die Integration lädt danach kurz neu, bis dahin fehlen ihre Werte.", { count: device.disabled_entities.length })}</p>`
          : nothing}
        ${preview === undefined ? html`<p class="muted">${t("Lade Live-Werte …")}</p>` : nothing}
        ${preview?.error ? html`<div class="alert error">${preview.error}</div>` : nothing}
        ${preview?.roles ? this._renderTiles(preview) : nothing}
        ${preview?.roles ? this._renderVehicleSoc(device, preview) : nothing}
        ${preview?.balance ? this._renderBalance(device.device_id) : nothing}
        ${editing && preview?.roles ? this._renderRoleEditor(device, preview) : nothing}
        ${!editing && this._advanced && preview?.roles ? this._renderRoleTable(preview) : nothing}
        ${this._advanced && device.components.length ? this._renderComponents(device) : nothing}
        <div class="actions">
          ${disabled
            ? html`<span class="small muted">${t("Deaktiviert – zum Bearbeiten wieder auf „aktiv“ stellen.")}</span>`
            : configured && !dirty
            ? html`<span class="chip ok">✓ ${t("Übernommen")}</span>`
            : html`<button class="ok" ?disabled=${this._busy.has(id) || !preview?.complete}
                @click=${() => this._accept(device)}>
                ${configured ? t("Anpassungen speichern") : dirty ? t("Mit Anpassungen übernehmen") : t("Werte passen")}
              </button>`}
          ${this._advanced || editing || dirty || !preview?.complete
            ? html`<button class="secondary" aria-expanded=${editing} @click=${() => this._toggleEdit(device)}>
                ${editing ? t("Zuordnung schließen") : t("Entitäten zuordnen")}
              </button>`
            : nothing}
        </div>
        ${stored && !disabled ? this._renderDeviceFacts(stored) : nothing}
        ${stored && !disabled && !stored.connection ? this._renderReload(stored) : nothing}
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
        <span class="field-label">${t("Ladestand des Fahrzeugs")}</span>
        <sf-picker label=${t("Ladestand des Fahrzeugs")} empty-label=${t("keiner")} placeholder=${t("z. B. MQTT-Sensor des Autos suchen …")}
          .value=${chosen}
          .options=${(this._candidates?.percent ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} %` }))}
          @change=${(e) => this._setOverride(device.device_id, "vehicle_soc", "entity_id", e.detail.value)}></sf-picker>
        <span class="small muted">${t("Die Wallbox kennt den Ladestand nicht. Ohne ihn plant Sunflow das Laden nicht ein; danach „Anpassungen speichern“.")}</span>
      </div>
    `;
  }

  async _setKind(device, kind) {
    try {
      await api.setDeviceKind(device.id, kind);
      this._configDevices = { ...this._configDevices, [device.ha_device_id]: { ...device, kind } };
      await Promise.all([this._loadCategories(), this._loadParameters()]);
    } catch (err) {
      this._error = t("Art nicht gespeichert: {error}", { error: err.message });
    }
  }

  _toggleExpanded(id, open) {
    setOpen(`device:${id}`, !open);
    this.requestUpdate();
  }

  async _setEnabled(device, enabled, input) {
    const stored = this._configDevices[device.device_id];
    if (!enabled && !(await confirmDialog({
      title: t("„{name}“ deaktivieren?", { name: device.name }),
      text: t("Die Einstellungen bleiben erhalten; Sunflow plant, misst und steuert das Gerät nicht mehr, bis es wieder aktiv ist."),
      confirm: t("Deaktivieren"),
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
      this._error = t("Gerät nicht umgeschaltet: {error}", { error: err.message });
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
                <div class="small muted">${r.missing ? t("nicht gefunden") : roleHint(r.role, r.value)}</div>
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
      text = t("Bilanz wird geprüft … ({count}/{total} Messungen)", { count: history.length, total: BALANCE_MIN_SAMPLES });
    } else if (passed * 2 > history.length) {
      state = "ok";
      text = `✓ ${t("Bilanz plausibel ({passed} von {count} Messungen)", { passed, count: history.length })}`;
    } else {
      state = "warn";
      text = `⚠ ${t("Bilanz unplausibel ({passed} von {count} Messungen)", { passed, count: history.length })}`;
    }
    return html`
      <div class="alert ${state}">
        <div>${text}</div>
        ${latest
          ? html`<div class="small">
          ${t("Letzte Messung: {formula} = {computed}, Hausverbrauch laut Gerät {reported} (Abweichung {deviation})", {
            formula: BALANCE_FORMULAS[latest.method],
            computed: formatValue(latest.computed_load, "W"),
            reported: formatValue(latest.reported_load, "W"),
            deviation: formatValue(Math.abs(latest.deviation), "W"),
          })}
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
            <tr><th>${t("Rolle")}</th><th>${t("Entität")}</th><th>${t("Rohwert")}</th><th>${t("Normalisiert")}</th></tr>
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
            <div class="editor-title">${t("Zuordnung anpassen")}</div>
            <div class="small muted">${t("Wähle eine andere Entität oder kehre das Vorzeichen um. Die Vorschau aktualisiert sich sofort.")}</div>
          </div>
          <button class="secondary" ?disabled=${!Object.keys(overrides).length} @click=${() => this._resetOverrides(id)}>
            ${t("Auf Profil zurücksetzen")}
          </button>
        </div>
        ${entities === undefined ? html`<p class="muted small">${t("Lade Entitäten …")}</p>` : nothing}
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>${t("Rolle")}</th><th>${t("Entität")}</th><th>${t("Vorzeichen")}</th><th>${t("Wert")}</th></tr>
            </thead>
            <tbody>
              ${preview.roles.map((r) => {
                const override = overrides[r.role] ?? {};
                const inverted = Boolean(override.sign) && override.sign !== r.profile_sign;
                return html`
                  <tr class=${Object.keys(override).length ? "overridden" : ""}>
                    <td>
                      ${roleLabel(r.role)}
                      ${Object.keys(override).length ? html`<span class="chip warn">${t("angepasst")}</span>` : nothing}
                    </td>
                    <td>
                      <sf-picker inline label=${t("Entität für {role}", { role: roleLabel(r.role) })}
                        empty-label=${t("Profil: {entity}", { entity: r.profile_entity_id ?? t("nicht gefunden") })}
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
                            ${t("umkehren")}
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
  /** An own load with a battery of its own (e-bike …): when it is ready, how much it needs, when it is full. */
  _renderCharger(device) {
    const edit = this._chargerEdits?.[device.id];
    const settings = edit ?? device.charger;
    const status = this._chargers?.[device.id];
    if (settings && this._candidates === null && !this._loadingCandidates) {
      this._loadingCandidates = true;
      this._loadCandidates().finally(() => (this._loadingCandidates = false));
    }
    const set = (key, value) => (this._chargerEdits = { ...this._chargerEdits, [device.id]: { ...settings, [key]: value } });
    const number = (key, label, unit, hint) => html`<label class="field">
        <span class="field-label">${label}</span>
        <span class="input-unit"><input type="number" min="0" .value=${settings[key] ?? ""}
          @input=${(e) => set(key, e.target.value === "" ? null : Number(e.target.value))}><span class="muted">${unit}</span></span>
        ${hint ? html`<span class="small muted">${hint}</span>` : nothing}
      </label>`;
    const picker = (key, label, options, empty) => html`<div class="field">
        <span class="field-label">${label}</span>
        <sf-picker label=${label} empty-label=${empty} placeholder=${t("Name oder Entität suchen …")} .value=${settings[key] ?? ""}
          .options=${(options ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: c.state ?? "–" }))}
          @change=${(e) => set(key, e.detail.value || null)}></sf-picker>
      </div>`;
    const at = (iso) => weekdayTime(iso);
    // Collapsible like the device cards; open while being edited
    const open = Boolean(edit) || isOpen(`charger:${device.id}`);
    const summary = !settings
      ? t("aus")
      : status?.testing
        ? t("Probeladung läuft …")
        : status?.note ?? (status?.ready ? t("Ladebereit") : t("an"));
    return html`
      <div class="field charger">
        <button class="device-toggle" aria-expanded=${open}
          @click=${() => { setOpen(`charger:${device.id}`, !open); this.requestUpdate(); }}>
          <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
          <span><span class="field-label">${t("Akku laden")}</span>${open ? nothing : html` <span class="small muted">· ${summary}</span>`}</span>
        </button>
        ${open ? html`<label class="inline wrap">
          <input type="checkbox" .checked=${Boolean(settings)}
            @change=${(e) => (e.target.checked
              ? (this._chargerEdits = { ...this._chargerEdits, [device.id]: { done_below_w: 10, done_after_min: 3, trigger_delay_min: 2, test_seconds: 60 } })
              : this._saveCharger(device, null))}>
          ${t("Verbraucher lädt einen eigenen Akku (z. B. E-Bike) und ist nur manchmal angesteckt")}
        </label>
        ${settings
          ? html`
            <span class="small muted">${t("Sunflow plant ihn nur, wenn er ladebereit ist – mit PV-Überschuss oder günstigem Strom –, und schaltet ihn sonst aus. Ladebereit heißt: die Entität „angesteckt“ ist an, oder eine kurze Probeladung nach dem Auslöser hat Leistung gezeigt. Sinkt die Leistung beim Laden unter die Schwelle, gilt er als voll oder abgesteckt. Geschaltet wird nur, wenn Sunflow den Verbraucher steuert.")}</span>
            <div class="fields">
              ${picker("connected_entity", t("Angesteckt (optional)"), this._candidates?.presence, t("keine"))}
              ${picker("soc_entity", t("Ladestand (optional)"), this._candidates?.percent, t("keiner"))}
              ${settings.soc_entity
                ? number("capacity_wh", t("Akkukapazität"), "Wh")
                : number("energy_wh", t("Energie je Ladung"), "Wh", status?.charges
                  ? t("Gelernt aus {count} Ladungen: {energy}", { count: status.charges, energy: formatValue(status.energy_wh, "Wh") })
                  : t("Startwert, bis Sunflow es aus den ersten Ladungen gelernt hat"))}
              ${number("done_below_w", t("Voll oder abgesteckt unter"), "W")}
              ${number("done_after_min", t("… für"), "min")}
              ${settings.soc_entity
                ? nothing
                : number("needs_above_w", t("Ladebedarf ab"), "W",
                  t("Erst ab dieser Leistung gilt er bei der Probeladung als ladebereit. Ein voller Akku, der angesteckt blieb, zieht nur wenig für die Selbstentladung – das wird nicht eingeplant. Leer: wie „Voll oder abgesteckt unter“."))}
            </div>
            ${settings.connected_entity
              ? nothing
              : html`<div class="fields">
                  ${picker("trigger_entity", t("Auslöser für die Probeladung"), this._candidates?.triggers, t("keiner"))}
                  <label class="field">
                    <span class="field-label">${t("bei Zustand")}</span>
                    <span class="input-unit"><input .value=${settings.trigger_state ?? ""} placeholder=${t("jede Änderung")}
                      @input=${(e) => set("trigger_state", e.target.value.trim() || null)}></span>
                    <span class="small muted">${t("z. B. locked, off, closed")}</span>
                  </label>
                  ${number("trigger_delay_min", t("danach warten"), "min")}
                  ${number("test_seconds", t("Probeladung"), "s")}
                </div>`}
            ${status
              ? html`<span class="small ${status.ready ? "" : "muted"}">${status.testing
                ? t("Probeladung läuft …")
                : status.note ?? (status.ready ? t("Ladebereit") : t("Nicht ladebereit"))}${status.since && !status.testing ? ` · ${at(status.since)}` : ""}${status.test_due
                  ? ` · ${t("Probeladung um {time}", { time: at(status.test_due) })}`
                  : ""}</span>`
              : nothing}
            ${edit
              ? html`<div class="actions"><button class="ok" @click=${() => this._saveCharger(device, edit)}>${t("Speichern")}</button>
                  <button class="secondary" @click=${() => this._dropChargerEdit(device)}>${t("Abbrechen")}</button></div>`
              : nothing}`
          : nothing}` : nothing}
      </div>
    `;
  }

  _dropChargerEdit(device) {
    const edits = { ...this._chargerEdits };
    delete edits[device.id];
    this._chargerEdits = edits;
  }

  async _saveCharger(device, settings) {
    try {
      await api.setDeviceCharger(device.id, settings);
      this._dropChargerEdit(device);
      this._configDevices = configuredDevices(await api.config());
      this._chargers = (await api.chargers()).devices;
      this._error = null;
    } catch (err) {
      this._error = t("Einstellung nicht gespeichert: {error}", { error: err.message });
    }
  }

  /** Explicit permission to reload the device's integration when it stays silent. */
  _renderReload(device) {
    const allowed = device.reload_after_min !== null && device.reload_after_min !== undefined;
    const status = this._reloads?.[device.id];
    const at = (iso) => weekdayTime(iso);
    return html`
      <div class="field reload">
        <span class="field-label">${t("Integration neu laden")}</span>
        <span class="choices">
          <label class="inline wrap">
            <input type="checkbox" .checked=${allowed} ?disabled=${this._busy.has(device.ha_device_id)}
              @change=${(e) => this._setReload(device, e.target.checked ? device.reload_after_min ?? 10 : null, e.target)}>
            ${t("erlaubt, wenn das Gerät keine Werte mehr liefert")}
          </label>
          ${allowed
            ? html`<span class="input-unit reload-minutes"><span class="muted">${t("nach")}</span>
                <input type="number" min="10" max="240" step="5" aria-label=${t("Minuten ohne Werte")} .value=${String(device.reload_after_min)}
                  @change=${(e) => this._setReload(device, Number(e.target.value), e.target)}>
                <span class="muted">min</span></span>`
            : nothing}
        </span>
        <span class="small muted">${t("Manche Integrationen verbinden sich nach einem Ausfall nicht von selbst neu, z. B. nach einem Neustart von Home Assistant. Sunflow lädt dann den Eintrag der Integration in Home Assistant neu – höchstens alle 30 Minuten und 6-mal am Tag. An das Gerät wird dabei nichts geschrieben; alle Geräte dieser Integration verbinden sich neu.")}</span>
        ${status?.silent_since ? html`<span class="small warn-text">${t("Liefert seit {time} keine Werte", { time: at(status.silent_since) })}</span>` : nothing}
        ${status?.last_reload ? html`<span class="small muted">${t("Zuletzt neu geladen: {time}", { time: at(status.last_reload) })}</span>` : nothing}
      </div>
    `;
  }

  async _setReload(device, minutes, input) {
    if (minutes !== null && !(minutes >= 10 && minutes <= 240)) {
      this._error = t("Minuten ohne Werte: erlaubt sind 10 bis 240.");
      input.value = String(device.reload_after_min ?? 10);
      return;
    }
    try {
      await api.setDeviceReload(device.id, minutes);
      this._configDevices = configuredDevices(await api.config());
      this._error = null;
    } catch (err) {
      this._error = t("Einstellung nicht gespeichert: {error}", { error: err.message });
    }
  }

  _renderDeviceFacts(device) {
    const params = (this._params[device.id] ?? []).filter((p) => this._advanced || !p.advanced);
    const capabilities = this._capabilitiesOf(device);
    const edits = this._paramEdits[device.id] ?? {};
    const nameEdit = this._nameEdits[device.id];
    const changed = Object.keys(edits).length > 0 || nameEdit !== undefined;
    return html`
      <div class="facts">
        <div class="field-label">${t("Kenndaten")}</div>
        <div class="fields">
          <label class="field">
            <span class="field-label">${t("Bezeichnung")}</span>
            <span class="input-unit">
              <input .value=${nameEdit ?? device.name}
                @input=${(e) => (this._nameEdits = { ...this._nameEdits, [device.id]: e.target.value })}>
            </span>
          </label>
          ${capabilities.includes("loadpoint") && device.profile.startsWith("evcc_intg/")
            ? html`<label class="field">
                <span class="field-label">${t("Art des Ladepunkts")}</span>
                <select @change=${(e) => this._setKind(device, e.target.value || null)}>
                  <option value="" ?selected=${!device.kind}>${t("automatisch (aus der Einheit des Ladestands)")}</option>
                  <option value="vehicle" ?selected=${device.kind === "vehicle"}>${t("Wallbox (Fahrzeug)")}</option>
                  <option value="heater" ?selected=${device.kind === "heater"}>${t("Wärme (z. B. Heizstab im Warmwasserspeicher)")}</option>
                </select>
              </label>`
            : nothing}
          ${params.map((p) => this._renderParameterField(device, p, edits))}
        </div>
        ${this._params[device.id] === undefined ? html`<p class="muted small">${t("Lade Kenndaten …")}</p>` : nothing}
        <div class="actions">
          <button class="ok" ?disabled=${!changed || this._busy.has(device.id)} @click=${() => this._saveDevice(device)}>
            ${t("Kenndaten speichern")}
          </button>
          <span class="spacer"></span>
          <button class="secondary danger" ?disabled=${this._busy.has(device.id)} @click=${() => this._removeDevice(device)}>
            ${t("Entfernen")}
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
        <h1>${t("Verbraucher")}</h1>
        <p class="muted">${t("Wie Sunflow Warmwasser, eigene Verbraucher und Wallboxen einplant. Fahrzeuge an Wallboxen haben ihre eigenen Vorgaben unter „Laden“; die hier gelten für eine Wallbox ohne zugeordnete Fahrzeuge.")}</p>
      </div>
      ${consumers.length === 0 ? html`<p class="muted">${t("Keine steuerbaren Verbraucher eingerichtet.")}</p>` : nothing}
      ${CATEGORIES.map(([key, title]) => {
        const members = consumers.filter((d) => this._configuredCategory(d) === key);
        return members.length
          ? html`<h2 class="category">${title}</h2>${members.map((d) => html`
              <section class="device">
                <div class="device-head"><div class="device-title">${d.name}</div>
                  ${this._flexEdits[d.id] !== undefined ? html`<span class="chip warn">${t("nicht gespeichert")}</span>` : nothing}</div>
                ${this._renderFlexSettings(d, this._params[d.id] ?? [])}
                <div class="actions">
                  <button class="ok" ?disabled=${this._flexEdits[d.id] === undefined || this._busy.has(d.id)}
                    @click=${() => this._saveDevice(d)}>${t("Speichern")}</button>
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
        <div class="device-title">${t("Einspeisevergütung")}</div>
        ${plants.map((device) => {
          const feedEdit = this._feedEdits[device.id] ?? {};
          return html`
            <div class="fields">
              <div class="field">
                <span class="field-label">${device.name}</span>
                <select aria-label=${t("Vergütung {name}", { name: device.name })} @change=${(e) => this._editFeedIn(device.id, "model", e.target.value)}>
                  ${Object.entries(FEED_IN_MODELS).map(
                    ([value, label]) => html`<option value=${value}
                      ?selected=${(feedEdit.model ?? device.feed_in?.model ?? "eeg_fixed") === value}>${label}</option>`,
                  )}
                </select>
              </div>
              <label class="field">
                <span class="field-label">${t("Vergütung")}</span>
                <span class="input-unit">
                  <input inputmode="decimal" placeholder=${t("z. B. 8,2")}
                    .value=${feedEdit.rate ?? toDisplay(device.feed_in?.rate_ct_per_kwh, "ct")}
                    @input=${(e) => this._editFeedIn(device.id, "rate", e.target.value)}>
                  <span class="muted">ct/kWh</span>
                </span>
              </label>
              <div class="field">
                <span class="field-label">&nbsp;</span>
                <button class="ok" ?disabled=${this._feedEdits[device.id] === undefined || this._busy.has(device.id)}
                  @click=${() => this._saveDevice(device)}>${t("Speichern")}</button>
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
      cost: [t("Kostenoptimal"), t("PV-Überschuss oder günstige Zeiten")],
      pv_only: [t("Nur PV-Überschuss"), t("nie aus dem Netz oder dem Hausspeicher")],
      fast: [t("Schnellstmöglich"), t("sofort mit voller Leistung")],
    };
    return html`
      <fieldset class="flex-settings">
        <legend class="field-label">${t("Planung")}</legend>
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
                <span class="field-label">${kind === "heater" ? t("Zieltemperatur") : t("Ziel-Ladestand")}</span>
                <span class="input-unit">
                  <input inputmode="decimal" placeholder=${t("aus evcc")} .value=${flex.target === null || flex.target === undefined ? "" : toDisplay(flex.target, "")}
                    @input=${(e) => set("target", fromDisplay(e.target.value, ""))}>
                  <span class="muted">${kind === "heater" ? "°C" : "%"}</span>
                </span>
                <span class="small muted">${t("Leer = Wert aus evcc. Ein aktiver evcc-Ladeplan hat Vorrang.")}</span>
              </label>
              <label class="field">
                <span class="field-label">${t("Höchstes Ziel von Hand")}</span>
                <span class="input-unit">
                  <input inputmode="decimal" placeholder=${kind === "heater" ? "60" : "100"}
                    .value=${flex.target_max === null || flex.target_max === undefined ? "" : toDisplay(flex.target_max, "")}
                    @input=${(e) => set("target_max", fromDisplay(e.target.value, ""))}>
                  <span class="muted">${kind === "heater" ? "°C" : "%"}</span>
                </span>
                <span class="small muted">${kind === "heater"
                  ? t("Bis hierhin lässt sich das Ziel in Steuerung und Live wählen. Leer = 60 °C; darüber Verbrühungsgefahr am Wasserhahn.")
                  : t("Bis hierhin lässt sich das Ziel in Steuerung und Live wählen. Leer = 100 %.")}</span>
              </label>`
            : nothing}
          <label class="field">
            <span class="field-label">${t("Fertig bis (täglich)")}</span>
            <span class="input-unit">
              <input type="time" .value=${flex.ready_by ?? ""} @change=${(e) => set("ready_by", e.target.value || null)}>
            </span>
            <span class="small muted">${kind === "vehicle" ? t("Leer = bis zum Ende des Planungszeitraums.") : t("Leer = innerhalb von 24 h.")}</span>
          </label>
          ${kind === "heater"
            ? html`<label class="field">
                <span class="field-label">${t("Alternative Wärmequelle (Fernwärme, Gas …)")}</span>
                <span class="input-unit">
                  <input inputmode="decimal" placeholder=${t("keine")}
                    .value=${flex.alternative_cost_ct === null || flex.alternative_cost_ct === undefined ? "" : toDisplay(flex.alternative_cost_ct, "ct")}
                    @input=${(e) => set("alternative_cost_ct", fromDisplay(e.target.value, "ct"))}>
                  <span class="muted">${t("ct/kWh Wärme")}</span>
                </span>
                <span class="small muted">${t("Elektrisch geheizt wird nur, wenn es günstiger ist; den Rest übernimmt die Alternative.")}</span>
              </label>`
            : nothing}
          <label class="field">
            <span class="field-label">${t("Strom aus dem Hausspeicher")}</span>
            <span class="inline wrap">
              <input type="checkbox" .checked=${flex.uses_battery} @change=${(e) => set("uses_battery", e.target.checked)}>
              ${t("Hausspeicher darf in diesen Verbraucher entladen")}
            </span>
            <span class="small muted">${kind === "heater"
              ? t("Aus: der Speicher versorgt nur das Haus; geheizt wird dann nur aus PV-Überschuss oder dem Netz. Gleichzeitiges Laden des Hausspeichers ist immer erlaubt.")
              : t("Aus: der Speicher versorgt nur das Haus; geladen wird dann nur aus PV-Überschuss oder dem Netz. Gleichzeitiges Laden des Hausspeichers ist immer erlaubt.")}</span>
          </label>
        </div>
      </fieldset>
    `;
  }

  _renderParameterField(device, param, edits) {
    if (param.name === "phase_mode") return this._renderPhaseMode(device, param, edits);
    const edited = edits[param.name];
    const value = edited ?? toDisplay(param.stored, param.unit);
    // Empty (reset) means the value from the sensors or the profile, not the one stored so far
    const placeholder = toDisplay(param.derived ?? param.default ?? param.effective, param.unit);
    let source;
    if (param.stored !== null && edited === undefined) source = html`<span class="muted">${t("eigener Wert")}</span>`;
    else if (param.derived !== null)
      source = html`<span class="ok-text">${t("aus Sensoren: {value}", { value: `${toDisplay(param.derived, param.unit)} ${displayUnit(param.unit)}` })}</span>`;
    else if (param.default !== null)
      source = html`<span class="muted">${t("Vorgabe: {value}", { value: `${toDisplay(param.default, param.unit)} ${displayUnit(param.unit)}` })}</span>`;
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
            ? html` · <button class="link" @click=${() => this._editParam(device.id, param.name, "")}>${t("zurücksetzen")}</button>`
            : nothing}
        </span>
      </label>
    `;
  }

  /** Phase switching as a choice instead of a number (0 automatic, 1 or 3 phases always). */
  _renderPhaseMode(device, param, edits) {
    const value = Number(edits[param.name] ?? param.effective ?? 0);
    return html`
      <label class="field">
        <span class="field-label">${paramLabel(param.name)}</span>
        <select @change=${(e) => this._editParam(device.id, param.name, e.target.value)}>
          <option value="0" ?selected=${value === 0}>${t("automatisch (Sunflow schaltet 1 ↔ 3 Phasen)")}</option>
          <option value="3" ?selected=${value === 3}>${t("immer 3 Phasen")}</option>
          <option value="1" ?selected=${value === 1}>${t("immer 1 Phase")}</option>
        </select>
        <span class="small muted">${t("Automatisch lädt das Auto schon bei wenig PV-Überschuss (1 Phase ab ≈ 1,4 kW) und schaltet bei genug Sonne auf 3 Phasen.")}</span>
      </label>
    `;
  }

  _renderEfficiencyHint(device, param) {
    const estimate = this._efficiency[device.id];
    if (estimate === undefined) return nothing;
    if (estimate === null) return html`<span class="small muted">${t("Noch keine Historie für eine Schätzung.")}</span>`;
    const pct = (v) => `${decimal((v * 100).toFixed(1))} %`;
    // The parameter is the round trip of the conversion; a constant standby draw does not
    // depend on how much is cycled and is left out when the history allows the split
    const split = estimate.conversion_round_trip !== null && estimate.conversion_round_trip !== undefined;
    const value = Math.round((split ? estimate.conversion_round_trip : estimate.round_trip) * 1000) / 10;
    return html`
      ${split
        ? html`<span class="small">
            ${t("Aufgeteilt: Umwandlung {conversion} Zyklus und Eigenverbrauch der Batterie etwa {standby} (unabhängig vom Laden) – für die Planung zählt die Umwandlung. Die Wandlung im Wechselrichter ist ein eigener Wert.", {
              conversion: pct(estimate.conversion_round_trip),
              standby: formatValue(estimate.standby_w, "W"),
            })}
          </span>`
        : nothing}
      <span class="small">
        ${estimate.source === "counters" ? t("aus Energiezählern") : t("aus Historie")}: <strong>${pct(estimate.round_trip)}</strong>
        ${t("Zyklus (je Richtung {efficiency}), {cycles} Zyklen in {hours} h", {
          efficiency: pct(estimate.efficiency),
          cycles: decimal(estimate.cycles.toFixed(1)),
          hours: Math.round(estimate.hours),
        })}
        ${estimate.plausible
          ? html` · <button class="link" @click=${() => this._editParam(device.id, param.name, decimal(value))}>
              ${estimate.reliable ? t("übernehmen") : t("vorläufig übernehmen")}</button>`
          : nothing}
      </span>
      ${!estimate.plausible
        ? html`<span class="small warn-text">${t("Unplausibel – vermutlich Messfehler der Batterieleistung oder falsche Kapazität. Wird nicht angeboten.")}</span>`
        : !estimate.reliable
          ? html`<span class="small muted">${t("Vorläufig – belastbar erst ab {cycles} vollen Zyklen. Enthält auch den Eigenverbrauch des Speichers.", { cycles: estimate.min_cycles })}</span>`
          : html`<span class="small muted">${t("Enthält auch den Eigenverbrauch des Speichers (z. B. DC/DC-Wandler der Module).")}</span>`}
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
        <h1>${t("Bilanzprüfung")}</h1>
        <p class="muted">${t("Wie hängen deine Geräte zusammen? Sunflow prüft damit die Messwerte gegeneinander und zählt keine PV-Leistung doppelt.")}</p>
      </div>
      ${pvOnly.length && measuring.length
        ? html`
            <section class="device">
              <div class="device-title">${t("Zusammenhang der Anlagen")}</div>
              ${pvOnly.map(
                (d) => html`
                  <div class="field wide">
                    <span class="field-label">${t("{name} wird zusätzlich gemessen von", { name: d.name })}</span>
                    <sf-picker label=${t("{name} wird zusätzlich gemessen von", { name: d.name })} hide-values
                      empty-label=${t("niemandem (eigene Einspeisung)")} .value=${d.measured_by?.device_id ?? ""}
                      .options=${measuring.map((m) => ({ value: m.id, label: t("{name} als „PV extern“", { name: m.name }) }))}
                      @change=${(e) => this._setMeasuredBy(d, e.detail.value)}></sf-picker>
                  </div>
                `,
              )}
            </section>
          `
        : nothing}
      ${this._renderMeasurements(byId)}
      ${site === null ? html`<p class="muted">${t("Lade Anlagenstatus …")}</p>` : this._renderSiteStatus(site, byId)}
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
            <div>${c.plausible === null ? t("Gegenprobe nicht möglich") : c.plausible ? `✓ ${t("Gegenprobe passt")}` : `⚠ ${t("Gegenprobe weicht ab")}`}:
              ${own} ${formatValue(c.own_value, "W")} · ${t("gemessen von {name}", { name: other })} ${formatValue(c.measured_value, "W")}</div>
            ${c.deviation !== null ? html`<div class="small">${t("Abweichung {value}", { value: formatValue(Math.abs(c.deviation), "W") })}</div>` : nothing}
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
                ${d.balance ? (d.balance.plausible ? `✓ ${t("Gerätebilanz plausibel")}` : `⚠ ${t("Gerätebilanz unplausibel")}`) : t("PV-Leistung")}
              </div>
            </div>
          `,
        )}
        <div class="tile total">
          <div class="small muted">${t("PV gesamt")}</div>
          <div class="value">${formatValue(site.pv_total, "W")}</div>
          <div class="small muted">${t("ohne Doppelzählung")}</div>
        </div>
      </div>
    `;
  }

  _renderSources() {
    return html`${this._renderSourcesBody()}${this._renderFeedIns()}`;
  }

  _renderSourcesBody() {
    const sources = this._sources;
    if (sources === null) return html`<h1>${t("Tarif und Prognose")}</h1><p class="muted">${t("Suche Quellen …")}</p>`;
    return html`
      <div class="heading">
        <h1>${t("Tarif und Prognose")}</h1>
        <p class="muted">${t("Strompreis und PV-Prognose kommen aus deinen vorhandenen Integrationen.")}</p>
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
        <div class="device-title">${t("Bezugspreis")}</div>
        ${sources.prices.length === 0
          ? html`<p class="small muted">${t("Keine Preis-Entität gefunden (Einheit €/kWh oder ct/kWh) – du kannst einen festen Preis angeben.")}</p>`
          : nothing}
        ${html`
              <div class="field wide">
                <span class="field-label">${t("Quelle")}</span>
                <sf-picker label=${t("Quelle Bezugspreis")} empty-label=${t("– keine (nur Eigenverbrauch) –")} .value=${selected ?? ""}
                  .options=${[
                    { value: FIXED_PRICE, label: t("Fester Preis …"), hideValue: true },
                    ...sources.prices.map((p) => ({
                      value: p.entity_id,
                      label: p.name,
                      detail: [PRICE_ADAPTERS[p.adapter] ?? p.adapter, p.current !== null ? formatCt(p.current) : null].filter(Boolean).join(" · "),
                    })),
                  ]}
                  @change=${(e) => this._setTariff(e.detail.value)}></sf-picker>
              </div>
            `}
        ${fixed
          ? html`
              <div class="fields">
                <label class="field">
                  <span class="field-label">${t("Arbeitspreis brutto")}</span>
                  <span class="input-unit">
                    <input inputmode="decimal" placeholder=${t("z. B. 32,5")}
                      .value=${this._fixedPriceEdit ?? toDisplay(sources.tariff.fixed_price_ct, "ct")}
                      @input=${(e) => (this._fixedPriceEdit = e.target.value)}>
                    <span class="muted">ct/kWh</span>
                  </span>
                </label>
              </div>
              <div class="actions">
                <button class="ok" ?disabled=${this._fixedPriceEdit === undefined} @click=${() => this._saveFixedPrice()}>
                  ${t("Preis speichern")}
                </button>
              </div>
            `
          : nothing}
        ${this._priceSeries?.exchange && !fixed ? this._renderSurcharges(sources.tariff) : nothing}
        ${points.length && !fixed ? this._renderPriceSummary(points) : nothing}
        ${points.length && !fixed
          ? html`<figure>
              <figcaption class="small muted">${t("Bezugspreis nächste {hours} h in ct/kWh", { hours: Math.round(points.length / 4) })}</figcaption>
              ${priceChart(points, t("Bezugspreis-Verlauf"))}
            </figure>`
          : nothing}
        ${this._priceSeries?.adapter === "fixed" && !fixed
          ? html`<div class="alert warn">⚠ ${t("Diese Quelle liefert nur den aktuellen Preis, keinen Verlauf. Die Planung nimmt ihn als konstant an.")}</div>`
          : nothing}
      </section>
    `;
  }

  /** Exchange prices: what comes on top until it is the price paid. */
  _renderSurcharges(tariff) {
    const edit = this._surchargeEdit ?? {};
    const set = (key, value) => (this._surchargeEdit = { ...edit, [key]: value });
    return html`
      <p class="small muted">${t("Diese Quelle liefert den reinen Börsenpreis. Für die Planung zählt, was du zahlst: Börsenpreis plus Netzentgelt, Umlagen und Gebühr des Anbieters, darauf die Mehrwertsteuer. Die Werte stehen auf deiner Rechnung bzw. im Preisblatt des Anbieters.")}</p>
      <div class="fields">
        <label class="field">
          <span class="field-label">${t("Aufschlag netto")}</span>
          <span class="input-unit">
            <input inputmode="decimal" placeholder=${t("z. B. 17,5")}
              .value=${edit.exchange_surcharge_ct ?? toDisplay(tariff.exchange_surcharge_ct, "ct")}
              @input=${(e) => set("exchange_surcharge_ct", e.target.value)}>
            <span class="muted">ct/kWh</span>
          </span>
        </label>
        <label class="field">
          <span class="field-label">${t("Mehrwertsteuer")}</span>
          <span class="input-unit">
            <input inputmode="decimal" placeholder="19"
              .value=${edit.exchange_vat_pct ?? toDisplay(tariff.exchange_vat_pct, "%")}
              @input=${(e) => set("exchange_vat_pct", e.target.value)}>
            <span class="muted">%</span>
          </span>
        </label>
      </div>
      <div class="actions">
        <button class="ok" ?disabled=${this._surchargeEdit === undefined} @click=${() => this._saveSurcharges()}>
          ${t("Aufschläge speichern")}
        </button>
      </div>
      ${tariff.exchange_surcharge_ct == null
        ? html`<div class="alert warn">⚠ ${t("Ohne Aufschläge plant Sunflow mit dem Börsenpreis allein – Netzbezug erscheint viel zu günstig.")}</div>`
        : nothing}
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
        <div class="tile"><div class="small muted">${t("Jetzt")}</div><div class="value">${formatCt(now.price)}</div></div>
        <div class="tile"><div class="small muted">${t("Günstigster Preis")}</div><div class="value">${formatCt(min)}</div>
          <div class="small muted">${window}</div></div>
        <div class="tile"><div class="small muted">${t("Höchster Preis")}</div>
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
        <div class="device-title">${t("PV-Prognose")}</div>
        ${sources.forecast_planes.length === 0
          ? html`<p class="muted">${t("Keine Prognose gefunden. Unterstützt werden Solcast und Forecast.Solar.")}</p>`
          : html`
              <p class="small muted">${t("Ordne jede Prognosefläche der Anlage zu, deren Module dort liegen. Gibt es eine Dachfläche bei mehreren Anbietern, verwende sie nur einmal – sonst wird sie doppelt gezählt.")}</p>
              <div class="fields">
                ${sources.forecast_planes.map(
                  (plane) => html`
                    <div class="field">
                      <span class="field-label">${FORECAST_SOURCES[plane.source] ?? plane.source} · ${plane.name}${plane.capacity_kw ? ` (${formatValue(plane.capacity_kw * 1000, "W")})` : ""}</span>
                      <span class="small muted">${plane.today_kwh !== null ? t("heute {energy} kWh", { energy: toDisplay(Math.round(plane.today_kwh * 10) / 10, "kWh") }) : t("keine Tagesprognose lesbar")}</span>
                      <span class="input-unit">
                        <input aria-label=${t("Name der Fläche")} placeholder=${plane.detected_name} .value=${plane.custom_name ?? ""}
                          @change=${(e) => this._renamePlane(plane, e.target.value)}>
                      </span>
                      <sf-picker label=${t("Anlage für {name}", { name: plane.name })} hide-values empty-label=${t("nicht verwenden")}
                        .value=${plane.device_id ?? ""} .options=${plants.map((p) => ({ value: p.id, label: p.name }))}
                        @change=${(e) => this._assignPlane(plane, e.detail.value)}></sf-picker>
                      ${this._renderPlaneStrings(plane, sources.pv_strings ?? [])}
                    </div>
                  `,
                )}
              </div>
            `}
        ${series.length
          ? html`
              <figure>
                <figcaption class="small muted">${t("PV-Prognose nächste 48 h je Fläche (gestapelt)")}</figcaption>
                <div class="legend">
                  ${series.map(
                    (s) => html`<span class="legend-item"><span class="swatch" style="background:${s.color}"></span>
                      ${s.name} → ${s.plant} · ${decimal(s.kwh.toFixed(1))} kWh</span>`,
                  )}
                </div>
                ${forecastChart(series, t("PV-Prognose je Fläche"))}
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
        <h1>${t("Gerätetest")}</h1>
        <p class="muted">${t("Ein kurzer Test prüft, ob die Steuerung beim Wechselrichter ankommt und die Vorzeichen stimmen. Danach geht der Speicher zurück auf Eigenverbrauch.")}</p>
      </div>
      ${devices.length === 0
        ? html`<p class="muted">${t("Kein steuerbares Gerät übernommen – für reine PV-Wechselrichter ist kein Test nötig.")}</p>`
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
            <div class="muted small">${t("Profil {profile}", { profile: device.profile })}</div>
          </div>
          ${passedAt ? html`<span class="chip ok">✓ ${t("Bestanden {time}", { time: weekdayTime(passedAt.toISOString()) })}</span>` : nothing}
        </div>
        ${running ? nothing : this._renderTestPlan(device, confirmed)}
        ${test ? this._renderTestProgress(device, test) : nothing}
      </section>
    `;
  }

  _renderTestPlan(device, confirmed) {
    return html`
      <ol class="plan small">
        <li>${t("Ausgangszustand 20 s messen (nur lesen).")}</li>
        <li>${t("Falls der Wechselrichter nicht im passiven Modus ist: dorthin umschalten (einmaliger EEPROM-Schreibzugriff).")}</li>
        <li>${t("Je nach Zustand: Laden bzw. Entladen auf 300 W begrenzen oder 500 W aus dem Netz laden – 60 s lang. Dafür werden nur die Werte des passiven Modus geschrieben (RAM).")}</li>
        <li>${t("Prüfen, ob die Batterie dem Befehl in der erwarteten Richtung folgt.")}</li>
        <li>${t("Zurück auf Eigenverbrauch, ggf. zurück in den vorherigen Modus, 30 s beobachten. Bei Abbruch oder Fehler wird sofort zurückgesetzt.")}</li>
      </ol>
      <label class="inline confirm">
        <input type="checkbox" .checked=${confirmed}
          @change=${(e) =>
            (this._testConfirm = e.target.checked
              ? new Set([...this._testConfirm, device.id])
              : this._without(this._testConfirm, device.id))}>
        ${t("Automationen anderer Systeme, die den passiven Modus schreiben, sind pausiert.")}
      </label>
      <div class="actions">
        <button class="primary" ?disabled=${!confirmed} @click=${() => this._startTest(device)}>
          ${t("Test starten (schreibt in den Wechselrichter)")}
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
        ${test.expectation ? html`<div class="small">${t("Erwartung: {expectation}", { expectation: test.expectation })}</div>` : nothing}
      </div>
      ${test.running
        ? html`
            <ol class="phases small">
              ${[t("Ausgangszustand"), t("Testbefehl"), t("Zurücksetzen"), t("Beobachten")].map(
                (label, i) => html`<li class=${i < current ? "done" : i === current ? "active" : ""}>${label}</li>`,
              )}
            </ol>
            <div class="actions">
              <button class="secondary" @click=${() => this._abortTest(device)}>${t("Abbrechen und zurücksetzen")}</button>
            </div>
          `
        : nothing}
      ${lastSamples.length
        ? html`<div class="small muted">${t("Batterie zuletzt:")}
            ${lastSamples.map((s) => (s.battery_power === null ? "–" : formatSigned(s.battery_power, "W"))).join(" · ")}
          </div>`
        : nothing}
      ${finished
        ? html`<ul class="results">
            ${result(test.control_works, t("Steuerung wirkt"), t("Batterie folgt dem Befehl nicht"))}
            ${result(test.sign_correct, t("Vorzeichen korrekt (+ = Laden)"), t("Vorzeichen falsch – in Schritt 2 „umkehren“ setzen"))}
            ${result(test.restored, t("Zurück auf Eigenverbrauch"), t("Zurücksetzen fehlgeschlagen – bitte in HA prüfen!"))}
            ${test.previous_mode
              ? result(
                  test.previous_mode_restored === true,
                  t("Modus zurück auf „{mode}“", { mode: test.previous_mode }),
                  t("Modus nicht zurückgesetzt – bitte in HA auf „{mode}“ stellen!", { mode: test.previous_mode }),
                )
              : nothing}
          </ul>`
        : nothing}
      ${test.error ? html`<div class="alert error">${test.error}</div>` : nothing}
      ${this._advanced && test.calls.length
        ? html`<div class="table-wrap"><table>
            <thead><tr><th>${t("Aktion")}</th><th>${t("Entität")}</th><th>${t("Daten")}</th><th>${t("Status")}</th></tr></thead>
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
    if (summary === null) return html`<h1>${t("Fertig")}</h1><p class="muted">${t("Lade Zusammenfassung …")}</p>`;
    const counts = { error: 0, warning: 0, info: 0 };
    for (const issue of summary.issues) counts[issue.level] += 1;
    const priceName = summary.tariff
      ? (this._sources?.prices.find((p) => p.entity_id === summary.tariff)?.name ?? summary.tariff)
      : summary.fixed_price_ct != null
        ? t("Fester Preis {price} ct/kWh", { price: toDisplay(summary.fixed_price_ct, "ct") })
        : t("keiner – nur Eigenverbrauch");
    return html`
      <div class="heading">
        <h1>${summary.completed ? t("Einrichtung abgeschlossen") : t("Fast fertig")}</h1>
        <p class="muted">
          ${summary.completed
            ? t("Sunflow ist eingerichtet und plant im Hintergrund. Ergebnisse siehst du unter Live, Steuerung und Planung.")
            : counts.error
              ? t("Bitte behebe die markierten Punkte, dann kannst du die Einrichtung abschließen.")
              : t("Prüfe die Übersicht und schließe die Einrichtung ab.")}
        </p>
      </div>
      ${summary.issues.length
        ? html`
            <ul class="issues">
              ${summary.issues.map(
                (i) => html`
                  <li class="alert ${ISSUE_STYLES[i.level].css}">
                    <span>${ISSUE_STYLES[i.level].icon} ${i.message}</span>
                    <button class="link" @click=${() => this._goTo(i.step)}>${t("zu „{area}“", { area: STEPS[stepIndex(i.step)][1] })}</button>
                  </li>
                `,
              )}
            </ul>
          `
        : html`<div class="alert ok">✓ ${t("Alles eingerichtet.")}</div>`}
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
          <strong>${t("Bezugspreis")}</strong>
          <div class="small muted">${priceName}</div>
        </li>
        <li>
          <strong>${t("PV-Prognose")}</strong>
          <div class="small muted">
            ${summary.forecast_planes.length
              ? summary.forecast_planes.map((p) => `${p.name} → ${p.plant ?? t("nicht zugeordnet")}`).join(" · ")
              : t("keine")}
          </div>
        </li>
      </ul>
      ${summary.completed
        ? html`<span class="chip ok">✓ ${t("Abgeschlossen")}</span>`
        : html`<div class="actions">
            <button class="ok" ?disabled=${!summary.can_complete} @click=${() => this._completeSetup()}>
              ${t("Einrichtung abschließen")}
            </button>
            ${counts.warning ? html`<span class="small muted">${t("{count} Hinweise kannst du auch später erledigen.", { count: counts.warning })}</span>` : nothing}
          </div>`}
    `;
  }

  _summaryDetails(device) {
    const details = [];
    const p = device.parameters;
    if (p.capacity_wh) details.push(`${toDisplay(p.capacity_wh, "Wh")} kWh`);
    if (p.min_soc !== undefined) details.push(t("min. {value}", { value: formatValue(p.min_soc, "%") }));
    if (p.max_charge) details.push(t("Laden ≤ {value}", { value: formatValue(p.max_charge, "W") }));
    if (p.max_discharge) details.push(t("Entladen ≤ {value}", { value: formatValue(p.max_discharge, "W") }));
    if (device.feed_in) {
      const rate = device.feed_in.rate_ct_per_kwh;
      details.push(`${FEED_IN_MODELS[device.feed_in.model]}${rate !== null ? ` ${toDisplay(rate, "ct")} ct/kWh` : ""}`);
    }
    if (device.measured_by) details.push(t("gemessen von {name} (PV extern)", { name: device.measured_by }));
    if (device.overrides.length) details.push(t("{count} angepasste Rollen", { count: device.overrides.length }));
    details.push(t("Profil {profile}", { profile: device.profile }));
    return details;
  }

  _renderComponents(device) {
    return html`
      <div class="small muted">
        ${t("Komponenten: {list}", { list: device.components.map((c) => `${c.name} (${c.model ?? "?"})`).join(", ") })}
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
    .nav-more { display: none; }
    .settings-search { position: relative; margin-bottom: 4px; }
    .settings-search input { width: 100%; box-sizing: border-box; min-height: 40px; }
    .search-results {
      position: absolute; z-index: 20; left: 0; right: 0; top: calc(100% + 4px); margin: 0; padding: 4px; list-style: none;
      background: var(--sf-surface); border: 1px solid var(--sf-border); border-radius: 10px;
      box-shadow: 0 6px 18px rgb(0 0 0 / 0.15); max-height: 60vh; overflow-y: auto;
    }
    .search-results li { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border-radius: 8px; cursor: pointer; }
    .search-results li.active, .search-results li:hover { background: var(--sf-primary-bg); color: var(--sf-primary-text); }
    .search-results li.search-empty { cursor: default; color: var(--sf-muted); }
    .search-results li.search-empty:hover { background: none; color: inherit; }
    .search-area { font-size: 12px; opacity: 0.75; }
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
      /* Steps as a compact row; during the first setup only the current one shows its name next
         to the numbers, afterwards (no numbers) all names, scrolling sideways */
      .sidebar { flex: 0 0 100%; }
      .sidebar nav { display: flex; align-items: center; gap: 4px; overflow-x: auto; min-width: 0; padding: 4px; }
      .step { width: auto; flex: none; min-height: 40px; padding: 4px 6px; white-space: nowrap; }
      .step:not(.active) .dot + span { display: none; }
      .nav-wrap { position: relative; }
      /* Arrows over the edges: more areas to swipe to */
      .nav-more {
        display: flex; position: absolute; top: 1px; bottom: 1px; z-index: 1; width: 36px; align-items: center;
        justify-content: center; border: 0; padding: 0; cursor: pointer; font-size: 26px; line-height: 1;
        color: var(--sf-primary);
      }
      .nav-more.left { left: 1px; border-radius: 12px 0 0 12px; background: linear-gradient(to right, var(--sf-surface) 60%, transparent); }
      .nav-more.right { right: 1px; border-radius: 0 12px 12px 0; background: linear-gradient(to left, var(--sf-surface) 60%, transparent); }
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
    .choices { display: flex; flex-wrap: wrap; gap: 6px 16px; }
    .inline.wrap { white-space: normal; }
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
    /* Its own block below name and actions, with its own buttons */
    .manual-item .charger { flex-basis: 100%; border-top: 1px solid var(--sf-border); padding-top: 12px; margin-top: 4px; }
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
