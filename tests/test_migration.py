from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path

import pytest

from echo_reader.app_config import AppPaths
from echo_reader.import_store import ImportStore, SCHEMA_VERSION, SchemaVersionError
import echo_reader.migration as migration_module
import echo_reader.app_config as app_config_module
from echo_reader.migration import (
    ActiveDatabaseError,
    MigrationConflict,
    MigrationError,
    migrate_from,
    recover_interrupted_migration,
)


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES (?)", (value,))


def _imports_v2(path: Path, content: Path, *, item_path: str = "item") -> str:
    store = ImportStore(path, content)
    job = store.create_batch(
        ["https://x.test/item"], {"mode": "auto", "asr_backend": "auto"},
        [{"mode": "download", "asr_backend": "online"}],
    )["items"][0]
    store.update(job["id"], status="pending_index", item_path=item_path,
                 error={"code": "index_failed", "retryable": True})
    store.close()
    return job["id"]


def _medialab_database(path: Path, content: Path, runtime: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, out_dir TEXT, error_code TEXT,
          error_detail TEXT, error_source TEXT, error_retryable INTEGER);
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE asr (id INTEGER PRIMARY KEY, raw_path TEXT, normalized_path TEXT, txt_path TEXT);
        """)
        connection.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                           ("task", str(content / "item"), None, None, None, None))
        connection.execute("INSERT INTO files VALUES (?,?)", (1, str(content / "item/media.mp3")))
        connection.execute("INSERT INTO asr VALUES (?,?,?,?)", (
            1, str(runtime / "tmp/task/raw.json"), str(content / "item/transcript.json"),
            str(content / "item/transcript.txt"),
        ))


def _empty_medialab_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE tasks (
          id TEXT PRIMARY KEY, url TEXT NOT NULL, platform TEXT, status TEXT NOT NULL,
          error_code TEXT, error_detail TEXT, error_source TEXT, error_retryable INTEGER,
          warnings_json TEXT, retries INTEGER NOT NULL DEFAULT 0, params_json TEXT,
          out_dir TEXT, created_at TEXT NOT NULL, started_at TEXT, done_at TEXT);
        CREATE TABLE files (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, kind TEXT NOT NULL,
          path TEXT, size INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE asr (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, status TEXT NOT NULL,
          segments_json TEXT, raw_path TEXT, normalized_path TEXT, txt_path TEXT,
          created_at TEXT NOT NULL);
        PRAGMA user_version=2;
        """)


def _paths(root: Path) -> AppPaths:
    return AppPaths(
        library_root=root / "library",
        state_root=root / "state",
        cache_root=root / "cache",
        logs_root=root / "logs",
        tmp_root=root / "tmp",
        config_root=root / "config",
        install_mode=True,
    )


