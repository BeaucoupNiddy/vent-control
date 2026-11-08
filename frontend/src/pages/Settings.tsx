import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Card } from "../components";

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
      hint: "Used only when no surface sensor is valid. Leave blank to block auto ventilation without a sensor." },
    { path: "control.zone_aggregation", label: "Multi-room aggregation", type: "select", options: [["wettest", "Wettest room"], ["mean", "Average moisture"]] },
  ] },
  { title: "Fan control", fields: [
    { path: "control.min_on_minutes", label: "Minimum runtime (min)" },
    { path: "control.min_off_minutes", label: "Minimum off interval (min)" },
    { path: "control.manual_override_minutes", label: "Default manual override (min)" },
    { path: "control.max_cycles_per_hour_alert", label: "Excessive switching alert (starts/h)" },
    { path: "system.decision_seconds", label: "Decision interval (s)" },
  ] },
  { title: "Efficiency", fields: [
    { path: "control.energy_profile", label: "Energy profile", type: "select", options: [["max_drying", "Maximum Drying"], ["balanced", "Balanced"], ["energy_saver", "Energy Saver"]] },
    { path: "control.min_efficiency_g_per_kwh_balanced", label: "Balanced: min efficiency (g/kWh)" },
    { path: "control.min_efficiency_g_per_kwh_saver", label: "Energy Saver: min efficiency (g/kWh)" },
    { path: "control.efficiency_off_factor", label: "Stop below fraction of start threshold", step: 0.05 },
    { path: "house.electricity_cost_per_kwh", label: "Electricity rate ($/kWh)", step: 0.01 },
    { path: "house.conditioned_air_fraction", label: "Upstairs air fraction of exhaust (0-1)", step: 0.05, hint: "An assumption unless measured" },
    { path: "house.upstairs_temp_f", label: "Assumed upstairs temp (°F)" },
    { path: "house.upstairs_rh_pct", label: "Assumed upstairs RH (%)" },
    { path: "house.cooling_cop", label: "AC efficiency (COP)", step: 0.1 },
    { path: "house.heating_cop", label: "Heating efficiency (COP)", step: 0.1 },
    { path: "house.natural_ach", label: "Natural leakage (ACH)", step: 0.1 },
  ] },
  { title: "Safety interlocks", note: "Fan control must never override required combustion air, flood openings or legally required ventilation. Have naturally vented gas appliances checked for backdrafting before running exhaust-only ventilation unattended.", fields: [
    { path: "control.water_intrusion_lockout", label: "Water-intrusion lockout active", type: "bool" },
    { path: "control.combustion_interlock", label: "Block exhaust-only (gas appliance present)", type: "bool" },
    { path: "control.max_depressurization_pa", label: "Max basement depressurization (Pa)", nullable: true, hint: "Needs a pressure sensor; blank = off" },
    { path: "switches.hardware_max_on_minutes", label: "Plug auto-off timer (min)", nullable: true, hint: "Set on the plug itself (Phase 2); protects against controller failure" },
  ] },
  { title: "Sensors", fields: [
    { path: "sensors.provider", label: "Sensor provider", type: "select", options: [["mock", "Mock (simulation)"], ["ecowitt", "Ecowitt (Phase 2)"], ["homeassistant", "Home Assistant (Phase 2)"]] },
    { path: "sensors.ecowitt_host", label: "Ecowitt gateway IP", type: "text" },
    { path: "sensors.poll_seconds", label: "Polling interval (s)" },
    { path: "sensors.stale_minutes", label: "Stale after (min)" },
  ] },
  { title: "Dehumidifier (monitoring only)", fields: [
    { path: "dehumidifier.enabled", label: "Dehumidifier present", type: "bool" },
    { path: "dehumidifier.setpoint_rh_pct", label: "Setpoint RH (%)" },
    { path: "dehumidifier.rated_w", label: "Rated power (W)" },
    { path: "dehumidifier.removal_pints_per_day", label: "Estimated removal (pints/day)" },
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
                <thead><tr><th>Sensor id</th><th>Name</th><th>Location</th><th>Zone</th><th>Temp offset (°F)</th><th>RH offset (%)</th><th>Enabled</th></tr></thead>
                <tbody>
                  {cfg.sensors.mappings.map((m: Json, i: number) => (
                    <tr key={m.sensor_id}>
                      <td className="faint">{m.sensor_id}</td>
                      <td><input value={m.name} onChange={(e) => mapChange(i, "name", e.target.value)} /></td>
                      <td><select value={m.role} onChange={(e) => mapChange(i, "role", e.target.value)}>
                        {["outdoor", "basement", "surface", "upstairs", "pressure", "ignore"].map((r) => <option key={r}>{r}</option>)}</select></td>
                      <td><input value={m.zone ?? ""} style={{ width: 80 }} onChange={(e) => mapChange(i, "zone", e.target.value || null)} /></td>
                      <td><input type="number" step={0.1} style={{ width: 70 }} value={m.temp_offset_f} onChange={(e) => mapChange(i, "temp_offset_f", Number(e.target.value))} /></td>
                      <td><input type="number" step={0.5} style={{ width: 70 }} value={m.rh_offset_pct} onChange={(e) => mapChange(i, "rh_offset_pct", Number(e.target.value))} /></td>
                      <td><input type="checkbox" checked={m.enabled} onChange={(e) => mapChange(i, "enabled", e.target.checked)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="faint">Calibrate by placing all sensors together for a few hours and entering the offsets that make them agree with your best reference.</p>
            </div>
          )}
        </Card>
      ))}
      <Card>{saveBar}</Card>
    </>
  );
}
