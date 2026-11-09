import { useEffect, useRef, useState } from "react";
import { api, Json } from "../api";
import { Card } from "../components";

const LABEL: Record<string, string> = {
  running: "Updating…",
  updated: "Updated",
  up_to_date: "Up to date",
  failed: "Update didn't install",
  offline: "Couldn't reach GitHub",
  unknown: "Unknown",
};

/** Settings card: shows the installed version and installs the latest version from GitHub on request. */
export function UpdateCard() {
  const [info, setInfo] = useState<Json>(null);
  const [err, setErr] = useState("");
  const [restarting, setRestarting] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  async function load() {
    try {
      const i = await api.updateInfo();
      setInfo(i);
      setRestarting(false);
      return i;
    } catch {
      // the app is unreachable for a few seconds while it restarts on the new version
      setRestarting(true);
      return null;
    }
  }

  const busy = restarting || info?.status?.state === "running";

  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!busy) return;
    timer.current = window.setInterval(load, 3000);
    return () => window.clearInterval(timer.current);
  }, [busy]);

  async function updateNow() {
    setErr("");
    try {
      await api.updateNow();
    } catch (e) {
      setErr(String((e as Error).message));
    }
    load();
  }

  if (!info) return <Card title="App updates">{restarting ? "Restarting…" : "Loading…"}</Card>;
  const st = info.status;
  const v = info.version;
  const tone = st?.state === "failed" || st?.state === "offline" ? "warn" : "";
  return (
    <Card title="App updates" right={
      <button className="btn primary" disabled={!info.available || busy} onClick={updateNow}>
        {busy ? "Updating…" : "Update now"}
      </button>}>
      <p className="note">
        The app checks GitHub every 30 minutes and installs new versions by itself. Use <b>Update now</b> to get the latest
        version straight away. It only switches over if the tests pass; otherwise it keeps the version that works.
      </p>
      {!info.available && <p className="error">{info.reason}</p>}
      {err && <p className="error">{err}</p>}
      <div className="form update-info">
        <div><span className="label">Installed version</span><div>{v ? <>{v.subject} <span className="faint">({v.commit}, {new Date(v.date).toLocaleString()})</span></> : "—"}</div></div>
        <div style={{ gridColumn: "span 2" }}><span className="label">Last update check</span><div>
          {restarting ? "Restarting on the new version…" : st ? <>
            <span className={`badge ${tone}`}>{LABEL[st.state] ?? st.state}</span> {st.message}{" "}
            <span className="faint">{new Date(st.time).toLocaleString()}</span></> : "No update has run yet."}
        </div></div>
      </div>
      {info.log?.length > 0 && (
        <p><button className="btn" onClick={() => setShowLog(!showLog)}>{showLog ? "Hide" : "Show"} update log</button></p>
      )}
      {showLog && <pre className="faint" style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{info.log.join("\n")}</pre>}
    </Card>
  );
}
