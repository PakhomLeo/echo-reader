"""Explicit, selective, non-destructive migration from a source checkout."""

from __future__ import annotations

import hashlib
import gc
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .app_config import CONFIG_NAME, AppConfig, AppPaths
from .import_store import (
    SCHEMA_VERSION as IMPORT_SCHEMA_VERSION,
    ImportStore,
    SchemaVersionError,
)


MARKER_NAME = "migration.json"
JOURNAL_NAME = ".echo-reader-migration-journal.json"
CONFIG_FILES = (Path("config/config.toml"), Path("config/secrets.toml"), Path("secrets.toml"))
DATABASE_FILES = ("imports.db", "medialab.db")


class MigrationError(RuntimeError):
    """The requested migration cannot be completed safely."""


class MigrationConflict(MigrationError):
    """Source or destination state conflicts with a prior migration."""


class ActiveDatabaseError(MigrationError):
    """A source database has an active journal/WAL sidecar."""


@dataclass(frozen=True)
class MigrationResult:
    status: str
    source_root: str
    fingerprint: str
    migrated: tuple[str, ...]
    marker: str


@dataclass(frozen=True)
class _Artifact:
    label: str
    source: Path
    target_root: Path
    relative_target: Path
    kind: str


def recover_interrupted_migration(paths: AppPaths) -> bool:
    """Recover one durable migration transaction without creating app roots.

    Desktop startup must call this after acquiring its single-instance lock and
    before any path ``ensure`` operation. A malformed or unsafe journal fails
    closed with :class:`MigrationConflict` and is left in place for diagnosis.
    """
    journal_path = paths.state_root.parent / JOURNAL_NAME
    if not journal_path.exists() and not journal_path.is_symlink():
        return False
    roots = _recovery_roots(journal_path, paths)
    _recover_journal(journal_path, roots)
    return True


def migrate_from(source_root: str | Path, paths: AppPaths) -> MigrationResult:
    """Migrate selected durable assets without modifying or deleting the source.

    The source layout is the repository layout: ``content/``, selected config
    files, ``.runtime/{imports,medialab}.db``, and
    ``.runtime/models/local-asr``. Runtime logs, tmp/test-runs, OpenVINO cache,
    build output, and every unlisted path are intentionally ignored.
    """
    source = Path(source_root).expanduser().resolve()
    if not source.is_dir():
        raise MigrationError(f"Migration source is not a directory: {source}")
    journal_path = paths.state_root.parent / JOURNAL_NAME
    recover_interrupted_migration(paths)
    artifacts = _discover(source, paths)
    if not artifacts:
        raise MigrationError(f"No migratable assets found in: {source}")
    roots = _transaction_roots(paths, artifacts)
    _validate_root_separation(source, roots)
    for artifact in artifacts:
        _validate_tree(artifact.source, source)
        if artifact.kind == "database":
            _validate_source_database(artifact.source)
    inventory = _inventory(artifacts)
    fingerprint = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    marker_path = paths.state_root / MARKER_NAME
    prior = _read_marker(marker_path)
    if prior is not None:
        if prior.get("source_root") != str(source) or prior.get("fingerprint") != fingerprint:
            raise MigrationConflict("Migration source changed after a completed migration")
        _validate_completed_targets(artifacts)
        return MigrationResult(
            status="already_completed", source_root=str(source), fingerprint=fingerprint,
            migrated=tuple(prior.get("migrated", ())), marker=str(marker_path),
        )

    token = uuid.uuid4().hex
    stages: dict[Path, Path] = {}
    migrated: list[str] = []
    try:
        for root in roots:
            root.parent.mkdir(parents=True, exist_ok=True)
            stage = root.parent / f".{root.name}.migrate-{token}"
            if stage.exists():
                raise MigrationConflict(f"Migration staging path already exists: {stage}")
            if root.exists():
                _validate_tree(root, root)
                shutil.copytree(root, stage)
            else:
                stage.mkdir(parents=True)
            stages[root] = stage
        for artifact in artifacts:
            transaction_root = _owning_root(artifact.target_root, roots)
            base = stages[transaction_root] / artifact.target_root.relative_to(transaction_root)
            target = base / artifact.relative_target
            if artifact.kind == "database":
                _copy_database(artifact.source, target, artifact.label, source, paths)
            elif artifact.kind == "file":
                _merge_file(artifact.source, target)
            else:
                _merge_tree(artifact.source, target)
            migrated.append(artifact.label)
        marker_stage_root = _owning_root(paths.state_root, roots)
        staged_marker = (stages[marker_stage_root]
                         / paths.state_root.relative_to(marker_stage_root) / MARKER_NAME)
        staged_marker.parent.mkdir(parents=True, exist_ok=True)
        marker = {
            "version": 1, "source_root": str(source), "fingerprint": fingerprint,
            "migrated": migrated,
        }
        staged_marker.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        _validate_staged(artifacts, stages, roots)
        if _inventory(artifacts) != inventory:
            raise MigrationError("Migration source changed while it was being staged")
        journal = {
            "version": 1,
            "token": token,
            "phase": "committing",
            "roots": [{
                "target": str(root), "stage": str(stages[root]),
                "backup": str(root.parent / f".{root.name}.pre-migrate-{token}"),
                "had_target": root.exists(), "state": "prepared",
            } for root in roots],
        }
        _write_journal(journal_path, journal)
        for entry in journal["roots"]:
            target, stage, backup = (Path(entry[name]) for name in ("target", "stage", "backup"))
            if entry["had_target"]:
                _commit_rename(target, backup)
                entry["state"] = "backup_moved"
                _write_journal(journal_path, journal)
            _commit_rename(stage, target)
            entry["state"] = "committed"
            _write_journal(journal_path, journal)
        journal["phase"] = "committed"
        _write_journal(journal_path, journal)
        _recover_journal(journal_path, roots)
    except Exception:
        if journal_path.exists():
            _recover_journal(journal_path, roots)
        else:
            for stage in stages.values():
                if stage.exists():
                    _remove_exact_tree(stage)
        raise
    return MigrationResult(
        status="migrated", source_root=str(source), fingerprint=fingerprint,
        migrated=tuple(migrated), marker=str(marker_path),
    )


