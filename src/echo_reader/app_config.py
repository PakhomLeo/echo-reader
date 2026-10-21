"""Host-owned paths and persisted application configuration."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_NAME = "app.json"


def _env_path(env: Mapping[str, str], name: str) -> Path | None:
    value = env.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


def _installed_default(env: Mapping[str, str], name: str, fallback: Path) -> Path:
    value = env.get(name, "").strip()
    return (Path(value).expanduser() if value else fallback).resolve()


def _documents_root() -> Path:
    """Resolve the current user's configured Documents known folder on Windows."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Personal")
            if isinstance(value, str) and value.strip():
                return Path(os.path.expandvars(value)).expanduser().resolve()
        except OSError:
            pass
    return (Path.home() / "Documents").resolve()


@dataclass(frozen=True)
class AppConfig:
    """Small host-owned configuration that must survive process restarts."""

    library_root: Path | None = None
    version: int = 1

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"Invalid persisted application config: {path}") from exc
        if (not isinstance(value, dict) or type(value.get("version", 1)) is not int
                or value.get("version", 1) != 1):
            raise ValueError(f"Invalid persisted application config: {path}")
        raw_root = value.get("library_root")
        if raw_root is not None and (not isinstance(raw_root, str) or not raw_root.strip()):
            raise ValueError(f"Invalid persisted application config: {path}")
        root = Path(raw_root).expanduser() if raw_root else None
        if root is not None and not root.is_absolute():
            raise ValueError(f"Invalid persisted application config: {path}")
        return cls(library_root=root.resolve() if root else None)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_name(f".{path.name}.tmp")
        pending.write_text(json.dumps({
            "version": self.version,
            "library_root": str(self.library_root) if self.library_root else None,
        }, indent=2), encoding="utf-8")
        pending.replace(path)


