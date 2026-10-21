from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from echo_reader.desktop import (
    _child_environment,
    _is_loopback_url,
    _is_session_launch_url,
    _read_instance,
    _reserve_loopback_socket,
)
from echo_reader.server import create_app


class Coordinator:
    def __init__(self) -> None:
        self.closed = False

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    async def index_health(self) -> None:
        return None

    def capabilities(self):
        return {"transcription": {"online": {}, "local": {}}}


def test_worker_self_test_runs_without_starting_application():
    from echo_reader.desktop import main

    with (
        patch("echo_reader.desktop._run_worker_process_smoke") as worker_smoke,
        patch("echo_reader.desktop.AppPaths.resolve") as resolve_paths,
    ):
        assert main(["--self-test-worker"]) == 0

    worker_smoke.assert_called_once_with()
    resolve_paths.assert_not_called()


def test_loopback_instance_url_validation_and_read(tmp_path: Path):
    valid = "http://127.0.0.1:43123"
    assert _is_loopback_url(valid)
    assert _is_loopback_url("http://[::1]:43123")
    for value in (
        "https://127.0.0.1:43123",
        "http://example.com:43123",
        "http://user@127.0.0.1:43123",
        "http://127.0.0.1:43123/api",
        "http://127.0.0.1",
    ):
        assert not _is_loopback_url(value)

    launch_url = f"{valid}/session/abcdefghijklmnopqrstuvwxyz_ABCDEFG-0123456789"
    assert _is_session_launch_url(launch_url, valid)
    assert not _is_session_launch_url(
        "http://127.0.0.1:43124/session/abcdefghijklmnopqrstuvwxyz_ABCDEFG-0123456789",
        valid,
    )

    path = tmp_path / "instance.json"
    path.write_text(json.dumps({
        "schema_version": 1, "state": "ready", "pid": os.getpid(), "url": valid,
        "launch_url": launch_url,
        "boot_id": "boot-id-at-least-16-characters",
    }), encoding="utf-8")
    record = _read_instance(path)
    assert record is not None and record.launch_url == launch_url and record.pid == os.getpid()
    path.write_text(json.dumps({
        "schema_version": 1, "state": "ready", "pid": os.getpid(),
        "url": "http://example.com:80",
        "launch_url": launch_url,
        "boot_id": "boot-id-at-least-16-characters",
    }), encoding="utf-8")
    assert _read_instance(path) is None


def test_reserved_socket_and_child_environment_are_loopback_and_explicit(tmp_path: Path):
    listener, url = _reserve_loopback_socket()
    try:
        assert _is_loopback_url(url)
        paths = type("Paths", (), {"library_root": tmp_path / "library", "cache_root": tmp_path / "cache"})()
        env = _child_environment(
            paths, url, "internal-secret", "public-secret-token-at-least-32-chars",
            "boot-id", tmp_path / "ready.json", tmp_path / "web", tmp_path / "tools",
        )
        assert env["ECHO_BIND"] == "127.0.0.1:0"
        assert env["IMPORT_API_URL"] == url
        assert env["ECHO_INTERNAL_TOKEN"] == "internal-secret"
        assert env["ECHO_PUBLIC_TOKEN"] == "public-secret-token-at-least-32-chars"
        assert env["ECHO_BOOT_ID"] == "boot-id"
        assert env["ECHO_HOST_PID"] == str(os.getpid())
        assert env["LIBRARY_ROOT"] == str(tmp_path / "library")
        assert env["ECHO_CACHE_DIR"] == str(tmp_path / "cache")
    finally:
        listener.close()


def test_windows_reserved_socket_is_exclusive_before_bind():
    listener = MagicMock()
    listener.getsockname.return_value = ("127.0.0.1", 43123)
    with (
        patch("echo_reader.desktop.sys.platform", "win32"),
        patch.object(socket, "SO_EXCLUSIVEADDRUSE", 4, create=True),
        patch("echo_reader.desktop.socket.socket", return_value=listener),
    ):
        reserved, url = _reserve_loopback_socket()

    assert reserved is listener
    assert url == "http://127.0.0.1:43123"
    assert listener.method_calls == [
        ("setsockopt", (socket.SOL_SOCKET, 4, 1), {}),
        ("bind", (("127.0.0.1", 0),), {}),
        ("listen", (128,), {}),
        ("setblocking", (False,), {}),
        ("getsockname", (), {}),
    ]


