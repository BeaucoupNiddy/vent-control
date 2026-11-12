/* Device check: one row per sensor and per fan plug, showing whether it kept reporting through the
   chosen range, with its own charts (sensors) or on/off timeline and runs (plugs) when opened. */
import { useState } from "react";
import { Json } from "../../api";
import { TimeChart } from "../../components";
import { TempUnit } from "../../units";
import { Sparkline } from "../home/charts";
import { HIcon } from "../home/icons";
import { ago, stats } from "../home/util";
import { Health, KIND_LABEL, PlugDev, PlugSummary, Pt, Source, durText, healthOf, plugSummary, readingOf } from "./data";

const ICON: Record<string, string> = { avg: "home", room: "sensor", outdoor: "sun", probe: "probe", upstairs: "sensor" };

function Chip({ h }: { h: Health }) {
  const cls = h.tone === "good" ? "good" : h.tone === "warn" ? "warn" : h.tone === "bad" ? "bad" : "subtle";
  return <span className={`h-chip ${cls}`}>{h.tone === "good" && <HIcon name="check" size={13} />}{h.tone === "bad" && <HIcon name="alert" size={13} />}{h.label}</span>;
}

const covText = (c: number | null) => (c == null ? "—" : c >= 0.995 ? "100%" : `${(c * 100).toFixed(c > 0.9 ? 1 : 0)}%`);

