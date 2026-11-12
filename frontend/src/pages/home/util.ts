/* Shared helpers for the Home page: tones, plain-words formatting and history roll-ups.
   Keep to syntax and APIs that older iPhone Safari (iOS 14+) understands: no regex lookbehind,
   no Array.prototype.at / findLast, no Object.hasOwn. */
import { Json } from "../../api";
import { TempUnit } from "../../units";

export type Tone = "good" | "ok" | "warn" | "bad" | "idle";
export type Targets = { target: number; upper: number; high: number };
export const DEFAULT_TARGETS: Targets = { target: 55, upper: 60, high: 70 };

export function rhTone(rh: number | null | undefined, t: Targets): Tone {
  if (rh == null || Number.isNaN(rh)) return "idle";
  if (rh < t.target) return "good";
  if (rh < t.upper) return "ok";
  if (rh < t.high) return "warn";
  return "bad";
}

export const HEADLINE: Record<Tone, string> = {
  good: "Nice and dry",
  ok: "Comfortable",
  warn: "A bit humid",
  bad: "Too humid",
  idle: "Waiting for readings",
};

export const LEVEL_WORD: Record<string, { word: string; tone: Tone }> = {
  acceptable: { word: "Comfortable", tone: "good" },
  observe: { word: "Keep an eye on it", tone: "ok" },
  elevated: { word: "A bit humid", tone: "warn" },
  high: { word: "Humid", tone: "bad" },
  critical: { word: "Very humid", tone: "bad" },
};

export const r0 = (v: number | null | undefined) => (v == null || Number.isNaN(v) ? "—" : `${Math.round(v)}%`);
export const cToF = (c: number | null | undefined) => (c == null ? null : (c * 9) / 5 + 32);

/** A temperature difference given in °F, shown in the chosen unit, e.g. "+3°F" / "−1.5°C". */
export function dDeg(dF: number | null | undefined, u: TempUnit, signed = true, digits?: number) {
  if (dF == null || Number.isNaN(dF)) return "—";
  const v = u === "F" ? dF : (dF * 5) / 9;
  const d = digits != null ? digits : u === "F" ? 0 : 1;
  const s = Math.abs(v).toFixed(d);
  const sign = !signed || Number(s) === 0 ? "" : v > 0 ? "+" : "−";
  return `${sign}${s}°${u}`;
}

export function hourLabel(iso: string | number) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric" });
}
export function isDaytime(iso: string) {
  const h = new Date(iso).getHours();
  return h >= 7 && h < 19;
}
export function minutesText(min: number) {
  if (min < 1) return "under a minute";
  const t = Math.round(min);
  if (t < 60) return `${t} min`;
  const h = Math.floor(t / 60), m = t % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}
