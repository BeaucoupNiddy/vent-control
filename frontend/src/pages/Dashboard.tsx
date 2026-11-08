import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Card, Icon, Stat } from "../components";
import { deltaTemp, duration, hours, localTime, moisture, num, pct, temp, useUnit } from "../units";

const MODES = [
  { id: "auto", label: "Auto" },
  { id: "off", label: "Off" },
  { id: "manual_on", label: "Manual on" },
  { id: "schedule", label: "Schedule" },
  { id: "simulation", label: "Simulation" },
];

export function AlertList({ alerts }: { alerts: Json[] }) {
  if (!alerts?.length) return null;
  return (
    <div className="grid" style={{ gap: 8 }}>
      {alerts.map((a) => (
        <div key={a.id} className={`alert ${a.severity}`} role="alert">
          <span className="icon"><Icon name={a.severity === "info" ? "info" : "alert"} /></span>
          <div>
            <strong>{a.severity === "critical" ? "Critical" : a.severity === "warning" ? "Warning" : "Note"}:</strong>{" "}
            {a.message} <span className="faint">since {localTime(a.since, true)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Dashboard({ status, refresh }: { status: Json; refresh: () => void }) {
  const u = useUnit();
  const [stats, setStats] = useState<Json>(null);
  const [manualMin, setManualMin] = useState(60);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const load = () => api.stats("24h").then(setStats).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  if (!status) return <div className="card">Loading…</div>;
  const g = status.groups[0];
  const d = g?.decision;
  const m = d?.metrics || {};
  const ind = m.indoor, out = m.outdoor;
  const eff = d?.effect;
  const mo = moisture(eff?.net_removal_g_h);

  async function setMode(mode: string) {
    setBusy(true);
    setErr("");
    try {
      await api.setMode(mode, mode === "manual_on" ? manualMin : undefined);
      refresh();
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <AlertList alerts={status.alerts} />

      <Card
        title={<span className="row">Ventilation{status.groups.length === 1 && g?.name ? ` · ${g.name}` : ""}</span>}
        right={
          <div className="row">
            <div className="seg" role="group" aria-label="Operating mode">
              {MODES.map((x) => (
                <button key={x.id} className={status.mode === x.id ? "active" : ""} disabled={busy}
                  onClick={() => setMode(x.id)}>{x.label}</button>
              ))}
            </div>
            <label className="faint row">
              for <input type="number" min={5} max={720} value={manualMin} style={{ width: 60 }}
                onChange={(e) => setManualMin(Number(e.target.value))} /> min
            </label>
          </div>
        }
      >
        {err && <p className="error">{err}</p>}
        {status.groups.map((grp: Json, i: number) => (
          <div key={grp.id} style={i > 0 ? { borderTop: "1px solid var(--border)", marginTop: 16, paddingTop: 16 } : undefined}>
            {status.groups.length > 1 && <h3 style={{ marginBottom: 8 }}>{grp.name}{grp.zone ? ` · zone ${grp.zone}` : " · all zones"}</h3>}
            <GroupDecision g={grp} status={status} />
          </div>
        ))}
      </Card>

      <div className="grid g3">
        <Card title="Basement" right={<span className="faint">{m.indoor_label}</span>}>
          <div className="big-num">{temp(ind?.t_c, u)} · {pct(ind?.rh_pct)}</div>
          <dl className="kv">
            <dt>Dew point</dt><dd>{temp(ind?.dew_point_c, u)}</dd>
            <dt>Humidity ratio</dt><dd>{num(ind?.w_g_per_kg, 2)} g/kg</dd>
            <dt>Absolute humidity</dt><dd>{num(ind?.abs_humidity_g_m3, 1)} g/m³</dd>
            <dt>Coldest surface</dt><dd>{temp(m.surface_c, u)} {m.surface_source && <span className="badge">{m.surface_source}</span>}</dd>
          </dl>
        </Card>
        <Card title="Outdoor">
          <div className="big-num">{temp(out?.t_c, u)} · {pct(out?.rh_pct)}</div>
          <dl className="kv">
            <dt>Dew point</dt><dd>{temp(out?.dew_point_c, u)}</dd>
            <dt>Humidity ratio</dt><dd>{num(out?.w_g_per_kg, 2)} g/kg</dd>
            <dt>RH if brought to basement temp</dt><dd>{pct(m.outdoor_rh_at_basement_temp_pct)}</dd>
            <dt>Condensation margin</dt><dd>{deltaTemp(m.condensation_margin_f, u)}</dd>
          </dl>
        </Card>
        <Card title="Difference" right={<span className="badge modeled">modeled</span>}>
          <div className={`big-num ${mo.kind === "remove" ? "pos" : mo.kind === "add" ? "neg" : ""}`} style={{ fontSize: 22 }}>
            {mo.kind === "none" ? "No meaningful transfer" : mo.text.charAt(0).toUpperCase() + mo.text.slice(1)}
          </div>
          <p className="faint" style={{ marginTop: 0 }}>Estimated if the fans run now{eff?.upstairs_path_cfm > 0 ? ", including assumed upstairs air" : ""}.</p>
          <dl className="kv">
            <dt>Dew point (basement − outdoor)</dt><dd>{deltaTemp(m.dewpoint_advantage_f, u)}</dd>
            <dt>Humidity ratio (basement − outdoor)</dt><dd>{num(m.delta_w_g_per_kg, 2)} g/kg</dd>
            <dt>Fan + conditioned-air power</dt><dd>{num(eff?.total_power_w, 0)} W</dd>
            <dt>Efficiency</dt><dd>{eff?.efficiency_g_per_kwh != null ? `${(eff.efficiency_g_per_kwh / 3785.4).toFixed(2)} gal/kWh` : "—"}</dd>
          </dl>
        </Card>
      </div>

      <div className="grid g2">
        <Card title="Fans" right={status.hardware?.hardware_auto_off_minutes
          ? <span className="badge good">plug auto-off {status.hardware.hardware_auto_off_minutes} min</span>
          : status.data_source !== "mock" ? <span className="badge crit">no hardware auto-off</span> : null}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Fan</th><th>State</th><th className="num">Running</th><th className="num">Power</th></tr></thead>
              <tbody>
                {status.groups.flatMap((grp: Json) => grp.fans).map((f: Json) => (
                  <tr key={f.id}>
                    <td>{f.name}<div className="faint">{f.type} · {f.actual_cfm} CFM est. · {f.switch_id}</div></td>
                    <td>
                      {f.switch.reachable ? (
                        <span className={`badge ${f.switch.confirmed ? "good" : ""}`}>{f.switch.confirmed ? "ON" : "OFF"} · verified</span>
                      ) : (
                        <span className="badge crit">unreachable · last known {f.switch.confirmed == null ? "unknown" : f.switch.confirmed ? "ON" : "OFF"}</span>
                      )}
                      {f.switch.commanded != null && f.switch.commanded !== f.switch.confirmed && (
                        <div className="faint">commanded {f.switch.commanded ? "ON" : "OFF"}</div>
                      )}
                    </td>
                    <td className="num">{duration(f.running_s)}</td>
                    <td className="num">{f.switch.power_w != null ? `${f.switch.power_w.toFixed(0)} W` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {status.dehumidifier && (
            <p className="faint" style={{ marginBottom: 0 }}>{status.dehumidifier.name} (monitored, never switched):{" "}
              {!status.dehumidifier.monitored ? "no metering plug configured" : status.dehumidifier.reachable === false ? "plug not responding" :
                status.dehumidifier.running == null ? "unknown" : `${status.dehumidifier.running ? "running" : "idle"} · ${num(status.dehumidifier.power_w, 0)} W`}</p>
          )}
        </Card>
        <Card title="Sensors" right={<span className={`badge ${status.data_source === "mock" ? "mock" : ""}`}>{status.data_source === "mock" ? "mock data" : status.data_source}</span>}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Sensor</th><th>Status</th><th className="num">Temp</th><th className="num">RH</th><th className="num">Dew pt</th><th className="num">Age</th></tr></thead>
              <tbody>
                {status.sensors.map((s: Json) => (
                  <tr key={s.sensor_id}>
                    <td>{s.name}<div className="faint">{s.role}{s.zone ? ` · ${s.zone}` : ""}</div></td>
                    <td><span className={`badge ${s.status === "ok" ? "good" : "crit"}`}>{s.status}</span></td>
                    <td className="num">{temp(s.temp_c, u)}</td>
                    <td className="num">{pct(s.rh_pct)}</td>
                    <td className="num">{temp(s.dew_point_c, u)}</td>
                    <td className="num">{s.age_s != null ? `${Math.round(s.age_s)} s` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {stats && (
        <Card title="Last 24 hours" right={<span className="faint">Modeled values are estimates, not measurements</span>}>
          <div className="grid g4">
            <Stat label="Fan runtime" value={hours(stats.fan_runtime_h)} />
            <Stat label="Switching cycles" value={stats.switch_cycles} />
            <Stat label="Net vapor moved by fans" badge="modeled"
              value={stats.net_vapor_removed_gal >= 0 ? `${stats.net_vapor_removed_gal.toFixed(2)} gal removed` : `${(-stats.net_vapor_removed_gal).toFixed(2)} gal added`}
              tone={stats.net_vapor_removed_gal >= 0 ? "pos" : "neg"} />
            <Stat label="Electricity" badge={stats.electricity_label} value={`${stats.electricity_kwh.toFixed(2)} kWh · $${stats.electricity_cost.toFixed(2)}`} />
            <Stat label="Time above upper RH" value={hours(stats.hours_above_upper)} />
            <Stat label="Time above high RH" value={hours(stats.hours_above_high)} />
            <Stat label="Ventilation favorable" badge="modeled" value={pct(stats.favorable_pct)} />
            <Stat label="Hours covered" value={hours(stats.hours_covered)} />
          </div>
        </Card>
      )}
    </>
  );
}

function GroupDecision({ g, status }: { g: Json; status: Json }) {
  const d = g?.decision;
  const safety = d?.safety;
  return (
    <>
      <div className="hero">
        <div className={`state-dot ${safety ? "safety" : g?.is_on ? "on" : "off"}`}>
          <Icon name={safety ? "shield" : "fan"} size={28} />
        </div>
        <div style={{ flex: 1, minWidth: 240 }}>
          <div className="row">
            <span className="state-big">{status.simulation_mode ? (d?.target_on ? "Would run" : "Would stay off") : g?.is_on ? "Fans ON" : "Fans OFF"}</span>
            <span className="badge">{status.mode.replace("_", " ").toUpperCase()}</span>
            {status.simulation_mode && <span className="badge mock">no physical commands</span>}
            {!g?.verified && <span className="badge crit">switch state unverified</span>}
            {g?.maintenance && <span className="badge crit">maintenance: interlocks bypassed</span>}
          </div>
          <p className="explain">{d?.explanation}</p>
          <div className="row faint">
            {g?.last_change && <span>Last switched {localTime(g.last_change, true)}</span>}
            {d?.next_eligible_switch && <span>· Next eligible switch {localTime(d.next_eligible_switch)}</span>}
            {g?.manual_until && <span>· Manual override ends {localTime(g.manual_until)}</span>}
            <span>· Profile: {status.energy_profile.replace("_", " ")}</span>
          </div>
          {d?.warnings?.map((w: string) => <p key={w} className="note" style={{ marginBottom: 0 }}>{w}</p>)}
        </div>
      </div>
      {d?.checks?.length > 0 && (
        <ul className="checks" aria-label="Decision checks">
          {d.checks.map((c: Json) => (
            <li key={c.id}>
              <span className={c.ok === true ? "ok" : c.ok === false ? "bad" : "na"} aria-label={c.ok ? "pass" : c.ok === false ? "fail" : "not evaluated"}>
                <Icon name={c.ok === true ? "check" : c.ok === false ? "x" : "dash"} />
              </span>
              <span>{c.label}{c.hard && <span className="faint"> · safety</span>}</span>
              <span className="muted detail">{c.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
