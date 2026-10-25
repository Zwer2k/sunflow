// Relative URLs so the UI works behind Home Assistant Ingress.
const BASE = "api/v1";

async function request(method, path, body) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    throw new Error(data?.detail ?? `${response.status} ${response.statusText}`);
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
  energy: (period) => request("GET", `/energy?period=${encodeURIComponent(period)}`),
  efficiency: (configDeviceId) => request("GET", `/config/devices/${encodeURIComponent(configDeviceId)}/efficiency`),
  history: (hours = 24) => request("GET", `/history?hours=${hours}`),
  shadow: (hours = 48) => request("GET", `/shadow?hours=${hours}`),
  setOptimizer: (settings) => request("PUT", "/config/optimizer", settings),
  setOverrides: (configDeviceId, overrides) =>
    request("PUT", `/config/devices/${encodeURIComponent(configDeviceId)}/role_overrides`, overrides),
};
