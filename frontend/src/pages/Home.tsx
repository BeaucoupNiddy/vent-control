/* Home: a friendly, at-a-glance overview. Everything here is also on the detailed
   Dashboard; this page trades detail for plain words, icons and a few quick controls. */
import { ReactNode, useEffect, useState } from "react";
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, Json } from "../api";
import { temp, useUnit } from "../units";
import "./home.css";

/* ---------- icons ---------- */

export function HIcon({ name, size = 20 }: { name: string; size?: number }) {
  const p = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "fan":
      return (<svg {...p}><circle cx="12" cy="12" r="1.8" /><path d="M12 10.2c-.3-3.6.9-6.9 3.6-6.9 2.6 0 2.4 4.6-1.8 6.9M13.8 12c3.6-.3 6.9.9 6.9 3.6 0 2.6-4.6 2.4-6.9-1.8M12 13.8c.3 3.6-.9 6.9-3.6 6.9-2.6 0-2.4-4.6 1.8-6.9M10.2 12c-3.6.3-6.9-.9-6.9-3.6 0-2.6 4.6-2.4 6.9 1.8" /></svg>);
    case "home": return (<svg {...p}><path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /><path d="M10 20v-5h4v5" /></svg>);
    case "sun": return (<svg {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>);
    case "moon": return (<svg {...p}><path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z" /></svg>);
    case "drop": return (<svg {...p}><path d="M12 3s6 7 6 11a6 6 0 01-12 0c0-4 6-11 6-11z" /></svg>);
    case "bucket": return (<svg {...p}><path d="M4 7h16l-2 13H6L4 7z" /><path d="M4 7c0-2 3.6-3 8-3s8 1 8 3" /><path d="M9 12c1 1 5 1 6 0" /></svg>);
    case "clock": return (<svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>);
    case "bolt": return (<svg {...p}><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z" /></svg>);
    case "spark": return (<svg {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z" /><path d="M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7L19 16z" /></svg>);
    case "play": return (<svg {...p}><circle cx="12" cy="12" r="9" /><path d="M10 8.5l5 3.5-5 3.5v-7z" /></svg>);
    case "power": return (<svg {...p}><path d="M12 3v9" /><path d="M6.3 6.3a8 8 0 1011.4 0" /></svg>);
    case "shield": return (<svg {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /><path d="M8.5 12l2.5 2.5 4.5-5" /></svg>);
    case "shield-alert": return (<svg {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /><path d="M12 8v5M12 16h.01" /></svg>);
    case "wind": return (<svg {...p}><path d="M3 8h11a3 3 0 10-3-3" /><path d="M3 12h16a3 3 0 11-3 3" /><path d="M3 16h7" /></svg>);
    case "up": return (<svg {...p}><path d="M12 19V5M6 11l6-6 6 6" /></svg>);
    case "down": return (<svg {...p}><path d="M12 5v14M6 13l6 6 6-6" /></svg>);
    case "flat": return (<svg {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>);
    case "bell": return (<svg {...p}><path d="M6 16V11a6 6 0 0112 0v5l2 2H4l2-2z" /><path d="M10 21h4" /></svg>);
    case "thermo": return (<svg {...p}><path d="M10 14V5a2 2 0 014 0v9a4 4 0 11-4 0z" /><path d="M12 9v7" /></svg>);
    case "wall": return (<svg {...p}><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M3 9h18M3 15h18M9 4v5M15 9v6M9 15v5" /></svg>);
    case "arrow": return (<svg {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>);
    case "check": return (<svg {...p}><path d="M5 12l5 5L20 7" /></svg>);
    default: return null;
  }
}

/* ---------- helpers ---------- */

type Tone = "good" | "ok" | "warn" | "bad" | "idle";

type Targets = { target: number; upper: number; high: number };
const DEFAULT_TARGETS: Targets = { target: 55, upper: 60, high: 70 };

function rhTone(rh: number | null | undefined, t: Targets): Tone {
  if (rh == null) return "idle";
  if (rh < t.target) return "good";
  if (rh < t.upper) return "ok";
  if (rh < t.high) return "warn";
  return "bad";
}

const HEADLINE: Record<Tone, string> = {
  good: "Nice and dry",
  ok: "Comfortable",
  warn: "A bit humid",
  bad: "Too humid",
  idle: "Waiting for readings",
};

const LEVEL_WORD: Record<string, { word: string; tone: Tone }> = {
  acceptable: { word: "Comfortable", tone: "good" },
  observe: { word: "Keep an eye on it", tone: "ok" },
  elevated: { word: "A bit humid", tone: "warn" },
  high: { word: "Humid", tone: "bad" },
  critical: { word: "Very humid", tone: "bad" },
};

const r0 = (v: number | null | undefined) => (v == null || Number.isNaN(v) ? "—" : `${Math.round(v)}%`);

function hourLabel(iso: string) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric" });
}
function isDaytime(iso: string) {
  const h = new Date(iso).getHours();
  return h >= 7 && h < 19;
}
function minutesText(min: number) {
  if (min < 60) return `${Math.round(min)} min`;
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}
function timeUntil(iso: string | null | undefined, nowIso: string) {
  if (!iso) return null;
  const min = (new Date(iso).getTime() - new Date(nowIso).getTime()) / 60000;
  return min > 0 ? minutesText(min) : null;
}
/** Muggy-ness of the air in plain words, from the dew point (°F). */
function feelWord(dpF: number | null | undefined) {
  if (dpF == null) return "—";
  if (dpF < 50) return "Dry";
  if (dpF < 60) return "Pleasant";
  if (dpF < 65) return "A little sticky";
  if (dpF < 70) return "Sticky";
  return "Muggy";
}
const cToF = (c: number | null | undefined) => (c == null ? null : (c * 9) / 5 + 32);

/* ---------- small building blocks ---------- */

function Panel({ icon, title, right, children, className = "" }: { icon: string; title: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`h-panel ${className}`}>
      <header className="h-panel-head">
        <span className="h-panel-icon"><HIcon name={icon} size={18} /></span>
        <h2>{title}</h2>
        <span className="spacer" />
        {right}
      </header>
      {children}
    </section>
  );
}

function Gauge({ value, target, tone }: { value: number | null; target: number; tone: Tone }) {
  // 270° ring, 0–100 % RH
  const R = 54, C = 64, START = 135, SWEEP = 270;
  const pt = (deg: number, r = R) => {
    const a = (deg * Math.PI) / 180;
    return [C + r * Math.cos(a), C + r * Math.sin(a)];
  };
  const arc = (from: number, to: number) => {
    const [x1, y1] = pt(from), [x2, y2] = pt(to);
    return `M ${x1} ${y1} A ${R} ${R} 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
  };
  const v = value == null ? 0 : Math.max(0, Math.min(100, value));
  const end = START + (SWEEP * v) / 100;
  const tDeg = START + (SWEEP * target) / 100;
  const [ti1x, ti1y] = pt(tDeg, R - 11), [ti2x, ti2y] = pt(tDeg, R + 11);
  return (
    <div className={`h-gauge tone-${tone}`} role="img" aria-label={`Basement humidity ${r0(value)}, target ${target}%`}>
      <svg viewBox="0 0 128 128">
        <path d={arc(START, START + SWEEP)} className="g-track" />
        {value != null && v > 0.5 && <path d={arc(START, end)} className="g-value" />}
        <line x1={ti1x} y1={ti1y} x2={ti2x} y2={ti2y} className="g-target" />
      </svg>
      <div className="g-center">
        <span className="g-num">{r0(value)}</span>
        <span className="g-label">humidity</span>
        <span className="g-target-label">goal {target}%</span>
      </div>
    </div>
  );
}

function FanOrb({ on, tone, size = 72 }: { on: boolean; tone: Tone; size?: number }) {
  return (
    <span className={`h-orb ${on ? "spinning" : ""} tone-${tone}`} style={{ width: size, height: size }}>
      <HIcon name="fan" size={Math.round(size * 0.55)} />
    </span>
  );
}

/* ---------- page ---------- */

export default function Home({ status, refresh, openTab }: { status: Json; refresh: () => void; openTab: (t: string) => void }) {
  const u = useUnit();
  const [stats, setStats] = useState<Json>(null);
  const [hist, setHist] = useState<Json[]>([]);
  const [targets, setTargets] = useState<Targets>(DEFAULT_TARGETS);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    api.config().then((c) => {
      const ctl = c?.control;
      if (ctl) setTargets({ target: ctl.target_rh_pct, upper: ctl.upper_rh_pct, high: ctl.high_rh_alert_pct });
    }).catch(() => {});
    const load = () => {
      api.stats("24h").then(setStats).catch(() => {});
      api.history("24h").then((h) => setHist(h?.rows || [])).catch(() => {});
    };
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  if (!status) return <div className="home"><div className="h-panel h-loading"><FanOrb on tone="idle" /> Loading your basement…</div></div>;

  const g = status.groups?.[0];
  const d = g?.decision;
  const m = d?.metrics || {};
  // The decision carries no air readings when it stops early (for example with no outdoor
  // sensor), so fall back to the measured basement average to keep the basement side filled in.
  const ba = status.basement_average;
  const ind = m.indoor ?? (ba?.t_c != null
    ? { ...ba, dew_point_f: ba.dew_point_c != null ? ba.dew_point_c * 9 / 5 + 32 : null }
    : undefined);
  const out = m.outdoor;
  const anyOn = (status.groups || []).some((x: Json) => x.is_on);
  const rh: number | null = ind?.rh_pct ?? null;
  const tone = rhTone(rh, targets);
  const safety = !!d?.safety;
  const advF: number | null = m.dewpoint_advantage_f ?? null;
  const outsideDrier = advF != null && advF > 0;
  const galDay: number | null = d?.effect?.net_removal_gal_day ?? null;
  const mode: string = status.mode;
  const sim = !!status.simulation_mode;
  const manualLeft = timeUntil(g?.manual_until, status.now);
  const multiZone = Object.keys(status.zones || {}).length > 1;
  const avg = multiZone && status.basement_average?.count > 1 ? status.basement_average : null;

  /* plain-words summary of what the system is doing */
  let doing: string;
  if (safety) doing = "The fan is paused for safety. Open the Dashboard tab to see why.";
  else if (sim) doing = d?.target_on ? "Practice mode: the fan would be running now, but no plugs are being switched." : "Practice mode: the fan would be resting now. No plugs are being switched.";
  else if (anyOn && mode === "manual_on") doing = `You turned the fan on${manualLeft ? `. It goes back to automatic in ${manualLeft}` : ""}.`;
  else if (anyOn && outsideDrier) doing = `The fan is pulling in drier outside air${galDay && galDay > 0.05 ? `, removing about ${galDay.toFixed(1)} gallons of water a day at this rate` : ""}.`;
  else if (anyOn) doing = "The fan is running.";
  else if (mode === "off") doing = "The fan is switched off. It will stay off until you choose Automatic.";
  else if (rh != null && rh < targets.target) doing = "The basement is already at its goal, so the fan is resting.";
  else if (advF != null && advF <= 0) doing = "Outside air is more humid than the basement right now, so the fan is resting until it dries out.";
  else doing = "The fan is resting. It turns on by itself when outside air can help.";

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key); setErr("");
    try { await fn(); refresh(); } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(""); }
  }

  /* forecast: hours where outdoor air would be drier than the basement */
  const indDpC: number | null = ind?.dew_point_c ?? null;
  const hours: Json[] = (status.forecast?.usable ? status.forecast.hours || [] : []).slice(0, 12);
  const goodHour = (h: Json) => indDpC != null && h.dew_point_c != null && cToF(indDpC)! - cToF(h.dew_point_c)! > 1;
  const goodCount = hours.filter(goodHour).length;

  /* last 24 h chart rows */
  const rows = hist.map((r) => ({ x: new Date(r.ts).getTime(), rh: r.indoor_rh, avg: r.avg_rh, out: r.outdoor_rh, on: r.fans_on ? 1 : 0 }))
    .filter((r) => r.rh != null);
  const showAvgLine = !!avg && rows.some((r) => r.avg != null);
  const fanHours = stats?.fan_runtime_h;

  const zones: Json[] = Object.values(status.zones || {});
  const surfaces: Json[] = status.surfaces || [];
  const circ = status.circulation;
  const circs: Json[] = circ?.circulators || [];
  const fans: Json[] = (status.groups || []).flatMap((x: Json) => x.fans || []);
  const water = stats?.net_vapor_removed_gal as number | undefined;

  return (
    <div className="home">
      {/* ---------- hero ---------- */}
      <section className={`h-hero tone-${safety ? "bad" : tone}`}>
        <Gauge value={rh} target={targets.target} tone={tone} />
        <div className="h-hero-text">
          <div className="h-eyebrow">
            <HIcon name="home" size={15} /> Your basement {multiZone ? <span className="faint">· most humid area</span> : null}
            {status.data_source === "mock" && <span className="h-chip subtle">demo data</span>}
          </div>
          <h1 className="h-headline">{HEADLINE[tone]}</h1>
          <p className="h-doing">{doing}</p>
          <div className="h-hero-facts">
            <span><HIcon name="thermo" size={16} /> {temp(ind?.t_c, u, 0)}</span>
            <span><HIcon name="drop" size={16} /> feels {feelWord(ind?.dew_point_f).toLowerCase()}</span>
            {avg && <span title="All areas mixed together"><HIcon name="home" size={16} /> whole basement {r0(avg.rh_pct)}</span>}
            {rh != null && <span><HIcon name="spark" size={16} /> goal under {targets.target}%</span>}
          </div>
        </div>
        <div className="h-hero-fan">
          <FanOrb on={anyOn} tone={anyOn ? "good" : "idle"} size={88} />
          <span className="h-fan-word">{sim ? (d?.target_on ? "Would run" : "Would rest") : anyOn ? "Fan running" : "Fan resting"}</span>
          <span className="faint">{mode === "manual_on" ? "Manual" : mode === "off" ? "Switched off" : mode === "schedule" ? "On a schedule" : mode === "simulation" ? "Practice mode" : "Automatic"}</span>
        </div>
      </section>

      {/* ---------- quick controls ---------- */}
      <div className="h-actions" role="group" aria-label="Fan controls">
        <button className={`h-action ${mode === "auto" ? "active" : ""}`} disabled={!!busy} onClick={() => act("auto", () => api.setMode("auto"))}>
          <HIcon name="spark" /><span><strong>Automatic</strong><small>Runs when it helps</small></span>
        </button>
        <button className={`h-action ${mode === "manual_on" ? "active" : ""}`} disabled={!!busy} onClick={() => act("on", () => api.setMode("manual_on", 60))}>
          <HIcon name="play" /><span><strong>Run for 1 hour</strong><small>{mode === "manual_on" && manualLeft ? `${manualLeft} left` : "Then back to automatic"}</small></span>
        </button>
        <button className={`h-action ${mode === "off" ? "active" : ""}`} disabled={!!busy} onClick={() => act("off", () => api.setMode("off"))}>
          <HIcon name="power" /><span><strong>Turn off</strong><small>Stays off until you choose</small></span>
        </button>
      </div>
      {err && <p className="error" style={{ margin: 0 }}>{err}</p>}

      {/* ---------- notices ---------- */}
      {status.alerts?.length > 0 && (
        <Panel icon="bell" title="Things to know" className="h-notices">
          <ul className="h-list">
            {status.alerts.map((a: Json) => (
              <li key={a.id} className={`sev-${a.severity}`}>
                <span className="dot" />
                <span>{a.message.split(/(?<=\.)\s/)[0].replace(/\bRH\b/g, "humidity")}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {/* ---------- inside vs outside ---------- */}
      <Panel icon="wind" title="Inside vs. outside" right={outsideDrier
        ? <span className="h-chip good"><HIcon name="check" size={14} /> Good time to air out</span>
        : advF != null ? <span className="h-chip warn">Outside is more humid</span> : null}>
        <div className="h-compare">
          <div className="h-place">
            <span className="h-place-icon in"><HIcon name="home" size={26} /></span>
            <div>
              <div className="h-place-name">Basement</div>
              <div className="h-place-temp">{temp(ind?.t_c, u, 0)}</div>
              <div className="h-place-sub">{r0(ind?.rh_pct)} humidity · {feelWord(ind?.dew_point_f)}</div>
            </div>
          </div>
          <div className="h-vs" aria-hidden>
            <HIcon name="arrow" size={22} />
            {advF != null && <span>{outsideDrier ? "drier outside" : "wetter outside"}</span>}
          </div>
          <div className="h-place">
            <span className="h-place-icon out"><HIcon name={isDaytime(status.now) ? "sun" : "moon"} size={26} /></span>
            <div>
              <div className="h-place-name">Outside</div>
              <div className="h-place-temp">{temp(out?.t_c, u, 0)}</div>
              <div className="h-place-sub">{r0(out?.rh_pct)} humidity · {feelWord(out?.dew_point_f)}</div>
            </div>
          </div>
        </div>
        <p className="h-hint">
          What matters is how much water the air holds, not just the humidity number. Warm outside air at a low humidity can
          still be wetter than the basement. The app compares the two for you.
        </p>
      </Panel>

      {/* ---------- last 24 hours ---------- */}
      <div className="h-stats">
        <div className="h-stat">
          <span className="h-stat-icon blue"><HIcon name="bucket" size={22} /></span>
          <span className="h-stat-val">{water == null ? "—" : `${Math.abs(water).toFixed(1)} gal`}</span>
          <span className="h-stat-label">{water != null && water < 0 ? "water added (est.)" : "water removed (est.)"}</span>
          {water != null && water > 0.15 && <span className="h-stat-note">≈ {Math.round(water / 0.132)} bottles of water</span>}
        </div>
        <div className="h-stat">
          <span className="h-stat-icon green"><HIcon name="clock" size={22} /></span>
          <span className="h-stat-val">{fanHours == null ? "—" : `${fanHours.toFixed(1)} h`}</span>
          <span className="h-stat-label">fan run time</span>
          {stats?.switch_cycles != null && <span className="h-stat-note">switched {stats.switch_cycles}×</span>}
        </div>
        <div className="h-stat">
          <span className="h-stat-icon amber"><HIcon name="bolt" size={22} /></span>
          <span className="h-stat-val">{stats ? `$${stats.electricity_cost.toFixed(2)}` : "—"}</span>
          <span className="h-stat-label">fan electricity</span>
          {stats && <span className="h-stat-note">{stats.electricity_kwh.toFixed(2)} kWh</span>}
        </div>
        <div className="h-stat">
          <span className="h-stat-icon violet"><HIcon name="sun" size={22} /></span>
          <span className="h-stat-val">{stats?.favorable_pct == null ? "—" : `${Math.round(stats.favorable_pct)}%`}</span>
          <span className="h-stat-label">of the day good for drying</span>
          {stats?.hours_above_upper != null && <span className="h-stat-note">{stats.hours_above_upper.toFixed(0)} h above {targets.upper}%</span>}
        </div>
      </div>
      <p className="faint h-caption">Last 24 hours. Water and drying figures are estimates from the sensors, not measurements.</p>

      <div className="h-two">
        {/* ---------- humidity chart ---------- */}
        <Panel icon="drop" title="Humidity today" right={<span className="faint">last 24 h</span>}>
          {rows.length > 1 ? (
            <div className="h-chart">
              <ResponsiveContainer width="100%" height={190}>
                <AreaChart data={rows} margin={{ top: 8, right: 6, bottom: 0, left: -18 }}>
                  <defs>
                    <linearGradient id="hRhFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--h-blue)" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="var(--h-blue)" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="x" type="number" scale="time" domain={["dataMin", "dataMax"]} tickLine={false}
                    axisLine={false} minTickGap={50} tick={{ fill: "var(--text-3)", fontSize: 11 }}
                    tickFormatter={(x) => new Date(x).toLocaleTimeString(undefined, { hour: "numeric" })} />
                  <YAxis domain={[(lo: number) => Math.max(0, Math.floor(Math.min(lo, targets.target) / 10) * 10 - 5), (hi: number) => Math.min(100, Math.ceil(hi / 10) * 10)]}
                    tickLine={false} axisLine={false} tick={{ fill: "var(--text-3)", fontSize: 11 }} tickFormatter={(v) => `${v}%`} />
                  <ReferenceLine y={targets.target} stroke="var(--h-green)" strokeDasharray="5 4"
                    label={{ value: "goal", position: "insideTopRight", fill: "var(--h-green)", fontSize: 11 }} />
                  <Tooltip cursor={{ stroke: "var(--text-3)" }} content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const r = payload[0].payload;
                    return (
                      <div className="tooltip">
                        <div className="t-head">{new Date(r.x).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}</div>
                        <div className="t-row"><span>{showAvgLine ? "Most humid area" : "Basement"}</span><strong>{r0(r.rh)}</strong></div>
                        {showAvgLine && r.avg != null && <div className="t-row"><span>Whole basement</span><strong>{r0(r.avg)}</strong></div>}
                        {r.out != null && <div className="t-row"><span>Outside</span><strong>{r0(r.out)}</strong></div>}
                        <div className="t-row"><span>Fan</span><strong>{r.on ? "running" : "resting"}</strong></div>
                      </div>
                    );
                  }} />
                  <Area type="monotone" dataKey="rh" stroke="var(--h-blue)" strokeWidth={2.5} fill="url(#hRhFill)" isAnimationActive={false} dot={false} />
                  {showAvgLine && <Area type="monotone" dataKey="avg" stroke="var(--h-violet)" strokeWidth={2} strokeDasharray="5 4" fill="none" isAnimationActive={false} dot={false} connectNulls />}
                </AreaChart>
              </ResponsiveContainer>
              <div className="h-fanstrip" aria-label="When the fan ran">
                {rows.map((r, i) => <span key={i} className={r.on ? "on" : ""} />)}
              </div>
              <div className="h-legend"><span className="sw blue" />{showAvgLine ? "Most humid area" : "Basement humidity"} {showAvgLine && <><span className="sw violet" />Whole basement </>}<span className="sw fan" />Fan running</div>
            </div>
          ) : <p className="faint">Not enough history yet. Check back in an hour.</p>}
        </Panel>

        {/* ---------- forecast ---------- */}
        <Panel icon="clock" title="Next 12 hours" right={hours.length ? <span className={`h-chip ${goodCount ? "good" : "subtle"}`}>{goodCount ? `${goodCount} good drying ${goodCount === 1 ? "hour" : "hours"}` : "No good drying hours"}</span> : null}>
          {hours.length ? (
            <>
              <div className="h-forecast">
                {hours.map((h) => (
                  <div key={h.ts} className={`h-hour ${goodHour(h) ? "good" : ""}`} title={goodHour(h) ? "Outside air drier than the basement" : "Outside air as wet or wetter"}>
                    <span className="h-hour-time">{hourLabel(h.ts)}</span>
                    <span className="h-hour-icon"><HIcon name={isDaytime(h.ts) ? "sun" : "moon"} size={18} /></span>
                    <span className="h-hour-temp">{temp(h.t_c, u, 0).replace(/°[FC]$/, "°")}</span>
                    <span className="h-hour-dot" />
                  </div>
                ))}
              </div>
              <p className="h-hint"><span className="h-dot-inline" /> Green hours: outside air is expected to be drier than the basement, so running the fan can help.</p>
            </>
          ) : <p className="faint">No weather forecast available right now.</p>}
        </Panel>
      </div>

      {/* ---------- rooms ---------- */}
      {zones.length > 0 && (
        <Panel icon="home" title={zones.length > 1 ? "Around the basement" : "Basement area"}>
          {avg && (
            <div className={`h-room h-room-avg tone-${(LEVEL_WORD[avg.level] || { tone: "ok" }).tone}`}>
              <div className="h-room-top">
                <span className="h-room-drop"><HIcon name="home" size={18} /></span>
                <span className="h-room-name">Whole basement</span>
                <span className="faint">· average of {avg.count} areas</span>
              </div>
              <div className="h-room-temp">{temp(avg.t_c, u, 0)}</div>
              <div>
                <div className="h-room-rh">{r0(avg.rh_pct)}</div>
                <div className="h-room-sub">
                  <span>{(LEVEL_WORD[avg.level] || { word: avg.level }).word} · ranges {r0(avg.rh_min_pct)} to {r0(avg.rh_max_pct)}</span>
                  {(() => {
                    const tr = avg.trend?.rh_pct_h;
                    const dir = tr == null || Math.abs(tr) < 0.3 ? "flat" : tr > 0 ? "up" : "down";
                    return <span className={`h-trend ${dir}`}><HIcon name={dir} size={14} />{dir === "flat" ? "steady" : dir === "down" ? "drying" : "rising"}</span>;
                  })()}
                </div>
              </div>
              <p className="h-hint" style={{ margin: "6px 0 0" }}>
                As if all the basement air were mixed together. The fan still watches the most humid area{avg.partial ? `. Not counted right now: ${avg.missing.join(", ")}` : ""}.
              </p>
            </div>
          )}
          <div className="h-rooms">
            {zones.map((z) => {
              const lv = LEVEL_WORD[z.level] || { word: z.level, tone: "ok" as Tone };
              const tr = z.trend?.rh_pct_h;
              const dir = tr == null || Math.abs(tr) < 0.3 ? "flat" : tr > 0 ? "up" : "down";
              const stale = z.status !== "ok";
              return (
                <div key={z.id} className={`h-room tone-${stale ? "idle" : lv.tone}`}>
                  <div className="h-room-top">
                    <span className="h-room-drop"><HIcon name="drop" size={18} /></span>
                    <span className="h-room-name">{z.name}</span>
                  </div>
                  <div className="h-room-rh">{r0(z.rh_pct)}</div>
                  <div className="h-room-sub">
                    <span>{stale ? "No recent reading" : lv.word}</span>
                    <span className={`h-trend ${dir}`}>
                      <HIcon name={dir} size={14} />{dir === "flat" ? "steady" : dir === "down" ? "drying" : "rising"}
                    </span>
                  </div>
                  <div className="h-room-temp">{temp(z.t_c, u, 0)}</div>
                </div>
              );
            })}
          </div>
        </Panel>
      )}

      <div className="h-two">
        {/* ---------- walls ---------- */}
        <Panel icon="wall" title="Walls & condensation">
          {surfaces.length === 0 ? (
            <div className="h-safe">
              <span className="h-safe-icon good"><HIcon name="shield" size={26} /></span>
              <div>
                <strong>{(m.condensation_margin_f ?? 99) > 3 ? "Outside air won't condense" : "Close to condensing"}</strong>
                <p className="h-hint">No wall sensor is connected, so this uses the coldest-surface estimate.</p>
              </div>
            </div>
          ) : surfaces.map((s) => {
            const safe = s.risk === "lower";
            const headroom = s.conservative_margin_f;
            return (
              <div key={s.sensor_id} className="h-safe">
                <span className={`h-safe-icon ${s.status !== "ok" ? "idle" : safe ? "good" : "warn"}`}><HIcon name={safe ? "shield" : "shield-alert"} size={26} /></span>
                <div>
                  <strong>{s.status !== "ok" ? `${s.name}: no recent reading` : safe ? "Walls are staying dry" : s.risk_label}</strong>
                  <div className="h-safe-sub">{s.name}{headroom != null && s.status === "ok" ? ` · ${u === "F" ? headroom.toFixed(0) : (headroom * 5 / 9).toFixed(0)}°${u} warmer than where dew would form` : ""}</div>
                  {s.future?.potential_risk && (
                    <p className="h-hint warn">Heads up: muggier air is expected around {hourLabel(s.future.at)}. The fan avoids bringing it in if it could wet this wall.</p>
                  )}
                </div>
              </div>
            );
          })}
        </Panel>

        {/* ---------- fans ---------- */}
        <Panel icon="fan" title="Your fans" right={circs.length ? <span className="h-chip subtle">{circ.mode === "shadow" ? "Room fans: learning mode" : circ.mode === "auto" ? "Room fans: automatic" : "Room fans: off"}</span> : null}>
          <ul className="h-fans">
            {fans.map((f) => {
              const on = !!f.switch?.confirmed;
              return (
                <li key={f.id}>
                  <FanOrb on={on} tone={on ? "good" : "idle"} size={40} />
                  <div className="h-fan-text">
                    <strong>{f.type === "intake" ? "Fresh-air fan" : "Exhaust fan"}</strong>
                    <span className="faint">{f.name}</span>
                  </div>
                  <span className={`h-chip ${!f.switch?.reachable ? "bad" : on ? "good" : "subtle"}`}>{!f.switch?.reachable ? "Not responding" : on ? "On" : "Off"}</span>
                </li>
              );
            })}
            {circs.map((c) => {
              const wouldRun = c.decision?.target_on;
              return (
                <li key={c.id}>
                  <FanOrb on={!!c.is_on} tone={c.is_on ? "good" : "idle"} size={40} />
                  <div className="h-fan-text">
                    <strong>{c.name}</strong>
                    <span className="faint">{c.location || c.model}{circ.mode === "shadow" ? ` · would ${wouldRun ? "run" : "rest"} now` : ""}</span>
                  </div>
                  <span className={`h-chip ${!c.switch?.reachable ? "bad" : c.is_on ? "good" : "subtle"}`}>{!c.switch?.reachable ? "Not responding" : c.is_on ? "On" : "Off"}</span>
                </li>
              );
            })}
          </ul>
          {circs.length > 0 && circ.mode === "shadow" && (
            <p className="h-hint">Room fans are in learning mode: the app works out when they would help but leaves the plugs alone. Room fans move moisture around; only the exhaust fan removes it.</p>
          )}
        </Panel>
      </div>

      <button className="h-more" onClick={() => openTab("Dashboard")}>
        See every detail on the Dashboard <HIcon name="arrow" size={16} />
      </button>
    </div>
  );
}
