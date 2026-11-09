import { Fragment, useEffect, useMemo, useState } from "react";
import { api, Json } from "../api";
import { Card, Stat, TimeChart, fanIntervals } from "../components";
import { hours, num, pct, tempVal, useUnit } from "../units";
import { POLICY_NAMES } from "./Tamo";

const RANGES = [["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"], ["custom", "Custom"]];

export default function History() {
  const u = useUnit();
  const [range, setRange] = useState("24h");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [data, setData] = useState<Json>(null);
  const [stats, setStats] = useState<Json>(null);
  const [replay, setReplay] = useState<Json>(null);
  const [err, setErr] = useState("");
  const [shadow, setShadow] = useState<Json>(null);
  const [therm, setTherm] = useState<Json>(null);
  const [cfgT, setCfgT] = useState<Json>(null);
  const [fitMsg, setFitMsg] = useState("");
  useEffect(() => {
    api.thermal().then(setTherm).catch(() => {});
    api.config().then((c) => setCfgT(c.temperature)).catch(() => {});
  }, []);

  const custom = range === "custom";
  const iso = (s: string) => (s ? new Date(s).toISOString() : undefined);

  useEffect(() => {
    if (custom && (!start || !end)) return;
    setErr("");
    const s = custom ? iso(start) : undefined, e = custom ? iso(end) : undefined;
    api.history(range, s, e).then(setData).catch((x) => setErr(String(x.message)));
    api.stats(range, s, e).then(setStats).catch(() => {});
    if (!custom) api.shadow(range).then(setShadow).catch(() => {});
    setReplay(null);
  }, [range, start, end, custom]);

  const rows = useMemo(() => (data?.rows || []).map((r: Json) => {
    const zones = r.zones || {};
    const z: Json = {};
    Object.entries(zones).forEach(([k, v]: [string, Json]) => { z[`zone_${k}`] = v.rh * 100; });
    return {
      x: new Date(r.ts).getTime(),
      on: r.fans_on > 0,
      in_t: tempVal(r.indoor_t_c, u), out_t: tempVal(r.outdoor_t_c, u), surf: tempVal(r.surface_t_c, u),
      in_rh: r.indoor_rh, out_rh: r.outdoor_rh,
      in_td: tempVal(r.indoor_td_c, u), out_td: tempVal(r.outdoor_td_c, u),
      dw: r.indoor_w != null && r.outdoor_w != null ? (r.indoor_w - r.outdoor_w) * 1000 : null,
      removal: r.fans_on > 0 ? (r.removal_g_h * 24) / 3785.4 : 0,
      potential: r.potential_removal_g_h != null ? (r.potential_removal_g_h * 24) / 3785.4 : null,
      power: r.power_w,
      util: r.utility_g_h, therm_pen: r.thermal_penalty_g_h != null ? -r.thermal_penalty_g_h : null,
      energy_pen: r.energy_penalty_g_h != null ? -r.energy_penalty_g_h : null,
      fan_dt: r.pred_fan_dt_f != null ? (u === "F" ? r.pred_fan_dt_f : r.pred_fan_dt_f * 5 / 9) : null,
      pipe: tempVal(r.pipe_t_c, u),
      ...z,
    };
  }), [data, u]);
  const zoneKeys = useMemo(() => {
    const s = new Set<string>();
    rows.forEach((r: Json) => Object.keys(r).filter((k) => k.startsWith("zone_")).forEach((k) => s.add(k)));
    return [...s];
  }, [rows]);

  const fan = fanIntervals(rows);
  const spanDays = rows.length > 1 ? (rows[rows.length - 1].x - rows[0].x) / 864e5 : 1;
  const xf = (x: number) => new Date(x).toLocaleString(undefined, spanDays > 1.5
    ? { month: "short", day: "numeric", hour: "numeric" } : { hour: "numeric", minute: "2-digit" });
  const T = `°${u}`;
  const zoneColors = ["var(--series-1)", "var(--series-3)", "var(--series-4)", "var(--series-5)"];
  const src = data?.rows?.[0]?.source;

  const f2u = (f: number) => (u === "F" ? f : ((f - 32) * 5) / 9);
  async function refit() {
    setFitMsg("Fitting…");
    try {
      const r = await api.thermalFit();
      setFitMsg(r.coupling_w_k ? `Fitted ${Math.round(r.coupling_w_k)} W/K from ${r.cycles_used} cycles (${r.confidence} confidence).` : r.note || "No informative cycles.");
      setTherm(await api.thermal());
    } catch (e) { setFitMsg(String((e as Error).message)); }
  }

  async function runReplay() {
    try {
      setReplay(await api.replay(range, custom ? iso(start) : undefined, custom ? iso(end) : undefined));
    } catch (e) {
      setErr(String((e as Error).message));
    }
  }

  return (
    <>
      <Card title="History" right={
        <div className="row">
          {src === "mock" && <span className="badge mock">mock data</span>}
          <div className="seg">
            {RANGES.map(([k, l]) => <button key={k} className={range === k ? "active" : ""} onClick={() => setRange(k)}>{l}</button>)}
          </div>
          {custom && (<>
            <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
            <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
          </>)}
        </div>
      }>
        {err && <p className="error">{err}</p>}
        {stats && (
          <div className="grid g4">
            <Stat label="Fan runtime" value={hours(stats.fan_runtime_h)} />
            <Stat label="Switching cycles" value={stats.switch_cycles} />
            <Stat label="Net vapor moved" badge="modeled" tone={stats.net_vapor_removed_gal >= 0 ? "pos" : "neg"}
              value={stats.net_vapor_removed_gal >= 0 ? `${stats.net_vapor_removed_gal.toFixed(2)} gal removed` : `${(-stats.net_vapor_removed_gal).toFixed(2)} gal added`} />
            <Stat label="Electricity" badge={stats.electricity_label} value={`${stats.electricity_kwh.toFixed(2)} kWh · $${stats.electricity_cost.toFixed(2)}`} />
            <Stat label="Above upper RH" value={hours(stats.hours_above_upper)} />
            <Stat label="Above high RH" value={hours(stats.hours_above_high)} />
            <Stat label="Ventilation favorable" badge="modeled" value={pct(stats.favorable_pct)} />
            <Stat label="Conditioned air lost" badge="modeled" value={`${Math.round(stats.conditioned_air_ft3).toLocaleString()} ft³ · $${stats.conditioned_air_cost.toFixed(2)}`} />
            {stats.dehumidifier && <Stat label="Dehumidifier" value={`${hours(stats.dehumidifier.runtime_h)} · ${stats.dehumidifier.kwh.toFixed(1)} kWh`} />}
            {stats.dehumidifier && <Stat label="Dehumidifier water" badge="modeled" value={`${stats.dehumidifier.water_gal_estimate.toFixed(1)} gal`} />}
          </div>
        )}
      </Card>

      {rows.length === 0 ? <Card><p className="muted">No recorded data in this range.</p></Card> : (
        <div className="grid g2">
          <Card><TimeChart title="Temperature" unit={T} data={rows} xFormat={xf} fanOn={fan} series={[
            { key: "in_t", name: "Basement", color: "var(--series-1)" },
            { key: "out_t", name: "Outdoor", color: "var(--series-2)" },
            { key: "surf", name: "Coldest surface", color: "var(--series-3)", dashed: true },
          ]} /></Card>
          <Card><TimeChart title="Dew point" unit={T} data={rows} xFormat={xf} fanOn={fan} series={[
            { key: "in_td", name: "Basement", color: "var(--series-1)" },
            { key: "out_td", name: "Outdoor", color: "var(--series-2)" },
          ]} /></Card>
          <Card><TimeChart title="Relative humidity" unit="%" data={rows} xFormat={xf} fanOn={fan} digits={0} series={[
            { key: "in_rh", name: "Basement", color: "var(--series-1)" },
            { key: "out_rh", name: "Outdoor", color: "var(--series-2)" },
          ]} /></Card>
          <Card><TimeChart title="Basement humidity by room" unit="%" data={rows} xFormat={xf} fanOn={fan} digits={0}
            series={zoneKeys.map((k, i) => ({ key: k, name: k.replace("zone_", ""), color: zoneColors[i % 4] }))} /></Card>
          <Card><TimeChart title="Humidity-ratio difference (basement − outdoor)" unit="g/kg" data={rows} xFormat={xf} fanOn={fan}
            zeroLine digits={2} series={[{ key: "dw", name: "ΔW", color: "var(--series-1)" }]} />
            <p className="faint">Above zero: outdoor air is drier. Below zero: ventilation would add moisture.</p></Card>
          <Card><TimeChart title="Estimated moisture transfer" badge="modeled" unit="gal/day" data={rows} xFormat={xf} fanOn={fan}
            zeroLine digits={2} series={[
              { key: "removal", name: "While fans ran", color: "var(--series-1)" },
              { key: "potential", name: "If fans ran", color: "var(--series-2)", dashed: true },
            ]} />
            <p className="faint">Positive = removal, negative = moisture addition.</p></Card>
          <Card><TimeChart title="Thermal impact" unit={T} digits={2} data={rows} xFormat={xf} fanOn={fan}
            band={cfgT ? { y1: f2u(cfgT.preferred_min_f), y2: f2u(cfgT.preferred_max_f), label: "Preferred range" } : undefined}
            lines={cfgT ? [{ y: f2u(cfgT.min_allowed_f), label: "Minimum allowed", tone: "crit" }] : undefined}
            series={[
              { key: "in_t", name: "Basement", color: "var(--series-1)" },
              { key: "pipe", name: "Pipe sensor", color: "var(--series-5)" },
            ]} />
            <p className="faint">Shaded band: preferred range from Settings. Fan intervals shaded blue.</p></Card>
          <Card><TimeChart title="Drying vs temperature tradeoff" badge="modeled" unit="g/h" data={rows} xFormat={xf} fanOn={fan} zeroLine digits={0}
            series={[
              { key: "util", name: "Net utility", color: "var(--series-1)" },
              { key: "therm_pen", name: "Thermal penalty", color: "var(--series-2)", dashed: true },
              { key: "energy_pen", name: "Energy penalty", color: "var(--series-4)", dashed: true },
            ]} />
            <p className="faint">TAMO's evaluation at each record, in drying-equivalent grams per hour. Penalties are drawn below zero.</p></Card>
          <Card><TimeChart title="Predicted fan effect on temperature" badge="modeled" unit={T} data={rows} xFormat={xf} fanOn={fan} zeroLine digits={2}
            series={[{ key: "fan_dt", name: "Fan effect at horizon", color: "var(--series-3)" }]} /></Card>
          <Card><TimeChart title="Fan power" badge={src === "mock" ? "mock" : undefined} unit="W" data={rows} xFormat={xf} fanOn={fan} digits={0}
            series={[{ key: "power", name: "Power", color: "var(--series-1)" }]} /></Card>
        </div>
      )}

      <div className="grid g2">
        <Card title="Shadow comparison" right={<span className="badge modeled">recorded decisions</span>}>
          {!shadow || !shadow.hours_compared ? <p className="muted">No shadow decisions recorded in this range.</p> : (
            <dl className="kv">
              <dt>Agreement between controllers</dt><dd>{pct(shadow.agreement_pct, 1)} of {hours(shadow.hours_compared)}</dd>
              <dt>Runtime of the controller in charge</dt><dd>{hours(shadow.runtime_in_control_h)}</dd>
              <dt>Runtime the shadow would have used</dt><dd>{hours(shadow.runtime_shadow_h)}</dd>
              {Object.entries(shadow.hours_by_policy || {}).map(([k, v]) => (
                <Fragment key={k}><dt>Hours commanded by {POLICY_NAMES[k] || k}</dt><dd>{hours(v as number)}</dd></Fragment>
              ))}
            </dl>
          )}
        </Card>
        <Card title="Thermal model" right={<button className="btn" onClick={refit}>Re-fit from fan cycles</button>}>
          {therm && (<>
            <dl className="kv">
              <dt>In use</dt><dd>{therm.params.source} · confidence {therm.params.confidence} · ±{Math.round(therm.params.uncertainty * 100)}%</dd>
              <dt>Air-to-structure coupling</dt><dd>{Math.round(therm.params.coupling_w_k)} W/K</dd>
              <dt>Structure heat capacity</dt><dd>{num(therm.params.structure_heat_capacity_mj_k, 1)} MJ/K</dd>
              <dt>Last fit</dt><dd>{therm.learned?.coupling_w_k
                ? `${Math.round(therm.learned.coupling_w_k)} W/K (${Math.round(therm.learned.low_w_k)}–${Math.round(therm.learned.high_w_k)}), ${therm.learned.cycles_used} cycles, ${therm.learned.confidence}`
                : `not enough data (${therm.learned?.cycles_found ?? 0} cycles found)`}</dd>
            </dl>
            {therm.predictions?.length > 0 && (
              <div className="table-wrap"><table>
                <thead><tr><th>Fan start</th><th className="num">Predicted change</th><th className="num">Observed change</th><th className="num">Error</th></tr></thead>
                <tbody>{therm.predictions.slice(0, 8).map((p: Json) => {
                  const d = (c: number | null) => (c == null || p.start_t_c == null ? null : (c - p.start_t_c) * 1.8 * (u === "F" ? 1 : 5 / 9));
                  const pr = d(p.predicted_c), ob = d(p.observed_c);
                  return (
                    <tr key={p.id}><td>{new Date(p.ts_start).toLocaleString()} <span className="faint">({num(p.horizon_min, 0)} min)</span></td>
                      <td className="num">{pr != null ? `${pr.toFixed(1)}°${u}` : "—"}</td>
                      <td className="num">{ob != null ? `${ob.toFixed(1)}°${u}` : "pending"}</td>
                      <td className="num">{p.error_f != null ? `${(p.error_f * (u === "F" ? 1 : 5 / 9)).toFixed(1)}°${u}` : "—"}</td></tr>
                  );
                })}</tbody>
              </table></div>
            )}
          </>)}
          {fitMsg && <p className="faint">{fitMsg}</p>}
        </Card>
      </div>

      <Card title="Historical replay (Level 1)" right={<button className="btn" onClick={runReplay} disabled={rows.length < 2}>Replay this range under each policy</button>}>
        <p className="note">Uses the recorded conditions to show how each control policy would have switched and how much moisture-transfer
          opportunity existed, given the recorded basement air. It does <strong>not</strong> show what basement humidity would have been —
          for that, run a counterfactual on the Simulation page with “Historical recorded conditions”.</p>
        {replay && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Policy</th><th className="num">Runtime</th><th className="num">Starts</th><th className="num">Transfer opportunity</th>
                <th className="num">Hours adding moisture</th><th className="num">Condensation-risk hours</th><th className="num">Energy (incl. cond. air)</th><th className="num">Cost</th></tr></thead>
              <tbody>
                {replay.results.map((r: Json) => (
                  <tr key={r.id}>
                    <td>{r.name}</td>
                    <td className="num">{hours(r.runtime_h)}</td>
                    <td className="num">{r.switch_cycles}</td>
                    <td className={`num ${r.opportunity_transfer_gal >= 0 ? "pos" : "neg"}`}>
                      {r.opportunity_transfer_gal >= 0 ? `${r.opportunity_transfer_gal.toFixed(2)} gal removed` : `${(-r.opportunity_transfer_gal).toFixed(2)} gal added`}</td>
                    <td className="num">{hours(r.hours_adding_moisture)}</td>
                    <td className="num">{hours(r.condensation_risk_hours)}</td>
                    <td className="num">{r.energy_kwh_incl_conditioned_air.toFixed(2)} kWh</td>
                    <td className="num">${r.cost.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="faint">{replay.label} Data source: {replay.data_source}.</p>
          </div>
        )}
      </Card>
    </>
  );
}