def test_selective_non_destructive_migration_and_idempotence(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    (source / "content/item").mkdir(parents=True)
    (source / "content/item/manifest.json").write_text('{"id":"item"}', encoding="utf-8")
    (source / "config").mkdir()
    (source / "config/config.toml").write_text("[audio]\nformat='m4a'\n", encoding="utf-8")
    (source / "config/secrets.toml").write_text("[asr]\napi_key='fake-test-value'\n", encoding="utf-8")
    job_id = _imports_v2(source / ".runtime/imports.db", source / "content")
    _medialab_database(source / ".runtime/medialab.db", source / "content", source / ".runtime")
    model = source / ".runtime/models/local-asr/model.bin"
    model.parent.mkdir(parents=True); model.write_bytes(b"fake-model")

    # Explicitly excluded source residue.
    for excluded in (
        source / ".runtime/test-runs/run/report.json",
        source / ".runtime/tmp/cookies.txt",
        source / ".runtime/imports.log",
        source / ".runtime/openvino-cache/blob.bin",
        source / "components/echo/backend/target/release/echo.exe",
    ):
        excluded.parent.mkdir(parents=True, exist_ok=True)
        excluded.write_text("must-not-migrate", encoding="utf-8")

    paths = _paths(destination)
    result = migrate_from(source, paths)
    assert result.status == "migrated"
    assert set(result.migrated) == {
        "content", "config/config.toml", "config/secrets.toml",
        "imports.db", "medialab.db", "models/local-asr",
    }
    assert (paths.library_root / "item/manifest.json").is_file()
    assert (paths.config_root / "secrets.toml").is_file()
    assert (paths.cache_root / "models/local-asr/model.bin").read_bytes() == b"fake-model"
    migrated_store = ImportStore(paths.state_root / "imports.db", paths.library_root)
    migrated_job = migrated_store.get_job(job_id)
    assert migrated_job["item_path"] == "item" and migrated_job["can_reindex"] is True
    migrated_store.close()
    with sqlite3.connect(paths.state_root / "medialab.db") as connection:
        assert connection.execute("SELECT out_dir FROM tasks").fetchone()[0] == str(paths.library_root / "item")
    assert not paths.logs_root.exists()
    assert not paths.tmp_root.exists()
    assert not (paths.cache_root / "openvino-cache").exists()
    assert not (destination / "target").exists()
    assert (source / ".runtime/tmp/cookies.txt").is_file()
    assert (source / "content/item/manifest.json").is_file()

    repeated = migrate_from(source, paths)
    assert repeated.status == "already_completed"
    assert repeated.fingerprint == result.fingerprint


def test_existing_target_import_backup_is_preserved(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    (source / "content/item").mkdir(parents=True)
    (source / "content/item/manifest.json").write_text('{"id":"item"}', encoding="utf-8")
    _imports_v2(source / ".runtime/imports.db", source / "content")

    paths = _paths(destination)
    paths.state_root.mkdir(parents=True)
    existing_backup = paths.state_root / "imports.db.backup-v1-preserve"
    existing_backup.write_bytes(b"user-owned-backup")

    migrate_from(source, paths)

    assert existing_backup.read_bytes() == b"user-owned-backup"


def test_first_launch_empty_databases_are_replaced_by_migration(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "content/item").mkdir(parents=True)
    (source / "content/item/manifest.json").write_text('{"id":"item"}', encoding="utf-8")
    job_id = _imports_v2(source / ".runtime/imports.db", source / "content")
    _medialab_database(source / ".runtime/medialab.db", source / "content", source / ".runtime")
    paths = _paths(tmp_path / "destination")

    # The host's normal first launch initializes both databases before the
    # operator can invoke --migrate-from.
    empty_imports = ImportStore(paths.state_root / "imports.db", paths.library_root)
    empty_imports.close()
    _empty_medialab_database(paths.state_root / "medialab.db")

    migrate_from(source, paths)

    migrated = ImportStore(paths.state_root / "imports.db", paths.library_root)
    assert migrated.get_job(job_id)["item_path"] == "item"
    migrated.close()
    with sqlite3.connect(paths.state_root / "medialab.db") as connection:
        assert connection.execute("SELECT id FROM tasks").fetchone()[0] == "task"


@pytest.mark.parametrize("database_name", ["imports.db", "medialab.db"])
def test_nonempty_initialized_database_remains_a_hard_conflict(
    tmp_path: Path, database_name: str,
) -> None:
    source = tmp_path / "source"
    (source / "content/item").mkdir(parents=True)
    (source / "content/item/manifest.json").write_text('{"id":"item"}', encoding="utf-8")
    paths = _paths(tmp_path / "destination")
    if database_name == "imports.db":
        _imports_v2(source / ".runtime/imports.db", source / "content")
        target = ImportStore(paths.state_root / "imports.db", paths.library_root)
        with target._db:
            target._db.execute(
                "INSERT INTO batches(id,options_json,created_at,updated_at) VALUES ('kept','{}','t','t')"
            )
        target.close()
    else:
        _medialab_database(source / ".runtime/medialab.db", source / "content", source / ".runtime")
        _empty_medialab_database(paths.state_root / "medialab.db")
        with sqlite3.connect(paths.state_root / "medialab.db") as connection:
            connection.execute(
                "INSERT INTO tasks(id,url,status,created_at) VALUES ('kept','https://example.test','done','t')"
            )

    with pytest.raises(MigrationConflict, match="already contains state"):
        migrate_from(source, paths)
    with sqlite3.connect(paths.state_root / database_name) as connection:
        table = "batches" if database_name == "imports.db" else "tasks"
        assert connection.execute(f"SELECT id FROM {table}").fetchone()[0] == "kept"


def test_changed_source_after_completed_migration_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "content").mkdir(parents=True)
    (source / "content/item.txt").write_text("first", encoding="utf-8")
    paths = _paths(tmp_path / "destination")
    migrate_from(source, paths)
    (source / "content/item.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(MigrationConflict, match="source changed"):
        migrate_from(source, paths)
    assert (paths.library_root / "item.txt").read_text(encoding="utf-8") == "first"


def test_empty_source_is_rejected_without_creating_targets(tmp_path: Path) -> None:
    source = tmp_path / "source"; source.mkdir()
    paths = _paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="No migratable assets"):
        migrate_from(source, paths)
    assert not paths.state_root.exists() and not paths.library_root.exists()


def test_config_only_migration_does_not_stage_unrelated_library_or_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    (source / "config").mkdir(parents=True)
    (source / "config/config.toml").write_text("[app]", encoding="utf-8")
    paths = _paths(tmp_path / "destination")
    paths.library_root.mkdir(parents=True)
    paths.cache_root.mkdir(parents=True)
    (paths.library_root / "large-media.bin").write_bytes(b"library")
    (paths.cache_root / "large-model.bin").write_bytes(b"model")
    copied_roots: list[Path] = []
    original_copytree = migration_module.shutil.copytree

    def recording_copytree(source_path: Path, target_path: Path):
        copied_roots.append(Path(source_path).resolve())
        return original_copytree(source_path, target_path)

    monkeypatch.setattr(migration_module.shutil, "copytree", recording_copytree)

    result = migrate_from(source, paths)

    assert result.migrated == ("config/config.toml",)
    assert (paths.config_root / "config.toml").read_text(encoding="utf-8") == "[app]"
    assert (paths.library_root / "large-media.bin").read_bytes() == b"library"
    assert (paths.cache_root / "large-model.bin").read_bytes() == b"model"
    assert paths.library_root not in copied_roots
    assert paths.cache_root not in copied_roots
    assert not list(paths.library_root.parent.glob(f".{paths.library_root.name}.migrate-*"))
    assert not list(paths.cache_root.parent.glob(f".{paths.cache_root.name}.migrate-*"))


def test_source_destination_overlap_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "content").mkdir(parents=True)
    (source / "content/item.txt").write_text("item", encoding="utf-8")
    paths = _paths(source / "installed")
    with pytest.raises(MigrationError, match="overlap"):
        migrate_from(source, paths)
    assert not paths.state_root.exists()


