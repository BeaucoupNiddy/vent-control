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

export type Series = { key: string; name: string; color: string; dashed?: boolean };

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

export function TimeChart({
  data, series, unit, height = 220, fanOn, xFormat, zeroLine, yDomain, digits = 1, title, badge,
}: {
  data: Json[]; series: Series[]; unit: string; height?: number; fanOn?: [number, number][];
  xFormat: (x: number) => string; zeroLine?: boolean; yDomain?: [number | string, number | string]; digits?: number;
  title: string; badge?: string;
}) {
  return (
    <div>
      <div className="chart-title">
        <h3>{title}</h3>
        {badge && <span className={`badge ${badge}`}>{badge}</span>}
        <div className="spacer" />
        <div className="legend">
          {series.length > 1 && series.map((s) => (
            <span key={s.key}><span className="sw" style={{ background: s.color }} />{s.name}</span>
          ))}
          {fanOn && <span><span className="sw area" style={{ background: "var(--fan-shade)" }} />Fans on</span>}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          {fanOn?.map(([a, b], i) => (
            <ReferenceArea key={i} x1={a} x2={b} fill="var(--fan-shade)" fillOpacity={1} stroke="none" ifOverflow="hidden" />
          ))}
          <XAxis dataKey="x" type="number" domain={["dataMin", "dataMax"]} tickFormatter={xFormat}
            tick={{ fill: "var(--text-3)", fontSize: 11 }} axisLine={{ stroke: "var(--border)" }} tickLine={false} minTickGap={40} scale="time" />
          <YAxis tick={{ fill: "var(--text-3)", fontSize: 11 }} axisLine={false} tickLine={false} width={44}
            domain={yDomain || ["auto", "auto"]} tickFormatter={(v) => `${Number(v).toFixed(digits >= 2 ? 1 : 0)}`} />
          {zeroLine && <ReferenceLine y={0} stroke="var(--text-3)" strokeDasharray="3 3" />}
          <Tooltip
            cursor={{ stroke: "var(--text-3)", strokeWidth: 1 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload;
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
            <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} dot={false}
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
