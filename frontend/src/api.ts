/* Thin API client. Backend values are SI; the UI converts for display. */

export type Json = any; // eslint-disable-line @typescript-eslint/no-explicit-any

async function req<T = Json>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) {
    const detail = data?.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d: Json) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("\n")
      : detail || r.statusText;
    throw new Error(msg);
  }
  return data as T;
}

export const api = {
  status: () => req("GET", "/api/status"),
  config: () => req("GET", "/api/config"),
  saveConfig: (c: Json) => req("PUT", "/api/config", c),
  exampleConfig: () => req("GET", "/api/config/example"),
  startNow: () => req("POST", "/api/start-now"),
  setMode: (mode: string, minutes?: number, maintenance = false) =>
    req("POST", "/api/mode", { mode, minutes, maintenance }),
  history: (range: string, start?: string, end?: string) =>
    req("GET", `/api/history?range=${range}${start ? `&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end!)}` : ""}`),
  stats: (range: string, start?: string, end?: string) =>
    req("GET", `/api/stats?range=${range}${start ? `&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end!)}` : ""}`),
  events: (kind?: string, limit = 300) => req("GET", `/api/events?limit=${limit}${kind ? `&kind=${kind}` : ""}`),
  profiles: () => req("GET", "/api/sim/profiles"),
  profile: (id: string) => req("GET", `/api/sim/profiles/${id}`),
  simDefaults: () => req("GET", "/api/sim/defaults"),
  simRun: (body: Json) => req("POST", "/api/sim/run", body),
  replay: (range: string, start?: string, end?: string) => req("POST", "/api/replay", { range, start, end }),
  discover: () => req("GET", "/api/sensors/discover"),
  sensorsRaw: () => req("GET", "/api/sensors/raw"),
  discoverTapo: () => req("GET", "/api/switches/discover-tapo"),
  switchTest: (host: string, channel = 0) => req("POST", "/api/switches/test", { host, channel }),
  calibration: (range: string) => req("GET", `/api/calibration?range=${range}`),
  applyCalibration: (body: Json) => req("POST", "/api/calibration/apply", body),
  weatherCoverage: () => req("GET", "/api/weather/coverage"),
  weatherFetch: (station: string, start: string, end: string) => req("POST", "/api/weather/fetch", { station, start, end }),
  seasonal: (range: string) => req("GET", `/api/reports/seasonal?range=${range}`),
  annual: (body: Json) => req("POST", "/api/reports/annual", body),
  calc: (t_f: number, rh_pct: number) => req("POST", "/api/calc", { t_f, rh_pct }),
  setPolicy: (policy: string) => req("POST", "/api/policy", { policy }),
  shadow: (range = "7d") => req("GET", `/api/shadow?range=${range}`),
  thermal: () => req("GET", "/api/thermal"),
  thermalFit: () => req("POST", "/api/thermal/fit"),
  forecast: () => req("GET", "/api/forecast"),
  simRuns: () => req("GET", "/api/sim/runs"),
  zones: () => req("GET", "/api/zones"),
  zoneStats: (range: string) => req("GET", `/api/zones/stats?range=${range}`),
  surfaces: () => req("GET", "/api/surfaces"),
  circulation: () => req("GET", "/api/circulation"),
  circulationMode: (mode: string) => req("POST", "/api/circulation/mode", { mode }),
  circulatorManual: (id: string, state: string, minutes?: number) =>
    req("POST", `/api/circulators/${id}/manual`, { state, minutes }),
  circulationCycles: (id?: string) => req("GET", `/api/circulation/cycles${id ? `?circulator_id=${id}` : ""}`),
  circulationEffectiveness: (range = "30d") => req("GET", `/api/circulation/effectiveness?range=${range}`),
  compareStatus: () => req("GET", "/api/sensors/compare"),
  compareAction: (action: string, sensor_ids?: string[], apply = false) =>
    req("POST", "/api/sensors/compare", { action, sensor_ids, apply }),
  simZones: (body: Json) => req("POST", "/api/sim/zones", body),
  simZoneStrategies: () => req("GET", "/api/sim/zones/strategies"),
  updateInfo: () => req("GET", "/api/update"),
  updateNow: () => req("POST", "/api/update"),
  setup: (probe = true) => req("GET", `/api/setup?probe=${probe}`),
  setupProgress: (body: Json) => req("PUT", "/api/setup/progress", body),
  setupReset: () => req("POST", "/api/setup/reset"),
  chatSettings: () => req("GET", "/api/chat/settings"),
  saveChatSettings: (body: { api_key?: string; model?: string }) => req("PUT", "/api/chat/settings", body),
  chatModels: () => req("GET", "/api/chat/models"),
  chat: (messages: { role: string; content: string }[], model?: string, snapshot_id?: string) =>
    req("POST", "/api/chat", { messages, model, snapshot_id }),
  fanOverride: (id: string, state: string, minutes?: number) =>
    req("POST", `/api/fans/${encodeURIComponent(id)}/override`, { state, minutes }),
  dismissAlerts:(ids?: string[]) => req("POST", "/api/alerts/dismiss", { ids }),
  restoreAlerts: () => req("POST", "/api/alerts/dismiss", { restore: true }),
  resetData: (scope: string, at?: string) => req("POST", "/api/data/reset", { scope, at }),
  mockState: () => req("GET", "/api/mock/state"),
  mockFault: (target: string, id: string | null, fault: string | null) =>
    req("POST", "/api/mock/fault", { target, id, fault }),
};
