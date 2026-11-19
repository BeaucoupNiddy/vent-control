import { ReactNode } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  const p = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  switch (name) {
    case "fan":
      return (<svg {...p}><circle cx="12" cy="12" r="2" /><path d="M12 10c0-4 1-7 4-7s2 5-2 7M14 12c4 0 7 1 7 4s-5 2-7-2M12 14c0 4-1 7-4 7s-2-5 2-7M10 12c-4 0-7-1-7-4s5-2 7 2" /></svg>);
    case "check": return (<svg {...p}><path d="M5 12l5 5L20 7" /></svg>);
    case "x": return (<svg {...p}><path d="M6 6l12 12M18 6L6 18" /></svg>);
    case "dash": return (<svg {...p}><path d="M6 12h12" /></svg>);
    case "alert": return (<svg {...p}><path d="M12 3l10 18H2L12 3z" /><path d="M12 10v5M12 18h.01" /></svg>);
    case "shield": return (<svg {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /></svg>);
    case "info": return (<svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>);
    case "drop": return (<svg {...p}><path d="M12 3s6 7 6 11a6 6 0 01-12 0c0-4 6-11 6-11z" /></svg>);
    default: return null;
  }
}

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && <div className="card-head"><h2>{title}</h2>{right}</div>}
      {children}
    </section>
  );
}

export function Stat({ label, value, badge, tone }: { label: string; value: ReactNode; badge?: string; tone?: "pos" | "neg" }) {
  return (
    <div className="stat">
      <span className="label">{label} {badge && <span className={`badge ${badge === "modeled" ? "modeled" : badge === "mock" ? "mock" : ""}`}>{badge}</span>}</span>
      <span className={`value ${tone || ""}`}>{value}</span>
    </div>
  );
}

export type Series = { key: string; name: string; color: string; dashed?: boolean; width?: number };

/** Shaded fan-on intervals from rows with a boolean-ish `on` field. */
export function fanIntervals(rows: { x: number; on: boolean }[]): [number, number][] {
  const out: [number, number][] = [];
  let start: number | null = null;
  rows.forEach((r, i) => {
    if (r.on && start === null) start = r.x;
    if ((!r.on || i === rows.length - 1) && start !== null) {
      out.push([start, r.x]);
      start = null;
    }
  });
  return out;
}

/** Spans with no readings: a jump in time between rows, or a run of rows where every series is blank
    (e.g. the computer was off or offline). Short blips under `minMs` are ignored. */
export function dataGaps(data: Json[], keys: string[], minMs = 20 * 60000): [number, number][] {
  const has = (r: Json) => keys.some((k) => r[k] !== null && r[k] !== undefined);
  const out: [number, number][] = [];
  let lastGood: number | null = null;
  for (const r of data) {
    if (!has(r)) continue;
    if (lastGood !== null && r.x - lastGood >= minMs) out.push([lastGood, r.x]);
    lastGood = r.x;
  }
  // blank stretch at the end (still offline now)
  const end = data.length ? data[data.length - 1].x : null;
  if (lastGood !== null && end !== null && end - lastGood >= minMs) out.push([lastGood, end]);
  return out;
}

/** Copy of `data` with a blank row inside each gap so lines break there instead of bridging it. */
function breakAt(data: Json[], gaps: [number, number][]): Json[] {
  if (!gaps.length) return data;
  const out = data.slice();
  gaps.forEach(([a, b]) => out.push({ x: (a + b) / 2, _gap: true }));
  return out.sort((p, q) => p.x - q.x);
}

/** Evenly spaced, round-hour ticks in local time: every few hours for a day, noon/midnight for a few days. */
export function niceTicks(x0: number, x1: number): { ticks: number[]; fmt: (x: number) => string } {
  const H = 3600000;
  const span = x1 - x0;
  const step = span <= 1.5 * 24 * H ? 4 * H : span <= 4 * 24 * H ? 12 * H : 24 * H;
  const d = new Date(x0);
  d.setMinutes(0, 0, 0);
  const stepH = step / H;
  // align to local midnight-based multiples of the step (DST-safe enough for labels)
  while (d.getHours() % Math.min(stepH, 24) !== 0 || d.getTime() < x0) d.setHours(d.getHours() + 1);
  const ticks: number[] = [];
  for (let t = new Date(d); t.getTime() <= x1; t.setHours(t.getHours() + stepH)) ticks.push(t.getTime());
  const fmt = (x: number) => {
    const t = new Date(x);
    if (step < 12 * H) return t.toLocaleTimeString(undefined, { hour: "numeric" });
    if (t.getHours() === 0) return `${t.toLocaleDateString(undefined, { weekday: "short" })} ${t.getDate()}`;
    return "noon";
  };
  return { ticks, fmt };
}

