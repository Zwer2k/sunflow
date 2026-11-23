import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { fromDisplay, toDisplay } from "../lib/format.js";
import "../components/picker.js";
import { confirmDialog } from "../components/dialog.js";
import { decimal, locale, t } from "../lib/i18n.js";
import { lineChart, SERIES_COLORS } from "../components/charts.js";
import { chainDiagram, curveChart, diagramStyles, historyChart, plantDiagram } from "../components/heat-pump-diagrams.js";
import { toggleSheet } from "../lib/display-toggles.js";
import { isOpen, setOpen } from "../lib/collapse.js";

// Room temperature at which the cooling rate is shown (api/house.py)
const COOLING_INSIDE_C = 20;
// Where the outside temperature comes from (house.outside_source); a function so the texts follow the language
const OUTSIDE_SOURCES = () => ({
  auto: t("automatisch (eigener Sensor, sonst Wärmepumpe)"),
  heat_pump: t("Wärmepumpe: Außentemperatur"),
  heat_pump_unit: t("Wärmepumpe: Außengerät (TA2)"),
  learning_source: t("eigener Sensor (Lernquelle)"),
});
// Type of the heating surfaces (house.heating_surface): start value of the heating curve's exponent n
const SURFACES = () => ({
  underfloor: t("Fußbodenheizung"),
  mixed: t("gemischt (Fußboden und Heizkörper)"),
  radiator: t("Heizkörper"),
});
// Fast change of a room (house.fast_change, design/heat_pump.md 6): start values and allowed ranges
// [low, high, whole number] as in the backend (heat_pump/defaults.py)
const FAST_CHANGE_DEFAULTS = { start_k: 0.8, start_min: 20, settle_k: 0.5, settle_min: 30, max_h: 2 };
const FAST_CHANGE_RANGES = {
  start_k: [0.2, 5, false], start_min: [5, 120, true], settle_k: [0.1, 5, false], settle_min: [10, 180, true], max_h: [0.5, 12, false],
};
// Where the start slope of the heating curve comes from (design/heat_pump.md, 8)
const SLOPE_SOURCES = () => ({ heat_pump: t("aus der Wärmepumpe gelesen"), surface: t("Startwert des Heizflächentyps"), user: t("eingestellt"), learned: t("aus dem Gelernten übernommen") });
// Status of the latest values; refreshed while the view is open
const STATUS_EVERY_MS = 60_000;
// Days the counts of the writes Sunflow would have caused cover
const WRITES_DAYS = 7;
// Hours the history can show, and how often it is read again (its rows are 5 minute means)
const HISTORY_HOURS = [6, 24, 72];
const HISTORY_EVERY_MS = 5 * 60_000;
/** A temperature or kelvin value with one decimal; "–" when unknown. */
const one = (v) => (v == null || !Number.isFinite(Number(v)) ? "–" : decimal(Number(v).toFixed(1)));
/** A temperature without ",0" when it is whole (limits). */
const whole = (v) => (v != null && Number.isInteger(Number(v)) ? String(v) : one(v));
/** A signed difference in kelvin. */
const signed = (v) => (v == null ? "–" : `${signedNumber(v)} K`);
/** A signed number with one decimal. */
const signedNumber = (v) => (v == null ? "–" : `${v >= 0 ? "+" : "−"}${decimal(Math.abs(v).toFixed(1))}`);
/** A number with two decimals (slope). */
const two = (v) => (v == null || !Number.isFinite(Number(v)) ? "–" : decimal(Number(v).toFixed(2)));
// What the protection layer is doing (design/heat_pump.md, 11)
const GUARD_PHASES = () => ({
  idle: t("Kompressor aus, keine Wärme nötig"),
  off: t("Kompressor aus: wartet auf Wiederanlauf"),
  restart: t("Wiederanlauf"),
  hot_water: t("Warmwasser: nichts schreiben"),
  defrost: t("Abtauen: nichts schreiben"),
  starting: t("nach dem Start: abwarten"),
  running: t("Heizbetrieb"),
  blocked: t("Sperre: Wärmepumpe aus"),
  inactive: t("schreibt nicht"),
});
// Classes of a compressor stop (learned.py)
const STOP_KINDS = () => ({
  unexplained: t("Abschaltregel"),
  defrost: t("Abtauen"),
  hot_water: t("Warmwasser"),
  block: t("Sperre"),
  other: t("andere Aktivität"),
  pending: t("noch offen"),
});
// Reasons of a write Sunflow would cause
const WRITE_REASONS = () => ({
  normal: t("normal"),
  start: t("Wiederanlauf"),
  track: t("Nachführen"),
  block_on: t("Sperre an"),
  block_off: t("Sperre aus"),
});
// Why a value waits (write layer, 12) or a "write now" lapsed
const HELD = () => ({
  spacing: t("Mindestabstand"),
  budget: t("Budget erschöpft"),
  not_active: t("Regeln nicht eingeschaltet (nur Lernen)"),
  nothing_pending: t("kein ausstehender Wert"),
  inactive: t("kein gültiger Zustand zum Schreiben"),
});
// Which limit of the budget holds a value back (12)
const BUDGET_LIMITS = () => ({
  day: t("Tagesbudget erschöpft"),
  emergency: t("Notkontingent erschöpft"),
  hour: t("Höchstzahl je Stunde erreicht"),
});
// Why the block would be set
const BLOCK_REASONS = () => ({
  room_warm: t("Raum deutlich über dem Ziel"),
  foreign: t("nicht von Sunflow gesetzt"),
});
// Limits the flow target or the heat pump reached (10)
const LIMITS = () => ({
  limit_max: t("Stellwert an der Obergrenze"),
  limit_min: t("Stellwert an der Untergrenze"),
  power_max: t("Wärmepumpe an der Höchstleistung"),
  power_min: t("Wärmepumpe an der Mindestleistung"),
});
const VALUE_SOURCES = () => ({ start: t("Startwert"), learned: t("gelernt") });
/** Why Sunflow would write nothing, in words. */
function inactiveText(reason) {
  const fixed = {
    mode_off: t("Modus Aus"),
    unusable: t("Gerät nicht erreichbar oder passt nicht"),
    fault: t("Störung"),
    tv_missing: t("Vorlauf unbekannt"),
    setpoint_missing: t("Max T-Vorlauf unbekannt"),
    esp_regulation: t("ESP-Regelung aktiv"),
  }[reason];
  if (fixed) return fixed;
  if (reason.startsWith("strategy_")) return t("Strategie: {state}", { state: STRATEGIES()[reason.slice(9)] ?? reason.slice(9) });
  return frozenText(reason);
}
// Canonical states (design/heat_pump.md, section 4)
const PROGRAMS = () => ({
  heating: t("Heizen"), summer: t("Sommer"), standby: t("Bereitschaft"), cooling: t("Kühlen"), unknown: t("unbekannt"),
});
const ACTIVITIES = () => ({
  heating: t("Heizen"), hot_water: t("Warmwasser"), defrost: t("Abtauen"), idle: t("Bereitschaft"), cooling: t("Kühlen"), unknown: t("unbekannt"),
});
const STRATEGIES = () => ({ fixed: t("Fest"), weather: t("Witterungsgeführt"), unknown: t("unbekannt") });
const LINK = () => ({
  compatible: t("verbunden, passt"), incompatible: t("passt nicht"), unavailable: t("nicht erreichbar"), pending: t("wird geprüft"),
});
/** Why control and learning are frozen, in words; unknown reasons as their key. */
function frozenText(reason) {
  const fixed = {
    unusable: t("Gerät nicht erreichbar oder passt nicht"),
    bypass: t("Bypass-Ventil offen"),
    bypass_missing: t("Bypass-Ventil unbekannt"),
    bypass_settling: t("Bypass-Ventil beruhigt sich"),
    after_hot_water: t("Nachlauf nach Warmwasser"),
    after_defrost: t("Nachlauf nach Abtauen"),
    after_unknown: t("Nachlauf nach Neustart"),
    blocked: t("Raumthermostat-Sperre aktiv"),
    blocked_unknown: t("Raumthermostat-Sperre unbekannt"),
    fault: t("Störung"),
    error_missing: t("Fehlercode unbekannt"),
    setpoint_missing: t("Vorlauf-Soll unbekannt"),
    self_limited: t("Wärmepumpe begrenzt den Vorlauf selbst"),
    rooms_missing: t("keine aktuellen Raumtemperaturen"),
    ventilation: t("schnelle Änderung erkannt"),
    strategy_unknown: t("Strategie unbekannt"),
    no_sample: t("keine Messwerte"),
    mode_off: t("Modus Aus"),
  }[reason];
  if (fixed) return fixed;
  if (reason.startsWith("program_")) return t("Programm: {state}", { state: PROGRAMS()[reason.slice(8)] ?? reason.slice(8) });
  if (reason.startsWith("activity_")) return t("Aktivität: {state}", { state: ACTIVITIES()[reason.slice(9)] ?? reason.slice(9) });
  return reason;
}

// Why writing for real cannot be switched on (api/heat_pump.py, writing_refused)
const WRITING_REFUSED = () => ({
  incompatible: t("Wärmepumpe nicht kompatibel oder nicht erreichbar"),
  no_sample: t("noch keine Messwerte"),
  strategy: t("HK Funktion nicht auf Fest"),
  esp_regulation: t("ESP-Regelung aktiv"),
});

/** Settings area "Wärmepumpe": status, mode and room setpoint on top, then per heat pump the control chain,
 * the history, the plant, writing and what was learned; house model and settings at the end. Explanations
 * carry the class "help" (shown or hidden with the "?" of the menu bar). */
class SunflowHeatPump extends LitElement {
  static properties = {
    _candidates: { state: true },
    _house: { state: true },
    _houseEdit: { state: true },
    _houseModel: { state: true },
    _status: { state: true },
    _writes: { state: true },
    _history: { state: true },
    _hours: { state: true },
    _selected: { state: true },
    _open: { state: true },
    _limitsEdit: { state: true },
    _curveEdit: { state: true },
    _writeNow: { state: true },
    _climateBusy: { state: true },
    _error: { state: true },
    _busy: { state: true },
  };

  constructor() {
    super();
    this._candidates = null;
    this._house = null;
    this._houseEdit = null;
    this._houseModel = null;
    this._status = null;
    this._writes = {};
    // Per heat pump: rows and events of the last hours, and when they were read
    this._history = {};
    this._historyAt = {};
    this._hours = HISTORY_HOURS[1];
    // Per heat pump: the block of the control chain whose details show
    this._selected = {};
    // Bumped when a section opens or closes (the state itself is in lib/collapse.js)
    this._open = 0;
    // Per heat pump the range of the set value being edited: { min, max } as typed
    this._limitsEdit = {};
    this._curveEdit = {};
    // Per heat pump: "write now" sent, waiting for the next tick
    this._writeNow = {};
    this._climateBusy = false;
    this._timer = null;
    this._error = null;
    this._busy = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._timer = setInterval(() => this._loadStatus(), STATUS_EVERY_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._timer);
    this._timer = null;
  }

  async _loadStatus() {
    try {
      this._status = await api.heatPumpStatus();
      // A "write now" is done once the tick ran: then its result shows instead of "being written"
      const waiting = Object.keys(this._writeNow).filter((id) =>
        this._status.devices?.find((d) => d.id === id)?.state?.flow?.guard?.write_now?.requested_at != null);
      this._writeNow = Object.fromEntries(waiting.map((id) => [id, true]));
    } catch (err) {
      this._status = { error: err.message, devices: [], house: null };
    }
    this._loadWrites();
    this._loadHistory();
  }

  /** Per day the writes Sunflow caused or would have caused and the unwanted shutdowns. */
  async _loadWrites() {
    const writes = {};
    for (const device of this._status?.devices ?? []) {
      if (!device.state?.flow?.guard) continue;
      try {
        writes[device.id] = await api.heatPumpShadowWrites(device.id, WRITES_DAYS);
      } catch (err) {
        writes[device.id] = { error: err.message };
      }
    }
    this._writes = writes;
  }

  /** The history of every heat pump; read again every HISTORY_EVERY_MS (rows are 5 minute means). */
  async _loadHistory(force = false) {
    const now = Date.now();
    for (const device of this._status?.devices ?? []) {
      if (!force && now - (this._historyAt[device.id] ?? 0) < HISTORY_EVERY_MS) continue;
      this._historyAt[device.id] = now;
      try {
        const history = await api.heatPumpHistory(device.id, this._hours);
        this._history = { ...this._history, [device.id]: history };
      } catch (err) {
        this._history = { ...this._history, [device.id]: { error: err.message } };
      }
    }
  }

  async _load() {
    try {
      this._house = await api.house();
      this._error = null;
    } catch (err) {
      this._error = t("Einstellungen nicht verfügbar: {error}", { error: err.message });
    }
    this._loadCandidates();
    this._loadHouseModel();
    this._loadStatus();
  }

