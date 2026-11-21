import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { fromDisplay, toDisplay } from "../lib/format.js";
import "../components/picker.js";
import { decimal, locale, t } from "../lib/i18n.js";
import { lineChart, SERIES_COLORS } from "../components/charts.js";

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
// Where the flow target starts from (design/heat_pump.md, 8.1)
const FLOW_SOURCES = () => ({
  base: t("Basis"),
  house_model: t("Kennlinie mit Wärme aus dem Hausmodell"),
  heat_recent: t("Kennlinie mit Wärme der letzten 3 h"),
});
/** Why the flow target uses the base, in words. */
function sourceReasonText(reason) {
  return {
    no_curve: t("noch keine Kennlinie"),
    curve_learning: t("Kennlinie lernt noch (zu wenig Daten)"),
    curve_implausible: t("Kennlinie nicht plausibel"),
    curve_unsure: t("Kennlinie sagt den Vorlauf nicht besser voraus als „Vorlauf bleibt“"),
    curve_stale: t("Heizflächentyp geändert, Kennlinie wird neu bewertet"),
    no_heat: t("benötigte Wärme unbekannt (kein Hausmodell, weniger als 2 h gültiger Heizbetrieb in den letzten 3 h)"),
    heat_low: t("benötigte Wärme zu klein"),
  }[reason] ?? reason;
}
// Status of the latest values; refreshed while the view is open
const STATUS_EVERY_MS = 60_000;
// Days the counts of the writes Sunflow would have caused cover
const WRITES_DAYS = 7;
// What the protection layer is doing (design/heat_pump.md, 8.4)
const GUARD_PHASES = () => ({
  idle: t("Pause: Startregel"),
  hot_water: t("Warmwasser: Start danach vorbereiten"),
  defrost: t("Abtauen: nichts schreiben"),
  start_hold: t("nach dem Start: halten"),
  running: t("Heizbetrieb"),
  blocked: t("Sperre: Wärmepumpe aus"),
  inactive: t("schreibt nicht"),
});
// Reasons of a write Sunflow would cause
const WRITE_REASONS = () => ({
  normal: t("normal"),
  start: t("Start"),
  track: t("Nachführen"),
  block_on: t("Sperre an"),
  block_off: t("Sperre aus"),
});
// Why the block would be set
const BLOCK_REASONS = () => ({
  room_warm: t("Raum deutlich über dem Ziel"),
  min_power: t("Mindestleistung über dem Bedarf"),
  foreign: t("nicht von Sunflow gesetzt"),
});
// Limits the flow target or the heat pump reached (8.3)
const LIMITS = () => ({
  limit_max: t("Stellwert an der Obergrenze"),
  limit_min: t("Stellwert an der Untergrenze"),
  power_max: t("Wärmepumpe an der Höchstleistung"),
  power_min: t("Wärmepumpe an der Mindestleistung"),
  implausible: t("Vorlauf-Ziel nicht plausibel"),
});
const START_KINDS = () => ({ normal: t("normal"), hot_water: t("nach Warmwasser"), defrost: t("nach Abtauen") });
const VALUE_SOURCES = () => ({ start: t("Startwert"), learned: t("gelernt"), raised: t("nach Abschaltung erhöht") });
/** Why Sunflow would write nothing, in words. */
function inactiveText(reason) {
  const fixed = {
    mode_off: t("Modus Aus"),
    unusable: t("Gerät nicht erreichbar oder passt nicht"),
    fault: t("Störung"),
    tv_missing: t("Vorlauf unbekannt"),
    setpoint_missing: t("Max T-Vorlauf unbekannt"),
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
    ventilation: t("Lüften erkannt"),
    setpoint_change: t("Raum-Soll geändert, Raum folgt noch"),
    strategy_unknown: t("Strategie unbekannt"),
    no_sample: t("keine Messwerte"),
  }[reason];
  if (fixed) return fixed;
  if (reason.startsWith("program_")) return t("Programm: {state}", { state: PROGRAMS()[reason.slice(8)] ?? reason.slice(8) });
  if (reason.startsWith("activity_")) return t("Aktivität: {state}", { state: ACTIVITIES()[reason.slice(9)] ?? reason.slice(9) });
  return reason;
}