export function timeUntil(iso: string | null | undefined, nowIso: string) {
  if (!iso) return null;
  const min = (new Date(iso).getTime() - new Date(nowIso).getTime()) / 60000;
  return min > 0 ? minutesText(min) : null;
}
export function ago(seconds: number | null | undefined) {
  if (seconds == null) return "—";
  const s = Math.max(0, seconds);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** Muggy-ness of the air in plain words, from the dew point (°F). */
export function feelWord(dpF: number | null | undefined) {
  if (dpF == null) return "—";
  if (dpF < 50) return "Dry";
  if (dpF < 60) return "Pleasant";
  if (dpF < 65) return "A little sticky";
  if (dpF < 70) return "Sticky";
  return "Muggy";
}

export function trendDir(perHour: number | null | undefined, eps = 0.3): "up" | "down" | "flat" {
  if (perHour == null || Math.abs(perHour) < eps) return "flat";
  return perHour > 0 ? "up" : "down";
}

/* ---------- history roll-ups ---------- */

/** One history row reduced to what the Home page plots. Temperatures stay in °C. */
export type Pt = {
  x: number; on: boolean;
  rh: number | null;      // whole-basement average when known, else the control (most humid) reading
  in_rh: number | null; avg_rh: number | null; out_rh: number | null;
  in_td: number | null; avg_td: number | null; out_td: number | null;
  in_t: number | null; avg_t: number | null; out_t: number | null;
  gapF: number | null;    // basement dew point minus outside dew point, °F (positive = outside drier)
  removal: number;        // g/h actually removed (0 while fans are off)
  zones: Record<string, number | null>; // zone RH %
};

/** Dew point (°C) from humidity ratio (kg/kg) at sea-level pressure (Magnus form). */
export function dewFromW(w: number) {
  const pw = (w * 101.325) / (0.621945 + w);
  const g = Math.log(pw / 0.61094);
  return (243.04 * g) / (17.625 - g);
}

export function toPts(rows: Json[]): Pt[] {
  return rows.map((r) => {
    // The fans compare outside air with the air they exhaust, so use that when it is recorded.
    const td = r.exhaust_w != null ? dewFromW(r.exhaust_w) : r.avg_td_c != null ? r.avg_td_c : r.indoor_td_c;
    const zones: Record<string, number | null> = {};
    if (r.zones) Object.keys(r.zones).forEach((k) => { const z = r.zones[k]; zones[k] = z && z.rh != null ? z.rh * 100 : null; });
    return {
      x: new Date(r.ts).getTime(), on: r.fans_on > 0,
      rh: r.avg_rh != null ? r.avg_rh : r.indoor_rh,
      in_rh: r.indoor_rh, avg_rh: r.avg_rh ?? null, out_rh: r.outdoor_rh,
      in_td: r.indoor_td_c, avg_td: r.avg_td_c ?? null, out_td: r.outdoor_td_c,
      in_t: r.indoor_t_c, avg_t: r.avg_t_c ?? null, out_t: r.outdoor_t_c,
      gapF: td != null && r.outdoor_td_c != null ? ((td - r.outdoor_td_c) * 9) / 5 : null,
      removal: r.fans_on > 0 && r.removal_g_h != null ? r.removal_g_h : 0,
      zones,
    };
  });
}

/** Time weights: each point covers the gap since the previous one, capped so outages don't count. */
function weights(pts: Pt[]): number[] {
  return pts.map((p, i) => (i === 0 ? 0 : Math.min(p.x - pts[i - 1].x, 15 * 60000) / 3600000));
}

export function since(pts: Pt[], hours: number): Pt[] {
  if (!pts.length) return pts;
  const cut = pts[pts.length - 1].x - hours * 3600000;
  return pts.filter((p) => p.x >= cut);
}

export type Day = { key: string; label: string; fanH: number; gal: number; rh: number | null; hours: number };

/** Per local calendar day: fan hours, water removed (gal) and time-weighted average humidity. */
export function daily(pts: Pt[]): Day[] {
  const w = weights(pts);
  const map: Record<string, Day & { rhW: number; rhSum: number }> = {};
  const order: string[] = [];
  pts.forEach((p, i) => {
    const d = new Date(p.x);
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    if (!map[key]) {
      map[key] = { key, label: d.toLocaleDateString(undefined, { weekday: "short" }), fanH: 0, gal: 0, rh: null, hours: 0, rhW: 0, rhSum: 0 };
      order.push(key);
    }
    const m = map[key], h = w[i];
    m.hours += h;
    if (p.on) m.fanH += h;
    m.gal += (p.removal * h) / 3785.411784;
    if (p.rh != null) { m.rhSum += p.rh * h; m.rhW += h; }
  });
  return order.map((k) => {
    const m = map[k];
    return { key: m.key, label: m.label, fanH: m.fanH, gal: m.gal, hours: m.hours, rh: m.rhW > 0 ? m.rhSum / m.rhW : null };
  });
}

/** Share of time spent in each humidity band. */
export function bands(pts: Pt[], t: Targets) {
  const w = weights(pts);
  const out = { good: 0, ok: 0, warn: 0, bad: 0 };
  let total = 0;
  pts.forEach((p, i) => {
    if (p.rh == null || !w[i]) return;
    const tone = rhTone(p.rh, t);
    if (tone !== "idle") { out[tone] += w[i]; total += w[i]; }
  });
  return { ...out, total };
}

export function stats(vals: (number | null)[]) {
  const v = vals.filter((x): x is number => x != null && !Number.isNaN(x));
  if (!v.length) return null;
  let min = v[0], max = v[0], sum = 0;
  v.forEach((x) => { if (x < min) min = x; if (x > max) max = x; sum += x; });
  return { min, max, mean: sum / v.length, last: v[v.length - 1], first: v[0] };
}
