"""SQLite storage: settings, history, events. Schema versioned with simple migrations."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import AppConfig

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        source TEXT NOT NULL,              -- mock | measured | backfill
        indoor_t_c REAL, indoor_rh REAL, indoor_td_c REAL, indoor_w REAL,
        outdoor_t_c REAL, outdoor_rh REAL, outdoor_td_c REAL, outdoor_w REAL,
        surface_t_c REAL, surface_source TEXT,
        fans_on INTEGER NOT NULL,          -- number of fans confirmed on
        fans_verified INTEGER NOT NULL,
        power_w REAL,
        removal_g_h REAL,                  -- modeled moisture transfer while fans on (+ removal)
        potential_removal_g_h REAL,        -- what the fans would do if on
        mode TEXT, reason_code TEXT,
        zones TEXT                         -- JSON {zone: {t_c, rh}}
    );
    CREATE INDEX idx_history_ts ON history(ts);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,                -- decision | command | fault | alert | override | config | startup
        severity TEXT NOT NULL,            -- info | warning | critical
        reason_code TEXT,
        message TEXT NOT NULL,
        details TEXT                       -- JSON: readings, decision, command, confirmed state, error
    );
    CREATE INDEX idx_events_ts ON events(ts);
    """,
    # 2: Phase 2-4 columns: per-group state, conditioned-air tracking, dehumidifier monitoring, weather cache
    """
    ALTER TABLE history ADD COLUMN penalty_w REAL;          -- modeled HVAC power to replace lost conditioned air
    ALTER TABLE history ADD COLUMN cond_air_cfm REAL;       -- modeled conditioned air drawn from upstairs
    ALTER TABLE history ADD COLUMN dehum_power_w REAL;      -- measured dehumidifier plug power
    ALTER TABLE history ADD COLUMN dehum_on INTEGER;
    ALTER TABLE history ADD COLUMN groups TEXT;             -- JSON {group: {on, reason_code}}
    CREATE TABLE weather_cache (
        source TEXT NOT NULL,              -- e.g. noaa-global-hourly
        station TEXT NOT NULL,
        ts TEXT NOT NULL,                  -- UTC hour
        temp_c REAL,
        dewpoint_c REAL,
        PRIMARY KEY (source, station, ts)
    );
    """,
    # 3: temperature-aware optimization telemetry, thermal predictions, simulation runs (additive only)
    """
    ALTER TABLE history ADD COLUMN policy TEXT;               -- control policy commanding the fans
    ALTER TABLE history ADD COLUMN utility_g_h REAL;          -- TAMO net utility (drying-equivalent g/h)
    ALTER TABLE history ADD COLUMN urgency REAL;
    ALTER TABLE history ADD COLUMN thermal_penalty_g_h REAL;
    ALTER TABLE history ADD COLUMN energy_penalty_g_h REAL;
    ALTER TABLE history ADD COLUMN pred_fan_dt_f REAL;        -- predicted fan-caused temperature change at horizon
    ALTER TABLE history ADD COLUMN shadow_on INTEGER;         -- what the policy not in control would do
    ALTER TABLE history ADD COLUMN opportunity TEXT;
    ALTER TABLE history ADD COLUMN pipe_t_c REAL;
    CREATE TABLE thermal_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_start TEXT NOT NULL, ts_due TEXT NOT NULL, group_id TEXT NOT NULL,
        horizon_min REAL, start_t_c REAL, inlet_t_c REAL,
        predicted_c REAL, predicted_conservative_c REAL,
        observed_c REAL, error_f REAL,
        model_version TEXT, confidence TEXT, params TEXT
    );
    CREATE INDEX idx_thermal_pred_ts ON thermal_predictions(ts_start);
    CREATE TABLE simulation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, version TEXT NOT NULL, profile TEXT, source TEXT,
        strategies TEXT, params TEXT, summary TEXT
    );
    """,
    # 4: multi-zone monitoring, concrete probe, internal circulation fans
    """
    ALTER TABLE history ADD COLUMN circ TEXT;      -- JSON {circulator: {on, target, reason, commanding}}
    ALTER TABLE history ADD COLUMN surfaces TEXT;  -- JSON {probe: {t_c, dew_c, margin_f, cons_margin_f, srh, risk}}
    CREATE TABLE circulation_cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        circulator_id TEXT NOT NULL, ts_start TEXT NOT NULL, ts_end TEXT NOT NULL,
        reason TEXT, start TEXT, "end" TEXT, context TEXT
    );
    CREATE INDEX idx_circ_cycles ON circulation_cycles(circulator_id, ts_start);
    """,
]


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # one connection shared by API threads and the control loop
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def _fetchall(self, sql: str, args=()) -> list:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def _fetchone(self, sql: str, args=()):
        with self._lock:
            return self.db.execute(sql, args).fetchone()

    def migrate(self) -> int:
        with self._lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
            cur = self._fetchone("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")[0]
            for v, sql in enumerate(MIGRATIONS, start=1):
                if v > cur:
                    self.db.executescript(sql)
                    self.db.execute("INSERT INTO schema_migrations VALUES (?, ?)",
                                    (v, _iso(datetime.now(timezone.utc))))
            self.db.commit()
            return len(MIGRATIONS)

    # ---------------------------------------------------------------- config
    def load_config(self) -> AppConfig | None:
        row = self._fetchone("SELECT value FROM settings WHERE key='config'")
        return AppConfig.model_validate_json(row["value"]) if row else None

    def save_config(self, config: AppConfig) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES('config', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (config.model_dump_json(), _iso(datetime.now(timezone.utc))))
            self.db.commit()

    # ---------------------------------------------------------------- key/value (model parameters etc.)
    def get_kv(self, key: str):
        row = self._fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else None

    def set_kv(self, key: str, value) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, default=str), _iso(datetime.now(timezone.utc))))
            self.db.commit()

    # ---------------------------------------------------------------- thermal predictions
    def add_thermal_prediction(self, row: dict) -> int:
        cols = list(row)
        vals = [_iso(v) if isinstance(v, datetime) else json.dumps(v) if isinstance(v, dict) else v
                for v in row.values()]
        with self._lock:
            cur = self.db.execute(f"INSERT INTO thermal_predictions ({','.join(cols)}) VALUES "
                                  f"({','.join('?' * len(cols))})", vals)
            self.db.commit()
            return cur.lastrowid

    def resolve_thermal_prediction(self, pid: int, observed_c: float | None, error_f: float | None) -> None:
        with self._lock:
            self.db.execute("UPDATE thermal_predictions SET observed_c=?, error_f=? WHERE id=?",
                            (observed_c, error_f, pid))
            self.db.commit()

    def thermal_predictions(self, limit: int = 50) -> list[dict]:
        rows = self._fetchall("SELECT * FROM thermal_predictions ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- simulation runs
    def add_simulation_run(self, version: str, profile: str, source: str, strategies: list[str], params: dict,
                           summary: dict) -> None:
        with self._lock:
            self.db.execute("INSERT INTO simulation_runs (ts, version, profile, source, strategies, params, summary) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (_iso(datetime.now(timezone.utc)), version, profile, source, json.dumps(strategies),
                             json.dumps(params, default=str), json.dumps(summary, default=str)))
            self.db.execute("DELETE FROM simulation_runs WHERE id NOT IN "
                            "(SELECT id FROM simulation_runs ORDER BY id DESC LIMIT 200)")
            self.db.commit()

    def simulation_runs(self, limit: int = 20) -> list[dict]:
        rows = self._fetchall("SELECT * FROM simulation_runs ORDER BY id DESC LIMIT ?", (limit,))
        out = [dict(r) for r in rows]
        for r in out:
            for k in ("strategies", "params", "summary"):
                r[k] = json.loads(r[k]) if r[k] else None
        return out

    # ---------------------------------------------------------------- history
    def add_history(self, ts: datetime, row: dict) -> None:
        cols = ["ts"] + list(row)
        vals = [_iso(ts)] + [json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()]
        with self._lock:
            self.db.execute(f"INSERT INTO history ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            self.db.commit()

    def add_history_many(self, rows: list[tuple[datetime, dict]]) -> None:
        if not rows:
            return
        cols = ["ts"] + list(rows[0][1])
        sql = f"INSERT INTO history ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        data = [[_iso(t)] + [json.dumps(v) if isinstance(v, (dict, list)) else v for v in r.values()] for t, r in rows]
        with self._lock:
            self.db.executemany(sql, data)
            self.db.commit()

    def history(self, start: datetime, end: datetime, max_points: int = 2000) -> list[dict]:
        rows = self._fetchall("SELECT * FROM history WHERE ts >= ? AND ts <= ? ORDER BY ts",
                               (_iso(start), _iso(end)))
        out = [dict(r) for r in rows]
        for r in out:
            r["zones"] = json.loads(r["zones"]) if r["zones"] else {}
            r["groups"] = json.loads(r["groups"]) if r.get("groups") else {}
            r["circ"] = json.loads(r["circ"]) if r.get("circ") else {}
            r["surfaces"] = json.loads(r["surfaces"]) if r.get("surfaces") else {}
        if len(out) > max_points:
            stride = len(out) / max_points
            out = [out[int(i * stride)] for i in range(max_points)]
        return out

    def add_circulation_cycle(self, row: dict) -> int:
        with self._lock:
            cur = self.db.execute(
                'INSERT INTO circulation_cycles (circulator_id, ts_start, ts_end, reason, start, "end", context) '
                'VALUES (?,?,?,?,?,?,?)',
                (row["circulator_id"], _iso(row["ts_start"]), _iso(row["ts_end"]), row.get("reason"),
                 json.dumps(row.get("start"), default=str), json.dumps(row.get("end"), default=str),
                 json.dumps(row.get("context"), default=str)))
            self.db.commit()
            return cur.lastrowid

    def circulation_cycles(self, circulator_id: str | None = None, limit: int = 500) -> list[dict]:
        q, args = "SELECT * FROM circulation_cycles", []
        if circulator_id:
            q += " WHERE circulator_id = ?"
            args.append(circulator_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        out = [dict(r) for r in self._fetchall(q, args)]
        for r in out:
            for k in ("start", "end", "context"):
                r[k] = json.loads(r[k]) if r[k] else None
        return out

    def history_count(self) -> int:
        return self._fetchone("SELECT COUNT(*) FROM history")[0]

    def latest_history_ts(self) -> datetime | None:
        r = self._fetchone("SELECT MAX(ts) FROM history")[0]
        return datetime.fromisoformat(r) if r else None

    # ---------------------------------------------------------------- events
    def add_event(self, ts: datetime, kind: str, severity: str, message: str, reason_code: str | None = None,
                  details: dict | None = None) -> None:
        with self._lock:
            self.db.execute("INSERT INTO events (ts, kind, severity, reason_code, message, details) VALUES (?,?,?,?,?,?)",
                            (_iso(ts), kind, severity, reason_code, message,
                             json.dumps(details, default=str) if details else None))
            self.db.commit()

    def events(self, limit: int = 200, kind: str | None = None, since: datetime | None = None) -> list[dict]:
        q = "SELECT * FROM events WHERE 1=1"
        args: list = []
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        if since:
            q += " AND ts >= ?"
            args.append(_iso(since))
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        out = [dict(r) for r in self._fetchall(q, args)]
        for r in out:
            r["details"] = json.loads(r["details"]) if r["details"] else None
        return out

    # ---------------------------------------------------------------- weather cache
    def cache_weather(self, source: str, station: str, rows: list[tuple[datetime, float | None, float | None]]) -> int:
        with self._lock:
            self.db.executemany(
                "INSERT OR REPLACE INTO weather_cache (source, station, ts, temp_c, dewpoint_c) VALUES (?,?,?,?,?)",
                [(source, station, _iso(t), tc, td) for t, tc, td in rows])
            self.db.commit()
        return len(rows)

    def cached_weather(self, source: str, station: str, start: datetime, end: datetime) -> list[dict]:
        rows = self._fetchall(
            "SELECT ts, temp_c, dewpoint_c FROM weather_cache WHERE source=? AND station=? AND ts>=? AND ts<? ORDER BY ts",
            (source, station, _iso(start), _iso(end)))
        return [dict(r) for r in rows]

    def weather_coverage(self) -> list[dict]:
        rows = self._fetchall("SELECT source, station, MIN(ts) AS first, MAX(ts) AS last, COUNT(*) AS hours "
                               "FROM weather_cache GROUP BY source, station")
        return [dict(r) for r in rows]

    def prune(self, now: datetime, retention_days: int) -> None:
        cutoff = _iso(now - timedelta(days=retention_days))
        with self._lock:
            self.db.execute("DELETE FROM history WHERE ts < ?", (cutoff,))
            self.db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self.db.commit()
