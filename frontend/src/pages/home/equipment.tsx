/* Equipment health and wall condensation for the Home page. */
import { Json } from "../../api";
import { plugOn, plugPending } from "../../fanstate";
import { TempUnit, temp } from "../../units";
import { HIcon } from "./icons";
import { Tone, ago, dDeg, hourLabel } from "./util";

type Device = { id: string; icon: string; name: string; kind: string; tone: Tone; state: string; detail: string; signal?: number | null };

const ROLE: Record<string, [string, string]> = {
  outdoor: ["Outdoor sensor", "sensor"],
  basement: ["Room sensor", "sensor"],
  surface: ["Concrete probe", "probe"],
  upstairs: ["Upstairs sensor", "sensor"],
};

export function devices(status: Json): Device[] {
  const out: Device[] = [];
  const hw = status.hardware || {};
  const sensors: Json[] = status.sensors || [];
  const mock = hw.sensor_provider === "mock";
  out.push({
    id: "gateway", icon: "router", kind: mock ? "Simulated sensors" : "Weather gateway",
    name: mock ? "Demo sensor hub" : hw.sensor_provider === "ecowitt" ? "Ecowitt gateway" : "Sensor hub",
    tone: hw.gateway_error ? "bad" : "good", state: hw.gateway_error ? "Problem" : "Online",
    detail: hw.gateway_error ? String(hw.gateway_error) : `${sensors.length} sensor${sensors.length === 1 ? "" : "s"} reporting through it`,
  });
  sensors.forEach((s) => {
    const [kind, icon] = ROLE[s.role] || ["Sensor", "sensor"];
    const ok = s.status === "ok";
    const bits = [ok ? `Updated ${ago(s.age_s)}` : s.message || "No recent reading"];
    if (s.battery_low) bits.push("battery low");
    out.push({
      id: `s:${s.sensor_id}`, icon, kind, name: s.name,
      tone: !ok ? "bad" : s.battery_low ? "warn" : "good",
      state: !ok ? "No signal" : s.battery_low ? "Battery low" : "Online",
      detail: bits.join(" · "), signal: s.signal,
    });
  });
  const owner: Record<string, string> = {};
  (status.groups || []).forEach((g: Json) => (g.fans || []).forEach((f: Json) => { if (f.switch_id) owner[f.switch_id] = f.name; }));
  ((status.circulation && status.circulation.circulators) || []).forEach((c: Json) => { if (c.switch_id) owner[c.switch_id] = c.name; });
  const sws = hw.switches || {};
  Object.keys(sws).forEach((id) => {
    const sw = sws[id];
    const ok = sw.reachable !== false;
    const on = plugOn(sw);
    out.push({
      id: `p:${id}`, icon: "plug", kind: "Smart plug", name: owner[id] ? `Plug · ${owner[id]}` : `Plug ${id}`,
      tone: ok ? (plugPending(sw) ? "warn" : "good") : "bad", state: ok ? (plugPending(sw) ? `Turning ${sw.commanded ? "on" : "off"}…` : on ? "On" : "Off") : "Not responding",
      detail: ok ? `${sw.power_w > 0.5 ? `${Math.round(sw.power_w)} W · ` : ""}checked ${sw.last_ok ? ago((new Date(status.now).getTime() - new Date(sw.last_ok).getTime()) / 1000) : "—"}`
        : sw.error ? String(sw.error) : "Commands are not getting through",
    });
  });
  const d = status.dehumidifier;
  if (d) {
    out.push({
      id: "dehum", icon: "dehum", kind: "Dehumidifier", name: d.name || "Dehumidifier",
      tone: d.reachable === false ? "bad" : "good", state: d.reachable === false ? "Not responding" : d.running ? "Running" : "Idle",
      detail: d.monitored ? (d.power_w != null ? `${Math.round(d.power_w)} W` : "Monitored by plug") : "Not monitored",
    });
  }
  const fc = status.forecast;
  if (fc && fc.enabled) {
    out.push({
      id: "forecast", icon: "cloud", kind: "Weather forecast", name: fc.source === "mock" ? "Demo forecast" : "Open-Meteo forecast",
      tone: fc.usable ? "good" : "warn", state: fc.usable ? "Up to date" : "Unavailable",
      detail: fc.usable ? `${(fc.hours || []).length} hours ahead · updated ${ago((fc.age_h || 0) * 3600)}` : fc.error || "Using live sensors only",
    });
  }
  return out;
}