def _discover(source: Path, paths: AppPaths) -> list[_Artifact]:
    artifacts: list[_Artifact] = []
    content = source / "content"
    if content.is_dir():
        artifacts.append(_Artifact("content", content, paths.library_root, Path(), "tree"))
    selected_config: dict[str, Path] = {}
    for relative in CONFIG_FILES:
        candidate = source / relative
        if not candidate.is_file():
            continue
        name = candidate.name
        previous = selected_config.get(name)
        if previous is not None and _sha256(previous) != _sha256(candidate):
            raise MigrationConflict(f"Conflicting source config files named {name}")
        selected_config[name] = candidate
    for name, candidate in selected_config.items():
        artifacts.append(_Artifact(f"config/{name}", candidate, paths.config_root,
                                   Path(name), "file"))
    runtime = source / ".runtime"
    for name in DATABASE_FILES:
        database = runtime / name
        if database.is_file():
            artifacts.append(_Artifact(name, database, paths.state_root, Path(name), "database"))
    models = runtime / "models" / "local-asr"
    if models.is_dir():
        artifacts.append(_Artifact("models/local-asr", models, paths.cache_root,
                                   Path("models/local-asr"), "tree"))
    return artifacts


def _transaction_roots(
    paths: AppPaths, artifacts: Iterable[_Artifact] | None = None,
) -> tuple[Path, ...]:
    candidates = [paths.library_root.resolve(), paths.config_root.resolve(),
                  paths.state_root.resolve(), paths.cache_root.resolve()]
    if artifacts is not None:
        touched = {artifact.target_root.resolve() for artifact in artifacts}
        touched.add(paths.state_root.resolve())
        candidates = [candidate for candidate in candidates if candidate in touched]
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    for left in unique:
        for right in unique:
            if left == right:
                continue
            if _contains(left, right) or _contains(right, left):
                raise MigrationError(f"Overlapping migration roots are not supported: {left} and {right}")
    return tuple(unique)


