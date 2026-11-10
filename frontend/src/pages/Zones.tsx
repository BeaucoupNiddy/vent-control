/* Version 3 dashboard sections: zone conditions, concrete condensation monitor, circulation fans. */
import { useEffect, useMemo, useState } from "react";
import { api, Json } from "../api";
import { Card, Icon, TimeChart, fanIntervals } from "../components";
import { deltaTemp, hours, localTime, num, pct, temp, tempVal, useUnit } from "../units";

const LEVEL: Record<string, { label: string; cls: string }> = {
  acceptable: { label: "Normal", cls: "good" },
  observe: { label: "Observe", cls: "" },
  elevated: { label: "Elevated humidity", cls: "warn" },
  high: { label: "High humidity", cls: "warn" },
  critical: { label: "Critical humidity", cls: "crit" },
};

const RISK: Record<string, { label: string; cls: string }> = {
  lower: { label: "Lower immediate risk", cls: "good" },
  monitor: { label: "Monitor", cls: "" },
  elevated: { label: "Elevated risk", cls: "warn" },
  possible: { label: "Condensation possible", cls: "crit" },
};

const GRADIENT_REASON: Record<string, string> = {
  ZONE_MOISTURE_GRADIENT: "real moisture difference (beyond sensor uncertainty)",
  GRADIENT_WITHIN_SENSOR_UNCERTAINTY: "within sensor uncertainty",
  RH_DIFFERENCE_FROM_TEMPERATURE: "RH differs because of temperature, not moisture",
  SENSOR_COMPARISON_ACTIVE: "sensors are in a side-by-side comparison",
  ZONE_SENSOR_UNAVAILABLE: "a zone sensor is unavailable",
};

export const ZONE_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)"];

