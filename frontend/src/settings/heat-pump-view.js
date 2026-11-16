import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { withSuggestedName } from "../lib/names.js";
import { api } from "../lib/api.js";
import { confirmDialog } from "../components/dialog.js";
import { formatPower, fromDisplay, toDisplay } from "../lib/format.js";
import { poll } from "../lib/poll.js";
import "../components/picker.js";
import { decimal, locale, t } from "../lib/i18n.js";
import { toggleSheet } from "../lib/display-toggles.js";
import { lineChart, SERIES_COLORS } from "../components/charts.js";

const PROFILE = "generic/heat_pump";
const REFRESH_MS = 10000;
const PHASES = ["L1", "L2", "L3"];

// How the electrical power is measured
const ELECTRIC = {
  power: t("Gesamtleistung (W)"),
  phase_power: t("Leistung je Phase (W)"),
  phase_current: t("Strom je Phase (A)"),
  energy: t("nur Zähler (kWh)"),
};

// How Sunflow steers the heat pump; "" only measures
const METHODS = {
  "": [t("nicht steuern – nur messen"), ""],
  sg_relays: [t("SG-Ready über zwei Relais"), t("Kontakt 1 sperrt, Kontakt 2 verstärkt; beide zusammen: Anlaufbefehl.")],
  sg_relay: [t("SG-Ready nur Kontakt 2"), t("Normal oder verstärkt – wenn Kontakt 1 schon die Sperre des Netzbetreibers ist (§14a).")],
  sg_select: [t("SG-Ready über eine Auswahl"), t("Eine Auswahl-Entität der Integration (oder ein input_select) mit den SG-Ready-Zuständen.")],
  setpoint: [t("Sollwert anheben"), t("Für Wärmepumpen ohne SG-Ready: z. B. den Warmwasser-Sollwert um einige Kelvin anheben.")],
  surplus: [t("PV-Überschuss melden"), t("Manche Wärmepumpen nehmen den PV-Überschuss in W entgegen und regeln selbst danach.")],
  boost_switch: [t("Boost-Schalter"), t("Ein Schalter der Integration, z. B. Warmwasser-Einmalladung.")],
};
const CAN_BLOCK = new Set(["sg_relays", "sg_select", "setpoint"]);
const CAN_FORCE = new Set(["sg_relays", "sg_select"]);
const STATES = { normal: t("normal"), boost: t("verstärkt"), block: t("gesperrt"), forced: t("Anlaufbefehl") };
// Built-in heating rod: how its integration lets it be released, and Sunflow's modes per season
const ROD_METHODS = {
  block_switch: [t("Sperr-Schalter (an = gesperrt)"), t("z. B. Bosch, Buderus, Junkers über EMS-ESP: „Zusatzheizer deaktivieren“ (auxheateroff)")],
  allow_switch: [t("Freigabe-Schalter (an = darf heizen)"), t("z. B. NIBE: „Allow additive heating“ bzw. „Zusatzheizung zulassen“")],
  select: [t("Auswahl (frei/gesperrt oder Leistungsstufen)"), t("z. B. Panasonic über HeishaMon: „DHW/Room Heater State“; EMS-ESP: „maxheat…“ in kW-Stufen")],
  limit: [t("Höchstleistung als Zahl"), t("z. B. NIBE: „Max int add. power“ in kW")],
};
const ROD_STATES = { free: t("frei (normal)"), blocked: t("gesperrt"), max: t("freigegeben (maximal)") };
const ROD_HEATING = { need: t("Nur bei Bedarf"), auto: t("Wärmepumpe entscheidet") };
const ROD_SUMMER = { pv: t("Bei PV-Überschuss"), need: t("Nur bei Bedarf"), auto: t("Wärmepumpe entscheidet") };
const MODES = { off: t("Aus"), pv_only: t("Nur PV-Überschuss"), cost: t("Automatisch (nach Kosten)") };
const RULE_DEFAULTS = {
  min_surplus_w: 1500, cheap_hours: 0, expensive_hours: 0, min_state_min: 20, block_max_min: 120, block_day_max_min: 240,
  boost_k: 5, block_k: 5,
};

const EMPTY = {
  name: "", suggestedName: "",
  electric_on: true, electric_mode: "power", power_entity: "", energy_entity: "", phase_power: ["", "", ""], phase_current: ["", "", ""],
  voltage: "", heat_on: false, heat_mode: "power", heat_power_entity: "", heat_energy_entity: "", cop_on: false, cop_entity: "",
  temperature_entity: "", outside_temperature_entity: "",
  method: "", sg_contact1_entity: "", sg_contact2_entity: "", contact1_inverted: false, contact2_inverted: false,
  sg_select_entity: "", options: {}, setpoint_entity: "", setpoint_normal: "", surplus_entity: "", boost_switch_entity: "",
  use_block: false, use_forced: false,
  rod_kind: "", rod_id: "", rod_heating_mode: "off", rod_summer_mode: "pv_only", rod_manual_h: "24",
  rod_method: "block_switch", rod_entity: "", rod_options: {}, rod_limit_normal: "", rod_limit_max: "", rod_limit_blocked: "0",
  rod_heating: "need", rod_summer: "pv", rod_bivalence: "-5", rod_min_state: "60", rod_released_until: null,
  rules: Object.fromEntries(Object.entries(RULE_DEFAULTS).map(([k, v]) => [k, String(v)])),
};

/** The form of a stored heat pump; planning holds its rod's season modes. */
function formOf(device, planning) {
  const role = (name) => device.role_overrides[name]?.entity_id ?? "";
  const phases = (prefix) => PHASES.map((p) => role(`${prefix}_${p.toLowerCase()}`));
  const phasePower = phases("power");
  const phaseCurrent = phases("current");
  const settings = device.heat_pump ?? {};
  return {
    ...EMPTY,
    name: device.name,
    electric_on: Boolean(role("power") || role("energy") || phasePower.some(Boolean) || phaseCurrent.some(Boolean)),
    electric_mode: role("power") ? "power" : phasePower.some(Boolean) ? "phase_power" : phaseCurrent.some(Boolean) ? "phase_current" : role("energy") ? "energy" : "power",
    power_entity: role("power"),
    energy_entity: role("energy"),
    phase_power: phasePower,
    phase_current: phaseCurrent,
    voltage: device.parameters.voltage ? String(device.parameters.voltage) : "",
    heat_on: Boolean(role("heat_power") || role("heat_energy")),
    heat_mode: role("heat_power") || !role("heat_energy") ? "power" : "energy",
    heat_power_entity: role("heat_power"),
    heat_energy_entity: role("heat_energy"),
    cop_on: Boolean(role("cop")),
    cop_entity: role("cop"),
    temperature_entity: role("temperature"),
    outside_temperature_entity: role("outside_temperature"),
    method: settings.method ?? "",
    sg_contact1_entity: role("sg_contact1"),
    sg_contact2_entity: role("sg_contact2"),
    contact1_inverted: Boolean(settings.contact1_inverted),
    contact2_inverted: Boolean(settings.contact2_inverted),
    sg_select_entity: role("sg_select"),
    options: { ...(settings.options ?? {}) },
    setpoint_entity: role("setpoint"),
    setpoint_normal: settings.setpoint_normal != null ? toDisplay(settings.setpoint_normal, "") : "",
    surplus_entity: role("surplus_power"),
    boost_switch_entity: role("boost_switch"),
    use_block: Boolean(settings.use_block),
    use_forced: Boolean(settings.use_forced),
    ...rodFormOf(settings.rod_id, planning),
    ...builtinFormOf(settings.rod, role(ROD_ROLES[settings.rod?.method] ?? "")),
    rules: Object.fromEntries(Object.keys(RULE_DEFAULTS).map((k) => [k, toDisplay(settings[k] ?? RULE_DEFAULTS[k], "")])),
  };
}

