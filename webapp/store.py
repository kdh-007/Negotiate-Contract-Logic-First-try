"""팀이 같이 보는 상태 — 최근 수집 결과, 참가여부·담당, 대화. SQLite 파일 하나.

같은 공고는 공고번호-차수(`key`)로 묶는다. 재수집해도 참가여부·대화는 그대로 남는다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

STATUSES = ["검토중", "참가", "보류", "불참"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    params TEXT NOT NULL,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS notice_state (
    key TEXT PRIMARY KEY,
    status TEXT,
    assignee TEXT,
    updated_by TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_key ON comments(key);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ── 수집 결과 ─────────────────────────────────────────────
    def save_run(self, started_at: str, params: dict[str, Any], payload: dict[str, Any]) -> int:
        cur = self._exec(
            "INSERT INTO runs(started_at, finished_at, params, payload) VALUES (?,?,?,?)",
            (started_at, _now(), json.dumps(params, ensure_ascii=False), json.dumps(payload, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def latest_run(self) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM runs WHERE payload IS NOT NULL ORDER BY id DESC LIMIT 1")
        if not rows:
            return None
        row = rows[0]
        return {
            "id": row["id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "params": json.loads(row["params"]),
            **json.loads(row["payload"]),
        }

    # ── 참가여부·담당 ─────────────────────────────────────────
    def set_state(self, key: str, by: str, status: str | None = None, assignee: str | None = None,
                  clear_status: bool = False, clear_assignee: bool = False) -> dict[str, Any]:
        if status is not None and status not in STATUSES:
            raise ValueError(f"참가여부는 {', '.join(STATUSES)} 중 하나: {status}")
        current = self.states().get(key, {})
        new_status = None if clear_status else (status if status is not None else current.get("status"))
        new_assignee = None if clear_assignee else (assignee if assignee is not None else current.get("assignee"))
        self._exec(
            "INSERT INTO notice_state(key, status, assignee, updated_by, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status, assignee=excluded.assignee, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (key, new_status, new_assignee, by, _now()),
        )
        return self.states()[key]

    def states(self) -> dict[str, dict[str, Any]]:
        return {
            r["key"]: {"status": r["status"], "assignee": r["assignee"],
                       "updated_by": r["updated_by"], "updated_at": r["updated_at"]}
            for r in self._query("SELECT * FROM notice_state")
        }

    # ── 대화 ─────────────────────────────────────────────────
    def add_comment(self, key: str, author: str, body: str) -> dict[str, Any]:
        cur = self._exec(
            "INSERT INTO comments(key, author, body, created_at) VALUES (?,?,?,?)", (key, author, body, _now())
        )
        return {"id": cur.lastrowid, "key": key, "author": author, "body": body}

    def comments(self, key: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM comments WHERE key=? ORDER BY id", (key,))]

    def comment_counts(self) -> dict[str, int]:
        return {r["key"]: r["n"] for r in self._query("SELECT key, COUNT(*) AS n FROM comments GROUP BY key")}
