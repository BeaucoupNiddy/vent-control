/* Readings explorer: one large chart that starts with the basement average and lets you switch each
   sensor on or off. The table underneath gives the low / average / high for every line shown. */
import { useEffect, useMemo, useState } from "react";
import { TimeChart } from "../../components";
import { TempUnit } from "../../units";
import { HIcon } from "../home/icons";
import { Seg } from "../home/charts";
import { stats } from "../home/util";
import { Metric, Pt, Source } from "./data";

const METRICS = [["rh", "Humidity"], ["t", "Temperature"], ["td", "Dew point"]] as const;
const KEY = "ventctl.history.explorer";

type Saved = { metric: Metric; on: string[] | null };
function load(): Saved | null {
  try { const v = localStorage.getItem(KEY); return v ? (JSON.parse(v) as Saved) : null; } catch { return null; }
}
function save(v: Saved) {
  try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* storage unavailable */ }
}

/** Which sources can draw a line for this metric. */
function supports(s: Source, m: Metric) {
  if (s.kind === "probe") return m !== "td"; // surface temperature and surface RH; no dew point of its own
  if (s.kind === "upstairs") return m === "t";
  return true;
}

export function Explorer({ sources, rows, u, xf, fan }: {
  sources: Source[]; rows: Pt[]; u: TempUnit; xf: (x: number) => string; fan: [number, number][];
}) {
  const saved = useMemo(load, []);
  const [metric, setMetric] = useState<Metric>(saved ? saved.metric : "rh");
  const [on, setOn] = useState<string[] | null>(saved ? saved.on : null);
  const ids = sources.map((s) => s.id);
  const defaults = ids.indexOf("avg") >= 0 ? ["avg"] : ids.slice(0, 1);
  const active = (on || defaults).filter((id) => ids.indexOf(id) >= 0);
  useEffect(() => { save({ metric, on }); }, [metric, on]);

  const toggle = (id: string) => {
    const cur = active.slice();
    const i = cur.indexOf(id);
    if (i >= 0) cur.splice(i, 1); else cur.push(id);
    setOn(cur);
  };
  const preset = (kinds: string[]) => setOn(sources.filter((s) => kinds.indexOf(s.kind) >= 0).map((s) => s.id));
  const hasAvg = ids.indexOf("avg") >= 0;
  const rooms = sources.filter((s) => s.kind === "room").length;

  const unit = metric === "rh" ? "%" : `°${u}`;
  const idx = (id: string) => ids.indexOf(id);
  const shown = sources.filter((s) => active.indexOf(s.id) >= 0 && supports(s, metric));
  const series = shown.map((s) => ({
    key: `${metric}_${idx(s.id)}`, name: s.kind === "probe" && metric === "rh" ? `${s.name} (surface RH)` : s.name, color: s.color,
    width: s.kind === "avg" ? 3 : 2, dashed: s.kind === "outdoor" || s.kind === "upstairs",
  }));
  const latest = (s: Source, m: Metric) => {
    const k = `${m}_${idx(s.id)}`;
    for (let i = rows.length - 1; i >= 0; i--) { const v = rows[i][k]; if (typeof v === "number") return v; }
    return null;
  };
  const fmt = (v: number | null | undefined, m: Metric = metric) =>
    v == null ? "—" : m === "rh" ? `${Math.round(v)}%` : `${v.toFixed(1)}°`;
  const hiddenHere = sources.filter((s) => active.indexOf(s.id) >= 0 && !supports(s, metric));

  return (
    <section className="h-panel hx-explorer">
      <header className="h-panel-head">
        <span className="h-panel-icon"><HIcon name="chart" size={18} /></span>
        <div className="h-panel-title">
          <h2>Readings</h2>
          <span>Starts with the basement average. Tap any sensor to add or remove its line.</span>
        </div>
        <span className="spacer" />
        <Seg value={metric} options={METRICS} onChange={setMetric} label="Reading" />
      </header>

      <div className="hx-presets" role="group" aria-label="Quick views">
        {hasAvg && <button className="h-pill" onClick={() => preset(["avg"])}>Average only</button>}
        {rooms > 0 && <button className="h-pill" onClick={() => preset(["avg", "room"])}>{hasAvg ? "Average + each room" : "Each room"}</button>}
        <button className="h-pill" onClick={() => preset(["avg", "room", "outdoor"])}>Inside vs outside</button>
        <button className="h-pill" onClick={() => preset(["avg", "room", "outdoor", "probe", "upstairs"])}>Everything</button>
      </div>

      <div className="hx-chips">
        {sources.map((s) => {
          const sel = active.indexOf(s.id) >= 0;
          const ok = supports(s, metric);
          const v = ok ? latest(s, metric) : null;
          return (
            <button key={s.id} className={`hx-chip ${sel ? "on" : ""} ${ok ? "" : "na"} k-${s.kind}`} aria-pressed={sel}
              onClick={() => toggle(s.id)} title={ok ? s.sub : `${s.name} has no ${METRICS.filter((m) => m[0] === metric)[0][1].toLowerCase()} reading`}>
              <span className="hx-sw" style={{ background: s.color }} />
              <span className="hx-chip-text"><strong>{s.name}</strong><small>{ok ? (v == null ? "no data" : `now ${fmt(v)}`) : "n/a here"}</small></span>
              <span className="hx-check">{sel && <HIcon name="check" size={14} />}</span>
            </button>
          );
        })}
      </div>

      {series.length === 0 ? (
        <p className="h-hint">Pick at least one sensor above to draw its line.</p>
      ) : (
        <div className="hx-chart">
          <TimeChart title="" unit={unit} data={rows} xFormat={xf} fanOn={fan} digits={metric === "rh" ? 0 : 1} series={series} height={300} hideLegend />
        </div>
      )}
      <div className="hx-legend-note"><span className="sw area" />Shaded = exhaust fans running</div>
      {hiddenHere.length > 0 && (
        <p className="h-hint">{hiddenHere.map((s) => s.name).join(", ")} {hiddenHere.length === 1 ? "has" : "have"} no {metric === "td" ? "dew point" : "humidity"} reading, so {hiddenHere.length === 1 ? "it isn't" : "they aren't"} drawn here.</p>
      )}

      {series.length > 0 && (
        <div className="table-wrap hx-table">
          <table>
            <thead><tr><th>Sensor</th><th className="num">Now</th><th className="num">Low</th><th className="num">Average</th><th className="num">High</th></tr></thead>
            <tbody>
              {shown.map((s) => {
                const st = stats(rows.map((r) => { const v = r[`${metric}_${idx(s.id)}`]; return typeof v === "number" ? v : null; }));
                return (
                  <tr key={s.id}>
                    <td><span className="hx-sw" style={{ background: s.color }} />{s.name}</td>
                    <td className="num">{fmt(st ? st.last : null)}</td>
                    <td className="num">{fmt(st ? st.min : null)}</td>
                    <td className="num"><strong>{fmt(st ? st.mean : null)}</strong></td>
                    <td className="num">{fmt(st ? st.max : null)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {metric === "rh" && shown.some((s) => s.kind === "avg") && (
        <p className="h-hint">The basement average mixes the moisture in every room by room size, then turns it back into humidity, so it's fair when rooms are at different temperatures.</p>
      )}
    </section>
  );
}