@pytest.mark.parametrize("sidecar", ["-wal", "-shm", "-journal"])
def test_database_sidecar_is_rejected_before_target_commit(tmp_path: Path, sidecar: str) -> None:
    source = tmp_path / "source"
    database = source / ".runtime/imports.db"
    _database(database, "source")
    database.with_name(database.name + sidecar).write_bytes(b"active")
    paths = _paths(tmp_path / "destination")
    with pytest.raises(ActiveDatabaseError, match="active sidecar"):
        migrate_from(source, paths)
    assert not paths.state_root.exists()
    assert database.is_file()


def test_corrupt_database_is_rejected_without_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    database = source / ".runtime/imports.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not sqlite")
    paths = _paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="Invalid SQLite"):
        migrate_from(source, paths)
    assert not (paths.state_root / "imports.db").exists()
    assert database.read_bytes() == b"not sqlite"


@pytest.mark.parametrize("database_name,supported", [
    ("imports.db", SCHEMA_VERSION), ("medialab.db", 2),
])
def test_future_source_database_is_rejected_before_target_mutation(
    tmp_path: Path, database_name: str, supported: int,
) -> None:
    source = tmp_path / "source"
    (source / "content/item").mkdir(parents=True)
    (source / "content/item/manifest.json").write_text('{"id":"item"}', encoding="utf-8")
    source_database = source / ".runtime" / database_name
    if database_name == "imports.db":
        _imports_v2(source_database, source / "content")
    else:
        _medialab_database(source_database, source / "content", source / ".runtime")
    with sqlite3.connect(source_database) as connection:
        connection.execute(f"PRAGMA user_version={supported + 1}")
        connection.commit()
        connection.execute("PRAGMA journal_mode=DELETE")

    paths = _paths(tmp_path / "destination")
    target_database = paths.state_root / database_name
    if database_name == "imports.db":
        initialized = ImportStore(target_database, paths.library_root)
        initialized.close()
    else:
        _empty_medialab_database(target_database)

    with pytest.raises(MigrationConflict, match="newer than supported"):
        migrate_from(source, paths)
    with sqlite3.connect(target_database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == supported
        table = "batches" if database_name == "imports.db" else "tasks"
        assert connection.execute(f"SELECT 1 FROM {table}").fetchone() is None


def test_import_store_schema_error_is_wrapped_at_migration_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    (source / "content/item").mkdir(parents=True)
    (source / "content/item/manifest.json").write_text('{"id":"item"}', encoding="utf-8")
    _imports_v2(source / ".runtime/imports.db", source / "content")

    def reject_schema(*args, **kwargs):
        raise SchemaVersionError("unsupported test schema")

    monkeypatch.setattr(migration_module, "ImportStore", reject_schema)
    with pytest.raises(MigrationError, match="Unsupported imports.db schema"):
        migrate_from(source, _paths(tmp_path / "destination"))


def test_destination_conflict_leaves_existing_target_and_source_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "content").mkdir(parents=True)
    (source / "content/item.txt").write_text("source", encoding="utf-8")
    paths = _paths(tmp_path / "destination")
    paths.library_root.mkdir(parents=True)
    (paths.library_root / "item.txt").write_text("target", encoding="utf-8")
    with pytest.raises(MigrationConflict, match="conflicts"):
        migrate_from(source, paths)
    assert (paths.library_root / "item.txt").read_text(encoding="utf-8") == "target"
    assert (source / "content/item.txt").read_text(encoding="utf-8") == "source"
    assert not (paths.state_root / "migration.json").exists()


