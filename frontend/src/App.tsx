import { useCallback, useEffect, useState } from "react";
import { api, Json } from "./api";
import { Icon } from "./components";
import Dashboard from "./pages/Dashboard";
import Diagnostics from "./pages/Diagnostics";
import Events from "./pages/Events";
import Home from "./pages/Home";
import History from "./pages/History";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import Simulation from "./pages/Simulation";
import { TempUnit, UnitContext } from "./units";

const TABS = ["Home", "Dashboard", "History", "Simulation", "Reports", "Settings", "Events", "Diagnostics"] as const;
type Tab = (typeof TABS)[number];

function load<T>(k: string, d: T): T {
  try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; }
}
function save(k: string, v: unknown) {
  try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ }
}

export default function App() {
  const [tab, setTab] = useState<Tab>(() => {
    const h = decodeURIComponent(location.hash.slice(1));
    return (TABS as readonly string[]).includes(h) ? (h as Tab) : "Home";
  });
  const [unit, setUnit] = useState<TempUnit>(() => load("ventctl.unit", "F"));
  const [status, setStatus] = useState<Json>(null);
  const [offline, setOffline] = useState(false);

  const refresh = useCallback(() => {
    api.status().then((s) => { setStatus(s); setOffline(false); }).catch(() => setOffline(true));
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);
  useEffect(() => { location.hash = tab; }, [tab]);
  useEffect(() => {
    const onHash = () => {
      const h = decodeURIComponent(location.hash.slice(1));
      if ((TABS as readonly string[]).includes(h)) setTab(h as Tab);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => { save("ventctl.unit", unit); }, [unit]);

  const g = status?.groups?.[0];
  return (
    <UnitContext.Provider value={unit}>
      <header className="topbar">
        <div className="brand">
          <Icon name="fan" size={22} />
          <h1>Basement Ventilation</h1>
          {status && <span className={`badge ${g?.is_on ? "good" : ""}`}>{g?.is_on ? "ON" : "OFF"} · {status.mode.replace("_", " ")}</span>}
          {status?.data_source === "mock" && <span className="badge mock">mock hardware</span>}
          {status?.clock_speed > 1 && <span className="badge mock">demo ×{status.clock_speed}</span>}
        </div>
        <nav className="nav">
          {TABS.map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}
        </nav>
        <div className="spacer" />
        <div className="seg" aria-label="Temperature unit">
          {(["F", "C"] as TempUnit[]).map((x) => <button key={x} className={unit === x ? "active" : ""} onClick={() => setUnit(x)}>°{x}</button>)}
        </div>
      </header>
      <main className="main">
        {offline && <div className="alert critical"><span className="icon"><Icon name="alert" /></span>
          <div><strong>Controller unreachable.</strong> The dashboard cannot reach the backend. Fans keep their last state; plug auto-off timers (if configured) still apply.</div></div>}
        {tab === "Home" && <Home status={status} refresh={refresh} openTab={(t) => setTab(t as Tab)} />}
        {tab === "Dashboard" && <Dashboard status={status} refresh={refresh} />}
        {tab === "History" && <History />}
        {tab === "Simulation" && <Simulation />}
        {tab === "Reports" && <Reports />}
        {tab === "Settings" && <Settings onSaved={refresh} />}
        {tab === "Events" && <Events />}
        {tab === "Diagnostics" && <Diagnostics status={status} />}
        <footer className="faint" style={{ textAlign: "center", padding: "8px 0 24px" }}>
          Ventilation controls moisture exchange, not drainage. It cannot keep a wet basement dry on its own.
        </footer>
      </main>
    </UnitContext.Provider>
  );
}
