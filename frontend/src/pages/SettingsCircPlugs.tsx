/* Settings: pick which smart plug powers each circulation fan, and add a new Tapo plug by its IP address. */
import { useState } from "react";
import { api, Json } from "../api";

type Change = (p: string, v: Json) => void;

const IP_RE = /^\d{1,3}(\.\d{1,3}){3}$/;

/** Plugs already set up in Hardware, with their address and who uses them. */
export function plugList(cfg: Json) {
  const sw = cfg.switches || {};
  const used: Record<string, string> = {};
  (cfg.fans || []).forEach((f: Json) => { if (f.switch_id) used[f.switch_id] = f.name; });
  (cfg.circulators || []).forEach((c: Json) => { if (c.switch_id) used[c.switch_id] = c.name; });
  if (cfg.dehumidifier?.switch_id) used[cfg.dehumidifier.switch_id] = "Dehumidifier";
  const devs: Json[] = [...(sw.tapo_devices || []), ...(sw.shelly_devices || [])];
  if (sw.provider === "mock") ["mock-plug-1", "mock-plug-2", "mock-plug-3", "mock-plug-4"].forEach((id) => devs.push({ switch_id: id, host: "" }));
  return { devs, used };
}

export function CircPlugSelect({ cfg, i, change }: { cfg: Json; i: number; change: Change }) {
  const { devs, used } = plugList(cfg);
  const c = cfg.circulators[i];
  const v: string = c.switch_id || "";
  return (
    <select value={v} aria-label={`Smart plug for ${c.name}`} onChange={(e) => change(`circulators.${i}.switch_id`, e.target.value)}>
      <option value="">No plug</option>
      {v && !devs.some((d) => d.switch_id === v) && <option value={v}>{v} (not in Hardware list)</option>}
      {devs.map((d) => {
        const by = used[d.switch_id];
        const other = by && d.switch_id !== v;
        return <option key={d.switch_id} value={d.switch_id} disabled={!!other}>
          {d.switch_id}{d.host ? ` (${d.host})` : ""}{other ? ` · used by ${by}` : ""}</option>;
      })}
    </select>
  );
}

export function CircPlugs({ cfg, change }: { cfg: Json; change: Change }) {
  const circs: Json[] = cfg.circulators || [];
  const sw = cfg.switches || {};
  const tapo: Json[] = sw.tapo_devices || [];
  const firstFree = circs.findIndex((c) => c.enabled && !c.switch_id);
  const [ip, setIp] = useState("");
  const [forFan, setForFan] = useState<string>("");
  const [msg, setMsg] = useState("");
  const [found, setFound] = useState<Json[] | null>(null);
  const [busy, setBusy] = useState(false);
  const target = forFan || (firstFree >= 0 ? circs[firstFree].id : "");

  const addPlug = (host: string, fanId: string) => {
    host = host.trim();
    if (!IP_RE.test(host)) { setMsg("Type the plug's IP address, like 192.168.1.29"); return; }
    const existing = tapo.find((d) => (d.host || "").trim() === host);
    let id = existing?.switch_id;
    if (!id) {
      const taken = new Set(plugList(cfg).devs.map((d) => d.switch_id));
      let n = 1;
      while (taken.has(`circ-fan-${n}`)) n++;
      id = `circ-fan-${n}`;
      change("switches.tapo_devices", [...tapo, { switch_id: id, host }]);
    }
    const fi = circs.findIndex((c) => c.id === fanId);
    if (fi >= 0) change(`circulators.${fi}.switch_id`, id);
    setMsg(`${existing ? "Plug already listed" : "Added plug"} ${id} (${host})${fi >= 0 ? ` for ${circs[fi].name}` : ""}. Press Save settings to keep it.`);
    setIp(""); setForFan("");
  };

  const find = async () => {
    setBusy(true); setMsg("");
    try { setFound((await api.discoverTapo()).plugs); } catch (e) { setMsg(String((e as Error).message)); }
    setBusy(false);
  };
  const known = new Set(tapo.map((d) => (d.host || "").trim()));

  return (
    <div className="circ-plugs">
      <h4 style={{ margin: "4px 0 8px" }}>Which plug powers each fan</h4>
      <div className="form">
        {circs.map((c, i) => (
          <div className="field" key={c.id}>
            <label>{c.name}{!c.enabled && " (disabled)"}</label>
            <CircPlugSelect cfg={cfg} i={i} change={change} />
          </div>
        ))}
      </div>
      {sw.provider === "tapo" && (<>
        <h4 style={{ margin: "16px 0 8px" }}>Add a new Tapo plug</h4>
        <p className="faint" style={{ marginTop: 0 }}>Find the plug's IP address in the Tapo app (the plug → gear icon → Device info), or press Find plugs on my Wi-Fi.</p>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <input value={ip} placeholder="192.168.1.29" inputMode="decimal" aria-label="New plug IP address" style={{ width: 150 }}
            onChange={(e) => setIp(e.target.value)} />
          <label className="row" style={{ gap: 6 }}>for
            <select value={target} onChange={(e) => setForFan(e.target.value)}>
              <option value="">no fan yet</option>
              {circs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select></label>
          <button className="btn primary" onClick={() => addPlug(ip, target)}>Add this plug</button>
          <button className="btn" onClick={find} disabled={busy}>{busy ? "Searching…" : "Find plugs on my Wi-Fi"}</button>
        </div>
        {found && found.length === 0 && <p className="note">No plugs answered. Check they're plugged in and on the same Wi-Fi as this computer.</p>}
        {found && found.length > 0 && (
          <div className="table-wrap" style={{ marginTop: 8 }}><table>
            <thead><tr><th>Found</th><th>Address</th><th>Now</th><th /></tr></thead>
            <tbody>{found.map((p) => (
              <tr key={p.host}>
                <td>{p.model ?? "plug"}{p.name ? ` · ${p.name}` : ""}</td>
                <td className="num">{p.host}</td>
                <td>{p.error ? <span className="error">{p.error}</span> : p.on ? "on" : "off"}</td>
                <td>{known.has(p.host)
                  ? <span className="faint">already added{p.switch_id ? ` as ${p.switch_id}` : ""}</span>
                  : <button className="btn" onClick={() => addPlug(p.host, target)}>Add{target ? ` for ${circs.find((c) => c.id === target)?.name}` : ""}</button>}</td>
              </tr>))}</tbody>
          </table></div>)}
        {found && found.length > 0 && <p className="faint">Not sure which is which? Press the button on one plug, then search again: the one whose on/off changed is that plug.</p>}
      </>)}
      {msg && <p className="success" role="status">{msg}</p>}
    </div>
  );
}