/** The rod part of the form; a rod without season modes gets the usual ones. */
function rodFormOf(rodId, planning) {
  const flex = (rodId && planning?.[rodId]) || {};
  return {
    rod_kind: rodId ? "consumer" : "",
    rod_id: rodId ?? "",
    rod_heating_mode: flex.heating_mode ?? EMPTY.rod_heating_mode,
    rod_summer_mode: flex.summer_mode ?? EMPTY.rod_summer_mode,
    rod_manual_h: toDisplay(flex.manual_max_h ?? 24, ""),
  };
}

const ROD_ROLES = { allow_switch: "rod_allow", block_switch: "rod_block", select: "rod_select", limit: "rod_limit" };

/** The built-in rod part of the form. */
function builtinFormOf(rod, entity) {
  if (!rod) return {};
  const number = (value) => (value != null ? toDisplay(value, "") : "");
  return {
    rod_kind: "builtin",
    rod_method: rod.method,
    rod_entity: entity,
    rod_options: { ...(rod.options ?? {}) },
    rod_limit_normal: number(rod.limit_normal),
    rod_limit_max: number(rod.limit_max),
    rod_limit_blocked: number(rod.limit_blocked ?? 0),
    rod_heating: rod.heating_mode,
    rod_summer: rod.summer_mode,
    rod_bivalence: number(rod.bivalence_c),
    rod_min_state: String(rod.min_state_min),
    rod_released_until: rod.released_until ?? null,
  };
}

/** Enough measured to know what it draws: electrical power, or heat with COP. */
function measured(form) {
  const electric = form.electric_on && {
    power: form.power_entity,
    phase_power: form.phase_power.some(Boolean),
    phase_current: form.phase_current.some(Boolean),
    energy: form.energy_entity,
  }[form.electric_mode];
  return Boolean(electric || (form.heat_on && form.heat_power_entity && form.cop_on && form.cop_entity));
}

/** The API body; undefined when a number is not valid. */
function bodyOf(form) {
  const electric = form.electric_on ? form.electric_mode : null;
  const phases = (list) => (list.some(Boolean) ? list.map((e) => e || null) : []);
  const number = (text) => (text === "" ? null : fromDisplay(text, ""));
  const rules = Object.fromEntries(Object.keys(RULE_DEFAULTS).map((k) => [k, number(form.rules[k])]));
  const voltage = electric === "phase_current" ? number(form.voltage) : null;
  const setpoint = number(form.setpoint_normal);
  const rodHours = number(form.rod_manual_h);
  const builtin = form.rod_kind === "builtin";
  const rodNumbers = builtin
    ? [number(form.rod_limit_normal), number(form.rod_limit_max), number(form.rod_limit_blocked), number(form.rod_bivalence)]
    : [];
  const rodMinutes = builtin ? Number.parseInt(form.rod_min_state, 10) : 60;
  if ([voltage, setpoint, rodHours, ...rodNumbers, ...Object.values(rules)].includes(undefined) || !(rodMinutes > 0)) return undefined;
  const [limitNormal, limitMax, limitBlocked, bivalence] = rodNumbers;
  const rod = builtin
    ? {
      method: form.rod_method,
      options: form.rod_method === "select" ? Object.fromEntries(Object.entries(form.rod_options).filter(([, option]) => option)) : {},
      limit_normal: form.rod_method === "limit" ? limitNormal : null,
      limit_max: form.rod_method === "limit" ? limitMax : null,
      limit_blocked: form.rod_method === "limit" ? limitBlocked ?? 0 : 0,
      heating_mode: form.rod_heating,
      summer_mode: form.rod_summer,
      bivalence_c: bivalence ?? -5,
      min_state_min: rodMinutes,
      released_until: form.rod_released_until,
    }
    : null;
  const method = form.method || null;
  return {
    name: form.name.trim(),
    type: "heat_pump",
    kind: "load",
    power_entity: electric === "power" ? form.power_entity || null : null,
    energy_entity: electric ? form.energy_entity || null : null,
    phase_power_entities: electric === "phase_power" ? phases(form.phase_power) : [],
    phase_current_entities: electric === "phase_current" ? phases(form.phase_current) : [],
    voltage,
    heat_power_entity: form.heat_on && form.heat_mode === "power" ? form.heat_power_entity || null : null,
    heat_energy_entity: form.heat_on ? form.heat_energy_entity || null : null,
    cop_entity: form.cop_on ? form.cop_entity || null : null,
    temperature_entity: form.temperature_entity || null,
    outside_temperature_entity: form.outside_temperature_entity || null,
    sg_contact1_entity: form.sg_contact1_entity || null,
    sg_contact2_entity: form.sg_contact2_entity || null,
    sg_select_entity: form.sg_select_entity || null,
    setpoint_entity: form.setpoint_entity || null,
    surplus_entity: form.surplus_entity || null,
    boost_switch_entity: form.boost_switch_entity || null,
    rod_entity: builtin ? form.rod_entity || null : null,
    heat_pump: {
      method,
      contact1_inverted: form.contact1_inverted,
      contact2_inverted: form.contact2_inverted,
      use_block: Boolean(method && CAN_BLOCK.has(method) && form.use_block),
      use_forced: Boolean(method && CAN_FORCE.has(method) && form.use_forced),
      options: Object.fromEntries(Object.entries(form.options).filter(([, option]) => option)),
      setpoint_normal: setpoint,
      rod: rod,
      rod_id: form.rod_kind === "consumer" ? form.rod_id || null : null,
      ...Object.fromEntries(Object.entries(rules).map(([k, v]) => [k, v ?? RULE_DEFAULTS[k]])),
    },
  };
}

// Typical names of the SG-Ready states in integrations (German and English)
const OPTION_NAMES = {
  normal: /normal|^2$|betrieb 2/i,
  boost: /verst(ä|ae)rk|boost|empfehl|recommend|^3$/i,
  block: /sperr|block|evu|^1$/i,
  forced: /anlauf|zwang|force|^4$/i,
};

/** Options of a select matched to the states by their names; chosen ones stay. */
function guessOptions(choices, current) {
  const result = { ...current };
  for (const [state, pattern] of Object.entries(OPTION_NAMES)) {
    if (!result[state] || !choices.includes(result[state])) result[state] = choices.find((c) => pattern.test(c)) ?? "";
  }
  return result;
}

/** "10-01" -> "01.10" */
/** Map a select's options to the rod states by their names (blocked/free, kW steps). */
function guessRodOptions(choices) {
  const kw = (o) => { const m = /([\d.,]+)\s*kw/i.exec(o); return m ? Number.parseFloat(m[1].replace(",", ".")) : null; };
  const powers = choices.map(kw);
  if (powers.some((p) => p !== null)) {
    const known = choices.filter((_, i) => powers[i] !== null);
    const sorted = [...known].sort((a, b) => kw(a) - kw(b));
    return { blocked: sorted[0], max: sorted.at(-1) };
  }
  const find = (re) => choices.find((o) => re.test(o)) ?? "";
  return { free: find(/free|frei|allow|on\b|an\b/i), blocked: find(/block|sperr|off\b|aus\b/i) };
}

function mmddToText(mmdd) {
  const [month, day] = (mmdd ?? "").split("-");
  return month && day ? `${day}.${month}` : "";
}

