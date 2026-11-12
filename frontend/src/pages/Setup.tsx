/* Guided setup walkthrough. Progress and answers are saved on the controller, so setup can be
   paused on one device and resumed on another. */
import { ReactNode, useCallback, useEffect, useState } from "react";
import { api, Json } from "../api";
import { Card, Icon } from "../components";
import Worksheet from "./Worksheet";
import "./setup.css";

export const GUIDE_URL = "https://github.com/BeaucoupNiddy/vent-control/blob/main/docs/SETUP_GUIDE.md";

function setIn(o: Json, path: string, v: Json): Json {
  const [k, ...rest] = path.split(".");
  const copy = Array.isArray(o) ? [...o] : { ...o };
  copy[k] = rest.length ? setIn(o[k], rest.join("."), v) : v;
  return copy;
}
function get(o: Json, path: string) {
  return path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
}

type Change = (path: string, v: Json) => void;

/* ---------- small form helpers ---------- */
function Num({ cfg, path, label, hint, step = 1, change, nullable }: { cfg: Json; path: string; label: string; hint?: string; step?: number; change: Change; nullable?: boolean }) {
  const v = get(cfg, path);
  return (
    <div className="field"><label>{label}</label>
      <input type="number" step={step} value={v ?? ""} onChange={(e) => change(path, e.target.value === "" ? (nullable ? null : 0) : Number(e.target.value))} />
      {hint && <span className="hint">{hint}</span>}</div>
  );
}
function Text({ cfg, path, label, hint, change, placeholder, password }: { cfg: Json; path: string; label: string; hint?: string; change: Change; placeholder?: string; password?: boolean }) {
  return (
    <div className="field"><label>{label}</label>
      <input type={password ? "password" : "text"} value={get(cfg, path) ?? ""} placeholder={placeholder} onChange={(e) => change(path, e.target.value)} />
      {hint && <span className="hint">{hint}</span>}</div>
  );
}
function Check({ cfg, path, label, change }: { cfg: Json; path: string; label: string; change: Change }) {
  return <label className="su-check"><input type="checkbox" checked={!!get(cfg, path)} onChange={(e) => change(path, e.target.checked)} /><span>{label}</span></label>;
}

/* ---------- how-to text for each step ---------- */
const HOWTO: Record<string, ReactNode> = {
  prepare: <>
    <p>Setup takes about an hour once the hardware is installed. You can stop at any point with <b>Pause</b> and pick up later, from this computer or your phone. Everything you enter is saved on the controller.</p>
    <p>Open the <b>Worksheet</b> (button above) first. It lists everything to write down while you walk round the basement: sensor channels, plug addresses and fan details. The full <a href={GUIDE_URL} target="_blank" rel="noreferrer">setup guide</a> explains each step in more detail.</p>
  </>,
  install: <>
    <p>This step is the physical part: putting the hardware in place. Tick each item once it's done. The setup guide's "Physical installation" section has the details for each device.</p>
    <p>Do this in order: <b>gateway</b> first (so you can watch sensors appear in the Ecowitt app), then <b>sensors</b>, the <b>concrete probe</b>, the <b>exterior fan</b>, the <b>plugs</b> and finally the <b>circulation fans</b>.</p>
  </>,
  location: <p>These set the clock, the weather forecast and the size of the air space the app is drying. New Orleans is already filled in.</p>,
  gateway: <>
    <p>The app reads the sensors from the Ecowitt GW1200 over your Wi-Fi. Find the gateway's address in the Ecowitt app (the gateway → <i>More</i> → <i>Device info</i>) or in your router's device list, type it below and press <b>Save and check</b>.</p>
    <p className="faint">Optional, faster updates: in the Ecowitt app open <i>Weather Services → Customized</i>, turn it on, protocol <b>Ecowitt</b>, server <b>{location.hostname}</b>, port <b>{location.port || "8000"}</b>, path <b>/data/report/</b>, interval 60 s.</p>
  </>,
  sensors: <p>The list shows every sensor the gateway can hear, with its live reading. For each one choose where it is. To tell sensors apart, breathe on one or hold it in your hand for a minute and watch which reading rises. Remove the simulated (mock) sensors from the list.</p>,
  zones: <p>The app treats the basement as three areas. Rename them to match your space and give each a rough size (floor area × ceiling height). Tick the zone where the exterior fan is. The moisture shares are guesses of where the dampness comes from; the app refines its view as it learns.</p>,
  plugs: <>
    <p>Enter the email and password you use for the Tapo app. They stay on this computer and are only used to talk to the plugs on your Wi-Fi.</p>
    <p>Add one row per plug with its address (Tapo app → the plug → gear icon → <i>Device info</i>). Then choose which plug powers which fan. Press <b>Save and check</b> to test the connection: nothing switches yet.</p>
  </>,
  exhaust: <p>Check the fan's details, then test it. <b>Run 2 minutes</b> turns the fan on (within about a minute) and back off automatically. Go and listen, and check the air blows out of the house.</p>,
  circulators: <p>Set each Honeywell fan's speed on the fan itself and leave its own switch ON: the plug does the switching. Test each one, then tick the checks and press <b>Mark commissioned</b>. Circulation starts in <b>Shadow</b>: the app records what it would do without switching the fans, so you can review it before allowing Automatic.</p>,
  safety: <p>An exhaust fan lowers the air pressure in the basement. That's how it works, but it can pull fumes back down a chimney from a gas water heater or furnace. Answer honestly; the app blocks the fan if it can't run safely.</p>,
  targets: <p>The defaults suit most basements. The app runs the fan only when outdoor air is drier than basement air, and keeps the basement within your temperature range.</p>,
  golive: <p>When everything above is green, switch to Automatic. The app then decides on its own when to run the exterior fan. You can switch back to Off any time from the Home page.</p>,
};