def _validate_root_separation(source: Path, roots: Iterable[Path]) -> None:
    for root in roots:
        if _contains(source, root) or _contains(root, source):
            raise MigrationError(f"Migration source and destination overlap: {source} and {root}")


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda value: False)(path))


def _validate_tree(path: Path, boundary: Path) -> None:
    if _is_link(path):
        raise MigrationError(f"Links are not allowed in migration paths: {path}")
    try:
        path.resolve().relative_to(boundary.resolve())
    except ValueError as exc:
        raise MigrationError(f"Migration path escapes its boundary: {path}") from exc
    if path.is_dir():
        for child in path.rglob("*"):
            if _is_link(child):
                raise MigrationError(f"Links are not allowed in migration paths: {child}")
            try:
                child.resolve().relative_to(boundary.resolve())
            except ValueError as exc:
                raise MigrationError(f"Migration path escapes its boundary: {child}") from exc
            if not child.is_dir() and not child.is_file():
                raise MigrationError(f"Special files are not allowed in migration paths: {child}")


def _validate_source_database(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        if path.with_name(path.name + suffix).exists():
            raise ActiveDatabaseError(f"Source database has an active sidecar: {path.name}{suffix}")
    _quick_check(path)


def _quick_check(path: Path) -> None:
    try:
        uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise MigrationError(f"Invalid SQLite database: {path.name}") from exc
    if result is None or result[0] != "ok":
        raise MigrationError(f"SQLite quick_check failed: {path.name}")


def _copy_database(source: Path, target: Path, label: str,
                   source_root: Path, paths: AppPaths) -> None:
    _validate_supported_schema(source, label)
    if target.exists():
        if not _is_fresh_database(target, label):
            raise MigrationConflict(f"Destination database already contains state: {target}")
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    uri = source.resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as source_db, closing(sqlite3.connect(target)) as target_db:
            source_db.backup(target_db)
            target_db.commit()
    except sqlite3.DatabaseError as exc:
        target.unlink(missing_ok=True)
        raise MigrationError(f"SQLite backup failed: {source.name}") from exc
    _validate_source_database(source)
    _quick_check(target)
    if label == "imports.db":
        _rebase_imports_database(target, source_root / "content", paths.library_root)
    elif label == "medialab.db":
        _rebase_medialab_database(target, source_root, paths)
    _quick_check(target)


def _validate_supported_schema(database: Path, label: str) -> None:
    supported = IMPORT_SCHEMA_VERSION if label == "imports.db" else 2
    try:
        uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise MigrationError(f"Cannot read schema version from {label}") from exc
    if version > supported:
        raise MigrationConflict(
            f"Source {label} schema {version} is newer than supported schema {supported}"
        )


def _is_fresh_database(database: Path, label: str) -> bool:
    """Recognize only databases produced by a normal, unused first launch."""
    if any(database.with_name(database.name + suffix).exists()
           for suffix in ("-wal", "-shm", "-journal")):
        return False
    try:
        uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if label == "imports.db":
                expected, expected_version = {"batches", "jobs", "job_events"}, IMPORT_SCHEMA_VERSION
            elif label == "medialab.db":
                expected, expected_version = {"tasks", "files", "asr"}, 2
            else:
                return False
            if tables != expected or version != expected_version:
                return False
            return all(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None
                       for table in expected)
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError):
        return False