/** "1.10." -> "10-01"; null when it is no date. */
function textToMmdd(text) {
  const match = text.trim().match(/^(\d{1,2})\.(\d{1,2})\.?$/);
  if (!match) return null;
  const day = Number(match[1]);
  const month = Number(match[2]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** The heat pump: measuring, steering, its heating rod and the heating season (settings area "Wärmepumpe"). */
class SunflowHeatPump extends LitElement {
  static properties = {
    _config: { state: true },
    _candidates: { state: true },
    _live: { state: true },
    _season: { state: true },
    _house: { state: true },
    _houseEdit: { state: true },
    _houseModel: { state: true },
    _form: { state: true },
    _editId: { state: true },
    _error: { state: true },
    _busy: { state: true },
  };

  constructor() {
    super();
    this._config = null;
    this._candidates = null;
    this._live = null;
    this._season = null;
    this._house = null;
    this._houseEdit = null;
    this._houseModel = null;
    this._form = null;
    this._editId = null;
    this._error = null;
    this._busy = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stopPolling = poll(() => this._loadLive(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling?.();
  }

  async _load() {
    try {
      const [config, season, house] = await Promise.all([api.config(), api.heatingSeason(), api.house()]);
      this._config = config;
      this._season = season;
      this._house = house;
      this._error = null;
    } catch (err) {
      this._error = t("Einstellungen nicht verfügbar: {error}", { error: err.message });
    }
    this._loadLive();
    this._loadHouseModel();
  }

  async _loadHouseModel() {
    try {
      this._houseModel = await api.houseModel();
    } catch (err) {
      this._houseModel = { state: "error", error: err.message };
    }
  }

  async _loadLive() {
    try {
      this._live = await api.live();
    } catch {
      // Live values are a bonus; the settings work without them
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

  get _heatPumps() {
    return (this._config?.devices ?? []).filter((d) => d.profile === PROFILE);
  }

  /** Consumers that heat: the rods a heat pump can take. */
  get _rods() {
    return (this._config?.devices ?? []).filter((d) => d.kind === "heater");
  }

  render() {
    if (this._config === null) {
      return html`<h1>${t("Wärmepumpe")}</h1>${this._error
        ? html`<div class="alert error" role="alert">${this._error}</div>`
        : html`<p class="muted">${t("Lade …")}</p>`}`;
    }
    const pumps = this._heatPumps;
    return html`
      <div class="heading">
        <h1>${t("Wärmepumpe")}</h1>
        <p class="muted small">${t("Sunflow misst die Wärmepumpe und schiebt sie auf Wunsch in sonnige oder günstige Stunden – über SG-Ready, einen Sollwert, den gemeldeten PV-Überschuss oder einen Boost-Schalter. Es schaltet die Wärmepumpe nie selbst ab: Es empfiehlt einen Zustand, ihre eigene Regelung bleibt zuständig.")}</p>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${pumps.map((d) => (this._editId === d.id ? this._renderEditor() : this._renderPump(d)))}
      ${this._form && this._editId === null ? this._renderEditor() : nothing}
      ${!this._form ? html`<div><button class="secondary" @click=${() => this._edit(null)}>${pumps.length ? t("Weitere Wärmepumpe hinzufügen …") : t("Wärmepumpe hinzufügen …")}</button></div>` : nothing}
      ${this._renderHouse()}
      ${pumps.some((d) => d.heat_pump?.rod_id || d.heat_pump?.rod) ? this._renderSeason() : nothing}
    `;
  }

  _renderPump(device) {
    const live = this._live?.consumers?.find((c) => c.device_id === device.id);
    const reading = live?.heat_pump;
    const approx = (source) => (source === "computed" ? "≈ " : "");
    const tile = (label, value) => html`<div class="tile"><div class="small muted">${label}</div><div class="value">${value}</div></div>`;
    const method = device.heat_pump?.method ?? "";
    const control = reading?.control;
    const steered = this._config.control?.devices?.[device.id];
    return html`
      <section class="device">
        <div class="title-row">
          <span class="device-title">${device.name}</span>
          <span class="spacer"></span>
          <button class="secondary" @click=${() => this._edit(device)}>${t("Bearbeiten")}</button>
          <button class="secondary danger" @click=${() => this._remove(device)}>${t("Entfernen")}</button>
        </div>
        ${this._renderRodLine(device)}
        <div class="small muted">${METHODS[method]?.[0] ?? method}${method
          ? html` · ${steered ? t("Sunflow steuert") : t("Schattenmodus – einschalten unter Steuerung")}`
          : nothing}</div>
        ${reading
          ? html`<div class="tiles">
              ${tile(t("Strom"), reading.electric_w != null ? `${approx(reading.electric_source)}${formatPower(reading.electric_w)}` : "–")}
              ${tile(t("Wärme"), reading.heat_w != null ? `${approx(reading.heat_source)}${formatPower(reading.heat_w)}` : "–")}
              ${tile(t("COP"), reading.cop != null ? `${approx(reading.cop_source)}${decimal(reading.cop.toFixed(1))}` : "–")}
              ${live.level != null ? tile(t("Speicher"), `${decimal(live.level.toFixed(1))} °C`) : nothing}
              ${reading.outside_temperature != null ? tile(t("Außen"), `${decimal(reading.outside_temperature.toFixed(1))} °C`) : nothing}
              ${control ? tile(control.steering ? t("Sunflow setzt") : t("Sunflow würde setzen"), STATES[control.state] ?? control.state) : nothing}
            </div>
            ${control ? html`<div class="small muted">${control.reason}</div>` : nothing}
            ${reading.problem
              ? html`<div class="small warn-text">${reading.problem === "cop_mismatch"
                  ? t("Gemessener und berechneter COP weichen stark voneinander ab – sind Strom- und Wärmesensor richtig gewählt?")
                  : t("COP außerhalb von 1–7 – ist vielleicht der Wärme- statt des Stromsensors als Strom gewählt?")}</div>`
              : nothing}`
          : nothing}
      </section>
    `;
  }

  /** Which rod the heat pump has and how it runs now. */
  _renderRodLine(device) {
    const builtin = device.heat_pump?.rod;
    if (builtin) return this._renderBuiltinLine(device, builtin);
    const rod = this._config.devices.find((d) => d.id === device.heat_pump?.rod_id);
    if (!rod) return nothing;
    const flex = this._config.planning?.[rod.id] ?? {};
    const active = this._season?.active;
    const mode = active ? flex.heating_mode : flex.summer_mode;
    return html`<div class="small">${t("Heizstab: {name}", { name: rod.name })} · ${active ? t("Heizperiode") : t("Sommer")}: ${MODES[mode] ?? "–"}</div>`;
  }

  _renderBuiltinLine(device, rod) {
    const steering = this._live?.consumers?.find((c) => c.device_id === device.id)?.heat_pump?.control;
    const control = steering?.rod;
    const until = rod.released_until ? new Date(rod.released_until) : null;
    const released = until && until > new Date();
    const time = released ? until.toLocaleString(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
    return html`<div class="title-row small">
      <span>${t("Eingebauter Heizstab")}${control ? html`: ${steering.steering ? nothing : t("Sunflow würde setzen")} <strong>${ROD_STATES[control.state] ?? control.state}</strong> – ${control.reason}` : nothing}</span>
      <span class="spacer"></span>
      ${released
        ? html`<span class="muted">${t("freigegeben bis {time}", { time })}</span>
          <button class="secondary" ?disabled=${this._busy} @click=${() => this._releaseRod(device, 0)}>${t("Freigabe beenden")}</button>`
        : html`<button class="secondary" ?disabled=${this._busy} @click=${() => this._releaseRod(device, 24)}>${t("Heizstab 24 h freigeben")}</button>`}
    </div>`;
  }

  async _releaseRod(device, hours) {
    this._busy = true;
    try {
      await api.releaseHeatPumpRod(device.id, hours);
      await this._load();
    } catch (err) {
      this._error = t("Heizstab konnte nicht freigegeben werden: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  async _edit(device) {
    await this._loadCandidates();
    this._editId = device ? device.id : null;
    this._form = device ? formOf(device, this._config.planning) : { ...EMPTY, rules: { ...EMPTY.rules }, options: {} };
  }

  _set(changes) {
    this._form = { ...this._form, ...changes };
  }

  _renderEditor() {
    const form = this._form;
    const candidates = this._candidates ?? {};
    const options = (list) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} ${c.unit ?? ""}` }));
    const picker = (label, field, list, empty = t("keine")) => html`<div class="field">
      <span class="field-label">${label}</span>
      <sf-picker label=${label} empty-label=${empty} placeholder=${t("Name oder Entität suchen …")}
        .value=${form[field]} .options=${options(list)}
        @change=${(e) => {
          const chosen = (list ?? []).find((c) => c.entity_id === e.detail.value);
          this._form = withSuggestedName({ ...this._form, [field]: e.detail.value }, chosen);
          if (field === "sg_select_entity") this._set({ options: guessOptions(chosen?.options ?? [], this._form.options) });
        }}></sf-picker>
    </div>`;
    const phasePickers = (field, list, label) => PHASES.map((phase, i) => html`<div class="field">
      <span class="field-label">${label} ${phase}</span>
      <sf-picker label=${`${label} ${phase}`} empty-label=${t("keine")} placeholder=${t("Name oder Entität suchen …")}
        .value=${form[field][i]} .options=${options(list)}
        @change=${(e) => {
          const values = [...this._form[field]];
          values[i] = e.detail.value;
          this._set({ [field]: values });
        }}></sf-picker>
    </div>`);
    const check = (field, label) => html`<label class="check"><input type="checkbox" .checked=${form[field]}
      @change=${(e) => this._set({ [field]: e.target.checked })}> ${label}</label>`;
    const devices = [...new Set([...(candidates.power ?? []), ...(candidates.energy ?? []), ...(candidates.temperature ?? [])]
      .map((c) => c.device).filter(Boolean))].sort();
    const valid = form.name.trim() && measured(form);
    return html`
      <section class="device">
        <span class="device-title">${this._editId ? t("„{name}“ bearbeiten", { name: form.name }) : t("Wärmepumpe hinzufügen")}</span>
        <div class="fields">
          <label class="field">
            <span class="field-label">${t("Name")}</span>
            <span class="input-unit"><input placeholder=${t("z. B. Wärmepumpe")} .value=${form.name} @input=${(e) => this._set({ name: e.target.value })}></span>
          </label>
          ${devices.length
            ? html`<label class="field">
                <span class="field-label">${t("Vorschläge aus Gerät")}</span>
                <select @change=${(e) => e.target.value && this._suggest(e.target.value)}>
                  <option value="">${t("– Gerät wählen –")}</option>
                  ${devices.map((d) => html`<option value=${d}>${d}</option>`)}
                </select>
              </label>`
            : nothing}
        </div>

        <fieldset class="group">
          <legend>${t("Messung")}</legend>
          <p class="small muted">${t("Alles ist optional, der Stromverbrauch (oder Wärme und COP) wird gebraucht. Aus zwei von Strom, Wärme und COP berechnet Sunflow den dritten Wert.")}</p>
          ${check("electric_on", t("Stromverbrauch"))}
          ${form.electric_on
            ? html`<div class="fields">
                <label class="field">
                  <span class="field-label">${t("Messung")}</span>
                  <select @change=${(e) => this._set({ electric_mode: e.target.value })}>
                    ${Object.entries(ELECTRIC).map(([value, label]) => html`<option value=${value} ?selected=${form.electric_mode === value}>${label}</option>`)}
                  </select>
                </label>
                ${form.electric_mode === "power" ? picker(t("Leistung"), "power_entity", candidates.power, nothing) : nothing}
                ${form.electric_mode === "phase_power" ? phasePickers("phase_power", candidates.power, t("Leistung")) : nothing}
                ${form.electric_mode === "phase_current"
                  ? html`${phasePickers("phase_current", candidates.currents, t("Strom"))}
                    <label class="field">
                      <span class="field-label">${t("Spannung")}</span>
                      <span class="input-unit"><input inputmode="decimal" placeholder="230" .value=${form.voltage}
                        @input=${(e) => this._set({ voltage: e.target.value })}><span class="muted">V</span></span>
                    </label>`
                  : nothing}
                ${form.electric_mode === "energy"
                  ? picker(t("Zähler"), "energy_entity", candidates.energy, nothing)
                  : picker(t("Zähler (optional)"), "energy_entity", candidates.energy)}
              </div>`
            : nothing}
          ${check("heat_on", t("Wärmeleistung"))}
          ${form.heat_on
            ? html`<div class="fields">
                <label class="field">
                  <span class="field-label">${t("Messung")}</span>
                  <select @change=${(e) => this._set({ heat_mode: e.target.value })}>
                    <option value="power" ?selected=${form.heat_mode === "power"}>${t("Leistung (W)")}</option>
                    <option value="energy" ?selected=${form.heat_mode === "energy"}>${t("nur Zähler (kWh)")}</option>
                  </select>
                </label>
                ${form.heat_mode === "power" ? picker(t("Wärmeleistung"), "heat_power_entity", candidates.power, nothing) : nothing}
                ${picker(form.heat_mode === "energy" ? t("Wärmezähler") : t("Wärmezähler (optional)"), "heat_energy_entity", candidates.energy,
                  form.heat_mode === "energy" ? nothing : t("keiner"))}
              </div>
              <p class="small muted">${t("Manche Integrationen nennen die Wärmeleistung nur „Leistung“ – hier gehört die abgegebene Wärme hin, nicht der Stromverbrauch.")}</p>`
            : nothing}
          ${check("cop_on", t("COP"))}
          ${form.cop_on ? html`<div class="fields">${picker(t("COP"), "cop_entity", candidates.ratios, nothing)}</div>` : nothing}
          <div class="fields">
            ${picker(t("Speichertemperatur (optional)"), "temperature_entity", candidates.temperature)}
            ${picker(t("Außentemperatur (optional)"), "outside_temperature_entity", candidates.temperature)}
          </div>
        </fieldset>

        ${this._renderControl(form, candidates, picker, check)}
        ${this._renderRodGroup(form, candidates)}

        <div class="actions">
          <button class="primary" ?disabled=${!valid || this._busy} @click=${() => this._save()}>${this._editId ? t("Speichern") : t("Hinzufügen")}</button>
          <button class="secondary" @click=${() => { this._form = null; this._editId = null; }}>${t("Abbrechen")}</button>
          ${!measured(form) ? html`<span class="small muted">${t("Bitte den Stromverbrauch angeben (oder Wärme und COP).")}</span>` : nothing}
        </div>
      </section>
    `;
  }

  _renderControl(form, candidates, picker, check) {
    const method = form.method;
    const rule = (field, label, unit, help = null) => html`<label class="field">
      <span class="field-label">${label}</span>
      <span class="input-unit"><input inputmode="decimal" .value=${form.rules[field]}
        @input=${(e) => this._set({ rules: { ...this._form.rules, [field]: e.target.value } })}><span class="muted">${unit}</span></span>
      ${help ? html`<span class="small muted">${help}</span>` : nothing}
    </label>`;
    const selected = (candidates.selects ?? []).find((c) => c.entity_id === form.sg_select_entity);
    const blockable = CAN_BLOCK.has(method);
    return html`
      <fieldset class="group">
        <legend>${t("Ansteuerung")}</legend>
        <div class="fields">
          <label class="field">
            <span class="field-label">${t("Sunflow steuert über")}</span>
            <select @change=${(e) => this._set({ method: e.target.value })}>
              ${Object.entries(METHODS).map(([value, [label]]) => html`<option value=${value} ?selected=${method === value}>${label}</option>`)}
            </select>
          </label>
        </div>
        ${method ? html`<p class="small muted">${METHODS[method][1]}</p>` : nothing}
        ${method === "sg_relays"
          ? html`<div class="fields">
              ${picker(t("Kontakt 1 (Sperre)"), "sg_contact1_entity", candidates.switches)}
              ${picker(t("Kontakt 2 (verstärkt)"), "sg_contact2_entity", candidates.switches, nothing)}
            </div>
            ${check("contact1_inverted", t("Kontakt 1 als Öffner (an = offen)"))}
            ${check("contact2_inverted", t("Kontakt 2 als Öffner (an = offen)"))}`
          : nothing}
        ${method === "sg_relay"
          ? html`<div class="fields">${picker(t("Kontakt 2 (verstärkt)"), "sg_contact2_entity", candidates.switches, nothing)}</div>
            ${check("contact2_inverted", t("Kontakt 2 als Öffner (an = offen)"))}`
          : nothing}
        ${method === "sg_select"
          ? html`<div class="fields">
              ${picker(t("Auswahl"), "sg_select_entity", candidates.selects, nothing)}
              ${selected
                ? Object.entries(STATES).map(([state, label]) => html`<label class="field">
                    <span class="field-label">${t("Option für „{state}“", { state: label })}</span>
                    <select @change=${(e) => this._set({ options: { ...this._form.options, [state]: e.target.value } })}>
                      <option value="">${state === "normal" || state === "boost" ? t("– wählen –") : t("nicht verwenden")}</option>
                      ${selected.options.map((o) => html`<option value=${o} ?selected=${form.options[state] === o}>${o}</option>`)}
                    </select>
                  </label>`)
                : nothing}
            </div>`
          : nothing}
        ${method === "setpoint"
          ? html`<div class="fields">
              ${picker(t("Sollwert (Zahl)"), "setpoint_entity", candidates.numbers, nothing)}
              <label class="field">
                <span class="field-label">${t("Normaler Sollwert")}</span>
                <span class="input-unit"><input inputmode="decimal" placeholder="50" .value=${form.setpoint_normal}
                  @input=${(e) => this._set({ setpoint_normal: e.target.value })}><span class="muted">°C</span></span>
              </label>
              ${rule("boost_k", t("Anheben um"), "K")}
              ${form.use_block ? rule("block_k", t("Absenken um (Sperre)"), "K") : nothing}
            </div>`
          : nothing}
        ${method === "surplus" ? html`<div class="fields">${picker(t("PV-Überschuss (Zahl in W)"), "surplus_entity", candidates.numbers, nothing)}</div>` : nothing}
        ${method === "boost_switch" ? html`<div class="fields">${picker(t("Boost-Schalter"), "boost_switch_entity", candidates.switches, nothing)}</div>` : nothing}
        ${method
          ? html`
            <div class="fields">
              ${rule("min_surplus_w", t("Verstärken ab Überschuss"), "W", t("geplante Einspeisung oder abgeregelte PV"))}
              ${rule("cheap_hours", t("Verstärken in den günstigsten"), t("Stunden/Tag"), t("0 = nur bei PV-Überschuss"))}
              ${rule("min_state_min", t("Mindestdauer je Zustand"), "min", t("schont den Verdichter"))}
            </div>
            ${blockable
              ? html`${check("use_block", t("In teuren Stunden sperren"))}
                ${form.use_block
                  ? html`<p class="small warn-text">${t("Nur, wenn Sunflow nicht den Kontakt der Sperre des Netzbetreibers (§14a EnWG) schaltet. Die Wärmepumpe holt nach einer Sperre meist nach.")}</p>
                    <div class="fields">
                      ${rule("expensive_hours", t("Sperren in den teuersten"), t("Stunden/Tag"))}
                      ${rule("block_max_min", t("Sperre höchstens am Stück"), "min")}
                      ${rule("block_day_max_min", t("Sperre höchstens pro Tag"), "min")}
                    </div>`
                  : nothing}`
              : nothing}
            ${CAN_FORCE.has(method) ? check("use_forced", t("Anlaufbefehl (Zustand 4) bei negativem Strompreis")) : nothing}
            <p class="small muted">${t("Gesteuert wird erst, wenn die Wärmepumpe unter Steuerung eingeschaltet ist; bis dahin zeigt Sunflow nur, was es setzen würde.")}</p>`
          : nothing}
      </fieldset>
    `;
  }

  /** Fill the empty fields with the entities of one Home Assistant device. */
  _suggest(deviceName) {
    const candidates = this._candidates ?? {};
    const of = (list, test = () => true) => (list ?? []).find((c) => c.device === deviceName && test(c))?.entity_id ?? "";
    const text = (c) => `${c.name} ${c.entity_id}`;
    // "Wärmepumpe" / "heat pump" itself is no hint of heat output
    const heat = /w(ä|ae)rme(?!pumpe)|heat(?![ _-]?pump)|therm/i;
    const outside = /au(ß|ss)en|outdoor|outside|ambient/i;
    const form = this._form;
    const fill = (field, value) => (form[field] || !value ? {} : { [field]: value });
    const heatPower = of(candidates.power, (c) => heat.test(text(c)));
    const heatEnergy = of(candidates.energy, (c) => heat.test(text(c)));
    const cop = of(candidates.ratios, (c) => /cop|leistungszahl/i.test(text(c)));
    this._form = {
      ...form,
      ...(form.name ? {} : { name: deviceName, suggestedName: deviceName }),
      ...fill("power_entity", of(candidates.power, (c) => !heat.test(text(c)))),
      ...fill("energy_entity", of(candidates.energy, (c) => !heat.test(text(c)))),
      ...fill("heat_power_entity", heatPower),
      ...fill("heat_energy_entity", heatEnergy),
      ...fill("cop_entity", cop),
      ...fill("outside_temperature_entity", of(candidates.temperature, (c) => outside.test(text(c)))),
      ...fill("temperature_entity", of(candidates.temperature, (c) => !outside.test(text(c)))),
      ...fill("sg_select_entity", of(candidates.selects, (c) => /sg.?ready/i.test(text(c)))),
      heat_on: form.heat_on || Boolean(heatPower || heatEnergy),
      cop_on: form.cop_on || Boolean(cop),
    };
    if (!form.rod_kind) this._form = { ...this._form, ...this._suggestRod(of, text, candidates) };
  }

  /** The built-in rod's entity among the device's, by the names integrations use (EMS-ESP, NIBE, HeishaMon). */
  _suggestRod(of, text, candidates) {
    const found = [
      ["block_switch", of(candidates.switches, (c) => /auxheateroff|zusatzheiz\w*\s*(deaktiv|aus)|disable.*(aux|add)/i.test(text(c)))],
      ["allow_switch", of(candidates.switches, (c) => /(allow|permit).*add|additive|zusatzheiz\w*\s*(zulass|erlaub)/i.test(text(c)))],
      ["select", of(candidates.selects, (c) => /heater.?state|room.?heater|dhw.?heater/i.test(text(c)))],
      ["limit", of(candidates.numbers, (c) => /max\w*\s*(int|internal)\w*\s*add|max.*zusatzheiz/i.test(text(c)))],
    ].find(([, entity]) => entity);
    if (!found) return {};
    const [method, entity] = found;
    const chosen = (candidates.selects ?? []).find((c) => c.entity_id === entity);
    return { rod_kind: "builtin", rod_method: method, rod_entity: entity, rod_options: method === "select" ? guessRodOptions(chosen?.options ?? []) : {} };
  }

  async _save() {
    const body = bodyOf(this._form);
    if (body === undefined) {
      this._error = t("Bitte gültige Zahlen eingeben.");
      return;
    }
    this._busy = true;
    try {
      if (this._editId) await api.updateManual(this._editId, body);
      else await api.addManual(body);
      if (this._form.rod_kind === "consumer" && this._form.rod_id) await this._saveRodModes(this._form);
      this._form = null;
      this._editId = null;
      await this._load();
    } catch (err) {
      this._error = t("Wärmepumpe konnte nicht gespeichert werden: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  /** The rod's season modes live in its own planning settings. */
  async _saveRodModes(form) {
    const flex = {
      strategy: "cost", target: null, target_max: null, ready_by: null, uses_battery: true, alternative_cost_ct: null,
      ...(this._config.planning?.[form.rod_id] ?? {}),
      heating_mode: form.rod_heating_mode,
      summer_mode: form.rod_summer_mode,
      manual_max_h: form.rod_manual_h === "" ? 24 : fromDisplay(form.rod_manual_h, ""),
    };
    await api.updateDevice(form.rod_id, { flex });
  }

  async _remove(device) {
    if (!(await confirmDialog({ title: t("„{name}“ entfernen?", { name: device.name }), text: t("Messung und Ansteuerung der Wärmepumpe werden gelöscht."), confirm: t("Entfernen"), danger: true }))) return;
    try {
      await api.removeDevice(device.id);
      await this._load();
    } catch (err) {
      this._error = t("Entfernen fehlgeschlagen: {error}", { error: err.message });
    }
  }

  /** The heating rod: the heat pump's own (released through its integration) or, as the alternative, a consumer. */
  _renderRodGroup(form, candidates) {
    const kinds = {
      "": t("kein Heizstab"),
      builtin: t("Eingebaut – über die Integration der Wärmepumpe"),
      consumer: t("Eigener Heizstab – als Verbraucher angelegt"),
    };
    return html`
      <fieldset class="group">
        <legend>${t("Heizstab (optional)")}</legend>
        <div class="fields">
          <label class="field">
            <span class="field-label">${t("Heizstab")}</span>
            <select @change=${(e) => this._set({ rod_kind: e.target.value })}>
              ${Object.entries(kinds).map(([value, label]) => html`<option value=${value} ?selected=${form.rod_kind === value}>${label}</option>`)}
            </select>
          </label>
        </div>
        ${form.rod_kind === "builtin" ? this._renderBuiltinRod(form, candidates) : nothing}
        ${form.rod_kind === "consumer" ? this._renderRodConsumer(form) : nothing}
      </fieldset>
    `;
  }

  _renderBuiltinRod(form, candidates) {
    const method = form.rod_method;
    const lists = { allow_switch: candidates.switches, block_switch: candidates.switches, select: candidates.selects, limit: candidates.numbers };
    const selected = (candidates.selects ?? []).find((c) => c.entity_id === form.rod_entity);
    const unit = (candidates.numbers ?? []).find((c) => c.entity_id === form.rod_entity)?.unit ?? "";
    const value = (field, label, help = null) => html`<label class="field">
      <span class="field-label">${label}</span>
      <span class="input-unit"><input inputmode="decimal" .value=${form[field]}
        @input=${(e) => this._set({ [field]: e.target.value })}><span class="muted">${unit}</span></span>
      ${help ? html`<span class="small muted">${help}</span>` : nothing}
    </label>`;
    const mode = (field, label, modes, help) => html`<label class="field">
      <span class="field-label">${label}</span>
      <select @change=${(e) => this._set({ [field]: e.target.value })}>
        ${Object.entries(modes).map(([key, text]) => html`<option value=${key} ?selected=${form[field] === key}>${text}</option>`)}
      </select>
      <span class="small muted">${help}</span>
    </label>`;
    return html`
      <p class="small muted">${t("Die Zusatz- oder Notheizung in der Wärmepumpe. Sunflow kann sie nur sperren, freigeben oder ihre Leistung begrenzen – ob sie heizt, entscheidet die Wärmepumpe. Gesperrt wird sie, solange die Wärmepumpe allein reicht, denn sie macht aus 1 kWh Strom nur 1 kWh Wärme statt 3–4.")}</p>
      <div class="fields">
        <label class="field">
          <span class="field-label">${t("Die Integration bietet")}</span>
          <select @change=${(e) => this._set({ rod_method: e.target.value, rod_entity: "", rod_options: {} })}>
            ${Object.entries(ROD_METHODS).map(([key, [label]]) => html`<option value=${key} ?selected=${method === key}>${label}</option>`)}
          </select>
          <span class="small muted">${ROD_METHODS[method][1]}</span>
        </label>
        <div class="field">
          <span class="field-label">${t("Entität")}</span>
          <sf-picker label=${t("Entität")} placeholder=${t("Name oder Entität suchen …")}
            .value=${form.rod_entity} .options=${(lists[method] ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} ${c.unit ?? ""}` }))}
            @change=${(e) => {
              const chosen = (candidates.selects ?? []).find((c) => c.entity_id === e.detail.value);
              this._set({ rod_entity: e.detail.value, ...(method === "select" ? { rod_options: guessRodOptions(chosen?.options ?? []) } : {}) });
            }}></sf-picker>
        </div>
        ${method === "select" && selected
          ? Object.entries(ROD_STATES).map(([state, label]) => html`<label class="field">
              <span class="field-label">${t("Option für „{state}“", { state: label })}</span>
              <select @change=${(e) => this._set({ rod_options: { ...this._form.rod_options, [state]: e.target.value } })}>
                <option value="">${state === "max" ? t("wie „frei“") : t("– wählen –")}</option>
                ${selected.options.map((o) => html`<option value=${o} ?selected=${form.rod_options[state] === o}>${o}</option>`)}
              </select>
            </label>`)
          : nothing}
        ${method === "limit"
          ? html`${value("rod_limit_normal", t("Normaler Wert"), t("wie vom Installateur eingestellt"))}
            ${value("rod_limit_max", t("Wert bei Freigabe (optional)"), t("leer: wie normal"))}
            ${value("rod_limit_blocked", t("Wert für gesperrt"))}`
          : nothing}
      </div>
      <div class="fields">
        ${mode("rod_heating", t("In der Heizperiode"), ROD_HEATING, t("Nur bei Bedarf: gesperrt, außer es ist kälter als die Grenze unten, der Strom kostet weniger als nichts oder du gibst ihn frei."))}
        ${mode("rod_summer", t("Im Sommer"), ROD_SUMMER, t("Bei PV-Überschuss: zusätzlich freigegeben, solange Überschuss eingeplant ist."))}
        <label class="field">
          <span class="field-label">${t("Frei unter Außentemperatur")}</span>
          <span class="input-unit"><input inputmode="decimal" .value=${form.rod_bivalence}
            @input=${(e) => this._set({ rod_bivalence: e.target.value })}><span class="muted">°C</span></span>
          <span class="small muted">${t("etwa der Bivalenzpunkt der Wärmepumpe; ohne Außentemperatur bleibt er frei")}</span>
        </label>
        <label class="field">
          <span class="field-label">${t("Höchstens ein Wechsel alle")}</span>
          <span class="input-unit"><input inputmode="numeric" .value=${form.rod_min_state}
            @input=${(e) => this._set({ rod_min_state: e.target.value })}><span class="muted">min</span></span>
          <span class="small muted">${t("Die Einstellung liegt meist im Flash-Speicher der Wärmepumpe – selten schreiben.")}</span>
        </label>
      </div>
      <p class="small muted">${t("Reicht die Wärmepumpe nicht, gibst du den Heizstab auf dieser Seite für 24 h frei. Wird die Wärmepumpe nicht mehr von Sunflow gesteuert, stellt Sunflow den normalen Wert wieder her.")}</p>
    `;
  }

  _renderRodConsumer(form) {
    const taken = new Set(this._heatPumps.filter((d) => d.id !== this._editId).map((d) => d.heat_pump?.rod_id).filter(Boolean));
    const rods = this._rods.filter((d) => !taken.has(d.id));
    const mode = (field, label, help) => html`<label class="field">
      <span class="field-label">${label}</span>
      <select @change=${(e) => this._set({ [field]: e.target.value })}>
        ${Object.entries(MODES).map(([value, text]) => html`<option value=${value} ?selected=${form[field] === value}>${text}</option>`)}
      </select>
      <span class="small muted">${help}</span>
    </label>`;
    return html`
      <p class="small muted">${t("Ein Heizstab außerhalb der Wärmepumpe, den Sunflow selbst schaltet – meist im Warmwasser- oder Pufferspeicher.")}</p>
      <div class="fields">
        <label class="field">
          <span class="field-label">${t("Verbraucher")}</span>
          <select @change=${(e) => this._set({ ...rodFormOf(e.target.value || null, this._config.planning), rod_kind: "consumer" })}>
            <option value="" ?selected=${!form.rod_id}>${t("– wählen –")}</option>
            ${rods.map((rod) => html`<option value=${rod.id} ?selected=${form.rod_id === rod.id}>${rod.name}</option>`)}
          </select>
          ${rods.length
            ? nothing
            : html`<span class="small muted">${t("Einen Heizstab legst du unter Geräte → Eigene Verbraucher an, mit Art „Wärme“ (z. B. „Relais je Phase (1–3)“ oder „Leistungsstufen (Auswahl)“). Danach erscheint er hier.")}</span>`}
        </label>
      </div>
      ${form.rod_id
        ? html`
          <p class="small muted">${t("Die Wärmepumpe macht aus 1 kWh Strom 3–4 kWh Wärme, der Heizstab nur 1 kWh. In der Heizperiode bekommt die Wärmepumpe deshalb den PV-Überschuss (Verstärken), der Heizstab bleibt aus. Im Sommer genügt der Überschuss meist für beide: Die Wärmepumpe hält das Warmwasser auf ihrem Sollwert, der Heizstab heizt mit dem restlichen Überschuss darüber hinaus, z. B. auf 60–65 °C. Das spart der Wärmepumpe Starts und hohe Temperaturen, bei denen ihr COP sinkt.")}</p>
          <div class="fields">
            ${mode("rod_heating_mode", t("In der Heizperiode"), t("empfohlen: Aus"))}
            ${mode("rod_summer_mode", t("Im Sommer"), t("empfohlen: Nur PV-Überschuss"))}
            <label class="field">
              <span class="field-label">${t("Von Hand an höchstens")}</span>
              <span class="input-unit"><input inputmode="decimal" .value=${form.rod_manual_h}
                @input=${(e) => this._set({ rod_manual_h: e.target.value })}><span class="muted">h</span></span>
              <span class="small muted">${t("Reicht die Wärmepumpe nicht, den Heizstab unter Steuerung auf „Schnellstmöglich“ stellen; das endet nach dieser Zeit.")}</span>
            </label>
          </div>
          <p class="small muted">${t("Bis zu welcher Temperatur der Heizstab heizt, stellst du unter Verbraucher ein (Zieltemperatur).")}</p>`
        : nothing}
    `;
  }

  /** The house: rooms, heat into it and the comfort band; below what Sunflow learned from them. */
  _renderHouse() {
    if (!this._house) return nothing;
    if (this._candidates === null) this._loadCandidates();
    const house = this._houseEdit ?? this._house;
    const candidates = this._candidates ?? {};
    const options = (list) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} ${c.unit ?? ""}` }));
    const set = (changes) => (this._houseEdit = { ...house, ...changes });
    const setRoom = (i, changes) => set({ rooms: house.rooms.map((r, k) => (k === i ? { ...r, ...changes } : r)) });
    const number = (field, label, unit = "°C") => html`<label class="field">
      <span class="field-label">${label}</span>
      <span class="input-unit"><input inputmode="decimal" .value=${toDisplay(house[field], "")}
        @change=${(e) => { const v = fromDisplay(e.target.value, ""); if (v != null) set({ [field]: v }); }}><span class="muted">${unit}</span></span>
    </label>`;
    const time = (field, label) => html`<label class="field">
      <span class="field-label">${label}</span>
      <span class="input-unit"><input type="time" .value=${house[field]} @change=${(e) => e.target.value && set({ [field]: e.target.value })}></span>
    </label>`;
    return html`
      <section class="device">
        <span class="device-title">${t("Haus")}</span>
        <p class="small muted">${t("Aus den Raumtemperaturen lernt Sunflow, wie schnell das Haus auskühlt und wie stark Heizung und Sonne es erwärmen. Damit lässt sich später planen, wie lange die Wärmepumpe pausieren darf und wann Vorheizen lohnt. Sunflow regelt keine einzelnen Räume.")}</p>
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
            <label class="check"><input type="radio" name="reference" .checked=${room.reference}
              @change=${() => set({ rooms: house.rooms.map((r, k) => ({ ...r, reference: k === i })) })}> ${t("Referenzraum")}</label>
            <button class="secondary" @click=${() => set({ rooms: house.rooms.filter((_, k) => k !== i) })}>${t("Entfernen")}</button>
          </div>`)}
          <div><button class="secondary" @click=${() => set({ rooms: [...house.rooms, { entity_id: "", weight: 1, reference: !house.rooms.length }] })}>${t("Raum hinzufügen")}</button></div>
          <p class="small muted">${t("Gewicht z. B. nach Fläche; ohne Angabe zählen alle Räume gleich. Für den Referenzraum gilt das Komfortband, etwa das Wohnzimmer.")}</p>
          <div class="fields">
            <div class="field">
              <span class="field-label">${t("Wärme ins Haus (optional)")}</span>
              <sf-picker label=${t("Wärme ins Haus (optional)")} empty-label=${t("aus der Wärmepumpe")} placeholder=${t("Name oder Entität suchen …")}
                .value=${house.heat_entity ?? ""} .options=${options(candidates.power)}
                @change=${(e) => set({ heat_entity: e.detail.value || null })}></sf-picker>
              <span class="small muted">${t("Wärmeleistung in W, z. B. vom Wärmemengenzähler der Heizung. Ohne Angabe nimmt Sunflow die Wärme der Wärmepumpe.")}</span>
            </div>
          </div>
        </fieldset>
        <fieldset class="group">
          <legend>${t("Komfortband")}</legend>
          <div class="fields">
            ${number("comfort_min", t("Nicht kälter als"))}
            ${number("comfort_max", t("Nicht wärmer heizen als"))}
          </div>
          <label class="check"><input type="checkbox" .checked=${house.night_min != null}
            @change=${(e) => set({ night_min: e.target.checked ? house.comfort_min - 2 : null })}> ${t("Nachtabsenkung")}</label>
          ${house.night_min != null
            ? html`<div class="fields">${number("night_min", t("Nachts nicht kälter als"))}${time("night_start", t("Von"))}${time("night_end", t("Bis"))}</div>`
            : nothing}
        </fieldset>
        ${this._houseEdit
          ? html`<div class="actions">
              <button class="primary" ?disabled=${this._busy || house.rooms.some((r) => !r.entity_id)} @click=${() => this._saveHouse()}>${t("Speichern")}</button>
              <button class="secondary" @click=${() => (this._houseEdit = null)}>${t("Abbrechen")}</button>
            </div>`
          : nothing}
        ${this._renderHouseModel(house)}
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
  _renderHouseModel(house) {
    const result = this._houseModel;
    if (!result || result.state === "no_rooms") return nothing;
    const one = (v) => decimal(v.toFixed(1));
    const messages = {
      no_outside: t("Es fehlt eine Außentemperatur: an der Wärmepumpe oder unter Lernquellen."),
      error: t("Nicht verfügbar: {error}", { error: result.error ?? "" }),
      learning: t("Lernt noch: {days} von {min} Tagen mit Raum- und Außentemperatur.", { days: result.days ?? 0, min: result.min_days ?? 7 }),
    };
    if (messages[result.state]) return html`<p class="small muted">${messages[result.state]}</p>`;
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
          ${tile(t("Abkühlung bei 0 °C außen"), `${one(m.cooling_k_per_h_at_0)} K/h`, t("ohne Heizung und Sonne, bei {temp} °C innen", { temp: decimal(String(house.comfort_min)) }))}
          ${m.loss_w_per_k != null ? tile(t("Wärmeverlust"), `${Math.round(m.loss_w_per_k)} W/K`, t("bei −10 °C außen etwa {kw} kW Heizleistung", { kw: one(m.loss_w_per_k * (house.comfort_min + 10) / 1000) })) : nothing}
          ${m.capacity_kwh_per_k != null ? tile(t("Wärmespeicher"), `${Math.round(m.capacity_kwh_per_k)} kWh/K`, t("Wärme für 1 K wärmer im ganzen Haus")) : nothing}
          ${m.sun_k_per_h != null ? tile(t("Sonne"), `${m.sun_k_per_h >= 0 ? "+" : ""}${one(m.sun_k_per_h)} K/h`, t("bei voller Sonne")) : nothing}
          ${m.error_k != null ? tile(t("Vorhersage über 24 h"), `± ${one(m.error_k)} K`, t("„bleibt gleich“ läge bei ± {k} K", { k: one(m.baseline_k) })) : nothing}
        </div>
        ${result.restless?.length
          ? html`<p class="small warn-text">${t("Auffällig unruhig – vielleicht Sonne, Heizkörper oder Fenster am Sensor: {rooms}. Ein geringeres Gewicht oder ein anderer Sensor hilft.", { rooms: result.restless.map(name).join(", ") })}</p>`
          : nothing}
        ${points.length
          ? html`<div class="legend small">
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
                yMin: Math.floor(Math.min(house.comfort_min - 1, ...points.map((p) => p.measured))),
                yMax: Math.ceil(Math.max(house.comfort_max + 1, ...points.map((p) => p.measured))),
                limits: [{ value: house.comfort_min, label: t("Komfort min") }, { value: house.comfort_max, label: t("Komfort max") }],
              },
            )}`
          : nothing}
      </div>
    `;
  }

  /** How the heating season is told; saved on change. */
  _renderSeason() {
    const season = this._season;
    if (!season) return nothing;
    if (this._candidates === null && season.source !== "dates") this._loadCandidates();
    const save = async (changes) => {
      try {
        this._season = await api.setHeatingSeason({ ...season, ...changes });
      } catch (err) {
        this._error = t("Heizperiode konnte nicht gespeichert werden: {error}", { error: err.message });
      }
    };
    const candidates = this._candidates ?? {};
    const options = (list) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} ${c.unit ?? ""}` }));
    const since = season.changed_at ? new Date(season.changed_at).toLocaleDateString(locale(), { day: "numeric", month: "numeric" }) : null;
    const now = season.active ? t("Gerade Heizperiode") : t("Gerade Sommer");
    return html`
      <section class="device">
        <span class="device-title">${t("Heizperiode")}</span>
        <p class="small muted">${since ? t("{state} seit {date}", { state: now, date: since }) : now} · ${t("Bestimmt, ob für den Heizstab der Modus der Heizperiode oder des Sommers gilt.")}</p>
        <div class="fields">
          <label class="field">
            <span class="field-label">${t("Erkennen nach")}</span>
            <select @change=${(e) => save({ source: e.target.value })}>
              <option value="temperature" ?selected=${season.source === "temperature"}>${t("Außentemperatur")}</option>
              <option value="dates" ?selected=${season.source === "dates"}>${t("Datum")}</option>
              <option value="entity" ?selected=${season.source === "entity"}>${t("Entität (z. B. Sommer/Winter der Wärmepumpe)")}</option>
            </select>
          </label>
          ${season.source === "temperature"
            ? html`
              <div class="field">
                <span class="field-label">${t("Außentemperatur")}</span>
                <sf-picker label=${t("Außentemperatur")} placeholder=${t("Name oder Entität suchen …")}
                  .value=${season.temperature_entity ?? ""} .options=${options(candidates.temperature)}
                  @change=${(e) => save({ temperature_entity: e.detail.value || null })}></sf-picker>
              </div>
              <label class="field">
                <span class="field-label">${t("Heizperiode unter (Tagesmittel)")}</span>
                <span class="input-unit"><input inputmode="decimal" .value=${toDisplay(season.threshold_c, "")}
                  @change=${(e) => { const v = fromDisplay(e.target.value, ""); if (v != null) save({ threshold_c: v }); }}><span class="muted">°C</span></span>
              </label>
              <label class="field">
                <span class="field-label">${t("Wechsel nach")}</span>
                <span class="input-unit"><input inputmode="numeric" .value=${String(season.days)}
                  @change=${(e) => { const v = Number.parseInt(e.target.value, 10); if (v >= 1) save({ days: v }); }}><span class="muted">${t("Tagen in Folge")}</span></span>
              </label>`
            : nothing}
          ${season.source === "dates"
            ? html`
              <label class="field">
                <span class="field-label">${t("Von (TT.MM.)")}</span>
                <span class="input-unit"><input .value=${mmddToText(season.start)} placeholder="01.10"
                  @change=${(e) => { const v = textToMmdd(e.target.value); if (v) save({ start: v }); }}></span>
              </label>
              <label class="field">
                <span class="field-label">${t("Bis (TT.MM.)")}</span>
                <span class="input-unit"><input .value=${mmddToText(season.end)} placeholder="30.04"
                  @change=${(e) => { const v = textToMmdd(e.target.value); if (v) save({ end: v }); }}></span>
              </label>`
            : nothing}
          ${season.source === "entity"
            ? html`
              <div class="field">
                <span class="field-label">${t("Entität")}</span>
                <sf-picker label=${t("Entität")} placeholder=${t("Name oder Entität suchen …")}
                  .value=${season.entity_id ?? ""} .options=${options([...(candidates.selects ?? []), ...(candidates.switches ?? []), ...(candidates.presence ?? [])])}
                  @change=${(e) => save({ entity_id: e.detail.value || null })}></sf-picker>
              </div>
              <label class="field">
                <span class="field-label">${t("Zustände für Heizperiode")}</span>
                <span class="input-unit"><input .value=${season.entity_states.join(", ")}
                  @change=${(e) => save({ entity_states: e.target.value.split(",").map((v) => v.trim()).filter(Boolean) })}></span>
              </label>`
            : nothing}
        </div>
      </section>
    `;
  }

  static styles = [toggleSheet, css`
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
    .title-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; font-size: 13px; }
    .field select { min-height: 44px; font-size: 14px; border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text); padding: 0 10px; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .group { border: 1px solid var(--sf-border); border-radius: 8px; padding: 12px 14px; margin: 0; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
    .group legend { font-weight: 600; font-size: 14px; padding: 0 4px; }
    .check { display: flex; gap: 8px; align-items: center; font-size: 14px; min-height: 36px; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .spacer { flex: 1; }
    .primary, .secondary { min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .danger { color: var(--sf-error-text); }
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
    .chart .limit { stroke: var(--sf-muted); stroke-width: 1; stroke-dasharray: 6 4; }
    .chart .limit-label { paint-order: stroke; stroke: var(--sf-surface); stroke-width: 3px; }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 10px 12px; }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
  `];
}

customElements.define("sf-heat-pump", SunflowHeatPump);
