"""SQLite 存储：tasks / files 表，WAL 模式。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    platform TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    error_code TEXT,
    error_detail TEXT,
    error_source TEXT,
    error_retryable INTEGER,
    warnings_json TEXT,
    retries INTEGER NOT NULL DEFAULT 0,
    params_json TEXT,
    out_dir TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    done_at TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    kind TEXT NOT NULL,
    path TEXT,
    size INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_task ON files(task_id);
CREATE TABLE IF NOT EXISTS asr (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    status TEXT NOT NULL,
    segments_json TEXT,
    raw_path TEXT,
    normalized_path TEXT,
    txt_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asr_task ON asr(task_id);
"""

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(tasks)")}
        for name, sql_type in (
            ("error_source", "TEXT"),
            ("error_retryable", "INTEGER"),
            ("warnings_json", "TEXT"),
        ):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {sql_type}")
        self._conn.execute("PRAGMA user_version=2")
        self._conn.commit()
        self._lock = threading.Lock()

    def create_task(self, url: str, platform: str, params: dict) -> str:
        task_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tasks (id, url, platform, status, params_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, url, platform, STATUS_QUEUED, json.dumps(params, ensure_ascii=False), now()),
            )
            self._conn.commit()
        return task_id

    def mark_interrupted(self):
        """上次进程异常退出遗留的 queued/running 任务统一标记为 interrupted，避免假运行状态残留。"""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status=?, error_code='interrupted', error_detail='上次进程退出时任务未完成',"
                " error_source='lifecycle', error_retryable=1"
                " WHERE status IN (?, ?)",
                (STATUS_INTERRUPTED, STATUS_QUEUED, STATUS_RUNNING),
            )
            self._conn.commit()

    def set_status(self, task_id: str, status: str, error_code: str | None = None,
                   error_detail: str | None = None, *, error_source: str | None = None,
                   error_retryable: bool | None = None):
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status=?, error_code=?, error_detail=?, error_source=?, error_retryable=?,"
                " started_at=COALESCE(started_at, ?),"
                " done_at=CASE WHEN ? IN ('done','failed') THEN ? ELSE done_at END"
                " WHERE id=?",
                (status, error_code, error_detail, error_source,
                 None if error_retryable is None else int(error_retryable),
                 now(), status, now(), task_id),
            )
            self._conn.commit()

    def add_retry(self, task_id: str):
        with self._lock, self._conn:
            self._conn.execute("UPDATE tasks SET retries = retries + 1 WHERE id=?", (task_id,))
            self._conn.commit()

    def set_out_dir(self, task_id: str, out_dir: str):
        with self._lock, self._conn:
            self._conn.execute("UPDATE tasks SET out_dir=? WHERE id=?", (out_dir, task_id))
            self._conn.commit()

    def add_file(self, task_id: str, kind: str, path: str, size: int | None = None):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO files (task_id, kind, path, size, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, kind, path, size, now()),
            )
            self._conn.commit()

    def add_asr(self, task_id: str, status: str, segments: list[dict] | None = None,
                raw_path: str | None = None, normalized_path: str | None = None, txt_path: str | None = None):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO asr (task_id, status, segments_json, raw_path, normalized_path, txt_path, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, status,
                 json.dumps(segments, ensure_ascii=False) if segments is not None else None,
                 raw_path, normalized_path, txt_path, now()),
            )
            self._conn.commit()

    def get_asr(self, task_id: str) -> dict | None:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM asr WHERE task_id=?", (task_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            d = dict(zip(cols, row))
            d["segments"] = json.loads(d.pop("segments_json") or "[]")
            return d

    def get_task(self, task_id: str) -> dict | None:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            task = dict(zip(cols, row))
            files = self._conn.execute(
                "SELECT kind, path, size FROM files WHERE task_id=?", (task_id,)
            ).fetchall()
        task["files"] = [{"kind": f[0], "path": f[1], "size": f[2]} for f in files]
        task["params"] = json.loads(task.get("params_json") or "{}")
        task["warnings"] = json.loads(task.pop("warnings_json") or "[]")
        if task.get("error_retryable") is not None:
            task["error_retryable"] = bool(task["error_retryable"])
        task.pop("params_json", None)
        return task

    def set_warnings(self, task_id: str, warnings: list[str]) -> None:
        normalized = list(dict.fromkeys(item for item in warnings if item))
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET warnings_json=? WHERE id=?",
                (json.dumps(normalized, ensure_ascii=False), task_id),
            )

    def list_tasks(self, limit: int = 50) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id, url, platform, status, error_code, created_at, done_at"
                " FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(zip(("id", "url", "platform", "status", "error_code", "created_at", "done_at"), r)) for r in rows]

    def close(self):
        with self._lock:
            self._conn.close()