/* ---------- step editors ---------- */
function Editor({ step, cfg, change, after }: { step: string; cfg: Json; change: Change; after: () => void }) {
  switch (step) {
    case "location": return (
      <div className="form">
        <Text cfg={cfg} path="system.timezone" label="Time zone" change={change} hint="New Orleans: America/Chicago" />
        <Num cfg={cfg} path="temperature.forecast_latitude" label="Latitude" step={0.01} change={change} />
        <Num cfg={cfg} path="temperature.forecast_longitude" label="Longitude" step={0.01} change={change} />
        <Num cfg={cfg} path="house.elevation_ft" label="Elevation (ft)" change={change} />
        <Num cfg={cfg} path="house.basement_volume_ft3" label="Basement air volume (ft³)" step={100} change={change} hint="Floor area × ceiling height" />
        <Num cfg={cfg} path="house.electricity_cost_per_kwh" label="Electricity price ($/kWh)" step={0.01} change={change} />
      </div>);
    case "gateway": return (
      <div className="form">
        <div className="field"><label>Sensor source</label>
          <select value={cfg.sensors.provider} onChange={(e) => change("sensors.provider", e.target.value)}>
            <option value="mock">Simulated basement (demo)</option><option value="ecowitt">Ecowitt gateway</option><option value="homeassistant">Home Assistant</option>
          </select></div>
        {cfg.sensors.provider === "ecowitt" && <Text cfg={cfg} path="sensors.ecowitt_host" label="Gateway address" placeholder="192.168.0.50" change={change} />}
        {cfg.sensors.provider === "ecowitt" && <Check cfg={cfg} path="sensors.ecowitt_push" label="Also accept pushed updates (Customized upload)" change={change} />}
      </div>);
    case "sensors": return <SensorEditor cfg={cfg} change={change} />;
    case "zones": return (
      <div className="table-wrap"><table>
        <thead><tr><th>Zone name</th><th>Size (ft³)</th><th>Exterior fan here</th><th>Share of dampness (%)</th></tr></thead>
        <tbody>{cfg.zones.map((z: Json, i: number) => (
          <tr key={z.id}>
            <td><input value={z.name} onChange={(e) => change(`zones.${i}.name`, e.target.value)} /></td>
            <td><input type="number" step={100} style={{ width: 90 }} value={z.volume_ft3} onChange={(e) => change(`zones.${i}.volume_ft3`, Number(e.target.value))} /></td>
            <td><input type="checkbox" checked={z.on_ventilation_path} onChange={(e) => change(`zones.${i}.on_ventilation_path`, e.target.checked)} /></td>
            <td><input type="number" style={{ width: 70 }} value={Math.round(z.moisture_source_share * 100)} onChange={(e) => change(`zones.${i}.moisture_source_share`, Number(e.target.value) / 100)} /></td>
          </tr>))}</tbody>
      </table></div>);
    case "plugs": return <PlugEditor cfg={cfg} change={change} />;
    case "exhaust": return (<>
      {cfg.fans.map((f: Json, i: number) => (
        <div className="form" key={f.id}>
          <Text cfg={cfg} path={`fans.${i}.name`} label="Fan name" change={change} />
          <div className="field"><label>Blows</label>
            <select value={f.type} onChange={(e) => change(`fans.${i}.type`, e.target.value)}><option value="exhaust">Out of the basement (exhaust)</option><option value="intake">Into the basement (intake)</option></select></div>
          <Num cfg={cfg} path={`fans.${i}.rated_cfm`} label="Rated airflow (CFM)" change={change} hint="V2D: 2 × 110 = 220" />
          <Num cfg={cfg} path={`fans.${i}.actual_cfm`} label="Installed airflow estimate (CFM)" change={change} />
          <Num cfg={cfg} path={`fans.${i}.rated_w`} label="Power (W)" change={change} hint="V2D: about 40 W" />
          <Check cfg={cfg} path={`fans.${i}.motor_load_ok`} label="The plug is rated for this fan" change={change} />
        </div>))}
      <TestButtons kind="exhaust" after={after} />
    </>);
    case "circulators": return <CircEditor cfg={cfg} change={change} after={after} />;
    case "safety": return (
      <div className="form">
        <Check cfg={cfg} path="control.combustion_interlock" label="Block exhaust-only fan (chimney-vented gas appliance)" change={change} />
        <Check cfg={cfg} path="control.water_intrusion_lockout" label="Water lockout (only while there's standing water)" change={change} />
        <Num cfg={cfg} path="switches.hardware_max_on_minutes" nullable label="Plug safety timer (min)" change={change} hint="The plug turns itself off this long after ON unless the app keeps it going. 30 recommended." />
      </div>);
    case "targets": return (
      <div className="form">
        <Num cfg={cfg} path="control.target_rh_pct" label="Humidity target (%)" change={change} hint="50-55% is typical" />
        <Num cfg={cfg} path="control.upper_rh_pct" label="Upper humidity limit (%)" change={change} />
        <Num cfg={cfg} path="temperature.preferred_min_f" label="Preferred lowest temperature (°F)" change={change} />
        <Num cfg={cfg} path="temperature.preferred_max_f" label="Preferred highest temperature (°F)" change={change} />
        <Num cfg={cfg} path="temperature.min_allowed_f" label="Never cool below (°F)" change={change} />
        <Check cfg={cfg} path="dehumidifier.enabled" label="There's a dehumidifier in the basement" change={change} />
      </div>);
    default: return null;
  }
}

