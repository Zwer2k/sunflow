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
  setOverrides: (configDeviceId, overrides) =>
    request("PUT", `/config/devices/${encodeURIComponent(configDeviceId)}/role_overrides`, overrides),
};
