import { useCallback, useEffect, useState } from "react";
import { api, Json } from "./api";
import { Icon } from "./components";
import Ask from "./pages/Ask";
import Dashboard from "./pages/Dashboard";
import Diagnostics from "./pages/Diagnostics";
import Events from "./pages/Events";
import Home from "./pages/Home";
import History from "./pages/History";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import Setup from "./pages/Setup";
import Simulation from "./pages/Simulation";
import { TempUnit, UnitContext } from "./units";

const TABS = ["Home", "Ask", "Setup", "Dashboard", "History", "Simulation", "Reports", "Settings", "Events", "Diagnostics"] as const;
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
  const [setup, setSetup] = useState<Json>(null);
  useEffect(() => { if (tab === "Home") api.setup(false).then(setSetup).catch(() => setSetup(null)); }, [tab]);

  const refresh = useCallback(() => {
    api.status().then((s) => { setStatus(s); setOffline(false); }).catch(() => setOffline(true));
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);
  useEffect(() => {
    location.hash = tab;
    // keep the chosen tab visible in the scrolling tab row on phones
    document.querySelector(".nav button.active")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [tab]);
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
        {tab === "Home" && setup && !setup.finished && (
          <div className="alert info setup-banner"><span className="icon"><Icon name="info" /></span>
            <div><strong>{setup.done === 0 && !setup.progress.started_at ? "Ready to connect your real sensors and fans?" : setup.progress.paused ? "Setup is paused." : "Setup in progress."}</strong>{" "}
              {setup.done} of {setup.total} steps done. The guided setup checks each item as you go, and you can pause any time.
              <div style={{ marginTop: 6 }}><button className="btn primary" onClick={() => setTab("Setup")}>{setup.progress.started_at ? "Resume setup" : "Start setup"}</button></div></div>
          </div>)}
        {tab === "Home" && <Home status={status} refresh={refresh} openTab={(t) => setTab(t as Tab)} />}
        {tab === "Ask" && <Ask />}
        {tab === "Dashboard" && <Dashboard status={status} refresh={refresh} />}
        {tab === "History" && <History />}
        {tab === "Simulation" && <Simulation />}
        {tab === "Reports" && <Reports />}
        {tab === "Setup" && <Setup onSaved={refresh} />}
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
