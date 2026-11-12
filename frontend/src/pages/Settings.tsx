import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Card } from "../components";
import { ZoneSettings } from "./SettingsZones";
import { UpdateCard } from "./SettingsUpdate";

function get(o: Json, path: string) {
  return path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
}
function setIn(o: Json, path: string, v: Json): Json {
  const [k, ...rest] = path.split(".");
  const copy = Array.isArray(o) ? [...o] : { ...o };
  copy[k] = rest.length ? setIn(o[k], rest.join("."), v) : v;
  return copy;
}

type F = { path: string; label: string; hint?: string; type?: "number" | "text" | "bool" | "select"; options?: string[][]; step?: number; nullable?: boolean };

const SECTIONS: { title: string; note?: string; fields: F[] }[] = [
  { title: "Environmental", fields: [
    { path: "control.target_rh_pct", label: "Target basement RH (%)" },
    { path: "control.upper_rh_pct", label: "Upper humidity threshold (%)" },
    { path: "control.high_rh_alert_pct", label: "High-humidity alert (%)" },
    { path: "control.rh_start_deadband_pct", label: "Start deadband above target (%)" },
    { path: "control.dewpoint_on_advantage_f", label: "Fan-on dew-point advantage (°F)", step: 0.5 },
    { path: "control.dewpoint_off_advantage_f", label: "Fan-off dew-point advantage (°F)", step: 0.5 },
    { path: "control.min_removal_g_per_h", label: "Minimum moisture removal (g/h)", hint: "Balanced profile; ×0.4 Max Drying, ×2 Energy Saver" },
    { path: "control.surface_margin_f", label: "Condensation safety margin (°F)", step: 0.5 },
    { path: "control.surface_temp_estimate_f", label: "Conservative min. surface temp (°F)", nullable: true, step: 0.5,
      hint: "Used only when no surface sensor is valid. Without either, auto still runs with warm dry outdoor air but not with air cold enough to chill surfaces." },
    { path: "control.zone_aggregation", label: "Multi-room aggregation", type: "select", options: [["wettest", "Wettest room (most water)"], ["mean", "Average moisture"]] },
    { path: "control.dry_window_advantage_f", label: "Strong drying window: outdoor dew point lower by (°F)", step: 0.5 },
    { path: "control.dry_window_stop_rh_pct", label: "During a strong window, keep running down to RH (%)", hint: "Dries the wood and concrete, not just the air" },
  ] },
  { title: "Fan control", fields: [
    { path: "control.min_on_minutes", label: "Minimum runtime (min)" },
    { path: "control.min_off_minutes", label: "Minimum off interval (min)" },
    { path: "control.manual_override_minutes", label: "Default manual override (min)" },
    { path: "control.max_cycles_per_hour_alert", label: "Excessive switching alert (starts/h)" },
    { path: "system.decision_seconds", label: "Decision interval (s)" },
  ] },
  { title: "Efficiency", fields: [
    { path: "control.energy_profile", label: "Energy profile", type: "select", options: [["max_drying", "Maximum Drying"], ["balanced", "Balanced"], ["temperature_preservation", "Temperature Preservation"], ["energy_saver", "Energy Saver"]] },
    { path: "control.min_efficiency_g_per_kwh_balanced", label: "Balanced: min efficiency (g/kWh)" },
    { path: "control.min_efficiency_g_per_kwh_saver", label: "Energy Saver: min efficiency (g/kWh)" },
    { path: "control.efficiency_off_factor", label: "Stop below fraction of start threshold", step: 0.05 },
    { path: "house.electricity_cost_per_kwh", label: "Electricity rate ($/kWh)", step: 0.01 },
    { path: "house.conditioned_air_fraction", label: "Upstairs air fraction of exhaust (0-1)", step: 0.05, hint: "Starting assumption; replaced by a learned value once an upstairs sensor has enough fan starts" },
    { path: "house.learn_conditioned_air_fraction", label: "Learn upstairs air fraction from fan starts", type: "bool" },
    { path: "house.heating_below_outdoor_f", label: "Heating runs below outdoor (°F)", hint: "Upstairs air costs heating energy only below this" },
    { path: "house.cooling_above_outdoor_f", label: "AC runs above outdoor (°F)", hint: "Upstairs air costs AC energy only above this" },
    { path: "house.upstairs_temp_f", label: "Assumed upstairs temp (°F)" },
    { path: "house.upstairs_rh_pct", label: "Assumed upstairs RH (%)" },
    { path: "house.cooling_cop", label: "AC efficiency (COP)", step: 0.1 },
    { path: "house.heating_cop", label: "Heating efficiency (COP)", step: 0.1 },
    { path: "house.natural_ach", label: "Natural leakage (ACH)", step: 0.1 },
    { path: "house.basement_volume_ft3", label: "Basement air volume (ft³)", step: 100 },
    { path: "house.moisture_buffer_kg", label: "Water held by wood and concrete (kg)", step: 25, hint: "Used in predictions; the air itself holds about 10 kg" },
    { path: "house.moisture_buffer_tau_h", label: "Materials response time (h)", step: 1 },
  ] },
  { title: "Temperature optimization", note: "Used by the temperature-aware controller (TAMO). The minimum temperature, pipe freeze protection and condensation checks are hard limits for both controllers. Penalty weights are heuristics, expressed in grams of water per hour so they can be compared with drying.", fields: [
    { path: "temperature.preferred_min_f", label: "Preferred minimum (°F)" },
    { path: "temperature.preferred_max_f", label: "Preferred maximum (°F)" },
    { path: "temperature.soft_tolerance_f", label: "Soft tolerance outside range (°F)", step: 0.5, hint: "Penalty grows faster past this distance" },
    { path: "temperature.min_allowed_f", label: "Minimum allowed basement temp (°F)", hint: "Hard limit, checked with a conservative prediction" },
    { path: "temperature.freeze_warning_f", label: "Pipe freeze warning (°F)", hint: "Needs a sensor with role \"pipe\"" },
    { path: "temperature.sensitivity", label: "Temperature sensitivity", type: "select", options: [["low", "Low"], ["medium", "Medium"], ["high", "High"], ["custom", "Custom"]] },
    { path: "temperature.custom_sensitivity_g_h", label: "Custom sensitivity (g/h per °F²)", step: 100 },
    { path: "temperature.change_tolerance_f", label: "Swing tolerance inside range (°F)", step: 0.5 },
    { path: "temperature.horizon_min", label: "Prediction horizon (min)", type: "select", options: [["15", "15"], ["30", "30"], ["60", "60"], ["120", "120"]] },
    { path: "temperature.urgency_enabled", label: "Relax temperature limits when humid", type: "bool" },
    { path: "temperature.urgency_low_rh_pct", label: "No urgency at or below RH (%)" },
    { path: "temperature.urgency_high_rh_pct", label: "Full urgency at or above RH (%)" },
    { path: "temperature.utility_start_g_h", label: "Start when net utility ≥ (g/h)", step: 10 },
    { path: "temperature.utility_stop_g_h", label: "Stop when net utility < (g/h)", step: 10 },
    { path: "temperature.min_dw_start_g_kg", label: "Min humidity-ratio advantage to start (g/kg)", step: 0.05 },
    { path: "temperature.min_dw_stop_g_kg", label: "Min humidity-ratio advantage to keep running (g/kg)", step: 0.05 },
    { path: "temperature.learning_enabled", label: "Learn thermal response from fan cycles", type: "bool" },
    { path: "temperature.learning_confidence", label: "Use learned model when confidence is", type: "select", options: [["conservative", "High only"], ["standard", "Medium or high"]] },
    { path: "temperature.air_structure_coupling_w_k", label: "Air-to-structure coupling (W/K)", nullable: true, hint: "Blank = estimate / learn" },
    { path: "temperature.structure_heat_capacity_mj_k", label: "Structure heat capacity (MJ/K)", nullable: true, hint: "Blank = estimate" },
    { path: "temperature.forecast_enabled", label: "Use outdoor forecast (optional)", type: "bool", hint: "Open-Meteo, no account. Works without internet." },
    { path: "temperature.forecast_latitude", label: "Latitude", step: 0.01 },
    { path: "temperature.forecast_longitude", label: "Longitude", step: 0.01 },
    { path: "temperature.max_deferral_min", label: "Max wait for a better window (min)" },
    { path: "house.elevation_ft", label: "Elevation (ft)", hint: "Sets air pressure for psychrometrics" },
  ] },
  { title: "Safety interlocks", note: "Fan control must never override required combustion air, flood openings or legally required ventilation. Have naturally vented gas appliances checked for backdrafting before running exhaust-only ventilation unattended.", fields: [
    { path: "control.water_intrusion_lockout", label: "Water-intrusion lockout active", type: "bool" },
    { path: "control.combustion_interlock", label: "Block exhaust-only (gas appliance present)", type: "bool" },
    { path: "control.max_depressurization_pa", label: "Max basement depressurization (Pa)", nullable: true, hint: "Needs a pressure sensor; blank = off" },
    { path: "switches.hardware_max_on_minutes", label: "Plug dead-man timer (min)", nullable: true, hint: "The plug turns itself off this long after ON unless the controller re-arms it. Protects against controller failure. Blank = off." },
  ] },
  { title: "Internal Air Circulation", note: "Controls the circulation fans. Start in Shadow mode: the controller logs what it would do without switching anything, while it learns how your zones respond. Automatic mode only switches fans marked commissioned. Circulation moves water between zones; it never counts as moisture removal.", fields: [
    { path: "circulation.mode", label: "Circulation automation", type: "select", options: [["off", "Off"], ["shadow", "Shadow (log only)"], ["auto", "Automatic (commissioned fans)"]] },
    { path: "circulation.min_on_minutes", label: "Minimum ON time (min)" },
    { path: "circulation.min_off_minutes", label: "Minimum OFF time (min)" },
    { path: "circulation.observe_rh_pct", label: "Observe zone RH (%)" },
    { path: "circulation.elevated_rh_pct", label: "Elevated zone RH (%)" },
    { path: "circulation.high_rh_pct", label: "High-priority RH (%)" },
    { path: "circulation.critical_rh_pct", label: "Critical RH alert (%)" },
    { path: "circulation.elevated_persist_min", label: "Elevated must persist (min)" },
    { path: "circulation.high_persist_min", label: "High must persist (min)" },
    { path: "circulation.critical_persist_min", label: "Critical alert after (min)" },
    { path: "circulation.rh_hysteresis_pct", label: "RH hysteresis (%)", step: 0.5 },
    { path: "circulation.min_dw_start_g_kg", label: "Moisture difference to start (g/kg)", step: 0.1 },
    { path: "circulation.min_dw_stop_g_kg", label: "Moisture difference to keep running (g/kg)", step: 0.1 },
    { path: "circulation.min_rh_improvement_pct", label: "Min predicted RH improvement (%)", step: 0.5 },
    { path: "circulation.rh_uncertainty_pct", label: "Sensor RH uncertainty, uncalibrated (%)", step: 0.5 },
    { path: "circulation.rh_uncertainty_calibrated_pct", label: "Sensor RH uncertainty, after comparison (%)", step: 0.5 },
    { path: "circulation.temp_uncertainty_f", label: "Sensor temperature uncertainty (°F)", step: 0.1 },
    { path: "circulation.ventilation_support", label: "Support exterior ventilation (assigned fans)", type: "bool" },
    { path: "circulation.post_ventilation_min", label: "Keep running after ventilation (min)" },
    { path: "circulation.condensation_aware", label: "Condensation-aware circulation", type: "bool" },
    { path: "circulation.periodic_mixing", label: "Periodic mixing (experimental)", type: "bool" },
    { path: "circulation.mixing_on_min", label: "Mixing: run (min)" },
    { path: "circulation.mixing_off_min", label: "Mixing: rest (min)" },
    { path: "circulation.energy_optimization", label: "Energy optimization", type: "bool", hint: "Skip circulation when every zone is below the observe threshold" },
    { path: "circulation.manual_override_minutes", label: "Manual override duration (min)" },
  ] },
  { title: "Concrete surface", note: "Risk classes use the conservative margin: surface temperature minus local dew point, minus the combined probe and dew-point uncertainty. A reading 0.5°F above the dew point is not treated as safe.", fields: [
    { path: "surfaces.monitor_margin_f", label: "Monitor below margin (°F)", step: 0.5 },
    { path: "surfaces.elevated_margin_f", label: "Elevated risk below (°F)", step: 0.5 },
    { path: "surfaces.possible_margin_f", label: "Condensation possible at or below (°F)", step: 0.5 },
    { path: "surfaces.probe_uncertainty_f", label: "Probe uncertainty (°F)", step: 0.1, hint: "Ecowitt WN30: about ±2°F stated; ±1°F after a check against a reference" },
    { path: "surfaces.dew_point_uncertainty_f", label: "Local dew-point uncertainty (°F)", step: 0.1 },
    { path: "surfaces.hysteresis_f", label: "Hysteresis (°F)", step: 0.1 },
    { path: "surfaces.advisory_sustain_min", label: "Advisory after sustained (min)" },
    { path: "surfaces.surface_rh_elevated_pct", label: "Track surface RH above (%)" },
    { path: "surfaces.persistent_hours", label: "Persistent risk after (h)", step: 0.5 },
    { path: "surfaces.air_follow_fraction", label: "Surface follows air change (0–1)", nullable: true, step: 0.05, hint: "Blank = learn from data (0.5 until confident)" },
  ] },
  { title: "Sensors", fields: [
    { path: "sensors.poll_seconds", label: "Polling interval (s)" },
    { path: "sensors.stale_minutes", label: "Stale after (min)" },
  ] },
  { title: "Dehumidifier (monitoring only)", fields: [
    { path: "dehumidifier.enabled", label: "Dehumidifier present", type: "bool" },
    { path: "dehumidifier.setpoint_rh_pct", label: "Setpoint RH (%)" },
    { path: "dehumidifier.rated_w", label: "Rated power (W)" },
    { path: "dehumidifier.removal_pints_per_day", label: "Estimated removal (pints/day)" },
    { path: "dehumidifier.monitor_switch_id", label: "Power-metering plug id", type: "text", hint: "Read only; the app never switches the dehumidifier" },
    { path: "dehumidifier.running_threshold_w", label: "Running above (W)" },
    { path: "dehumidifier.require_ventilation_beats_dehumidifier", label: "Energy Saver: fans only when cheaper than dehumidifier", type: "bool" },
  ] },
  { title: "System", fields: [
    { path: "system.port", label: "Port (restart required)" },
    { path: "system.host", label: "Listen address (restart required)", type: "text" },
    { path: "system.timezone", label: "Time zone", type: "text" },
    { path: "system.history_minutes", label: "Save history every (min)" },
    { path: "system.retention_days", label: "Keep data (days)" },
  ] },
];

