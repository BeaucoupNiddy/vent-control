/* Home: the product's front page. A live overview up top (status, key numbers), then
   controls, trends, rooms and equipment health. Everything is also on the detailed tabs;
   this page trades raw detail for plain words, clear graphics and quick controls.
   Keep syntax and APIs friendly to older iPhone Safari (see home/util.ts). */
import { ReactNode, useEffect, useState } from "react";
import { api, Json } from "../api";
import { exhaustState, plugOn } from "../fanstate";
import { temp, tempVal, useUnit } from "../units";
import { ComfortDonut, ClimateChart, DailyBars, DryingChart, Gauge, MiniBars, Ring, Seg, Sparkline } from "./home/charts";
import { CIRC_MODES, CircFanCard, ExhaustFanCard, FanOrb, ModeControl, modeLabel } from "./home/controls";
import { EquipmentList, WallMeter, devices } from "./home/equipment";
import { HIcon } from "./home/icons";
import {
  DEFAULT_TARGETS, HEADLINE, LEVEL_WORD, Pt, Targets, Tone, bands, cToF, dDeg, daily, feelWord, hourLabel, isDaytime,
  r0, rhTone, since, timeUntil, toPts, trendDir, withLive,
} from "./home/util";
import "./home.css";

export { HIcon };

/* ---------- small building blocks ---------- */

function Panel({ icon, title, sub, right, children, className = "", id }: {
  icon: string; title: ReactNode; sub?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; id?: string;
}) {
  return (
    <section className={`h-panel ${className}`} id={id}>
      <header className="h-panel-head">
        <span className="h-panel-icon"><HIcon name={icon} size={18} /></span>
        <div className="h-panel-title">
          <h2>{title}</h2>
          {sub && <span>{sub}</span>}
        </div>
        <span className="spacer" />
        {right}
      </header>
      {children}
    </section>
  );
}

function SectionTitle({ id, title, sub }: { id: string; title: string; sub?: string }) {
  return (
    <div className="h-section" id={id}>
      <h2>{title}</h2>
      {sub && <span>{sub}</span>}
    </div>
  );
}

function Tile({ icon, label, value, sub, tone = "ok", viz, title }: {
  icon: string; label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; viz?: ReactNode; title?: string;
}) {
  return (
    <div className={`h-tile tone-${tone}`} title={title}>
      <div className="t-top"><span className="t-icon"><HIcon name={icon} size={16} /></span><span className="t-label">{label}</span></div>
      <div className="t-value">{value}</div>
      {sub && <div className="t-sub">{sub}</div>}
      {viz && <div className="t-viz">{viz}</div>}
    </div>
  );
}

function Trend({ perHour, unit = "%/h", eps = 0.3 }: { perHour: number | null | undefined; unit?: string; eps?: number }) {
  const dir = trendDir(perHour, eps);
  const word = dir === "flat" ? "steady" : dir === "down" ? "drying" : "rising";
  return (
    <span className={`h-trend ${dir}`}>
      <HIcon name={dir} size={13} />{word}{dir !== "flat" && perHour != null ? ` ${Math.abs(perHour).toFixed(1)}${unit}` : ""}
    </span>
  );
}

const sentence = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

const JUMPS = [["controls", "Controls"], ["trends", "Trends"], ["rooms", "Rooms"], ["equipment", "Equipment"]] as const;

/* ---------- page ---------- */

