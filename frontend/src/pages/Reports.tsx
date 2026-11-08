import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Card } from "../components";
import { hours, pct, temp, useUnit } from "../units";

const gal = (g: number) => (g >= 0 ? `${g.toFixed(1)} gal removed` : `${(-g).toFixed(1)} gal added`);

export default function Reports() {
  const u = useUnit();
  const [range, setRange] = useState("365d");
  const [seasonal, setSeasonal] = useState<Json>(null);
  const [view, setView] = useState<"months" | "seasons">("months");
  const [cal, setCal] = useState<Json>(null);
  const [calRange, setCalRange] = useState("30d");
  const [annual, setAnnual] = useState<Json>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => { api.seasonal(range).then(setSeasonal); }, [range]);
  useEffect(() => { api.calibration(calRange).then(setCal); }, [calRange]);

  async function runAnnual() {
    setBusy(true);
    try {
      const d = await api.simDefaults();
      setAnnual(await api.annual({ params: d.params, use_observed: true }));
    } finally { setBusy(false); }
  }
  async function apply(p: Json) {
    const body = p.id === "natural_ach" ? { natural_ach: p.estimate } : { fan_cfm_total: p.estimate };
    if (!confirm(`Apply ${p.label} = ${p.estimate.toFixed(2)} ${p.unit} to your settings?`)) return;
    await api.applyCalibration(body);
    setMsg(`Applied ${p.label}.`);
    api.calibration(calRange).then(setCal);
  }

  const rows = seasonal ? seasonal[view] : [];
  return (
    <>
      <Card title="Seasonal report" right={
        <div className="row">
          {seasonal?.data_source === "mock" && <span className="badge mock">mock data</span>}
          <div className="seg">{[["90d", "90 days"], ["365d", "1 year"], ["all", "All"]].map(([k, l]) =>
            <button key={k} className={range === k ? "active" : ""} onClick={() => setRange(k)}>{l}</button>)}</div>
          <div className="seg">{(["months", "seasons"] as const).map((k) =>
            <button key={k} className={view === k ? "active" : ""} onClick={() => setView(k)}>{k}</button>)}</div>
        </div>}>
        {!rows?.length ? <p className="muted">No recorded history yet.</p> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Period</th><th className="num">Mean RH</th><th className="num">Max RH</th><th className="num">&gt;60% RH</th><th className="num">&gt;70% RH</th>
              <th className="num">Basement dew pt</th><th className="num">Outdoor dew pt</th><th className="num">Fan runtime</th><th className="num">Favorable</th>
              <th className="num">Vapor moved <span className="badge modeled">modeled</span></th><th className="num">Fan kWh</th><th className="num">Cond. air cost</th><th className="num">Dehumidifier</th></tr></thead>
            <tbody>{rows.map((r: Json) => (
              <tr key={r.period}>
                <td>{r.period}</td><td className="num">{pct(r.mean_rh, 1)}</td><td className="num">{pct(r.max_rh, 0)}</td>
                <td className="num">{hours(r.hours_above_upper, 0)}</td><td className="num">{hours(r.hours_above_high, 0)}</td>
                <td className="num">{temp(r.mean_indoor_dewpoint_c, u)}</td><td className="num">{temp(r.mean_outdoor_dewpoint_c, u)}</td>
                <td className="num">{hours(r.fan_runtime_h, 0)}</td><td className="num">{pct(r.favorable_pct)}</td>
                <td className={`num ${r.net_vapor_removed_gal >= 0 ? "pos" : "neg"}`}>{gal(r.net_vapor_removed_gal)}</td>
                <td className="num">{r.electricity_kwh.toFixed(1)}</td><td className="num">${r.conditioned_air_cost.toFixed(2)}</td>
                <td className="num">{r.dehumidifier ? `${hours(r.dehumidifier.runtime_h, 0)} · ${r.dehumidifier.kwh.toFixed(0)} kWh` : "—"}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Card>

      <Card title="Model calibration" right={
        <div className="seg">{[["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"]].map(([k, l]) =>
          <button key={k} className={calRange === k ? "active" : ""} onClick={() => setCalRange(k)}>{l}</button>)}</div>}>
        <p className="note">Fits a simple moisture balance to how basement humidity actually responded to fan runtime and outdoor air. Results are
          suggestions with 95% ranges. Nothing changes unless you apply it, and safety limits are never touched.</p>
        {msg && <p className="success">{msg}</p>}
        {cal && (<>
          {cal.parameters.length > 0 && (
            <div className="table-wrap"><table>
              <thead><tr><th>Parameter</th><th className="num">Estimate</th><th className="num">95% range</th><th className="num">Configured</th><th>Status</th><th /></tr></thead>
              <tbody>{cal.parameters.map((p: Json) => (
                <tr key={p.id}><td>{p.label}</td>
                  <td className="num">{p.estimate.toFixed(2)} {p.unit}</td>
                  <td className="num">{p.low.toFixed(2)} – {p.high.toFixed(2)}</td>
                  <td className="num">{p.configured != null ? `${Number(p.configured).toFixed(2)} ${p.unit}` : "—"}</td>
                  <td>{p.reliable ? <span className="badge good">well determined</span> : <span className="badge">uncertain: keep assumption</span>}</td>
                  <td>{p.reliable && p.suggest_key && <button className="btn" onClick={() => apply(p)}>Apply</button>}</td></tr>))}</tbody>
            </table></div>
          )}
          <p className="faint">{cal.blocks} half-hour blocks{cal.r_squared != null ? ` · R² ${cal.r_squared.toFixed(2)}` : ""}</p>
          <ul className="faint" style={{ paddingLeft: 18 }}>{cal.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
        </>)}
      </Card>

      <Card title="Annual performance estimate" right={<button className="btn primary" onClick={runAnnual} disabled={busy}>{busy ? "Simulating 12 months…" : "Estimate a year"}</button>}>
        <p className="note">Simulates a representative 6-day period for each month under three strategies and scales to the month. Uses cached NOAA observations
          where available (download them on the Simulation page); otherwise synthetic monthly profiles.</p>
        {annual && (<>
          <div className="table-wrap"><table>
            <thead><tr><th>Strategy</th><th className="num">Mean RH</th><th className="num">&gt;60% RH</th><th className="num">&gt;70% RH</th><th className="num">Runtime</th>
              <th className="num">Vapor moved</th><th className="num">Fan kWh</th><th className="num">Cond. air kWh</th><th className="num">Total cost</th><th className="num">Condensation-risk h</th></tr></thead>
            <tbody>{Object.entries(annual.totals).map(([k, t]: [string, Json]) => (
              <tr key={k}><td>{annual.strategies[k]}</td><td className="num">{pct(t.mean_rh, 1)}</td>
                <td className="num">{hours(t.hours_above_60, 0)}</td><td className="num">{hours(t.hours_above_70, 0)}</td>
                <td className="num">{hours(t.fan_runtime_h, 0)}</td>
                <td className={`num ${t.fan_net_removed_gal >= 0 ? "pos" : "neg"}`}>{gal(t.fan_net_removed_gal)}</td>
                <td className="num">{t.fan_kwh.toFixed(0)}</td><td className="num">{t.conditioned_air_kwh.toFixed(0)}</td>
                <td className="num">${t.total_cost.toFixed(2)}</td>
                <td className={`num ${t.condensation_risk_hours > 0 ? "neg" : ""}`}>{hours(t.condensation_risk_hours, 0)}</td></tr>))}</tbody>
          </table></div>
          <h3 style={{ margin: "14px 0 6px" }}>Mean basement RH by month <span className="badge modeled">modeled</span></h3>
          <div className="table-wrap"><table>
            <thead><tr><th>Strategy</th>{annual.months.map((m: Json) => <th key={m.month} className="num">{m.name}</th>)}</tr></thead>
            <tbody>{Object.keys(annual.totals).map((k) => (
              <tr key={k}><td>{annual.strategies[k]}</td>{annual.months.map((m: Json) =>
                <td key={m.month} className="num">{m.results[k].mean_rh.toFixed(0)}%</td>)}</tr>))}
              <tr><td className="faint">Weather</td>{annual.months.map((m: Json) => <td key={m.month} className="num faint">{m.source === "observed" ? "NOAA" : "synthetic"}</td>)}</tr>
            </tbody>
          </table></div>
          <p className="faint">{annual.label}</p>
        </>)}
      </Card>
    </>
  );
}
