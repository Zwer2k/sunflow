// Relative URLs so the UI works behind Home Assistant Ingress.
const BASE = "api/v1";

// A hanging request must not block the browser's few connections to Home Assistant
const TIMEOUT_MS = 60 * 1000;

/**
 * Renew the Home Assistant Ingress session through HA's own connection in the parent page.
 *
 * The Supervisor expires the ingress_session cookie after a while without validation;
 * HA's frontend renews it only while its panel is active, so a tab or app in the
 * background comes back with "401: Unauthorized". The panel page is same-origin.
 */
export async function renewIngressSession() {
  try {
    const hass = window.parent?.document.querySelector("home-assistant")?.hass;
    if (!hass || window.parent === window) return false;
    const result = await hass.callWS({ type: "supervisor/api", endpoint: "/ingress/session", method: "post" });
    if (!result?.session) return false;
    const secure = window.location.protocol === "https:" ? ";Secure" : "";
    document.cookie = `ingress_session=${result.session};path=/api/hassio_ingress/;SameSite=Strict${secure}`;
    return true;
  } catch {
    return false;
  }
}

async function send(method, path, body) {
  return fetch(`${BASE}${path}`, {
    method,
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
}

async function request(method, path, body) {
  let response;
  try {
    response = await send(method, path, body);
    // Expired ingress session: renew once and repeat the request
    if (response.status === 401 && (await renewIngressSession())) response = await send(method, path, body);
  } catch (err) {
    if (err.name === "TimeoutError") throw new Error("Keine Antwort vom Server (Zeitüberschreitung)");
    throw err;
  }
  if (response.status === 401) {
    throw new Error("Sitzung bei Home Assistant abgelaufen – bitte die Seite neu laden");
  }
  // Server errors may come as plain text ("Internal Server Error")
  const text = response.status === 204 ? "" : await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    if (response.ok) throw new Error("Ungültige Antwort vom Server");
  }
  if (!response.ok) {
    if (response.status >= 500 && !data?.detail) {
      throw new Error(`Serverfehler (${response.status}) – Details im Log der App`);
    }
    const detail = Array.isArray(data?.detail) ? data.detail.map((e) => e.msg).join("; ") : data?.detail;
    throw new Error(detail ?? `${response.status} ${response.statusText}`);
  }
  return data;
}

export const api = {
  health: () => request("GET", "/health"),
  discovery: () => request("GET", "/discovery"),
  preview: (haDeviceId, profile, overrides) =>
    overrides
      ? request("POST", `/devices/${encodeURIComponent(haDeviceId)}/preview`, { profile, role_overrides: overrides })
      : request("GET", `/devices/${encodeURIComponent(haDeviceId)}/preview`),
  entities: (haDeviceId) => request("GET", `/devices/${encodeURIComponent(haDeviceId)}/entities`),
  config: () => request("GET", "/config"),
  addDevice: (haDeviceId, profile, overrides) =>
    request("POST", "/config/devices", { ha_device_id: haDeviceId, profile, role_overrides: overrides ?? {} }),
  parameters: (configDeviceId) => request("GET", `/config/devices/${encodeURIComponent(configDeviceId)}/parameters`),
  removeDevice: (configDeviceId) => request("DELETE", `/config/devices/${encodeURIComponent(configDeviceId)}`),
  updateDevice: (configDeviceId, changes) =>
    request("PATCH", `/config/devices/${encodeURIComponent(configDeviceId)}`, changes),
  site: () => request("GET", "/site"),
  sources: () => request("GET", "/sources"),
  summary: () => request("GET", "/setup/summary"),
  startTest: (configDeviceId) =>
    request("POST", `/config/devices/${encodeURIComponent(configDeviceId)}/test?confirm=true`),
  testState: (configDeviceId) => request("GET", `/config/devices/${encodeURIComponent(configDeviceId)}/test`),
  abortTest: (configDeviceId) => request("POST", `/config/devices/${encodeURIComponent(configDeviceId)}/test/abort`),
  completeSetup: () => request("POST", "/setup/complete"),
  setTariff: (tariff) => request("PUT", "/config/tariff", tariff),
  setForecast: (planes) => request("PUT", "/config/forecast", planes),
  tariffSeries: (entityId) =>
    request("GET", entityId ? `/tariff/series?entity_id=${encodeURIComponent(entityId)}` : "/tariff/series"),
  forecastSeries: () => request("GET", "/forecast/series"),
  plan: () => request("GET", "/plan"),
  latestPlan: () => request("GET", "/plan/latest"),
  control: () => request("GET", "/control"),
  setDeviceOverride: (id, body) => request("PUT", `/control/devices/${encodeURIComponent(id)}`, body),
  clearDeviceOverride: (id) => request("DELETE", `/control/devices/${encodeURIComponent(id)}`),
  setStrategyOverride: (body) => request("PUT", "/control/strategy", body),
  clearStrategyOverride: () => request("DELETE", "/control/strategy"),
  setPriorities: (order) => request("PUT", "/control/priorities", order),
  setPriorityConditions: (deviceId, conditions) =>
    request("PUT", `/control/priorities/${encodeURIComponent(deviceId)}/conditions`, conditions),
  live: () => request("GET", "/live"),
  candidates: () => request("GET", "/entities/candidates"),
  addManual: (body) => request("POST", "/config/devices/manual", body),
  updateManual: (configDeviceId, body) =>
    request("PUT", `/config/devices/manual/${encodeURIComponent(configDeviceId)}`, body),
  measurements: () => request("GET", "/config/measurements"),
  setMeasurements: (choices) => request("PUT", "/config/measurements", choices),
  pendingRestore: () => request("GET", "/device-test/pending-restore"),
  restoreAfterTest: () => request("POST", "/device-test/restore?confirm=true"),
  efficiencyHistory: (days) => request("GET", `/efficiency/history?days=${days}`),
  pvLearning: () => request("GET", "/pv-learning"),
  weatherEntities: () => request("GET", "/weather/entities"),
  energy: (period) => request("GET", `/energy?period=${encodeURIComponent(period)}`),
  efficiency: (configDeviceId) => request("GET", `/config/devices/${encodeURIComponent(configDeviceId)}/efficiency`),
  history: (hours = 24) => request("GET", `/history?hours=${hours}`),
  shadow: (hours = 48) => request("GET", `/shadow?hours=${hours}`),
  setOptimizer: (settings) => request("PUT", "/config/optimizer", settings),
  setOverrides: (configDeviceId, overrides) =>
    request("PUT", `/config/devices/${encodeURIComponent(configDeviceId)}/role_overrides`, overrides),
};