function SensorEditor({ cfg, change }: { cfg: Json; change: Change }) {
  const [disc, setDisc] = useState<Json[] | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => { setErr(""); api.discover().then((d) => setDisc(d.channels)).catch((e) => setErr(String(e.message))); }, []);
  useEffect(() => { if (cfg.sensors.provider !== "mock") load(); }, [load, cfg.sensors.provider]);
  const maps: Json[] = cfg.sensors.mappings;
  const live: Record<string, Json> = Object.fromEntries((disc || []).map((c) => [c.id, c]));
  const unmapped = (disc || []).filter((c) => !maps.some((m) => m.sensor_id === c.id));
  const roleOf = (id: string) => id === "ecowitt:outdoor" ? "outdoor" : id === "ecowitt:indoor" ? "upstairs" : id.startsWith("ecowitt:tf") ? "surface" : "basement";
  const add = (c: Json) => change("sensors.mappings", [...maps, {
    sensor_id: c.id, name: c.id.replace("ecowitt:", ""), role: roleOf(c.id),
    zone: roleOf(c.id) === "basement" || roleOf(c.id) === "surface" ? cfg.zones?.[0]?.id ?? null : null,
    temp_offset_f: 0, rh_offset_pct: 0, enabled: true }]);
  const mocks = maps.filter((m) => m.sensor_id.startsWith("mock-"));
  const fmt = (c: Json) => c ? `${c.temp_c != null ? (c.temp_c * 9 / 5 + 32).toFixed(1) + "°F" : "—"}${c.rh_pct != null ? ` · ${Math.round(c.rh_pct)}%` : ""}` : "no reading";
  return (<>
    <div className="row" style={{ marginBottom: 8 }}>
      <button className="btn" onClick={load}>Refresh live readings</button>
      {mocks.length > 0 && cfg.sensors.provider !== "mock" && <button className="btn" onClick={() => change("sensors.mappings", maps.filter((m) => !m.sensor_id.startsWith("mock-")))}>Remove {mocks.length} simulated sensors</button>}
      {err && <span className="error">{err}</span>}
    </div>
    <div className="table-wrap"><table>
      <thead><tr><th>Channel</th><th>Now</th><th>Name</th><th>Where is it?</th><th>Zone</th><th /></tr></thead>
      <tbody>
        {maps.map((m, i) => (
          <tr key={m.sensor_id}>
            <td className="faint">{m.sensor_id}</td>
            <td className="num">{disc ? fmt(live[m.sensor_id]) : "—"}</td>
            <td><input value={m.name} onChange={(e) => change(`sensors.mappings.${i}.name`, e.target.value)} /></td>
            <td><select value={m.role} onChange={(e) => change(`sensors.mappings.${i}.role`, e.target.value)}>
              <option value="outdoor">Outdoors</option><option value="basement">Basement air</option><option value="surface">Concrete probe</option>
              <option value="upstairs">Upstairs</option><option value="pipe">Next to a pipe</option><option value="ignore">Not used</option></select></td>
            <td>{(m.role === "basement" || m.role === "surface") ? (
              <select value={m.zone ?? ""} onChange={(e) => change(`sensors.mappings.${i}.zone`, e.target.value || null)}>
                <option value="">choose…</option>{cfg.zones.map((z: Json) => <option key={z.id} value={z.id}>{z.name}</option>)}</select>) : <span className="faint">—</span>}</td>
            <td><button className="btn" onClick={() => change("sensors.mappings", maps.filter((_, j) => j !== i))}>Remove</button></td>
          </tr>))}
        {unmapped.map((c) => (
          <tr key={c.id} className="su-new">
            <td>{c.id}</td><td className="num">{fmt(c)}</td><td colSpan={3} className="faint">New sensor, not in the list yet</td>
            <td><button className="btn primary" onClick={() => add(c)}>Add</button></td>
          </tr>))}
      </tbody>
    </table></div>
    <p className="faint">ecowitt:outdoor is the outdoor sensor, ecowitt:ch1…ch8 are the T/RH sensors by channel, ecowitt:tf1 is the concrete probe, ecowitt:indoor is the gateway's own sensor.</p>
  </>);
}

