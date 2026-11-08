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
]


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def migrate(self) -> int:
        with self._lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
            cur = self.db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            for v, sql in enumerate(MIGRATIONS, start=1):
                if v > cur:
                    self.db.executescript(sql)
                    self.db.execute("INSERT INTO schema_migrations VALUES (?, ?)",
                                    (v, _iso(datetime.now(timezone.utc))))
            self.db.commit()
            return len(MIGRATIONS)

    # ---------------------------------------------------------------- config
    def load_config(self) -> AppConfig | None:
        row = self.db.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        return AppConfig.model_validate_json(row["value"]) if row else None

    def save_config(self, config: AppConfig) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES('config', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (config.model_dump_json(), _iso(datetime.now(timezone.utc))))
            self.db.commit()

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
        rows = self.db.execute("SELECT * FROM history WHERE ts >= ? AND ts <= ? ORDER BY ts",
                               (_iso(start), _iso(end))).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["zones"] = json.loads(r["zones"]) if r["zones"] else {}
        if len(out) > max_points:
            stride = len(out) / max_points
            out = [out[int(i * stride)] for i in range(max_points)]
        return out

    def history_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM history").fetchone()[0]

    def latest_history_ts(self) -> datetime | None:
        r = self.db.execute("SELECT MAX(ts) FROM history").fetchone()[0]
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
        out = [dict(r) for r in self.db.execute(q, args).fetchall()]
        for r in out:
            r["details"] = json.loads(r["details"]) if r["details"] else None
        return out

    def prune(self, now: datetime, retention_days: int) -> None:
        cutoff = _iso(now - timedelta(days=retention_days))
        with self._lock:
            self.db.execute("DELETE FROM history WHERE ts < ?", (cutoff,))
            self.db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self.db.commit()
