import { lang, locale, t, tc } from "./i18n.js";
const ROLE_LABELS = {
  battery_soc: t("Ladestand"),
  battery_power: t("Batterie"),
  pv_power: "PV",
  pv_ext_power: t("PV extern"),
  grid_power: t("Netz"),
  load_power: t("Hausverbrauch"),
  charge_power: t("Ladeleistung"),
  mode: t("Modus"),
  charging: t("Lädt"),
  connected: t("Verbunden"),
  vehicle_soc: t("Ladestand / Temperatur"),
  limit_soc: t("Ziel"),
  inverter_power: t("WR-Ausgang (AC)"),
  ac_power: t("PV (AC)"),
  daily_yield: t("Tagesertrag"),
  status: t("Status"),
  plan_soc: t("Plan-Ziel"),
  plan_time: t("Plan-Abfahrt"),
  pv1_power: t("PV String 1"),
  pv2_power: t("PV String 2"),
  pv1_voltage: t("PV String 1 Spannung"),
  pv1_current: t("PV String 1 Strom"),
  pv2_voltage: t("PV String 2 Spannung"),
  pv2_current: t("PV String 2 Strom"),
  pv_energy: t("PV-Ertrag (Zähler)"),
  pv_dc_energy: t("PV-Eingang DC (Zähler)"),
  load_energy: t("Hausverbrauch (Zähler)"),
  grid_import_energy: t("Netzbezug (Zähler)"),
  grid_export_energy: t("Einspeisung (Zähler)"),
  battery_charge_energy: t("Speicher geladen (Zähler)"),
  battery_discharge_energy: t("Speicher entladen (Zähler)"),
  switch: t("Schalter"),
  power: t("Leistung"),
  charged_energy: t("Geladen (Ladevorgang)"),
  stored_current: t("Eingestellter Ladestrom"),
  allowed_current: t("Erlaubter Ladestrom"),
  transaction: t("Ladekarte"),
  card_required: t("Karte nötig"),
  phase_mode: t("Phasenmodus"),
  level: t("Leistungsstufe"),
  current: t("Stromstärke"),
  energy: t("Energie (Zähler)"),
  temperature: t("Temperatur"),
};

