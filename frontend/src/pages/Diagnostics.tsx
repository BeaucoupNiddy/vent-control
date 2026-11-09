import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Card } from "../components";
import { num, pct, temp, useUnit } from "../units";
import { SensorComparison } from "./Zones";

export default function Diagnostics({ status }: { status: Json }) {
  const u = useUnit();
  const [mock, setMock] = useState<Json>(null);
  const [t, setT] = useState(79);
  const [rh, setRh] = useState(70);
  const [calc, setCalc] = useState<Json>(null);
  const [err, setErr] = useState("");

  useEffect(() => { api.mockState().then(setMock).catch(() => {}); }, []);
  useEffect(() => { api.calc(t, rh).then((r) => { setCalc(r); setErr(""); }).catch((e) => setErr(e.message)); }, [t, rh]);

  const fault = async (target: string, id: string | null, f: string | null) => setMock(await api.mockFault(target, id, f));
  const sensors = status?.sensors || [];
  const switches = [...new Set([...(status?.groups || []).flatMap((g: Json) => g.fans.map((f: Json) => f.switch_id)),
    ...(status?.circulation?.circulators || []).map((c: Json) => c.switch_id).filter(Boolean)])] as string[];

  return (
    <div className="grid g2">
      <SensorComparison status={status} />
      <UpstairsAirCard model={status?.airflow_model} />
      {status?.data_source === "mock" && mock && (
        <Card title="Mock fault injection" right={<span className="badge mock">mock hardware</span>}>
          <p className="note">Exercise the fail-safe behavior. Effects show on the dashboard within a decision cycle; stale-sensor
            shutdown needs the stale threshold ({"≥"} 5 min by default) to pass. Mock outdoor weather follows the
            “{mock.profile.name}” synthetic profile.</p>
          <h3 style={{ margin: "12px 0 6px" }}>Sensors</h3>
          <div className="grid" style={{ gap: 6 }}>
            {sensors.map((s: Json) => {
              const off = mock.sensor_faults.includes(s.sensor_id);
              return (<div key={s.sensor_id} className="row"><span style={{ flex: 1 }}>{s.name}</span>
                <button className={`btn ${off ? "on" : ""}`} onClick={() => fault("sensor", s.sensor_id, off ? null : "offline")}>{off ? "Restore" : "Stop updating"}</button></div>);
            })}
            <div className="row"><span style={{ flex: 1 }}>Sensor gateway</span>
              <button className={`btn ${mock.gateway_down ? "on" : ""}`} onClick={() => fault("gateway", null, mock.gateway_down ? null : "down")}>{mock.gateway_down ? "Restore" : "Take offline"}</button></div>
          </div>
          <h3 style={{ margin: "12px 0 6px" }}>Smart plugs</h3>
          <div className="grid" style={{ gap: 6 }}>
            {switches.map((sid) => {
              const f = mock.switch_faults[sid];
              return (<div key={sid} className="row"><span style={{ flex: 1 }}>{sid} {f && <span className="badge crit">{f}</span>}</span>
                <button className="btn" onClick={() => fault("switch", sid, f === "unreachable" ? null : "unreachable")}>{f === "unreachable" ? "Restore" : "Unreachable"}</button>
                <button className="btn" onClick={() => fault("switch", sid, f === "stuck" ? null : "stuck")}>{f === "stuck" ? "Restore" : "Ignore commands"}</button></div>);
            })}
          </div>
        </Card>
      )}
      <Card title="Psychrometric calculator">
        <div className="form">
          <div className="field"><label htmlFor="ct">Temperature (°F)</label><input id="ct" type="number" value={t} onChange={(e) => setT(Number(e.target.value))} /></div>
          <div className="field"><label htmlFor="cr">Relative humidity (%)</label><input id="cr" type="number" value={rh} onChange={(e) => setRh(Number(e.target.value))} /></div>
        </div>
        {err && <p className="error">{err}</p>}
        {calc && (
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>Dew point</dt><dd>{temp(calc.dew_point_c, u, 2)}</dd>
            <dt>Humidity ratio</dt><dd>{num(calc.w_g_per_kg, 3)} g/kg ({num(calc.w * 7000, 1)} grains/lb)</dd>
            <dt>Vapor pressure</dt><dd>{num(calc.pw_kpa, 3)} kPa</dd>
            <dt>Absolute humidity</dt><dd>{num(calc.abs_humidity_g_m3, 2)} g/m³</dd>
            <dt>Enthalpy</dt><dd>{num(calc.enthalpy_kj_kg, 1)} kJ/kg dry air</dd>
            <dt>RH</dt><dd>{pct(calc.rh_pct, 1)}</dd>
          </dl>
        )}
        <p className="faint">ASHRAE Hyland-Wexler saturation over liquid water, ideal-gas humidity ratio, sea-level pressure.</p>
      </Card>
    </div>
  );
}

function UpstairsAirCard({ model }: { model: Json }) {
  if (!model) return null;
  const l = model.learned || {};
  return (
    <Card title="Where the fan's makeup air comes from">
      <p className="note">An exhaust fan pulls replacement air from wherever the house leaks most: outdoors, or down through
        the floor from upstairs. Upstairs air is already heated or cooled, so it costs energy while the HVAC runs.</p>
      <p>Using <strong>{num(model.upstairs_fraction * 100, 0)}%</strong> from upstairs
        ({model.source === "learned" ? "learned from fan starts" : "assumed"}).</p>
      {l.fraction != null ? (
        <p className="note">Estimate from {l.starts_used} fan starts: {num(l.fraction * 100, 0)}% (likely range{" "}
          {num(l.low * 100, 0)}–{num(l.high * 100, 0)}%), confidence {l.confidence}.{" "}
          {model.source !== "learned" && "Used once confidence is medium or better."}</p>
      ) : (
        <p className="note">{l.note || "Not estimated yet. Needs an upstairs temperature/humidity sensor and a few days of fan runs."}</p>
      )}
    </Card>
  );
}
