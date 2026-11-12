/* Per-device data for the History tab: which sensors and plugs exist, each one's reading in every
   history row, and whether it kept reporting. Rows recorded before per-device logging fall back to the
   zone, outdoor and surface columns. Keep syntax friendly to older iPhone Safari (no .at(), findLast,
   Object.hasOwn, regex lookbehind). */
import { Json } from "../../api";
import { TempUnit, tempVal } from "../../units";

export type Kind = "avg" | "room" | "outdoor" | "probe" | "upstairs";
export type Metric = "rh" | "t" | "td";
export type Source = { id: string; name: string; kind: Kind; sub: string; color: string; sensorId?: string; zone?: string | null };
export type PlugDev = { id: string; name: string; sub: string; switchId: string; kind: "exhaust" | "circ"; group?: string; circId?: string };
export type Reading = { t: number | null; rh: number | null; td: number | null; ok: boolean | null };
export type Pt = { x: number; on: boolean; [k: string]: number | boolean | null };

const ROOM_COLORS = ["var(--hx-room1)", "var(--hx-room2)", "var(--hx-room3)", "var(--hx-room4)", "var(--hx-room5)"];
export const KIND_LABEL: Record<Kind, string> = {
  avg: "Calculated", room: "Room sensor", outdoor: "Outdoor sensor", probe: "Concrete probe", upstairs: "Upstairs sensor",
};

/** Dew point (°C) from temperature (°C) and RH (%), Magnus formula. */
export function dewC(t: number | null | undefined, rh: number | null | undefined): number | null {
  if (t == null || rh == null || rh <= 0) return null;
  const a = 17.62, b = 243.12;
  const g = Math.log(Math.min(rh, 100) / 100) + (a * t) / (b + t);
  return (b * g) / (a - g);
}

export function sourcesFrom(cfg: Json): Source[] {
  const maps: Json[] = ((cfg && cfg.sensors && cfg.sensors.mappings) || []).filter((m: Json) => m.enabled !== false);
  const zones: Json[] = (cfg && cfg.zones) || [];
  const zname = (id: string | null) => {
    for (const z of zones) if (z.id === id) return z.name;
    return null;
  };
  const rooms = maps.filter((m) => m.role === "basement");
  const out: Source[] = [];
  if (rooms.length > 1) {
    out.push({ id: "avg", name: "Basement average", kind: "avg", sub: `All ${rooms.length} room sensors, weighted by room size`, color: "var(--hx-avg)" });
  }
  rooms.forEach((m, i) => {
    const z = zname(m.zone);
    out.push({ id: `s:${m.sensor_id}`, name: m.name, kind: "room", sub: z && z !== m.name ? `Room sensor · ${z}` : "Room sensor",
      color: ROOM_COLORS[i % ROOM_COLORS.length], sensorId: m.sensor_id, zone: m.zone });
  });
  maps.filter((m) => m.role === "outdoor").forEach((m) => out.push({ id: `s:${m.sensor_id}`, name: m.name, kind: "outdoor", sub: "Outdoor sensor", color: "var(--hx-out)", sensorId: m.sensor_id }));
  maps.filter((m) => m.role === "surface").forEach((m) => {
    const z = zname(m.zone);
    out.push({ id: `s:${m.sensor_id}`, name: m.name, kind: "probe", sub: z ? `Concrete probe · ${z}` : "Concrete probe", color: "var(--hx-probe)", sensorId: m.sensor_id, zone: m.zone });
  });
  maps.filter((m) => m.role === "upstairs").forEach((m) => out.push({ id: `s:${m.sensor_id}`, name: m.name, kind: "upstairs", sub: "Upstairs sensor", color: "var(--hx-up)", sensorId: m.sensor_id }));
  return out;
}

export function plugsFrom(cfg: Json): PlugDev[] {
  const out: PlugDev[] = [];
  const fans: Json[] = ((cfg && cfg.fans) || []).filter((f: Json) => f.enabled !== false && f.switch_id);
  fans.forEach((f) => out.push({ id: `f:${f.id}`, name: f.name, sub: `Exhaust fan · plug ${f.switch_id}`, switchId: f.switch_id, kind: "exhaust", group: f.group }));
  ((cfg && cfg.circulators) || []).filter((c: Json) => c.enabled !== false && c.switch_id).forEach((c: Json) =>
    out.push({ id: `c:${c.id}`, name: c.name, sub: `Circulation fan · plug ${c.switch_id}`, switchId: c.switch_id, kind: "circ", circId: c.id }));
  return out;
}

/** One source's reading in one raw history row (°C and %). */
export function readingOf(s: Source, r: Json): Reading {
  if (s.kind === "avg") {
    const t = r.avg_t_c, rh = r.avg_rh;
    return { t: t == null ? null : t, rh: rh == null ? null : rh, td: r.avg_td_c != null ? r.avg_td_c : dewC(t, rh), ok: t != null || rh != null ? true : null };
  }
  const dev = r.devices && r.devices.sensors ? r.devices.sensors[s.sensorId || ""] : undefined;
  if (s.kind === "probe") {
    const sf = (r.surfaces || {})[s.sensorId || ""];
    const t = dev ? dev.t_c : sf ? sf.t_c : null;
    const srh = sf && sf.srh != null ? Math.min(100, sf.srh * 100) : null;
    return { t: t == null ? null : t, rh: srh, td: sf && sf.dew_c != null ? sf.dew_c : null, ok: dev ? !!dev.ok : t != null ? true : null };
  }
  if (dev) {
    const t = dev.t_c == null ? null : dev.t_c, rh = dev.rh == null ? null : dev.rh;
    return { t, rh, td: dewC(t, rh), ok: !!dev.ok };
  }
  if (r.devices) return { t: null, rh: null, td: null, ok: false }; // per-device logging on, but this sensor was missing
  // older rows: recorded per zone / role only
  let t: number | null = null, rh: number | null = null;
  if (s.kind === "room") {
    const z = (r.zones || {})[s.zone || ""];
    if (z) { t = z.t_c == null ? null : z.t_c; rh = z.rh == null ? null : z.rh * 100; }
  } else if (s.kind === "outdoor") {
    t = r.outdoor_t_c == null ? null : r.outdoor_t_c; rh = r.outdoor_rh == null ? null : r.outdoor_rh;
  } else if (s.kind === "upstairs") {
    t = r.upstairs_t_c == null ? null : r.upstairs_t_c;
  }
  return { t, rh, td: dewC(t, rh), ok: t != null || rh != null ? true : null };
}

