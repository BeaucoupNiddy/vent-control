/* Controls on the Home page: system mode with boost, and one card per fan. */
import { useState } from "react";
import { Json, api } from "../../api";
import { circOn, plugOn, plugPending } from "../../fanstate";
import { HIcon } from "./icons";
import { Tone, minutesText, timeUntil } from "./util";

export type Act = (key: string, fn: () => Promise<unknown>) => void;

export function FanOrb({ on, tone, size = 72 }: { on: boolean; tone: Tone; size?: number }) {
  return (
    <span className={`h-orb ${on ? "spinning" : ""} tone-${tone}`} style={{ width: size, height: size }}>
      <HIcon name="fan" size={Math.round(size * 0.55)} />
    </span>
  );
}

const MODE_LABEL: Record<string, string> = {
  auto: "Automatic", manual_on: "Boost", off: "Off", schedule: "Schedule", simulation: "Practice mode",
};
export const modeLabel = (m: string) => MODE_LABEL[m] || m;

const BOOSTS = [[30, "30 min"], [60, "1 h"], [120, "2 h"], [240, "4 h"]] as const;

/* ---------- system mode ---------- */

export function ModeControl({ status, busy, act }: { status: Json; busy: string; act: Act }) {
  const mode: string = status.mode;
  const g = status.groups && status.groups[0];
  const left = timeUntil(g && g.manual_until, status.now);
  const boosting = mode === "manual_on";
  const [startNote, setStartNote] = useState("");
  const startNow = () => act("startnow", async () => {
    const r: Json = await api.startNow();
    setStartNote((r && r.start_now_note) || "");
  });
  const untilGap = (label: string) => (
    <button className="h-pill" disabled={!!busy} onClick={startNow}
      title="Starts the exhaust fans now. Automatic turns them off when the dew-point gap closes to your stop gap, the basement reaches its target, or a safety check needs it.">
      {busy === "startnow" ? "…" : label}
    </button>
  );
  const tiles: [string, string, string, string, () => Promise<unknown>][] = [
    ["auto", "spark", "Automatic", "Runs only when outside air helps", () => api.setMode("auto")],
    ["manual_on", "rocket", "Boost", boosting && left ? `${left} left` : "Run every exhaust fan now", () => api.setMode("manual_on", 60)],
    ["off", "power", "Off", "Stays off until you choose", () => api.setMode("off")],
  ];
  return (
    <div className="h-mode">
      <div className="h-modes" role="radiogroup" aria-label="System mode">
        {tiles.map(([k, icon, title, sub, fn]) => (
          <button key={k} role="radio" aria-checked={mode === k} className={`h-mode-tile m-${k} ${mode === k ? "active" : ""}`}
            disabled={!!busy} onClick={() => act(`mode:${k}`, fn)}>
            <span className="mt-icon"><HIcon name={icon} size={22} /></span>
            <strong>{busy === `mode:${k}` ? "…" : title}</strong>
            <small>{sub}</small>
          </button>
        ))}
      </div>
      <div className="h-boost-row">
        {boosting ? (
          <>
            <span className="h-boost-text"><span className="h-live-dot" /> <strong>Boosting</strong>{left ? ` for another ${left}` : ""}, then back to Automatic.</span>
            <button className="h-pill" disabled={!!busy} onClick={() => act("mode:auto", () => api.setMode("auto"))}>End boost</button>
            {untilGap("Stop when the gap closes instead")}
          </>
        ) : (
          <>
            <span className="faint">Boost for</span>
            {BOOSTS.map(([m, l]) => (
              <button key={m} className="h-pill" disabled={!!busy} onClick={() => act(`boost:${m}`, () => api.setMode("manual_on", m))}>
                {busy === `boost:${m}` ? "…" : l}
              </button>
            ))}
            {untilGap("Until the gap closes")}
          </>
        )}
      </div>
      {startNote && <p className="h-hint">{startNote.replace(/^Start now declined\. Fans OFF: /, "Couldn't start: ")}</p>}
      {mode === "schedule" && <p className="h-hint">Running on a schedule set in Settings. Choose Automatic to let the app decide.</p>}
      {status.simulation_mode && <p className="h-hint">Practice mode is on: the app decides as usual but no plugs are switched.</p>}
    </div>
  );
}

/* ---------- Auto / On / Off switch ---------- */

function Tri({ name, state, busy, keyBase, onPick, onLabel = "On 1 h", offLabel = "Off" }: {
  name: string; state: string; busy: string; keyBase: string; onPick: (s: string) => void; onLabel?: string; offLabel?: string;
}) {
  const opts: [string, string, string][] = [["auto", "Auto", "Let the app decide"], ["on", onLabel, "Run for 1 hour, then back to Auto"], ["off", offLabel, "Keep this fan off"]];
  return (
    <div className="h-seg tri" role="radiogroup" aria-label={`${name} control`}>
      {opts.map(([s, label, tip]) => (
        <button key={s} role="radio" aria-checked={state === s} title={tip} className={state === s ? `active ${s}` : ""}
          disabled={!!busy} onClick={() => onPick(s)}>
          {busy === `${keyBase}:${s}` ? "…" : label}
        </button>
      ))}
    </div>
  );
}

/* ---------- exhaust fan ---------- */

