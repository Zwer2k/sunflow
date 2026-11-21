import { locale, t } from "./i18n.js";

/** How a consumer is planned, as chosen in the control tab and the live view's quick controls. */
export const MODES = {
  cost: t("Kostenoptimal"),
  pv_only: t("Nur PV"),
  fast: t("Sofort"),
  pause: t("Pause"),
};

const time = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });
const dayTime = new Intl.DateTimeFormat(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });

/** End of an override for "midnight", "hours4" or "open" (null: until further notice). */
export function untilFor(duration) {
  if (duration === "open") return null;
  const until = new Date();
  if (duration === "hours4") until.setHours(until.getHours() + 4);
  else until.setHours(24, 0, 0, 0);
  return until.toISOString();
}

export function untilText(until, untilDone = false) {
  if (untilDone) return t("bis Ladeende");
  if (!until) return t("bis auf Weiteres");
  const date = new Date(until);
  return date.toDateString() === new Date().toDateString() || date.getHours() === 0
    ? date.getHours() === 0 && date.getMinutes() === 0 ? t("bis Mitternacht") : t("bis {time}", { time: time.format(date) })
    : t("bis {time}", { time: dayTime.format(date) });
}

/** Duration of a new consumer change: at a wallbox until the car is unplugged, else until midnight. */
export function defaultDuration(device) {
  return device.connected === null || device.connected === undefined ? "midnight" : "session";
}

/** Fields of an override for a duration; "session" ends when the car is unplugged. */
export function durationFields(duration) {
  return duration === "session" ? { until: null, until_done: true } : { until: untilFor(duration), until_done: false };
}

/** The override to store: a further change keeps the duration chosen before, a first one gets the default. */
export function deviceOverride(device, changes) {
  const next = overrideFor(device, changes);
  if (next === null || device.override) return next;
  return { ...next, ...durationFields(defaultDuration(device)) };
}

/** The override after choosing a mode; null when the consumer is back at its default. */
export function overrideFor(device, changes) {
  const current = device.override ?? { strategy: null, target: null, paused: false };
  const next = { ...current, ...changes };
  return next.strategy === null && next.target === null && !next.paused ? null : next;
}

/** Changes for a mode: the default strategy drops the strategy override instead of storing a copy. */
export function modeChanges(device, mode) {
  if (mode === "pause") return { paused: true, strategy: null };
  return { paused: false, strategy: mode === device.default.strategy ? null : mode };
}

export function currentMode(device) {
  return device.paused ? "pause" : device.override?.strategy ?? device.default.strategy;
}

// Hot water above 60 °C only when set as the maximum in the settings (scalding)
const TARGETS = {
  vehicle: { presets: [50, 80, 100], min: 10, max: 100, unit: "%" },
  heater: { presets: [45, 55, 60], min: 30, max: 60, unit: "°C" },
};

/** The target without a change by hand (for a car its charging settings at this wallbox), rounded. */
export function defaultTarget(device) {
  return device.default_target === null || device.default_target === undefined ? null : Math.round(device.default_target);
}

/** Presets and range of the knob for a consumer's target; null when it has none. */
export function targetRange(device) {
  const base = TARGETS[device.kind];
  if (!base) return null;
  const max = device.default.target_max ?? base.max;
  return { ...base, max, presets: base.presets.filter((p) => p <= max) };
}
