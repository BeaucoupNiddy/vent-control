/* Setup worksheet: fill it in on screen (saved on the controller) or print it and write on it. */
import { useState } from "react";
import { Json } from "../api";

type Row = { key: string; label: string; hint?: string };

const SENSORS: Row[] = [
  { key: "outdoor", label: "Outdoor", hint: "Shade, under an eave, ~5 ft up" },
  { key: "main", label: "Main basement", hint: "Near the exterior fan's zone, chest height" },
  { key: "pocket", label: "Remote pocket", hint: "The area farthest from the fan" },
  { key: "damp", label: "Moisture-prone area", hint: "Where it smells or feels dampest" },
  { key: "probe", label: "Concrete probe (WN30BL)", hint: "Coldest outside wall, ~1 ft above floor" },
  { key: "upstairs", label: "Upstairs (gateway's own sensor)", hint: "Optional" },
];
const PLUGS = ["1", "2", "3", "4", "5", "6"];

export default function Worksheet({ cfg, progress, back, onAnswers }: { cfg: Json; progress: Json; back: () => void; onAnswers: (a: Json) => void }) {
  const [a, setA] = useState<Json>(progress.answers || {});
  const v = (k: string) => a[`ws_${k}`] ?? "";
  const set = (k: string, val: string) => setA({ ...a, [`ws_${k}`]: val });
  const save = (k: string) => { if ((progress.answers || {})[`ws_${k}`] !== a[`ws_${k}`]) onAnswers({ [`ws_${k}`]: a[`ws_${k}`] ?? "" }); };
  const In = ({ k, w, ph }: { k: string; w?: number; ph?: string }) => (
    <input className="ws-in" style={w ? { width: w } : undefined} value={v(k)} placeholder={ph} onChange={(e) => set(k, e.target.value)} onBlur={() => save(k)} />);
  const zones: Json[] = cfg.zones || [];

  return (
    <div className="worksheet">
      <div className="row ws-tools">
        <button className="btn" onClick={back}>← Back to setup</button>
        <button className="btn primary" onClick={() => window.print()}>Print</button>
        <span className="faint">Fill this in on screen (it saves as you go) or print it and write on it. Then use it in the walkthrough.</span>
      </div>

      <h1>Basement ventilation setup worksheet</h1>
      <p className="ws-sub">Write down each item as you install it. Never write your TP-Link password here.</p>

      <section>
        <h2>1. Network</h2>
        <table className="ws-t"><tbody>
          <tr><th>Wi-Fi name (2.4 GHz)</th><td>{In({ k: "wifi" })}</td><th>Router app</th><td>{In({ k: "router", ph: "e.g. Eero, Xfinity" })}</td></tr>
          <tr><th>This computer's address</th><td>{In({ k: "mac_ip", ph: location.hostname })}</td><th>Set to never sleep?</th><td>{In({ k: "mac_sleep", w: 60, ph: "Y / N" })}</td></tr>
          <tr><th>TP-Link account email</th><td colSpan={3}>{In({ k: "tplink_email" })}</td></tr>
        </tbody></table>
      </section>

      <section>
        <h2>2. Ecowitt GW1200 gateway</h2>
        <table className="ws-t"><tbody>
          <tr><th>Where it is</th><td>{In({ k: "gw_loc", ph: "upstairs, near router" })}</td><th>Address (IP)</th><td>{In({ k: "gw_ip", ph: "192.168.0.__" })}</td></tr>
          <tr><th>Address reserved in router?</th><td>{In({ k: "gw_reserved", w: 60, ph: "Y / N" })}</td><th>Firmware version</th><td>{In({ k: "gw_fw" })}</td></tr>
        </tbody></table>
      </section>

      <section>
        <h2>3. Sensors</h2>
        <p className="ws-note">Set each sensor's channel switch (inside the battery door) before putting batteries in. Use a different channel for each.</p>
        <table className="ws-t ws-grid"><thead><tr><th>Location</th><th>Model</th><th>Channel</th><th>Exact spot</th><th>Batteries in (date)</th></tr></thead>
          <tbody>{SENSORS.map((s) => (
            <tr key={s.key}><th>{s.label}<small>{s.hint}</small></th><td>{In({ k: `s_${s.key}_model`, w: 90, ph: s.key === "probe" ? "WN30BL" : "WN31" })}</td>
              <td>{In({ k: `s_${s.key}_ch`, w: 60, ph: "CH_" })}</td><td>{In({ k: `s_${s.key}_spot` })}</td><td>{In({ k: `s_${s.key}_batt`, w: 90 })}</td></tr>))}
          </tbody></table>
      </section>

      <section>
        <h2>4. Smart plugs (Tapo P125M)</h2>
        <p className="ws-note">The setup page's "Find plugs on my Wi-Fi" shows each plug's address (IP) and MAC. Reserve each address in your router.</p>
        <table className="ws-t ws-grid"><thead><tr><th>#</th><th>Name in this app</th><th>Powers</th><th>Address (IP)</th><th>MAC</th><th>Reserved?</th></tr></thead>
          <tbody>{PLUGS.map((n) => (
            <tr key={n}><th>{n}</th><td>{In({ k: `p${n}_name`, ph: ["exhaust-fan-1", "exhaust-fan-2", "circ-fan-1", "circ-fan-2"][Number(n) - 1] ?? "spare" })}</td>
              <td>{In({ k: `p${n}_powers`, ph: ["Tjernlund exhaust #1", "Tjernlund exhaust #2", "Honeywell HT-900 #1", "Honeywell HT-900 #2"][Number(n) - 1] ?? "" })}</td>
              <td>{In({ k: `p${n}_ip`, w: 120 })}</td><td>{In({ k: `p${n}_mac`, w: 130 })}</td><td>{In({ k: `p${n}_res`, w: 50 })}</td></tr>))}
          </tbody></table>
      </section>

      <section>
        <h2>5. Fans</h2>
        <table className="ws-t ws-grid"><thead><tr><th>Fan</th><th>Where</th><th>Blows from → to</th><th>Speed / airflow</th><th>Watts</th></tr></thead>
          <tbody>
            <tr><th>Tjernlund exhaust #1</th><td>{In({ k: "f_v2d_loc", ph: "which foundation vent" })}</td><td>{In({ k: "f_v2d_dir", ph: "basement → outside" })}</td><td>{In({ k: "f_v2d_cfm", w: 90, ph: "CFM" })}</td><td>{In({ k: "f_v2d_w", w: 60, ph: "W" })}</td></tr>
            <tr><th>Tjernlund exhaust #2</th><td>{In({ k: "f_v2d2_loc", ph: "which foundation vent" })}</td><td>{In({ k: "f_v2d2_dir", ph: "basement → outside" })}</td><td>{In({ k: "f_v2d2_cfm", w: 90, ph: "CFM" })}</td><td>{In({ k: "f_v2d2_w", w: 60, ph: "W" })}</td></tr>
            <tr><th>Honeywell HT-900 #1</th><td>{In({ k: "f_c1_loc" })}</td><td>{In({ k: "f_c1_dir" })}</td><td>{In({ k: "f_c1_speed", w: 90, ph: "Low/Med/High" })}</td><td>{In({ k: "f_c1_w", w: 60 })}</td></tr>
            <tr><th>Honeywell HT-900 #2</th><td>{In({ k: "f_c2_loc" })}</td><td>{In({ k: "f_c2_dir" })}</td><td>{In({ k: "f_c2_speed", w: 90, ph: "Low/Med/High" })}</td><td>{In({ k: "f_c2_w", w: 60 })}</td></tr>
          </tbody></table>
      </section>

      <section>
        <h2>6. Basement zones</h2>
        <table className="ws-t ws-grid"><thead><tr><th>Zone</th><th>Your name for it</th><th>Floor area (ft²)</th><th>Ceiling height (ft)</th><th>Volume = area × height</th></tr></thead>
          <tbody>{(zones.length ? zones : [{ id: "main", name: "Main basement" }, { id: "pocket", name: "Remote pocket" }, { id: "damp", name: "Moisture-prone area" }]).map((z: Json) => (
            <tr key={z.id}><th>{z.name}</th><td>{In({ k: `z_${z.id}_name` })}</td><td>{In({ k: `z_${z.id}_area`, w: 90 })}</td><td>{In({ k: `z_${z.id}_h`, w: 70 })}</td><td>{In({ k: `z_${z.id}_vol`, w: 110 })}</td></tr>))}
            <tr><th>Whole basement</th><td /><td>{In({ k: "z_total_area", w: 90, ph: "3,000" })}</td><td>{In({ k: "z_total_h", w: 70, ph: "8" })}</td><td>{In({ k: "z_total_vol", w: 110, ph: "24,000" })}</td></tr>
          </tbody></table>
      </section>

      <section>
        <h2>7. Safety</h2>
        <table className="ws-t"><tbody>
          <tr><th>Gas/oil appliances in basement</th><td colSpan={3}>{In({ k: "gas_list", ph: "water heater, furnace…" })}</td></tr>
          <tr><th>How they vent</th><td colSpan={3}>{In({ k: "gas_vent", ph: "chimney / metal flue / sealed plastic pipe" })}</td></tr>
          <tr><th>CO alarm in basement?</th><td>{In({ k: "co", w: 60, ph: "Y / N" })}</td><th>Flood vents kept clear?</th><td>{In({ k: "flood", w: 60, ph: "Y / N" })}</td></tr>
          <tr><th>Any standing water now?</th><td>{In({ k: "water", w: 60, ph: "Y / N" })}</td><th>Dehumidifier?</th><td>{In({ k: "dehum", ph: "model, setpoint" })}</td></tr>
        </tbody></table>
      </section>

      <section>
        <h2>8. Notes</h2>
        <textarea className="ws-notes" rows={5} value={v("notes")} onChange={(e) => set("notes", e.target.value)} onBlur={() => save("notes")} />
      </section>
    </div>
  );
}