def _rebase_imports_database(database: Path, source_content: Path,
                             target_library: Path) -> None:
    """Rebase legacy/v2 import locators inside the staged copy only."""
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "jobs" not in tables:
            raise MigrationError("imports.db does not contain a jobs table")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        if "out_dir" in columns:
            with connection:
                for row in connection.execute(
                    "SELECT id,status,out_dir,error_json FROM jobs"
                ).fetchall():
                    rebased = _rebase_content_path(row["out_dir"], source_content, target_library)
                    error_json = row["error_json"]
                    if row["out_dir"] and rebased is None and row["status"] == "pending_index":
                        error_json = _locator_error("Stored legacy item path is outside source content")
                    connection.execute(
                        "UPDATE jobs SET out_dir=?,error_json=? WHERE id=?",
                        (rebased, error_json, row["id"]),
                    )
                _rewrite_import_json(connection, "jobs", "id", (
                    "request_json", "result_json", "error_json", "quality_json", "duplicate_json",
                ), source_content, target_library)
                if "job_events" in tables:
                    _rewrite_import_json(connection, "job_events", "id", ("payload_json",),
                                         source_content, target_library)
    # The production store owns the only supported v1→current schema migration.
    # The staged state root may already contain user-owned migration backups copied
    # from the destination snapshot. Only remove a backup created by this opening.
    backup_pattern = f"{database.name}.backup-v*-*"
    existing_backups = set(database.parent.glob(backup_pattern))
    try:
        store = ImportStore(database, target_library)
    except SchemaVersionError as exc:
        raise MigrationError(f"Unsupported imports.db schema: {exc}") from exc
    store.close()
    # ImportStore's migration backup uses sqlite3's transaction context
    # manager, which commits but does not close the backup connection on
    # Windows. Collect the now-unreachable connection before moving staging.
    gc.collect()
    for backup in set(database.parent.glob(backup_pattern)) - existing_backups:
        backup.unlink()

    source_key, target_key = _library_key(source_content), _library_key(target_library)
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        if not {"library_key", "item_path"} <= columns:
            raise MigrationError("imports.db migration did not produce locator schema")
        with connection:
            # Legacy migration already assigned target_key; v2 source rows still
            # carry source_key. Other historical libraries remain intentionally
            # detached because their roots cannot be reconstructed safely.
            rows = connection.execute(
                "SELECT id,status,library_key,item_path,error_json FROM jobs WHERE library_key IN (?,?)",
                (source_key, target_key),
            ).fetchall()
            for row in rows:
                locator = _safe_item_locator(row["item_path"], source_content)
                error_json = row["error_json"]
                if row["item_path"] and locator is None and row["status"] == "pending_index":
                    error_json = _locator_error("Stored item locator is invalid or missing from source content")
                connection.execute(
                    "UPDATE jobs SET library_key=?,item_path=?,error_json=? WHERE id=?",
                    (target_key, locator, error_json, row["id"]),
                )


def _rewrite_import_json(connection: sqlite3.Connection, table: str, identity: str,
                         columns: tuple[str, ...], source_content: Path,
                         target_library: Path) -> None:
    available = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    selected = [column for column in columns if column in available]
    if not selected:
        return
    for row in connection.execute(
        f"SELECT {identity},{','.join(selected)} FROM {table}"
    ).fetchall():
        for column in selected:
            raw = row[column]
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                continue
            rewritten = _rewrite_out_dirs(value, source_content, target_library)
            if rewritten != value:
                connection.execute(
                    f"UPDATE {table} SET {column}=? WHERE {identity}=?",
                    (json.dumps(rewritten), row[identity]),
                )


def _rewrite_out_dirs(value: object, source_content: Path, target_library: Path) -> object:
    if isinstance(value, dict):
        rewritten: dict[str, object] = {}
        for key, item in value.items():
            if key == "out_dir":
                rebased = _rebase_content_path(item, source_content, target_library)
                if rebased is not None:
                    rewritten["out_dir"] = rebased
                continue
            rewritten[str(key)] = _rewrite_out_dirs(item, source_content, target_library)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_out_dirs(item, source_content, target_library) for item in value]
    return value


def _rebase_content_path(value: object, source_content: Path,
                         target_library: Path) -> str | None:
    relative = _contained_relative(value, source_content)
    return str(target_library / relative) if relative is not None and relative.parts else None


def _safe_item_locator(value: object, source_content: Path) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    locator = PurePosixPath(value)
    if locator.is_absolute() or ".." in locator.parts or not locator.parts:
        return None
    candidate = source_content.joinpath(*locator.parts).resolve()
    try:
        candidate.relative_to(source_content.resolve())
    except ValueError:
        return None
    return locator.as_posix() if candidate.exists() else None


