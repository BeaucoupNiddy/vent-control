import { useState } from "react";
import { api, Json } from "../api";
import { Card } from "../components";

type Scope = "since" | "before" | "all";

function localInput(d: Date) {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Settings card: delete recorded readings and events, all of them or from/before a point in time. */
export function DataResetCard() {
  const [scope, setScope] = useState<Scope>("since");
  const [at, setAt] = useState(() => localInput(new Date(Date.now() - 24 * 3600 * 1000)));
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Json>(null);
  const [err, setErr] = useState("");

  const when = new Date(at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const summary = scope === "all" ? "all recorded readings and events. The history starts again from now"
    : scope === "since" ? `everything recorded from ${when} up to now`
      : `everything recorded before ${when}`;

  async function run() {
    setBusy(true); setErr(""); setResult(null);
    try {
      // send the browser's local time with its offset, so the server deletes exactly what was picked
      const iso = scope === "all" ? undefined : new Date(at).toISOString();
      setResult(await api.resetData(scope, iso));
      setConfirming(false);
    } catch (e: Json) { setErr(String(e.message || e)); } finally { setBusy(false); }
  }

  return (
    <Card title="Reset recorded data">
      <p className="muted" style={{ marginTop: 0 }}>
        Clear out old or bad readings, for example from before your real sensors were connected. This deletes the
        history charts, events and fan-cycle records in the chosen period. Your settings, plug setup and learned
        models are kept, and a backup copy of everything is saved first.
      </p>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div className="field">
          <label htmlFor="rscope">Delete</label>
          <select id="rscope" value={scope} onChange={(e) => { setScope(e.target.value as Scope); setConfirming(false); }}>
            <option value="since">From a point in time up to now</option>
            <option value="before">Everything older than a point in time</option>
            <option value="all">Everything (start fresh from now)</option>
          </select>
        </div>
        {scope !== "all" && (
          <div className="field">
            <label htmlFor="rat">Point in time</label>
            <input id="rat" type="datetime-local" value={at} max={localInput(new Date())}
              onChange={(e) => { setAt(e.target.value); setConfirming(false); }} />
          </div>
        )}
        {!confirming && <button className="btn" disabled={busy || (scope !== "all" && !at)} onClick={() => setConfirming(true)}>Delete…</button>}
      </div>
      {confirming && (
        <div className="alert warning" style={{ marginTop: 12 }}>
          <div>
            This will delete <strong>{summary}</strong>. A backup is saved in the app's data/backups folder.
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn primary" disabled={busy} onClick={run}>{busy ? "Deleting…" : "Yes, delete it"}</button>
              <button className="btn" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
      {result && <p className="muted">Done. Deleted {result.deleted.history.toLocaleString()} readings and{" "}
        {result.deleted.events.toLocaleString()} events.{result.backup ? " Backup saved." : ""}</p>}
      {err && <p className="error">{err}</p>}
    </Card>
  );
}