function Field({ f, cfg, onChange }: { f: F; cfg: Json; onChange: (p: string, v: Json) => void }) {
  const v = get(cfg, f.path);
  const id = `f-${f.path}`;
  let input;
  if (f.type === "bool") input = <input id={id} type="checkbox" checked={!!v} onChange={(e) => onChange(f.path, e.target.checked)} />;
  else if (f.type === "select") input = (
    <select id={id} value={v ?? ""} onChange={(e) => onChange(f.path, e.target.value)}>
      {f.options!.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>);
  else if (f.type === "text") input = <input id={id} type="text" value={v ?? ""} onChange={(e) => onChange(f.path, e.target.value)} />;
  else input = <input id={id} type="number" step={f.step || 1} value={v ?? ""}
    onChange={(e) => onChange(f.path, e.target.value === "" ? (f.nullable ? null : "") : Number(e.target.value))} />;
  return (
    <div className="field">
      <label htmlFor={id}>{f.label}</label>
      {input}
      {f.hint && <span className="hint">{f.hint}</span>}
    </div>
  );
}

export default function Settings({ onSaved }: { onSaved: () => void }) {
  const [cfg, setCfg] = useState<Json>(null);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => { api.config().then(setCfg); }, []);
  if (!cfg) return <Card>Loading…</Card>;

  const change = (p: string, v: Json) => { setCfg(setIn(cfg, p, v)); setDirty(true); setOk(""); };
  const fanChange = (i: number, k: string, v: Json) => change(`fans.${i}.${k}`, v);
  const mapChange = (i: number, k: string, v: Json) => change(`sensors.mappings.${i}.${k}`, v);

  async function save() {
    setErr(""); setOk("");
    try {
      setCfg(await api.saveConfig(cfg));
      setDirty(false);
      setOk("Saved.");
      onSaved();
    } catch (e) {
      setErr(String((e as Error).message));
    }
  }

  const saveBar = (
    <div className="row">
      {err && <span className="error">{err}</span>}
      {ok && <span className="success">{ok}</span>}
      <a className="btn" href="/api/export/config">Export config</a>
      <a className="btn" href="/api/export/history.csv">Export history (CSV)</a>
      <button className="btn primary" disabled={!dirty} onClick={save}>Save settings</button>
    </div>
  );

  return (
    <>
      <Card title="Settings" right={saveBar}>
        <p className="note">All values are validated before saving. Defaults are starting points, not universally optimal settings.
          The controller never changes safety limits on its own.</p>
      </Card>
      <UpdateCard />
      <Hardware cfg={cfg} change={change} />
      <Groups cfg={cfg} change={change} />
      <ZoneSettings cfg={cfg} change={change} />
      {SECTIONS.map((s) => (
        <Card key={s.title} title={s.title}>
          {s.note && <p className="note">{s.note}</p>}
          <div className="form">{s.fields.map((f) => <Field key={f.path} f={f} cfg={cfg} onChange={change} />)}</div>
          {s.title === "Fan control" && (
            <div className="table-wrap" style={{ marginTop: 16 }}>
              <table>
                <thead><tr><th>Name</th><th>Type</th><th>Rated CFM</th><th>Actual CFM (est.)</th><th>Watts</th><th>Switch id</th><th>Group</th><th>Motor-rated plug</th><th>Enabled</th><th /></tr></thead>
                <tbody>
                  {cfg.fans.map((f: Json, i: number) => (
                    <tr key={i}>
                      <td><input value={f.name} onChange={(e) => fanChange(i, "name", e.target.value)} /></td>
                      <td><select value={f.type} onChange={(e) => fanChange(i, "type", e.target.value)}><option value="exhaust">exhaust</option><option value="intake">intake</option></select></td>
                      <td><input type="number" style={{ width: 70 }} value={f.rated_cfm} onChange={(e) => fanChange(i, "rated_cfm", Number(e.target.value))} /></td>
                      <td><input type="number" style={{ width: 70 }} value={f.actual_cfm} onChange={(e) => fanChange(i, "actual_cfm", Number(e.target.value))} /></td>
                      <td><input type="number" style={{ width: 60 }} value={f.rated_w} onChange={(e) => fanChange(i, "rated_w", Number(e.target.value))} /></td>
                      <td><input value={f.switch_id} onChange={(e) => fanChange(i, "switch_id", e.target.value)} /></td>
                      <td><select value={f.group} onChange={(e) => fanChange(i, "group", e.target.value)}>{cfg.groups.map((g: Json) => <option key={g.id} value={g.id}>{g.name}</option>)}</select></td>
                      <td><input type="checkbox" checked={f.motor_load_ok} onChange={(e) => fanChange(i, "motor_load_ok", e.target.checked)} /></td>
                      <td><input type="checkbox" checked={f.enabled} onChange={(e) => fanChange(i, "enabled", e.target.checked)} /></td>
                      <td><button className="btn" onClick={() => change("fans", cfg.fans.filter((_: Json, j: number) => j !== i))}>Remove</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button className="btn" style={{ marginTop: 8 }} onClick={() => change("fans", [...cfg.fans, {
                id: `fan_${Date.now()}`, name: "New fan", type: "exhaust", rated_cfm: 100, actual_cfm: 80, rated_w: 30,
                motor_load_ok: true, switch_id: "", group: cfg.groups[0].id, enabled: false }])}>Add fan</button>
              <p className="faint">Rated airflow is not installed airflow. Enter your best estimate of actual CFM.</p>
            </div>
          )}
          {s.title === "Sensors" && (
            <div className="table-wrap" style={{ marginTop: 16 }}>
              <table>
                <thead><tr><th>Sensor id</th><th>Name</th><th>Location</th><th>Zone</th><th>Temp offset (°F)</th><th>RH offset (%)</th><th>Calibrated</th><th>Enabled</th></tr></thead>
                <tbody>
                  {cfg.sensors.mappings.map((m: Json, i: number) => (
                    <tr key={m.sensor_id}>
                      <td className="faint">{m.sensor_id}</td>
                      <td><input value={m.name} onChange={(e) => mapChange(i, "name", e.target.value)} /></td>
                      <td><select value={m.role} onChange={(e) => mapChange(i, "role", e.target.value)}>
                        {["outdoor", "basement", "surface", "pipe", "upstairs", "pressure", "ignore"].map((r) => <option key={r}>{r}</option>)}</select></td>
                      <td>{cfg.zones?.length ? (
                        <select value={m.zone ?? ""} onChange={(e) => mapChange(i, "zone", e.target.value || null)}>
                          <option value="">none</option>{cfg.zones.map((z: Json) => <option key={z.id} value={z.id}>{z.name}</option>)}</select>
                      ) : <input value={m.zone ?? ""} style={{ width: 80 }} onChange={(e) => mapChange(i, "zone", e.target.value || null)} />}</td>
                      <td><input type="number" step={0.1} style={{ width: 70 }} value={m.temp_offset_f} onChange={(e) => mapChange(i, "temp_offset_f", Number(e.target.value))} /></td>
                      <td><input type="number" step={0.5} style={{ width: 70 }} value={m.rh_offset_pct} onChange={(e) => mapChange(i, "rh_offset_pct", Number(e.target.value))} /></td>
                      <td className="faint">{m.calibrated_at ? new Date(m.calibrated_at).toLocaleDateString() : "—"}</td>
                      <td><input type="checkbox" checked={m.enabled} onChange={(e) => mapChange(i, "enabled", e.target.checked)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="faint">Zone sets which basement area a sensor (or concrete probe) represents. To calibrate, use the side-by-side comparison on the Diagnostics page: it suggests offsets and records when they were applied. Raw readings are always kept.</p>
            </div>
          )}
        </Card>
      ))}
      <Card>{saveBar}</Card>
    </>
  );
}

function Hardware({ cfg, change }: { cfg: Json; change: (p: string, v: Json) => void }) {
  const [disc, setDisc] = useState<Json>(null);
  const [msg, setMsg] = useState("");
  const [raw, setRaw] = useState<Json>(null);
  const sw = cfg.switches;
  const discover = async () => {
    setMsg("");
    try { setDisc(await api.discover()); } catch (e) { setMsg(String((e as Error).message)); }
  };
  const addMapping = (ch: Json) => change("sensors.mappings", [...cfg.sensors.mappings, {
    sensor_id: ch.id, name: ch.id, role: ch.kind === "temp" ? "surface" : "basement", zone: ch.kind === "temp" ? null : "main",
    temp_offset_f: 0, rh_offset_pct: 0, enabled: true }]);
  const test = async (host: string) => {
    setMsg("Testing…");
    try { const r = await api.switchTest(host); setMsg(`${host}: ${JSON.stringify(r.info).slice(0, 160)} · relay ${r.status.on ? "ON" : "OFF"}`); }
    catch (e) { setMsg(String((e as Error).message)); }
  };
  const devTable = (key: "tapo_devices" | "shelly_devices", cols: [string, string][], blank: Json) => (
    <div className="table-wrap" style={{ marginTop: 8 }}>
      <table>
        <thead><tr>{cols.map(([, l]) => <th key={l}>{l}</th>)}<th /></tr></thead>
        <tbody>
          {sw[key].map((d: Json, i: number) => (
            <tr key={i}>
              {cols.map(([k]) => <td key={k}><input value={d[k] ?? ""} onChange={(e) => change(`switches.${key}.${i}.${k}`, k === "channel" ? Number(e.target.value) : e.target.value)} /></td>)}
              <td className="row">
                {key === "shelly_devices" && <button className="btn" onClick={() => test(d.host)}>Test</button>}
                <button className="btn" onClick={() => change(`switches.${key}`, sw[key].filter((_: Json, j: number) => j !== i))}>Remove</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn" style={{ marginTop: 6 }} onClick={() => change(`switches.${key}`, [...sw[key], blank])}>Add plug</button>
    </div>
  );
  return (
    <Card title="Hardware" right={<span className="faint">Save, then the app reconnects with the new hardware</span>}>
      <div className="form">
        <div className="field"><label>Sensors</label>
          <select value={cfg.sensors.provider} onChange={(e) => change("sensors.provider", e.target.value)}>
            <option value="mock">Mock (simulated basement)</option><option value="ecowitt">Ecowitt gateway</option><option value="homeassistant">Home Assistant</option>
          </select></div>
        <div className="field"><label>Fan plugs</label>
          <select value={sw.provider} onChange={(e) => change("switches.provider", e.target.value)}>
            <option value="mock">Mock</option><option value="tapo">TP-Link Tapo</option><option value="shelly">Shelly</option><option value="homeassistant">Home Assistant (incl. Matter)</option>
          </select></div>
      </div>

      {cfg.sensors.provider === "ecowitt" && (<>
        <h3 style={{ margin: "14px 0 6px" }}>Ecowitt GW1200</h3>
        <div className="form">
          <div className="field"><label>Gateway IP</label><input value={cfg.sensors.ecowitt_host} placeholder="192.168.1.50" onChange={(e) => change("sensors.ecowitt_host", e.target.value)} />
            <span className="hint">Polled at /get_livedata_info</span></div>
          <div className="field"><label>Accept pushed data</label><label className="row"><input type="checkbox" checked={cfg.sensors.ecowitt_push} onChange={(e) => change("sensors.ecowitt_push", e.target.checked)} /> Customized upload</label>
            <span className="hint">In the Ecowitt app: Weather Services → Customized → protocol Ecowitt, server = this computer's IP, port 8000, path /data/report/, interval 60 s.</span></div>
        </div>
      </>)}
      {(cfg.sensors.provider === "homeassistant" || sw.provider === "homeassistant") && (<>
        <h3 style={{ margin: "14px 0 6px" }}>Home Assistant</h3>
        <div className="form">
          <div className="field"><label>URL</label><input value={cfg.homeassistant.url} placeholder="http://homeassistant.local:8123" onChange={(e) => change("homeassistant.url", e.target.value)} /></div>
          <div className="field"><label>Long-lived token</label><input type="password" value={cfg.homeassistant.token} onChange={(e) => change("homeassistant.token", e.target.value)} /></div>
        </div>
        {cfg.sensors.provider === "homeassistant" && <div className="field" style={{ marginTop: 8 }}><label>Sensor entities (JSON: sensor id → temp / rh / pressure_pa entity)</label>
          <textarea rows={5} defaultValue={JSON.stringify(cfg.homeassistant.sensor_entities, null, 2)}
            onBlur={(e) => { try { change("homeassistant.sensor_entities", JSON.parse(e.target.value || "{}")); setMsg(""); } catch { setMsg("Sensor entities: invalid JSON"); } }} /></div>}
        {sw.provider === "homeassistant" && <p className="faint">For Home Assistant plugs, set each fan's switch id to the entity id, e.g. switch.basement_fan.</p>}
      </>)}
      {sw.provider === "tapo" && (<>
        <h3 style={{ margin: "14px 0 6px" }}>TP-Link Tapo plugs</h3>
        <p className="note">Local control through python-kasa. Plugs set up through Matter (e.g. Apple Home) and never added in the Tapo app need no
          TP-Link account: leave the email and password empty. Plugs added in the Tapo app need that login, which is used only to build the
          local connection key and is stored only in this computer's database. The app turns on each plug's own auto-off timer ({sw.hardware_max_on_minutes ?? "—"} min) and re-arms it while fans run,
          causing a ~1 s off/on blip about every {sw.hardware_max_on_minutes ? Math.round(sw.hardware_max_on_minutes / 2) : "—"} minutes.
          Only energy-monitoring models (P110M/P115) report watts. Give each plug a fixed IP in your router.</p>
        <div className="form">
          <div className="field"><label>TP-Link account email (optional)</label><input value={sw.tapo_username} onChange={(e) => change("switches.tapo_username", e.target.value)} /></div>
          <div className="field"><label>TP-Link password (optional)</label><input type="password" value={sw.tapo_password} onChange={(e) => change("switches.tapo_password", e.target.value)} /></div>
        </div>
        {devTable("tapo_devices", [["switch_id", "Switch id (used by fans)"], ["host", "Plug IP"]], { switch_id: `tapo-${sw.tapo_devices.length + 1}`, host: "" })}
      </>)}
      {sw.provider === "shelly" && (<>
        <h3 style={{ margin: "14px 0 6px" }}>Shelly plugs</h3>
        {devTable("shelly_devices", [["switch_id", "Switch id (used by fans)"], ["host", "Plug IP"], ["channel", "Channel"]],
          { switch_id: `shelly-${sw.shelly_devices.length + 1}`, host: "", channel: 0, generation: "auto", username: "", password: "" })}
      </>)}

      <div className="row" style={{ marginTop: 14 }}>
        <button className="btn" onClick={discover}>Discover sensor channels</button>
        {cfg.sensors.provider === "ecowitt" && <button className="btn" onClick={async () => setRaw(await api.sensorsRaw())}>Show raw gateway data</button>}
        {msg && <span className={msg.startsWith("Testing") ? "faint" : "error"}>{msg}</span>}
      </div>
      {disc && (
        <div className="table-wrap" style={{ marginTop: 8 }}>
          <table>
            <thead><tr><th>Channel</th><th>Type</th><th className="num">Temp</th><th className="num">RH</th><th /></tr></thead>
            <tbody>{disc.channels.map((ch: Json) => (
              <tr key={ch.id}><td>{ch.id}</td><td>{ch.kind}</td>
                <td className="num">{ch.temp_c != null ? `${(ch.temp_c * 9 / 5 + 32).toFixed(1)}°F` : "—"}</td>
                <td className="num">{ch.rh_pct != null ? `${ch.rh_pct}%` : "—"}</td>
                <td>{cfg.sensors.mappings.some((m: Json) => m.sensor_id === ch.id) ? <span className="faint">mapped</span> :
                  <button className="btn" onClick={() => addMapping(ch)}>Add to mappings</button>}</td></tr>
            ))}</tbody>
          </table>
          <p className="faint">After adding, set each sensor's location and zone below (Sensors), then save.</p>
        </div>
      )}
      {raw && <pre>{JSON.stringify(raw, null, 2)}</pre>}
    </Card>
  );
}

function Groups({ cfg, change }: { cfg: Json; change: (p: string, v: Json) => void }) {
  const zones = [...new Set(cfg.sensors.mappings.filter((m: Json) => m.role === "basement").map((m: Json) => m.zone).filter(Boolean))] as string[];
  return (
    <Card title="Fan groups and zones">
      <p className="note">Each group is decided independently. A group can serve one basement zone (its sensors only) or all zones together.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Group id</th><th>Name</th><th>Zone served</th><th>Fans</th><th /></tr></thead>
          <tbody>{cfg.groups.map((g: Json, i: number) => (
            <tr key={i}>
              <td className="faint">{g.id}</td>
              <td><input value={g.name} onChange={(e) => change(`groups.${i}.name`, e.target.value)} /></td>
              <td><select value={g.zone ?? ""} onChange={(e) => change(`groups.${i}.zone`, e.target.value || null)}>
                <option value="">All zones (aggregate)</option>{zones.map((z) => <option key={z} value={z}>{z}</option>)}</select></td>
              <td>{cfg.fans.filter((f: Json) => f.group === g.id).map((f: Json) => f.name).join(", ") || <span className="faint">none</span>}</td>
              <td>{cfg.groups.length > 1 && !cfg.fans.some((f: Json) => f.group === g.id) &&
                <button className="btn" onClick={() => change("groups", cfg.groups.filter((_: Json, j: number) => j !== i))}>Remove</button>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <button className="btn" style={{ marginTop: 8 }} onClick={() => change("groups", [...cfg.groups, { id: `group_${cfg.groups.length + 1}`, name: `Group ${cfg.groups.length + 1}`, zone: null }])}>Add group</button>
    </Card>
  );
}