def _locator_error(message: str) -> str:
    return json.dumps({
        "code": "migration_path_outside_library", "message": message,
        "retryable": False, "source": "migration",
    })


def _library_key(root: Path) -> str:
    normalized = str(root.resolve()).replace("\\", "/")
    if os.name == "nt":
        normalized = normalized.casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _rebase_medialab_database(database: Path, source_root: Path,
                              paths: AppPaths) -> None:
    mappings = (
        (source_root / "content", paths.library_root),
        (source_root / ".runtime" / "models", paths.cache_root / "models"),
        (source_root / ".runtime" / "tmp", paths.tmp_root),
        (source_root / ".runtime" / "openvino-cache", paths.cache_root / "openvino"),
        (source_root / ".runtime", paths.state_root),
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        with connection:
            if "tasks" in tables:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
                if "out_dir" in columns:
                    for row in connection.execute("SELECT id,out_dir FROM tasks").fetchall():
                        if not row["out_dir"]:
                            continue
                        rebased = _rebase_runtime_path(row["out_dir"], mappings)
                        assignments, values = ["out_dir=?"], [rebased]
                        if rebased is None:
                            if "error_detail" in columns:
                                assignments.append("error_detail=CASE WHEN error_detail IS NULL OR error_detail='' THEN ? ELSE error_detail || '; ' || ? END")
                                values.extend(["Migration cleared an out-of-root path"] * 2)
                            if "error_code" in columns:
                                assignments.append("error_code=COALESCE(error_code,'migration_path_outside_root')")
                            if "error_source" in columns:
                                assignments.append("error_source='migration'")
                            if "error_retryable" in columns:
                                assignments.append("error_retryable=0")
                        values.append(row["id"])
                        connection.execute(
                            f"UPDATE tasks SET {','.join(assignments)} WHERE id=?", values
                        )
            if "files" in tables:
                _rebase_medialab_columns(connection, "files", "id", ("path",), mappings)
            if "asr" in tables:
                _rebase_medialab_columns(
                    connection, "asr", "id", ("raw_path", "normalized_path", "txt_path"), mappings
                )


def _rebase_medialab_columns(connection: sqlite3.Connection, table: str, identity: str,
                             columns: tuple[str, ...], mappings: tuple[tuple[Path, Path], ...]) -> None:
    available = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    selected = [column for column in columns if column in available]
    if not selected:
        return
    for row in connection.execute(
        f"SELECT {identity},{','.join(selected)} FROM {table}"
    ).fetchall():
        for column in selected:
            if row[column]:
                connection.execute(
                    f"UPDATE {table} SET {column}=? WHERE {identity}=?",
                    (_rebase_runtime_path(row[column], mappings), row[identity]),
                )


def _rebase_runtime_path(value: object,
                         mappings: tuple[tuple[Path, Path], ...]) -> str | None:
    for source, target in mappings:
        relative = _contained_relative(value, source)
        if relative is not None:
            return str(target / relative)
    return None


def _contained_relative(value: object, root: Path) -> Path | None:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return None
    try:
        return Path(value).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
def _merge_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.rglob("*")):
        relative = child.relative_to(source)
        destination = target / relative
        if child.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            _merge_file(child, destination)


def _merge_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or _sha256(source) != _sha256(target):
            raise MigrationConflict(f"Destination file conflicts with source: {target}")
        return
    shutil.copy2(source, target)


def _inventory(artifacts: Iterable[_Artifact]) -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    for artifact in artifacts:
        if artifact.source.is_file():
            inventory.append({"artifact": artifact.label, "path": ".", "size": artifact.source.stat().st_size,
                              "sha256": _sha256(artifact.source)})
        else:
            for file in sorted(path for path in artifact.source.rglob("*") if path.is_file()):
                inventory.append({"artifact": artifact.label,
                                  "path": file.relative_to(artifact.source).as_posix(),
                                  "size": file.stat().st_size, "sha256": _sha256(file)})
    return inventory


