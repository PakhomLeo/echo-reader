"""Versioned durable storage for import batches, jobs, and timelines."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ALL_STATUSES = (
    "queued", "running", "pending_index", "succeeded", "failed", "cancelled", "interrupted",
)
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
SCHEMA_VERSION = 2
HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class SchemaVersionError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def sanitize_url(value: str) -> str:
    # This is intentionally the public MediaLab boundary. Both components must
    # apply one authoritative platform/query policy to durable URLs.
    from medialab import sanitize_persisted_url

    return sanitize_persisted_url(value)


def sanitize_text(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        core = candidate.rstrip(".,;:!?)]}")
        return sanitize_url(core) + candidate[len(core):]

    return HTTP_URL_PATTERN.sub(replace, value)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def _library_key(root: Path) -> str:
    normalized = str(root.resolve()).replace("\\", "/")
    if os.name == "nt":
        normalized = normalized.casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _relative_locator(value: str | None, root: Path) -> str | None:
    if not value:
        return None
    try:
        path = Path(value).resolve()
        relative = path.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not relative.parts:
        return None
    return PurePosixPath(*relative.parts).as_posix()


SCHEMA = """
CREATE TABLE batches (
    id TEXT PRIMARY KEY, options_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
    ordinal INTEGER NOT NULL, attempt INTEGER NOT NULL,
    url TEXT NOT NULL, request_json TEXT NOT NULL,
    requested_mode TEXT NOT NULL, effective_mode TEXT NOT NULL,
    asr_backend TEXT, status TEXT NOT NULL,
    stage TEXT NOT NULL, message TEXT NOT NULL, progress REAL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    started_at TEXT, completed_at TEXT, item_id TEXT,
    library_key TEXT NOT NULL, item_path TEXT,
    result_json TEXT, error_json TEXT, warning TEXT,
    quality_json TEXT, duplicate_json TEXT
);
CREATE INDEX jobs_batch_idx ON jobs(batch_id, ordinal, attempt);
CREATE INDEX jobs_status_idx ON jobs(status, created_at DESC);
CREATE INDEX jobs_library_idx ON jobs(library_key, status);
CREATE TABLE job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, event TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX events_job_idx ON job_events(job_id, id);
"""


class ImportStore:
    def __init__(self, path: str | Path, content_root: str | Path,
                 *, terminal_limit: int = 1000) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.content_root = Path(content_root).resolve()
        self.library_key = _library_key(self.content_root)
        self.terminal_limit = terminal_limit
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            with self._lock:
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA busy_timeout=5000")
                self._db.execute("PRAGMA foreign_keys=ON")
                self._db.execute("PRAGMA secure_delete=ON")
                self._initialize_schema()
                self._scrub_history()
                self._recover_interrupted()
                self._trim()
                self._db.commit()
        except BaseException:
            self._db.close()
            raise

    def _initialize_schema(self) -> None:
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"imports.db schema {version} is newer than supported schema {SCHEMA_VERSION}"
            )
        exists = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        if not exists:
            self._db.executescript(SCHEMA)
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._db.commit()
            return
        if version == 0:
            self._scrub_history()
            self._db.execute("PRAGMA user_version=1")
            self._db.commit()
            version = 1
        if version < SCHEMA_VERSION:
            self._backup(version)
        while version < SCHEMA_VERSION:
            if version == 1:
                self._migrate_v1_to_v2()
                version = 2
            else:
                raise SchemaVersionError(f"No imports.db migration from schema {version}")

    def _backup(self, version: int) -> None:
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.path.with_name(f"{self.path.name}.backup-v{version}-{suffix}")
        with sqlite3.connect(target) as backup:
            self._db.backup(backup)

    def _migrate_v1_to_v2(self) -> None:
        rows = self._db.execute("SELECT * FROM jobs").fetchall()
        events = self._db.execute("SELECT * FROM job_events ORDER BY id").fetchall()
        with self._db:
            self._db.execute("DROP INDEX IF EXISTS jobs_batch_idx")
            self._db.execute("DROP INDEX IF EXISTS jobs_status_idx")
            self._db.execute("DROP INDEX IF EXISTS events_job_idx")
            self._db.execute("ALTER TABLE job_events RENAME TO job_events_legacy")
            self._db.execute("ALTER TABLE jobs RENAME TO jobs_legacy")
            statements = [statement.strip() for statement in SCHEMA.split(";") if statement.strip()]
            # batches already exists in legacy schema.
            for statement in statements:
                if statement.startswith("CREATE TABLE batches"):
                    continue
                self._db.execute(statement)
            columns = (
                "id,batch_id,ordinal,attempt,url,request_json,requested_mode,effective_mode,"
                "asr_backend,status,stage,message,progress,created_at,updated_at,started_at,"
                "completed_at,item_id,library_key,item_path,result_json,error_json,warning,"
                "quality_json,duplicate_json"
            )
            for row in rows:
                locator = _relative_locator(row["out_dir"], self.content_root)
                error_json = row["error_json"]
                message = row["message"]
                if row["status"] == "pending_index" and row["out_dir"] and not locator:
                    error = {"code": "migration_path_outside_library",
                             "message": "Stored item path is outside the configured library",
                             "retryable": False, "source": "import_service"}
                    error_json = json.dumps(error)
                    message = error["message"]
                values = [row[name] for name in (
                    "id", "batch_id", "ordinal", "attempt", "url", "request_json",
                    "requested_mode", "effective_mode", "asr_backend", "status", "stage",
                )]
                values.extend([message, row["progress"], row["created_at"], row["updated_at"],
                               row["started_at"], row["completed_at"], row["item_id"],
                               self.library_key, locator, row["result_json"], error_json,
                               row["warning"], row["quality_json"], row["duplicate_json"]])
                self._db.execute(
                    f"INSERT INTO jobs ({columns}) VALUES ({','.join('?' for _ in values)})", values
                )
            for event in events:
                self._db.execute(
                    "INSERT INTO job_events(id,job_id,created_at,event,payload_json) VALUES (?,?,?,?,?)",
                    tuple(event),
                )
            self._db.execute("DROP TABLE job_events_legacy")
            self._db.execute("DROP TABLE jobs_legacy")
            self._db.execute("PRAGMA user_version=2")

    def _scrub_history(self) -> None:
        tables = {row[0] for row in self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "batches" in tables:
            for row in self._db.execute("SELECT id,options_json FROM batches").fetchall():
                self._scrub_json_column("batches", "id", row["id"], "options_json", row["options_json"])
        if "jobs" in tables:
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(jobs)")}
            selected = [name for name in (
                "url", "request_json", "result_json", "error_json", "warning", "message",
                "quality_json", "duplicate_json",
            ) if name in columns]
            for row in self._db.execute(f"SELECT id,{','.join(selected)} FROM jobs").fetchall():
                for column in selected:
                    value = row[column]
                    if value is None:
                        continue
                    clean = self._sanitize_stored(column, value)
                    if clean != value:
                        self._db.execute(f"UPDATE jobs SET {column}=? WHERE id=?", (clean, row["id"]))
        if "job_events" in tables:
            for row in self._db.execute("SELECT id,event,payload_json FROM job_events").fetchall():
                clean_event = sanitize_text(row["event"])
                clean_payload = self._sanitize_stored("payload_json", row["payload_json"])
                if clean_event != row["event"] or clean_payload != row["payload_json"]:
                    self._db.execute(
                        "UPDATE job_events SET event=?,payload_json=? WHERE id=?",
                        (clean_event, clean_payload, row["id"]),
                    )
        self._db.commit()
        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _scrub_json_column(self, table: str, key: str, identity: str,
                           column: str, value: str) -> None:
        clean = self._sanitize_stored(column, value)
        if clean != value:
            self._db.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (clean, identity))

    def _sanitize_stored(self, column: str, value: str) -> str:
        if column == "url":
            return sanitize_url(value)
        if column.endswith("_json"):
            try:
                return json.dumps(self._durable_payload(json.loads(value)), ensure_ascii=False)
            except (TypeError, ValueError):
                return json.dumps(sanitize_text(value), ensure_ascii=False)
        return sanitize_text(value)

    def _durable_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if str(key) == "out_dir":
                    locator = _relative_locator(item, self.content_root) if isinstance(item, str) else None
                    if locator:
                        result["item_path"] = locator
                    continue
                result[str(key)] = self._durable_payload(item)
            return result
        if isinstance(value, list):
            return [self._durable_payload(item) for item in value]
        return sanitize_text(value) if isinstance(value, str) else value

    def _recover_interrupted(self) -> None:
        timestamp = now()
        for row in self._db.execute(
            "SELECT id FROM jobs WHERE status IN ('queued','running')"
        ).fetchall():
            error = {"code": "interrupted", "message": "Import interrupted by service restart",
                     "retryable": True, "source": "import_service"}
            self._db.execute(
                "UPDATE jobs SET status='interrupted',stage='interrupted',message=?,error_json=?,completed_at=?,updated_at=? WHERE id=?",
                (error["message"], json.dumps(error), timestamp, timestamp, row["id"]),
            )
            self._insert_event(row["id"], "interrupted", error, timestamp)
        for row in self._db.execute(
            "SELECT id FROM jobs WHERE status='pending_index' AND error_json IS NULL"
        ).fetchall():
            error = {"code": "index_interrupted", "message": "Indexing interrupted by service restart",
                     "retryable": True, "source": "import_service"}
            self._db.execute(
                "UPDATE jobs SET stage='pending_index',message=?,error_json=?,completed_at=?,updated_at=? WHERE id=?",
                (error["message"], json.dumps(error), timestamp, timestamp, row["id"]),
            )
            self._insert_event(row["id"], "index_interrupted", error, timestamp)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def set_content_root(self, root: str | Path) -> None:
        with self._lock:
            self.content_root = Path(root).resolve()
            self.library_key = _library_key(self.content_root)

    def create_batch(self, urls: Iterable[str], options: dict[str, Any],
                     plans: list[dict[str, Any]]) -> dict[str, Any]:
        urls = list(urls)
        batch_id, timestamp = str(uuid.uuid4()), now()
        with self._lock, self._db:
            self._db.execute("INSERT INTO batches VALUES (?,?,?,?)", (
                batch_id, json.dumps(sanitize_payload(options)), timestamp, timestamp,
            ))
            for ordinal, (url, plan) in enumerate(zip(urls, plans), 1):
                request = sanitize_payload({"url": sanitize_url(url), **options})
                requested = str(plan.get("requested_mode") or options.get("mode", "auto"))
                effective = str(plan.get("resolved_mode") or plan.get("effective_mode") or
                                plan.get("mode") or requested)
                backend_value = plan.get("resolved_backend", plan.get("asr_backend", options.get("asr_backend")))
                backend = str(backend_value) if backend_value is not None else None
                if backend == "local":
                    request["speed_factor"], request["timeout"] = 1.0, None
                job_id = str(uuid.uuid4())
                self._db.execute(
                    """INSERT INTO jobs
                    (id,batch_id,ordinal,attempt,url,request_json,requested_mode,effective_mode,
                     asr_backend,status,stage,message,progress,created_at,updated_at,library_key)
                    VALUES (?,?,?,?,?,?,?,?,?,'queued','queued','Waiting to start',0,?,?,?)""",
                    (job_id, batch_id, ordinal, 1, sanitize_url(url), json.dumps(request),
                     requested, effective, backend, timestamp, timestamp, self.library_key),
                )
                self._insert_event(job_id, "queued", {"stage": "queued", "message": "Waiting to start"}, timestamp)
        return self.get_batch(batch_id)  # type: ignore[return-value]

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self._db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                return None
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE batch_id=? ORDER BY ordinal,attempt", (batch_id,)
            ).fetchall()
        items = [self._job(row) for row in rows]
        counts = {status: 0 for status in ALL_STATUSES}
        for item in items:
            counts[item["status"]] += 1
        active = any(item["is_active"] for item in items)
        waiting = any(item["status"] == "queued" for item in items)
        failed = any(item["status"] in {"failed", "interrupted"} for item in items)
        pending = any(item["status"] == "pending_index" for item in items)
        status = ("running" if active else "queued" if waiting else "pending_index" if pending else
                  "failed" if failed else "cancelled" if items and all(i["status"] == "cancelled" for i in items)
                  else "succeeded")
        return {"id": batch_id, "status": status, "total": len(items), "counts": counts,
                "active_count": sum(1 for item in items if item["is_active"]),
                "created_at": batch["created_at"], "updated_at": batch["updated_at"], "items": items}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list_jobs(self, *, page: int, page_size: int, status: str | None = None,
                  batch_id: str | None = None) -> dict[str, Any]:
        clauses, values = [], []
        if status:
            clauses.append("status=?"); values.append(status)
        if batch_id:
            clauses.append("batch_id=?"); values.append(batch_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        count_where = " WHERE batch_id=?" if batch_id else ""
        count_values = (batch_id,) if batch_id else ()
        with self._lock:
            total = self._db.execute("SELECT count(*) FROM jobs" + where, values).fetchone()[0]
            rows = self._db.execute(
                "SELECT * FROM jobs" + where + " ORDER BY created_at DESC,ordinal LIMIT ? OFFSET ?",
                (*values, page_size, (page - 1) * page_size),
            ).fetchall()
            grouped = self._db.execute(
                "SELECT status,count(*) FROM jobs" + count_where + " GROUP BY status", count_values
            ).fetchall()
            active_where = " WHERE batch_id=? AND " if batch_id else " WHERE "
            active_count = self._db.execute(
                "SELECT count(*) FROM jobs" + active_where +
                "(status IN ('queued','running') OR (status='pending_index' AND error_json IS NULL))",
                count_values,
            ).fetchone()[0]
        counts = {name: 0 for name in ALL_STATUSES}
        counts.update({row[0]: row[1] for row in grouped})
        return {"items": [self._job(row) for row in rows], "total": total, "page": page,
                "page_size": page_size, "counts": counts, "active_count": active_count}

    def active_count(self) -> int:
        with self._lock:
            return int(self._db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running') OR (status='pending_index' AND error_json IS NULL)"
            ).fetchone()[0])

    def queued_ids(self) -> list[str]:
        with self._lock:
            return [row[0] for row in self._db.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,ordinal"
            )]

    def median_duration(self, effective_mode: str, asr_backend: str | None) -> float | None:
        with self._lock:
            rows = self._db.execute(
                """SELECT started_at,completed_at FROM jobs WHERE status='succeeded'
                AND effective_mode=? AND asr_backend IS ? AND started_at IS NOT NULL
                AND completed_at IS NOT NULL""", (effective_mode, asr_backend),
            ).fetchall()
        durations = sorted((datetime.fromisoformat(row[1]) - datetime.fromisoformat(row[0])).total_seconds()
                           for row in rows)
        durations = [value for value in durations if value > 0]
        if not durations:
            return None
        middle = len(durations) // 2
        return durations[middle] if len(durations) % 2 else (durations[middle - 1] + durations[middle]) / 2

    def active_ahead(self, job_id: str) -> int:
        with self._lock:
            ids = [row[0] for row in self._db.execute(
                "SELECT id FROM jobs WHERE status IN ('queued','running') ORDER BY created_at,batch_id,ordinal"
            )]
        return ids.index(job_id) if job_id in ids else 0

    def request_for(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT request_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return json.loads(row[0])

    def claim_queued(self, job_id: str) -> dict[str, Any] | None:
        timestamp = now()
        with self._lock, self._db:
            cursor = self._db.execute(
                """UPDATE jobs SET status='running',stage='acquiring',message='Acquiring content',
                progress=0,started_at=?,updated_at=?,error_json=NULL WHERE id=? AND status='queued'""",
                (timestamp, timestamp, job_id),
            )
            if cursor.rowcount != 1:
                return None
            self._touch_batch(job_id, timestamp)
            self._insert_event(job_id, "started", {"stage": "acquiring"}, timestamp)
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row)

    def cancel_queued(self, job_id: str) -> dict[str, Any] | None:
        timestamp = now()
        with self._lock, self._db:
            cursor = self._db.execute(
                """UPDATE jobs SET status='cancelled',stage='cancelled',message='Import cancelled',
                completed_at=?,updated_at=? WHERE id=? AND status='queued'""",
                (timestamp, timestamp, job_id),
            )
            if cursor.rowcount != 1:
                return None
            self._touch_batch(job_id, timestamp)
            self._insert_event(job_id, "cancelled", {}, timestamp)
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row)

    def interrupt_active(self, message: str = "Import interrupted by service shutdown") -> None:
        timestamp = now()
        with self._lock, self._db:
            rows = self._db.execute("SELECT id FROM jobs WHERE status IN ('queued','running')").fetchall()
            error = {"code": "interrupted", "message": message, "retryable": True,
                     "source": "import_service"}
            for row in rows:
                cursor = self._db.execute(
                    """UPDATE jobs SET status='interrupted',stage='interrupted',message=?,error_json=?,
                    completed_at=?,updated_at=? WHERE id=? AND status IN ('queued','running')""",
                    (message, json.dumps(error), timestamp, timestamp, row["id"]),
                )
                if cursor.rowcount:
                    self._insert_event(row["id"], "interrupted", error, timestamp)

    def update(self, job_id: str, *, event: str | None = None,
               payload: dict[str, Any] | None = None, **changes: Any) -> dict[str, Any]:
        timestamp = now()
        json_fields = {"error": "error_json", "quality": "quality_json",
                       "duplicate": "duplicate_json", "result": "result_json"}
        columns, values = [], []
        for key, value in changes.items():
            column = json_fields.get(key, key)
            if key in json_fields:
                value = json.dumps(self._durable_payload(value)) if value is not None else None
            elif isinstance(value, str):
                value = sanitize_text(value)
            columns.append(f"{column}=?"); values.append(value)
        columns.append("updated_at=?"); values.extend([timestamp, job_id])
        with self._lock, self._db:
            self._db.execute(f"UPDATE jobs SET {','.join(columns)} WHERE id=?", values)
            self._touch_batch(job_id, timestamp)
            if event:
                self._insert_event(job_id, event, payload or changes, timestamp)
            self._trim()
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def timeline(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT created_at,event,payload_json FROM job_events WHERE job_id=? ORDER BY id", (job_id,)
            ).fetchall()
        return [{"created_at": row[0], "event": row[1], "payload": json.loads(row[2])} for row in rows]

    def retry_job(self, source_id: str, *, reindex: bool = False) -> dict[str, Any] | None:
        allowed = ("pending_index",) if reindex else ("failed", "interrupted")
        with self._lock, self._db:
            source = self._db.execute("SELECT * FROM jobs WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise KeyError(source_id)
            locator = self._visible_locator(source)
            if source["status"] not in allowed or (reindex and (not locator or source["error_json"] is None)):
                return None
            timestamp = now()
            request = json.loads(source["request_json"])
            if reindex:
                request["reindex_only"] = True
            else:
                request.pop("reindex_only", None)
            placeholders = ",".join("?" for _ in allowed)
            cursor = self._db.execute(
                """UPDATE jobs SET attempt=?,request_json=?,status='queued',stage='queued',
                message='Waiting to start',progress=0,updated_at=?,started_at=NULL,completed_at=NULL,
                error_json=NULL,warning=?,quality_json=?,duplicate_json=?,item_id=?,item_path=?,result_json=?
                ,library_key=? WHERE id=? AND status IN (""" + placeholders + ")" +
                (" AND error_json IS NOT NULL" if reindex else ""),
                (source["attempt"] + 1, json.dumps(request), timestamp,
                 source["warning"] if reindex else None, source["quality_json"] if reindex else None,
                 source["duplicate_json"] if reindex else None, source["item_id"] if reindex else None,
                 locator if reindex else None, source["result_json"] if reindex else None,
                 source["library_key"] if reindex else self.library_key, source_id, *allowed),
            )
            if cursor.rowcount != 1:
                return None
            self._touch_batch(source_id, timestamp)
            self._insert_event(source_id, "queued", {"attempt": source["attempt"] + 1,
                                                       "reindex_only": reindex}, timestamp)
        return self.get_job(source_id)

    def retry_failed_batch(self, batch_id: str) -> tuple[list[dict[str, Any]], bool]:
        timestamp = now()
        with self._lock, self._db:
            if self._db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone() is None:
                return [], False
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE batch_id=? AND status IN ('failed','interrupted') ORDER BY ordinal",
                (batch_id,),
            ).fetchall()
            queued = []
            for source in rows:
                request = json.loads(source["request_json"]); request.pop("reindex_only", None)
                cursor = self._db.execute(
                    """UPDATE jobs SET attempt=?,request_json=?,status='queued',stage='queued',
                    message='Waiting to start',progress=0,updated_at=?,started_at=NULL,completed_at=NULL,
                    error_json=NULL,warning=NULL,quality_json=NULL,duplicate_json=NULL,item_id=NULL,
                    item_path=NULL,result_json=NULL,library_key=? WHERE id=? AND status IN ('failed','interrupted')""",
                    (source["attempt"] + 1, json.dumps(request), timestamp, self.library_key, source["id"]),
                )
                if cursor.rowcount:
                    self._insert_event(source["id"], "queued", {"attempt": source["attempt"] + 1,
                                                                  "reindex_only": False}, timestamp)
                    queued.append(self._job(self._db.execute(
                        "SELECT * FROM jobs WHERE id=?", (source["id"],)
                    ).fetchone()))
            if queued:
                self._db.execute("UPDATE batches SET updated_at=? WHERE id=?", (timestamp, batch_id))
        return queued, True

    def completed_for_duplicates(self, exclude_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                """SELECT * FROM jobs WHERE status='succeeded' AND id<>? AND item_path IS NOT NULL
                AND library_key=? ORDER BY completed_at DESC""", (exclude_id, self.library_key),
            ).fetchall()
        return [self._job(row) for row in rows]

    def _touch_batch(self, job_id: str, timestamp: str) -> None:
        self._db.execute(
            "UPDATE batches SET updated_at=? WHERE id=(SELECT batch_id FROM jobs WHERE id=?)",
            (timestamp, job_id),
        )

    def _insert_event(self, job_id: str, event: str, payload: dict[str, Any], timestamp: str) -> None:
        self._db.execute(
            "INSERT INTO job_events(job_id,created_at,event,payload_json) VALUES (?,?,?,?)",
            (job_id, timestamp, sanitize_text(event),
             json.dumps(self._durable_payload(payload), default=str)),
        )

    def _trim(self) -> None:
        self._db.execute(
            """DELETE FROM jobs WHERE id IN (
            SELECT id FROM jobs WHERE status IN ('succeeded','failed','cancelled','interrupted')
            OR (status='pending_index' AND error_json IS NOT NULL)
            ORDER BY COALESCE(completed_at,updated_at) DESC,updated_at DESC LIMIT -1 OFFSET ?)""",
            (self.terminal_limit,),
        )
        self._db.execute("DELETE FROM batches WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.batch_id=batches.id)")

    @staticmethod
    def _loads(value: str | None) -> Any:
        return json.loads(value) if value else None

    def _visible_locator(self, row: sqlite3.Row) -> str | None:
        if row["library_key"] != self.library_key:
            return None
        value = row["item_path"]
        if not value:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            return None
        return path.as_posix()

    def _job(self, row: sqlite3.Row) -> dict[str, Any]:
        status = row["status"]
        error = self._loads(row["error_json"])
        locator = self._visible_locator(row)
        is_active = status in {"queued", "running"} or (status == "pending_index" and error is None)
        return {
            "id": row["id"], "batch_id": row["batch_id"], "position": row["ordinal"],
            "url": row["url"], "requested_mode": row["requested_mode"],
            "effective_mode": row["effective_mode"], "asr_backend": row["asr_backend"],
            "status": status, "stage": row["stage"], "message": row["message"],
            "progress": row["progress"], "queue_position": None, "eta_seconds": None,
            "attempt": row["attempt"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "started_at": row["started_at"], "completed_at": row["completed_at"],
            "item_id": row["item_id"], "item_path": locator,
            "error": error, "warning": row["warning"], "is_active": is_active,
            "quality": self._loads(row["quality_json"]), "duplicate": self._loads(row["duplicate_json"]),
            "can_cancel": status == "queued", "can_retry": status in {"failed", "interrupted"},
            "can_reindex": status == "pending_index" and error is not None and bool(locator),
        }