export type PlugState = { on: boolean | null; ok: boolean | null; w: number | null };
export function plugOf(p: PlugDev, r: Json): PlugState {
  const d = r.devices && r.devices.plugs ? r.devices.plugs[p.switchId] : undefined;
  if (d) return { on: d.on == null ? null : !!d.on, ok: !!d.ok, w: d.w == null ? null : d.w };
  if (p.kind === "exhaust") {
    const g = (r.groups || {})[p.group || ""];
    return { on: g ? !!g.on : r.fans_on != null ? r.fans_on > 0 : null, ok: null, w: null };
  }
  const c = (r.circ || {})[p.circId || ""];
  return { on: c ? !!c.on : null, ok: null, w: c && c.power_w != null ? c.power_w : null };
}

/** Chart rows with keys `<metric>_<index>` for every source, in display units. */
export function chartRows(raw: Json[], sources: Source[], u: TempUnit): Pt[] {
  return raw.map((r) => {
    const o: Pt = { x: new Date(r.ts).getTime(), on: r.fans_on > 0 };
    sources.forEach((s, i) => {
      const v = readingOf(s, r);
      o[`t_${i}`] = tempVal(v.t, u);
      o[`td_${i}`] = tempVal(v.td, u);
      o[`rh_${i}`] = v.rh;
    });
    return o;
  });
}

/** Typical spacing between rows, used to cap gaps when adding up time. */
export function stepMs(raw: Json[]): number {
  if (raw.length < 2) return 120000;
  const d: number[] = [];
  for (let i = 1; i < raw.length && i < 400; i++) d.push(new Date(raw[i].ts).getTime() - new Date(raw[i - 1].ts).getTime());
  d.sort((a, b) => a - b);
  return Math.max(60000, d[Math.floor(d.length / 2)]);
}

export type Health = { tone: "good" | "warn" | "bad" | "idle"; label: string; coverage: number | null; lastOk: number | null; missing: number };

/** Did this device keep reporting through the range? */
export function healthOf(oks: (boolean | null)[], xs: number[], lastNowOk?: boolean | null): Health {
  let known = 0, good = 0, lastOk: number | null = null, missing = 0;
  oks.forEach((ok, i) => {
    if (ok == null) return;
    known++;
    if (ok) { good++; lastOk = xs[i]; } else missing++;
  });
  if (!known) return { tone: "idle", label: "No data yet", coverage: null, lastOk: null, missing: 0 };
  const cov = good / known;
  const lastRowOk = oks[oks.length - 1];
  if (lastNowOk === false || (lastNowOk == null && lastRowOk === false)) {
    return { tone: "bad", label: good ? "Stopped reporting" : "Not reporting", coverage: cov, lastOk, missing };
  }
  if (cov >= 0.98) return { tone: "good", label: "Reporting", coverage: cov, lastOk, missing };
  return { tone: "warn", label: "Some gaps", coverage: cov, lastOk, missing };
}

export type Run = { start: number; end: number };
export type PlugSummary = { runs: Run[]; onH: number; starts: number; reach: number | null; avgW: number | null; offline: Run[]; states: PlugState[] };

export function plugSummary(p: PlugDev, raw: Json[]): PlugSummary {
  const cap = stepMs(raw) * 3;
  const states = raw.map((r) => plugOf(p, r));
  const xs = raw.map((r) => new Date(r.ts).getTime());
  const runs: Run[] = [], offline: Run[] = [];
  let onMs = 0, starts = 0, known = 0, reach = 0, wSum = 0, wN = 0;
  let runStart: number | null = null, offStart: number | null = null;
  states.forEach((s, i) => {
    const x = xs[i];
    const next = i + 1 < xs.length ? xs[i + 1] : x;
    const dt = Math.min(next - x, cap);
    if (s.ok != null) { known++; if (s.ok) reach++; }
    if (s.ok === false) { if (offStart == null) offStart = x; } else if (offStart != null) { offline.push({ start: offStart, end: x }); offStart = null; }
    if (s.on) {
      onMs += dt;
      if (runStart == null) { runStart = x; if (i > 0 && states[i - 1].on === false) starts++; }
      if (s.w != null && s.w > 0.5) { wSum += s.w; wN++; }
    } else if (runStart != null) { runs.push({ start: runStart, end: x }); runStart = null; }
  });
  const lastX = xs.length ? xs[xs.length - 1] : 0;
  if (runStart != null) runs.push({ start: runStart, end: lastX });
  if (offStart != null) offline.push({ start: offStart, end: lastX });
  return { runs, onH: onMs / 3.6e6, starts, reach: known ? reach / known : null, avgW: wN ? wSum / wN : null, offline, states };
}

export function durText(ms: number) {
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}