def _validate_staged(artifacts: Iterable[_Artifact], stages: dict[Path, Path],
                     roots: tuple[Path, ...]) -> None:
    for artifact in artifacts:
        transaction_root = _owning_root(artifact.target_root, roots)
        target = (stages[transaction_root] / artifact.target_root.relative_to(transaction_root)
                  / artifact.relative_target)
        if artifact.kind == "database":
            _quick_check(target)
        elif artifact.kind == "file":
            if _sha256(artifact.source) != _sha256(target):
                raise MigrationError(f"Staged file validation failed: {artifact.label}")
        else:
            for source_file in (path for path in artifact.source.rglob("*") if path.is_file()):
                target_file = target / source_file.relative_to(artifact.source)
                if not target_file.is_file() or _sha256(source_file) != _sha256(target_file):
                    raise MigrationError(f"Staged tree validation failed: {artifact.label}")


def _validate_completed_targets(artifacts: Iterable[_Artifact]) -> None:
    for artifact in artifacts:
        target = artifact.target_root / artifact.relative_target
        if artifact.kind == "database":
            if not target.is_file():
                raise MigrationConflict(f"Migrated database is missing: {target}")
            _quick_check(target)
        elif artifact.kind == "file":
            if not target.is_file():
                raise MigrationConflict(f"Migrated artifact is missing: {target}")
        else:
            for source_file in (path for path in artifact.source.rglob("*") if path.is_file()):
                destination = target / source_file.relative_to(artifact.source)
                if not destination.is_file():
                    raise MigrationConflict(f"Migrated artifact is missing: {destination}")


def _owning_root(target: Path, roots: tuple[Path, ...]) -> Path:
    for root in roots:
        if _contains(root, target):
            return root
    raise MigrationError(f"Target is outside configured migration roots: {target}")