export function DeviceCheck({ sources, plugs, raw, rows, u, xf, fan, status }: {
  sources: Source[]; plugs: PlugDev[]; raw: Json[]; rows: Pt[]; u: TempUnit; xf: (x: number) => string; fan: [number, number][]; status: Json;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const xs = rows.map((r) => r.x);
  const live: Record<string, Json> = {};
  ((status && status.sensors) || []).forEach((s: Json) => { live[s.sensor_id] = s; });
  const sw: Json = (status && status.hardware && status.hardware.switches) || {};
  const sensors = sources.filter((s) => s.kind !== "avg");
  const idx = (id: string) => sources.map((s) => s.id).indexOf(id);

  const sensorRows = sensors.map((s) => {
    const reads = raw.map((r) => readingOf(s, r));
    const ls = s.sensorId ? live[s.sensorId] : undefined;
    const h = healthOf(reads.map((r) => r.ok), xs, ls ? ls.status === "ok" : null);
    return { s, reads, h, ls };
  });
  const plugRows = plugs.map((p) => {
    const sum = plugSummary(p, raw);
    const l = sw[p.switchId];
    const nowOk = l ? l.reachable !== false : null;
    let h: Health;
    if (nowOk === false) h = { tone: "bad", label: "Not responding", coverage: sum.reach, lastOk: null, missing: 0 };
    else if (sum.reach == null) h = { tone: sum.states.some((x) => x.on != null) ? "good" : "idle", label: sum.states.some((x) => x.on != null) ? "Recording" : "No data yet", coverage: null, lastOk: null, missing: 0 };
    else if (sum.reach >= 0.98) h = { tone: "good", label: "Responding", coverage: sum.reach, lastOk: null, missing: 0 };
    else h = { tone: "warn", label: "Dropped out at times", coverage: sum.reach, lastOk: null, missing: 0 };
    return { p, sum, h, l };
  });
  const all = sensorRows.map((x) => x.h).concat(plugRows.map((x) => x.h));
  const bad = all.filter((h) => h.tone === "bad").length, warn = all.filter((h) => h.tone === "warn").length;
  const summary = bad ? `${bad} device${bad === 1 ? " needs" : "s need"} attention` : warn ? `${warn} device${warn === 1 ? " had" : "s had"} gaps in this range` : "Every device reported through this range";
  const t0 = xs.length ? xs[0] : 0, t1 = xs.length ? xs[xs.length - 1] : 1;

  return (
    <section className="h-panel hx-devices">
      <header className="h-panel-head">
        <span className={`h-panel-icon ${bad ? "bad" : warn ? "warn" : "good"}`}><HIcon name={bad || warn ? "alert" : "shield"} size={18} /></span>
        <div className="h-panel-title">
          <h2>Device check</h2>
          <span>{summary}. Tap a device to see its own history.</span>
        </div>
      </header>

      <h3 className="hx-sub">Sensors</h3>
      <ul className="hx-list">
        {sensorRows.map(({ s, reads, h, ls }) => {
          const isOpen = open === s.id;
          const rhVals = rows.map((r) => r[`rh_${idx(s.id)}`] as number | null);
          const tVals = rows.map((r) => r[`t_${idx(s.id)}`] as number | null);
          const useRh = s.kind !== "upstairs" && rhVals.some((v) => v != null);
          const st = stats(useRh ? rhVals : tVals);
          const lastT = stats(tVals), lastRh = stats(rhVals);
          const now = [lastT ? `${lastT.last.toFixed(1)}°${u}` : null, lastRh && s.kind !== "probe" ? `${Math.round(lastRh.last)}%` : null,
            lastRh && s.kind === "probe" ? `surface RH ${Math.round(lastRh.last)}%` : null].filter(Boolean).join(" · ");
          const bits = [ls && ls.status === "ok" ? `updated ${ago(ls.age_s)}` : ls ? ls.message || "no recent reading" : null,
            ls && ls.battery_low ? "battery low" : null].filter(Boolean);
          return (
            <li key={s.id} className={`hx-item tone-${h.tone} ${isOpen ? "open" : ""}`}>
              <button className="hx-row" onClick={() => setOpen(isOpen ? null : s.id)} aria-expanded={isOpen}>
                <span className="eq-icon" style={{ color: s.color }}><HIcon name={ICON[s.kind]} size={18} /></span>
                <span className="hx-name"><strong>{s.name}</strong><small>{s.sub}{bits.length ? ` · ${bits.join(" · ")}` : ""}</small></span>
                <span className="hx-now">{now || "—"}</span>
                <span className="hx-spark"><Sparkline values={useRh ? rhVals : tVals} tone={h.tone === "bad" ? "bad" : "ok"} height={30} /></span>
                <span className="hx-health"><Chip h={h} /><small>{covText(h.coverage)} of readings</small></span>
                <span className={`hx-chev ${isOpen ? "flip" : ""}`}><HIcon name="chevron" size={16} /></span>
              </button>
              {isOpen && (
                <div className="hx-detail">
                  <div className="hx-facts">
                    <span><b>Type</b>{KIND_LABEL[s.kind]}</span>
                    <span><b>Readings in range</b>{reads.filter((r) => r.ok).length} of {reads.filter((r) => r.ok != null).length || reads.length}</span>
                    <span><b>Last good reading</b>{h.lastOk ? new Date(h.lastOk).toLocaleString() : "—"}</span>
                    {st && <span><b>{useRh ? "Humidity range" : "Temperature range"}</b>{useRh ? `${Math.round(st.min)}–${Math.round(st.max)}%` : `${st.min.toFixed(1)}–${st.max.toFixed(1)}°${u}`}</span>}
                    {ls && ls.signal != null && <span><b>Signal</b>{ls.signal} of 4</span>}
                  </div>
                  <div className="hx-detail-charts">
                    {s.kind !== "upstairs" && (
                      <TimeChart title={s.kind === "probe" ? "Surface humidity" : "Humidity"} unit="%" digits={0} data={rows} xFormat={xf} fanOn={fan} height={180}
                        series={[{ key: `rh_${idx(s.id)}`, name: s.name, color: s.color }]} />
                    )}
                    <TimeChart title={s.kind === "probe" ? "Surface temperature vs. air dew point" : "Temperature and dew point"} unit={`°${u}`} data={rows} xFormat={xf} fanOn={fan} height={180}
                      series={[{ key: `t_${idx(s.id)}`, name: s.kind === "probe" ? "Surface" : "Temperature", color: s.color },
                        ...(s.kind === "upstairs" ? [] : [{ key: `td_${idx(s.id)}`, name: s.kind === "probe" ? "Air dew point nearby" : "Dew point", color: "var(--hx-dew)", dashed: true }])]} />
                  </div>
                  {h.missing > 0 && <p className="h-hint warn">{h.missing} recorded reading{h.missing === 1 ? " was" : "s were"} missing for this sensor in this range. Gaps show as breaks in the line.</p>}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {plugRows.length > 0 && <h3 className="hx-sub">Fans and plugs</h3>}
      <ul className="hx-list">
        {plugRows.map(({ p, sum, h, l }) => {
          const isOpen = open === p.id;
          const onNow = l ? !!l.confirmed : sum.states.length ? !!sum.states[sum.states.length - 1].on : false;
          return (
            <li key={p.id} className={`hx-item tone-${h.tone} ${isOpen ? "open" : ""}`}>
              <button className="hx-row" onClick={() => setOpen(isOpen ? null : p.id)} aria-expanded={isOpen}>
                <span className={`eq-icon ${onNow ? "fan-on" : ""}`}><HIcon name={p.kind === "exhaust" ? "fan" : "wind"} size={18} /></span>
                <span className="hx-name"><strong>{p.name}</strong><small>{p.sub}{l && l.power_w > 0.5 ? ` · ${Math.round(l.power_w)} W now` : ""}</small></span>
                <span className="hx-now">{onNow ? "On now" : "Off now"}</span>
                <span className="hx-spark"><Timeline sum={sum} t0={t0} t1={t1} /></span>
                <span className="hx-health"><Chip h={h} /><small>ran {sum.onH < 10 ? sum.onH.toFixed(1) : Math.round(sum.onH)} h · {sum.starts} start{sum.starts === 1 ? "" : "s"}</small></span>
                <span className={`hx-chev ${isOpen ? "flip" : ""}`}><HIcon name="chevron" size={16} /></span>
              </button>
              {isOpen && <PlugDetail sum={sum} t0={t0} t1={t1} xf={xf} />}
            </li>
          );
        })}
      </ul>
      {raw.length > 0 && !raw[raw.length - 1].devices && (
        <p className="h-hint">Readings recorded before this update were saved per room rather than per sensor, so older history may show the room value for each sensor.</p>
      )}
    </section>
  );
}

function Timeline({ sum, t0, t1, big }: { sum: PlugSummary; t0: number; t1: number; big?: boolean }) {
  const span = Math.max(1, t1 - t0);
  const X = (t: number) => ((t - t0) / span) * 1000;
  return (
    <svg className={`hx-timeline ${big ? "big" : ""}`} viewBox="0 0 1000 20" preserveAspectRatio="none" aria-hidden>
      <rect x={0} y={0} width={1000} height={20} className="tl-bg" />
      {sum.offline.map((r, i) => <rect key={`o${i}`} x={X(r.start)} y={0} width={Math.max(2, X(r.end) - X(r.start))} height={20} className="tl-off" />)}
      {sum.runs.map((r, i) => <rect key={i} x={X(r.start)} y={3} width={Math.max(2, X(r.end) - X(r.start))} height={14} rx={2} className="tl-on" />)}
    </svg>
  );
}

function PlugDetail({ sum, t0, t1, xf }: { sum: PlugSummary; t0: number; t1: number; xf: (x: number) => string }) {
  const runs = sum.runs.slice().reverse().slice(0, 8);
  return (
    <div className="hx-detail">
      <div className="hx-facts">
        <span><b>Run time</b>{durText(sum.onH * 3.6e6)}</span>
        <span><b>Times switched on</b>{sum.starts}</span>
        <span><b>Plug answered</b>{covText(sum.reach)}{sum.reach == null ? " (not recorded before this update)" : " of checks"}</span>
        {sum.avgW != null && <span><b>Average power while on</b>{Math.round(sum.avgW)} W</span>}
      </div>
      <div className="hx-tl-wrap">
        <Timeline sum={sum} t0={t0} t1={t1} big />
        <div className="hx-tl-axis"><span>{xf(t0)}</span><span>{xf(t1)}</span></div>
        <div className="hx-tl-key"><span><i className="k-on" />Running</span><span><i className="k-off" />Plug not answering</span></div>
      </div>
      {runs.length > 0 ? (
        <div className="table-wrap"><table>
          <thead><tr><th>Recent runs</th><th className="num">Length</th></tr></thead>
          <tbody>{runs.map((r, i) => (
            <tr key={i}><td>{new Date(r.start).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}{i === 0 && r.end >= t1 ? " (still running)" : ""}</td>
              <td className="num">{durText(r.end - r.start)}</td></tr>
          ))}</tbody>
        </table></div>
      ) : <p className="h-hint">This fan didn't run in this range.</p>}
    </div>
  );
}