  async _loadHouseModel() {
    try {
      this._houseModel = await api.houseModel();
    } catch (err) {
      this._houseModel = { state: "error", error: err.message };
    }
  }

  async _loadCandidates() {
    if (this._candidates) return;
    try {
      this._candidates = await api.candidates();
    } catch (err) {
      this._error = t("Entitäten konnten nicht geladen werden: {error}", { error: err.message });
    }
  }

  render() {
    if (this._house === null) {
      return html`<h1>${t("Wärmepumpe")}</h1>${this._error
        ? html`<div class="alert error" role="alert">${this._error}</div>`
        : html`<p class="muted">${t("Lade …")}</p>`}`;
    }
    const devices = this._status?.devices ?? [];
    const single = devices.length === 1 ? devices[0] : null;
    return html`
      <div class="title-row">
        <h1>${t("Wärmepumpe")}</h1>
        ${single ? html`<span class="small ${single.status === "compatible" ? "muted" : "warn-text"}">${single.name} · ${LINK()[single.status] ?? single.status}</span>` : nothing}
      </div>
      <p class="help small">${t("Sunflow lernt aus den Messwerten, wie Haus und Wärmepumpe reagieren, und regelt den Vorlauf, wenn du „Lernen + Regeln“ einschaltest. Erklärungen blendest du oben mit „?“ ein, einzeln mit dem „?“ hinter einer Überschrift.")}</p>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${this._status?.error ? html`<div class="alert error" role="alert">${t("Status nicht verfügbar: {error}", { error: this._status.error })}</div>` : nothing}
      ${devices.map((device) => this._renderStatusLine(device, devices.length > 1))}
      ${this._renderControls()}
      ${devices.map((device) => this._renderDevice(device, devices.length > 1))}
      ${this._section("house_model", t("Hausmodell"), this._houseModelSummary(), null, () => this._renderHouseModel(), false)}
      ${this._section("settings", t("Einstellungen"), t("Spielraum, Grenzen, Räume, Heizflächen"), null, () => this._renderSettings(), false)}
    `;
  }

  /** A collapsible section; open or closed is remembered per browser (lib/collapse.js). */
  _section(id, title, summary, help, body, fallback = true) {
    const key = `heat_pump:${id}`;
    const open = isOpen(key, fallback);
    const toggle = () => {
      setOpen(key, !open);
      this._open += 1;
    };
    return html`
      <section class="sec">
        <div class="section-head" @click=${toggle}>
          <button class="sec-toggle" aria-expanded=${open} aria-label=${title} @click=${(e) => { e.stopPropagation(); toggle(); }}>
            <span class="chev ${open ? "open" : ""}"></span>
          </button>
          <h2>${title}</h2>
          <span class="sum">${summary ?? ""}</span>
        </div>
        ${open && help ? html`<p class="help small">${help}</p>` : nothing}
        ${open ? html`<div class="sec-body">${body()}</div>` : nothing}
      </section>
    `;
  }

  _tile(label, value, note = null, cls = "") {
    return html`<div class="tile ${cls}"><div class="small muted">${label}</div><div class="value">${value}</div>${note ? html`<div class="small muted">${note}</div>` : nothing}</div>`;
  }

  _roomName(entityId) {
    return (this._candidates?.temperature ?? []).find((c) => c.entity_id === entityId)?.name ?? entityId;
  }

  /** The latest row of the history (outside temperature and rooms are not in the status). */
  _lastRow(deviceId) {
    const rows = this._history[deviceId]?.rows ?? [];
    return rows.length ? rows[rows.length - 1] : null;
  }

  /** One sentence on what happens now, what Sunflow writes or would write, and marks of the state. */
  _renderStatusLine(device, named) {
    const house = this._status?.house;
    const state = device.state;
    const flow = state?.flow;
    const guard = flow?.guard;
    const shadow = flow ? flow.shadow !== false : !device.writing?.active;
    const values = state?.values ?? {};
    let tone = "";
    let main;
    if (house?.mode === "off") main = t("Modus Aus: Sunflow regelt nicht und lernt nicht.");
    else if (!state) main = t("Noch keine Messwerte.");
    else if (state.fault) {
      tone = "error";
      main = t("Störung der Wärmepumpe: {error}", { error: state.error ?? "–" });
    } else if (device.status !== "compatible") {
      tone = "warn";
      main = t("Wärmepumpe: {state}", { state: LINK()[device.status] ?? device.status });
    } else if (state.blocked) {
      tone = "warn";
      main = guard?.block_reason === "foreign"
        ? t("Raumthermostat-Sperre aktiv, nicht von Sunflow gesetzt: Die Wärmepumpe heizt nicht. Sunflow hebt sie nicht auf.")
        : t("Raumthermostat-Sperre aktiv: Die Wärmepumpe heizt nicht.");
    } else if (state.activity === "hot_water") main = t("Warmwasser läuft – die Raumregelung pausiert.");
    else if (state.activity === "defrost") main = t("Abtauen – die Raumregelung pausiert.");
    else if (state.valid) {
      main = flow?.target_c != null
        ? t("Heizbetrieb: Vorlauf-Ziel {target} °C.", { target: one(flow.target_c) })
        : t("Heizbetrieb.");
    } else main = t("Raumregelung pausiert: {reasons}", { reasons: state.frozen.map(frozenText).join(", ") });
    const time = (iso) => new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
    const setpoint = flow?.setpoint;
    let sub = null;
    if (house?.mode !== "off" && guard) {
      if (shadow) {
        sub = guard.setpoint_c != null
          ? t("Nur Lernen: Sunflow würde Max T-Vorlauf {value} °C schreiben; in der Wärmepumpe stehen {real} °C.", { value: one(guard.setpoint_c), real: one(values.max_flow_setpoint) })
          : t("Nur Lernen: Sunflow schreibt nichts an die Wärmepumpe.");
      } else if (setpoint?.written_c != null) {
        sub = t("Max T-Vorlauf {value} °C, geschrieben {time} ({reason}).", {
          value: one(setpoint.written_c), time: setpoint.written_at ? time(setpoint.written_at) : "–",
          reason: WRITE_REASONS()[setpoint.written_reason] ?? setpoint.written_reason ?? "–",
        });
      }
      if (guard.phase === "off" && guard.restart_at) {
        sub = `${sub ?? ""} ${t("Kompressor aus: Wiederanlauf um {time}, falls er nicht selbst startet.", { time: time(guard.restart_at) })}`;
      } else if (guard.phase === "starting" && guard.run_since) {
        sub = `${sub ?? ""} ${t("Nach dem Start: Sunflow senkt ab {time} auf das Vorlauf-Ziel.", { time: time(new Date(Date.parse(guard.run_since) + 10 * 60_000).toISOString()) })}`;
      }
    }
    const curve = flow?.curve;
    const budget = guard?.budget;
    const chips = [
      house?.mode === "off" ? null : shadow ? [t("Nur Lernen"), ""] : [t("Lernen + Regeln"), "ok"],
      flow?.frozen?.length ? [t("Korrektur angehalten: {reasons}", { reasons: flow.frozen.map(frozenText).join(", ") }), ""] : null,
      (flow?.limited ?? []).length ? [flow.limited.map((l) => LIMITS()[l] ?? l).join(", "), "warn"] : null,
      curve ? [t("Heizkurve: Steilheit {slope}, Verschiebung {shift} K", { slope: two(curve.slope), shift: signedNumber(curve.shift_k) }), ""] : null,
      budget ? [t("{used} von {per_day} Schreibvorgängen heute", { used: budget.used_today, per_day: budget.per_day }), ""] : null,
      device.learn_stops ? [t("Lernphase Abschaltgrenze"), "warn"] : null,
    ].filter(Boolean);
    return html`
      <div class="status ${tone}" role="status">
        <div class="status-main">${named ? html`<span class="muted">${device.name}: </span>` : nothing}${main}</div>
        ${sub ? html`<div class="status-sub">${sub}</div>` : nothing}
        ${chips.length ? html`<div class="chips">${chips.map(([text, cls]) => html`<span class="chip ${cls}">${text}</span>`)}</div>` : nothing}
      </div>
    `;
  }

  /** Mode, room setpoint and the main values; warnings about rooms stay visible. */
  _renderControls() {
    const house = this._status?.house;
    if (!house) return nothing;
    const pumps = (this._status?.devices ?? []).filter((d) => d.writing);
    const current = house.mode === "off" ? "off" : pumps.some((d) => d.writing.active) ? "control" : "learn";
    // Learning + control needs at least one heat pump that may be switched on
    const refused = pumps.length && !pumps.some((d) => d.writing.active || d.writing.refused == null)
      ? pumps.map((d) => WRITING_REFUSED()[d.writing.refused] ?? d.writing.refused).join(", ")
      : pumps.length ? null : t("keine Wärmepumpe");
    const modeHelp = {
      off: t("Sunflow regelt nicht, lernt nicht und zeichnet keine Messwerte der Wärmepumpe auf; das Gelernte bleibt erhalten."),
      learn: t("Sunflow zeichnet auf und lernt, zeigt an, was es schreiben würde, schreibt aber nichts an die Wärmepumpe."),
      control: t("Sunflow schreibt Max T-Vorlauf (EEPROM, im Budget) und die Raumthermostat-Sperre, wenn die Regeln es verlangen. Zurückschalten stellt nichts zurück: Max T-Vorlauf und eine gesetzte Sperre bleiben stehen."),
    }[current];
    const mode = (value, label) => html`<button class=${current === value ? "on" : ""} aria-pressed=${current === value}
      ?disabled=${this._climateBusy || this._busy || (value === "control" && refused != null && current !== "control")}
      @click=${() => this._setOperation(value, current, pumps)}>${label}</button>`;
    const step = Number(house.step_c ?? 0.5);
    const setTo = (value) => {
      const clamped = Math.min(Number(house.max_c), Math.max(Number(house.min_c), Math.round(value / step) * step));
      if (clamped !== Number(house.setpoint_c)) this._setClimate({ setpoint_c: clamped });
    };
    const total = house.rooms_used.length + house.rooms_missing.length + house.rooms_stale.length + (house.rooms_ventilated ?? []).length;
    const first = this._status.devices?.[0];
    const values = first?.state?.values ?? {};
    const outside = first?.state?.flow?.outside_c ?? (first ? this._lastRow(first.id)?.outside_c : null);
    return html`
      <section class="card">
        <div class="controls">
          <div class="field">
            <span class="field-label">${t("Modus")}</span>
            <div class="seg" role="group" aria-label=${t("Modus")}>${mode("off", t("Aus"))}${mode("learn", t("Nur Lernen"))}${mode("control", t("Lernen + Regeln"))}</div>
            <span class="help small">${modeHelp}</span>
            ${refused != null && current !== "control"
              ? html`<span class="small warn-text">${t("„Lernen + Regeln“ nicht möglich: {reason}", { reason: refused })}</span>`
              : nothing}
          </div>
          <div class="field">
            <span class="field-label">${t("Raum-Soll")}</span>
            <div class="stepper">
              <button class="secondary" aria-label=${t("kälter")} ?disabled=${this._climateBusy} @click=${() => setTo(Number(house.setpoint_c) - step)}>−</button>
              <span class="input-unit"><input type="number" inputmode="decimal" min=${house.min_c} max=${house.max_c} step=${house.step_c}
                .value=${String(house.setpoint_c)} ?disabled=${this._climateBusy}
                @change=${(e) => this._setClimate({ setpoint_c: Number(e.target.value) })}> °C</span>
              <button class="secondary" aria-label=${t("wärmer")} ?disabled=${this._climateBusy} @click=${() => setTo(Number(house.setpoint_c) + step)}>+</button>
            </div>
            <span class="help small">${t("Mittlere Raumtemperatur aller Räume ({min} bis {max} °C). Auch als Thermostat „Haus“ in Home Assistant, z. B. für Zeitpläne. Aus dort entspricht Aus hier; Heizen dort kehrt zu „Nur Lernen“ bzw. „Lernen + Regeln“ zurück, je nachdem, was zuletzt galt. „Lernen + Regeln“ wird nur hier eingeschaltet.", { min: one(house.min_c), max: one(house.max_c) })}</span>
          </div>
          <div class="tiles main-tiles">
            ${this._tile(t("Raum-Ist"), `${one(house.room_c)} °C`, t("aus {used} von {total} Räumen", { used: house.rooms_used.length, total }))}
            ${this._tile(t("Außen"), `${one(outside)} °C`)}
            ${first?.state ? this._tile(t("Vorlauf"), `${one(values.flow_temperature)} °C`, t("Rücklauf {value} °C", { value: one(values.return_temperature) })) : nothing}
          </div>
        </div>
        ${house.rooms_stale.length || house.rooms_missing.length
          ? html`<p class="small warn-text">${t("Nicht berücksichtigt (nicht verfügbar oder seit über 2 h keine Meldung): {rooms}", { rooms: [...house.rooms_missing, ...house.rooms_stale].map((e) => this._roomName(e)).join(", ") })}</p>`
          : nothing}
        ${this._renderFastNow(house)}
      </section>
    `;
  }

