from __future__ import annotations

import json
import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from echo_reader.app_config import AppPaths
import echo_reader.app_config as app_config_module
from echo_reader.echo_api import EchoApiClient
from echo_reader.import_store import ImportStore, SCHEMA_VERSION, SchemaVersionError
from echo_reader.server import create_app
from echo_reader.server import ImportService


LEGACY_SCHEMA = """
CREATE TABLE batches (
 id TEXT PRIMARY KEY, options_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), ordinal INTEGER NOT NULL,
 attempt INTEGER NOT NULL, url TEXT NOT NULL, request_json TEXT NOT NULL,
 requested_mode TEXT NOT NULL, effective_mode TEXT NOT NULL, asr_backend TEXT,
 status TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL, progress REAL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
 item_id TEXT, out_dir TEXT, result_json TEXT, error_json TEXT, warning TEXT,
 quality_json TEXT, duplicate_json TEXT
);
CREATE INDEX jobs_batch_idx ON jobs(batch_id, ordinal, attempt);
CREATE INDEX jobs_status_idx ON jobs(status, created_at DESC);
CREATE TABLE job_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL, event TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX events_job_idx ON job_events(job_id, id);
"""


def test_installed_app_paths_separate_state_cache_logs_tmp_and_config(tmp_path: Path) -> None:
    env = {"LOCALAPPDATA": str(tmp_path / "local"), "APPDATA": str(tmp_path / "roaming")}
    paths = AppPaths.resolve(install_mode=True, environ=env)
    assert paths.state_root == (tmp_path / "local" / "EchoReader" / "state").resolve()
    assert paths.cache_root == (tmp_path / "local" / "EchoReader" / "cache").resolve()
    assert paths.logs_root.name == "logs" and paths.tmp_root.name == "tmp"
    assert paths.config_root == (tmp_path / "local" / "EchoReader" / "config").resolve()
    assert paths.library_root.name == "Echo Reader Library"