def _read_marker(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise MigrationConflict(f"Invalid migration marker: {path}") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise MigrationConflict(f"Invalid migration marker: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_exact_tree(path: Path) -> None:
    resolved = path.resolve()
    if resolved != path.parent.resolve() / path.name or not path.name.startswith("."):
        raise MigrationError(f"Refusing to remove unexpected migration path: {path}")
    shutil.rmtree(path)


def _commit_rename(source: Path, target: Path) -> None:
    """One durable rename boundary; tests replace this to simulate hard exits."""
    source.replace(target)
    _fsync_directory(target.parent)


def _write_journal(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp")
    data = json.dumps(value, indent=2).encode("utf-8")
    with pending.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)
    _fsync_directory(path.parent)


def _clear_journal(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.with_name(f".{path.name}.tmp").unlink(missing_ok=True)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _recovery_roots(path: Path, paths: AppPaths) -> tuple[Path, ...]:
    """Resolve a canonical journal subset, permitting a config-proven custom library."""
    configured = _transaction_roots(paths)
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
        entries = journal["roots"]
        token = journal["token"]
        targets = tuple(Path(entry["target"]).resolve() for entry in entries)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise MigrationConflict(f"Invalid durable migration journal: {path}") from exc
    if (not isinstance(token, str) or not token or not isinstance(entries, list)
            or not targets or paths.state_root.resolve() not in targets):
        raise MigrationConflict(f"Unsafe durable migration journal: {path}")

    configured_subset = tuple(root for root in configured if root in targets)
    if targets == configured_subset:
        return targets

    unknown = [target for target in targets if target not in configured]
    if len(unknown) != 1:
        raise MigrationConflict(f"Unsafe durable migration journal: {path}")
    candidate = paths.with_library(unknown[0])
    candidate_roots = _transaction_roots(candidate)
    candidate_subset = tuple(root for root in candidate_roots if root in targets)
    if candidate_subset != targets:
        raise MigrationConflict(f"Unsafe durable migration journal: {path}")

    config_target = paths.config_root.resolve()
    config_roots = [config_target]
    for entry in entries:
        if Path(str(entry.get("target", ""))).resolve() == config_target:
            try:
                config_roots.extend((Path(str(entry["stage"])).resolve(),
                                     Path(str(entry["backup"])).resolve()))
            except (OSError, TypeError, KeyError, ValueError) as exc:
                raise MigrationConflict(f"Invalid durable migration journal: {path}") from exc
            break
    persisted_roots: list[Path] = []
    for config_root in config_roots:
        config_path = config_root / CONFIG_NAME
        if config_path.is_symlink():
            raise MigrationConflict(f"Unsafe recovery configuration link: {config_path}")
        if config_path.is_file():
            try:
                persisted = AppConfig.load(config_path).library_root
            except ValueError as exc:
                raise MigrationConflict(f"Invalid recovery configuration: {config_path}") from exc
            if persisted is not None:
                persisted_roots.append(persisted.resolve())
    if not persisted_roots or any(root != unknown[0] for root in persisted_roots):
        raise MigrationConflict(f"Custom migration library is not proven by persisted config: {path}")
    return targets


def _recover_journal(path: Path, expected_roots: tuple[Path, ...]) -> None:
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, UnicodeError, ValueError) as exc:
        raise MigrationConflict(f"Invalid durable migration journal: {path}") from exc
    entries = journal.get("roots") if isinstance(journal, dict) else None
    token = journal.get("token") if isinstance(journal, dict) else None
    if (journal.get("version") != 1 or journal.get("phase") not in {"committing", "committed"}
            or not isinstance(token, str) or not token
            or not isinstance(entries, list) or len(entries) != len(expected_roots)):
        raise MigrationConflict(f"Invalid durable migration journal: {path}")
    expected = [root.resolve() for root in expected_roots]
    normalized: list[tuple[dict[str, object], Path, Path, Path]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise MigrationConflict(f"Invalid durable migration journal: {path}")
        try:
            target = Path(str(entry["target"])).resolve()
            stage = Path(str(entry["stage"])).resolve()
            backup = Path(str(entry["backup"])).resolve()
        except (KeyError, OSError, ValueError) as exc:
            raise MigrationConflict(f"Invalid durable migration journal: {path}") from exc
        if (target != expected[index] or stage.parent != target.parent or backup.parent != target.parent
                or stage.name != f".{target.name}.migrate-{token}"
                or backup.name != f".{target.name}.pre-migrate-{token}"
                or entry.get("state") not in {"prepared", "backup_moved", "committed"}
                or type(entry.get("had_target")) is not bool):
            raise MigrationConflict(f"Unsafe durable migration journal: {path}")
        normalized.append((entry, target, stage, backup))
    states = [str(entry[0]["state"]) for entry in normalized]
    first_incomplete = next((index for index, state in enumerate(states)
                             if state != "committed"), len(states))
    tail = states[first_incomplete:]
    if (any(state == "committed" for state in tail)
            or sum(state == "backup_moved" for state in tail) > 1
            or ("backup_moved" in tail and tail[0] != "backup_moved")
            or any(state == "backup_moved" and not normalized[index][0]["had_target"]
                   for index, state in enumerate(states))):
        raise MigrationConflict(f"Invalid durable migration journal ordering: {path}")
    if journal.get("phase") == "committed" and any(state != "committed" for state in states):
        raise MigrationConflict(f"Invalid durable migration journal ordering: {path}")
    fully_committed = journal.get("phase") == "committed" and all(
        entry[0].get("state") == "committed" for entry in normalized
    )
    if fully_committed:
        for _, _, stage, backup in normalized:
            if stage.exists():
                _remove_exact_tree(stage)
            if backup.exists():
                _remove_exact_tree(backup)
        _clear_journal(path)
        return

    # Any incomplete transaction rolls back to the exact pre-migration roots.
    for entry, target, stage, backup in reversed(normalized):
        had_target = bool(entry["had_target"])
        if backup.exists():
            if target.exists():
                if stage.exists():
                    raise MigrationConflict(f"Ambiguous migration recovery state for {target}")
                target.replace(stage)
                _fsync_directory(target.parent)
            backup.replace(target)
            _fsync_directory(target.parent)
        elif not had_target and target.exists() and not stage.exists():
            target.replace(stage)
            _fsync_directory(target.parent)
        elif had_target and not target.exists():
            raise MigrationConflict(f"Missing pre-migration backup for {target}")
        if stage.exists():
            _remove_exact_tree(stage)
    _clear_journal(path)
