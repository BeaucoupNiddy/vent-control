import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Card } from "../components";
import { localTime } from "../units";

const KINDS = ["", "decision", "command", "fault", "alert", "override", "config", "startup"];

export default function Events() {
  const [kind, setKind] = useState("");
  const [rows, setRows] = useState<Json[]>([]);
  useEffect(() => {
    const load = () => api.events(kind || undefined).then(setRows);
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [kind]);
  return (
    <Card title="Event log" right={
      <div className="seg">{KINDS.map((k) => <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>{k || "all"}</button>)}</div>
    }>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Kind</th><th>Reason</th><th>Message</th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td style={{ whiteSpace: "nowrap" }}>{localTime(e.ts, true)}</td>
                <td><span className={`badge ${e.severity === "critical" ? "crit" : e.severity === "warning" ? "mock" : ""}`}>{e.kind}</span></td>
                <td className="faint">{e.reason_code}</td>
                <td>{e.message}
                  {e.details && <details><summary className="faint">details (readings, decision, command, confirmed state)</summary>
                    <pre>{JSON.stringify(e.details, null, 2)}</pre></details>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