def test_source_link_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    source, outside = tmp_path / "source", tmp_path / "outside.txt"
    (source / "content").mkdir(parents=True)
    outside.write_text("outside", encoding="utf-8")
    link = source / "content/escape.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    paths = _paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="Links are not allowed"):
        migrate_from(source, paths)
    assert not paths.library_root.exists()


def _legacy_imports(path: Path, out_dir: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    job_id = "legacy-job"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE batches (id TEXT PRIMARY KEY, options_json TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE jobs (id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
          ordinal INTEGER NOT NULL, attempt INTEGER NOT NULL, url TEXT NOT NULL,
          request_json TEXT NOT NULL, requested_mode TEXT NOT NULL, effective_mode TEXT NOT NULL,
          asr_backend TEXT, status TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL,
          progress REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
          completed_at TEXT, item_id TEXT, out_dir TEXT, result_json TEXT, error_json TEXT,
          warning TEXT, quality_json TEXT, duplicate_json TEXT);
        CREATE TABLE job_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, created_at TEXT NOT NULL,
          event TEXT NOT NULL, payload_json TEXT NOT NULL);
        PRAGMA user_version=1;
        """)
        timestamp = "2026-01-01T00:00:00+00:00"
        connection.execute("INSERT INTO batches VALUES (?,?,?,?)",
                           ("batch", "{}", timestamp, timestamp))
        values = (job_id, "batch", 1, 1, "https://x.test", '{"url":"https://x.test"}',
                  "auto", "download", "online", "pending_index", "pending_index", "ready", 100,
                  timestamp, timestamp, timestamp, None, "item", str(out_dir),
                  json.dumps({"out_dir": str(out_dir)}),
                  json.dumps({"code": "index_failed", "retryable": True}),
                  None, None, None)
        connection.execute(
            f"INSERT INTO jobs VALUES ({','.join('?' for _ in values)})", values
        )
        connection.execute(
            "INSERT INTO job_events(job_id,created_at,event,payload_json) VALUES (?,?,?,?)",
            (job_id, timestamp, "ready", json.dumps({"out_dir": str(out_dir)})),
        )
    return job_id


def test_legacy_v1_import_locator_is_rebased_through_current_store_schema(tmp_path: Path) -> None:
    source = tmp_path / "source"
    item = source / "content/item"; item.mkdir(parents=True)
    (item / "manifest.json").write_text('{"id":"item"}', encoding="utf-8")
    job_id = _legacy_imports(source / ".runtime/imports.db", item)
    paths = _paths(tmp_path / "destination")
    migrate_from(source, paths)
    store = ImportStore(paths.state_root / "imports.db", paths.library_root)
    job = store.get_job(job_id)
    assert job["item_path"] == "item" and job["can_reindex"] is True
    timeline = store.timeline(job_id)
    assert timeline[0]["payload"]["item_path"] == "item"
    store.close()


def test_import_locator_outside_source_is_detached_and_not_reindexable(tmp_path: Path) -> None:
    source = tmp_path / "source"; (source / "content").mkdir(parents=True)
    outside = tmp_path / "outside/item"; outside.mkdir(parents=True)
    job_id = _legacy_imports(source / ".runtime/imports.db", outside)
    paths = _paths(tmp_path / "destination")
    migrate_from(source, paths)
    store = ImportStore(paths.state_root / "imports.db", paths.library_root)
    job = store.get_job(job_id)
    assert job["item_path"] is None and job["can_reindex"] is False
    assert job["error"]["code"] == "migration_path_outside_library"
    store.close()
    assert outside.is_dir()


def test_medialab_paths_rebase_runtime_and_clear_external_diagnostics(tmp_path: Path) -> None:
    source = tmp_path / "source"
    item = source / "content/item"; item.mkdir(parents=True)
    runtime = source / ".runtime"
    database = runtime / "medialab.db"
    _medialab_database(database, source / "content", runtime)
    external = tmp_path / "external/file.txt"
    external.parent.mkdir(); external.write_text("external", encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)",
                           ("outside", str(external.parent), None, None, None, None))
        connection.execute("INSERT INTO files VALUES (?,?)", (2, str(external)))
    paths = _paths(tmp_path / "destination")
    migrate_from(source, paths)
    with sqlite3.connect(paths.state_root / "medialab.db") as connection:
        connection.row_factory = sqlite3.Row
        task = connection.execute("SELECT * FROM tasks WHERE id='task'").fetchone()
        assert task["out_dir"] == str(paths.library_root / "item")
        outside_task = connection.execute("SELECT * FROM tasks WHERE id='outside'").fetchone()
        assert outside_task["out_dir"] is None
        assert outside_task["error_source"] == "migration"
        assert "out-of-root" in outside_task["error_detail"]
        assert connection.execute("SELECT path FROM files WHERE id=2").fetchone()[0] is None
        asr = connection.execute("SELECT * FROM asr").fetchone()
        assert asr["raw_path"] == str(paths.tmp_root / "task/raw.json")
        assert asr["normalized_path"] == str(paths.library_root / "item/transcript.json")
    assert external.read_text(encoding="utf-8") == "external"


class SimulatedHardCrash(BaseException):
    pass


@pytest.mark.parametrize("crash_after", range(1, 5))
def test_durable_journal_recovers_every_root_rename_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after: int,
) -> None:
    source = tmp_path / "source"
    (source / "content").mkdir(parents=True)
    (source / "content/new.txt").write_text("new", encoding="utf-8")
    paths = _paths(tmp_path / "destination")
    for root in (paths.library_root, paths.config_root, paths.state_root, paths.cache_root):
        root.mkdir(parents=True)
        (root / "existing.txt").write_text(root.name, encoding="utf-8")
    original = migration_module._commit_rename
    calls = 0

    def crashing_rename(source_path: Path, target_path: Path) -> None:
        nonlocal calls
        original(source_path, target_path)
        calls += 1
        if calls == crash_after:
            raise SimulatedHardCrash()

    monkeypatch.setattr(migration_module, "_commit_rename", crashing_rename)
    with pytest.raises(SimulatedHardCrash):
        migrate_from(source, paths)
    monkeypatch.setattr(migration_module, "_commit_rename", original)

    assert recover_interrupted_migration(paths) is True
    assert recover_interrupted_migration(paths) is False
    assert not (paths.library_root / "new.txt").exists()
    for root in (paths.library_root, paths.config_root, paths.state_root, paths.cache_root):
        assert (root / "existing.txt").read_text(encoding="utf-8") == root.name

    result = migrate_from(source, paths)
    assert result.status == "migrated"
    assert (paths.library_root / "new.txt").read_text(encoding="utf-8") == "new"
    for root in (paths.library_root, paths.config_root, paths.state_root, paths.cache_root):
        assert (root / "existing.txt").read_text(encoding="utf-8") == root.name
    assert not (paths.state_root.parent / migration_module.JOURNAL_NAME).exists()


def test_recovery_uses_journal_config_for_temporarily_missing_custom_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    (source / "content").mkdir(parents=True)
    (source / "content/new.txt").write_text("new", encoding="utf-8")
    (source / "config").mkdir()
    (source / "config/config.toml").write_text("[app]", encoding="utf-8")
    custom_library = (tmp_path / "custom-library").resolve()
    original_paths = _paths(tmp_path / "destination").with_library(custom_library)
    original_paths.persist()
    for root in (original_paths.library_root, original_paths.state_root,
                 original_paths.cache_root):
        root.mkdir(parents=True, exist_ok=True)
        (root / "existing.txt").write_text(root.name, encoding="utf-8")
    (original_paths.config_root / "existing.txt").write_text("config", encoding="utf-8")
    original = migration_module._commit_rename
    calls = 0

    def crash_after_config_backup(source_path: Path, target_path: Path) -> None:
        nonlocal calls
        original(source_path, target_path)
        calls += 1
        if calls == 3:
            raise SimulatedHardCrash()

    monkeypatch.setattr(migration_module, "_commit_rename", crash_after_config_backup)
    with pytest.raises(SimulatedHardCrash):
        migrate_from(source, original_paths)
    monkeypatch.setattr(migration_module, "_commit_rename", original)
    assert not original_paths.config_root.exists()

    monkeypatch.setattr(app_config_module, "_documents_root", lambda: tmp_path)
    fresh_paths = AppPaths.resolve(
        state_root=original_paths.state_root,
        cache_root=original_paths.cache_root,
        logs_root=original_paths.logs_root,
        tmp_root=original_paths.tmp_root,
        config_root=original_paths.config_root,
        install_mode=True,
    )
    assert fresh_paths.library_root == (tmp_path / "Echo Reader Library").resolve()
    assert fresh_paths.library_root != custom_library

    assert recover_interrupted_migration(fresh_paths) is True
    resolved_again = AppPaths.resolve(
        state_root=original_paths.state_root,
        cache_root=original_paths.cache_root,
        logs_root=original_paths.logs_root,
        tmp_root=original_paths.tmp_root,
        config_root=original_paths.config_root,
        install_mode=True,
    )
    assert resolved_again.library_root == custom_library
    assert (custom_library / "existing.txt").read_text(encoding="utf-8") == custom_library.name
    assert not (custom_library / "new.txt").exists()


@pytest.mark.parametrize("tamper", ["root_order", "target_path"])
def test_recovery_rejects_tampered_journal_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str,
) -> None:
    source = tmp_path / "source"
    (source / "content").mkdir(parents=True)
    (source / "content/new.txt").write_text("new", encoding="utf-8")
    paths = _paths(tmp_path / "destination")
    for root in (paths.library_root, paths.config_root, paths.state_root, paths.cache_root):
        root.mkdir(parents=True)
        (root / "existing.txt").write_text(root.name, encoding="utf-8")
    original = migration_module._commit_rename

    def crash_after_first_rename(source_path: Path, target_path: Path) -> None:
        original(source_path, target_path)
        raise SimulatedHardCrash()

    monkeypatch.setattr(migration_module, "_commit_rename", crash_after_first_rename)
    with pytest.raises(SimulatedHardCrash):
        migrate_from(source, paths)
    monkeypatch.setattr(migration_module, "_commit_rename", original)

    journal_path = paths.state_root.parent / migration_module.JOURNAL_NAME
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if tamper == "root_order":
        journal["roots"][0], journal["roots"][1] = journal["roots"][1], journal["roots"][0]
    else:
        journal["roots"][0]["target"] = str(tmp_path / "outside")
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(MigrationConflict, match="journal"):
        recover_interrupted_migration(paths)
    assert journal_path.is_file()
    assert not (tmp_path / "outside").exists()
