import { Json } from "../api";

/* Which smart plug powers the dehumidifier, and which room it dries. */
export function DehumPlug({ cfg, change }: { cfg: Json; change: (p: string, v: Json) => void }) {
  const dh = cfg.dehumidifier || {};
  const used: Record<string, string> = {};
  (cfg.fans || []).forEach((f: Json) => { if (f.switch_id) used[f.switch_id] = f.name; });
  (cfg.circulators || []).forEach((c: Json) => { if (c.switch_id) used[c.switch_id] = c.name; });
  const sw = cfg.switches || {};
  const plugs: string[] = [
    ...(sw.tapo_devices || []).map((d: Json) => d.switch_id),
    ...(sw.shelly_devices || []).map((d: Json) => d.switch_id),
    ...(sw.provider === "mock" ? ["mock-plug-5", "mock-plug-6", "mock-plug-7"] : []),
  ];
  const host = (id: string) => (sw.tapo_devices || []).concat(sw.shelly_devices || []).find((d: Json) => d.switch_id === id)?.host;
  const zones: Json[] = cfg.zones || [];
  return (
    <div className="form" style={{ marginBottom: 12 }}>
      <div className="field">
        <label htmlFor="dh-plug">Smart plug it's plugged into</label>
        <select id="dh-plug" value={dh.switch_id || ""} onChange={(e) => change("dehumidifier.switch_id", e.target.value)}>
          <option value="">None (the app doesn't switch it)</option>
          {plugs.map((p) => (
            <option key={p} value={p} disabled={!!used[p]}>{p}{host(p) ? ` (${host(p)})` : ""}{used[p] ? ` · used by ${used[p]}` : ""}</option>
          ))}
          {dh.switch_id && !plugs.includes(dh.switch_id) && <option value={dh.switch_id}>{dh.switch_id}</option>}
        </select>
        <span className="hint">Spare plug not listed? Add it in the Hardware card above (or Setup → Find plugs on my Wi-Fi), save, then pick it here.</span>
      </div>
      <div className="field">
        <label htmlFor="dh-zone">Room it sits in</label>
        <select id="dh-zone" value={dh.zone || ""} onChange={(e) => change("dehumidifier.zone", e.target.value || null)}>
          <option value="">Whole basement (average)</option>
          {zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
        </select>
        <span className="hint">Its humidity reading decides when it runs</span>
      </div>
    </div>
  );
}
