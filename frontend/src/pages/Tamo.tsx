import { useEffect, useMemo, useState } from "react";
import { api, Json } from "../api";
import { Card, TimeChart } from "../components";
import { deltaTemp, num, pct, tempVal, useUnit } from "../units";

const OPP: Record<string, { label: string; cls: string; text: string }> = {
  excellent: { label: "Excellent", cls: "good", text: "Strong drying with little temperature cost." },
  good: { label: "Good", cls: "good", text: "Worth running." },
  marginal: { label: "Marginal", cls: "", text: "Small net benefit." },
  unfavorable: { label: "Unfavorable", cls: "mock", text: "Costs outweigh the drying benefit." },
  unsafe: { label: "Unsafe", cls: "crit", text: "A safety limit blocks ventilation." },
};

export const POLICY_NAMES: Record<string, string> = { moisture: "Moisture-only", tamo: "Temperature-aware (TAMO)" };

/** Plain-language label for a structured reason code. */
export function reasonLabel(code: string) {
  return code.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function OpportunityBadge({ value }: { value?: string }) {
  const o = value ? OPP[value] : undefined;
  if (!o) return null;
  return <span className={`badge ${o.cls}`} title={o.text}>Opportunity: {o.label}</span>;
}

/** Which controller commands the fans, and what the other one would do. */
export function ControllerLine({ g, status }: { g: Json; status: Json }) {
  const d = g?.decision;
  const sh = g?.shadow;
  const other = status.policy === "tamo" ? "moisture" : "tamo";
  return (
    <div className="row faint" style={{ marginTop: 4 }}>
      <span>Commanding: <strong className="muted">{POLICY_NAMES[d?.policy || status.policy]}</strong></span>
      {sh && status.shadow_evaluation && (
        <span>· Shadow ({POLICY_NAMES[other]}) would turn fans <strong className="muted">{sh.target_on ? "ON" : "OFF"}</strong>
          {sh.target_on !== d?.target_on && <span className="badge" style={{ marginLeft: 6 }}>disagrees</span>}</span>
      )}
    </div>
  );
}

export function ReasonList({ d }: { d: Json }) {
  const [open, setOpen] = useState(false);
  if (!d?.reasons?.length) return null;
  return (
    <div style={{ marginTop: 8 }}>
      <div className="row" style={{ gap: 6 }}>
        {d.reasons.map((r: string, i: number) => (
          <span key={r} className={`badge ${i === 0 ? "" : "modeled"}`}>{reasonLabel(r)}</span>
        ))}
        {d.tamo && <button className="btn link" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Details"}</button>}
      </div>
      {open && d.tamo && <pre className="json">{JSON.stringify(d.tamo.terms ? {
        terms: d.tamo.terms, urgency: d.tamo.urgency, k_eff_g_h: d.tamo.k_eff_g_h, predicted: d.tamo.predicted,
        condensation: d.tamo.condensation, forecast: d.tamo.forecast, labels: d.tamo.labels,
      } : d.tamo, null, 2)}</pre>}
    </div>
  );
}

function Bar({ label, value, max, tone, hint }: { label: string; value: number; max: number; tone: "pos" | "neg" | "net"; hint?: string }) {
  const w = max > 0 ? Math.min(100, (Math.abs(value) / max) * 100) : 0;
  return (
    <div className="ubar" title={hint}>
      <span className="ubar-label">{label}</span>
      <span className="ubar-track"><span className={`ubar-fill ${tone}${value < 0 && tone === "net" ? " neg" : ""}`} style={{ width: `${w}%` }} /></span>
      <span className="ubar-val">{(tone === "neg" && value > 0) || (tone === "net" && value < 0) ? "−" : ""}{Math.round(Math.abs(value)).toLocaleString()}</span>
    </div>
  );
}

export default function TamoPanel({ status, refresh }: { status: Json; refresh: () => void }) {
  const u = useUnit();
  const g = status.groups[0];
  const d = g?.decision;
  const t = d?.tamo;
  const [hist, setHist] = useState<Json[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [cfgT, setCfgT] = useState<Json>(null);

  useEffect(() => {
    const load = () => api.history("24h").then((h) => setHist(h.rows || [])).catch(() => {});
    load();
    api.config().then((c) => setCfgT(c.temperature)).catch(() => {});
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, []);

  const nowX = new Date(status.now).getTime();
  const chart = useMemo(() => {
    const rows: Json[] = hist.filter((r) => new Date(r.ts).getTime() >= nowX - 6 * 3600e3).map((r) => ({
      x: new Date(r.ts).getTime(), on: r.fans_on > 0, in_t: tempVal(r.indoor_t_c, u),
    }));
    const p = t?.prediction;
    const cur = d?.metrics?.indoor?.t_c;
    if (p && cur != null) {
      const f2u = (f: number) => (u === "F" ? f : ((f - 32) * 5) / 9);
      const start = { x: nowX, in_t: tempVal(cur, u), p_on: tempVal(cur, u), p_off: tempVal(cur, u), p_cons: tempVal(cur, u) };
      rows.push(start);
      p.minutes.forEach((m: number, i: number) => rows.push({
        x: nowX + m * 60e3, p_on: f2u(p.on_f[i]), p_off: f2u(p.off_f[i]), p_cons: f2u(p.on_conservative_f[i]),
      }));
    }
    return rows.sort((a, b) => a.x - b.x);
  }, [hist, t, d, u, nowX]);

  async function choose(policy: string) {
    setBusy(true); setErr("");
    try { await api.setPolicy(policy); refresh(); } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  }

  const f2u = (f: number) => (u === "F" ? f : ((f - 32) * 5) / 9);
  const terms = t?.terms;
  const maxTerm = terms ? Math.max(terms.benefit_g_h, terms.thermal_penalty_g_h, terms.energy_penalty_g_h,
    terms.risk_penalty_g_h, Math.abs(terms.utility_g_h), 1) : 1;
  const pr = t?.predicted;
  const model = t?.model || status.thermal_model;
  const attr = status.rh_attribution;
  const xf = (x: number) => new Date(x).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

  return (
    <Card title="Temperature-aware drying" right={
      <div className="row">
        <OpportunityBadge value={d?.opportunity} />
        <div className="seg" role="group" aria-label="Control policy">
          {["moisture", "tamo"].map((p) => (
            <button key={p} className={status.policy === p ? "active" : ""} disabled={busy} onClick={() => choose(p)}>{POLICY_NAMES[p]}</button>
          ))}
        </div>
      </div>
    }>
      {err && <p className="error">{err}</p>}
      <p className="faint" style={{ marginTop: 0 }}>
        {status.policy === "tamo"
          ? "TAMO is commanding the fans. Switch back to Moisture-only at any time; the change takes effect on the next decision."
          : "Moisture-only is commanding the fans. TAMO runs alongside in shadow mode and records what it would have done."}
        {" "}Safety limits (minimum temperature, pipe freeze, condensation) apply to both.
      </p>
      {!t ? <p className="muted">No evaluation yet: needs current basement and outdoor readings.</p> : (
        <div className="grid g2">
          <div>
            <h3>Net utility at {num(t.horizon_min, 0)} min <span className="badge modeled">heuristic</span></h3>
            <Bar label="Drying benefit" value={terms.benefit_g_h} max={maxTerm} tone="pos"
              hint="Urgency × (water removed + RH relief), drying-equivalent g/h" />
            <Bar label="Thermal penalty" value={terms.thermal_penalty_g_h} max={maxTerm} tone="neg" />
            <Bar label="Energy" value={terms.energy_penalty_g_h} max={maxTerm} tone="neg" />
            <Bar label="Condensation risk" value={terms.risk_penalty_g_h} max={maxTerm} tone="neg" />
            <Bar label="Net" value={terms.utility_g_h} max={maxTerm} tone="net" />
            <p className="faint">Units: grams of water per hour (drying-equivalent). Start at ≥ {t.thresholds?.start_g_h}, keep running
              above {t.thresholds?.stop_g_h}. Humidity urgency {num(t.urgency, 2)}; temperature weight {Math.round(t.k_eff_g_h)} g/h per °F².</p>
            <dl className="kv">
              <dt>Water removed</dt><dd>{num(t.removal_gal_h, 3)} gal/h · {num(t.removal_gal_day, 2)} gal/day <span className="badge modeled">estimated</span></dd>
              <dt>Humidity ratio: basement / incoming</dt><dd>{num(d.metrics?.indoor?.w_g_per_kg, 2)} / {num(t.inlet_w_g_kg, 2)} g/kg</dd>
              <dt>Sensible heat from fan air</dt><dd>{Math.round(t.sensible_heat_btu_h).toLocaleString()} Btu/h ({Math.round(t.sensible_heat_w)} W)</dd>
              <dt>Surface condensation margin</dt><dd>{deltaTemp(t.condensation?.indoor_margin_f, u)} now → {deltaTemp(t.condensation?.indoor_margin_on_f, u)} with fans</dd>
              {attr && <><dt>Recent RH change ({num(attr.minutes, 0)} min)</dt>
                <dd>{attr.rh_change_pct >= 0 ? "+" : ""}{num(attr.rh_change_pct, 1)}%: {num(attr.from_moisture_pct, 1)} from moisture, {num(attr.from_temperature_pct, 1)} from temperature</dd></>}
            </dl>
          </div>
          <div>
            <h3>Predicted in {num(t.horizon_min, 0)} min <span className="badge modeled">coupled model</span></h3>
            {pr && <div className="table-wrap">
              <table>
                <thead><tr><th></th><th className="num">Fans ON</th><th className="num">Fans OFF</th></tr></thead>
                <tbody>
                  <tr><td>Temperature</td><td className="num">{num(tempVal((pr.on.t_f - 32) / 1.8, u), 1)}°{u}</td><td className="num">{num(tempVal((pr.off.t_f - 32) / 1.8, u), 1)}°{u}</td></tr>
                  <tr><td>Relative humidity</td><td className="num">{pct(pr.on.rh_pct, 1)}</td><td className="num">{pct(pr.off.rh_pct, 1)}</td></tr>
                  <tr><td>Dew point</td><td className="num">{num(tempVal((pr.on.dew_point_f - 32) / 1.8, u), 1)}°{u}</td><td className="num">{num(tempVal((pr.off.dew_point_f - 32) / 1.8, u), 1)}°{u}</td></tr>
                  <tr><td>Humidity ratio</td><td className="num">{num(pr.on.w_g_kg, 2)} g/kg</td><td className="num">{num(pr.off.w_g_kg, 2)} g/kg</td></tr>
                </tbody>
              </table>
            </div>}
            {t.prediction && <div className="table-wrap" style={{ marginTop: 8 }}>
              <table>
                <thead><tr><th>Fan effect on temperature</th>{t.prediction.minutes.map((m: number) => <th key={m} className="num">{m} min</th>)}</tr></thead>
                <tbody>
                  <tr><td>Expected</td>{t.prediction.fan_effect_f.map((v: number, i: number) => <td key={i} className="num">{deltaTemp(v, u)}</td>)}</tr>
                  <tr><td>RH with fans / without</td>{t.prediction.on_rh_pct.map((v: number, i: number) => <td key={i} className="num">{v.toFixed(0)} / {t.prediction.off_rh_pct[i].toFixed(0)}%</td>)}</tr>
                </tbody>
              </table>
            </div>}
            <p className="faint">Thermal model: {model?.source} · confidence {model?.confidence} · ±{Math.round((model?.uncertainty || 0) * 100)}%
              {" "}· air-to-structure {Math.round(model?.coupling_w_k)} W/K. Predictions hold outdoor conditions constant.</p>
          </div>
        </div>
      )}
      {t?.prediction && chart.length > 1 && cfgT && (
        <TimeChart title="Thermal impact: last 6 h and predictions" unit={`°${u}`} data={chart} xFormat={xf} height={240} digits={2}
          fanOn={undefined} nowX={nowX}
          band={{ y1: f2u(cfgT.preferred_min_f), y2: f2u(cfgT.preferred_max_f), label: "Preferred range" }}
          lines={[{ y: f2u(cfgT.min_allowed_f), label: "Minimum allowed", tone: "crit" }]}
          series={[
            { key: "in_t", name: "Basement (measured)", color: "var(--series-1)" },
            { key: "p_on", name: "Fans ON", color: "var(--series-2)", dashed: true },
            { key: "p_off", name: "Fans OFF", color: "var(--series-3)", dashed: true },
            { key: "p_cons", name: "Fans ON, conservative", color: "var(--series-4)", dashed: true },
          ]} />
      )}
      {status.forecast?.enabled && (
        <p className="faint" style={{ marginBottom: 0 }}>Forecast: {status.forecast.usable
          ? `${status.forecast.source}${status.forecast.source === "mock" ? " (mock world)" : ""}, ${num(status.forecast.age_h, 1)} h old`
          : `not available${status.forecast.error ? ` (${status.forecast.error})` : ""}; decisions use live sensors only`}
          {g?.deferring_since && ` · waiting for a better window since ${new Date(g.deferring_since).toLocaleTimeString()}`}</p>
      )}
    </Card>
  );
}