  /** Rooms with a fast change now (airing, cooking ...): left out of room temperature and correction. */
  _renderFastNow(house) {
    const active = house.ventilation ?? [];
    if (!active.length) return nothing;
    const time = (iso) => new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
    const hours = decimal(String((this._house?.fast_change?.max_h) ?? 2));
    return html`<p class="small warn-text">${t("Schnelle Änderung erkannt, Raum zählt vorerst nicht und die Korrektur wartet (höchstens {hours} h): {rooms}", {
      hours,
      rooms: active.map((e) => t("{room} seit {time} ({from} → {low} °C)", { room: this._roomName(e.entity_id), time: time(e.since), from: one(e.start_c), low: one(e.extreme_c ?? e.low_c) })).join(", "),
    })}</p>`;
  }

  /** Fast changes of the last 24 h, as a list. */
  _renderFastRecent(house) {
    const recent = house?.ventilation_recent ?? [];
    if (!recent.length) return nothing;
    const time = (iso) => new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
    const change = (e) => {
      const k = e.change_k ?? -e.drop_k;
      return `${k < 0 ? "−" : "+"}${decimal(Math.abs(Number(k)).toFixed(1))}`;
    };
    return html`<p class="small muted">${t("Schnelle Änderungen in den letzten 24 h: {events}", {
      events: recent.map((e) => t("{room} {from}–{until} ({change} K)", { room: this._roomName(e.entity_id), from: time(e.since), until: time(e.until), change: change(e) })).join(", "),
    })}</p>`;
  }

  /** Switch the mode. Order keeps it safe: the switch of the heat pumps first, then the climate mode. */
  async _setOperation(value, current, pumps) {
    if (value === current) return;
    if (value === "control" && !(await confirmDialog({
      title: t("„Lernen + Regeln“ einschalten?"),
      text: t("Sunflow schreibt dann Max T-Vorlauf und die Raumthermostat-Sperre an die Wärmepumpe. Andere Regler, die diese Werte schreiben, vorher abschalten. Zurückschalten stellt nichts zurück."),
      confirm: t("Einschalten"),
    }))) {
      return;
    }
    this._busy = true;
    try {
      if (value === "control") {
        for (const d of pumps.filter((p) => !p.writing.active && p.writing.refused == null)) await api.setHeatPumpWriting(d.id, true);
      } else if (value === "learn") {
        for (const d of pumps.filter((p) => p.writing.active)) await api.setHeatPumpWriting(d.id, false);
      }
      await api.setClimate({ mode: value === "off" ? "off" : "heat" });
      this._error = null;
    } catch (err) {
      this._error = t("Modus nicht umgeschaltet: {error}", { error: err.message });
    } finally {
      this._busy = false;
      await this._loadStatus();
    }
  }

  async _setClimate(changes) {
    this._climateBusy = true;
    try {
      await api.setClimate(changes);
      this._error = null;
    } catch (err) {
      this._error = t("Raum-Soll konnte nicht gespeichert werden: {error}", { error: err.message });
    } finally {
      this._climateBusy = false;
      this._loadStatus();
    }
  }

  /** A heat pump: problems, then its sections (control chain, history, plant, writing, learned). */
  _renderDevice(device, named) {
    const state = device.state;
    const flow = state?.flow;
    const id = device.id;
    const shadow = flow ? flow.shadow !== false : true;
    const guard = flow?.guard;
    const values = state?.values ?? {};
    const one1 = (v) => `${one(v)} °C`;
    return html`
      ${named ? html`<div class="title-row device-name"><h2>${device.name}</h2>
        <span class="small ${device.status === "compatible" ? "muted" : "warn-text"}">${LINK()[device.status] ?? device.status}${device.mac ? ` · ${device.mac}` : ""}</span></div>` : nothing}
      ${(device.problems ?? []).length
        ? html`<ul class="small warn-text">${device.problems.map((p) => html`<li>${p.reason}${p.optional ? ` (${t("optional")})` : ""}</li>`)}</ul>`
        : nothing}
      ${!state
        ? html`<p class="small muted">${t("Noch keine Messwerte.")}</p>`
        : html`
          ${flow ? this._section(`chain:${id}`, t("Regelkette"),
            flow.target_c == null ? "" : shadow
              ? t("Ziel {target} → würde {value} schreiben", { target: one1(flow.target_c), value: one1(guard?.setpoint_c) })
              : t("Ziel {target} → Max T-Vorlauf {value}", { target: one1(flow.target_c), value: one1(values.max_flow_setpoint) }),
            t("So entsteht der Wert, den Sunflow an die Wärmepumpe schreibt (Max T-Vorlauf), von links nach rechts. Jeder Block zeigt seinen aktuellen Wert; ein Klick zeigt darunter die Einzelheiten."),
            () => this._renderChain(device)) : nothing}
          ${this._section(`history:${id}`, t("Verlauf"), t("letzte {hours} h", { hours: this._hours }),
            t("Vorlauf, Vorlauf-Ziel und Max T-Vorlauf oben, die Räume unten, als 5-Minuten-Mittel. Bei Warmwasser und Abtauen springt der Vorlauf; das zählt nicht für die Regelung. Ein Punkt ist ein Schreibvorgang; bei „Nur Lernen“ zeigt die gestrichelte Linie, was Sunflow geschrieben hätte."),
            () => this._renderHistory(device))}
          ${this._section(`plant:${id}`, t("Anlage"),
            [ACTIVITIES()[state.activity] ?? state.activity, state.compressor == null ? null : state.compressor ? t("Kompressor an") : t("Kompressor aus"),
              state.bypass_pct == null ? null : t("Bypass {pct} %", { pct: Math.round(state.bypass_pct) })].filter(Boolean).join(" · "),
            t("Zustand der Wärmepumpe und wohin die Wärme gerade fließt. Die Räume sind nach ihrem Abstand zum Raum-Soll gefärbt: grün höchstens 0,5 K, rot wärmer, blau kälter."),
            () => this._renderPlant(device), false)}
          ${guard ? this._section(`writing:${id}`, t("Schreiben und Schutz"), this._writingSummary(guard), null,
            () => this._renderGuard(id, guard, this._writes[id]), false) : nothing}
          ${flow ? this._section(`learned:${id}`, t("Gelernt"),
            flow.curve ? t("Heizkurve: Steilheit {slope}, Verschiebung {shift} K, aus {hours} h", { slope: two(flow.curve.slope), shift: signedNumber(flow.curve.shift_k), hours: flow.curve.hours }) : "",
            null, () => this._renderLearned(device), false) : nothing}`}
    `;
  }

  // ---------- Control chain ----------

  /** The blocks of the control chain with their values and whether they are held or acting. */
  _chainBlocks(device) {
    const house = this._status?.house ?? {};
    const state = device.state;
    const flow = state.flow;
    const guard = flow.guard ?? {};
    const values = state.values ?? {};
    const shadow = flow.shadow !== false;
    const budget = guard.budget;
    const total = (house.rooms_used ?? []).length + (house.rooms_missing ?? []).length + (house.rooms_stale ?? []).length + (house.rooms_ventilated ?? []).length;
    const time = (iso) => new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
    const setpoint = flow.setpoint ?? {};
    const restarting = guard.phase === "off" || guard.phase === "restart";
    return {
      soll: { title: t("Raum-Soll"), value: `${one(house.setpoint_c)} °C`,
        sub: house.preheat_k ? t("Vorheizen bis +{k} K", { k: one(house.preheat_k) }) : "" },
      ist: { title: t("Raum-Ist"), value: `${one(house.room_c)} °C`, sub: t("{used} von {total} Räumen", { used: (house.rooms_used ?? []).length, total }),
        state: (house.rooms_missing ?? []).length + (house.rooms_stale ?? []).length ? "warn" : (house.ventilation ?? []).length ? "acting" : null },
      corr: { title: t("Korrektur"), value: signed(flow.correction_k), sub: flow.frozen.length ? t("angehalten") : t("läuft"),
        state: flow.frozen.length ? "frozen" : (flow.limited ?? []).length ? "acting" : null },
      heat: { title: t("Außen"), value: `${one(flow.outside_c)} °C`, sub: t("Mittel der letzten 15 min"), state: flow.outside_c == null ? "warn" : null },
      ff: { title: t("Heizkurve"), value: `${one(flow.curve_c)} °C`,
        sub: flow.curve ? t("{pct} % gelernt", { pct: flow.curve.learned_pct ?? 0 }) : "" },
      target: { title: t("Vorlauf-Ziel"), value: `${one(flow.target_c)} °C`, sub: t("Heizkurve + Korrektur") },
      limits: { title: t("Grenzen"), value: `${whole(guard.min_c)}–${whole(guard.max_c)} °C`,
        sub: (guard.limited ?? flow.limited ?? []).length ? (guard.limited ?? flow.limited).map((l) => LIMITS()[l] ?? l).join(", ") : t("eingehalten"),
        state: (guard.limited ?? flow.limited ?? []).length ? "acting" : null },
      guard: {
        title: t("Schutz"),
        value: guard.block ? t("Sperre") : restarting && guard.floor_c != null ? `↑ ${one(guard.floor_c)} °C` : `${one(guard.setpoint_c)} °C`,
        sub: [GUARD_PHASES()[guard.phase] ?? guard.phase, device.learn_stops ? t("Lernphase an") : guard.floor_c != null && !restarting ? t("≥ {value} °C", { value: one(guard.floor_c) }) : null]
          .filter(Boolean).join(" · "),
        state: guard.block || restarting ? "acting" : null,
      },
      writer: { title: t("Schreiben"), value: budget ? `${budget.used_today} / ${budget.per_day}` : "–",
        sub: guard.pending ? t("ausstehend {value} °C", { value: one(guard.pending.value_c) }) : t("Budget heute"),
        state: guard.pending?.held ? "acting" : null },
      hp: { title: t("Wärmepumpe"), value: `${one(values.max_flow_setpoint)} °C`,
        sub: shadow
          ? t("Max T-Vorlauf · Nur Lernen")
          : setpoint.written_at ? t("Max T-Vorlauf · {time}", { time: time(setpoint.written_at) }) : t("Max T-Vorlauf"),
        state: state.fault ? "warn" : null },
    };
  }

  _renderChain(device) {
    const flow = device.state.flow;
    const selected = this._selected[device.id] ?? "target";
    const blocks = this._chainBlocks(device);
    const detail = this._chainDetail(device, selected);
    return html`
      <div class="diagram">${chainDiagram(blocks, {
        selected,
        onSelect: (id) => (this._selected = { ...this._selected, [device.id]: id }),
        live: flow.shadow === false,
        corrFrozen: flow.frozen.length > 0,
      })}</div>
      <div class="detail">
        <div class="title-row"><h3>${blocks[selected]?.title ?? ""}</h3><span class="small muted">${t("Block anklicken für Einzelheiten")}</span></div>
        ${detail.help ? html`<p class="help small">${detail.help}</p>` : nothing}
        ${detail.lead ?? nothing}
        <div class="tiles">${detail.tiles}</div>
        ${detail.notes ?? nothing}
      </div>
    `;
  }

  /** The heating curve with the current numbers (detail of the block "Heizkurve"). */
  _renderFormula(device) {
    const flow = device.state.flow;
    const c = flow.curve;
    const room = this._status?.house?.setpoint_c;
    const known = c && room != null && flow.outside_c != null;
    return html`<div class="formula">
      <div class="formula-main">${t("Vorlauf = R + S · (R − A) + P")}</div>
      ${known
        ? html`<div>= ${one(room)} + ${two(c.slope)} · (${one(room)} − ${one(flow.outside_c)}) ${c.shift_k < 0 ? "−" : "+"} ${decimal(Math.abs(c.shift_k).toFixed(1))} = <strong>${one(flow.curve_c)} °C</strong></div>
          <div>${t("+ Korrektur {corr} = Vorlauf-Ziel {target} °C", { corr: signed(flow.correction_k), target: one(flow.target_c) })}</div>`
        : nothing}
      <div class="small muted">${t("R Raum-Soll · A Außen · S Steilheit · P Verschiebung")}</div>
    </div>`;
  }

