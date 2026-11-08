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
  switchTest: (host: string, channel = 0) => req("POST", "/api/switches/test", { host, channel }),
  calibration: (range: string) => req("GET", `/api/calibration?range=${range}`),
  applyCalibration: (body: Json) => req("POST", "/api/calibration/apply", body),
  weatherCoverage: () => req("GET", "/api/weather/coverage"),
  weatherFetch: (station: string, start: string, end: string) => req("POST", "/api/weather/fetch", { station, start, end }),
  seasonal: (range: string) => req("GET", `/api/reports/seasonal?range=${range}`),
  annual: (body: Json) => req("POST", "/api/reports/annual", body),
  calc: (t_f: number, rh_pct: number) => req("POST", "/api/calc", { t_f, rh_pct }),
  mockState: () => req("GET", "/api/mock/state"),
  mockFault: (target: string, id: string | null, fault: string | null) =>
    req("POST", "/api/mock/fault", { target, id, fault }),
};
