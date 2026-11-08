import { useEffect, useMemo, useState } from "react";
import { api, Json } from "../api";
import { Card, TimeChart } from "../components";
import { hours, useUnit } from "../units";

const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)"];

type FieldDef = [key: string, label: string, hint?: string, step?: number];
const FIELDS: { title: string; fields: FieldDef[] }[] = [
  { title: "Basement", fields: [
    ["floor_area_ft2", "Floor area (ft²)"], ["ceiling_ft", "Ceiling height (ft)", "", 0.5],
    ["initial_temp_f", "Initial temp (°F)"], ["initial_rh_pct", "Initial RH (%)"],
    ["basement_mean_temp_f", "Mean temp (°F)", "blank = profile default"], ["temp_variation_f", "Daily temp swing ± (°F)", "", 0.5],
    ["surface_temp_f", "Coldest surface (°F)", "blank = profile default", 0.5],
  ] },
  { title: "Moisture", fields: [
    ["moisture_source_pints_day", "Moisture sources (pints/day)", "slab, walls, leaks"],
    ["natural_ach", "Natural leakage (ACH)", "", 0.1],
    ["buffer_capacity_kg", "Wood/concrete buffer (kg)", "0 disables buffering"], ["buffer_tau_h", "Buffer time constant (h)"],
  ] },
  { title: "Fans & cost", fields: [
    ["fan_count", "Number of fans"], ["fan_cfm", "Actual CFM per fan"], ["fan_w", "Watts per fan"],
    ["conditioned_air_fraction", "Upstairs air fraction (0-1)", "assumption", 0.05],
    ["electricity_cost_per_kwh", "Electricity ($/kWh)", "", 0.01],
  ] },
  { title: "Run", fields: [
    ["duration_h", "Duration (hours)"], ["step_s", "Time step (s)"], ["start_hour", "Start hour (0-23)"],
    ["dehumidifier_pints_day", "Dehumidifier (pints/day)"], ["dehumidifier_w", "Dehumidifier watts"], ["dehumidifier_setpoint_pct", "Dehumidifier setpoint (%)"],
  ] },
];