/** The house: its rooms and what Sunflow learned from them (settings area "Wärmepumpe"). */
class SunflowHeatPump extends LitElement {
  static properties = {
    _candidates: { state: true },
    _house: { state: true },
    _houseEdit: { state: true },
    _houseModel: { state: true },
    _status: { state: true },
    _writes: { state: true },
    _limitsEdit: { state: true },
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
    // Per heat pump the range of the set value being edited: { min, max } as typed
    this._limitsEdit = {};
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
    } catch (err) {
      this._status = { error: err.message, devices: [], house: null };
    }
    this._loadWrites();
  }

  /** Per day the writes Sunflow would have caused and the unwanted shutdowns (shadow). */
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

  async _load() {
    try {
      this._house = await api.house();
      this._error = null;
    } catch (err) {
      this._error = t("Einstellungen nicht verfügbar: {error}", { error: err.message });
    }
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
    return html`
      <div class="heading">
        <h1>${t("Wärmepumpe")}</h1>
        <p class="muted small">${t("Schattenbetrieb: Sunflow rechnet den Vorlauf aus und zeigt ihn an, schreibt aber nichts an die Wärmepumpe.")}</p>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${this._renderClimate()}
      ${(this._status?.devices ?? []).map((device) => this._renderDevice(device))}
      ${this._renderHouse()}
    `;
  }

  /** Room setpoint and mode (climate entity "Haus"), room temperature and a setpoint change being followed. */
  _renderClimate() {
    const house = this._status?.house;
    if (this._status?.error) return html`<div class="alert error" role="alert">${t("Status nicht verfügbar: {error}", { error: this._status.error })}</div>`;
    if (!house) return nothing;
    const one = (v) => (v == null ? "–" : decimal(Number(v).toFixed(1)));
    const settling = house.settling;
    return html`
      <section class="device">
        <span class="device-title">${t("Raum-Soll")}</span>
        <p class="small muted">${t("Auch als Thermostat „Haus“ in Home Assistant, z. B. für Zeitpläne. Wer nicht heizen will, stellt den Modus auf Aus.")}</p>
        <div class="fields">
          <label class="field">
            <span class="field-label">${t("Raum-Soll")}</span>
            <span class="input-unit"><input type="number" inputmode="decimal" min=${house.min_c} max=${house.max_c} step=${house.step_c}
              .value=${String(house.setpoint_c)} ?disabled=${this._climateBusy}
              @change=${(e) => this._setClimate({ setpoint_c: Number(e.target.value) })}> °C</span>
            <span class="small muted">${t("{min} bis {max} °C", { min: one(house.min_c), max: one(house.max_c) })}</span>
          </label>
          <label class="field">
            <span class="field-label">${t("Modus")}</span>
            <select ?disabled=${this._climateBusy} @change=${(e) => this._setClimate({ mode: e.target.value })}>
              <option value="heat" ?selected=${house.mode === "heat"}>${t("Heizen")}</option>
              <option value="off" ?selected=${house.mode === "off"}>${t("Aus (Sunflow schreibt nichts)")}</option>
            </select>
          </label>
          <div class="tile">
            <div class="small muted">${t("Raum-Ist")}</div>
            <div class="value">${one(house.room_c)} °C</div>
            <div class="small muted">${t("aus {used} von {total} Räumen", { used: house.rooms_used.length, total: house.rooms_used.length + house.rooms_missing.length + house.rooms_stale.length + (house.rooms_ventilated ?? []).length })}</div>
          </div>
        </div>
        ${house.rooms_stale.length || house.rooms_missing.length
          ? html`<p class="small warn-text">${t("Nicht berücksichtigt (nicht verfügbar oder seit über 2 h keine Meldung): {rooms}", { rooms: [...house.rooms_missing, ...house.rooms_stale].map((e) => this._roomName(e)).join(", ") })}</p>`
          : nothing}
        ${this._renderVentilation(house, one)}
        ${settling
          ? html`<p class="small muted">${t("Raum-Soll geändert auf {target} °C: Die Korrektur wartet, bis der Raum ihn erreicht (höchstens 24 h).", { target: one(settling.target_c) })}</p>`
          : nothing}
      </section>
    `;
  }

  /** Rooms being aired now (left out of room temperature and correction) and events of the last 24 h. */
  _renderVentilation(house, one) {
    const active = house.ventilation ?? [];
    const recent = house.ventilation_recent ?? [];
    if (!active.length && !recent.length) return nothing;
    const time = (iso) => new Date(iso).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
    const drop = (e) => decimal(Number(e.drop_k).toFixed(1));
    return html`
      ${active.length
        ? html`<p class="small warn-text">${t("Lüften erkannt, Raum zählt vorerst nicht und die Korrektur wartet (höchstens 2 h): {rooms}", {
          rooms: active.map((e) => t("{room} seit {time} ({from} → {low} °C)", { room: this._roomName(e.entity_id), time: time(e.since), from: one(e.start_c), low: one(e.low_c) })).join(", "),
        })}</p>`
        : nothing}
      ${recent.length
        ? html`<p class="small muted">${t("Lüften in den letzten 24 h: {events}", {
          events: recent.map((e) => t("{room} {from}–{until} (−{drop} K)", { room: this._roomName(e.entity_id), from: time(e.since), until: time(e.until), drop: drop(e) })).join(", "),
        })}</p>`
        : nothing}
    `;
  }

  _roomName(entityId) {
    return (this._candidates?.temperature ?? []).find((c) => c.entity_id === entityId)?.name ?? entityId;
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

  /** A heat pump: link, states, whether it heats validly, live values and the shadow flow target. */
  _renderDevice(device) {
    const state = device.state;
    const one = (v) => (v == null ? "–" : decimal(Number(v).toFixed(1)));
    const tile = (label, value, help = null) => html`<div class="tile"><div class="small muted">${label}</div><div class="value">${value}</div>${help ? html`<div class="small muted">${help}</div>` : nothing}</div>`;
    const flow = state?.flow;
    const values = state?.values ?? {};
    return html`
      <section class="device">
        <span class="device-title">${device.name}</span>
        <p class="small ${device.status === "compatible" ? "muted" : "warn-text"}">${LINK()[device.status] ?? device.status}${device.mac ? ` · ${device.mac}` : ""}</p>
        ${(device.problems ?? []).length
          ? html`<ul class="small warn-text">${device.problems.map((p) => html`<li>${p.reason}${p.optional ? ` (${t("optional")})` : ""}</li>`)}</ul>`
          : nothing}
        ${state?.blocked ? html`<div class="alert warn" role="status">${t("Raumthermostat-Sperre aktiv: Die Wärmepumpe heizt nicht.")}</div>` : nothing}
        ${state?.fault ? html`<div class="alert error" role="alert">${t("Störung der Wärmepumpe: {error}", { error: state.error ?? "–" })}</div>` : nothing}
        ${!state
          ? html`<p class="small muted">${t("Noch keine Messwerte.")}</p>`
          : html`
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
              ? t("Gültiger Heizbetrieb: Sunflow rechnet die Korrektur und lernt.")
              : t("Eingefroren: {reasons}", { reasons: state.frozen.map(frozenText).join(", ") })}</p>
            ${flow ? this._renderFlow(flow, one, tile) : nothing}
            ${flow?.guard ? this._renderGuard(flow.guard, this._writes[device.id], one, tile) : nothing}`}
        ${device.flow_limits ? this._renderLimits(device, one) : nothing}
      </section>
    `;
  }

  /** Range of the set value (Max T-Vorlauf) per heat pump; empty: the heating surface type's start limit. */
  _renderLimits(device, one) {
    const limits = device.flow_limits;
    const edit = this._limitsEdit[device.id];
    const value = (key) => (edit ? edit[key] : toDisplay(limits[`${key}_c`], ""));
    const set = (key, text) => (this._limitsEdit = { ...this._limitsEdit, [device.id]: { min: value("min"), max: value("max"), [key]: text } });
    const entity = limits.entity_min_c != null || limits.entity_max_c != null
      ? t("Die Wärmepumpe erlaubt {min}–{max} °C; enger gilt immer.", { min: one(limits.entity_min_c), max: one(limits.entity_max_c) })
      : t("Den Bereich der Wärmepumpe kennt Sunflow noch nicht (Manifest).");
    return html`
      <fieldset class="group">
        <legend>${t("Grenzen des Stellwerts")}</legend>
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
        <p class="small muted">${t("Max T-Vorlauf, den Sunflow schreiben darf. Höchstens ist zugleich der höchste Vorlauf für Estrich bzw. Heizflächen. Leer: Startwert der Heizflächen ({min}–{max} °C). {entity}", { min: one(limits.default_min_c), max: one(limits.default_max_c), entity })}</p>
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

  /** Source, base or curve value, correction and flow target; shadow: computed, not written. */
  _renderFlow(flow, one, tile) {
    const signed = (v) => (v == null ? "–" : `${v >= 0 ? "+" : "−"}${decimal(Math.abs(v).toFixed(1))} K`);
    const source = flow.source ?? "base";
    const curve = source !== "base";
    const targetHelp = flow.reason === "no_base"
      ? t("noch kein wirksamer Vorlauf-Soll aus gültigem Heizbetrieb")
      : curve ? t("Kennlinie + Korrektur") : t("Basis + Korrektur");
    const kw = (w) => decimal((w / 1000).toFixed(2));
    return html`
      <div class="learned">
        <span class="field-label">${t("Vorlauf-Ziel (Schatten)")}</span>
        <div class="tiles">
          ${tile(t("Vorlauf-Ziel"), flow.target_c == null ? "–" : `${one(flow.target_c)} °C`, targetHelp)}
          ${tile(t("Quelle"), FLOW_SOURCES()[source] ?? source, curve ? null : sourceReasonText(flow.source_reason))}
          ${curve
            ? tile(t("Kennlinie"), `${one(flow.curve_c)} °C`, t("für {kw} kW bei Raum-Soll", { kw: kw(flow.heat_needed_w) }))
            : nothing}
          ${tile(t("Basis"), flow.base_c == null ? "–" : `${one(flow.base_c)} °C`, curve ? t("wirksamer Vorlauf-Soll, gilt ohne Kennlinie") : t("wirksamer Vorlauf-Soll im gültigen Heizbetrieb"))}
          ${tile(t("Korrektur"), signed(flow.correction_k), t("aus Raum-Ist und Raum-Soll, Nachstellzeit {ti} h, höchstens ± {limit} K", { ti: decimal(String(flow.parameters.ti_h)), limit: decimal(String(flow.parameters.limit_k)) }))}
        </div>
        ${source === "heat_recent"
          ? html`<p class="small muted">${t("Die Wärme der letzten 3 h entsteht aus dem Vorlauf, den die Wärmepumpe gerade fährt: Die Kennlinie bildet nur den aktuellen Zustand nach, die Richtung gibt die Korrektur vor.")}</p>`
          : nothing}
        <p class="small muted">${t("Wechselt die Quelle, springt das Vorlauf-Ziel im Schattenbetrieb einmal; die Korrektur bleibt.")}</p>
        <p class="small muted">${flow.frozen.length
          ? t("Korrektur angehalten: {reasons}", { reasons: flow.frozen.map(frozenText).join(", ") })
          : t("Korrektur läuft.")} ${t("Im Schattenbetrieb wirkt die Korrektur nicht zurück und kann bis an ihre Grenze laufen; beim Aktivschalten beginnt sie bei 0.")}</p>
        ${(flow.limited ?? []).length
          ? html`<p class="small warn-text">${t("An einer Grenze, die Korrektur läuft nicht weiter darüber hinaus: {limits}", { limits: flow.limited.map((l) => LIMITS()[l] ?? l).join(", ") })}</p>`
          : nothing}
      </div>
      ${this._renderCurve(flow.curve, flow.surface, tile)}
    `;
  }

  /** Set value and writes Sunflow would cause (protection layer, shadow), learned values, daily counts. */
  _renderGuard(guard, writes, one, tile) {
    const time = (iso) => new Date(iso).toLocaleString(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
    const two = (v) => (v == null ? "–" : decimal(Number(v).toFixed(1)));
    const minutes = (s) => (s == null ? "–" : decimal((s / 60).toFixed(0)));
    const last = guard.writes?.[0];
    const values = guard.values ?? {};
    const sources = values.sources ?? {};
    const source = (key) => VALUE_SOURCES()[sources[key]] ?? sources[key] ?? "";
    const writeText = (w) => w.reason === "block_on" || w.reason === "block_off"
      ? WRITE_REASONS()[w.reason]
      : t("{reason}: {value} °C", { reason: WRITE_REASONS()[w.reason] ?? w.reason, value: one(w.value_c) });
    return html`
      <div class="learned">
        <span class="field-label">${t("Stellwert und Schutz (Schatten)")}</span>
        <p class="small muted">${t("Sunflow rechnet aus, was es an die Wärmepumpe schreiben würde (Max T-Vorlauf und Raumthermostat-Sperre), schreibt aber nichts.")}</p>
        ${guard.block && guard.block_reason === "foreign"
          ? html`<div class="alert warn" role="status">${t("Raumthermostat-Sperre aktiv, nicht von Sunflow gesetzt: Die Wärmepumpe heizt nicht. Sunflow hebt sie nicht auf – bitte selbst aufheben, wenn sie nicht gewollt ist.")}</div>`
          : guard.block ? html`<div class="alert warn" role="status">${t("Sunflow würde die Raumthermostat-Sperre setzen: {reason}", { reason: BLOCK_REASONS()[guard.block_reason] ?? guard.block_reason ?? "–" })}</div>` : nothing}
        <div class="tiles">
          ${tile(t("Stellwert"), guard.setpoint_c == null ? "–" : `${one(guard.setpoint_c)} °C`, last ? t("würde schreiben {time}: {what}", { time: time(last.at), what: writeText(last) }) : t("würde noch nichts schreiben"))}
          ${tile(t("Vorlauf-Ziel in Grenzen"), guard.desired_c == null ? "–" : `${one(guard.desired_c)} °C`, t("Grenzen {min}–{max} °C", { min: one(guard.min_c), max: one(guard.max_c) }))}
          ${tile(t("Untergrenze Schutz"), guard.floor_c == null ? "–" : `${one(guard.floor_c)} °C`, GUARD_PHASES()[guard.phase] ?? guard.phase)}
          ${tile(t("Sperre"), guard.block ? (guard.block_reason === "foreign" ? t("an (nicht von Sunflow)") : t("an")) : t("aus"), guard.block_since ? t("seit {time}", { time: time(guard.block_since) }) : null)}
        </div>
        ${guard.inactive?.length
          ? html`<p class="small muted">${t("Würde nichts schreiben: {reasons}", { reasons: guard.inactive.map(inactiveText).join(", ") })}</p>`
          : nothing}
        ${guard.implausible_k != null
          ? html`<p class="small warn-text">${t("Vorlauf-Ziel nicht plausibel ({k} K vom wirksamen Soll bzw. der Kennlinie): würde nicht geschrieben.", { k: two(guard.implausible_k) })}</p>`
          : nothing}
        <div class="tiles">
          ${tile(t("Schwankung im Betrieb"), `${two(values.steady_swing)} K`, source("steady_swing"))}
          ${["normal", "hot_water", "defrost"].map((kind) => tile(
            t("Sprung beim Start ({kind})", { kind: START_KINDS()[kind] }),
            `${two(values.start_jump?.[kind])} K`,
            t("{source}, beruhigt nach {minutes} min", { source: source(`start_jump.${kind}`), minutes: minutes(values.start_settle_s?.[kind]) }),
          ))}
          ${tile(t("Leistungsbereich"), `${decimal((values.power_min_w / 1000).toFixed(1))}–${decimal((values.power_max_w / 1000).toFixed(1))} kW`, source("power"))}
        </div>
        ${guard.writes?.length
          ? html`<p class="small muted">${t("Würde schreiben (zuletzt): {writes}", { writes: guard.writes.slice(0, 8).map((w) => `${time(w.at)} ${writeText(w)}`).join(" · ") })}</p>`
          : nothing}
        ${guard.unwanted_stops?.length
          ? html`<p class="small warn-text">${t("Ungewollte Abschaltungen (zuletzt): {stops}", { stops: guard.unwanted_stops.slice(0, 5).map((s) => t("{time} bei {peak} °C (Soll {setpoint} °C)", { time: time(s.at), peak: one(s.peak_c), setpoint: one(s.setpoint_c) })).join(" · ") })}</p>`
          : nothing}
        ${this._renderWriteCounts(writes)}
      </div>
    `;
  }

  /** Per day: writes Sunflow would have caused by reason and unwanted shutdowns, against the budget. */
  _renderWriteCounts(writes) {
    if (!writes) return nothing;
    if (writes.error) return html`<p class="small warn-text">${t("Zählung nicht verfügbar: {error}", { error: writes.error })}</p>`;
    const budget = writes.budget ?? {};
    const day = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(locale(), { weekday: "short", day: "2-digit", month: "2-digit" });
    const over = (count, limit) => (limit != null && count > limit ? "warn-text" : "");
    return html`
      <span class="field-label">${t("Schreibvorgänge je Tag (würde schreiben)")}</span>
      <table>
        <thead><tr>
          <th>${t("Tag")}</th><th>${t("normal")}</th><th>${t("Start")}</th><th>${t("Nachführen")}</th>
          <th>${t("Sperre an/aus")}</th><th>${t("ungewollte Abschaltungen")}</th>
        </tr></thead>
        <tbody>
          ${writes.days.map((d) => html`<tr>
            <td>${day(d.date)}</td>
            <td class=${over(d.normal, budget.per_day)}>${d.normal}</td>
            <td class=${over(d.protect, budget.emergency_per_day)}>${d.start}</td>
            <td class=${over(d.protect, budget.emergency_per_day)}>${d.track}</td>
            <td>${d.block_on}/${d.block_off}</td>
            <td class=${d.unwanted_stops ? "warn-text" : ""}>${d.unwanted_stops}</td>
          </tr>`)}
        </tbody>
      </table>
      <p class="small muted">${t("Geplantes Budget (ab dem Aktivschalten): {normal} normale Schreibvorgänge und {emergency} für Start und Nachführen je Tag, höchstens {hour} je Stunde. Im Schatten gelten noch kein Mindestabstand und kein Budget; die Sperre zählt nicht (nicht im EEPROM).", { normal: budget.per_day ?? "–", emergency: budget.emergency_per_day ?? "–", hour: budget.per_hour ?? "–" })}</p>
    `;
  }

  /** The learned heating curve (shadow): K, n, spread, quality and the data it rests on. */
  _renderCurve(curve, surface, tile) {
    if (!curve) {
      return html`<div class="learned"><span class="field-label">${t("Kennlinie (Schatten)")}</span>
        <p class="small muted">${t("Noch nicht berechnet.")}</p></div>`;
    }
    const two = (v) => (v == null ? "–" : decimal(Number(v).toFixed(2)));
    const one = (v) => (v == null ? "–" : decimal(Number(v).toFixed(1)));
    const state = {
      ready: t("nutzbar"),
      learning: t("lernt noch (zu wenig Daten)"),
      implausible: t("nicht plausibel"),
      unsure: t("nicht besser als „Vorlauf bleibt“"),
    }[curve.state] ?? curve.state;
    const stale = surface && curve.surface !== surface;
    return html`
      <div class="learned">
        <span class="field-label">${t("Kennlinie (Schatten)")}</span>
        <p class="small ${curve.state === "ready" && !stale ? "muted" : "warn-text"}">${t("Wärmeabgabe der Heizflächen Q = K · (Tw − T_Raum)^n: {state}", { state })}</p>
        ${stale ? html`<p class="small warn-text">${t("Heizflächentyp geändert: Die Kennlinie wird neu bewertet, die gemessenen Daten bleiben erhalten.")}</p>` : nothing}
        <div class="tiles">
          ${tile("K", curve.k == null ? "–" : `${decimal(Number(curve.k).toFixed(0))} W/K^n`, t("gelernt"))}
          ${tile("n", decimal(String(curve.n)), SURFACES()[curve.surface] ?? curve.surface)}
          ${tile(t("Spreizung"), `${one(curve.typical_spread_k)} K`, t("bei {kw} kW (typisch); {s0} K + {s1} K je kW", { kw: curve.typical_heat_w == null ? "–" : decimal((curve.typical_heat_w / 1000).toFixed(1)), s0: two(curve.spread0_k), s1: two(curve.spread_k_per_kw) }))}
          ${tile(t("Güte"), curve.error_k == null ? "–" : `± ${two(curve.error_k)} K`, curve.baseline_k == null ? t("Vorlauf auf zurückgehaltenen Tagen") : t("„Vorlauf bleibt“ läge bei ± {k} K", { k: two(curve.baseline_k) }))}
          ${tile(t("Datenumfang"), t("{count} Intervalle", { count: curve.intervals }), t("{days} Tage, davon {held} Intervalle zum Prüfen; nötig {min} Intervalle aus {min_days} Tagen", { days: curve.days, held: curve.held, min: curve.min_intervals, min_days: curve.min_days }))}
        </div>
        <p class="small muted">${t("Gelernt nur aus gültigem Heizbetrieb mit laufendem Kompressor, ohne Nachlauf und ohne Lüften; stündlich neu berechnet.")}</p>
      </div>
    `;
  }

  /** The house: rooms and heat into it; below what Sunflow learned from them. */
  _renderHouse() {
    if (this._candidates === null) this._loadCandidates();
    const house = this._houseEdit ?? this._house;
    const candidates = this._candidates ?? {};
    const options = (list) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} ${c.unit ?? ""}` }));
    const set = (changes) => (this._houseEdit = { ...house, ...changes });
    const setRoom = (i, changes) => set({ rooms: house.rooms.map((r, k) => (k === i ? { ...r, ...changes } : r)) });
    return html`
      <section class="device">
        <span class="device-title">${t("Haus")}</span>
        <p class="small muted">${t("Aus den Raumtemperaturen lernt Sunflow, wie schnell das Haus auskühlt und wie stark Heizung und Sonne es erwärmen. Sunflow regelt keine einzelnen Räume.")}</p>
        <fieldset class="group">
          <legend>${t("Räume")}</legend>
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
          <p class="small muted">${t("Gewicht z. B. nach Fläche; ohne Angabe zählen alle Räume gleich.")}</p>
          <div class="fields">
            <div class="field">
              <span class="field-label">${t("Wärme ins Haus (optional)")}</span>
              <sf-picker label=${t("Wärme ins Haus (optional)")} empty-label=${t("aus der Wärmepumpe")} placeholder=${t("Name oder Entität suchen …")}
                .value=${house.heat_entity ?? ""} .options=${options(candidates.power)}
                @change=${(e) => set({ heat_entity: e.detail.value || null })}></sf-picker>
              <span class="small muted">${t("Wärmeleistung in W, z. B. vom Wärmemengenzähler der Heizung. Ohne Angabe nimmt Sunflow die Wärme der Wärmepumpe.")}</span>
            </div>
            <label class="field">
              <span class="field-label">${t("Heizflächen")}</span>
              <select @change=${(e) => set({ heating_surface: e.target.value })}>
                ${Object.entries(SURFACES()).map(([value, label]) => html`<option value=${value} ?selected=${(house.heating_surface ?? "underfloor") === value}>${label}</option>`)}
              </select>
              <span class="small muted">${t("Bestimmt den Startwert der Kennlinie (Exponent n: Fußboden 1,1, gemischt 1,2, Heizkörper 1,3).")}</span>
              ${this._houseEdit && (this._houseEdit.heating_surface ?? "underfloor") !== (this._house.heating_surface ?? "underfloor")
                ? html`<span class="small warn-text">${t("Nach dem Speichern wird die Kennlinie neu bewertet; die gemessenen Daten bleiben erhalten.")}</span>`
                : nothing}
            </label>
            <label class="field">
              <span class="field-label">${t("Außentemperatur")}</span>
              <select @change=${(e) => set({ outside_source: e.target.value })}>
                ${Object.entries(OUTSIDE_SOURCES()).map(([value, label]) => html`<option value=${value} ?selected=${(house.outside_source ?? "auto") === value}>${label}</option>`)}
              </select>
              <span class="small muted">${t("Für das Hausmodell und die Aufzeichnung der Wärmepumpe. Den eigenen Sensor wählst du unter Lernquellen.")}</span>
            </label>
          </div>
        </fieldset>
        ${this._houseEdit
          ? html`<div class="actions">
              <button class="primary" ?disabled=${this._busy || house.rooms.some((r) => !r.entity_id)} @click=${() => this._saveHouse()}>${t("Speichern")}</button>
              <button class="secondary" @click=${() => (this._houseEdit = null)}>${t("Abbrechen")}</button>
            </div>`
          : nothing}
        ${this._renderHouseModel()}
      </section>
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

  /** What Sunflow learned about the house, in words, with the last week measured and expected. */
  _renderHouseModel() {
    const result = this._houseModel;
    if (!result || result.state === "no_rooms") return nothing;
    const one = (v) => decimal(v.toFixed(1));
    const messages = {
      no_outside: t("Es fehlt eine Außentemperatur: an der Wärmepumpe oder unter Lernquellen."),
      error: t("Nicht verfügbar: {error}", { error: result.error ?? "" }),
      learning: t("Lernt noch: {days} von {min} Tagen mit Raum- und Außentemperatur.", { days: result.days ?? 0, min: result.min_days ?? 7 }),
    };
    if (messages[result.state]) return html`<p class="small muted">${messages[result.state]}</p>${this._renderHouseSource(result)}`;
    const m = result.model;
    const heat = {
      meter: t("Wärme vom gewählten Sensor"),
      measured: t("Wärme der Wärmepumpe gemessen"),
      cop: t("Wärme aus Strom × COP der Wärmepumpe"),
      estimated: t("Wärme aus dem Strom der Wärmepumpe geschätzt (COP aus der Außentemperatur)"),
    }[result.heat_source] ?? t("ohne Wärmemessung – Heizung und innere Wärme lassen sich nicht trennen");
    const tile = (label, value, help = null) => html`<div class="tile"><div class="small muted">${label}</div><div class="value">${value}</div>${help ? html`<div class="small muted">${help}</div>` : nothing}</div>`;
    const name = (entityId) => (this._candidates?.temperature ?? []).find((c) => c.entity_id === entityId)?.name ?? entityId;
    const points = result.chart ?? [];
    return html`
      <div class="learned">
        <span class="field-label">${t("Gelernt")}</span>
        <p class="small ${result.state === "ready" ? "muted" : "warn-text"}">${result.state === "ready"
          ? t("Aus {days} Tagen · {heat}", { days: result.days, heat })
          : m.plausible
            ? t("Noch unsicher: Die Vorhersage ist nicht besser als „die Temperatur bleibt“. Sunflow nutzt das Modell noch nicht. ({heat})", { heat })
            : t("Noch nicht plausibel – vielleicht heizt etwas, das nicht gemessen wird, oder ein Sensor hängt in der Sonne. ({heat})", { heat })}</p>
        <div class="tiles">
          ${m.tau_h != null ? tile(t("Zeitkonstante"), `${Math.round(m.tau_h)} h`, t("bis zwei Drittel des Unterschieds nach außen weg sind")) : nothing}
          ${tile(t("Abkühlung bei 0 °C außen"), `${one(m.cooling_k_per_h_at_0)} K/h`, t("ohne Heizung und Sonne, bei {temp} °C innen", { temp: decimal(String(COOLING_INSIDE_C)) }))}
          ${m.loss_w_per_k != null ? tile(t("Wärmeverlust"), `${Math.round(m.loss_w_per_k)} W/K`, t("bei −10 °C außen etwa {kw} kW Heizleistung", { kw: one(m.loss_w_per_k * (COOLING_INSIDE_C + 10) / 1000) })) : nothing}
          ${m.capacity_kwh_per_k != null ? tile(t("Wärmespeicher"), `${Math.round(m.capacity_kwh_per_k)} kWh/K`, t("Wärme für 1 K wärmer im ganzen Haus")) : nothing}
          ${m.sun_k_per_h != null ? tile(t("Sonne"), `${m.sun_k_per_h >= 0 ? "+" : ""}${one(m.sun_k_per_h)} K/h`, t("bei voller Sonne")) : nothing}
          ${m.error_k != null ? tile(t("Vorhersage über 24 h"), `± ${one(m.error_k)} K`, t("„bleibt gleich“ läge bei ± {k} K", { k: one(m.baseline_k) })) : nothing}
        </div>
        ${this._renderHouseSource(result)}
        ${result.excluded_hours
          ? html`<p class="small muted">${result.source === "heat_pump_samples"
            ? t("{hours} Stunden mit Warmwasser, Abtauen, Nachlauf oder Lüften sind ausgelassen.", { hours: result.excluded_hours })
            : t("{hours} Stunden mit Warmwasser oder Abtauen sind ausgelassen: deren Wärme ging nicht ins Haus.", { hours: result.excluded_hours })}</p>`
          : nothing}
        ${result.restless?.length
          ? html`<p class="small warn-text">${t("Auffällig unruhig – vielleicht Sonne, Heizkörper oder Fenster am Sensor: {rooms}. Ein geringeres Gewicht oder ein anderer Sensor hilft.", { rooms: result.restless.map(name).join(", ") })}</p>`
          : nothing}
        ${points.length
          ? html`<span class="field-label">${t("Letzte 7 Tage")}</span>
            <div class="legend small">
              <span class="legend-item"><span class="swatch" style="background: ${SERIES_COLORS[0]}"></span>${t("gemessen")}</span>
              <span class="legend-item"><span class="swatch" style="background: ${SERIES_COLORS[1]}"></span>${t("Modell (startet jeden Tag neu)")}</span>
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
      </div>
    `;
  }

  /** Where the house model's hours come from: Home Assistant until the heat pump's samples cover enough days. */
  _renderHouseSource(result) {
    if (!result.source) return nothing;
    return html`<p class="small muted">${result.source === "heat_pump_samples"
      ? t("Quelle: Messwerte der Wärmepumpe (5 min, {days} Tage gültiger Heizbetrieb).", { days: result.coverage_days })
      : t("Quelle: Stundenmittel aus Home Assistant. Umstellung auf die Messwerte der Wärmepumpe ab {min} Tagen gültigem Heizbetrieb (bisher {days}).", { min: result.min_days ?? 7, days: result.coverage_days ?? 0 })}</p>`;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 12px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    .heading { display: flex; flex-direction: column; gap: 4px; }
    .heading p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    p { margin: 0; }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 12px; }
    .device-title { font-weight: 600; font-size: 16px; display: block; }
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
    .learned { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--sf-border); padding-top: 12px; }
    .legend { display: flex; flex-wrap: wrap; gap: 16px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; flex: none; }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    table { border-collapse: collapse; width: 100%; font-size: 14px; display: block; overflow-x: auto; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--sf-border); font-variant-numeric: tabular-nums; }
    th { font-size: 13px; color: var(--sf-muted); font-weight: 600; }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 10px 12px; }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .alert.warn { padding: 10px 14px; border-radius: 8px; background: var(--sf-warn-bg, var(--sf-subtle)); color: var(--sf-warn-text); font-weight: 600; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
  `;
}

customElements.define("sf-heat-pump", SunflowHeatPump);