const number1 = new Intl.NumberFormat(locale(), { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const number0 = new Intl.NumberFormat(locale(), { maximumFractionDigits: 0 });

export function roleLabel(role) {
  return ROLE_LABELS[role] ?? role;
}

const POWER_FACTORS = { W: 1, kW: 1000, MW: 1_000_000 };

/**
 * Power (W) shown like its source entity in Home Assistant: in the entity's unit with
 * its display precision. display: {unit, precision} from the API; without it as usual.
 */
export function formatPower(watts, display) {
  if (watts === null || watts === undefined) return "–";
  // W below 1 kW, kW above; never finer than the entity resolves (e.g. 0.1 kW steps)
  const step = POWER_FACTORS[display?.unit] && display.precision !== null && display.precision !== undefined
    ? POWER_FACTORS[display.unit] / 10 ** display.precision
    : 1;
  const rounded = Math.round(watts / step) * step;
  // By the rounded value: 999.6 W is "1,0 kW", not "1.000 W"
  if (Math.abs(rounded) < 1000) return `${number0.format(rounded === 0 ? 0 : rounded)} W`;
  const digits = Math.min(2, Math.max(0, Math.round(3 - Math.log10(Math.max(step, 1)))));
  const number = new Intl.NumberFormat(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${number.format(watts / 1000)} kW`;
}

export function formatValue(value, unit) {
  if (value === null || value === undefined) return "–";
  // Values that round to zero are shown without sign ("0 W", not "−0 W")
  if (Math.abs(value) < 0.5 && (unit === "W" || unit === "Wh")) value = 0;
  // By the rounded value: 999.6 is "1,0 k…", not "1.000 …"
  const kilo = Math.round(Math.abs(value)) >= 1000;
  if (unit === "W") return kilo ? `${number1.format(value / 1000)} kW` : `${number0.format(value)} W`;
  if (unit === "Wh") return kilo ? `${number1.format(value / 1000)} kWh` : `${number0.format(value)} Wh`;
  if (unit === "%") return `${number0.format(value)} %`;
  return unit ? `${number1.format(value)} ${unit}` : number1.format(value);
}

export function formatSigned(value, unit) {
  const text = formatValue(value, unit);
  return value > 0 ? `+${text}` : text.replace("-", "−");
}

// Direction hint below a value, following Sunflow sign conventions.
export function roleHint(role, value) {
  if (value === null || value === undefined || Math.abs(value) < 20) {
    return role === "battery_power" || role === "grid_power" ? t("ruht") : "";
  }
  if (role === "battery_power") return value > 0 ? t("lädt") : t("entlädt");
  if (role === "grid_power") return value > 0 ? t("Bezug") : t("Einspeisung");
  return "";
}

const PARAM_LABELS = {
  capacity_wh: t("Nutzbare Kapazität"),
  min_soc: t("Mindest-Ladestand"),
  max_charge: t("Max. Ladeleistung"),
  max_discharge: t("Max. Entladeleistung"),
  grid_offset: t("Netz-Zielwert (Desired Grid Power)"),
  efficiency_pct: t("Wirkungsgrad Speicher (Akku und DC/DC, gesamter Zyklus)"),
  inverter_efficiency_pct: t("Wirkungsgrad Wechselrichter (je Richtung)"),
  max_power: t("Maximale Leistung"),
  vehicle_capacity_wh: t("Akkukapazität Fahrzeug (nutzbar)"),
  charge_efficiency_pct: t("Ladewirkungsgrad"),
  tank_l: t("Speichervolumen"),
  daily_energy_wh: t("Energiebedarf pro Tag"),
  phases: t("Phasen (ohne Umschaltung)"),
  min_current_a: t("Mindest-Ladestrom"),
  max_current_a: t("Maximaler Ladestrom"),
  phase_mode: t("Phasenumschaltung"),
  phase_up_w: t("Auf 3 Phasen ab"),
  phase_down_w: t("Auf 1 Phase unter"),
  phase_up_delay_min: t("Hochschalten nach"),
  phase_down_delay_min: t("Herunterschalten nach"),
  phase_min_interval_min: t("Mindestabstand zwischen Umschaltungen"),
  phase_max_switches_day: t("Höchstens Umschaltungen pro Tag"),
  current_interval_min: t("Ladestrom ändern höchstens alle"),
  max_ac_power: t("Max. AC-Leistung"),
  autorepeat_s: t("Befehl wiederholen für"),
};

// Short explanation below a parameter
const PARAM_HINTS = {
  phase_up_w: t("Leistung, die das Auto im Mittel bekommen kann, bevor auf 3 Phasen geschaltet wird; über 3 × Mindeststrom (bei 6 A ≈ 4,1 kW) plus Reserve. Beim PV-Überschuss nur, wenn der Plan auch für die nächste Viertelstunde genug erwartet."),
  phase_down_w: t("Darunter wird auf 1 Phase geschaltet; liegt unter „Auf 3 Phasen ab“, damit es nicht pendelt."),
  phase_up_delay_min: t("So lange muss der Überschuss im Mittel reichen – kurze Sonnenlücken schalten nicht."),
  phase_down_delay_min: t("Kürzer als beim Hochschalten: sonst bezieht das Auto Netzstrom."),
  phase_min_interval_min: t("Jede Umschaltung unterbricht das Laden kurz."),
  current_interval_min: t("Die Wallbox speichert den Ladestrom dauerhaft; seltener ändern schont ihren Speicher."),
  max_ac_power: t("Höchste Ausgangsleistung des Wechselrichters; gilt wieder, wenn Sunflow das Abregeln beendet."),
  autorepeat_s: t("So lange wiederholt die Integration einen Befehl ohne neuen von Sunflow."),
  grid_offset: t("Puffer gegen Lastsprünge, während Sunflow steuert. Negativ (z. B. −200 W) gilt nur, wenn der Plan ohnehin einspeist (PV-Überschuss) – sonst regelt der Wechselrichter auf 0, damit kein gespeicherter Strom ins Netz geht. Positiv = bewusst leichter Bezug, gilt immer."),
};

export function paramHint(name) {
  return PARAM_HINTS[name] ?? null;
}

export function paramLabel(name) {
  return PARAM_LABELS[name] ?? name;
}

// Energy is entered in kWh, stored in Wh
export function displayUnit(unit) {
  return unit === "Wh" ? "kWh" : unit;
}

export function toDisplay(value, unit) {
  if (value === null || value === undefined) return "";
  // Wh are entered in kWh; three decimals keep small consumers (e.g. 40 Wh = 0,04 kWh)
  const text = unit === "Wh" ? String(Math.round(value) / 1000) : String(value);
  return lang() === "de" ? text.replace(".", ",") : text;
}

export function fromDisplay(text, unit) {
  const trimmed = String(text).trim().replace(",", ".");
  if (trimmed === "") return null;
  const number = Number(trimmed);
  if (Number.isNaN(number)) return undefined;
  return unit === "Wh" ? Math.round(number * 1000) : number;
}

export const FEED_IN_MODELS = {
  eeg_fixed: t("Feste EEG-Vergütung"),
  solar_peak: t("Solarspitzengesetz (keine Vergütung bei negativen Preisen)"),
  direct: t("Direktvermarktung / dynamisch"),
};

const CONTROL_CAPABILITIES = new Set(["battery_window", "battery_control", "curtail", "switch_off", "ev_charger"]);

// Purpose of a device derived from its profile capabilities, e.g. "Wechselrichter + Speicher"
/** Device groups in the UI, in this order (the backend decides which device is which). */
export const CATEGORIES = [
  ["inverter", tc("group", "Wechselrichter")],
  ["battery", tc("group", "Speicher")],
  ["meter", tc("group", "Zähler")],
  ["wallbox", t("Wallboxen und Fahrzeuge")],
  ["heat", t("Wärme")],
  ["load", tc("group", "Verbraucher")],
];
// For a single device
export const CATEGORY_LABELS = {
  inverter: t("Wechselrichter"),
  battery: t("Speicher"),
  meter: t("Zähler"),
  wallbox: t("Wallbox"),
  heat: t("Wärme"),
  load: t("Verbraucher"),
};

export function devicePurpose(capabilities) {
  const has = (c) => capabilities.includes(c);
  if (has("ev_charger")) return t("Wallbox");
  if (has("loadpoint")) return t("Ladepunkt (evcc)");
  if (has("battery")) return has("pv") ? t("Wechselrichter + Speicher") : t("Speicher");
  if (has("pv")) return t("PV-Wechselrichter");
  if (has("load")) return t("Verbraucher");
  if (has("grid_meter")) return t("Netzzähler");
  return t("Gerät");
}

export function isControllable(capabilities) {
  return capabilities.some((c) => CONTROL_CAPABILITIES.has(c));
}

export const SIGNED_ROLES = new Set(["battery_power", "grid_power"]);