export function ExhaustFanCard({ f, groupSafety, status, busy, act }: { f: Json; groupSafety: boolean; status: Json; busy: string; act: Act }) {
  const sw = f.switch || {};
  const on = plugOn(sw);
  const reachable = sw.reachable !== false;
  const o = f.override as { state: string; until: string | null } | null;
  const state = (o && o.state) || "auto";
  const left = o && o.until ? timeUntil(o.until, status.now) : null;
  let note: string;
  if (!reachable) note = `Smart plug not responding${sw.confirmed != null ? ` · last reported ${sw.confirmed ? "on" : "off"}` : ""}`;
  else if (plugPending(sw)) note = `Turning ${sw.commanded ? "on" : "off"} · waiting for the plug to confirm`;
  else if (state === "off") note = "Held off by you";
  else if (state === "on" && !on) note = `Paused for safety${f.override_blocked ? `: ${String(f.override_blocked).replace(/^Fans OFF[^:]*:\s*/, "")}` : ""}`;
  else if (state === "on") note = `On by you${left ? ` · ${left} left` : ""}`;
  else if (groupSafety && !on) note = "Automatic · paused for safety";
  else if (status.mode === "manual_on" && on) note = "Boosting";
  else note = on ? "Automatic · running" : "Automatic · resting";
  const runFor = on && f.running_s ? minutesText(f.running_s / 60) : null;
  const kb = `fan:${f.id}`;
  return (
    <div className={`h-fancard ${on ? "on" : ""} ${!reachable ? "offline" : ""}`}>
      <div className="fc-top">
        <FanOrb on={on} tone={!reachable ? "bad" : on ? "good" : "idle"} size={46} />
        <div className="fc-name">
          <strong>{f.name}</strong>
          <span>{note}</span>
        </div>
        <span className={`h-chip ${!reachable ? "bad" : on ? "good" : "subtle"}`}>{!reachable ? "Offline" : on ? "On" : "Off"}</span>
      </div>
      <div className="fc-specs">
        <span><HIcon name="wind" size={14} /> {Math.round(f.actual_cfm || f.rated_cfm || 0)} CFM</span>
        <span><HIcon name="bolt" size={14} /> {sw.power_w > 0.5 ? `${Math.round(sw.power_w)} W now` : `${Math.round(f.rated_w || 0)} W rated`}</span>
        <span><HIcon name="clock" size={14} /> {runFor ? `on ${runFor}` : "exhaust"}</span>
      </div>
      <Tri name={f.name} state={state} busy={busy} keyBase={kb}
        onPick={(s) => act(`${kb}:${s}`, () => api.fanOverride(f.id, s, s === "on" ? 60 : undefined))} />
    </div>
  );
}

/* ---------- circulation fan ---------- */

export function CircFanCard({ c, circMode, status, busy, act }: { c: Json; circMode: string; status: Json; busy: string; act: Act }) {
  const sw = c.switch || {};
  const on = circOn(c);
  const reachable = sw.reachable !== false;
  const manualActive = c.manual != null && (!c.manual_until || new Date(c.manual_until).getTime() > new Date(status.now).getTime());
  const state = manualActive ? (c.manual ? "on" : "off") : "auto";
  const left = manualActive ? timeUntil(c.manual_until, status.now) : null;
  const would = c.decision && c.decision.target_on;
  let note: string;
  if (!reachable) note = "Smart plug not responding";
  else if (plugPending(sw)) note = `Turning ${sw.commanded ? "on" : "off"} · waiting for the plug to confirm`;
  else if (manualActive) note = `${c.manual ? "On" : "Off"} by you${left ? ` · ${left} left` : ""}`;
  else if (circMode === "shadow") note = `Learning · would ${would ? "run" : "rest"} now`;
  else if (circMode === "off") note = "Automation off";
  else note = on ? "Automatic · running" : "Automatic · resting";
  const today = c.runtime && c.runtime.today_h;
  const kb = `circ:${c.id}`;
  return (
    <div className={`h-fancard ${on ? "on" : ""} ${!reachable ? "offline" : ""}`}>
      <div className="fc-top">
        <FanOrb on={on} tone={!reachable ? "bad" : on ? "good" : "idle"} size={46} />
        <div className="fc-name">
          <strong>{c.name}</strong>
          <span>{note}</span>
        </div>
        <span className={`h-chip ${!reachable ? "bad" : on ? "good" : "subtle"}`}>{!reachable ? "Offline" : on ? "On" : "Off"}</span>
      </div>
      <div className="fc-specs">
        <span><HIcon name="home" size={14} /> {c.location || "Basement"}</span>
        <span><HIcon name="bolt" size={14} /> {sw.power_w > 0.5 ? `${Math.round(sw.power_w)} W now` : `${Math.round(c.rated_w || 0)} W rated`}</span>
        <span><HIcon name="clock" size={14} /> {today != null ? `${today.toFixed(1)} h today` : c.model}</span>
      </div>
      <Tri name={c.name} state={state} busy={busy} keyBase={kb} offLabel="Off 1 h"
        onPick={(s) => act(`${kb}:${s}`, () => api.circulatorManual(c.id, s, s === "auto" ? undefined : 60))} />
    </div>
  );
}

export const CIRC_MODES = [["off", "Off"], ["shadow", "Learning"], ["auto", "Automatic"]] as const;