@dataclass(frozen=True)
class AppPaths:
    library_root: Path
    state_root: Path
    cache_root: Path
    logs_root: Path
    tmp_root: Path
    config_root: Path
    install_mode: bool

    @classmethod
    def resolve(
        cls,
        *,
        library_root: str | Path | None = None,
        runtime_root: str | Path | None = None,
        state_root: str | Path | None = None,
        cache_root: str | Path | None = None,
        logs_root: str | Path | None = None,
        tmp_root: str | Path | None = None,
        config_root: str | Path | None = None,
        install_mode: bool | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "AppPaths":
        env = os.environ if environ is None else environ
        installed = (
            install_mode
            if install_mode is not None
            else env.get("ECHO_READER_INSTALL_MODE", "").strip().casefold() in {"1", "true", "yes"}
            or bool(getattr(sys, "frozen", False))
        )
        explicit_runtime = Path(runtime_root).expanduser().resolve() if runtime_root else None
        if installed:
            local = _installed_default(env, "LOCALAPPDATA", Path.home() / "AppData" / "Local") / "EchoReader"
            documents = _documents_root() / "Echo Reader Library"
            defaults = {
                "library": documents,
                "state": local / "state",
                "cache": local / "cache",
                "logs": local / "logs",
                "tmp": local / "tmp",
                # Library paths and provider secrets are machine-local.  Do
                # not place either in a roaming profile.
                "config": local / "config",
            }
        else:
            runtime = explicit_runtime or PROJECT_ROOT / ".runtime"
            defaults = {
                "library": PROJECT_ROOT / "content",
                "state": runtime,
                "cache": runtime,
                "logs": runtime,
                "tmp": runtime / "tmp",
                "config": PROJECT_ROOT / "config",
            }
        if explicit_runtime is not None:
            defaults.update(state=explicit_runtime, cache=explicit_runtime,
                            logs=explicit_runtime, tmp=explicit_runtime / "tmp")
        config = Path(config_root).expanduser().resolve() if config_root else (
            _env_path(env, "ECHO_READER_CONFIG_ROOT") or defaults["config"].resolve()
        )
        persisted = AppConfig.load(config / CONFIG_NAME)
        explicit_library = Path(library_root).expanduser().resolve() if library_root else None
        environment_library = (
            _env_path(env, "ECHO_READER_LIBRARY_ROOT") or _env_path(env, "LIBRARY_ROOT")
        )
        library = (explicit_library or environment_library or persisted.library_root
                   or defaults["library"].resolve())
        resolved = cls(
            library_root=library,
            state_root=(Path(state_root).expanduser().resolve() if state_root else
                        _env_path(env, "ECHO_READER_STATE_ROOT") or defaults["state"].resolve()),
            cache_root=(Path(cache_root).expanduser().resolve() if cache_root else
                        _env_path(env, "ECHO_READER_CACHE_ROOT") or defaults["cache"].resolve()),
            logs_root=(Path(logs_root).expanduser().resolve() if logs_root else
                       _env_path(env, "ECHO_READER_LOGS_ROOT") or defaults["logs"].resolve()),
            tmp_root=(Path(tmp_root).expanduser().resolve() if tmp_root else
                      _env_path(env, "ECHO_READER_TMP_ROOT") or defaults["tmp"].resolve()),
            config_root=config,
            install_mode=installed,
        )
        if explicit_library is None and environment_library is None and persisted.library_root:
            resolved._validate_library_root(
                persisted.library_root,
                probe_writable=False,
                allow_missing_for_recovery=(
                    resolved.state_root.parent / ".echo-reader-migration-journal.json"
                ).is_file(),
            )
        return resolved

    def ensure_directories(self) -> None:
        for path in (self.library_root, self.state_root, self.cache_root,
                     self.logs_root, self.tmp_root, self.config_root):
            path.mkdir(parents=True, exist_ok=True)

    def with_library(self, library_root: str | Path) -> "AppPaths":
        return replace(self, library_root=Path(library_root).expanduser().resolve())

    def validate_library_root(self, library_root: str | Path) -> Path:
        """Return a safe writable library directory or raise ``ValueError``."""
        return self._validate_library_root(library_root, probe_writable=True)

    def _validate_library_root(
        self, library_root: str | Path, *, probe_writable: bool,
        allow_missing_for_recovery: bool = False,
    ) -> Path:
        candidate = Path(library_root).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Library path must be absolute")
        try:
            candidate = candidate.resolve()
        except OSError as exc:
            raise ValueError("Library path could not be resolved") from exc
        if candidate == Path(candidate.anchor).resolve():
            raise ValueError("Library path cannot be a filesystem root")
        for name, durable_root in (
            ("state", self.state_root), ("cache", self.cache_root),
            ("config", self.config_root), ("logs", self.logs_root), ("temporary", self.tmp_root),
        ):
            root = durable_root.resolve()
            if _contains_path(candidate, root) or _contains_path(root, candidate):
                raise ValueError(f"Library path must not overlap the {name} directory")
        if not candidate.is_dir():
            if allow_missing_for_recovery and not candidate.exists():
                return candidate
            raise ValueError("Library path must be an existing directory")
        if probe_writable:
            try:
                _probe_library_writable(candidate)
            except OSError as exc:
                raise ValueError("Library path must be writable") from exc
        return candidate

    def persist(self) -> None:
        AppConfig(library_root=self.library_root).save(self.config_root / CONFIG_NAME)

    def public_dict(self) -> dict[str, Any]:
        return {
            "library_root": str(self.library_root),
            "state_root": str(self.state_root),
            "cache_root": str(self.cache_root),
            "logs_root": str(self.logs_root),
            "tmp_root": str(self.tmp_root),
            "config_root": str(self.config_root),
            "install_mode": self.install_mode,
        }


def _contains_path(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _probe_library_writable(path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=".echo-reader-write-test-", dir=path, delete=True,
    ) as stream:
        stream.write(b"ok")
        stream.flush()