export default function Simulation() {
  const u = useUnit();
  const [profiles, setProfiles] = useState<Json[]>([]);
  const [profileId, setProfileId] = useState("nola_summer");
  const [params, setParams] = useState<Json>(null);
  const [csv, setCsv] = useState("hour,temp_f,dewpoint_f\n0,80,74\n6,77,74\n12,88,73\n18,86,73\n");
  const [result, setResult] = useState<Json>(null);
  const [preview, setPreview] = useState<Json>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [show, setShow] = useState<"rh" | "td">("rh");

  useEffect(() => {
    api.profiles().then(setProfiles);
    api.simDefaults().then((d) => setParams(d.params));
  }, []);
  useEffect(() => {
    if (profileId.startsWith("nola")) api.profile(profileId).then(setPreview); else setPreview(null);
  }, [profileId]);

  const set = (k: string, v: string) => setParams({ ...params, [k]: v === "" ? null : Number(v) });

  async function run() {
    setBusy(true);
    setErr("");
    try {
      const body: Json = { params: { ...params } };
      if (profileId === "custom") body.csv_text = csv;
      else if (profileId.startsWith("recorded")) body.recorded_range = profileId.split(":")[1];
      else body.profile_id = profileId;
      setResult(await api.simRun(body));
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  const merged = useMemo(() => {
    if (!result) return [];
    const base = result.results[0].series;
    return base.map((pt: Json, i: number) => {
      const row: Json = { x: pt.h, out_td: u === "F" ? pt.out_td_f : (pt.out_td_f - 32) * 5 / 9 };
      result.results.forEach((r: Json) => {
        const p = r.series[i];
        if (!p) return;
        row[r.id] = show === "rh" ? p.rh : (u === "F" ? p.td_f : (p.td_f - 32) * 5 / 9);
      });
      return row;
    });
  }, [result, show, u]);

  const identical = useMemo(() => {
    if (!result) return [] as string[];
    const groups: Record<string, string[]> = {};
    result.results.forEach((r: Json) => {
      const k = r.series.map((p: Json) => p.rh.toFixed(2)).join(",");
      (groups[k] ||= []).push(r.name);
    });
    return Object.values(groups).filter((g) => g.length > 1).map((g) => g.join(", "));
  }, [result]);

  const previewRows = useMemo(() => preview ? preview.temp_f.map((t: number, i: number) => ({
    x: i, t: u === "F" ? t : (t - 32) * 5 / 9, td: u === "F" ? preview.dewpoint_f[i] : (preview.dewpoint_f[i] - 32) * 5 / 9,
  })) : [], [preview, u]);

  const xf = (h: number) => {
    const sh = params?.start_hour || 0;
    const t = h + sh;
    return `${t >= 24 ? `d${Math.floor(t / 24) + 1} ` : ""}${String(Math.floor(t % 24)).padStart(2, "0")}:00`;
  };
  const best = (key: string, lowIsBest = true) => {
    if (!result) return null;
    const vals = result.results.map((r: Json) => r.summary[key]).filter((v: Json) => v != null);
    return lowIsBest ? Math.min(...vals) : Math.max(...vals);
  };

  if (!params) return <Card>Loading…</Card>;
  return (
    <>
      <Card title="Scenario simulation" right={<span className="badge modeled">modeled</span>}>
        <p className="note">Compares six strategies under identical conditions with a simplified moisture mass balance (1-minute steps).
          Results depend on the assumed moisture sources, buffering and airflow. The strategy that removes the most moisture is not
          necessarily the safest or cheapest.</p>
        <div className="form" style={{ marginTop: 12 }}>
          <div className="field">
            <label>Climate profile</label>
            <select value={profileId} onChange={(e) => setProfileId(e.target.value)}>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              <option value="custom">Custom hourly profile (CSV)</option>
              <option value="recorded:24h">Historical recorded conditions (last 24 h)</option>
              <option value="recorded:7d">Historical recorded conditions (last 7 days)</option>
            </select>
            <span className="hint">{profiles.find((p) => p.id === profileId)?.description || ""}</span>
          </div>
          <div className="field">
            <label>Dehumidifier</label>
            <label className="row"><input type="checkbox" checked={!!params.dehumidifier}
              onChange={(e) => setParams({ ...params, dehumidifier: e.target.checked })} /> include in all strategies</label>
          </div>
          <div className="field">
            <label>Fan arrangement</label>
            <label className="row"><input type="checkbox" checked={!!params.balanced}
              onChange={(e) => setParams({ ...params, balanced: e.target.checked })} /> balanced (matching intake fans)</label>
          </div>
        </div>
        {profileId === "custom" && (
          <div className="field" style={{ marginTop: 12 }}>
            <label>Hourly CSV: temp_f or temp_c, plus dewpoint_f, dewpoint_c or rh_pct. One row per hour.</label>
            <textarea rows={6} value={csv} onChange={(e) => setCsv(e.target.value)} />
          </div>
        )}
        {preview && (
          <p className="faint">{preview.notes?.join(" ")} Source: {preview.source}. Suggested basement {preview.basement_temp_f}°F, surface {preview.surface_temp_f}°F.</p>
        )}
        <details style={{ marginTop: 8 }}>
          <summary>Model inputs</summary>
          {FIELDS.map((sec) => (
            <div key={sec.title} style={{ marginTop: 12 }}>
              <h3 style={{ marginBottom: 6 }}>{sec.title}</h3>
              <div className="form">
                {sec.fields.map(([k, label, hint, step]) => (
                  <div className="field" key={k}>
                    <label htmlFor={`sim-${k}`}>{label}</label>
                    <input id={`sim-${k}`} type="number" step={step || 1} value={params[k] ?? ""} onChange={(e) => set(k, e.target.value)} />
                    {hint && <span className="hint">{hint}</span>}
                  </div>
                ))}
              </div>
            </div>
          ))}
          <p className="faint">Controller strategies (D, E) use the thresholds from Settings. D uses the Maximum Drying profile; E uses your energy profile.</p>
        </details>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={run} disabled={busy}>{busy ? "Running…" : "Run simulation"}</button>
          {err && <span className="error">{err}</span>}
        </div>
      </Card>

      {preview && !result && (
        <Card><TimeChart title={`Outdoor conditions · ${preview.name}`} badge="synthetic" unit={`°${u}`} data={previewRows}
          xFormat={(h) => `${h}h`} series={[{ key: "t", name: "Temperature", color: "var(--series-2)" }, { key: "td", name: "Dew point", color: "var(--series-1)" }]} /></Card>
      )}

      {result && (
        <>
          {result.insights.length > 0 && (
            <Card title="What this shows">
              <ul style={{ margin: 0, paddingLeft: 18 }}>{result.insights.map((s: string) => <li key={s}>{s}</li>)}</ul>
              <p className="faint">{result.label} Profile: {result.profile.name} ({result.profile.source}).</p>
            </Card>
          )}
          <Card right={<div className="seg"><button className={show === "rh" ? "active" : ""} onClick={() => setShow("rh")}>RH</button>
            <button className={show === "td" ? "active" : ""} onClick={() => setShow("td")}>Dew point</button></div>}>
            <TimeChart title={show === "rh" ? "Basement RH by strategy" : "Basement dew point by strategy"} badge="modeled"
              unit={show === "rh" ? "%" : `°${u}`} data={merged} xFormat={xf} height={300} digits={1}
              series={[...result.results.map((r: Json, i: number) => ({ key: r.id, name: r.name, color: COLORS[i] })),
                ...(show === "td" ? [{ key: "out_td", name: "Outdoor dew point", color: "var(--text-3)", dashed: true }] : [])]} />
            {identical.length > 0 && <p className="faint">Overlapping lines (identical results): {identical.join("; ")}.</p>}
          </Card>
          <Card title="Strategy comparison" right={<span className="faint">Best value per column highlighted</span>}>
            <div className="table-wrap">
              <table>
                <thead><tr>
                  <th>Strategy</th><th className="num">Final RH</th><th className="num">Mean RH</th><th className="num">Max RH</th>
                  <th className="num">&gt;60% RH</th><th className="num">&gt;70% RH</th><th className="num">Net moved by fans</th>
                  <th className="num">Runtime</th><th className="num">Starts</th><th className="num">Fan cost</th><th className="num">Cond. air lost</th>
                  <th className="num">Cond. air cost</th><th className="num">Dehum. water</th><th className="num">Condensation-risk h</th><th className="num">$/gal</th>
                </tr></thead>
                <tbody>
                  {result.results.map((r: Json, i: number) => {
                    const s = r.summary;
                    const hl = (k: string, low = true) => (s[k] != null && s[k] === best(k, low) ? { fontWeight: 700 } : undefined);
                    return (
                      <tr key={r.id}>
                        <td><span className="legend"><span className="sw" style={{ background: COLORS[i] }} /></span>{r.name}<div className="faint">{r.description}</div></td>
                        <td className="num" style={hl("final_rh")}>{s.final_rh.toFixed(1)}%</td>
                        <td className="num" style={hl("mean_rh")}>{s.mean_rh.toFixed(1)}%</td>
                        <td className="num" style={hl("max_rh")}>{s.max_rh.toFixed(1)}%</td>
                        <td className="num" style={hl("hours_above_60")}>{hours(s.hours_above_60)}</td>
                        <td className="num" style={hl("hours_above_70")}>{hours(s.hours_above_70)}</td>
                        <td className={`num ${s.fan_net_removed_gal >= 0 ? "pos" : "neg"}`}>
                          {Math.abs(s.fan_net_removed_gal) < 0.005 ? "0" : s.fan_net_removed_gal > 0 ? `${s.fan_net_removed_gal.toFixed(2)} gal removed` : `${(-s.fan_net_removed_gal).toFixed(2)} gal added`}</td>
                        <td className="num">{hours(s.fan_runtime_h)}</td>
                        <td className="num">{s.switch_cycles}</td>
                        <td className="num">${s.fan_cost.toFixed(2)}</td>
                        <td className="num">{Math.round(s.conditioned_air_ft3).toLocaleString()} ft³</td>
                        <td className="num">${s.conditioned_air_cost.toFixed(2)}</td>
                        <td className="num">{result.params.dehumidifier ? `${(s.dehumidifier_removed_kg / 3.785).toFixed(2)} gal` : "—"}</td>
                        <td className={`num ${s.condensation_risk_hours > 0 ? "neg" : ""}`}>{hours(s.condensation_risk_hours)}</td>
                        <td className="num" style={hl("cost_per_gallon_removed")}>{s.cost_per_gallon_removed != null ? `$${s.cost_per_gallon_removed.toFixed(2)}` : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="faint">“Net moved by fans” includes outdoor air and assumed conditioned air drawn from upstairs. Condensation-risk hours count
              fan runtime while the outdoor dew point was within the safety margin of the coldest surface.</p>
          </Card>
        </>
      )}
    </>
  );
}
