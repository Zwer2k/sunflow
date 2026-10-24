const ROLE_LABELS = {
  battery_soc: "Ladestand",
  battery_power: "Batterie",
  pv_power: "PV",
  pv_ext_power: "PV extern",
  grid_power: "Netz",
  load_power: "Hausverbrauch",
  inverter_power: "WR-Ausgang (AC)",
  ac_power: "PV (AC)",
  daily_yield: "Tagesertrag",
  status: "Status",
};

const number1 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const number0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

export function roleLabel(role) {
  return ROLE_LABELS[role] ?? role;
}

export function formatValue(value, unit) {
  if (value === null || value === undefined) return "–";
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
  grid_offset: "Einspeise-Puffer (Netz-Zielwert)",
  efficiency_pct: "Wirkungsgrad Laden bzw. Entladen",
};

export function paramLabel(name) {
  return PARAM_LABELS[name] ?? name;
}

// Energy is entered in kWh, stored in Wh
export function displayUnit(unit) {
  return unit === "Wh" ? "kWh" : unit;
}

export function toDisplay(value, unit) {
  if (value === null || value === undefined) return "";
  const text = unit === "Wh" ? String(Math.round(value / 100) / 10) : String(value);
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
export function devicePurpose(capabilities) {
  const has = (c) => capabilities.includes(c);
  if (has("ev_charger")) return "Wallbox";
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
