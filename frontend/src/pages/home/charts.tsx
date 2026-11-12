/* Charts and graphics for the Home page. Hand-drawn SVG for the small pieces (gauge, sparkline,
   donut); Recharts for the interactive time and bar charts. */
import { ReactNode, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Json } from "../../api";
import { TimeChart, fanIntervals } from "../../components";
import { TempUnit, tempVal } from "../../units";
import { Day, Pt, Targets, Tone, bands, dDeg, r0, stats } from "./util";

/* ---------- humidity gauge ---------- */

export function Gauge({ value, t, tone, label = "humidity" }: { value: number | null; t: Targets; tone: Tone; label?: string }) {
  const R = 52, C = 64, START = 135, SWEEP = 270;
  const pt = (pct: number, r = R) => {
    const a = ((START + (SWEEP * pct) / 100) * Math.PI) / 180;
    return [C + r * Math.cos(a), C + r * Math.sin(a)];
  };
  const arc = (from: number, to: number, r = R) => {
    const [x1, y1] = pt(from, r), [x2, y2] = pt(to, r);
    return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${((to - from) * SWEEP) / 100 > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  };
  const v = value == null ? 0 : Math.max(0, Math.min(100, value));
  const segs: [number, number, string][] = [[0, t.target, "good"], [t.target, t.upper, "ok"], [t.upper, t.high, "warn"], [t.high, 100, "bad"]];
  const [dx, dy] = pt(v);
  const [ti1x, ti1y] = pt(t.target, R - 9), [ti2x, ti2y] = pt(t.target, R + 13);
  return (
    <div className={`h-gauge tone-${tone}`} role="img" aria-label={`Basement ${label} ${r0(value)}, goal under ${t.target}%`}>
      <svg viewBox="0 0 128 128">
        {segs.map(([a, b, k]) => b - a > 0.5 && <path key={k} d={arc(a + 0.6, b - 0.6, R + 10)} className={`g-band band-${k}`} />)}
        <path d={arc(0, 100)} className="g-track" />
        {value != null && v > 0.5 && <path d={arc(0, v)} className="g-value" />}
        <line x1={ti1x} y1={ti1y} x2={ti2x} y2={ti2y} className="g-target" />
        {value != null && <circle cx={dx} cy={dy} r={5.5} className="g-dot" />}
      </svg>
      <div className="g-center">
        <span className="g-num">{value == null ? "—" : Math.round(value)}<small>%</small></span>
        <span className="g-label">{label}</span>
        <span className="g-target-label">goal under {t.target}%</span>
      </div>
    </div>
  );
}

/* ---------- sparkline (no axes; decoration for a stat tile) ---------- */

export function Sparkline({ values, tone = "ok", height = 36, refY }: { values: (number | null)[]; tone?: Tone; height?: number; refY?: number }) {
  const v = values.filter((x): x is number => x != null);
  if (v.length < 2) return <div className="h-spark empty" style={{ height }} />;
  const W = 120, H = height, pad = 3;
  let min = Math.min.apply(null, v), max = Math.max.apply(null, v);
  if (refY != null) { min = Math.min(min, refY); max = Math.max(max, refY); }
  if (max - min < 1e-6) { max += 1; min -= 1; }
  const n = values.length;
  const X = (i: number) => (i / (n - 1)) * W;
  const Y = (y: number) => pad + (1 - (y - min) / (max - min)) * (H - pad * 2);
  let d = "", started = false, firstX = 0, lastX = 0;
  values.forEach((y, i) => {
    if (y == null) { started = false; return; }
    if (!started && !d) firstX = X(i);
    d += `${started ? "L" : "M"}${X(i).toFixed(1)} ${Y(y).toFixed(1)} `;
    started = true; lastX = X(i);
  });
  const area = `${d}L${lastX.toFixed(1)} ${H} L${firstX.toFixed(1)} ${H} Z`;
  return (
    <svg className={`h-spark tone-${tone}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height }} aria-hidden>
      {refY != null && <line x1={0} x2={W} y1={Y(refY)} y2={Y(refY)} className="s-ref" vectorEffect="non-scaling-stroke" />}
      {d.indexOf("M") === d.lastIndexOf("M") && <path d={area} className="s-area" />}
      <path d={d} className="s-line" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Tiny bars for the last few days in a stat tile. */
export function MiniBars({ values, tone = "ok", height = 36 }: { values: number[]; tone?: Tone; height?: number }) {
  if (!values.length) return <div className="h-spark empty" style={{ height }} />;
  const max = Math.max.apply(null, values.concat([1e-9]));
  return (
    <div className={`h-minibars tone-${tone}`} style={{ height }} aria-hidden>
      {values.map((x, i) => <span key={i} className={i === values.length - 1 ? "last" : ""} style={{ height: `${Math.max(4, (x / max) * 100)}%` }} />)}
    </div>
  );
}

/** Progress ring for a share (0–1). */
export function Ring({ share, tone, size = 44 }: { share: number | null; tone: Tone; size?: number }) {
  const r = 18, c = 2 * Math.PI * r;
  const s = share == null ? 0 : Math.max(0, Math.min(1, share));
  return (
    <svg className={`h-ring tone-${tone}`} width={size} height={size} viewBox="0 0 44 44" aria-hidden>
      <circle cx="22" cy="22" r={r} className="r-track" />
      {s > 0.005 && <circle cx="22" cy="22" r={r} className="r-value" strokeDasharray={`${(s * c).toFixed(2)} ${c.toFixed(2)}`} transform="rotate(-90 22 22)" />}
    </svg>
  );
}

/* ---------- segmented control used by the chart panels ---------- */

export function Seg<T extends string>({ value, options, onChange, label, small = true }: {
  value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void; label: string; small?: boolean;
}) {
  return (
    <div className={`h-seg ${small ? "small" : ""}`} role="radiogroup" aria-label={label}>
      {options.map(([k, l]) => (
        <button key={k} role="radio" aria-checked={value === k} className={value === k ? "active" : ""} onClick={() => onChange(k)}>{l}</button>
      ))}
    </div>
  );
}

/* ---------- climate chart: one big chart, three metrics ---------- */

type Metric = "rh" | "td" | "t";
const METRICS = [["rh", "Humidity"], ["td", "Dew point"], ["t", "Temperature"]] as const;

export function ClimateChart({ pts, u, t, range, setRange, loading }: {
  pts: Pt[]; u: TempUnit; t: Targets; range: string; setRange: (r: string) => void; loading: boolean;
}) {
  const [metric, setMetric] = useState<Metric>(() => {
    try { return (localStorage.getItem("ventctl.homeMetric") as Metric) || "rh"; } catch { return "rh"; }
  });
  const pick = (m: Metric) => { setMetric(m); try { localStorage.setItem("ventctl.homeMetric", m); } catch { /* storage unavailable */ } };
  const T = `°${u}`;
  const rows = pts.map((p) => ({
    x: p.x, on: p.on,
    in_rh: p.in_rh, avg_rh: p.avg_rh, out_rh: p.out_rh,
    in_td: tempVal(p.in_td, u), avg_td: tempVal(p.avg_td, u), out_td: tempVal(p.out_td, u),
    in_t: tempVal(p.in_t, u), avg_t: tempVal(p.avg_t, u), out_t: tempVal(p.out_t, u),
  }));
  const showAvg = rows.some((r) => r.avg_rh != null && r.in_rh != null && Math.abs(r.avg_rh - r.in_rh) > 0.5);
  const days = rows.length > 1 ? (rows[rows.length - 1].x - rows[0].x) / 864e5 : 1;
  const xf = (x: number) => new Date(x).toLocaleString(undefined, days > 1.5 ? { weekday: "short", hour: "numeric" } : { hour: "numeric", minute: "2-digit" });
  const key = (k: string) => `${k}_${metric}`;
  const series = [
    { key: key("in"), name: showAvg ? "Most humid area" : "Basement", color: "var(--series-1)" },
    ...(showAvg ? [{ key: key("avg"), name: "Whole basement", color: "var(--series-3)", dashed: true }] : []),
    { key: key("out"), name: "Outside", color: "var(--series-2)" },
  ];
  const mainKey = showAvg ? key("avg") : key("in");
  const st = stats(rows.map((r) => (r as Json)[mainKey]));
  const unit = metric === "rh" ? "%" : T;
  const fmt = (v: number) => (metric === "rh" ? `${Math.round(v)}%` : `${v.toFixed(u === "F" ? 0 : 1)}${T}`);
  const lines = metric === "rh"
    ? [{ y: t.upper, label: `${t.upper}% mold-risk line`, tone: "warn" as const }]
    : undefined;
  const band = metric === "rh" ? { y1: 30, y2: t.target, label: `Goal (under ${t.target}%)` } : undefined;
  return (
    <div>
      <div className="h-chart-tools">
        <Seg value={metric} options={METRICS} onChange={pick} label="What to chart" />
        <Seg value={range} options={RANGES} onChange={setRange} label="Time range" />
      </div>
      {loading ? <div className="h-chart-empty">Loading…</div> : rows.length < 2 ? <div className="h-chart-empty">Not enough history yet. Check back in an hour.</div> : (
        <>
          <TimeChart title="" unit={unit} digits={metric === "rh" ? 0 : 1} data={rows} xFormat={xf} fanOn={fanIntervals(rows)}
            height={260} series={series} lines={lines} band={band} yDomain={metric === "rh" ? ["dataMin - 4", "dataMax + 4"] : undefined} />
          {st && (
            <div className="h-chart-stats">
              <span><small>Now</small><strong>{fmt(st.last)}</strong></span>
              <span><small>Average</small><strong>{fmt(st.mean)}</strong></span>
              <span><small>Low</small><strong>{fmt(st.min)}</strong></span>
              <span><small>High</small><strong>{fmt(st.max)}</strong></span>
              <span><small>Change</small><strong>{metric === "rh" ? (Math.round(st.last - st.first) === 0 ? "0%" : `${st.last > st.first ? "+" : "−"}${Math.abs(Math.round(st.last - st.first))}%`) : dDeg(u === "F" ? st.last - st.first : ((st.last - st.first) * 9) / 5, u)}</strong></span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
export const RANGES = [["24h", "24 h"], ["3d", "3 days"], ["7d", "7 days"]] as const;

/* ---------- drying opportunity: dew point gap, past and forecast ---------- */

export function DryingChart({ pts, forecast, basementDpC, u, onF }: {
  pts: Pt[]; forecast: Json[]; basementDpC: number | null; u: TempUnit; onF: number;
}) {
  const conv = (f: number | null) => (f == null ? null : u === "F" ? f : (f * 5) / 9);
  const rows: Json[] = pts.map((p) => ({ x: p.x, on: p.on, gap: conv(p.gapF), fc: null }));
  const nowX = rows.length ? rows[rows.length - 1].x : Date.now();
  if (rows.length && basementDpC != null && forecast.length) {
    rows[rows.length - 1].fc = rows[rows.length - 1].gap;
    forecast.forEach((h) => {
      const x = new Date(h.ts).getTime();
      if (x > nowX && h.dew_point_c != null) rows.push({ x, on: false, gap: null, fc: conv(((basementDpC - h.dew_point_c) * 9) / 5) });
    });
  }
  if (rows.length < 2) return <div className="h-chart-empty">Not enough readings yet.</div>;
  const vals = rows.map((r) => (r.gap != null ? r.gap : r.fc)).filter((v: number | null) => v != null) as number[];
  const th = conv(onF)!;
  const max = Math.max(th + 1, Math.max.apply(null, vals));
  const min = Math.min(-1, Math.min.apply(null, vals));
  // Gradients are laid out over each shape's own bounding box, so split each one where zero falls inside it.
  const split = (vs: number[], withZero: boolean) => {
    if (!vs.length) return 0.5;
    let hi = Math.max.apply(null, vs), lo = Math.min.apply(null, vs);
    if (withZero) { hi = Math.max(hi, 0); lo = Math.min(lo, 0); }
    return hi - lo < 1e-9 ? (hi > 0 ? 1 : 0) : Math.max(0, Math.min(1, hi / (hi - lo)));
  };
  const pastV = rows.filter((r) => r.gap != null).map((r) => r.gap as number);
  const fcV = rows.filter((r) => r.fc != null).map((r) => r.fc as number);
  const grads: [string, number, boolean][] = [
    ["hGapFillP", split(pastV, true), true], ["hGapLineP", split(pastV, false), false],
    ["hGapFillF", split(fcV, true), true], ["hGapLineF", split(fcV, false), false],
  ];
  const fan = fanIntervals(rows.filter((r) => r.gap != null) as { x: number; on: boolean }[]);
  const xf = (x: number) => new Date(x).toLocaleTimeString(undefined, { hour: "numeric" });
  const U = `°${u}`;
  return (
    <ResponsiveContainer width="100%" height={230}>
      <AreaChart data={rows} margin={{ top: 10, right: 8, bottom: 0, left: 0 }}>
        <defs>
          {grads.map(([id, o, fill]) => (
            <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset={0} stopColor="var(--h-green)" stopOpacity={fill ? 0.35 : 1} />
              <stop offset={o} stopColor="var(--h-green)" stopOpacity={fill ? 0.06 : 1} />
              <stop offset={o} stopColor="var(--h-amber)" stopOpacity={fill ? 0.06 : 1} />
              <stop offset={1} stopColor="var(--h-amber)" stopOpacity={fill ? 0.35 : 1} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        {fan.map(([a, b], i) => <ReferenceArea key={i} x1={a} x2={b} fill="var(--fan-shade)" fillOpacity={1} stroke="none" ifOverflow="hidden" />)}
        <XAxis dataKey="x" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={xf} minTickGap={40}
          tick={{ fill: "var(--text-3)", fontSize: 11 }} axisLine={{ stroke: "var(--border)" }} tickLine={false} />
        <YAxis domain={[Math.floor(min), Math.ceil(max)]} width={40} tick={{ fill: "var(--text-3)", fontSize: 11 }} axisLine={false} tickLine={false}
          tickFormatter={(v) => `${v > 0 ? "+" : ""}${Math.round(Number(v))}°`} />
        <ReferenceLine y={0} stroke="var(--text-3)" />
        <ReferenceLine y={th} stroke="var(--h-green)" strokeDasharray="4 3"
          label={{ value: "Fans can start", position: "insideTopLeft", fill: "var(--text-2)", fontSize: 11 }} />
        <ReferenceLine x={nowX} stroke="var(--text-3)" strokeDasharray="2 3" label={{ value: "now", position: "insideTopRight", fill: "var(--text-3)", fontSize: 11 }} />
        <Tooltip cursor={{ stroke: "var(--text-3)", strokeWidth: 1 }} content={({ active, payload, label }) => {
          if (!active || !payload || !payload.length) return null;
          const r = payload[0].payload;
          const v = r.gap != null ? r.gap : r.fc;
          if (v == null) return null;
          return (
            <div className="tooltip">
              <div className="t-head">{new Date(Number(label)).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}{r.gap == null ? " · forecast" : r.on ? " · fans on" : ""}</div>
              <div className="t-row"><span>{v >= 0 ? "Outside drier by" : "Outside wetter by"}</span><strong>{Math.abs(v).toFixed(1)}{U}</strong></div>
            </div>
          );
        }} />
        <Area dataKey="gap" type="monotone" stroke="url(#hGapLineP)" strokeWidth={2} fill="url(#hGapFillP)" baseValue={0}
          isAnimationActive={false} connectNulls={false} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
        <Area dataKey="fc" type="monotone" stroke="url(#hGapLineF)" strokeWidth={2} strokeDasharray="5 4" fill="url(#hGapFillF)" fillOpacity={0.6}
          baseValue={0} isAnimationActive={false} connectNulls={false} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ---------- daily bars ---------- */

export function DailyBars({ days, field, unit, digits, color, title, refY, refLabel }: {
  days: Day[]; field: "fanH" | "gal" | "rh"; unit: string; digits: number; color: string; title: ReactNode; refY?: number; refLabel?: string;
}) {
  const data = days.map((d, i) => ({ label: i === days.length - 1 ? "Today" : d.label, v: d[field], hours: d.hours }));
  const fmt = (v: number | null) => (v == null ? "—" : `${v.toFixed(digits)}${unit}`);
  const total = field === "rh" ? null : days.reduce((s, d) => s + (d[field] || 0), 0);
  return (
    <div className="h-daily">
      <div className="h-daily-head">
        <h3>{title}</h3>
        {total != null && <span className="faint">{fmt(total)} in {days.length} days</span>}
      </div>
      <ResponsiveContainer width="100%" height={150}>
        <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} barCategoryGap="22%">
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "var(--text-3)", fontSize: 11 }} axisLine={{ stroke: "var(--border)" }} tickLine={false} interval={0} />
          <YAxis width={34} tick={{ fill: "var(--text-3)", fontSize: 11 }} axisLine={false} tickLine={false}
            domain={field === "rh" ? [(dmin: number) => Math.max(0, Math.floor(Math.min(dmin, refY || 100) - 5)), "auto"] : [0, "auto"]}
            tickFormatter={(v) => `${Number(v).toFixed(field === "gal" && Number(v) < 2 ? 1 : 0)}`} />
          {refY != null && <ReferenceLine y={refY} stroke="var(--h-green)" strokeDasharray="4 3" ifOverflow="extendDomain"
            label={{ value: refLabel, position: "insideTopRight", fill: "var(--text-2)", fontSize: 11 }} />}
          <Tooltip cursor={{ fill: "var(--surface-2)" }} content={({ active, payload }) => {
            if (!active || !payload || !payload.length) return null;
            const r = payload[0].payload;
            return (
              <div className="tooltip">
                <div className="t-head">{r.label}{r.hours < 23 ? ` · ${r.hours.toFixed(0)} h recorded` : ""}</div>
                <div className="t-row"><span>{title}</span><strong>{fmt(r.v)}</strong></div>
              </div>
            );
          }} />
          <Bar dataKey="v" fill={color} radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ---------- time in each humidity band ---------- */

export function ComfortDonut({ pts, t, rangeLabel }: { pts: Pt[]; t: Targets; rangeLabel: string }) {
  const b = bands(pts, t);
  if (!b.total) return <div className="h-chart-empty">Not enough history yet.</div>;
  const items: [keyof typeof b, string, string][] = [
    ["good", "Dry", `under ${t.target}%`], ["ok", "Okay", `${t.target}–${t.upper}%`],
    ["warn", "Humid", `${t.upper}–${t.high}%`], ["bad", "Very humid", `over ${t.high}%`],
  ];
  const R = 46, C = 2 * Math.PI * R, GAP = 2.5;
  let acc = 0;
  const under = (b.good + b.ok) / b.total;
  const hoursText = (h: number) => (h >= 48 ? `${(h / 24).toFixed(1)} d` : `${h.toFixed(h < 10 ? 1 : 0)} h`);
  return (
    <div className="h-donut-wrap">
      <div className="h-donut">
        <svg viewBox="0 0 120 120" role="img" aria-label={`${Math.round(under * 100)}% of the time under ${t.upper}%`}>
          <circle cx="60" cy="60" r={R} className="d-track" />
          {items.map(([k]) => {
            const share = (b[k] as number) / b.total;
            if (share <= 0.002) return null;
            const len = Math.max(0.5, share * C - (share < 0.999 ? GAP : 0));
            const el = <circle key={k} cx="60" cy="60" r={R} className={`d-seg band-${k}`} strokeDasharray={`${len.toFixed(2)} ${C.toFixed(2)}`}
              strokeDashoffset={(-acc).toFixed(2)} transform="rotate(-90 60 60)" />;
            acc += share * C;
            return el;
          })}
        </svg>
        <div className="d-center"><strong>{Math.round(under * 100)}%</strong><small>under {t.upper}%</small></div>
      </div>
      <ul className="h-donut-legend">
        {items.map(([k, name, range]) => (
          <li key={k}>
            <span className={`sw band-${k}`} />
            <span className="nm">{name}<small>{range}</small></span>
            <strong>{Math.round(((b[k] as number) / b.total) * 100)}%</strong>
            <span className="faint">{hoursText(b[k] as number)}</span>
          </li>
        ))}
      </ul>
      <p className="h-hint" style={{ gridColumn: "1 / -1" }}>Whole-basement humidity over the {rangeLabel}. Mold grows more easily the longer air stays above {t.upper}%.</p>
    </div>
  );
}
