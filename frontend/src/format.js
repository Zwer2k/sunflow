const ROLE_LABELS = {
  battery_soc: "Ladestand",
  battery_power: "Batterie",
  pv_power: "PV",
  pv_ext_power: "PV extern",
  grid_power: "Netz",
  load_power: "Hausverbrauch",
  charge_power: "Ladeleistung",
  mode: "Modus",
  charging: "Lädt",
  connected: "Verbunden",
  vehicle_soc: "Ladestand / Temperatur",
  limit_soc: "Ziel",
  inverter_power: "WR-Ausgang (AC)",
  ac_power: "PV (AC)",
  daily_yield: "Tagesertrag",
  status: "Status",
  plan_soc: "Plan-Ziel",
  plan_time: "Plan-Abfahrt",
  pv1_power: "PV String 1",
  pv2_power: "PV String 2",
  pv1_voltage: "PV String 1 Spannung",
  pv1_current: "PV String 1 Strom",
  pv2_voltage: "PV String 2 Spannung",
  pv2_current: "PV String 2 Strom",
  pv_energy: "PV-Ertrag (Zähler)",
  pv_dc_energy: "PV-Eingang DC (Zähler)",
  load_energy: "Hausverbrauch (Zähler)",
  grid_import_energy: "Netzbezug (Zähler)",
  grid_export_energy: "Einspeisung (Zähler)",
  battery_charge_energy: "Speicher geladen (Zähler)",
  battery_discharge_energy: "Speicher entladen (Zähler)",
  switch: "Schalter",
  power: "Leistung",
};

const number1 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const number0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

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
  if (Math.abs(watts) < 1000) return `${number0.format(rounded === 0 ? 0 : rounded)} W`;
  const digits = Math.min(2, Math.max(0, Math.round(3 - Math.log10(Math.max(step, 1)))));
  const number = new Intl.NumberFormat("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${number.format(watts / 1000)} kW`;
}

export function formatValue(value, unit) {
  if (value === null || value === undefined) return "–";
  // Values that round to zero are shown without sign ("0 W", not "−0 W")
  if (Math.abs(value) < 0.5 && (unit === "W" || unit === "Wh")) value = 0;
  if (unit === "W") {
    return Math.abs(value) >= 1000 ? `${number1.format(value / 1000)} kW` : `${number0.format(value)} W`;
  }
  if (unit === "Wh") {
    return Math.abs(value) >= 1000 ? `${number1.format(value / 1000)} kWh` : `${number0.format(value)} Wh`;
  }
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
    return role === "battery_power" || role === "grid_power" ? "ruht" : "";
  }
  if (role === "battery_power") return value > 0 ? "lädt" : "entlädt";
  if (role === "grid_power") return value > 0 ? "Bezug" : "Einspeisung";
  return "";
}

const PARAM_LABELS = {
  capacity_wh: "Nutzbare Kapazität",
  min_soc: "Mindest-Ladestand",
  max_charge: "Max. Ladeleistung",
  max_discharge: "Max. Entladeleistung",
  grid_offset: "Netz-Zielwert (Desired Grid Power)",
  efficiency_pct: "Wirkungsgrad Speicher (Akku und DC/DC, gesamter Zyklus)",
  inverter_efficiency_pct: "Wirkungsgrad Wechselrichter (je Richtung)",
  max_power: "Maximale Leistung",
  vehicle_capacity_wh: "Akkukapazität Fahrzeug (nutzbar)",
  charge_efficiency_pct: "Ladewirkungsgrad",
  tank_l: "Speichervolumen",
  daily_energy_wh: "Energiebedarf pro Tag",
};

// Short explanation below a parameter
const PARAM_HINTS = {
  grid_offset:
    "Puffer gegen Lastsprünge, während Sunflow steuert. Negativ (z. B. −200 W) gilt nur, wenn der Plan ohnehin einspeist " +
    "(PV-Überschuss) – sonst regelt der Wechselrichter auf 0, damit kein gespeicherter Strom ins Netz geht. Positiv = " +
    "bewusst leichter Bezug, gilt immer.",
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
  return text.replace(".", ",");
}

export function fromDisplay(text, unit) {
  const trimmed = String(text).trim().replace(",", ".");
  if (trimmed === "") return null;
  const number = Number(trimmed);
  if (Number.isNaN(number)) return undefined;
  return unit === "Wh" ? Math.round(number * 1000) : number;
}

export const FEED_IN_MODELS = {
  eeg_fixed: "Feste EEG-Vergütung",
  solar_peak: "Solarspitzengesetz (keine Vergütung bei negativen Preisen)",
  direct: "Direktvermarktung / dynamisch",
};

const CONTROL_CAPABILITIES = new Set(["battery_window", "curtail", "switch_off", "ev_charger"]);

// Purpose of a device derived from its profile capabilities, e.g. "Wechselrichter + Speicher"
/** Device groups in the UI, in this order (the backend decides which device is which). */
export const CATEGORIES = [
  ["inverter", "Wechselrichter"],
  ["battery", "Speicher"],
  ["meter", "Zähler"],
  ["wallbox", "Wallboxen und Fahrzeuge"],
  ["heat", "Wärme"],
  ["load", "Verbraucher"],
];
// For a single device
export const CATEGORY_LABELS = {
  inverter: "Wechselrichter",
  battery: "Speicher",
  meter: "Zähler",
  wallbox: "Wallbox",
  heat: "Wärme",
  load: "Verbraucher",
};

export function devicePurpose(capabilities) {
  const has = (c) => capabilities.includes(c);
  if (has("ev_charger")) return "Wallbox";
  if (has("loadpoint")) return "Ladepunkt (evcc)";
  if (has("battery")) return has("pv") ? "Wechselrichter + Speicher" : "Speicher";
  if (has("pv")) return "PV-Wechselrichter";
  if (has("load")) return "Verbraucher";
  if (has("grid_meter")) return "Netzzähler";
  return "Gerät";
}

export function isControllable(capabilities) {
  return capabilities.some((c) => CONTROL_CAPABILITIES.has(c));
}

export const SIGNED_ROLES = new Set(["battery_power", "grid_power"]);