function Bars({ n }: { n: number | null | undefined }) {
  if (n == null) return null;
  return (
    <span className="h-bars" aria-label={`Signal ${n} of 4`}>
      {[1, 2, 3, 4].map((i) => <span key={i} className={i <= n ? "on" : ""} style={{ height: 3 + i * 2.5 }} />)}
    </span>
  );
}

export function EquipmentList({ list }: { list: Device[] }) {
  return (
    <ul className="h-equip">
      {list.map((d) => (
        <li key={d.id} className={`tone-${d.tone}`}>
          <span className="eq-icon"><HIcon name={d.icon} size={18} /></span>
          <span className="eq-text">
            <strong>{d.name}</strong>
            <small>{d.kind} · {d.detail}</small>
          </span>
          <Bars n={d.signal} />
          <span className="eq-state"><span className="dot" />{d.state}</span>
        </li>
      ))}
    </ul>
  );
}

/* ---------- wall condensation: how far the wall sits above the dew point ---------- */

export function WallMeter({ s, u }: { s: Json; u: TempUnit }) {
  const ok = s.status === "ok";
  const wallC: number | null = s.t_c, dewC: number | null = s.local_dew_point_c;
  const margin: number | null = s.margin_f;
  const tone: Tone = !ok ? "idle" : s.risk === "lower" ? "good" : s.risk === "monitor" ? "ok" : s.risk === "elevated" ? "warn" : "bad";
  let body = null;
  if (ok && wallC != null && dewC != null) {
    const lo = Math.min(wallC, dewC) - 4, hi = Math.max(wallC, dewC) + 4;
    const pos = (c: number) => `${(((c - lo) / (hi - lo)) * 100).toFixed(1)}%`;
    const a = Math.min(wallC, dewC), b = Math.max(wallC, dewC);
    body = (
      <div className="h-wall-track" aria-hidden>
        <span className="wt-gap" style={{ left: pos(a), width: `${(((b - a) / (hi - lo)) * 100).toFixed(1)}%` }} />
        <span className="wt-mark dew" style={{ left: pos(dewC) }}><HIcon name="drop" size={14} /><em>Dew point {temp(dewC, u, 0)}</em></span>
        <span className="wt-mark wall" style={{ left: pos(wallC) }}><HIcon name="wall" size={14} /><em>Wall {temp(wallC, u, 0)}</em></span>
      </div>
    );
  }
  const vent = s.ventilation || {};
  return (
    <div className={`h-wall tone-${tone}`}>
      <div className="h-wall-head">
        <span className="h-safe-icon"><HIcon name={tone === "good" || tone === "ok" ? "shield" : "shield-alert"} size={22} /></span>
        <div>
          <strong>{s.name}</strong>
          <small>{!ok ? "No recent reading" : `${dDeg(margin, u, false)} above the dew point · ${s.risk_label || s.risk}`}</small>
        </div>
      </div>
      {body}
      {ok && vent.predicted_margin_on_f != null && (
        <p className="h-hint">With the fans running for {vent.horizon_min || 30} min the wall would stay {dDeg(vent.predicted_margin_on_f, u, false)} above it (conservative estimate).</p>
      )}
      {s.future && s.future.potential_risk && (
        <p className="h-hint warn">Muggier air is forecast around {hourLabel(s.future.at)}. The fans avoid bringing it in if it could wet this wall.</p>
      )}
    </div>
  );
}