export function TimeChart({
  data: rawData, series, unit, height = 220, fanOn, xFormat, zeroLine, yDomain, digits = 1, title, badge, band, lines, nowX, hideLegend,
}: {
  data: Json[]; series: Series[]; unit: string; height?: number; fanOn?: [number, number][];
  xFormat: (x: number) => string; zeroLine?: boolean; yDomain?: [number | string, number | string]; digits?: number;
  title: string; badge?: string;
  band?: { y1: number; y2: number; label: string };
  lines?: { y: number; label: string; tone?: "crit" | "warn" }[];
  nowX?: number; hideLegend?: boolean;
}) {
  const gaps = dataGaps(rawData, series.map((s) => s.key));
  const data = breakAt(rawData, gaps);
  const x0 = data.length ? data[0].x : 0;
  const x1 = data.length ? data[data.length - 1].x : 0;
  const { ticks, fmt: tickFmt } = niceTicks(x0, x1);
  // fan state is unknown while there is no data, so cut the fan bands at each gap
  const fanBands = fanOn?.flatMap(([a, b]) => {
    let parts: [number, number][] = [[a, b]];
    gaps.forEach(([g0, g1]) => {
      parts = parts.flatMap(([p0, p1]) => (g1 <= p0 || g0 >= p1 ? [[p0, p1]] : [[p0, g0], [g1, p1]].filter(([u, v]) => v > u) as [number, number][]));
    });
    return parts;
  });
  const fmtGap = (ms: number) => (ms >= 2 * 3600000 ? `${Math.round(ms / 3600000)} h` : `${Math.round(ms / 60000)} min`);
  return (
    <div>
      <div className="chart-title">
        <h3>{title}</h3>
        {badge && <span className={`badge ${badge}`}>{badge}</span>}
        <div className="spacer" />
        <div className="legend">
          {!hideLegend && series.length > 1 && series.map((s) => (
            <span key={s.key}><span className="sw" style={{ background: s.color }} />{s.name}</span>
          ))}
          {fanOn && <span><span className="sw area" style={{ background: "var(--fan-shade)" }} />Fans on</span>}
          {band && <span><span className="sw area" style={{ background: "var(--band-shade)" }} />{band.label}</span>}
          {gaps.length > 0 && <span><span className="sw area sw-gap" />No data</span>}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: nowX !== undefined ? 18 : 6, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <pattern id="tcGapHatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="6" height="6" fill="var(--gap-shade, rgba(128,128,128,0.08))" />
              <line x1="0" y1="0" x2="0" y2="6" stroke="var(--text-3)" strokeOpacity="0.25" strokeWidth="2" />
            </pattern>
          </defs>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          {gaps.map(([a, b], i) => (
            <ReferenceArea key={`g${i}`} x1={a} x2={b} fill="url(#tcGapHatch)" fillOpacity={1} stroke="none" ifOverflow="hidden"
              label={{ value: b - a >= (x1 - x0) * 0.1 ? `No data · ${fmtGap(b - a)}` : b - a >= (x1 - x0) * 0.025 ? fmtGap(b - a) : "", position: "insideTop", fill: "var(--text-3)", fontSize: 11 }} />
          ))}
          {fanBands?.map(([a, b], i) => (
            <ReferenceArea key={i} x1={a} x2={b} fill="var(--fan-shade)" fillOpacity={1} stroke="none" ifOverflow="hidden" />
          ))}
          <XAxis dataKey="x" type="number" domain={["dataMin", "dataMax"]} tickFormatter={ticks.length >= 2 ? tickFmt : xFormat}
            ticks={ticks.length >= 2 ? ticks : undefined} interval={ticks.length >= 2 ? "preserveStartEnd" : undefined}
            tick={{ fill: "var(--text-3)", fontSize: 11 }} axisLine={{ stroke: "var(--border)" }} tickLine={false} minTickGap={40} scale="time" />
          <YAxis tick={{ fill: "var(--text-3)", fontSize: 11 }} axisLine={false} tickLine={false} width={44}
            domain={yDomain || ["auto", "auto"]} tickFormatter={(v) => `${Number(v).toFixed(digits >= 2 ? 1 : 0)}`} />
          {zeroLine && <ReferenceLine y={0} stroke="var(--text-3)" strokeDasharray="3 3" />}
          {band && <ReferenceArea y1={band.y1} y2={band.y2} fill="var(--band-shade)" fillOpacity={1} stroke="none" ifOverflow="hidden" />}
          {lines?.map((l) => (
            <ReferenceLine key={l.label} y={l.y} stroke={l.tone === "crit" ? "var(--critical)" : "var(--warning)"} strokeDasharray="4 3"
              label={{ value: l.label, position: "insideBottomLeft", fill: "var(--text-3)", fontSize: 11 }} ifOverflow="extendDomain" />
          ))}
          {nowX !== undefined && <ReferenceLine x={nowX} stroke="var(--text-3)" ifOverflow="extendDomain" label={(p: { viewBox?: { x: number; y: number } }) => (
            p.viewBox ? <text x={p.viewBox.x - 3} y={p.viewBox.y - 6} textAnchor="end" fill="var(--text-3)" fontSize={11}>now</text> : <g />
          )} />}
          <Tooltip
            cursor={{ stroke: "var(--text-3)", strokeWidth: 1 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload;
              if (row._gap) return null;
              return (
                <div className="tooltip">
                  <div className="t-head">{xFormat(Number(label))}{row.on !== undefined && (row.on ? " · fans on" : " · fans off")}</div>
                  {series.map((s) => row[s.key] !== null && row[s.key] !== undefined && (
                    <div className="t-row" key={s.key}>
                      <span><span className="sw" style={{ background: s.color, display: "inline-block", width: 8, height: 3, marginRight: 6, verticalAlign: "middle" }} />{s.name}</span>
                      <strong>{Number(row[s.key]).toFixed(digits)} {unit}</strong>
                    </div>
                  ))}
                </div>
              );
            }}
          />
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={s.width || 2} dot={false}
              strokeDasharray={s.dashed ? "4 3" : undefined} isAnimationActive={false} connectNulls={false}
              activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any;
