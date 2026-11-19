/* Settings: zones, zone connections, circulation fans (Version 3). */
import { Card } from "../components";
import { Json } from "../api";

type Change = (p: string, v: Json) => void;

const num = (v: string) => (v === "" ? 0 : Number(v));

const ROLES: [string, string][] = [
  ["zone_exchange", "Swap air between zones"],
  ["surface_drying", "Dry a damp surface"],
  ["general_mixing", "Stir one room"],
  ["dehumidifier_support", "Help the dehumidifier"],
];

function newCirc(circs: Json[], kind: "fan" | "blower"): Json {
  let n = circs.length + 1;
  const base = kind === "blower" ? "blower" : "circ";
  while (circs.some((c) => c.id === `${base}-${n}`)) n++;
  return {
    id: `${base}-${n}`, name: kind === "blower" ? `Blower ${n}` : `Circulation fan ${n}`, kind, role: "zone_exchange", zone: null,
    min_on_minutes: null, min_off_minutes: null, model: kind === "blower" ? "Blower" : "Honeywell HT-900", switch_id: "",
    location: "", physical_speed: "unknown", rated_w: kind === "blower" ? 80 : 30, ventilation_support: false, auto_enabled: true,
    commissioned: false, enabled: true,
  };
}

export function ZoneSettings({ cfg, change }: { cfg: Json; change: Change }) {
  const zones: Json[] = cfg.zones || [];
  const conns: Json[] = cfg.connections || [];
  const circs: Json[] = cfg.circulators || [];
  const mixFans: string[] = cfg.circulation?.mixing_fans || [];
  const zoneOpts = zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>);
  const plugs: string[] = [
    ...(cfg.switches.tapo_devices || []).map((d: Json) => d.switch_id),
    ...(cfg.switches.shelly_devices || []).map((d: Json) => d.switch_id),
    ...(cfg.switches.provider === "mock" ? ["mock-plug-1", "mock-plug-2", "mock-plug-3", "mock-plug-4"] : []),
  ];
  return (
    <>
      <Card title="Zones">
        <p className="note">A zone is one part of the basement with its own air sensor. Rename zones freely; assign sensors to a zone
          in the Sensors table below. Volumes and exposure are estimates used only for predictions and simulation.</p>
        <div className="table-wrap"><table>
          <thead><tr><th>Id</th><th>Name</th><th>Volume (ft³)</th><th>On exterior-fan air path</th><th>Ventilation exposure (0–1)</th>
            <th>Share of moisture sources</th><th>Notes</th><th /></tr></thead>
          <tbody>{zones.map((z, i) => (
            <tr key={i}>
              <td className="faint">{z.id}</td>
              <td><input value={z.name} onChange={(e) => change(`zones.${i}.name`, e.target.value)} /></td>
              <td><input type="number" step={100} style={{ width: 80 }} value={z.volume_ft3} onChange={(e) => change(`zones.${i}.volume_ft3`, num(e.target.value))} /></td>
              <td><input type="checkbox" checked={z.on_ventilation_path} onChange={(e) => change(`zones.${i}.on_ventilation_path`, e.target.checked)} /></td>
              <td><input type="number" step={0.05} style={{ width: 70 }} value={z.ventilation_exposure} onChange={(e) => change(`zones.${i}.ventilation_exposure`, num(e.target.value))} /></td>
              <td><input type="number" step={0.05} style={{ width: 70 }} value={z.moisture_source_share} onChange={(e) => change(`zones.${i}.moisture_source_share`, num(e.target.value))} /></td>
              <td><input value={z.notes} onChange={(e) => change(`zones.${i}.notes`, e.target.value)} /></td>
              <td><button className="btn" disabled={conns.some((k) => k.zone_a === z.id || k.zone_b === z.id)}
                title="Remove its connections first" onClick={() => change("zones", zones.filter((_, j) => j !== i))}>Remove</button></td>
            </tr>
          ))}</tbody>
        </table></div>
        <button className="btn" style={{ marginTop: 8 }} onClick={() => change("zones", [...zones, {
          id: `zone_${zones.length + 1}`, name: `Zone ${zones.length + 1}`, volume_ft3: 4000, on_ventilation_path: false,
          ventilation_exposure: 0.4, moisture_source_share: 0.2, notes: "" }])}>Add zone</button>
      </Card>

      <Card title="Zone connections">
        <p className="note">How zones connect. This is the intended circulation, not proven airflow: a small fan in an open room does not
          create a predictable flow from one whole room to another. Exchange rates are assumptions until the effectiveness
          analysis (History page) has data.</p>
        <div className="table-wrap"><table>
          <thead><tr><th>Id</th><th>Zone A</th><th>Zone B</th><th>Fan</th><th>Direction</th><th>Exchange, fan off (CFM)</th><th>Exchange, fan on (CFM)</th>
            <th>Opening clear</th><th>Fan placement</th><th /></tr></thead>
          <tbody>{conns.map((k, i) => (
            <tr key={i}>
              <td className="faint">{k.id}</td>
              <td><select value={k.zone_a} onChange={(e) => change(`connections.${i}.zone_a`, e.target.value)}>{zoneOpts}</select></td>
              <td><select value={k.zone_b} onChange={(e) => change(`connections.${i}.zone_b`, e.target.value)}>{zoneOpts}</select></td>
              <td><select value={k.circulator_id ?? ""} onChange={(e) => change(`connections.${i}.circulator_id`, e.target.value || null)}>
                <option value="">none</option>{circs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></td>
              <td><select value={k.direction} onChange={(e) => change(`connections.${i}.direction`, e.target.value)}>
                <option value="a_to_b">A → B</option><option value="b_to_a">B → A</option><option value="mixing">mixing</option></select></td>
              <td><input type="number" style={{ width: 70 }} value={k.natural_cfm} onChange={(e) => change(`connections.${i}.natural_cfm`, num(e.target.value))} /></td>
              <td><input type="number" style={{ width: 70 }} value={k.fan_exchange_cfm} onChange={(e) => change(`connections.${i}.fan_exchange_cfm`, num(e.target.value))} /></td>
              <td><input type="checkbox" checked={k.unobstructed} onChange={(e) => change(`connections.${i}.unobstructed`, e.target.checked)} /></td>
              <td><select value={k.placement} onChange={(e) => change(`connections.${i}.placement`, e.target.value)}>
                <option value="doorway">doorway</option><option value="corridor">corridor</option><option value="in_zone">within a zone</option></select></td>
              <td><button className="btn" onClick={() => change("connections", conns.filter((_, j) => j !== i))}>Remove</button></td>
            </tr>
          ))}</tbody>
        </table></div>
        {zones.length >= 2 && <button className="btn" style={{ marginTop: 8 }} onClick={() => change("connections", [...conns, {
          id: `conn_${conns.length + 1}`, zone_a: zones[0].id, zone_b: zones[1].id, circulator_id: null, direction: "mixing",
          natural_cfm: 15, fan_exchange_cfm: 60, unobstructed: true, placement: "doorway" }])}>Add connection</button>}
      </Card>

      <Card title="Circulation fans">
        <p className="note">ON/OFF only: set the speed on the fan itself. Automatic control stays off for a fan until it is marked commissioned.
          Before ticking "Commissioned", check that: the fan runs when plugged straight into an outlet; its speed switch stays where you set it;
          it restarts by itself when power returns (switch the plug off and on); the plug is rated for a motor load; and power cycling causes
          nothing unexpected.</p>
        <p className="note"><strong>Water safety:</strong> do not energize fans or plugs that are in or near standing water. Keep them above
          known flood levels, use GFCI-protected outlets, and remember a smart plug does not make a household fan suitable for wet locations.
          The water-intrusion lockout (Safety interlocks) turns every fan off, including manual overrides.</p>
        <div className="table-wrap"><table>
          <thead><tr><th>Id</th><th>Name</th><th>Type</th><th>Job</th><th>Sits in zone</th><th>Location</th><th>Model</th><th>Smart plug</th><th>Speed set on fan</th><th>Watts</th>
            <th>Ventilation support</th><th>Timed cycling</th><th>Automatic allowed</th><th>Commissioned</th><th>Enabled</th><th /></tr></thead>
          <tbody>{circs.map((c, i) => (
            <tr key={i}>
              <td className="faint">{c.id}</td>
              <td><input value={c.name} style={{ width: 140 }} onChange={(e) => change(`circulators.${i}.name`, e.target.value)} /></td>
              <td><select value={c.kind || "fan"} onChange={(e) => change(`circulators.${i}.kind`, e.target.value)}>
                <option value="fan">Fan</option><option value="blower">Blower</option></select></td>
              <td><select value={c.role || "zone_exchange"} onChange={(e) => change(`circulators.${i}.role`, e.target.value)}>
                {ROLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
              <td><select value={c.zone ?? ""} onChange={(e) => change(`circulators.${i}.zone`, e.target.value || null)}>
                <option value="">—</option>{zoneOpts}</select></td>
              <td><input value={c.location} style={{ width: 130 }} onChange={(e) => change(`circulators.${i}.location`, e.target.value)} /></td>
              <td><input value={c.model} style={{ width: 130 }} onChange={(e) => change(`circulators.${i}.model`, e.target.value)} /></td>
              <td><input value={c.switch_id} list="plug-ids" style={{ width: 110 }} onChange={(e) => change(`circulators.${i}.switch_id`, e.target.value)} /></td>
              <td><select value={c.physical_speed} onChange={(e) => change(`circulators.${i}.physical_speed`, e.target.value)}>
                {["low", "medium", "high", "unknown"].map((s) => <option key={s}>{s}</option>)}</select></td>
              <td><input type="number" style={{ width: 60 }} value={c.rated_w} onChange={(e) => change(`circulators.${i}.rated_w`, num(e.target.value))} /></td>
              <td><input type="checkbox" checked={c.ventilation_support} onChange={(e) => change(`circulators.${i}.ventilation_support`, e.target.checked)} /></td>
              <td><input type="checkbox" checked={!mixFans.length || mixFans.includes(c.id)} onChange={(e) => {
                const cur: string[] = mixFans.length ? mixFans : circs.map((x) => x.id);
                const next = e.target.checked ? [...cur.filter((x) => x !== c.id), c.id] : cur.filter((x) => x !== c.id);
                // every fan chosen = the empty list (includes fans added later); none chosen keeps an impossible id
                change("circulation.mixing_fans", circs.every((x) => next.includes(x.id)) ? [] : next.length ? next : ["none"]);
              }} /></td>
              <td><input type="checkbox" checked={c.auto_enabled} onChange={(e) => change(`circulators.${i}.auto_enabled`, e.target.checked)} /></td>
              <td><input type="checkbox" checked={c.commissioned} onChange={(e) => change(`circulators.${i}.commissioned`, e.target.checked)} /></td>
              <td><input type="checkbox" checked={c.enabled} onChange={(e) => change(`circulators.${i}.enabled`, e.target.checked)} /></td>
              <td><button className="btn" disabled={conns.some((k) => k.circulator_id === c.id)} title="Unassign it from connections first"
                onClick={() => change("circulators", circs.filter((_, j) => j !== i))}>Remove</button></td>
            </tr>
          ))}</tbody>
        </table></div>
        <datalist id="plug-ids">{plugs.map((p) => <option key={p} value={p} />)}</datalist>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" onClick={() => change("circulators", [...circs, newCirc(circs, "fan")])}>Add circulation fan</button>
          <button className="btn" onClick={() => change("circulators", [...circs, newCirc(circs, "blower")])}>Add blower</button>
        </div>
        <p className="faint"><strong>Jobs:</strong> <em>Swap air between zones</em> runs when one zone holds noticeably more water
          (by dew point, not RH) than the zone it connects to. <em>Dry a damp surface</em> blows across concrete, so in automatic it only runs
          with a working concrete probe nearby and the surface comfortably above its dew point. <em>Stir one room</em> needs "Sits in zone" and
          runs with the exhaust fans or on the mixing schedule. <em>Help the dehumidifier</em> runs only when the app can see the dehumidifier
          running (a power-metering plug); with the Tapo P125M plugs it cannot, so it falls back to humidity readings.</p>
        <p className="faint">Each circulation fan needs its own plug, separate from the exterior fans. Add the plug under Hardware first.</p>
        <label className="row" style={{ marginTop: 8 }}>Dehumidifier draws its air from
          <select value={cfg.dehumidifier?.zone ?? ""} onChange={(e) => change("dehumidifier.zone", e.target.value || null)}>
            <option value="">—</option>{zoneOpts}</select>
          <span className="faint">(used by "Help the dehumidifier" fans)</span></label>
      </Card>
    </>
  );
}