  /** Tiles, notes and the explanation of one block of the control chain. */
  _chainDetail(device, id) {
    const house = this._status?.house ?? {};
    const state = device.state;
    const flow = state.flow;
    const guard = flow.guard ?? {};
    const values = state.values ?? {};
    const shadow = flow.shadow !== false;
    const tile = (...args) => this._tile(...args);
    const time = (iso) => new Date(iso).toLocaleString(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
    switch (id) {
      case "soll": {
        const setpoint = Number(house.setpoint_c);
        return {
          help: t("Gewünschte mittlere Raumtemperatur. Den Spielraum nutzt die Planung: bei PV-Überschuss oder günstigem Strom bis „Vorheizen bis“ über den Raum-Soll heizen, bei teurem Strom bis „Absenken bis“ darunter fallen lassen. Ohne Ersparnis bleibt es beim Raum-Soll. Einstellbar unter „Einstellungen“."),
          tiles: [
            tile(t("Raum-Soll"), `${one(setpoint)} °C`),
            house.preheat_k != null ? tile(t("Vorheizen bis"), `+${one(house.preheat_k)} K`, `= ${one(setpoint + Number(house.preheat_k))} °C`) : nothing,
            house.lower_k != null ? tile(t("Absenken bis"), `−${one(house.lower_k)} K`, `= ${one(setpoint - Number(house.lower_k))} °C`) : nothing,
          ],
        };
      }
      case "ist": {
        const row = this._lastRow(device.id);
        const rooms = (this._house?.rooms ?? []).map((r) => r.entity_id);
        const aired = new Set((house.ventilation ?? []).map((e) => e.entity_id));
        const out = new Set([...(house.rooms_missing ?? []), ...(house.rooms_stale ?? [])]);
        return {
          help: t("Gewichteter Mittelwert der Raumtemperaturen (Gewicht unter „Einstellungen“). Ein Raum mit schneller Änderung (Lüften, Kochen …) zählt bis zu ihrem Ende nicht; ein Raum ohne Meldung seit 2 h fällt heraus. Sunflow regelt keine einzelnen Räume."),
          tiles: rooms.map((e) => tile(this._roomName(e), row?.rooms?.[e] != null ? `${one(row.rooms[e])} °C` : "–",
            aired.has(e) ? t("schnelle Änderung, zählt nicht") : out.has(e) ? t("zählt nicht (keine Meldung)") : t("Mittel der letzten 5 min"),
            aired.has(e) || out.has(e) ? "warn" : "")),
          notes: this._renderFastRecent(house),
        };
      }
      case "corr":
        return {
          help: t("PI-Regler: gleicht aus, was die Heizkurve nicht trifft, aus der Abweichung von Raum-Soll und Raum-Ist. Angehalten, solange kein gültiger Heizbetrieb läuft (Warmwasser, Abtauen, Bypass offen, schnelle Änderung), damit er sich nicht aufschaukelt. Bei „Nur Lernen“ wirkt die Korrektur nicht zurück und kann bis an ihre Grenze laufen; bei „Lernen + Regeln“ beginnt sie bei 0."),
          tiles: [
            tile(t("Korrektur"), signed(flow.correction_k), t("höchstens ± {limit} K", { limit: decimal(String(flow.parameters.limit_k)) })),
            tile(t("Abweichung"), house.room_c == null ? "–" : signed(Number(house.setpoint_c) - Number(house.room_c)), t("Raum-Soll − Raum-Ist")),
            tile(t("Nachstellzeit"), `${decimal(String(flow.parameters.ti_h))} h`, SURFACES()[flow.surface] ?? flow.surface ?? ""),
            tile(t("Totzone"), `± ${decimal(String(flow.parameters.deadband_k))} K`),
          ],
          notes: html`<p class="small ${flow.frozen.length ? "warn-text" : "muted"}">${flow.frozen.length
            ? t("Korrektur angehalten: {reasons}", { reasons: flow.frozen.map(frozenText).join(", ") })
            : t("Korrektur läuft.")}</p>
            ${(flow.limited ?? []).length
              ? html`<p class="small warn-text">${t("An einer Grenze, die Korrektur läuft nicht weiter darüber hinaus: {limits}", { limits: flow.limited.map((l) => LIMITS()[l] ?? l).join(", ") })}</p>`
              : nothing}`,
        };
      case "heat":
        return {
          help: t("Gemessene Außentemperatur, gemittelt über die letzten 15 Minuten. Ohne sie gibt es kein Vorlauf-Ziel. Später kommt die erwartete Änderung aus der Wetterprognose dazu."),
          tiles: [
            tile(t("Außen (Regelung)"), `${one(flow.outside_c)} °C`, t("Mittel der letzten 15 min")),
          ],
        };
      case "ff": {
        const c = flow.curve ?? {};
        return {
          help: t("Es wirken die Startwerte (unten einstellbar) und, je mehr Stunden beim Raum-Soll vorliegen, die gelernten Werte. Ändert sich die gelernte Kurve, wird die Korrektur so verschoben, dass das Vorlauf-Ziel nicht springt."),
          tiles: [
            tile(t("Heizkurve"), `${one(flow.curve_c)} °C`, t("bei Raum-Soll {room} °C und außen {outside} °C", { room: one(house.setpoint_c), outside: one(flow.outside_c) })),
            tile(t("Steilheit"), two(c.slope), t("Start {start}", { start: two(c.start_slope) })),
            tile(t("Verschiebung"), `${signedNumber(c.shift_k)} K`, t("Start {start} K", { start: signedNumber(c.start_shift_k) })),
            tile(t("Gelernt"), `${c.learned_pct ?? 0} %`, t("aus {hours} h beim Raum-Soll", { hours: c.hours ?? 0 })),
          ],
          lead: this._renderFormula(device),
          notes: flow.curve ? html`${this._renderCurveShare(flow.curve)}${this._renderCurveStart(device, flow.curve)}` : nothing,
        };
      }
      case "target":
        return {
          help: t("Der Vorlauf, den Sunflow für die Räume für richtig hält: Heizkurve plus Korrektur, noch ohne Grenzen und Schutz."),
          tiles: [
            tile(t("Vorlauf-Ziel"), `${one(flow.target_c)} °C`, flow.reason === "no_outside" ? t("keine Außentemperatur") : null),
            tile(t("= Heizkurve"), `${one(flow.curve_c)} °C`),
            tile(t("+ Korrektur"), signed(flow.correction_k)),
          ],
        };
      case "limits":
        return {
          help: t("Der Stellwert bleibt im Bereich, den die Heizflächen vertragen (unter „Einstellungen“ je Wärmepumpe änderbar, eingeengt auf den Bereich der Wärmepumpe)."),
          tiles: [
            tile(t("Bereich"), `${one(guard.min_c)}–${one(guard.max_c)} °C`),
            tile(t("Vorlauf-Ziel in Grenzen"), guard.desired_c == null ? "–" : `${one(guard.desired_c)} °C`),
          ],
        };
      case "guard":
        return {
          help: t("Sunflow sagt nicht voraus, wann der Kompressor schaltet, sondern reagiert: Ist er 5 Minuten aus, obwohl die Räume Wärme brauchen (auch nach Warmwasser oder Abtauen), hebt Sunflow den Stellwert auf Vorlauf + Einschaltabstand (Wiederanlauf). Nach einem Start wartet es 10 Minuten und senkt dann in einem Schritt auf das Vorlauf-Ziel – nicht tiefer als die Abschaltgrenze erlaubt (höchster Vorlauf − Abschaltgrenze + Schwankung). In der Lernphase senkt es bis aufs Vorlauf-Ziel und lässt den Kompressor abschalten, um die Grenze zu lernen. Ist das Haus deutlich zu warm, setzt es die Raumthermostat-Sperre."),
          tiles: [
            tile(t("Phase"), GUARD_PHASES()[guard.phase] ?? guard.phase ?? "–",
              guard.phase === "off" && guard.restart_at ? t("Wiederanlauf um {time}", { time: time(guard.restart_at) }) : null),
            tile(guard.phase === "off" || guard.phase === "restart" ? t("Wiederanlauf auf") : t("Untergrenze"),
              guard.floor_c == null ? "–" : `${one(guard.floor_c)} °C`,
              guard.floor_c == null && device.learn_stops ? t("Lernphase: keine Untergrenze") : null),
            tile(t("Abschaltgrenze"), `${one(guard.values?.stop_margin)} K`, VALUE_SOURCES()[guard.values?.sources?.stop_margin] ?? ""),
            tile(t("Sperre"), guard.block ? (guard.block_reason === "foreign" ? t("an (nicht von Sunflow)") : t("an")) : t("aus"),
              guard.block ? (BLOCK_REASONS()[guard.block_reason] ?? guard.block_reason ?? "") + (guard.block_since ? ` · ${t("seit {time}", { time: time(guard.block_since) })}` : "") : null),
          ],
          notes: html`
            ${guard.block && guard.block_reason !== "foreign"
              ? html`<p class="small warn-text">${shadow
                ? t("Sunflow würde die Raumthermostat-Sperre setzen: {reason}", { reason: BLOCK_REASONS()[guard.block_reason] ?? guard.block_reason ?? "–" })
                : t("Raumthermostat-Sperre von Sunflow gesetzt: {reason}", { reason: BLOCK_REASONS()[guard.block_reason] ?? guard.block_reason ?? "–" })}</p>`
              : nothing}
            ${guard.inactive?.length
              ? html`<p class="small muted">${shadow
                ? t("Würde nichts schreiben: {reasons}", { reasons: guard.inactive.map(inactiveText).join(", ") })
                : t("Schreibt nichts: {reasons}", { reasons: guard.inactive.map(inactiveText).join(", ") })}</p>`
              : nothing}`,
        };
      case "writer":
        return {
          help: t("Jeder Schreibvorgang landet im EEPROM der Wärmepumpe. Darum schreibt Sunflow sparsam: Mindestabstand, Totzone, ein Tagesbudget mit Notkontingent für Schutzschritte und eine Höchstzahl je Stunde. Einzelheiten unter „Schreiben und Schutz“."),
          tiles: this._budgetTiles(guard),
          notes: this._renderPending(device.id, guard),
        };
      case "hp":
      default:
        return {
          help: t("Sunflow stellt nur Max T-Vorlauf (und bei Bedarf die Raumthermostat-Sperre); den Kompressor regelt die Wärmepumpe selbst. Zurückschalten auf „Nur Lernen“ stellt nichts zurück."),
          tiles: [
            tile(t("Max T-Vorlauf"), `${one(values.max_flow_setpoint)} °C`, shadow
              ? t("Nur Lernen: Sunflow würde {value} °C schreiben", { value: one(guard.setpoint_c) })
              : flow.setpoint?.written_at ? t("geschrieben {time}", { time: time(flow.setpoint.written_at) }) : null),
            tile(t("Vorlauf-Soll (wirksam)"), `${one(values.flow_setpoint)} °C`),
            tile(t("Vorlauf"), `${one(values.flow_temperature)} °C`, values.flow_temperature_max == null ? null : t("Spitze {value} °C", { value: one(values.flow_temperature_max) })),
            tile(t("Aktivität"), ACTIVITIES()[state.activity] ?? state.activity, state.compressor == null ? null : state.compressor ? t("Kompressor an") : t("Kompressor aus")),
          ],
          notes: this._renderAdopt(guard),
        };
    }
  }

  // ---------- History ----------

  _renderHistory(device) {
    const history = this._history[device.id];
    const hours = html`<div class="seg small-seg" role="group" aria-label=${t("Zeitraum")}>
      ${HISTORY_HOURS.map((h) => html`<button class=${this._hours === h ? "on" : ""} aria-pressed=${this._hours === h}
        @click=${() => { this._hours = h; this._loadHistory(true); }}>${h < 48 ? t("{hours} h", { hours: h }) : t("{days} Tage", { days: h / 24 })}</button>`)}
    </div>`;
    if (!history) return html`${hours}<p class="small muted">${t("Lade …")}</p>`;
    if (history.error) return html`${hours}<p class="small warn-text">${t("Verlauf nicht verfügbar: {error}", { error: history.error })}</p>`;
    if (!history.rows?.length) return html`${hours}<p class="small muted">${t("Noch keine Messwerte in diesem Zeitraum (bei Modus Aus zeichnet Sunflow nichts auf).")}</p>`;
    const names = Object.fromEntries((this._house?.rooms ?? []).map((r) => [r.entity_id, this._roomName(r.entity_id)]));
    const item = (cls, text, style = "") => html`<span><i class=${cls} style=${style}></i>${text}</span>`;
    return html`
      ${hours}
      <div class="legend">
        ${item("", t("Vorlauf"), "background: var(--hp-hot)")}
        ${item("", t("Rücklauf"), "background: var(--hp-cold); opacity: .6")}
        ${item("", t("Vorlauf-Ziel"), "background: var(--sf-series-3)")}
        ${item("", t("Max T-Vorlauf in der Wärmepumpe (● geschrieben)"), "background: var(--sf-text)")}
        ${(history.events ?? []).some((e) => e.kind === "write" && e.shadow !== false && e.value_c != null)
          ? item("dash", t("würde schreiben (Nur Lernen)"), "color: var(--sf-series-6)") : nothing}
        ${item("", t("Raum-Ist (dünn: einzelne Räume)"), "background: var(--sf-series-1)")}
        ${item("dash", t("Raum-Soll"), "color: var(--sf-series-4)")}
        <span><i class="cross"></i>${t("Kompressor aus")}</span>
        ${item("box", t("Warmwasser"), "background: var(--hp-dhw)")}
        ${item("box", t("Sperre"), "background: var(--sf-muted)")}
        ${item("box", t("schnelle Änderung"), "background: var(--hp-cold)")}
        ${item("box", t("Kompressor an"), "background: var(--sf-ok)")}
      </div>
      <div class="diagram">${historyChart(history, { names })}</div>
    `;
  }

  // ---------- Plant ----------

  _renderPlant(device) {
    const state = device.state;
    const values = state.values ?? {};
    const house = this._status?.house ?? {};
    const row = this._lastRow(device.id);
    const aired = new Set((house.ventilation ?? []).map((e) => e.entity_id));
    const rooms = (this._house?.rooms ?? []).map((r) => ({ name: this._roomName(r.entity_id), value: row?.rooms?.[r.entity_id] ?? null, fast: aired.has(r.entity_id) }));
    const tile = (...args) => this._tile(...args);
    return html`
      <div class="diagram">${plantDiagram({
        outside: row?.outside_c, compressor: state.compressor, heatKw: row?.heat_w != null ? row.heat_w / 1000 : null,
        flow: values.flow_temperature, ret: values.return_temperature, maxSetpoint: values.max_flow_setpoint,
        bypass: state.bypass_pct, activity: state.activity, rooms, room: house.room_c, setpoint: house.setpoint_c, blocked: state.blocked,
      })}</div>
      <div class="tiles">
        ${tile(t("Programm"), PROGRAMS()[state.program] ?? state.program, state.program_key ?? null)}
        ${tile(t("Aktivität"), ACTIVITIES()[state.activity] ?? state.activity, state.compressor == null ? null : state.compressor ? t("Kompressor an") : t("Kompressor aus"))}
        ${tile(t("Strategie"), STRATEGIES()[state.strategy] ?? state.strategy)}
        ${tile(t("Bypass-Ventil"), state.bypass_pct == null ? "–" : `${Math.round(state.bypass_pct)} %`)}
        ${tile(t("Vorlauf"), `${one(values.flow_temperature)} °C`, values.flow_temperature_max == null ? null : t("Spitze {value} °C", { value: one(values.flow_temperature_max) }))}
        ${tile(t("Rücklauf"), `${one(values.return_temperature)} °C`)}
        ${tile(t("Vorlauf-Soll (wirksam)"), `${one(values.flow_setpoint)} °C`, t("Max T-Vorlauf {value} °C", { value: one(values.max_flow_setpoint) }))}
      </div>
      <p class="small ${state.valid ? "muted" : "warn-text"}">${state.valid
        ? (house.mode === "off" ? t("Gültiger Heizbetrieb; Modus Aus: Sunflow lernt nicht.") : t("Gültiger Heizbetrieb: Sunflow rechnet die Korrektur und lernt."))
        : t("Eingefroren: {reasons}", { reasons: state.frozen.map(frozenText).join(", ") })}</p>
    `;
  }

  // ---------- Writing and protection ----------

  _writingSummary(guard) {
    const budget = guard.budget;
    const parts = [
      budget ? t("{used}/{per_day} heute", { used: budget.used_today, per_day: budget.per_day }) : null,
      guard.pending ? t("ausstehend {value} °C", { value: one(guard.pending.value_c) }) : t("nichts ausstehend"),
    ];
    return parts.filter(Boolean).join(" · ");
  }

  /** Budget of today as tiles with bars. */
  _budgetTiles(guard) {
    const budget = guard.budget;
    if (!budget) return [];
    const shadow = guard.shadow !== false;
    const bar = (used, limit, cls = "") => html`<div class="bar ${cls}"><div style=${`width: ${Math.min(100, limit ? (used / limit) * 100 : 0)}%`}></div></div>`;
    const emergencyUsed = budget.emergency_per_day - budget.emergency_left;
    return [
      html`<div class="tile"><div class="small muted">${shadow ? t("Heute (würde)") : t("Heute")}</div>
        <div class="value">${budget.used_today} / ${budget.per_day}</div>${bar(budget.used_today, budget.per_day)}</div>`,
      html`<div class="tile"><div class="small muted">${t("Notkontingent")}</div>
        <div class="value">${emergencyUsed} / ${budget.emergency_per_day}</div>${bar(emergencyUsed, budget.emergency_per_day, "emergency")}
        <div class="small muted">${t("nur Schutzschritte")}</div></div>`,
      html`<div class="tile"><div class="small muted">${t("Letzte Stunde")}</div>
        <div class="value">${budget.used_hour} / ${budget.per_hour}</div>${bar(budget.used_hour, budget.per_hour)}</div>`,
    ];
  }

  /** A value waiting to be written, "write now" and a lapsed "write now". */
  _renderPending(deviceId, guard) {
    const time = (iso) => new Date(iso).toLocaleString(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
    const shadow = guard.shadow !== false;
    const pending = guard.pending;
    const lastWriteNow = guard.write_now?.last;
    // "write now" sent: waiting until the tick ran (cleared in _loadStatus)
    const sent = Boolean(this._writeNow[deviceId]);
    const pendingText = !pending
      ? null
      : pending.due_at
        ? (shadow
          ? t("ausstehend: {value} °C ({reason}), würde um {time} geschrieben", { value: one(pending.value_c), reason: WRITE_REASONS()[pending.reason] ?? pending.reason, time: time(pending.due_at) })
          : t("ausstehend: {value} °C ({reason}), wird um {time} geschrieben", { value: one(pending.value_c), reason: WRITE_REASONS()[pending.reason] ?? pending.reason, time: time(pending.due_at) }))
        : t("ausstehend: {value} °C ({reason}), wartet: {why}", { value: one(pending.value_c), reason: WRITE_REASONS()[pending.reason] ?? pending.reason, why: (pending.held === "budget" && BUDGET_LIMITS()[pending.budget]) || (HELD()[pending.held] ?? pending.held) });
    return html`
      ${pendingText ? html`<p class="small">${pendingText}</p>` : nothing}
      ${!shadow && (pending || sent)
        ? html`<div class="actions">
            <button class="secondary" ?disabled=${sent} @click=${() => this._sendWriteNow(deviceId)}>${t("Jetzt schreiben")}</button>
            ${sent ? html`<span class="small muted">${t("wird geschrieben …")}</span>` : nothing}
          </div>`
        : nothing}
      ${lastWriteNow && !lastWriteNow.done
        ? html`<p class="small warn-text">${t("„Jetzt schreiben“ verfallen {time}: {why}", { time: time(lastWriteNow.at), why: HELD()[lastWriteNow.reason] ?? lastWriteNow.reason })}</p>`
        : nothing}
    `;
  }

  /** Whether the heat pump took the last written value over. */
  _renderAdopt(guard) {
    if (guard.adopt?.state === "missing") {
      return html`<p class="small warn-text">${t("Zuletzt geschriebener Wert {value} °C von der Wärmepumpe nicht übernommen.", { value: one(guard.adopt.value_c) })}</p>`;
    }
    if (guard.adopt?.state === "limited") {
      return html`<p class="small warn-text">${t("Geschrieben {value} °C, wirksamer Vorlauf-Soll {effective} °C: die Wärmepumpe begrenzt selbst. Obergrenze des Stellwerts höchstens auf diesen Wert stellen.", { value: one(guard.adopt.value_c), effective: one(guard.adopt.effective_c) })}</p>`;
    }
    return nothing;
  }

  /** "Write now": the next tick writes the pending value without waiting for the spacing (only when active). */
  async _sendWriteNow(deviceId) {
    this._writeNow = { ...this._writeNow, [deviceId]: true };
    try {
      await api.heatPumpWriteNow(deviceId);
      this._error = null;
    } catch (err) {
      const { [deviceId]: _, ...rest } = this._writeNow;
      this._writeNow = rest;
      this._error = t("„Jetzt schreiben“ nicht möglich: {error}", { error: err.message });
    }
  }

  /** Budget, pending value, the last writes, counts per day. */
  _renderGuard(deviceId, guard, writes) {
    const time = (iso) => {
      const at = new Date(iso);
      return at.toDateString() === new Date().toDateString()
        ? at.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" })
        : at.toLocaleString(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
    };
    const minutes = (s) => (s == null ? "–" : decimal((s / 60).toFixed(0)));
    const shadow = guard.shadow !== false;
    const params = guard.parameters ?? {};
    return html`
      ${guard.block && guard.block_reason === "foreign"
        ? html`<div class="alert warn" role="status">${t("Raumthermostat-Sperre aktiv, nicht von Sunflow gesetzt: Die Wärmepumpe heizt nicht. Sunflow hebt sie nicht auf – bitte selbst aufheben, wenn sie nicht gewollt ist.")}</div>`
        : nothing}
      ${this._renderAdopt(guard)}
      <div class="title-row"><h3>${t("Budget heute")}</h3></div>
      <p class="help small">${t("Mindestabstand zwischen zwei Schreibvorgängen {normal} min, bei Schutzschritten (Wiederanlauf, Nachführen) {protect} min; dazwischen merkt Sunflow nur den letzten Wert. Die erste Änderung nach einer Ruhepause geht sofort hinaus. Bei „Nur Lernen“ zählt Sunflow, was es schreiben würde.", { normal: minutes(params.min_interval_s), protect: minutes(params.protect_interval_s) })}</p>
      <div class="tiles">${this._budgetTiles(guard)}</div>
      ${this._renderPending(deviceId, guard)}
      <div class="row2">
        <div>
          <div class="title-row"><h3>${shadow ? t("Würde schreiben (zuletzt)") : t("Schreibvorgänge (zuletzt)")}</h3></div>
          <p class="help small">${t("„echt“: an die Wärmepumpe geschrieben; „würde“: nur ausgerechnet (Nur Lernen).")}</p>
          ${guard.writes?.length
            ? html`<table>
                <thead><tr><th>${t("Zeit")}</th><th>${t("Wert")}</th><th>${t("Grund")}</th><th></th></tr></thead>
                <tbody>${guard.writes.slice(0, 10).map((w) => html`<tr>
                  <td>${time(w.at)}</td>
                  <td>${w.reason === "block_on" || w.reason === "block_off" ? "–" : html`${one(w.from_c)} → <strong>${one(w.value_c)}</strong>`}</td>
                  <td>${WRITE_REASONS()[w.reason] ?? w.reason}${w.manual ? ` (${t("manuell")})` : ""}${w.block_reason ? ` – ${BLOCK_REASONS()[w.block_reason] ?? w.block_reason}` : ""}</td>
                  <td><span class="tag ${w.shadow === false ? "real" : ""}">${w.shadow === false ? t("echt") : t("würde")}</span></td>
                </tr>`)}</tbody>
              </table>`
            : html`<p class="small muted">${shadow ? t("würde noch nichts schreiben") : t("noch nichts geschrieben")}</p>`}
        </div>
        <div>${this._renderWriteCounts(writes)}</div>
      </div>
    `;
  }

  /** Per day: writes by reason and compressor stops, against the budget. */
  _renderWriteCounts(writes) {
    if (!writes) return nothing;
    if (writes.error) return html`<p class="small warn-text">${t("Zählung nicht verfügbar: {error}", { error: writes.error })}</p>`;
    const budget = writes.budget ?? {};
    const day = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(locale(), { weekday: "short", day: "2-digit", month: "2-digit" });
    const over = (count, limit) => (limit != null && count > limit ? "warn-text" : "");
    return html`
      <div class="title-row"><h3>${t("Schreibvorgänge je Tag")}</h3></div>
      <p class="help small">${t("Budget: {normal} Schreibvorgänge je Tag, dazu {emergency} nur für Schutzschritte (Wiederanlauf, Nachführen), höchstens {hour} je Stunde; der Tag folgt der Zeitzone von Home Assistant. Ist das Budget erschöpft, wartet der Wert. Die Raumthermostat-Sperre zählt nicht (nicht im EEPROM).", { normal: budget.per_day ?? "–", emergency: budget.emergency_per_day ?? "–", hour: budget.per_hour ?? "–" })}</p>
      <table>
        <thead><tr>
          <th>${t("Tag")}</th><th>${t("normal")}</th><th>${t("Wiederanlauf")}</th><th>${t("Nachführen")}</th>
          <th>${t("Sperre an/aus")}</th><th>${t("Kompressor-Stopps")}</th>
        </tr></thead>
        <tbody>
          ${writes.days.map((d) => html`<tr>
            <td>${day(d.date)}</td>
            <td class=${over(d.setpoint, budget.per_day)}>${d.normal}</td>
            <td class=${over(d.setpoint, (budget.per_day ?? 0) + (budget.emergency_per_day ?? 0))}>${d.start}</td>
            <td class=${over(d.setpoint, (budget.per_day ?? 0) + (budget.emergency_per_day ?? 0))}>${d.track}</td>
            <td>${d.block_on}/${d.block_off}</td>
            <td>${d.stops ?? "–"}</td>
          </tr>`)}
        </tbody>
      </table>
    `;
  }

  // ---------- Learned ----------

  /** The heating curve as a diagram with its data, and the learned behaviour of the heat pump. */
  _renderLearned(device) {
    const flow = device.state.flow;
    const curve = flow.curve;
    const guard = flow.guard ?? {};
    const house = this._status?.house ?? {};
    const tile = (...args) => this._tile(...args);
    const values = guard.values ?? {};
    const sources = values.sources ?? {};
    const source = (key) => VALUE_SOURCES()[sources[key]] ?? sources[key] ?? "";
    // Hours that were enough (valid heating, compressor running, room at its setpoint): effective setpoint over outside
    const points = (this._history[device.id]?.rows ?? [])
      .filter((r) => r.valid && (r.compressor ?? 0) >= 0.99 && r.setpoint_c != null && r.outside_c != null && r.room_c != null
        && r.room_setpoint_c != null && Math.abs(r.room_c - r.room_setpoint_c) <= 0.3)
      .map((r) => ({ outside: r.outside_c, flow: r.setpoint_c }));
    return html`
      <div class="row2">
        <div>
          <div class="title-row"><h3>${t("Heizkurve")}</h3></div>
          <p class="help small">${t("Vorlauf über der Außentemperatur beim aktuellen Raum-Soll. Gestrichelt: die Startkurve, durchgezogen: die gelernte. Punkte: Stunden aus dem Verlauf, in denen der Raum beim Soll lag (wirksamer Vorlauf-Soll). Ein Raum-Soll 1 K höher hebt die ganze Kurve um 1 + Steilheit Kelvin.")}</p>
          ${!curve
            ? html`<p class="small muted">${t("Keine Heizkurve.")}</p>`
            : html`
              <div class="diagram">${curveChart(curve, { roomC: house.setpoint_c, minC: guard.min_c, maxC: guard.max_c, points, outsideC: flow.outside_c })}</div>
              ${this._renderCurveShare(curve)}
              <div class="tiles">
                ${tile(t("Steilheit (wirkt)"), two(curve.slope), t("Start {start}", { start: two(curve.start_slope) }))}
                ${tile(t("Verschiebung (wirkt)"), `${signedNumber(curve.shift_k)} K`, t("Start {start} K", { start: signedNumber(curve.start_shift_k) }))}
                ${tile(t("Gelernt aus"), t("{hours} h", { hours: curve.hours }), t("Stunden beim Raum-Soll, letzte 28 Tage"))}
              </div>
              <p class="small muted">${t("Startwerte einstellbar in der Regelkette: Block „Heizkurve“ anklicken.")}</p>`}
        </div>
        <div>
          <div class="title-row"><h3>${t("Verhalten der Wärmepumpe")}</h3></div>
          <p class="help small">${t("Werte, mit denen Sunflow Abschaltungen vermeidet. Solange zu wenig gelernt ist, gelten die Startwerte des Geräteprofils.")}</p>
          ${guard.values
            ? html`<div class="tiles">
                ${tile(t("Abschaltgrenze"), `${two(values.stop_margin)} K`, t("{source}; aus {count} Abschaltungen", { source: source("stop_margin"), count: guard.learned?.stop_samples ?? 0 }))}
                ${tile(t("Einschaltabstand"), `${two(values.start_margin)} K`, t("Wiederanlauf auf Vorlauf + diesen Wert"))}
                ${tile(t("Schwankung im Betrieb"), `${two(values.steady_swing)} K`, source("steady_swing"))}
                ${tile(t("Leistungsbereich"), `${decimal((values.power_min_w / 1000).toFixed(1))}–${decimal((values.power_max_w / 1000).toFixed(1))} kW`, source("power"))}
              </div>`
            : html`<p class="small muted">${t("Noch nichts gelernt.")}</p>`}
          ${this._renderLearnStops(device)}
        </div>
      </div>
      ${this._renderStops(guard.learned)}
    `;
  }

  /** How much the learned hours shape the curve, the rest are the start values. */
  _renderCurveShare(curve) {
    const pct = Math.max(0, Math.min(100, curve.learned_pct ?? 0));
    return html`
      <div class="share" role="img" aria-label=${t("Gelernt {learned} %, Startwerte {start} %", { learned: pct, start: 100 - pct })}>
        <div class="share-bar"><span class="share-learned" style=${`width: ${pct}%`}></span></div>
        <div class="small"><strong>${t("Gelernt {pct} %", { pct })}</strong> · ${t("Startwerte {pct} %", { pct: 100 - pct })}</div>
        <p class="help small">${t("Die Startwerte zählen wie 24 Stunden auf der Startkurve, jede gelernte Stunde beim Raum-Soll kommt dazu (ältere weniger, Halbwertszeit 14 Tage, höchstens 28 Tage). So wirken anfangs nur die Startwerte, mit der Zeit immer mehr das Gelernte – ganz verschwinden die Startwerte nie (höchstens etwa 94 % gelernt). Sie halten die Kurve stabil, wenn die Daten nur wenige Außentemperaturen abdecken.")}</p>
      </div>`;
  }

  /** Start values of the heating curve: editable, read from the heat pump or taken over from the learned curve. */
  _renderCurveStart(device, curve) {
    const stored = device.curve_start ?? {};
    const edit = this._curveEdit[device.id];
    const value = (key) => (edit ? edit[key] : toDisplay(key === "slope" ? curve.start_slope : curve.start_shift_k, ""));
    const set = (key, text) => (this._curveEdit = { ...this._curveEdit, [device.id]: { slope: value("slope"), shift: value("shift"), [key]: text } });
    const own = device.state?.flow?.heat_pump_slope;
    const range = t("Steilheit {min}–{max} (Bereich der Wärmepumpe), Verschiebung ±{shift} K", {
      min: two(stored.slope_min ?? 0), max: two(stored.slope_max ?? 3), shift: decimal(String(stored.shift_max_k ?? 10)) });
    return html`
      <div class="group">
        <div class="title-row"><h3>${t("Startwerte der Heizkurve")}</h3>
          <span class="small muted">${SLOPE_SOURCES()[stored.source ?? curve.start_source] ?? ""}</span></div>
        <p class="help small">${t("Die Kurve, zu der das Lernen hinzieht. Ohne eigene Werte liest Sunflow die Steilheit einmal aus der Wärmepumpe (HPSU „Heizkurve“; eine Verschiebung kennt sie nicht, dann 0 K). Eine Änderung wirkt sofort; die gelernten Stunden bleiben und werden neu verrechnet. „Gelerntes übernehmen“ macht die gelernte Kurve zum Start, z. B. vor dem Sommer für den nächsten Herbst.")}</p>
        <div class="fields">
          <label class="field narrow">
            <span class="field-label">${t("Steilheit S")}</span>
            <span class="input-unit"><input inputmode="decimal" .value=${value("slope")} @input=${(e) => set("slope", e.target.value)}></span>
          </label>
          <label class="field narrow">
            <span class="field-label">${t("Verschiebung P")}</span>
            <span class="input-unit"><input inputmode="decimal" .value=${value("shift")} @input=${(e) => set("shift", e.target.value)}> K</span>
          </label>
        </div>
        <p class="small muted">${range}</p>
        <div class="actions">
          ${edit
            ? html`<button class="primary" ?disabled=${this._busy} @click=${() => this._saveCurveStart(device, device.id)}>${t("Speichern")}</button>
                <button class="secondary" @click=${() => { const { [device.id]: _, ...rest } = this._curveEdit; this._curveEdit = rest; }}>${t("Abbrechen")}</button>`
            : nothing}
          <button class="secondary" ?disabled=${this._busy || own == null} @click=${() => this._takeCurveStart(device.id, "heat_pump")}>
            ${own == null ? t("Aus der Wärmepumpe neu einlesen") : t("Aus der Wärmepumpe neu einlesen ({slope})", { slope: two(own) })}</button>
          <button class="secondary" ?disabled=${this._busy || !curve.hours} @click=${() => this._takeCurveStart(device.id, "learned")}>${t("Gelerntes übernehmen")}</button>
        </div>
      </div>`;
  }

  async _saveCurveStart(device, deviceId) {
    const edit = this._curveEdit[deviceId];
    const stored = device.curve_start ?? {};
    const low = stored.slope_min ?? 0;
    const high = stored.slope_max ?? 3;
    const limit = stored.shift_max_k ?? 10;
    const slope = fromDisplay(edit.slope, "");
    const shift = fromDisplay(edit.shift, "");
    if (slope == null || shift == null || slope < low || slope > high || Math.abs(shift) > limit) {
      this._error = t("Startwerte: Steilheit {min}–{max}, Verschiebung ±{shift} K.", { min: two(low), max: two(high), shift: decimal(String(limit)) });
      return;
    }
    this._busy = true;
    try {
      await api.setHeatPumpCurveStart(deviceId, slope, shift);
      const { [deviceId]: _, ...rest } = this._curveEdit;
      this._curveEdit = rest;
      this._error = null;
      await this._loadStatus();
    } catch (err) {
      this._error = t("Startwerte konnten nicht gespeichert werden: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  async _takeCurveStart(deviceId, origin) {
    this._busy = true;
    try {
      await api.takeHeatPumpCurveStart(deviceId, origin);
      const { [deviceId]: _, ...rest } = this._curveEdit;
      this._curveEdit = rest;
      this._error = null;
      await this._loadStatus();
    } catch (err) {
      this._error = t("Startwerte nicht übernommen: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  /** The learning phase of the stop margin: a switch the user ends when enough stops are recorded. */
  _renderLearnStops(device) {
    const on = Boolean(device.learn_stops);
    return html`
      <div class="field">
        <span class="field-label">${t("Lernphase Abschaltgrenze")}</span>
        <div class="seg" role="group" aria-label=${t("Lernphase Abschaltgrenze")}>
          <button class=${on ? "on" : ""} aria-pressed=${on} ?disabled=${this._busy} @click=${() => this._setLearnStops(device.id, true)}>${t("an")}</button>
          <button class=${on ? "" : "on"} aria-pressed=${!on} ?disabled=${this._busy} @click=${() => this._setLearnStops(device.id, false)}>${t("aus")}</button>
        </div>
        <span class="help small">${t("An: Sunflow senkt nach jedem Start bis aufs Vorlauf-Ziel und lässt den Kompressor abschalten, wenn er will – nach 5 Minuten startet es ihn wieder. So zeigt die Wärmepumpe, ab wann sie abschaltet. Aus, wenn genug Abschaltungen aufgezeichnet sind: dann bleibt Sunflow über der gelernten Grenze.")}</span>
      </div>
    `;
  }

  async _setLearnStops(deviceId, active) {
    this._busy = true;
    try {
      await api.setHeatPumpLearnStops(deviceId, active);
      this._error = null;
    } catch (err) {
      this._error = t("Lernphase nicht umgeschaltet: {error}", { error: err.message });
    } finally {
      this._busy = false;
      await this._loadStatus();
    }
  }

  /** Compressor stops of the last days with their class and how far Tv was above the effective setpoint. */
  _renderStops(learned) {
    const stops = learned?.stops ?? [];
    const counts = learned?.stop_counts ?? {};
    const time = (iso) => new Date(iso).toLocaleString(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
    const k = (v) => (v == null ? "–" : `${v >= 0 ? "+" : "−"}${decimal(Math.abs(v).toFixed(1))}`);
    return html`
      <div class="title-row"><h3>${t("Abschaltungen des Kompressors")}</h3>
        <span class="small muted">${Object.entries(counts).filter(([, n]) => n).map(([kind, n]) => `${STOP_KINDS()[kind] ?? kind} ${n}`).join(" · ")}</span></div>
      <p class="help small">${t("Jeder Stopp des Kompressors im Heizbetrieb, eingeordnet nach dem, was folgte: Abtauen, Warmwasser, Sperre – sonst „Abschaltregel“. Nur diese zählen für die Abschaltgrenze. „über Soll“: Vorlauf (Spitze) − wirksamer Vorlauf-Soll; UART: derselbe Abstand mit dem Vorlauf des UART-Knotens. Stündlich neu ausgewertet.")}</p>
      ${stops.length
        ? html`<table>
            <thead><tr><th>${t("Zeit")}</th><th>${t("Art")}</th><th>${t("Vorlauf")}</th><th>${t("Soll")}</th><th>${t("über Soll")}</th><th>UART</th></tr></thead>
            <tbody>${stops.map((s) => html`<tr>
              <td>${time(s.at)}</td>
              <td class=${s.kind === "unexplained" ? "warn-text" : ""}>${STOP_KINDS()[s.kind] ?? s.kind}</td>
              <td>${one(s.peak_c)} °C</td>
              <td>${one(s.setpoint_c)} °C</td>
              <td>${k(s.margin_k)} K</td>
              <td>${k(s.margin_uart_k)} K</td>
            </tr>`)}</tbody>
          </table>`
        : html`<p class="small muted">${t("Noch keine Abschaltungen aufgezeichnet.")}</p>`}
    `;
  }

  // ---------- House model ----------

  _houseModelSummary() {
    const result = this._houseModel;
    if (!result) return "";
    return {
      ready: t("gelernt"),
      learning: t("lernt: {days} von {min} Tagen", { days: result.days ?? 0, min: result.min_days ?? 7 }),
      no_rooms: t("keine Räume"),
      no_outside: t("Außentemperatur fehlt"),
      error: t("nicht verfügbar"),
    }[result.state] ?? t("noch unsicher");
  }

  /** What Sunflow learned about the house, in words, with the last week measured and expected. */
  _renderHouseModel() {
    const result = this._houseModel;
    if (!result) return html`<p class="small muted">${t("Lade …")}</p>`;
    if (result.state === "no_rooms") return html`<p class="small muted">${t("Noch keine Räume: unter „Einstellungen“ hinzufügen.")}</p>`;
    const intro = html`<p class="help small">${t("Aus den Raumtemperaturen lernt Sunflow, wie schnell das Haus auskühlt und wie stark Heizung und Sonne es erwärmen. Die Planung (Vorheizen) nutzt das Modell ab {min} Tagen gültigem Heizbetrieb.", { min: result.min_days ?? 7 })}</p>`;
    const messages = {
      no_outside: t("Es fehlt eine Außentemperatur: an der Wärmepumpe oder unter Lernquellen."),
      error: t("Nicht verfügbar: {error}", { error: result.error ?? "" }),
    };
    if (messages[result.state]) return html`${intro}<p class="small muted">${messages[result.state]}</p>${this._renderHouseSource(result)}`;
    if (result.state === "learning") {
      const days = result.days ?? 0;
      const min = result.min_days ?? 7;
      return html`${intro}
        <div class="small">${t("Tage mit Raum- und Außentemperatur: {value} von {total}", { value: days, total: min })}</div>
        <div class="bar"><div style=${`width: ${Math.min(100, (days / min) * 100)}%`}></div></div>
        ${this._renderHouseSource(result)}`;
    }
    const m = result.model;
    const heat = {
      meter: t("Wärme vom gewählten Sensor"),
      measured: t("Wärme der Wärmepumpe gemessen"),
      cop: t("Wärme aus Strom × COP der Wärmepumpe"),
      estimated: t("Wärme aus dem Strom der Wärmepumpe geschätzt (COP aus der Außentemperatur)"),
    }[result.heat_source] ?? t("ohne Wärmemessung – Heizung und innere Wärme lassen sich nicht trennen");
    const tile = (...args) => this._tile(...args);
    const name = (entityId) => this._roomName(entityId);
    const points = result.chart ?? [];
    const v1 = (v) => decimal(v.toFixed(1));
    return html`
      ${intro}
      <p class="small ${result.state === "ready" ? "muted" : "warn-text"}">${result.state === "ready"
        ? t("Aus {days} Tagen · {heat}", { days: result.days, heat })
        : m.plausible
          ? t("Noch unsicher: Die Vorhersage ist nicht besser als „die Temperatur bleibt“. Sunflow nutzt das Modell noch nicht. ({heat})", { heat })
          : t("Noch nicht plausibel – vielleicht heizt etwas, das nicht gemessen wird, oder ein Sensor hängt in der Sonne. ({heat})", { heat })}</p>
      <div class="tiles">
        ${m.tau_h != null ? tile(t("Zeitkonstante"), `${Math.round(m.tau_h)} h`, t("bis zwei Drittel des Unterschieds nach außen weg sind")) : nothing}
        ${tile(t("Abkühlung bei 0 °C außen"), `${v1(m.cooling_k_per_h_at_0)} K/h`, t("ohne Heizung und Sonne, bei {temp} °C innen", { temp: decimal(String(COOLING_INSIDE_C)) }))}
        ${m.loss_w_per_k != null ? tile(t("Wärmeverlust"), `${Math.round(m.loss_w_per_k)} W/K`, t("bei −10 °C außen etwa {kw} kW Heizleistung", { kw: v1(m.loss_w_per_k * (COOLING_INSIDE_C + 10) / 1000) })) : nothing}
        ${m.capacity_kwh_per_k != null ? tile(t("Wärmespeicher"), `${Math.round(m.capacity_kwh_per_k)} kWh/K`, t("Wärme für 1 K wärmer im ganzen Haus")) : nothing}
        ${m.sun_k_per_h != null ? tile(t("Sonne"), `${m.sun_k_per_h >= 0 ? "+" : ""}${v1(m.sun_k_per_h)} K/h`, t("bei voller Sonne")) : nothing}
        ${m.error_k != null ? tile(t("Vorhersage über 24 h"), `± ${v1(m.error_k)} K`, t("„bleibt gleich“ läge bei ± {k} K", { k: v1(m.baseline_k) })) : nothing}
      </div>
      ${this._renderHouseSource(result)}
      ${result.excluded_hours
        ? html`<p class="small muted">${result.source === "heat_pump_samples"
          ? t("{hours} Stunden mit Warmwasser, Abtauen, Nachlauf oder schnellen Änderungen sind ausgelassen.", { hours: result.excluded_hours })
          : t("{hours} Stunden mit Warmwasser oder Abtauen sind ausgelassen: deren Wärme ging nicht ins Haus.", { hours: result.excluded_hours })}</p>`
        : nothing}
      ${result.restless?.length
        ? html`<p class="small warn-text">${t("Auffällig unruhig – vielleicht Sonne, Heizkörper oder Fenster am Sensor: {rooms}. Ein geringeres Gewicht oder ein anderer Sensor hilft.", { rooms: result.restless.map(name).join(", ") })}</p>`
        : nothing}
      ${points.length
        ? html`<div class="title-row"><h3>${t("Letzte 7 Tage")}</h3></div>
          <div class="legend">
            <span><i style=${`background: ${SERIES_COLORS[0]}`}></i>${t("gemessen")}</span>
            <span><i style=${`background: ${SERIES_COLORS[1]}`}></i>${t("Modell (startet jeden Tag neu)")}</span>
          </div>
          ${lineChart(
            [
              { name: t("gemessen"), color: SERIES_COLORS[0], points: points.map((p) => ({ start: p.start, end: p.end, value: p.measured })) },
              { name: t("Modell"), color: SERIES_COLORS[1], dashed: true, points: points.map((p) => ({ start: p.start, end: p.end, value: p.model })) },
            ],
            {
              label: t("Raumtemperatur der letzten 7 Tage, gemessen und vom Modell erwartet"),
              formatY: (v) => `${decimal(v.toFixed(1))} °C`,
              yMin: Math.floor(Math.min(...points.map((p) => p.measured)) - 1),
              yMax: Math.ceil(Math.max(...points.map((p) => p.measured)) + 1),
            },
          )}`
        : nothing}
    `;
  }

  /** Where the house model's hours come from: Home Assistant until the heat pump's samples cover enough days. */
  _renderHouseSource(result) {
    if (!result.source) return nothing;
    return html`<p class="small muted">${result.source === "heat_pump_samples"
      ? t("Quelle: Messwerte der Wärmepumpe (5 min, {days} Tage gültiger Heizbetrieb).", { days: result.coverage_days })
      : t("Quelle: Stundenmittel aus Home Assistant. Umstellung auf die Messwerte der Wärmepumpe ab {min} Tagen gültigem Heizbetrieb (bisher {days}).", { min: result.min_days ?? 7, days: result.coverage_days ?? 0 })}</p>`;
  }

  // ---------- Settings ----------

  _renderSettings() {
    const house = this._status?.house;
    return html`
      ${house?.preheat_k != null && house?.lower_k != null
        ? html`<fieldset class="group">
            <legend>${t("Spielraum der Planung")}</legend>
            <p class="help small">${t("Bei PV-Überschuss oder günstigem Strom darf Sunflow das Haus bis zu so viel über den Raum-Soll heizen bzw. es bei teurem Strom so weit darunter fallen lassen. Ohne Ersparnis bleibt es beim Raum-Soll.")}</p>
            <div class="fields">${this._renderMargin(house)}</div>
          </fieldset>`
        : nothing}
      ${(this._status?.devices ?? []).filter((d) => d.flow_limits).map((d) => this._renderLimits(d, (this._status.devices.length > 1)))}
      ${this._renderHouse()}
    `;
  }

  /** Margin of the planning around the room setpoint (design/heat_pump.md 6, 14): preheat up to +x, lower down to -y. */
  _renderMargin(house) {
    const max = house.margin_max_k ?? 3;
    const step = house.margin_step_k ?? 0.1;
    const setpoint = Number(house.setpoint_c);
    const field = (key, label, note) => html`
      <label class="field">
        <span class="field-label">${label}</span>
        <span class="input-unit"><input type="number" inputmode="decimal" min="0" max=${max} step=${step}
          .value=${String(house[key])} ?disabled=${this._climateBusy}
          @change=${(e) => this._setClimate({ [key]: Number(e.target.value) })}> K</span>
        <span class="small muted">${note}</span>
      </label>`;
    return html`
      ${field("preheat_k", t("Vorheizen bis"), t("= {temp} °C (0 bis {max} K)", { temp: one(setpoint + Number(house.preheat_k)), max: one(max) }))}
      ${field("lower_k", t("Absenken bis"), t("= {temp} °C (0 bis {max} K)", { temp: one(setpoint - Number(house.lower_k)), max: one(max) }))}
    `;
  }

  /** Range of the set value (Max T-Vorlauf) per heat pump; empty: the heating surface type's start limit. */
  _renderLimits(device, named) {
    const limits = device.flow_limits;
    const edit = this._limitsEdit[device.id];
    const value = (key) => (edit ? edit[key] : toDisplay(limits[`${key}_c`], ""));
    const set = (key, text) => (this._limitsEdit = { ...this._limitsEdit, [device.id]: { min: value("min"), max: value("max"), [key]: text } });
    const entity = limits.entity_min_c != null || limits.entity_max_c != null
      ? t("Die Wärmepumpe erlaubt {min}–{max} °C; enger gilt immer.", { min: one(limits.entity_min_c), max: one(limits.entity_max_c) })
      : t("Den Bereich der Wärmepumpe kennt Sunflow noch nicht (Manifest).");
    return html`
      <fieldset class="group">
        <legend>${named ? t("Grenzen des Stellwerts: {name}", { name: device.name }) : t("Grenzen des Stellwerts")}</legend>
        <p class="help small">${t("Max T-Vorlauf, den Sunflow schreiben darf. Höchstens ist zugleich der höchste Vorlauf für Estrich bzw. Heizflächen; nicht höher als die Grenze, die die Wärmepumpe selbst setzt. Leer: Startwert der Heizflächen ({min}–{max} °C). {entity}", { min: one(limits.default_min_c), max: one(limits.default_max_c), entity })}</p>
        <div class="fields">
          <label class="field narrow">
            <span class="field-label">${t("Mindestens")}</span>
            <span class="input-unit"><input inputmode="decimal" .value=${value("min")} placeholder=${toDisplay(limits.default_min_c, "")}
              @input=${(e) => set("min", e.target.value)}> °C</span>
          </label>
          <label class="field narrow">
            <span class="field-label">${t("Höchstens")}</span>
            <span class="input-unit"><input inputmode="decimal" .value=${value("max")} placeholder=${toDisplay(limits.default_max_c, "")}
              @input=${(e) => set("max", e.target.value)}> °C</span>
          </label>
        </div>
        <p class="small muted">${t("Gilt jetzt: {min}–{max} °C", { min: one(limits.effective_min_c), max: one(limits.effective_max_c) })}</p>
        ${edit
          ? html`<div class="actions">
              <button class="primary" ?disabled=${this._busy} @click=${() => this._saveLimits(device.id)}>${t("Speichern")}</button>
              <button class="secondary" @click=${() => { const { [device.id]: _, ...rest } = this._limitsEdit; this._limitsEdit = rest; }}>${t("Abbrechen")}</button>
            </div>`
          : nothing}
      </fieldset>
    `;
  }

  async _saveLimits(deviceId) {
    const edit = this._limitsEdit[deviceId];
    const min = fromDisplay(edit.min, "");
    const max = fromDisplay(edit.max, "");
    if (min === undefined || max === undefined) {
      this._error = t("Grenzen: bitte Zahlen eingeben.");
      return;
    }
    this._busy = true;
    try {
      await api.setHeatPumpFlowLimits(deviceId, { min_c: min, max_c: max });
      const { [deviceId]: _, ...rest } = this._limitsEdit;
      this._limitsEdit = rest;
      this._error = null;
      await this._loadStatus();
    } catch (err) {
      this._error = t("Grenzen konnten nicht gespeichert werden: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  /** The house: rooms, fast change, heat into it, heating surfaces and outside temperature. */
  _renderHouse() {
    const house = this._houseEdit ?? this._house;
    const candidates = this._candidates ?? {};
    const options = (list) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} ${c.unit ?? ""}` }));
    const set = (changes) => (this._houseEdit = { ...house, ...changes });
    const setRoom = (i, changes) => set({ rooms: house.rooms.map((r, k) => (k === i ? { ...r, ...changes } : r)) });
    return html`
      <fieldset class="group">
        <legend>${t("Räume")}</legend>
        <p class="help small">${t("Aus den Raumtemperaturen bildet Sunflow das Raum-Ist und lernt das Hausmodell. Gewicht z. B. nach Fläche; ohne Angabe zählen alle Räume gleich. Sunflow regelt keine einzelnen Räume.")}</p>
        ${house.rooms.map((room, i) => html`<div class="room">
          <div class="field grow">
            <span class="field-label">${t("Temperatur")}</span>
            <sf-picker label=${t("Temperatur")} placeholder=${t("Name oder Entität suchen …")}
              .value=${room.entity_id} .options=${options(candidates.temperature)}
              @change=${(e) => e.detail.value && setRoom(i, { entity_id: e.detail.value })}></sf-picker>
          </div>
          <label class="field narrow">
            <span class="field-label">${t("Gewicht")}</span>
            <span class="input-unit"><input inputmode="decimal" .value=${toDisplay(room.weight, "")}
              @change=${(e) => { const v = fromDisplay(e.target.value, ""); if (v > 0) setRoom(i, { weight: v }); }}></span>
          </label>
          <button class="secondary" @click=${() => set({ rooms: house.rooms.filter((_, k) => k !== i) })}>${t("Entfernen")}</button>
        </div>`)}
        <div><button class="secondary" @click=${() => set({ rooms: [...house.rooms, { entity_id: "", weight: 1 }] })}>${t("Raum hinzufügen")}</button></div>
      </fieldset>
      ${this._renderFastChange(house, set)}
      <fieldset class="group">
        <legend>${t("Haus")}</legend>
        <div class="fields">
          <div class="field">
            <span class="field-label">${t("Wärme ins Haus (optional)")}</span>
            <sf-picker label=${t("Wärme ins Haus (optional)")} empty-label=${t("aus der Wärmepumpe")} placeholder=${t("Name oder Entität suchen …")}
              .value=${house.heat_entity ?? ""} .options=${options(candidates.power)}
              @change=${(e) => set({ heat_entity: e.detail.value || null })}></sf-picker>
            <span class="help small">${t("Wärmeleistung in W, z. B. vom Wärmemengenzähler der Heizung. Ohne Angabe nimmt Sunflow die Wärme der Wärmepumpe.")}</span>
          </div>
          <label class="field">
            <span class="field-label">${t("Heizflächen")}</span>
            <select @change=${(e) => set({ heating_surface: e.target.value })}>
              ${Object.entries(SURFACES()).map(([value, label]) => html`<option value=${value} ?selected=${(house.heating_surface ?? "underfloor") === value}>${label}</option>`)}
            </select>
            <span class="help small">${t("Startsteilheit der Heizkurve, wenn die Wärmepumpe keine eigene liefert (Fußboden 0,6, gemischt 1,0, Heizkörper 1,4), und Nachstellzeit der Korrektur.")}</span>
            ${this._houseEdit && (this._houseEdit.heating_surface ?? "underfloor") !== (this._house.heating_surface ?? "underfloor")
              ? html`<span class="small warn-text">${t("Nach dem Speichern gilt die neue Startsteilheit (falls die Wärmepumpe keine liefert); die Heizkurve wird neu gelernt.")}</span>`
              : nothing}
          </label>
          <label class="field">
            <span class="field-label">${t("Außentemperatur")}</span>
            <select @change=${(e) => set({ outside_source: e.target.value })}>
              ${Object.entries(OUTSIDE_SOURCES()).map(([value, label]) => html`<option value=${value} ?selected=${(house.outside_source ?? "auto") === value}>${label}</option>`)}
            </select>
            <span class="help small">${t("Für die Heizkurve, das Hausmodell und die Aufzeichnung. Den eigenen Sensor wählst du unter Lernquellen.")}</span>
          </label>
        </div>
      </fieldset>
      ${this._houseEdit
        ? html`<div class="actions">
            <button class="primary" ?disabled=${this._busy || house.rooms.some((r) => !r.entity_id)} @click=${() => this._saveHouse()}>${t("Speichern")}</button>
            <button class="secondary" @click=${() => (this._houseEdit = null)}>${t("Abbrechen")}</button>
          </div>`
        : nothing}
    `;
  }

  /** Setting house.fast_change: when a room does not count for a while (airing, cooking ...). */
  _renderFastChange(house, set) {
    const current = { ...FAST_CHANGE_DEFAULTS, ...(house.fast_change ?? {}) };
    const input = (key, unit) => html`<input inputmode="decimal" class="short" .value=${toDisplay(current[key], "")}
      @change=${(e) => {
        const [low, high, whole] = FAST_CHANGE_RANGES[key];
        const v = fromDisplay(e.target.value, "");
        if (v == null || v < low || v > high || (whole && !Number.isInteger(v))) {
          e.target.value = toDisplay(current[key], "");
          return;
        }
        set({ fast_change: { ...current, [key]: v } });
      }}> ${unit}`;
    return html`
      <fieldset class="group">
        <legend>${t("Schnelle Änderung (Lüften, Kochen …)")}</legend>
        <p class="help small">${t("Ändert sich ein Raum so schnell, zählt er bis zum Ende nicht für das Raum-Ist und die Korrektur wartet; danach zählt er mit seinem Rückstand oder Überschuss.")}</p>
        <div class="fields">
          <div class="field">
            <span class="field-label">${t("Beginn ab")}</span>
            <span class="input-unit">${input("start_k", "K")} ${t("in")} ${input("start_min", "min")}</span>
          </div>
          <div class="field">
            <span class="field-label">${t("Vorbei bei höchstens")}</span>
            <span class="input-unit">${input("settle_k", "K")} ${t("in")} ${input("settle_min", "min")}</span>
          </div>
          <div class="field">
            <span class="field-label">${t("Höchstens")}</span>
            <span class="input-unit">${input("max_h", "h")}</span>
          </div>
        </div>
      </fieldset>
    `;
  }

  async _saveHouse() {
    this._busy = true;
    try {
      this._house = await api.setHouse(this._houseEdit);
      this._houseEdit = null;
      this._houseModel = null;
      this._loadHouseModel();
    } catch (err) {
      this._error = t("Haus konnte nicht gespeichert werden: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  static styles = [toggleSheet, diagramStyles, css`
    :host { display: flex; flex-direction: column; gap: 12px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0; font-size: 16px; font-weight: 600; }
    h3 { margin: 0; font-size: 14px; font-weight: 600; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    p { margin: 0; }
    .title-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px; justify-content: space-between; }
    .title-row > :is(h1, h2, h3) { flex: 0 1 auto; }
    .device-name { margin-top: 8px; }
    .status { padding: 12px 14px; border-radius: 10px; background: var(--sf-subtle); border: 1px solid var(--sf-border); display: flex; flex-direction: column; gap: 4px; }
    .status.warn { background: var(--sf-warn-bg); color: var(--sf-warn-text); border-color: transparent; }
    .status.error { background: var(--sf-error-bg); color: var(--sf-error-text); border-color: transparent; }
    .status-main { font-weight: 600; font-size: 15px; }
    .status-sub { font-size: 14px; color: var(--sf-muted); }
    .status.warn .status-sub, .status.error .status-sub { color: inherit; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
    .chip { font-size: 12px; padding: 1px 8px; border-radius: 10px; background: var(--sf-neutral-bg); color: var(--sf-neutral-text); }
    .chip.ok { background: var(--sf-ok-bg); color: var(--sf-ok-text); }
    .chip.warn { background: var(--sf-warn-bg); color: var(--sf-warn-text); }
    .card, .sec { border: 1px solid var(--sf-border); border-radius: 10px; }
    .card { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
    .controls { display: grid; grid-template-columns: auto auto 1fr; gap: 16px 28px; align-items: start; }
    .main-tiles { grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); }
    .seg { display: inline-flex; flex-wrap: wrap; border: 1px solid var(--sf-border-strong); border-radius: 8px; overflow: hidden; width: fit-content; }
    .seg button { border: 0; background: var(--sf-surface); padding: 0 14px; min-height: 40px; cursor: pointer; }
    .seg button + button { border-left: 1px solid var(--sf-border-strong); }
    .seg button.on { background: var(--sf-primary); color: var(--sf-on-primary); }
    .small-seg button { min-height: 32px; padding: 0 10px; font-size: 13px; }
    .stepper { display: flex; align-items: center; gap: 8px; }
    .stepper .secondary { min-width: 44px; padding: 0; font-size: 18px; }
    .stepper input { width: 5.5em; flex: none; text-align: center; font-weight: 600; }
    .sec { display: flex; flex-direction: column; }
    .section-head { display: flex; align-items: center; gap: 10px; padding: 12px 14px; cursor: pointer; }
    .sec-toggle { border: 0; background: none; padding: 0; width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; }
    .chev { width: 8px; height: 8px; border-right: 2px solid var(--sf-muted); border-bottom: 2px solid var(--sf-muted); transform: rotate(-45deg); transition: transform .15s; }
    .chev.open { transform: rotate(45deg); }
    .sum { margin-left: auto; color: var(--sf-muted); font-size: 13px; text-align: right; }
    .sec > .help { padding: 0 14px 8px 44px; }
    .sec-body { padding: 0 14px 14px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
    .detail { display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--sf-border); padding-top: 10px; }
    .row2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px 24px; }
    .row2 > div { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
    .help { color: var(--sf-muted); }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; font-size: 13px; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input, select {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .group { border: 1px solid var(--sf-border); border-radius: 8px; padding: 12px 14px; margin: 0; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
    .group legend { font-weight: 600; font-size: 14px; padding: 0 4px; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .primary, .secondary { min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .warn-text { color: var(--sf-warn-text); }
    .room { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
    .room .grow { flex: 1 1 260px; }
    .room .narrow { flex: 0 0 90px; }
    .input-unit input.short { width: 4.5em; flex: none; }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    table { border-collapse: collapse; width: 100%; font-size: 14px; display: block; overflow-x: auto; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--sf-border); font-variant-numeric: tabular-nums; }
    th { font-size: 13px; color: var(--sf-muted); font-weight: 600; }
    td:first-child { white-space: nowrap; }
    .legend i.cross { width: 10px; height: 10px; background: linear-gradient(45deg, transparent 42%, var(--sf-error-text) 42% 58%, transparent 58%), linear-gradient(-45deg, transparent 42%, var(--sf-error-text) 42% 58%, transparent 58%); }
    .tag { font-size: 11px; padding: 0 6px; border-radius: 8px; background: var(--sf-neutral-bg); color: var(--sf-muted); white-space: nowrap; }
    .tag.real { background: var(--sf-ok-bg); color: var(--sf-ok-text); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
    .share { margin: 8px 0 12px; }
    .formula { background: var(--sf-subtle); border: 1px solid var(--sf-border); border-radius: 8px; padding: 10px 14px; margin: 0 0 12px; line-height: 1.6; }
    .formula-main { font-weight: 700; font-size: 1.05em; }
    .share-bar { height: 10px; border-radius: 5px; background: var(--sf-subtle); border: 1px solid var(--sf-border); overflow: hidden; margin-bottom: 4px; }
    .share-learned { display: block; height: 100%; background: var(--sf-primary); }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 10px 12px; }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .tile.warn .value { color: var(--sf-warn-text); }
    .bar { height: 8px; background: var(--sf-neutral-bg); border-radius: 4px; overflow: hidden; margin: 4px 0; }
    .bar > div { height: 100%; background: var(--sf-primary); }
    .bar.emergency > div { background: var(--sf-warn-text); }
    .alert.warn { padding: 10px 14px; border-radius: 8px; background: var(--sf-warn-bg, var(--sf-subtle)); color: var(--sf-warn-text); font-weight: 600; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
    @media (max-width: 760px) {
      .controls { grid-template-columns: 1fr; }
      .sum { display: none; }
    }
  `];
}

customElements.define("sf-heat-pump", SunflowHeatPump);