def test_import_store_migrates_legacy_locator_scrubs_and_is_idempotent(tmp_path: Path) -> None:
    library = tmp_path / "library"; item = library / "item"; item.mkdir(parents=True)
    database = tmp_path / "imports.db"
    timestamp = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute("INSERT INTO batches VALUES (?,?,?,?)", (
            "batch", '{"callback":"https://a.test/?TOKEN=LEAK_BATCH_123&keep=1"}', timestamp, timestamp,
        ))
        connection.execute(
            """INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("job", "batch", 1, 1, "https://a.test/watch?xsec_token=url&id=1",
             '{"url":"https://a.test/watch?signature=LEAK_REQUEST_123&id=1"}', "auto", "download",
             "online", "succeeded", "complete", "done https://a.test/?auth=LEAK_MESSAGE_123&ok=1", 100,
             timestamp, timestamp, timestamp, timestamp, "item", str(item),
             '{"next":"https://a.test/?utm_source=LEAK_RESULT_123&ok=1",'
             f'"out_dir":{json.dumps(str(item))}}}', None, None,
             '{"source":"https://a.test/?key=LEAK_QUALITY_123&q=1"}',
             '{"ref":"https://a.test/?credential=LEAK_DUPLICATE_123&r=1"}'),
        )
        connection.execute(
            "INSERT INTO job_events(job_id,created_at,event,payload_json) VALUES (?,?,?,?)",
            ("job", timestamp, "done", json.dumps({
                "error": "https://a.test/?authorization=LEAK_EVENT_123&e=1",
                "out_dir": str(item),
            })),
        )
    store = ImportStore(database, library)
    job = store.get_job("job")
    assert job is not None and job["item_path"] == "item" and "out_dir" not in job
    timeline = store.timeline("job")
    assert "out_dir" not in str(timeline) and timeline[0]["payload"]["item_path"] == "item"
    store.close()
    backups = list(tmp_path.glob("imports.db.backup-v1-*"))
    assert len(backups) == 1
    raw = database.read_bytes().lower()
    for secret in (b"leak_batch_123", b"leak_request_123", b"leak_message_123",
                   b"leak_result_123", b"leak_quality_123", b"leak_duplicate_123",
                   b"leak_event_123"):
        assert secret not in raw
    reopened = ImportStore(database, library)
    reopened.close()
    assert len(list(tmp_path.glob("imports.db.backup-v1-*"))) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "out_dir" not in {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}


def test_import_store_rejects_newer_schema(tmp_path: Path) -> None:
    database = tmp_path / "imports.db"
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaVersionError, match="newer"):
        ImportStore(database, tmp_path)


def test_pending_index_failures_are_retained_with_terminal_limit(tmp_path: Path) -> None:
    store = ImportStore(tmp_path / "imports.db", tmp_path, terminal_limit=2)
    options = {"mode": "auto", "asr_backend": "auto"}
    plan = {"mode": "download", "asr_backend": "online"}
    for index in range(3):
        job = store.create_batch([f"https://x.test/{index}"], options, [plan])["items"][0]
        store.update(job["id"], status="pending_index", error={"code": "index_failed"})
    assert store.list_jobs(page=1, page_size=10)["total"] == 2
    store.close()


def test_normal_service_close_persists_interrupted_state(tmp_path: Path) -> None:
    paths = AppPaths.resolve(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime")
    service = ImportService(paths)
    job = service.store.create_batch(
        ["https://x.test/job"], {"mode": "auto", "asr_backend": "auto"},
        [{"mode": "download", "asr_backend": "online"}],
    )["items"][0]
    service.store.claim_queued(job["id"])
    asyncio.run(service.close())
    reopened = ImportStore(paths.state_root / "imports.db", paths.library_root)
    assert reopened.get_job(job["id"])["status"] == "interrupted"
    reopened.close()


class SwitchCoordinator:
    def __init__(self, paths: AppPaths, calls: list[tuple[str, str]]):
        self.paths = paths
        self.calls = calls

    async def initialize(self): pass
    async def close(self): pass
    async def index_health(self): pass
    def capabilities(self): return {"transcription": {}}
    async def switch_library(self, path: Path):
        self.calls.append((str(self.paths.library_root), str(path)))
        return {"library_root": str(path), "item_count": 0, "invalid_item_count": 0}


def test_library_switch_persists_and_restart_uses_new_root(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    runtime, config = tmp_path / "runtime", tmp_path / "config"
    calls: list[tuple[str, str]] = []
    factory = lambda paths: SwitchCoordinator(paths, calls)
    app = create_app(library_root=first, runtime_root=runtime, config_root=config,
                     coordinator_factory=factory, bridge=None)
    with TestClient(app) as client:
        response = client.post("/api/v1/library/switch", json={"path": str(second)})
        assert response.status_code == 200
        assert client.get("/api/v1/app-config").json()["library_root"] == str(second.resolve())
    restarted = create_app(runtime_root=runtime, config_root=config,
                           coordinator_factory=factory, bridge=None)
    with TestClient(restarted) as client:
        assert client.get("/api/v1/health").json()["content_root"] == str(second.resolve())


@pytest.mark.parametrize("kind", ["missing", "filesystem_root", "state", "state_parent"])
def test_restart_rejects_unsafe_persisted_library_root(tmp_path: Path, kind: str) -> None:
    config = tmp_path / "config"
    config.mkdir()
    state = tmp_path / "runtime" / "state"
    candidates = {
        "missing": tmp_path / "missing",
        "filesystem_root": Path(tmp_path.anchor),
        "state": state,
        "state_parent": state.parent,
    }
    (config / "app.json").write_text(json.dumps({
        "version": 1, "library_root": str(candidates[kind].resolve()),
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Library path"):
        AppPaths.resolve(state_root=state, cache_root=tmp_path / "runtime/cache",
                         logs_root=tmp_path / "runtime/logs", tmp_root=tmp_path / "runtime/tmp",
                         config_root=config)


def test_restart_validation_does_not_probe_persisted_library_writability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, config = tmp_path / "library", tmp_path / "config"
    library.mkdir(); config.mkdir()
    (config / "app.json").write_text(json.dumps({
        "version": 1, "library_root": str(library.resolve()),
    }), encoding="utf-8")
    monkeypatch.setattr(
        app_config_module, "_probe_library_writable",
        lambda path: (_ for _ in ()).throw(AssertionError("startup must not probe")),
    )
    assert AppPaths.resolve(runtime_root=tmp_path / "runtime", config_root=config).library_root == library


def test_library_switch_rejects_control_root_overlap_and_filesystem_root(tmp_path: Path) -> None:
    current = tmp_path / "library"
    current.mkdir()
    runtime, config = tmp_path / "runtime", tmp_path / "config"
    calls: list[tuple[str, str]] = []
    app = create_app(library_root=current, runtime_root=runtime, config_root=config,
                     coordinator_factory=lambda paths: SwitchCoordinator(paths, calls), bridge=None)
    with TestClient(app) as client:
        nested = runtime / "nested-library"
        nested.mkdir()
        candidates = (runtime, nested, tmp_path, Path(tmp_path.anchor))
        for candidate in candidates:
            response = client.post("/api/v1/library/switch", json={"path": str(candidate)})
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "invalid_library"
    assert calls == []


def test_library_switch_validates_writable_without_probe_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, candidate = tmp_path / "library", tmp_path / "empty-library"
    current.mkdir(); candidate.mkdir()
    calls: list[tuple[str, str]] = []
    app = create_app(library_root=current, runtime_root=tmp_path / "runtime",
                     config_root=tmp_path / "config",
                     coordinator_factory=lambda paths: SwitchCoordinator(paths, calls), bridge=None)
    with TestClient(app) as client:
        response = client.post("/api/v1/library/switch", json={"path": str(candidate)})
        assert response.status_code == 200
    assert not list(candidate.glob(".echo-reader-write-test-*"))

    blocked = tmp_path / "blocked-library"
    blocked.mkdir()
    monkeypatch.setattr(
        app_config_module, "_probe_library_writable",
        lambda path: (_ for _ in ()).throw(PermissionError("read only")),
    )
    app = create_app(library_root=candidate, runtime_root=tmp_path / "runtime-2",
                     config_root=tmp_path / "config-2",
                     coordinator_factory=lambda paths: SwitchCoordinator(paths, calls), bridge=None)
    with TestClient(app) as client:
        response = client.post("/api/v1/library/switch", json={"path": str(blocked)})
        assert response.status_code == 422
        assert response.json()["detail"] == {
            "code": "invalid_library", "message": "Library path must be writable",
        }


def test_library_switch_rolls_echo_and_config_back_on_persist_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    calls: list[tuple[str, str]] = []
    app = create_app(library_root=first, runtime_root=tmp_path / "runtime",
                     config_root=tmp_path / "config",
                     coordinator_factory=lambda paths: SwitchCoordinator(paths, calls), bridge=None)
    monkeypatch.setattr(AppPaths, "persist", lambda self: (_ for _ in ()).throw(OSError("disk full")))
    with TestClient(app) as client:
        response = client.post("/api/v1/library/switch", json={"path": str(second)})
        assert response.status_code == 502
        assert client.get("/api/v1/health").json()["content_root"] == str(first.resolve())
    assert calls[-2:] == [(str(second.resolve()), str(second.resolve())),
                          (str(first.resolve()), str(first.resolve()))]


def test_echo_internal_switch_sends_required_token() -> None:
    seen: list[httpx.Request] = []
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"library_root": "C:/library", "item_count": 2,
                                         "invalid_item_count": 1})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await EchoApiClient("http://echo.test", client=client,
                                       internal_token="secret-token").switch_library("C:/library")
    result = asyncio.run(run())
    assert result["item_count"] == 2
    assert seen[0].url.path == "/internal/v1/library/switch"
    assert seen[0].headers["X-Echo-Internal-Token"] == "secret-token"