const PLUG_NAMES = ["exterior-fan", "circ-fan-1", "circ-fan-2"];

function PlugFinder({ devs, change }: { devs: Json[]; change: Change }) {
  const [found, setFound] = useState<Json[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const find = async () => {
    setBusy(true); setErr("");
    try { setFound((await api.discoverTapo()).plugs); } catch (e) { setErr(String((e as Error).message)); }
    setBusy(false);
  };
  const hosts = devs.map((d) => d.host);
  const add = (p: Json) => {
    const used = devs.map((d) => d.switch_id);
    const id = PLUG_NAMES.find((n) => !used.includes(n)) ?? `plug-${devs.length + 1}`;
    change("switches.tapo_devices", [...devs, { switch_id: id, host: p.host }]);
  };
  return (<>
    <div className="row" style={{ marginBottom: 8 }}>
      <button className="btn" onClick={find} disabled={busy}>{busy ? "Searching…" : "Find plugs on my Wi-Fi"}</button>
      {err && <span className="error">{err}</span>}
    </div>
    {found && found.length === 0 && <p className="note">No plugs answered. Check they're plugged in and on the same Wi-Fi as this computer, then try again.</p>}
    {found && found.length > 0 && (<>
      <div className="table-wrap"><table>
        <thead><tr><th>Found</th><th>Address</th><th>Now</th><th /></tr></thead>
        <tbody>{found.map((p) => (
          <tr key={p.host} className={hosts.includes(p.host) ? "" : "su-new"}>
            <td>{p.model ?? "plug"}{p.name ? ` · ${p.name}` : ""} <span className="faint">{p.mac ?? ""}</span></td>
            <td className="num">{p.host}</td>
            <td>{p.error ? <span className="error">{p.error}</span> : p.on ? "on" : "off"}</td>
            <td>{hosts.includes(p.host) ? <span className="faint">added</span> : <button className="btn primary" onClick={() => add(p)}>Add</button>}</td>
          </tr>))}</tbody>
      </table></div>
      <p className="faint">Not sure which plug is which? Press the button on one plug, then search again: the one whose on/off changed is that plug.</p>
    </>)}
  </>);
}

function PlugEditor({ cfg, change }: { cfg: Json; change: Change }) {
  const sw = cfg.switches;
  const devs: Json[] = sw.tapo_devices;
  const ids = devs.map((d) => d.switch_id);
  const plugSelect = (path: string, v: string) => (
    <select value={v} onChange={(e) => change(path, e.target.value)}>
      <option value="">choose a plug…</option>
      {v && !ids.includes(v) && <option value={v}>{v} (not in list)</option>}
      {ids.map((id) => <option key={id} value={id}>{id}</option>)}
    </select>);
  return (<>
    <div className="form">
      <div className="field"><label>Plug type</label>
        <select value={sw.provider} onChange={(e) => change("switches.provider", e.target.value)}>
          <option value="mock">Simulated (demo)</option><option value="tapo">TP-Link Tapo</option><option value="shelly">Shelly</option><option value="homeassistant">Home Assistant</option>
        </select></div>
      {sw.provider === "tapo" && <Text cfg={cfg} path="switches.tapo_username" label="TP-Link account email (usually leave empty)" change={change}
        hint="Only needed if you added the plugs in the Tapo app. Plugs set up with Apple Home need no account." />}
      {sw.provider === "tapo" && sw.tapo_username && <Text cfg={cfg} path="switches.tapo_password" label="TP-Link password" password change={change} />}
    </div>
    {sw.provider === "tapo" && (<>
      <h3 className="su-h3">Plugs</h3>
      <PlugFinder devs={devs} change={change} />
      <div className="table-wrap"><table>
        <thead><tr><th>Plug name</th><th>Address</th><th /></tr></thead>
        <tbody>{devs.map((d, i) => (
          <tr key={i}>
            <td><input value={d.switch_id} onChange={(e) => change(`switches.tapo_devices.${i}.switch_id`, e.target.value.replace(/\s+/g, "-"))} /></td>
            <td><input value={d.host} placeholder="192.168.0.60" onChange={(e) => change(`switches.tapo_devices.${i}.host`, e.target.value.trim())} /></td>
            <td><button className="btn" onClick={() => change("switches.tapo_devices", devs.filter((_, j) => j !== i))}>Remove</button></td>
          </tr>))}</tbody>
      </table></div>
      <button className="btn" style={{ marginTop: 6 }} onClick={() => change("switches.tapo_devices", [...devs, { switch_id: PLUG_NAMES[devs.length] ?? `plug-${devs.length + 1}`, host: "" }])}>Add plug</button>
      <h3 className="su-h3">Which plug powers which fan?</h3>
      <div className="form">
        {cfg.fans.map((f: Json, i: number) => <div className="field" key={f.id}><label>{f.name}</label>{plugSelect(`fans.${i}.switch_id`, f.switch_id)}</div>)}
        {cfg.circulators.map((c: Json, i: number) => <div className="field" key={c.id}><label>{c.name}</label>{plugSelect(`circulators.${i}.switch_id`, c.switch_id)}</div>)}
      </div>
    </>)}
    {sw.provider !== "tapo" && sw.provider !== "mock" && <p className="note">For {sw.provider}, set the plugs up on the Settings page, then come back and press Check again.</p>}
  </>);
}

function TestButtons({ kind, id, after }: { kind: "exhaust" | "circ"; id?: string; after: () => void }) {
  const [msg, setMsg] = useState("");
  const run = async (on: boolean) => {
    setMsg("");
    try {
      if (kind === "exhaust") await api.setMode(on ? "manual_on" : "off", on ? 2 : undefined);
      else await api.circulatorManual(id!, on ? "on" : "off", on ? 2 : 1);
      setMsg(on ? "Sent: the fan should start within a minute and stop after 2 minutes." : "Sent: the fan should stop within a minute.");
      setTimeout(after, 5000);
    } catch (e) { setMsg(String((e as Error).message)); }
  };
  return (
    <div className="row su-test">
      <button className="btn" onClick={() => run(true)}><Icon name="fan" /> Run 2 minutes</button>
      <button className="btn" onClick={() => run(false)}>Stop now</button>
      {msg && <span className="faint">{msg}</span>}
    </div>);
}

function CircEditor({ cfg, change, after }: { cfg: Json; change: Change; after: () => void }) {
  return (<>
    {cfg.circulators.map((c: Json, i: number) => (
      <div key={c.id} className="su-circ">
        <div className="form">
          <Text cfg={cfg} path={`circulators.${i}.name`} label="Name" change={change} />
          <Text cfg={cfg} path={`circulators.${i}.location`} label="Where it is" change={change} placeholder="e.g. doorway to back room" />
          <div className="field"><label>Speed set on the fan</label>
            <select value={c.physical_speed} onChange={(e) => change(`circulators.${i}.physical_speed`, e.target.value)}>
              <option value="unknown">not chosen</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></div>
          <Num cfg={cfg} path={`circulators.${i}.rated_w`} label="Power at that speed (W)" change={change} hint="HT-900: about 20 / 30 / 40 W" />
        </div>
        <div className="row">
          <TestButtons kind="circ" id={c.id} after={after} />
          <button className={`btn ${c.commissioned ? "on" : ""}`} onClick={() => change(`circulators.${i}.commissioned`, !c.commissioned)}>
            {c.commissioned ? "Commissioned ✓" : "Mark commissioned"}</button>
        </div>
      </div>))}
    <div className="form">
      <div className="field"><label>Circulation automation</label>
        <select value={cfg.circulation.mode} onChange={(e) => change("circulation.mode", e.target.value)}>
          <option value="off">Off</option><option value="shadow">Shadow: record only (recommended to start)</option><option value="auto">Automatic</option></select></div>
    </div>
  </>);
}

/* ---------- checks ---------- */
const GAS: [string, string][] = [
  ["none", "No gas or oil appliance in the basement"],
  ["sealed", "Yes, but it's sealed (plastic pipe vents straight outside)"],
  ["natural", "Yes, it vents up a chimney or metal flue"],
  ["tested", "Chimney-vented, and a technician passed a backdraft test with the fan on"],
];

function Checks({ checks, answers, answer }: { checks: Json[]; answers: Json; answer: (k: string, v: Json) => void }) {
  return (
    <ul className="su-checks">
      {checks.map((c) => (
        <li key={c.id} className={`su-c ${c.status}`}>
          <span className="su-ic">{c.status === "ok" ? <Icon name="check" /> : c.status === "fail" ? <Icon name="x" /> : c.status === "warn" ? <Icon name="alert" /> : <Icon name="dash" />}</span>
          <div>
            {c.ask === "confirm" ? (
              <label className="su-confirm"><input type="checkbox" checked={answers[c.id] === true} onChange={(e) => answer(c.id, e.target.checked)} /> {c.label}</label>
            ) : <span className="su-label">{c.label}</span>}
            {c.detail && <div className="su-detail">{c.detail}</div>}
            {c.ask === "gas" && (
              <div className="su-gas">{GAS.map(([k, l]) => (
                <label key={k} className="row"><input type="radio" name="gas" checked={answers.gas_appliance === k} onChange={() => answer("gas_appliance", k)} /> {l}</label>))}</div>
            )}
          </div>
        </li>))}
    </ul>);
}

/* ---------- page ---------- */
export default function Setup({ onSaved }: { onSaved: () => void }) {
  const [data, setData] = useState<Json>(null);
  const [cfg, setCfg] = useState<Json>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [view, setView] = useState<"walk" | "sheet">("walk");

  const recheck = useCallback(async () => {
    setBusy(true);
    try { setData(await api.setup(true)); } catch (e) { setErr(String((e as Error).message)); }
    setBusy(false);
  }, []);
  useEffect(() => {
    api.setup(false).then((d) => { setData(d); recheck(); });
    api.config().then(setCfg);
  }, [recheck]);

  const current = data?.progress?.current_step;
  useEffect(() => {
    document.querySelector(".su-step.active")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [current, view]);

  if (!data || !cfg) return <Card>Loading setup…</Card>;
  if (view === "sheet") return <Worksheet cfg={cfg} progress={data.progress} back={() => setView("walk")} onAnswers={(a) => api.setupProgress({ answers: a }).then(setData)} />;

  const p = data.progress;
  const steps: Json[] = data.steps;
  const idx = Math.max(0, steps.findIndex((s) => s.id === p.current_step));
  const step = steps[idx];
  const change: Change = (path, v) => { setCfg(setIn(cfg, path, v)); setDirty(true); };
  const progress = (body: Json) => api.setupProgress(body).then(setData).catch((e) => setErr(String(e.message)));

  async function saveConfig(): Promise<boolean> {
    if (!dirty) return true;
    setErr("");
    try { setCfg(await api.saveConfig(cfg)); setDirty(false); onSaved(); return true; }
    catch (e) { setErr(String((e as Error).message)); return false; }
  }
  async function saveAndCheck() {
    if (await saveConfig()) { setTimeout(recheck, cfg.sensors.provider === "mock" ? 0 : 1500); }
  }
  async function go(to: number, mark?: "done" | "skipped") {
    if (!(await saveConfig())) return;
    const body: Json = { current_step: steps[Math.min(Math.max(to, 0), steps.length - 1)].id, paused: false };
    if (mark) body.mark = { step: step.id, state: mark };
    await progress(body);
    window.scrollTo({ top: 0, behavior: "smooth" });
    recheck();
  }
  async function pause() {
    if (!(await saveConfig())) return;
    await progress({ paused: true, current_step: step.id });
  }

  const pct = Math.round((data.done / data.total) * 100);
  const last = idx === steps.length - 1;

  return (
    <div className="setup">
      <Card title="Guided setup" right={<div className="row">
        <button className="btn" onClick={() => setView("sheet")}>Worksheet</button>
        <a className="btn" href={GUIDE_URL} target="_blank" rel="noreferrer">Setup guide</a>
        <button className="btn" onClick={pause}>Pause</button>
      </div>}>
        <div className="su-bar"><div className="su-fill" style={{ width: `${pct}%` }} /></div>
        <p className="faint" style={{ margin: "6px 0 0" }}>{data.finished ? "Setup finished. You can come back here any time to re-check things." :
          `${data.done} of ${data.total} steps done. Progress is saved on the controller, so you can stop and carry on later from any device.`}</p>
        {p.paused && !data.finished && <div className="alert info su-paused"><span className="icon"><Icon name="info" /></span>
          <div><strong>Setup is paused.</strong> Everything so far is saved. Pick up where you left off whenever you're ready.
            <div style={{ marginTop: 6 }}><button className="btn primary" onClick={() => progress({ paused: false })}>Resume</button></div></div></div>}
      </Card>

      <div className="su-layout">
        <nav className="su-steps" aria-label="Setup steps">
          {steps.map((s, i) => (
            <button key={s.id} className={`su-step ${i === idx ? "active" : ""} ${s.state || ""} ${s.ready ? "ready" : ""}`} onClick={() => go(i)}>
              <span className="su-num">{s.state === "done" ? <Icon name="check" size={14} /> : i + 1}</span>
              <span className="su-st">{s.title}<small>{s.state === "skipped" ? "skipped" : s.state === "done" ? "done" : s.ready ? "ready" : s.counts.fail ? `${s.counts.fail} to fix` : s.counts.todo ? `${s.counts.todo} to confirm` : ""}</small></span>
            </button>))}
        </nav>

        <Card className="su-panel" title={<>Step {idx + 1}: {step.title}</>} right={<button className="btn" disabled={busy} onClick={saveAndCheck}>{busy ? "Checking…" : dirty ? "Save and check" : "Check again"}</button>}>
          <div className="su-howto">{HOWTO[step.id]}</div>
          <Editor step={step.id} cfg={cfg} change={change} after={recheck} />
          {dirty && <p className="su-unsaved">You have unsaved changes. Press <b>Save and check</b>.</p>}
          <h3 className="su-h3">Checklist</h3>
          <Checks checks={step.checks} answers={p.answers} answer={(k, v) => progress({ answers: { [k]: v } })} />
          {step.id === "golive" && (
            <div className="row" style={{ margin: "10px 0" }}>
              <button className="btn primary" disabled={!steps.slice(0, -1).every((s) => s.ready || s.state)} onClick={async () => { await api.setMode("auto"); onSaved(); recheck(); }}>Switch to Automatic</button>
              <button className="btn" onClick={() => progress({ finished: true, mark: { step: "golive", state: "done" } })}>Finish setup</button>
            </div>)}
          {err && <p className="error" style={{ whiteSpace: "pre-wrap" }}>{err}</p>}
          <div className="su-nav">
            <button className="btn" disabled={idx === 0} onClick={() => go(idx - 1)}>Back</button>
            <div className="spacer" />
            {!step.ready && !last && <button className="btn" onClick={() => go(idx + 1, "skipped")}>Skip for now</button>}
            {!last && <button className="btn primary" disabled={!step.ready} title={step.ready ? "" : "Fix or confirm the items in the checklist first"} onClick={() => go(idx + 1, "done")}>Done, next step</button>}
          </div>
        </Card>
      </div>
    </div>
  );
}