export function ZonePanel({ status }: { status: Json }) {
  const u = useUnit();
  const zones: Json[] = Object.values(status?.zones || {});
  const sum = status?.zone_summary;
  if (!zones.length) return null;
  const g = sum?.largest_gradient;
  const name = (id: string) => status.zones[id]?.name || id;
  return (
    <Card title="Basement zones" right={<span className="faint">Compared by moisture content, not RH alone</span>}>
      {sum && (
        <p className="explain" style={{ marginTop: 0 }}>
          {sum.wettest_rh ? <>Highest RH: <strong>{name(sum.wettest_rh)}</strong> ({pct(sum.wettest_rh_pct)}). </> : null}
          {sum.wettest_w ? <>Most water vapor: <strong>{name(sum.wettest_w)}</strong> ({num(sum.wettest_w_g_kg, 2)} g/kg). </> : null}
          {sum.cooler_not_wetter && <span className="muted">The highest-RH zone is cooler, not wetter. </span>}
          {g && <>Largest difference: {name(g.a)} vs {name(g.b)}, {num(Math.abs(g.dw_g_kg), 2)} ± {num(g.uncertainty_g_kg, 2)} g/kg
            {" "}({GRADIENT_REASON[g.reason] || g.reason}).</>}
        </p>
      )}
      <BasementAverage status={status} />
      <div className="grid g3">
        {zones.map((z) => {
          const lv = LEVEL[z.level] || LEVEL.acceptable;
          const tr = z.trend || {};
          const mins = z.minutes_above || {};
          return (
            <div key={z.id} className="zone-card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{z.name}</strong>
                {z.status === "unavailable" || z.status === "unconfigured"
                  ? <span className="badge crit">unavailable</span>
                  : z.colocated ? <span className="badge mock">comparison</span>
                    : <span className={`badge ${lv.cls}`}>{lv.label}</span>}
              </div>
              <div className="big-num" style={{ fontSize: 24 }}>{temp(z.t_c, u)} · {pct(z.rh_pct)}</div>
              <dl className="kv">
                <dt>Dew point</dt><dd>{temp(z.dew_point_c, u)}</dd>
                <dt>Humidity ratio</dt><dd>{num(z.w_g_kg, 2)} ± {num(z.w_uncertainty_g_kg, 2)} g/kg</dd>
                <dt>Absolute humidity</dt><dd>{num(z.abs_humidity_g_m3, 1)} g/m³</dd>
                <dt>RH trend</dt><dd>{tr.rh_pct_h == null ? "—" : `${tr.rh_pct_h >= 0 ? "+" : ""}${tr.rh_pct_h.toFixed(1)} %/h`}</dd>
                <dt>Dew-point trend</dt><dd>{tr.dew_point_f_h == null ? "—" : `${deltaTemp(tr.dew_point_f_h, u)}/h`}</dd>
                <dt>Above {status.circulation?.elevated_rh_pct ?? 60}% RH</dt>
                <dd>{mins.elevated == null ? "—" : mins.elevated >= 120 ? `${(mins.elevated / 60).toFixed(1)} h` : `${Math.round(mins.elevated)} min`}</dd>
                <dt>Sensor quality</dt><dd>{z.quality}{z.on_ventilation_path ? " · on vent path" : ""}</dd>
              </dl>
              {z.message && <p className="faint" style={{ margin: "6px 0 0" }}>{z.message}</p>}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

/** Whole-basement average: what you would read if all the basement air were mixed together. */
export function BasementAverage({ status }: { status: Json }) {
  const u = useUnit();
  const a = status?.basement_average;
  if (!a || a.count < 2) return null;
  const lv = LEVEL[a.level] || LEVEL.acceptable;
  const tr = a.trend || {};
  const weights = (a.included || []).map((x: Json) => `${x.name} ${Math.round(x.weight * 100)}%`).join(", ");
  return (
    <div className="zone-card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Whole-basement average</strong>
        <span className={`badge ${lv.cls}`}>{lv.label}</span>
      </div>
      <div className="big-num" style={{ fontSize: 24 }}>{temp(a.t_c, u)} · {pct(a.rh_pct)}</div>
      <dl className="kv">
        <dt>Dew point</dt><dd>{temp(a.dew_point_c, u)}</dd>
        <dt>Humidity ratio</dt><dd>{num(a.w_g_kg, 2)} g/kg</dd>
        <dt>Range across zones</dt><dd>{pct(a.rh_min_pct)} – {pct(a.rh_max_pct)} RH</dd>
        <dt>RH trend</dt><dd>{tr.rh_pct_h == null ? "—" : `${tr.rh_pct_h >= 0 ? "+" : ""}${tr.rh_pct_h.toFixed(1)} %/h`}</dd>
        <dt>Weighting</dt><dd>{a.method === "equal" ? "equal per sensor" : `by zone volume (${weights})`}</dd>
      </dl>
      <p className="faint" style={{ margin: "6px 0 0" }}>
        Averages the water in the air and the temperature, then works out RH, as if all the air were mixed.
        For monitoring only: fan decisions still use the control reading
        {status?.groups?.[0]?.decision?.metrics?.indoor_label ? ` (${status.groups[0].decision.metrics.indoor_label})` : ""}.
        {a.partial && <> Missing: {a.missing.join(", ")}.</>}
      </p>
    </div>
  );
}

export function SurfaceMonitor({ status }: { status: Json }) {
  const u = useUnit();
  const surfaces: Json[] = status?.surfaces || [];
  const [rows, setRows] = useState<Json[]>([]);
  useEffect(() => {
    if (!surfaces.length) return;
    const load = () => api.history("24h").then((h) => setRows(h.rows)).catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [surfaces.length]);
  if (!surfaces.length) return null;
  return (
    <Card title="Concrete condensation monitor"
      right={<span className="faint">Temperature probe only: it cannot detect water</span>}>
      {surfaces.map((s) => <SurfaceRow key={s.sensor_id} s={s} rows={rows} u={u} />)}
    </Card>
  );
}

function SurfaceRow({ s, rows, u }: { s: Json; rows: Json[]; u: "F" | "C" }) {
  const rk = RISK[s.risk || ""];
  const data = useMemo(() => rows.map((r) => {
    const x = (r.surfaces || {})[s.sensor_id];
    return { x: new Date(r.ts).getTime(), surface: tempVal(x?.t_c, u), dew: tempVal(x?.dew_c, u),
      on: (r.fans_on || 0) > 0 };
  }).filter((p) => p.surface != null), [rows, s.sensor_id, u]);
  const tr = s.trend || {};
  const v = s.ventilation || {};
  const fut = s.future || {};
  return (
    <div className="grid g2" style={{ alignItems: "start", marginBottom: 12 }}>
      <div>
        <div className="row">
          <h3 style={{ margin: 0 }}>{s.name}</h3>
          <span className="faint">{s.zone_name}</span>
          {rk ? <span className={`badge ${rk.cls}`}>{rk.label}</span> : <span className="badge crit">probe unavailable</span>}
        </div>
        {s.t_c == null ? <p className="note">{s.message}</p> : (
          <>
            <div className="big-num" style={{ fontSize: 24 }}>Margin {deltaTemp(s.margin_f, u)}</div>
            <p className="faint" style={{ marginTop: 0 }}>
              Surface minus local dew point. Counting ±{num(s.uncertainty_f, 1)}°F sensor uncertainty, the margin
              could be as low as {deltaTemp(s.conservative_margin_f, u)}.
            </p>
            <dl className="kv">
              <dt>Surface</dt><dd>{temp(s.t_c, u)}</dd>
              <dt>Local air</dt><dd>{temp(s.local_t_c, u)} · {pct(s.local_rh_pct)}{s.local_source === "fallback" ? " (wettest zone)" : ""}</dd>
              <dt>Local dew point</dt><dd>{temp(s.local_dew_point_c, u)}</dd>
              <dt>Estimated surface RH</dt><dd>{pct(s.surface_rh_pct)}{s.surface_rh_uncapped_pct > 100 ? " (saturated)" : ""}</dd>
              <dt>Surface vs air</dt><dd>{deltaTemp(s.surface_minus_air_f, u)}</dd>
              <dt>Margin trend</dt><dd>{tr.margin_f_h == null ? "—" : `${deltaTemp(tr.margin_f_h, u)}/h`}</dd>
              <dt>Time below 3°F margin</dt><dd>{s.minutes?.below_elevated != null ? `${Math.round(s.minutes.below_elevated)} min` : "—"}</dd>
            </dl>
            {s.cause && <p className="explain">{s.cause}</p>}
            <ul className="plain">
              <li><strong>Now (measured indoor air):</strong> {rk?.label || "—"}.</li>
              <li><strong>If outdoor air reached it:</strong>{" "}
                {v.outdoor_margin_f == null ? "no outdoor reading" : v.outdoor_air_wetter_than_local === false
                  ? "outdoor air is drier than the air here, so ventilating lowers the dew point at this surface."
                  : v.outdoor_air_would_condense
                  ? `outdoor dew point ${temp(v.outdoor_dew_point_c, u)} is at or above this surface. Ventilation could cause condensation here.`
                  : v.outdoor_air_risky ? `margin would be about ${deltaTemp(v.outdoor_margin_f, u)}. Risky.`
                    : `margin would be about ${deltaTemp(v.outdoor_margin_f, u)}.`}</li>
              <li><strong>Next 12 h (forecast):</strong>{" "}
                {fut.margin_f == null ? "no forecast" : `${fut.potential_risk ? "potential risk" : "no added risk"}; peak outdoor dew point ${temp(fut.max_outdoor_dew_point_c, u)} at ${localTime(fut.at)}.`}</li>
            </ul>
          </>
        )}
      </div>
      <div>
        {data.length > 1 ? (
          <TimeChart title="Surface vs local dew point (24 h)" unit={`°${u}`} data={data}
            series={[{ key: "surface", name: "Surface", color: "var(--series-1)" }, { key: "dew", name: "Local dew point", color: "var(--critical)", dashed: true }]}
            xFormat={(x) => new Date(x).toLocaleTimeString([], { hour: "numeric" })} height={240} />
        ) : <p className="faint">History will appear after a few readings.</p>}
      </div>
    </div>
  );
}

const CIRC_MODES = [
  { id: "off", label: "Off" },
  { id: "shadow", label: "Shadow" },
  { id: "auto", label: "Automatic" },
];

export function CirculationPanel({ status, refresh }: { status: Json; refresh: () => void }) {
  const circ = status?.circulation;
  const [mins, setMins] = useState(30);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  if (!circ?.circulators?.length) return null;
  async function run(f: () => Promise<unknown>) {
    setBusy(true);
    setErr("");
    try { await f(); refresh(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <Card title="Internal circulation fans"
      right={
        <div className="row">
          <div className="seg" role="group" aria-label="Circulation automation">
            {CIRC_MODES.map((m) => (
              <button key={m.id} className={circ.mode === m.id ? "active" : ""} disabled={busy}
                onClick={() => run(() => api.circulationMode(m.id))}>{m.label}</button>
            ))}
          </div>
          <label className="faint row">manual for <input type="number" min={5} max={720} value={mins} style={{ width: 60 }}
            onChange={(e) => setMins(Number(e.target.value))} /> min</label>
        </div>
      }>
      {err && <p className="error">{err}</p>}
      <p className="faint" style={{ marginTop: 0 }}>
        {circ.mode === "shadow" ? "Shadow mode: the controller shows what it would do but never switches these fans. Manual controls still work."
          : circ.mode === "off" ? "Automation off. Manual controls still work."
            : "Automatic: commissioned fans are switched by the controller."}{" "}
        Circulation moves water between zones; it never counts as moisture removal.
      </p>
      <div className="grid g2">
        {circ.circulators.map((c: Json) => {
          const d = c.decision;
          const st = c.state;
          return (
            <div key={c.id} className="zone-card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div className="row">
                  <Icon name="fan" size={18} />
                  <strong>{c.name}</strong>
                  <span className="faint">{c.location}</span>
                </div>
                <span className={`badge ${st === "on" ? "good" : st === "unknown" ? "crit" : ""}`}>
                  {st === "unknown" ? "state unknown" : st.toUpperCase()}
                </span>
              </div>
              <div className="row faint" style={{ marginTop: 4 }}>
                <span>{c.model} · speed {c.physical_speed}</span>
                <span>· plug {c.switch_id || "none"}</span>
                {c.manual != null ? <span className="badge">manual {c.manual ? "ON" : "OFF"} until {localTime(c.manual_until)}</span>
                  : <span className="badge">{d?.commanding ? "automatic" : circ.mode === "auto" && !c.commissioned ? "not commissioned" : circ.mode}</span>}
              </div>
              {d && (
                <p className="explain" style={{ marginBottom: 4 }}>
                  {!d.commanding && c.manual == null
                    ? <><span className="muted">Would {d.target_on ? "run" : "stay off"}: </span>{d.explanation.replace(/^(ON|OFF)( requested)?: /, "")}</>
                    : d.explanation}
                  {d.benefit && d.benefit !== "none" && <span className={`badge ${d.benefit === "beneficial" ? "good" : d.benefit === "adverse" ? "crit" : ""}`} style={{ marginLeft: 6 }}>
                    {d.benefit === "beneficial" ? "expected benefit" : d.benefit === "adverse" ? "may make it worse" : "benefit uncertain"}</span>}
                </p>
              )}
              <dl className="kv">
                <dt>Connections</dt><dd>{c.connections.map((k: Json) => `${status.zones?.[k.zone_a]?.name || k.zone_a} ↔ ${status.zones?.[k.zone_b]?.name || k.zone_b}`).join(", ") || "none"}</dd>
                <dt>Runtime today</dt><dd>{hours(c.runtime?.today_h)}</dd>
                <dt>Runtime this month</dt><dd>{hours(c.runtime?.month_h)}</dd>
                <dt>Energy this month</dt><dd>{num(c.runtime?.month_kwh, 2)} kWh <span className="faint">({c.energy_label})</span></dd>
                <dt>Power now</dt><dd>{c.switch?.power_w != null && st === "on" ? `${c.switch.power_w.toFixed(0)} W` : "—"}</dd>
              </dl>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn" disabled={busy} onClick={() => run(() => api.circulatorManual(c.id, "on", mins))}>On</button>
                <button className="btn" disabled={busy} onClick={() => run(() => api.circulatorManual(c.id, "off", mins))}>Off</button>
                <button className="btn" disabled={busy || c.manual == null} onClick={() => run(() => api.circulatorManual(c.id, "auto"))}>Back to automatic</button>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

/** History page section: zones, gradients, surface margin, fan activity, effectiveness, exposure. */
export function ZoneHistory({ rawRows, range, xf, fan }: { rawRows: Json[]; range: string; xf: (x: number) => string; fan: [number, number][] }) {
  const u = useUnit();
  const [cfg, setCfg] = useState<Json>(null);
  const [eff, setEff] = useState<Json>(null);
  const [zst, setZst] = useState<Json>(null);
  useEffect(() => { api.config().then(setCfg).catch(() => {}); }, []);
  useEffect(() => {
    const r = range === "custom" ? "30d" : range;
    api.circulationEffectiveness(range === "24h" ? "7d" : r).then(setEff).catch(() => {});
    api.zoneStats(r).then(setZst).catch(() => {});
  }, [range]);
  const zones: Json[] = cfg?.zones || [];
  const conns: Json[] = cfg?.connections || [];
  const circs: Json[] = cfg?.circulators || [];
  const probes: Json[] = (cfg?.sensors?.mappings || []).filter((m: Json) => m.role === "surface");
  const zname = (id: string) => zones.find((z) => z.id === id)?.name || id;
  const rows = useMemo(() => rawRows.map((r: Json) => {
    const o: Json = { x: new Date(r.ts).getTime(), on: r.fans_on > 0 };
    const zs = r.zones || {};
    for (const z of zones) {
      const v = zs[z.id];
      if (v && !v.coloc) {
        o[`rh_${z.id}`] = v.rh != null ? v.rh * 100 : null;
        o[`td_${z.id}`] = tempVal(v.td_c, u);
      }
    }
    for (const k of conns) {
      const a = zs[k.zone_a], b = zs[k.zone_b];
      o[`g_${k.id}`] = a?.w != null && b?.w != null ? (b.w - a.w) * 1000 : null;
    }
    const sf = r.surfaces || {};
    for (const p of probes) {
      const s = sf[p.sensor_id];
      o[`m_${p.sensor_id}`] = s?.margin_f != null ? (u === "F" ? s.margin_f : s.margin_f * 5 / 9) : null;
    }
    // fan activity lanes: exterior at 3, circulators at 2, 1, ...
    o.ext = r.fans_on > 0 ? 3.8 : 3;
    circs.forEach((c, i) => { o[`c_${c.id}`] = (r.circ?.[c.id]?.on ? 0.8 : 0) + (2 - i); });
    return o;
  }), [rawRows, zones, conns, probes, circs, u]);
  if (!cfg || zones.length < 2) return null;
  const T = `°${u}`;
  const toU = (f: number) => (u === "F" ? f : f * 5 / 9);
  const ss = cfg.surfaces;
  return (
    <>
      <h2 style={{ margin: "8px 0 0" }}>Zones and circulation</h2>
      <div className="grid g2">
        <Card><TimeChart title="Zone humidity" unit="%" data={rows} xFormat={xf} fanOn={fan} digits={0}
          series={zones.map((z, i) => ({ key: `rh_${z.id}`, name: z.name, color: ZONE_COLORS[i % ZONE_COLORS.length] }))} /></Card>
        <Card><TimeChart title="Zone dew point" unit={T} data={rows} xFormat={xf} fanOn={fan}
          series={zones.map((z, i) => ({ key: `td_${z.id}`, name: z.name, color: ZONE_COLORS[i % ZONE_COLORS.length] }))} /></Card>
        <Card><TimeChart title="Moisture difference between connected zones" unit="g/kg" data={rows} xFormat={xf} fanOn={fan} zeroLine digits={2}
          series={conns.map((k, i) => ({ key: `g_${k.id}`, name: `${zname(k.zone_b)} − ${zname(k.zone_a)}`, color: ZONE_COLORS[(i + 1) % ZONE_COLORS.length] }))} />
          <p className="faint">Humidity-ratio difference. Values within about ±0.5 g/kg are inside uncalibrated sensor uncertainty.</p></Card>
        {probes.length > 0 && (
          <Card><TimeChart title="Concrete condensation margin" unit={T} data={rows} xFormat={xf} fanOn={fan} zeroLine
            yDomain={[Math.floor(Math.min(toU(ss.possible_margin_f) - 1, ...rows.flatMap((r: Json) => probes.map((p) => r[`m_${p.sensor_id}`])).filter((v: Json) => v != null))), "auto"]}
            lines={[{ y: toU(ss.monitor_margin_f), label: "monitor", tone: "warn" }, { y: toU(ss.elevated_margin_f), label: "elevated", tone: "warn" },
              { y: toU(ss.possible_margin_f), label: "condensation possible", tone: "crit" }]}
            series={probes.map((p, i) => ({ key: `m_${p.sensor_id}`, name: p.name, color: ZONE_COLORS[(i + 2) % ZONE_COLORS.length] }))} />
            <p className="faint">Surface temperature minus local dew point. Sensor uncertainty (about ±{num(Math.hypot(ss.probe_uncertainty_f, ss.dew_point_uncertainty_f), 1)}°F) is not subtracted here.</p></Card>
        )}
        <Card><TimeChart title="Fan activity" unit="" data={rows} xFormat={xf} digits={0} yDomain={[0, 4]}
          series={[{ key: "ext", name: "Exterior fans", color: "var(--series-1)" },
            ...circs.map((c, i) => ({ key: `c_${c.id}`, name: c.name, color: ZONE_COLORS[(i + 1) % ZONE_COLORS.length] }))]} />
          <p className="faint">Each lane steps up while that fan runs.</p></Card>
        {zst && (
          <Card title="Condensation exposure and time above thresholds">
            <div className="table-wrap"><table>
              <thead><tr><th>Zone</th><th className="num">Mean RH</th><th className="num">Max RH</th>
                <th className="num">≥ {cfg.circulation.elevated_rh_pct}%</th><th className="num">≥ {cfg.circulation.high_rh_pct}%</th><th className="num">≥ {cfg.circulation.critical_rh_pct}%</th></tr></thead>
              <tbody>{Object.entries(zst.zones).map(([id, z]: [string, Json]) => (
                <tr key={id}><td>{z.name}</td><td className="num">{pct(z.mean_rh)}</td><td className="num">{pct(z.max_rh)}</td>
                  <td className="num">{hours(z.above_elevated_h)}</td><td className="num">{hours(z.above_high_h)}</td><td className="num">{hours(z.above_critical_h)}</td></tr>
              ))}</tbody>
            </table></div>
            {Object.keys(zst.surfaces).length > 0 && <div className="table-wrap"><table>
              <thead><tr><th>Surface</th><th className="num">Margin &lt; {ss.monitor_margin_f}°F</th><th className="num">&lt; {ss.elevated_margin_f}°F</th>
                <th className="num">Possible</th><th className="num">Surface RH ≥ {ss.surface_rh_elevated_pct}%</th><th className="num">Min margin</th></tr></thead>
              <tbody>{Object.entries(zst.surfaces).map(([id, s]: [string, Json]) => (
                <tr key={id}><td>{s.name}</td><td className="num">{hours(s.below_monitor_h)}</td><td className="num">{hours(s.below_elevated_h)}</td>
                  <td className="num">{hours(s.possible_h)}</td><td className="num">{hours(s.surface_rh_elevated_h)}</td>
                  <td className="num">{s.min_margin_f != null ? deltaTemp(s.min_margin_f, u) : "—"}</td></tr>
              ))}</tbody>
            </table></div>}
            <p className="faint">{zst.label}</p>
          </Card>
        )}
      </div>
      {eff && (
        <Card title="Circulation effectiveness" right={<span className="badge modeled">informational</span>}>
          {eff.fans.map((f: Json) => (
            <div key={f.id} style={{ marginBottom: 10 }}>
              <strong>{f.name}</strong> <span className="faint">{f.runs} recorded run(s)</span>
              <ul className="plain">
                {f.connections.map((k: Json) => (
                  <li key={k.connection}>
                    <span className={`badge ${k.verdict === "reduces_gradient" ? "good" : k.verdict === "increases_gradient" ? "crit" : ""}`}>{k.verdict.replace(/_/g, " ")}</span>{" "}
                    {k.text}
                    {k.comparisons.map((c: Json) => (
                      <div key={String(c.exterior_on)} className="faint">
                        Exterior {c.exterior_on ? "on" : "off"}: gradient changes {num(c.on_rate_g_kg_h, 3)} g/kg/h with the fan vs {num(c.off_rate_g_kg_h, 3)} without
                        ({c.on_samples} / {c.off_samples} samples).
                      </div>
                    ))}
                  </li>
                ))}
                {f.surface_note && <li className="note">{f.surface_note}</li>}
              </ul>
            </div>
          ))}
          <p className="faint">{eff.label}</p>
        </Card>
      )}
    </>
  );
}

/** Diagnostics: side-by-side sensor comparison and concrete-probe check. */
export function SensorComparison({ status }: { status: Json }) {
  const u = useUnit();
  const [st, setSt] = useState<Json>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const load = () => api.compareStatus().then(setSt).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, []);
  const air = (status?.sensors || []).filter((s: Json) => s.role === "basement");
  useEffect(() => { if (!sel.length && air.length) setSel(air.map((s: Json) => s.sensor_id)); }, [air.length]); // eslint-disable-line react-hooks/exhaustive-deps
  async function act(action: string, apply = false) {
    setErr("");
    try { await api.compareAction(action, action === "start" ? sel : undefined, apply); await load(); } catch (e) { setErr((e as Error).message); }
  }
  const res = st?.result;
  const probe = (status?.surfaces || [])[0];
  return (
    <Card title="Sensor side-by-side comparison" right={st?.active ? <span className="badge warn">running {num(st.hours, 1)} h</span> : null}>
      <p className="note">Before placing the air sensors in their zones, put them together in one spot for 24–48 hours. The comparison
        finds persistent offsets so that sensor disagreement is not mistaken for a real moisture difference. While it runs, zone
        readings are not used for circulation decisions. Raw readings are always kept; applying offsets records the date.</p>
      {err && <p className="error">{err}</p>}
      {!st?.active ? (
        <>
          <div className="row" style={{ flexWrap: "wrap" }}>
            {air.map((s: Json) => (
              <label key={s.sensor_id} className="row"><input type="checkbox" checked={sel.includes(s.sensor_id)}
                onChange={(e) => setSel(e.target.checked ? [...sel, s.sensor_id] : sel.filter((x) => x !== s.sensor_id))} /> {s.name}</label>
            ))}
            <button className="btn primary" disabled={sel.length < 2} onClick={() => act("start")}>Start comparison</button>
          </div>
          {st?.last?.result?.ok && <p className="faint">Last comparison ended {localTime(st.last.ended_at, true)}; offsets applied to {st.last.applied?.length ? st.last.applied.join(", ") : "no sensors"}.</p>}
        </>
      ) : (
        <>
          {res?.ok ? (
            <div className="table-wrap"><table>
              <thead><tr><th>Sensor</th><th className="num">Temp diff</th><th className="num">RH diff</th><th className="num">Dew-point diff</th>
                <th className="num">RH spread</th><th className="num">Suggested offsets</th><th>Status</th></tr></thead>
              <tbody>{Object.entries(res.sensors).map(([id, r]: [string, Json]) => (
                <tr key={id}><td>{air.find((s: Json) => s.sensor_id === id)?.name || id}</td>
                  <td className="num">{deltaTemp(r.mean_dt_f, u)}</td><td className="num">{num(r.mean_drh_pct, 1)}%</td>
                  <td className="num">{deltaTemp(r.mean_d_dew_point_f, u)}</td><td className="num">{num(r.rh_spread_pct, 1)}%</td>
                  <td className="num">{r.suggested_temp_offset_f >= 0 ? "+" : ""}{r.suggested_temp_offset_f}°F · {r.suggested_rh_offset_pct >= 0 ? "+" : ""}{r.suggested_rh_offset_pct}%</td>
                  <td>{r.disagrees ? <span className="badge warn">disagrees</span> : <span className="badge good">agrees</span>}{!r.stable && <span className="badge">not yet stable</span>}</td></tr>
              ))}</tbody>
            </table></div>
          ) : <p className="faint">{res?.note || "Collecting readings…"}</p>}
          <p className="faint">Compared with {res?.reference || "the median of all sensors"} over {res?.samples ?? 0} readings. Recommended: {st.recommended_hours?.join("–")} hours.</p>
          <div className="row">
            <button className="btn primary" disabled={!res?.ok} onClick={() => act("stop", true)}>Stop and apply stable offsets</button>
            <button className="btn" onClick={() => act("stop", false)}>Stop without changes</button>
          </div>
        </>
      )}
      {probe && (
        <>
          <h3 style={{ margin: "14px 0 6px" }}>Concrete probe check</h3>
          <p className="faint" style={{ marginTop: 0 }}>
            Probe {temp(probe.t_c, u)}, local air {temp(probe.local_t_c, u)} ({deltaTemp(probe.surface_minus_air_f, u)}).
            To verify it, tape a reference thermometer next to the tip for an hour and compare. A probe that reads the concrete
            changes more slowly than the air and usually stays a little colder than the air on an exterior wall. If it tracks the
            air closely, improve the contact and cover the tip with a small piece of insulation.
          </p>
        </>
      )}
    </Card>
  );
}

/** Simulation page: multi-zone circulation strategies. */
export function ZoneSimulation() {
  const u = useUnit();
  const [profiles, setProfiles] = useState<Json[]>([]);
  const [pid, setPid] = useState("nola_front");
  const [hrs, setHrs] = useState(72);
  const [res, setRes] = useState<Json>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState("none");
  useEffect(() => { api.profiles().then(setProfiles).catch(() => {}); }, []);
  async function run() {
    setBusy(true); setErr("");
    try { setRes(await api.simZones({ profile_id: pid, params: { duration_h: hrs } })); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  const zones: Json[] = res?.zones || [];
  const sel = res?.results?.find((r: Json) => r.id === show);
  const series = useMemo(() => (sel?.series || []).map((p: Json) => {
    const o: Json = { x: p.h * 3600e3, on: !!p.ext_on };
    const cv = (f: number | null | undefined) => (f == null ? null : u === "F" ? f : ((f - 32) * 5) / 9);
    zones.forEach((z) => { o[`rh_${z.id}`] = p[`rh_${z.id}`]; o[`td_${z.id}`] = cv(p[`td_${z.id}`]); });
    o.surface = cv(p.surface_f);
    return o;
  }), [sel, zones, u]);
  const fan = fanIntervals(series);
  const hx = (x: number) => `${Math.round(x / 3600e3)} h`;
  return (
    <Card title="Multi-zone circulation simulation" right={<span className="badge modeled">modeled</span>}>
      <p className="note">Compares the eight internal-circulation strategies with the same exterior ventilation. Water is conserved:
        internal fans only move it between zones. Zone exchange rates, temperature offsets and the concrete response are assumptions
        until measured.</p>
      <div className="row">
        <select value={pid} onChange={(e) => setPid(e.target.value)}>{profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
        <label className="row faint">hours <input type="number" min={6} max={336} value={hrs} style={{ width: 70 }} onChange={(e) => setHrs(Number(e.target.value))} /></label>
        <button className="btn primary" disabled={busy} onClick={run}>{busy ? "Running…" : "Run"}</button>
        {err && <span className="error">{err}</span>}
      </div>
      {res && (<>
        <div className="table-wrap" style={{ marginTop: 12 }}><table>
          <thead><tr><th>Strategy</th>{zones.map((z) => <th key={z.id} className="num">{z.name} mean / max RH</th>)}
            <th className="num">Zone difference</th><th className="num">Surface risk</th><th className="num">Condensation possible</th>
            <th className="num">Circulator run</th><th className="num">kWh</th><th className="num">Removed by ventilation</th><th className="num">Water balance</th></tr></thead>
          <tbody>{res.results.map((r: Json) => {
            const s = r.summary;
            const g = (Object.values(s.mean_gradient_g_kg || {}) as number[]).reduce((a, b) => a + Math.abs(b), 0);
            const ch = (Object.values(s.circulator_hours || {}) as number[]).reduce((a, b) => a + b, 0);
            return (
              <tr key={r.id} onClick={() => setShow(r.id)} style={{ cursor: "pointer", background: show === r.id ? "var(--fan-shade)" : undefined }}>
                <td>{r.name}<div className="faint">{r.description}</div></td>
                {zones.map((z) => <td key={z.id} className="num">{pct(s.zones[z.id]?.mean_rh, 1)} / {pct(s.zones[z.id]?.max_rh)}</td>)}
                <td className="num">{num(g, 2)} g/kg</td><td className="num">{hours(s.surface_risk_hours)}</td><td className="num">{hours(s.condensation_possible_hours)}</td>
                <td className="num">{hours(ch)}</td><td className="num">{num(s.kwh, 2)}</td>
                <td className="num">{num(s.exterior_removed_gal, 2)} gal</td>
                <td className="num faint">{Math.abs(s.water_balance_error_kg) < 1e-6 ? "conserved" : `${s.water_balance_error_kg.toExponential(1)} kg`}</td>
              </tr>);
          })}</tbody>
        </table></div>
        {res.insights?.length > 0 && <ul>{res.insights.map((x: string) => <li key={x}>{x}</li>)}</ul>}
        <p className="faint">{res.label} Click a row to chart it.</p>
        {sel && (
          <div className="grid g2">
            <TimeChart title={`${sel.name}: zone RH`} unit="%" data={series} digits={0} fanOn={fan} xFormat={hx}
              series={zones.map((z, i) => ({ key: `rh_${z.id}`, name: z.name, color: ZONE_COLORS[i % ZONE_COLORS.length] }))} />
            <TimeChart title={`${sel.name}: dew points and surface`} unit={`°${u}`} data={series} fanOn={fan} xFormat={hx}
              series={[...zones.map((z, i) => ({ key: `td_${z.id}`, name: `${z.name} dew pt`, color: ZONE_COLORS[i % ZONE_COLORS.length] })),
                { key: "surface", name: "Concrete surface", color: "var(--text-3)", dashed: true }]} />
          </div>
        )}
      </>)}
    </Card>
  );
}