def test_installed_shutdown_is_idle_only_and_disables_new_work(tmp_path: Path):
    coordinator = Coordinator()
    callbacks: list[str] = []
    app = create_app(
        library_root=tmp_path / "library",
        runtime_root=tmp_path / "runtime",
        install_mode=True,
        internal_token="test-token",
        coordinator_factory=lambda _paths: coordinator,
        bridge=None,
        shutdown_callback=lambda: callbacks.append("called"),
    )
    with TestClient(app, base_url="http://127.0.0.1:3100") as client:
        assert client.get("/api/v1/health").status_code == 401
        client.headers["X-Echo-Internal-Token"] = "test-token"
        assert client.post(
            "/api/v1/app/shutdown",
            headers={"Origin": "http://attacker.test", "Sec-Fetch-Site": "cross-site"},
        ).status_code == 403
        with patch.object(app.state.imports.store, "active_count", return_value=1):
            blocked = client.post("/api/v1/app/shutdown")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "imports_active"

        response = client.post("/api/v1/app/shutdown")
        assert response.status_code == 202
        assert response.json() == {"status": "shutting_down"}
        assert callbacks == ["called"]
        rejected = client.post("/api/v1/import-batches", json={"urls": ["https://example.test/a"]})
        assert rejected.status_code == 503
    assert coordinator.closed


def test_development_service_has_no_process_shutdown_endpoint(tmp_path: Path):
    coordinator = Coordinator()
    app = create_app(
        library_root=tmp_path / "library",
        runtime_root=tmp_path / "runtime",
        install_mode=False,
        coordinator_factory=lambda _paths: coordinator,
        bridge=None,
        shutdown_callback=lambda: None,
    )
    with TestClient(app) as client:
        assert client.post("/api/v1/app/shutdown").status_code == 404


def test_desktop_recovers_interrupted_migration_before_startup(tmp_path: Path):
    state_root = tmp_path / "state"
    paths = SimpleNamespace(
        state_root=state_root,
        ensure_directories=lambda: state_root.mkdir(parents=True, exist_ok=True),
    )
    mutex = MagicMock()
    mutex.acquire.return_value = True
    order: list[str] = []

    def recover(actual_paths):
        assert actual_paths is paths
        order.append("recover")
        return True

    async def run(**_kwargs):
        order.append("run")
        return 0

    with (
        patch("echo_reader.desktop.AppPaths.resolve", return_value=paths),
        patch("echo_reader.desktop.SingleInstance", return_value=mutex),
        patch("echo_reader.desktop.recover_interrupted_migration", side_effect=recover),
        patch("echo_reader.desktop.run_desktop", new=AsyncMock(side_effect=run)),
    ):
        from echo_reader.desktop import main

        assert main(["--no-browser"]) == 0

    assert order == ["recover", "run"]
    mutex.close.assert_called_once_with()


def test_desktop_reports_expected_migration_failure(tmp_path: Path):
    from echo_reader.desktop import main
    from echo_reader.migration import MigrationConflict

    paths = SimpleNamespace(state_root=tmp_path / "state")
    mutex = MagicMock()
    mutex.acquire.return_value = True
    with (
        patch("echo_reader.desktop.AppPaths.resolve", return_value=paths),
        patch("echo_reader.desktop.SingleInstance", return_value=mutex),
        patch("echo_reader.desktop.recover_interrupted_migration", return_value=False),
        patch("echo_reader.desktop.migrate_from", side_effect=MigrationConflict("conflict")),
        patch("echo_reader.desktop._show_error") as show_error,
    ):
        assert main(["--migrate-from", str(tmp_path / "old")]) == 1

    show_error.assert_called_once()
    assert "conflict" in show_error.call_args.args[0]


def test_migration_refuses_to_succeed_while_an_instance_is_running(tmp_path: Path):
    from echo_reader.desktop import main

    paths = SimpleNamespace(state_root=tmp_path / "state")
    mutex = MagicMock()
    mutex.acquire.return_value = False
    with (
        patch("echo_reader.desktop.AppPaths.resolve", return_value=paths),
        patch("echo_reader.desktop.SingleInstance", return_value=mutex),
        patch("echo_reader.desktop._open_existing") as open_existing,
        patch("echo_reader.desktop._show_error") as show_error,
    ):
        assert main(["--migrate-from", str(tmp_path / "old")]) == 1

    open_existing.assert_not_called()
    show_error.assert_called_once()
    assert "迁移前" in show_error.call_args.args[0]
