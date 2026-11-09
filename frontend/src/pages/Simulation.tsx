import { useEffect, useMemo, useState } from "react";
import { api, Json } from "../api";
import { Card, TimeChart } from "../components";
import { hours, useUnit } from "../units";

const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)"];
const color = (i: number) => COLORS[i % COLORS.length];
const dashed = (i: number) => i >= COLORS.length;

type FieldDef = [key: string, label: string, hint?: string, step?: number];
const FIELDS: { title: string; fields: FieldDef[] }[] = [
  { title: "Basement", fields: [
    ["volume_ft3", "Basement air volume (ft³)", "", 100],
    ["initial_temp_f", "Initial temp (°F)"], ["initial_rh_pct", "Initial RH (%)"],
    ["basement_mean_temp_f", "Mean temp (°F)", "blank = profile default"], ["temp_variation_f", "Daily temp swing ± (°F)", "", 0.5],
    ["surface_temp_f", "Coldest surface (°F)", "blank = profile default", 0.5],
  ] },
  { title: "Moisture", fields: [
    ["moisture_source_pints_day", "Moisture sources (pints/day)", "slab, walls, leaks"],
    ["natural_ach", "Natural leakage (ACH)", "", 0.1],
    ["buffer_capacity_kg", "Wood/concrete buffer (kg)", "0 disables buffering"], ["buffer_tau_h", "Buffer time constant (h)"],
  ] },
  { title: "Thermal (two-node model)", fields: [
    ["coupling_w_k", "Air-to-structure coupling (W/K)", "blank = estimate from volume", 100],
    ["structure_mj_k", "Structure heat capacity (MJ/K)", "blank = estimate from volume"],
    ["structure_tau_h", "Structure relaxation to ground (h)"],
  ] },
  { title: "Fans & cost", fields: [
    ["fan_count", "Number of fans"], ["fan_cfm", "Actual CFM per fan"], ["fan_w", "Watts per fan"],
    ["conditioned_air_fraction", "Upstairs air fraction (0-1)", "assumption", 0.05],
    ["electricity_cost_per_kwh", "Electricity ($/kWh)", "", 0.01],
  ] },
  { title: "Run", fields: [
    ["duration_h", "Duration (hours)"], ["step_s", "Time step (s)"], ["decision_every_s", "Controller decides every (s)"], ["start_hour", "Start hour (0-23)"],
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
  const [show, setShow] = useState<"rh" | "td" | "t">("rh");
  const [wx, setWx] = useState<Json>(null);
  const [station, setStation] = useState("72231012916");
  const [wStart, setWStart] = useState("2025-07-01");
  const [wEnd, setWEnd] = useState("2025-07-07");
  const [wMsg, setWMsg] = useState("");

  useEffect(() => {
    api.profiles().then(setProfiles);
    api.simDefaults().then((d) => setParams(d.params));
    api.weatherCoverage().then(setWx).catch(() => {});
  }, []);

  async function download() {
    setWMsg("Downloading from NOAA…");
    try {
      const r = await api.weatherFetch(station, wStart, wEnd);
      setWx({ ...wx, cached: r.cached });
      setWMsg(`Cached ${r.hours} hours.`);
    } catch (e) { setWMsg(String((e as Error).message)); }
  }
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
      else if (profileId === "noaa") Object.assign(body, { weather_station: station, weather_start: wStart, weather_end: wEnd });
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
        const f = show === "td" ? p.td_f : p.t_f;
        row[r.id] = show === "rh" ? p.rh : (u === "F" ? f : (f - 32) * 5 / 9);
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

  const tf = (f: number | null | undefined) => (f == null ? "—" : `${(u === "F" ? f : (f - 32) * 5 / 9).toFixed(1)}°${u}`);
  const dtf = (f: number | null | undefined) => (f == null ? "—" : `${(u === "F" ? f : f * 5 / 9).toFixed(1)}°${u}`);
  if (!params) return <Card>Loading…</Card>;
  return (
    <>
      <Card title="Scenario simulation" right={<span className="badge modeled">modeled</span>}>
        <p className="note">Compares ten strategies under identical conditions with the same coupled temperature–moisture model the live
          controller uses (air and structure temperatures, humidity ratio, materials buffer). Results depend on the assumed moisture sources,
          buffering, airflow and thermal parameters. “TAMO + forecast” sees the scenario's own future weather, so it is an upper bound. The strategy that removes the most moisture is not
          necessarily the safest or cheapest.</p>
        <div className="form" style={{ marginTop: 12 }}>
          <div className="field">
            <label>Climate profile</label>
            <select value={profileId} onChange={(e) => setProfileId(e.target.value)}>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              <option value="custom">Custom hourly profile (CSV)</option>
              <option value="noaa">NOAA observations (cached)</option>
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
        {profileId === "noaa" && wx && (
          <div style={{ marginTop: 12 }}>
            <div className="form">
              <div className="field"><label>Station</label><select value={station} onChange={(e) => setStation(e.target.value)}>
                {Object.entries(wx.stations).map(([k, v]) => <option key={k} value={k}>{String(v)} ({k})</option>)}</select></div>
              <div className="field"><label>From</label><input type="date" value={wStart} onChange={(e) => setWStart(e.target.value)} /></div>
              <div className="field"><label>To</label><input type="date" value={wEnd} onChange={(e) => setWEnd(e.target.value)} /></div>
              <div className="field"><label>&nbsp;</label><button className="btn" onClick={download}>Download &amp; cache from NOAA</button></div>
            </div>
            {wMsg && <p className={wMsg.includes("failed") ? "error" : "faint"}>{wMsg}</p>}
            <p className="faint">Observed hourly data from NOAA NCEI (global-hourly). Downloads need internet once; cached data works offline.
              Cached: {wx.cached.length ? wx.cached.map((c: Json) => `${c.station} ${c.first.slice(0, 10)} to ${c.last.slice(0, 10)} (${c.hours} h)`).join("; ") : "nothing yet"}.</p>
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
          <p className="faint">Controller strategies use the thresholds from Settings. D uses the Maximum Drying profile; E and F use your energy profile;
            H and I are TAMO with Maximum Drying and Temperature Preservation weights.</p>
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
            <button className={show === "td" ? "active" : ""} onClick={() => setShow("td")}>Dew point</button>
            <button className={show === "t" ? "active" : ""} onClick={() => setShow("t")}>Temperature</button></div>}>
            <TimeChart title={show === "rh" ? "Basement RH by strategy" : show === "td" ? "Basement dew point by strategy" : "Basement temperature by strategy"} badge="modeled"
              unit={show === "rh" ? "%" : `°${u}`} data={merged} xFormat={xf} height={300} digits={1}
              series={[...result.results.map((r: Json, i: number) => ({ key: r.id, name: r.name, color: color(i), dashed: dashed(i) })),
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
                  <th className="num">Mean temp</th><th className="num">Min temp</th><th className="num">Temp std</th><th className="num">Outside preferred</th>
                  <th className="num">Below min</th><th className="num">Max fan cooling</th>
                </tr></thead>
                <tbody>
                  {result.results.map((r: Json, i: number) => {
                    const s = r.summary;
                    const hl = (k: string, low = true) => (s[k] != null && s[k] === best(k, low) ? { fontWeight: 700 } : undefined);
                    return (
                      <tr key={r.id}>
                        <td style={{ minWidth: 200 }}><span className="legend"><span className="sw" style={{ background: color(i) }} /></span>{r.name}<div className="faint">{r.description}</div></td>
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
                        <td className="num">{tf(s.mean_temp_f)}</td>
                        <td className="num">{tf(s.min_temp_f)}</td>
                        <td className="num" style={hl("temp_std_f")}>{dtf(s.temp_std_f)}</td>
                        <td className="num" style={hl("hours_outside_preferred")}>{hours(s.hours_outside_preferred)}</td>
                        <td className={`num ${s.hours_below_min_temp > 0 ? "neg" : ""}`}>{hours(s.hours_below_min_temp)}</td>
                        <td className="num">{s.max_fan_cooling_f != null ? dtf(s.max_fan_cooling_f) : "—"}</td>
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
