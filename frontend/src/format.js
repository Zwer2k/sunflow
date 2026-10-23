const ROLE_LABELS = {
  battery_soc: "Ladestand",
  battery_power: "Batterie",
  pv_power: "PV",
  pv_ext_power: "PV extern",
  grid_power: "Netz",
  load_power: "Hausverbrauch",
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

export const SIGNED_ROLES = new Set(["battery_power", "grid_power"]);