export default function Home({ status, refresh, openTab }: { status: Json; refresh: () => void; openTab: (t: string) => void }) {
  const u = useUnit();
  const [stats, setStats] = useState<Json>(null);
  const [targets, setTargets] = useState<Targets>(DEFAULT_TARGETS);
  const [dpOnF, setDpOnF] = useState(3);
  const [week, setWeek] = useState<Pt[] | null>(null);
  const [range, setRangeState] = useState<string>(() => {
    try { return localStorage.getItem("ventctl.homeRange") || "24h"; } catch { return "24h"; }
  });
  const [chartPts, setChartPts] = useState<Pt[] | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [showAllAlerts, setShowAllAlerts] = useState(false);
  // Reload history whenever the plugs report a change, so the charts' fan bands never lag the live state.
  const liveSig = status ? `${exhaustState(status).on}:${status.simulation_mode ? 1 : 0}:${status.mode}` : "";

  useEffect(() => {
    api.config().then((c) => {
      const ctl = c && c.control;
      if (ctl) setTargets({ target: ctl.target_rh_pct, upper: ctl.upper_rh_pct, high: ctl.high_rh_alert_pct });
      if (ctl && ctl.dewpoint_on_advantage_f != null) setDpOnF(ctl.dewpoint_on_advantage_f);
    }).catch(() => {});
    const load = () => {
      api.stats("24h").then(setStats).catch(() => {});
      api.history("7d").then((h) => setWeek(toPts((h && h.rows) || []))).catch(() => setWeek([]));
    };
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [liveSig]);

  useEffect(() => {
    try { localStorage.setItem("ventctl.homeRange", range); } catch { /* storage unavailable */ }
    const load = () => api.history(range).then((h) => setChartPts(toPts((h && h.rows) || []))).catch(() => setChartPts([]));
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [range, liveSig]);
  const pickRange = (r: string) => { setChartPts(null); setRangeState(r); };

  if (!status) return <div className="home"><div className="h-panel h-loading"><FanOrb on tone="ok" size={56} /> Loading your basement…</div></div>;

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key); setErr("");
    try { await fn(); refresh(); } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(""); }
  }

  /* ---------- derive what the page shows ---------- */
  const g = status.groups && status.groups[0];
  const d = g && g.decision;
  const m = (d && d.metrics) || {};
  const ba = status.basement_average;
  const ind = m.indoor || (ba && ba.t_c != null ? { ...ba, dew_point_f: cToF(ba.dew_point_c) } : undefined);
  const out = m.outdoor;
  const zones: Json[] = Object.keys(status.zones || {}).map((k) => status.zones[k]);
  const multiZone = zones.length > 1;
  const avg = multiZone && ba && ba.count > 1 ? ba : null;
  const main = avg || ind; // the whole basement when we can, else the control reading
  const rh: number | null = main && main.rh_pct != null ? main.rh_pct : null;
  const tone = rhTone(rh, targets);
  const safety = !!(d && d.safety);
  const advF: number | null = m.dewpoint_advantage_f != null ? m.dewpoint_advantage_f : null;
  const outsideDrier = advF != null && advF > 0;
  const mode: string = status.mode;
  const sim = !!status.simulation_mode;
  const groups: Json[] = status.groups || [];
  // Fan state everywhere on this page = what the plugs report (see fanstate.ts).
  const ex = exhaustState(status);
  const fans = ex.fans;
  const fansOn = ex.on;
  const anyOn = ex.anyOn;
  const totalCfm = fans.reduce((s, f) => s + (f.actual_cfm || f.rated_cfm || 0), 0);
  // What the history records as "fans on": plug reports, or the hypothetical state in practice mode.
  const liveOn = sim ? groups.some((x) => x.is_on) : anyOn;
  const onSince = fans.filter((f) => plugOn(f.switch) && f.switch.on_since).map((f) => new Date(f.switch.on_since).getTime());
  const liveSinceMs = onSince.length ? Math.min.apply(null, onSince) : null;
  const nowMs = new Date(status.now).getTime();
  const manualLeft = timeUntil(g && g.manual_until, status.now);
  // The decision's removal figure assumes every exhaust fan runs; scale it to the ones actually running.
  const galAll: number | null = d && d.effect ? d.effect.net_removal_gal_day : null;
  const galDay: number | null = galAll == null ? null : anyOn && fans.length ? (galAll * fansOn) / fans.length : galAll;
  const circ = status.circulation;
  const circs: Json[] = (circ && circ.circulators) || [];
  const surfaces: Json[] = status.surfaces || [];
  const fanWord = fans.length > 1 ? "fans" : "fan";
  const wettest = multiZone ? zones.filter((z) => z.status === "ok").sort((a, b) => (b.rh_pct || 0) - (a.rh_pct || 0))[0] : null;

  let doing: string;
  if (safety) doing = `The exhaust ${fanWord} ${fans.length > 1 ? "are" : "is"} paused for safety. ${d && d.explanation ? sentence(String(d.explanation).replace(/^Fans OFF[^:]*:\s*/, "")) : ""}`;
  else if (sim) doing = d && d.target_on ? `Practice mode: the ${fanWord} would be running now, but no plugs are being switched.` : `Practice mode: the ${fanWord} would be resting now. No plugs are being switched.`;
  else if (anyOn && mode === "manual_on") doing = `Boost is on${manualLeft ? `. Back to Automatic in ${manualLeft}` : ""}.`;
  else if (anyOn && outsideDrier) doing = `Pulling in drier outside air${galDay && galDay > 0.05 ? `, removing about ${galDay.toFixed(1)} gallons of water a day at this rate` : ""}.`;
  else if (anyOn) doing = `The exhaust ${fanWord} ${fans.length > 1 ? "are" : "is"} running.`;
  else if (mode === "off") doing = `The system is switched off and stays off until you choose Automatic.`;
  else if (rh != null && rh < targets.target) doing = `The basement is at its goal, so the ${fanWord} ${fans.length > 1 ? "are" : "is"} resting.`;
  else if (advF != null && advF <= 0) doing = `Outside air is more humid than the basement right now, so the ${fanWord} ${fans.length > 1 ? "wait" : "waits"} for it to dry out.`;
  else doing = `Resting. The ${fanWord} start${fans.length > 1 ? "" : "s"} by ${fans.length > 1 ? "themselves" : "itself"} when outside air can help.`;

  /* forecast */
  const basementDpC: number | null = m.exhaust_air && m.exhaust_air.dew_point_c != null ? m.exhaust_air.dew_point_c : ind ? ind.dew_point_c : null;
  const fcHours: Json[] = status.forecast && status.forecast.usable ? status.forecast.hours || [] : [];
  const gapAt = (h: Json) => (basementDpC != null && h.dew_point_c != null ? ((basementDpC - h.dew_point_c) * 9) / 5 : null);
  const goodHour = (h: Json) => { const x = gapAt(h); return x != null && x >= dpOnF; };
  const strip = fcHours.slice(0, 12);
  const goodCount = fcHours.filter(goodHour).length;
  const nextGood = fcHours.filter(goodHour)[0];
  const indDpC: number | null = ind ? ind.dew_point_c : null;

  /* history roll-ups */
  const day = week ? since(week, 24) : [];
  const dayLive = withLive(day, liveOn, nowMs, liveSinceMs);
  const chartLive = chartPts ? withLive(chartPts, liveOn, nowMs, liveSinceMs) : null;
  const days = week ? daily(week).slice(-7) : [];
  const b24 = bands(day, targets);
  const under24 = b24.total ? (b24.good + b24.ok) / b24.total : null;
  const rhFirst = day.length ? day.filter((p) => p.rh != null)[0] : null;
  const rhChange = rhFirst && rh != null ? rh - (rhFirst.rh as number) : null;
  const water = stats ? (stats.net_vapor_removed_gal as number) : null;
  const fanHours = stats ? stats.fan_runtime_h : null;
  const rangeLabel = range === "24h" ? "last 24 hours" : range === "3d" ? "last 3 days" : "last 7 days";

  /* alerts, most serious first */
  const sevRank: Record<string, number> = { critical: 0, warning: 1, info: 2 };
  const alerts: Json[] = (status.alerts || []).slice().sort((a: Json, b: Json) => (sevRank[a.severity] || 3) - (sevRank[b.severity] || 3));
  const shownAlerts = showAllAlerts ? alerts : alerts.slice(0, 3);

  const equip = devices(status);
  const equipBad = equip.filter((x) => x.tone === "bad" || x.tone === "warn").length;
  const sensorAge = Math.min.apply(null, (status.sensors || []).filter((s: Json) => s.status === "ok").map((s: Json) => Math.max(0, s.age_s || 0)).concat([1e9]));

  const jump = (id: string) => { const el = document.getElementById(id); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); };

  return (
    <div className="home">
      {/* ================= OVERVIEW ================= */}
      <section className={`h-hero tone-${safety ? "bad" : tone}`}>
        <div className="h-hero-main">
          <Gauge value={rh} t={targets} tone={tone} label={avg ? "whole basement" : "humidity"} />
          <div className="h-hero-text">
            <div className="h-eyebrow">
              <span className="h-live"><span className="h-live-dot" />Live</span>
              <span>{sensorAge < 1e8 ? `Updated ${sensorAge < 60 ? "just now" : `${Math.round(sensorAge / 60)} min ago`}` : "Waiting for sensors"}</span>
              {status.data_source === "mock" && <span className="h-chip subtle">demo data</span>}
            </div>
            <h1 className="h-headline">{safety ? "Paused for safety" : HEADLINE[tone]}</h1>
            <p className="h-doing">{doing}</p>
            <div className="h-hero-facts">
              <span><HIcon name="thermo" size={15} /> {temp(main && main.t_c, u, 0)}</span>
              <span><HIcon name="drop" size={15} /> Dew point {temp(main && main.dew_point_c, u, 0)} · {feelWord(cToF(main && main.dew_point_c)).toLowerCase()}</span>
              {avg && avg.trend && <span><Trend perHour={avg.trend.rh_pct_h} /></span>}
              {wettest && <span className={`tone-${(LEVEL_WORD[wettest.level] || { tone: "ok" }).tone}`}><HIcon name="target" size={15} /> Wettest: {wettest.name} {r0(wettest.rh_pct)}</span>}
            </div>
          </div>
        </div>
        <div className="h-hero-side">
          <div className="hs-fan">
            <FanOrb on={anyOn} tone={safety ? "bad" : anyOn ? "good" : "idle"} size={64} />
            <div>
              <strong>{sim ? (d && d.target_on ? "Would run" : "Would rest") : anyOn ? `${fans.length > 1 ? `${fansOn} of ${fans.length} fans` : "Fan"} running` : `Exhaust ${fanWord} resting`}</strong>
              <span>{modeLabel(mode)}{mode === "manual_on" && manualLeft ? ` · ${manualLeft} left` : ""}{anyOn && ex.cfm ? ` · ${Math.round(ex.cfm)} CFM` : ""}{ex.pending ? " · waiting for a plug to confirm" : ""}</span>
            </div>
          </div>
          <dl className="hs-facts">
            <div>
              <dt>Outside air</dt>
              <dd className={advF == null ? "" : advF >= dpOnF ? "good" : advF > 0 ? "ok" : "warn"}>
                {advF == null ? "—" : advF > 0 ? `${dDeg(advF, u, false)} drier` : advF < 0 ? `${dDeg(-advF, u, false)} wetter` : "Same as inside"}
              </dd>
            </div>
            <div>
              <dt>Next drying window</dt>
              <dd>{anyOn && outsideDrier ? "Now" : nextGood ? hourLabel(nextGood.ts) : fcHours.length ? `None in ${fcHours.length} h` : "—"}</dd>
            </div>
            <div>
              <dt>{anyOn ? "Removing now" : "If the fans ran"}</dt>
              <dd>{galDay == null ? "—" : galDay > 0 ? `${galDay.toFixed(1)} gal/day` : "Would add water"}</dd>
            </div>
          </dl>
        </div>
      </section>

      {/* key numbers */}
      <div className="h-tiles">
        <Tile icon="drop" label={avg ? "Basement humidity" : "Humidity"} tone={tone} value={r0(rh)}
          sub={rhChange == null ? `Goal under ${targets.target}%` : `${Math.abs(rhChange) < 0.5 ? "No change" : `${rhChange > 0 ? "Up" : "Down"} ${Math.abs(Math.round(rhChange))}%`} since yesterday`}
          viz={<Sparkline values={day.map((p) => p.rh)} tone={tone} refY={targets.target} />} />
        <Tile icon="thermo" label="Dew point" tone="ok" value={temp(main && main.dew_point_c, u, 0)}
          sub={feelWord(cToF(main && main.dew_point_c))}
          viz={<Sparkline values={day.map((p) => tempVal(p.avg_td != null ? p.avg_td : p.in_td, u))} tone="ok" />} />
        <Tile icon="wind" label="Drying power" tone={advF == null ? "idle" : advF >= dpOnF ? "good" : advF > 0 ? "ok" : "warn"}
          value={dDeg(advF, u)} title="Basement dew point minus outside dew point. Positive means outside air is drier."
          sub={advF == null ? "Needs an outdoor reading" : advF >= dpOnF ? "Outside is drier: fans help" : advF > 0 ? "Slightly drier outside" : "Outside is wetter"}
          viz={<Sparkline values={day.map((p) => (p.gapF == null ? null : u === "F" ? p.gapF : (p.gapF * 5) / 9))} tone={advF != null && advF > 0 ? "good" : "warn"} refY={0} />} />
        <Tile icon="bucket" label="Water removed" tone="ok"
          value={water == null ? "—" : `${Math.abs(water).toFixed(1)} gal`}
          sub={water != null && water < 0 ? "Added (estimate)" : water != null && water > 0.15 ? `Last 24 h · ≈ ${Math.round(water / 0.132)} bottles` : "Last 24 h · estimate"}
          viz={<MiniBars values={days.map((x) => Math.max(0, x.gal))} tone="ok" />} />
        <Tile icon="clock" label="Fan run time" tone="good"
          value={fanHours == null ? "—" : `${fanHours.toFixed(1)} h`}
          sub={stats ? `Last 24 h · $${stats.electricity_cost.toFixed(2)} power` : "Last 24 h"}
          viz={<MiniBars values={days.map((x) => x.fanH)} tone="good" />} />
        <Tile icon="target" label={`Time under ${targets.upper}%`} tone={under24 == null ? "idle" : under24 > 0.8 ? "good" : under24 > 0.4 ? "warn" : "bad"}
          value={under24 == null ? "—" : `${Math.round(under24 * 100)}%`}
          sub={under24 == null ? "Building history" : `Last 24 h · ${(b24.total * (1 - under24)).toFixed(0)} h above`}
          viz={<div className="t-ring"><Ring share={under24} tone={under24 == null ? "idle" : under24 > 0.8 ? "good" : under24 > 0.4 ? "warn" : "bad"} /></div>} />
      </div>

      <nav className="h-jump" aria-label="Jump to section">
        {JUMPS.map(([id, l]) => <button key={id} onClick={() => jump(id)}>{l}</button>)}
      </nav>

      {/* alerts */}
      {(alerts.length > 0 || status.dismissed_alerts > 0) && (
        <Panel icon="bell" title="Needs your attention" className="h-alerts"
          sub={alerts.length ? `${alerts.length} notice${alerts.length === 1 ? "" : "s"}` : undefined}
          right={<span className="row">
            {alerts.length > 1 && <button className="btn link" onClick={() => api.dismissAlerts().then(refresh)}>Dismiss all</button>}
            {status.dismissed_alerts > 0 && <button className="btn link" onClick={() => api.restoreAlerts().then(refresh)}>Show {status.dismissed_alerts} dismissed</button>}
          </span>}>
          <ul className="h-alert-list">
            {shownAlerts.map((a: Json) => (
              <li key={a.id} className={`sev-${a.severity}`}>
                <span className="al-icon"><HIcon name={a.severity === "info" ? "info" : "alert"} size={16} /></span>
                <span className="al-text">{String(a.message).replace(/\.\s[\s\S]*$/, ".").replace(/\bRH\b/g, "humidity")}</span>
                <button className="alert-x" title="Dismiss (it comes back if the problem returns)" aria-label="Dismiss"
                  onClick={() => api.dismissAlerts([a.id]).then(refresh)}><HIcon name="x" size={14} /></button>
              </li>
            ))}
            {alerts.length === 0 && <li className="faint">All notices dismissed.</li>}
          </ul>
          {alerts.length > 3 && (
            <button className="h-more-link" onClick={() => setShowAllAlerts(!showAllAlerts)}>
              {showAllAlerts ? "Show fewer" : `Show ${alerts.length - 3} more`} <span className={showAllAlerts ? "flip" : ""}><HIcon name="chevron" size={14} /></span>
            </button>
          )}
        </Panel>
      )}

      {/* ================= CONTROLS ================= */}
      <SectionTitle id="controls" title="Controls" sub="Changes take effect within a few seconds" />
      {err && <p className="error h-err">{err}</p>}
      <div className="h-grid h-grid-ctl">
        <Panel icon="sliders" title="System mode" sub="Applies to every exhaust fan">
          <ModeControl status={status} busy={busy} act={act} />
        </Panel>
        {fans.length > 0 && (
          <Panel icon="fan" title={fans.length > 1 ? "Exhaust fans" : "Exhaust fan"} sub={`${fans.length} × ${Math.round(totalCfm / fans.length)} CFM · pull damp air out`}>
            <div className="h-fancards">
              {fans.map((f) => <ExhaustFanCard key={f.id} f={f} groupSafety={safety} status={status} busy={busy} act={act} />)}
            </div>
            <p className="h-hint">Auto follows the app. On runs that fan for an hour. Off keeps it off until you pick Auto.</p>
          </Panel>
        )}
        {circs.length > 0 && (
          <Panel icon="fan" title="Circulation fans" sub="Mix the air; they don't remove water" className="h-span-2"
            right={<Seg value={circ.mode} options={CIRC_MODES} label="Circulation automation"
              onChange={(v) => act(`circmode:${v}`, () => api.circulationMode(v))} />}>
            <div className="h-fancards h-fancards-2">
              {circs.map((c) => <CircFanCard key={c.id} c={c} circMode={circ.mode} status={status} busy={busy} act={act} />)}
            </div>
            <p className="h-hint">
              {circ.mode === "shadow" ? "Learning: the app works out when these would help but leaves the plugs alone. " : circ.mode === "auto" ? "Automatic: the app runs these when mixing helps, and alongside the exhaust fans. " : "Automation is off. "}
              On and Off here last one hour, then go back to Auto.
            </p>
          </Panel>
        )}
      </div>

      {/* ================= TRENDS ================= */}
      <SectionTitle id="trends" title="Trends" sub="Shaded bands show when the exhaust fans ran" />
      <div className="h-grid h-grid-trend">
        <Panel icon="chart" title="Climate history" sub={rangeLabel[0].toUpperCase() + rangeLabel.slice(1)}>
          <ClimateChart pts={chartLive || []} loading={chartLive === null} nowX={nowMs} u={u} t={targets} range={range} setRange={pickRange} />
        </Panel>
        <Panel icon="target" title="Time in each humidity band" sub={rangeLabel[0].toUpperCase() + rangeLabel.slice(1)}>
          {chartPts === null ? <div className="h-chart-empty">Loading…</div> : <ComfortDonut pts={chartPts} t={targets} rangeLabel={rangeLabel} />}
        </Panel>
      </div>
      <div className="h-grid h-grid-trend">
        <Panel icon="wind" title="Drying power" sub={`Past 24 h${fcHours.length ? ` and next ${fcHours.length} h forecast` : ""}`}
          right={fcHours.length ? <span className={`h-chip ${goodCount ? "good" : "subtle"}`}>{goodCount ? `${goodCount} good ${goodCount === 1 ? "hour" : "hours"} ahead` : "No good hours ahead"}</span> : null}>
          {week === null ? <div className="h-chart-empty">Loading…</div> : <DryingChart pts={dayLive} forecast={fcHours} basementDpC={basementDpC} u={u} onF={dpOnF} />}
          <p className="h-hint">How much drier outside air is than the air the fans pull out. Above the dashed line the fans can start; below zero, outside air would add water. The dashed part is the forecast.</p>
        </Panel>
        <Panel icon="home" title="Inside vs. outside">
          <div className="h-compare">
            <div className="h-place in">
              <span className="h-place-icon"><HIcon name="home" size={22} /></span>
              <div className="h-place-name">Basement</div>
              <div className="h-place-temp">{temp(ind && ind.t_c, u, 0)}</div>
              <div className="h-place-sub">{r0(ind && ind.rh_pct)} · dew {temp(indDpC, u, 0)}</div>
            </div>
            <div className={`h-vs ${outsideDrier ? "good" : "warn"}`} aria-hidden>
              <HIcon name="arrow" size={18} />
              <span>{advF == null ? "" : outsideDrier ? "drier" : "wetter"}</span>
            </div>
            <div className="h-place out">
              <span className="h-place-icon"><HIcon name={isDaytime(status.now) ? "sun" : "moon"} size={22} /></span>
              <div className="h-place-name">Outside</div>
              <div className="h-place-temp">{temp(out && out.t_c, u, 0)}</div>
              <div className="h-place-sub">{r0(out && out.rh_pct)} · dew {temp(out && out.dew_point_c, u, 0)}</div>
            </div>
          </div>
          {strip.length > 0 ? (
            <>
              <div className="h-forecast" role="list" aria-label="Forecast">
                {strip.map((h) => (
                  <div key={h.ts} role="listitem" className={`h-hour ${goodHour(h) ? "good" : ""}`} title={goodHour(h) ? "Outside air drier than the basement" : "Outside air too wet to help"}>
                    <span className="h-hour-time">{hourLabel(h.ts)}</span>
                    <span className="h-hour-icon"><HIcon name={isDaytime(h.ts) ? "sun" : "moon"} size={16} /></span>
                    <span className="h-hour-temp">{temp(h.t_c, u, 0).replace(/°[FC]$/, "°")}</span>
                    <span className="h-hour-dew">{temp(h.dew_point_c, u, 0).replace(/°[FC]$/, "°")}</span>
                    <span className="h-hour-dot" />
                  </div>
                ))}
              </div>
              <p className="h-hint"><span className="h-dot-inline" /> Green hours: forecast outside air is dry enough for the fans to help. Second number is the outside dew point.</p>
            </>
          ) : <p className="h-hint">No weather forecast available right now.</p>}
        </Panel>
      </div>
      {days.length > 0 && (
        <Panel icon="chart" title="Last 7 days" sub="Daily totals from recorded history">
          <div className="h-dailies">
            <DailyBars days={days} field="fanH" unit=" h" digits={1} color="var(--series-1)" title="Fan run time" />
            <DailyBars days={days} field="gal" unit=" gal" digits={2} color="var(--series-3)" title="Water removed (est.)" />
            <DailyBars days={days} field="rh" unit="%" digits={0} color="var(--series-4)" title={`Average humidity (line: ${targets.target}% goal)`} refY={targets.target} />
          </div>
        </Panel>
      )}

      {/* ================= ROOMS ================= */}
      {zones.length > 0 && (
        <>
          <SectionTitle id="rooms" title="Rooms" sub={multiZone ? `${zones.length} areas · the fans watch the most humid one` : undefined} />
          <div className="h-rooms">
            {avg && (
              <RoomCard name="Whole basement" note={`Average of ${avg.count} areas`} rh={avg.rh_pct} tC={avg.t_c} dpC={avg.dew_point_c}
                level={avg.level} trend={avg.trend && avg.trend.rh_pct_h} t={targets} u={u} spark={day.map((p) => p.avg_rh)} featured />
            )}
            {zones.map((z) => (
              <RoomCard key={z.id} name={z.name} note={z.on_ventilation_path ? "On the fans' air path" : "Off the main air path"} rh={z.rh_pct}
                tC={z.t_c} dpC={z.dew_point_c} level={z.level} trend={z.trend && z.trend.rh_pct_h} t={targets} u={u}
                stale={z.status !== "ok"} spark={day.map((p) => (p.zones[z.id] != null ? p.zones[z.id] : null))} />
            ))}
          </div>
        </>
      )}

      {/* ================= EQUIPMENT ================= */}
      <SectionTitle id="equipment" title="Equipment" sub={equipBad ? `${equipBad} need${equipBad === 1 ? "s" : ""} attention` : `All ${equip.length} devices working`} />
      <div className="h-grid h-grid-eq">
        <Panel icon="chip" title="Device health" right={<span className={`h-chip ${equipBad ? "warn" : "good"}`}>{equipBad ? `${equipBad} issue${equipBad === 1 ? "" : "s"}` : <><HIcon name="check" size={13} /> All good</>}</span>}>
          <EquipmentList list={equip} />
        </Panel>
        <Panel icon="wall" title="Walls & condensation" sub="Dew forms on surfaces colder than the dew point">
          {surfaces.length === 0 ? (
            <div className="h-wall tone-good">
              <div className="h-wall-head">
                <span className="h-safe-icon"><HIcon name="shield" size={22} /></span>
                <div>
                  <strong>{(m.condensation_margin_f != null ? m.condensation_margin_f : 99) > 3 ? "Outside air won't condense" : "Close to condensing"}</strong>
                  <small>No wall probe connected, so this uses the coldest-surface estimate.</small>
                </div>
              </div>
            </div>
          ) : surfaces.map((s) => <WallMeter key={s.sensor_id} s={s} u={u} />)}
        </Panel>
      </div>

      <div className="h-footer-links">
        <button className="h-more" onClick={() => openTab("Ask")}><HIcon name="spark" size={16} /> Ask why the fans are doing this</button>
        <button className="h-more" onClick={() => openTab("Dashboard")}>Every detail on the Dashboard <HIcon name="arrow" size={16} /></button>
      </div>
    </div>
  );
}

