"""SQLite 持久层(小而稳,单进程内使用)。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workloads (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    entrypoint   TEXT DEFAULT '',
    command      TEXT DEFAULT '',        -- 自定义启动命令(非 python 时使用)
    auto_restart INTEGER DEFAULT 1,
    autostart    INTEGER DEFAULT 0,
    env          TEXT DEFAULT '{}',
    created_at   REAL NOT NULL,
    restarts     INTEGER DEFAULT 0,
    last_exit    INTEGER,
    last_started REAL,
    status       TEXT DEFAULT 'stopped',
    pid          INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    message TEXT NOT NULL,
    meta    TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ssh_keys (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    key_line    TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    added_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id        TEXT PRIMARY KEY,
    token     TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used REAL
);
CREATE TABLE IF NOT EXISTS backups (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'all',   -- all | workload:<id>
    storage    TEXT NOT NULL DEFAULT 'r2',    -- r2 | local
    object_key TEXT NOT NULL,
    size       INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
"""


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Database:
    """线程安全的 sqlite 封装(WAL + 单锁)。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def execute(self, sql: str, params=()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def query(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def queryone(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- settings kv ----
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.queryone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ---- workloads ----
    def list_workloads(self) -> list[dict]:
        rows = self.query("SELECT * FROM workloads ORDER BY created_at ASC")
        for r in rows:
            r["env"] = json.loads(r.get("env") or "{}")
        return rows

    def get_workload(self, wid: str) -> dict | None:
        row = self.queryone("SELECT * FROM workloads WHERE id=?", (wid,))
        if row:
            row["env"] = json.loads(row.get("env") or "{}")
        return row

    def create_workload(self, wid: str, name: str, entrypoint: str = "",
                        command: str = "", auto_restart: bool = True,
                        autostart: bool = False, env: dict | None = None) -> dict:
        self.execute(
            "INSERT INTO workloads(id,name,entrypoint,command,auto_restart,autostart,env,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (wid, name, entrypoint, command, int(auto_restart), int(autostart),
             json.dumps(env or {}, ensure_ascii=False), now()),
        )
        return self.get_workload(wid)

    def update_workload(self, wid: str, **fields) -> None:
        allowed = {"name", "entrypoint", "command", "auto_restart", "autostart", "env",
                   "restarts", "last_exit", "last_started", "status", "pid"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "env" and isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            if k in ("auto_restart", "autostart") and isinstance(v, bool):
                v = int(v)
            sets.append(f"{k}=?")
            params.append(v)
        if not sets:
            return
        params.append(wid)
        self.execute(f"UPDATE workloads SET {', '.join(sets)} WHERE id=?", params)

    def delete_workload(self, wid: str) -> None:
        self.execute("DELETE FROM workloads WHERE id=?", (wid,))

    # ---- events ----
    def add_event(self, kind: str, message: str, meta: dict | None = None) -> dict:
        ev = {"id": None, "ts": now(), "kind": kind, "message": message,
              "meta": meta or {}}
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(ts,kind,message,meta) VALUES(?,?,?,?)",
                (ev["ts"], kind, message, json.dumps(ev["meta"], ensure_ascii=False)),
            )
            ev["id"] = cur.lastrowid
            # 只保留最近 500 条
            self._conn.execute(
                "DELETE FROM events WHERE id <= ("
                " SELECT MAX(id) FROM events) - 500")
            self._conn.commit()
        return ev

    def list_events(self, limit: int = 100) -> list[dict]:
        rows = self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),))
        for r in rows:
            r["meta"] = json.loads(r.get("meta") or "{}")
        return rows

    # ---- ssh keys ----
    def list_ssh_keys(self) -> list[dict]:
        return self.query("SELECT * FROM ssh_keys ORDER BY added_at ASC")

    def add_ssh_key(self, kid: str, name: str, key_line: str, fingerprint: str) -> None:
        self.execute(
            "INSERT INTO ssh_keys(id,name,key_line,fingerprint,added_at) VALUES(?,?,?,?,?)",
            (kid, name, key_line, fingerprint, now()),
        )

    def delete_ssh_key(self, kid: str) -> None:
        self.execute("DELETE FROM ssh_keys WHERE id=?", (kid,))

    def update_ssh_key_fp(self, kid: str, fingerprint: str) -> None:
        self.execute("UPDATE ssh_keys SET fingerprint=? WHERE id=?", (fingerprint, kid))

    # ---- api keys ----
    def list_api_keys(self) -> list[dict]:
        return self.query("SELECT id,token,name,created_at,last_used FROM api_keys ORDER BY created_at ASC")

    def get_api_key_by_token(self, token: str) -> dict | None:
        return self.queryone("SELECT * FROM api_keys WHERE token=?", (token,))

    def add_api_key(self, kid: str, token: str, name: str) -> None:
        self.execute(
            "INSERT INTO api_keys(id,token,name,created_at) VALUES(?,?,?,?)",
            (kid, token, name, now()),
        )

    def touch_api_key(self, kid: str) -> None:
        self.execute("UPDATE api_keys SET last_used=? WHERE id=?", (now(), kid))

    def delete_api_key(self, kid: str) -> None:
        self.execute("DELETE FROM api_keys WHERE id=?", (kid,))

    # ---- backups ----
    def list_backups(self) -> list[dict]:
        return self.query("SELECT * FROM backups ORDER BY created_at DESC")

    def get_backup(self, bid: str) -> dict | None:
        return self.queryone("SELECT * FROM backups WHERE id=?", (bid,))

    def add_backup(self, bid: str, name: str, scope: str, storage: str,
                   object_key: str, size: int) -> None:
        self.execute(
            "INSERT INTO backups(id,name,scope,storage,object_key,size,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (bid, name, scope, storage, object_key, size, now()),
        )

    def delete_backup(self, bid: str) -> None:
        self.execute("DELETE FROM backups WHERE id=?", (bid,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