/* ---------- one room ---------- */

function RoomCard({ name, note, rh, tC, dpC, level, trend, t, u, spark, stale, featured }: {
  name: string; note: string; rh: number | null; tC: number | null; dpC: number | null; level: string; trend: number | null | undefined;
  t: Targets; u: "F" | "C"; spark: (number | null)[]; stale?: boolean; featured?: boolean;
}) {
  const lv = LEVEL_WORD[level] || { word: level, tone: "ok" as Tone };
  const tone: Tone = stale ? "idle" : rhTone(rh, t);
  const pos = (v: number) => `${Math.max(0, Math.min(100, v))}%`;
  return (
    <div className={`h-room tone-${tone} ${featured ? "featured" : ""}`}>
      <div className="rm-top">
        <div>
          <strong>{name}</strong>
          <small>{note}</small>
        </div>
        <span className={`h-chip ${tone === "good" ? "good" : tone === "warn" ? "warn" : tone === "bad" ? "bad" : "subtle"}`}>{stale ? "No reading" : lv.word}</span>
      </div>
      <div className="rm-mid">
        <span className="rm-rh">{r0(rh)}</span>
        <div className="rm-meta">
          <span>{temp(tC, u, 0)} · dew {temp(dpC, u, 0)}</span>
          <Trend perHour={trend} />
        </div>
      </div>
      <div className="rm-scale" aria-hidden>
        <span className="band-good" style={{ width: pos(t.target) }} />
        <span className="band-ok" style={{ width: pos(t.upper - t.target) }} />
        <span className="band-warn" style={{ width: pos(t.high - t.upper) }} />
        <span className="band-bad" style={{ width: pos(100 - t.high) }} />
        {rh != null && !stale && <i style={{ left: pos(rh) }} />}
      </div>
      <Sparkline values={spark} tone={tone} height={34} refY={t.target} />
    </div>
  );
}
