"""Installed Windows host for the local Echo Reader web application.

The frozen Python executable owns the durable application paths, import API,
credential bridge, and child-process lifecycle.  Rust remains a focused reader
and single-origin gateway.  Neither process binds a non-loopback interface.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import ipaddress
import json
import logging
import multiprocessing
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn

from .app_config import AppPaths
from .migration import MigrationError, migrate_from, recover_interrupted_migration
from .server import create_app

APP_MUTEX_NAMES = (
    r"Global\EchoReader.LocalApp",
    r"Local\EchoReader.LocalApp",
)
INSTANCE_FILE_NAME = "instance.json"
READY_FILE_NAME = "echo-ready.json"
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000


class _SecurityAttributes(ctypes.Structure):
    _fields_ = (
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    )


class DesktopError(RuntimeError):
    """A user-actionable host startup or shutdown failure."""


def _worker_process_smoke(value: str) -> str:
    """Top-level spawn target used to verify a frozen multiprocessing bundle."""

    return value


def _run_worker_process_smoke() -> None:
    with ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn")
    ) as executor:
        result = executor.submit(_worker_process_smoke, "echo-reader-worker").result(timeout=30)
    if result != "echo-reader-worker":
        raise DesktopError("本地 ASR 工作进程自检返回了无效结果")


@dataclass(frozen=True)
class InstanceRecord:
    pid: int
    url: str
    launch_url: str
    boot_id: str


class SingleInstance:
    """Process-lifetime Windows mutex; no stale lock file can block startup."""

    def __init__(self, names: Sequence[str] = APP_MUTEX_NAMES) -> None:
        self.names = tuple(names)
        self._handles: list[int] = []

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        descriptor = wintypes.LPVOID()
        descriptor_size = wintypes.ULONG()
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.ULONG),
        )
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:(A;;GA;;;WD)", 1, ctypes.byref(descriptor), ctypes.byref(descriptor_size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False
        )
        kernel32.CreateMutexW.argtypes = (
            ctypes.POINTER(_SecurityAttributes), wintypes.BOOL, wintypes.LPCWSTR
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        try:
            for name in self.names:
                ctypes.set_last_error(0)
                handle = kernel32.CreateMutexW(ctypes.byref(attributes), False, name)
                if not handle:
                    self.close()
                    raise ctypes.WinError(ctypes.get_last_error())
                if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                    kernel32.CloseHandle(handle)
                    self.close()
                    return False
                self._handles.append(int(handle))
            return True
        finally:
            kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
            kernel32.LocalFree.restype = wintypes.HLOCAL
            kernel32.LocalFree(descriptor)

    def close(self) -> None:
        if not self._handles or os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        for handle in reversed(self._handles):
            kernel32.CloseHandle(handle)
        self._handles.clear()

    def __enter__(self) -> "SingleInstance":
        if not self.acquire():
            raise DesktopError("Echo Reader is already running")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _is_loopback_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        loopback = bool(host) and (
            host.casefold() == "localhost" or ipaddress.ip_address(host).is_loopback
        )
        return bool(
            parsed.scheme == "http"
            and loopback
            and parsed.port is not None
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except (ValueError, UnicodeError):
        return False


def _is_session_launch_url(value: str, base_url: str) -> bool:
    try:
        parsed = urlsplit(value)
        base = urlsplit(base_url)
        token = parsed.path.removeprefix("/session/")
        return bool(
            parsed.scheme == base.scheme
            and parsed.hostname == base.hostname
            and parsed.port == base.port
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and len(token) >= 32
            and re.fullmatch(r"[A-Za-z0-9_-]+", token)
        )
    except (ValueError, UnicodeError):
        return False


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _read_instance(path: Path) -> InstanceRecord | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("state") != "ready"):
        return None
    pid = payload.get("pid")
    url = payload.get("url")
    launch_url = payload.get("launch_url")
    boot_id = payload.get("boot_id")
    if (type(pid) is not int or not isinstance(url, str) or not _is_loopback_url(url)
            or not isinstance(launch_url, str) or not _is_session_launch_url(launch_url, url)
            or not isinstance(boot_id, str) or len(boot_id) < 16):
        return None
    return InstanceRecord(pid=pid, url=url, launch_url=launch_url, boot_id=boot_id)


def _read_starting_pid(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("state") != "starting"):
        return None
    pid = payload.get("pid")
    return pid if type(pid) is int and pid > 0 else None


def _instance_healthy(record: InstanceRecord) -> bool:
    if not _process_alive(record.pid):
        return False
    try:
        response = httpx.get(
            f"{record.url}/health", timeout=0.75, follow_redirects=False,
            trust_env=False,
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    return bool(
        response.status_code == 200
        and isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("boot_id") == record.boot_id
    )


def _open_existing(instance_file: Path, *, timeout: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout
    missing_deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        record = _read_instance(instance_file)
        if record is not None and _instance_healthy(record):
            webbrowser.open(record.launch_url)
            return True
        starting_pid = _read_starting_pid(instance_file)
        if starting_pid is not None:
            if not _process_alive(starting_pid):
                return False
            missing_deadline = deadline
        elif time.monotonic() >= missing_deadline:
            return False
        time.sleep(0.1)
    return False


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _resolve_bundle_file(root: Path, override: Path | None, relative: str) -> Path:
    candidate = (override or root / relative).expanduser().resolve()
    if not candidate.is_file():
        raise DesktopError(f"Required application file is missing: {candidate}")
    return candidate


def _resolve_web_root(root: Path, override: Path | None) -> Path:
    candidate = (override or root / "web").expanduser().resolve()
    if not candidate.is_dir() or not (candidate / "index.html").is_file():
        raise DesktopError(f"Web application files are missing: {candidate}")
    return candidate


def _reserve_loopback_socket() -> tuple[socket.socket, str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    host, port = listener.getsockname()[:2]
    return listener, f"http://{host}:{port}"


def _child_environment(paths: AppPaths, import_url: str, internal_token: str,
                       public_token: str,
                       boot_id: str, ready_file: Path, web_root: Path,
                       tools_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "LIBRARY_ROOT": str(paths.library_root),
        "IMPORT_API_URL": import_url,
        "ECHO_INTERNAL_TOKEN": internal_token,
        "ECHO_PUBLIC_TOKEN": public_token,
        "ECHO_BOOT_ID": boot_id,
        "ECHO_HOST_PID": str(os.getpid()),
        "ECHO_BIND": "127.0.0.1:0",
        "ECHO_READY_FILE": str(ready_file),
        "ECHO_WEB_ROOT": str(web_root),
    })
    if tools_root.is_dir():
        env["PATH"] = f"{tools_root}{os.pathsep}{env.get('PATH', '')}"
    return env


def _rotate_file(path: Path, *, max_bytes: int = 2_000_000, backups: int = 3) -> None:
    if not path.is_file() or path.stat().st_size <= max_bytes:
        return
    oldest = path.with_suffix(path.suffix + f".{backups}")
    oldest.unlink(missing_ok=True)
    for index in range(backups - 1, 0, -1):
        source = path.with_suffix(path.suffix + f".{index}")
        if source.exists():
            source.replace(path.with_suffix(path.suffix + f".{index + 1}"))
    path.replace(path.with_suffix(path.suffix + ".1"))


async def _wait_ready_file(path: Path, process: subprocess.Popen[bytes], boot_id: str, *,
                           timeout: float = 30.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if process.poll() is not None:
            raise DesktopError(f"Reader service exited during startup (code {process.returncode})")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            url = payload.get("url") if isinstance(payload, dict) else None
            ready_boot_id = payload.get("boot_id") if isinstance(payload, dict) else None
        except (OSError, UnicodeError, ValueError):
            url = None
            ready_boot_id = None
        if isinstance(url, str) and _is_loopback_url(url) and ready_boot_id == boot_id:
            return url
        await asyncio.sleep(0.05)
    raise DesktopError("Reader service did not become ready in time")


async def _wait_http(url: str, *, timeout: float = 60.0,
                     guard: asyncio.Task[Any] | None = None,
                     headers: dict[str, str] | None = None) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    async with httpx.AsyncClient(
        timeout=1.5, follow_redirects=False, trust_env=False
    ) as client:
        while asyncio.get_running_loop().time() < deadline:
            if guard is not None and guard.done():
                if guard.cancelled():
                    raise DesktopError("Application service startup was cancelled")
                error = guard.exception()
                raise DesktopError(
                    f"Application service stopped during startup: {error or 'unknown error'}"
                )
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise DesktopError(f"Application service did not become healthy: {url}")


async def _request_reader_shutdown(public_url: str, token: str) -> None:
    async with httpx.AsyncClient(
        timeout=3.0, follow_redirects=False, trust_env=False
    ) as client:
        with suppress(httpx.HTTPError):
            await client.post(
                f"{public_url}/internal/v1/app/shutdown",
                headers={"X-Echo-Internal-Token": token},
            )


async def _stop_child(process: subprocess.Popen[bytes], public_url: str, token: str,
                      *, graceful_timeout: float = 10.0) -> None:
    if process.poll() is not None:
        return
    await _request_reader_shutdown(public_url, token)
    try:
        await asyncio.wait_for(asyncio.to_thread(process.wait), graceful_timeout)
        return
    except asyncio.TimeoutError:
        process.terminate()
    try:
        await asyncio.wait_for(asyncio.to_thread(process.wait), 5.0)
    except asyncio.TimeoutError:
        process.kill()
        await asyncio.to_thread(process.wait)


async def _stop_import_server(server: uvicorn.Server, task: asyncio.Task[Any], *,
                              graceful_timeout: float = 35.0,
                              force_timeout: float = 5.0) -> bool:
    """Bound shutdown even when a lifespan task deliberately shields cancellation."""
    if task.done():
        return True
    server.should_exit = True
    done, _pending = await asyncio.wait({task}, timeout=graceful_timeout)
    if done:
        return True
    server.force_exit = True
    task.cancel()
    done, _pending = await asyncio.wait({task}, timeout=force_timeout)
    return bool(done)


def _configure_logging(paths: AppPaths) -> logging.Logger:
    logger = logging.getLogger("echo_reader.desktop")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(
        paths.logs_root / "desktop.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    return logger


async def run_desktop(*, paths: AppPaths, boot_id: str,
                      backend: Path | None = None, web_root: Path | None = None,
                      open_browser: bool = True) -> int:
    paths.ensure_directories()
    logger = _configure_logging(paths)
    root = _bundle_root()
    backend_path = _resolve_bundle_file(root, backend, "echo-backend.exe")
    frontend_path = _resolve_web_root(root, web_root)
    tools_root = root / "tools"
    ready_file = paths.tmp_root / READY_FILE_NAME
    instance_file = paths.state_root / INSTANCE_FILE_NAME
    ready_file.unlink(missing_ok=True)
    import_socket, import_url = _reserve_loopback_socket()
    internal_token = secrets.token_urlsafe(32)
    public_token = secrets.token_urlsafe(32)
    shutdown_requested = asyncio.Event()
    backend_log_path = paths.logs_root / "echo-backend.log"
    _rotate_file(backend_log_path)
    backend_log = backend_log_path.open("ab", buffering=0)
    process: subprocess.Popen[bytes] | None = None
    server: uvicorn.Server | None = None
    server_task: asyncio.Task[None] | None = None
    application: Any | None = None
    public_url = ""
    abnormal_shutdown = False
    try:
        env = _child_environment(
            paths, import_url, internal_token, public_token, boot_id,
            ready_file, frontend_path, tools_root,
        )
        kwargs: dict[str, Any] = {
            "cwd": str(root), "env": env, "stdin": subprocess.DEVNULL,
            "stdout": backend_log, "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        process = subprocess.Popen([str(backend_path)], **kwargs)
        public_url = await _wait_ready_file(ready_file, process, boot_id)
        await _wait_http(f"{public_url}/health", timeout=20.0)

        application = create_app(
            install_mode=True,
            echo_url=public_url,
            internal_token=internal_token,
            shutdown_callback=shutdown_requested.set,
        )
        config = uvicorn.Config(
            application, host="127.0.0.1", port=0, log_config=None, access_log=False,
            timeout_graceful_shutdown=30,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        server_task = asyncio.create_task(server.serve(sockets=[import_socket]), name="import-api")
        await _wait_http(
            f"{import_url}/api/v1/health", timeout=90.0, guard=server_task,
            headers={"X-Echo-Internal-Token": internal_token},
        )
        launch_url = f"{public_url}/session/{public_token}"
        _atomic_write_json(instance_file, {
            "schema_version": 1,
            "state": "ready",
            "pid": os.getpid(),
            "url": public_url,
            "launch_url": launch_url,
            "boot_id": boot_id,
            "started_at": time.time(),
        })
        logger.info("Echo Reader ready at %s", public_url)
        if open_browser:
            webbrowser.open(launch_url)

        requested = asyncio.create_task(shutdown_requested.wait(), name="shutdown-requested")
        child_exited = asyncio.create_task(asyncio.to_thread(process.wait), name="reader-exited")
        done, pending = await asyncio.wait(
            {requested, child_exited, server_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            if task not in {server_task, child_exited}:
                task.cancel()
        if child_exited in done and not shutdown_requested.is_set():
            abnormal_shutdown = True
            raise DesktopError(f"Reader service stopped unexpectedly (code {process.returncode})")
        if server_task in done and not shutdown_requested.is_set():
            abnormal_shutdown = True
            exception = server_task.exception()
            raise DesktopError(f"Import service stopped unexpectedly: {exception or 'unknown error'}")
        return 0
    finally:
        instance_file.unlink(missing_ok=True)
        if abnormal_shutdown and application is not None:
            imports = application.state.imports
            imports.accepting = False
            with suppress(Exception):
                imports.store.interrupt_active()
        if server is not None and server_task is not None and not server_task.done():
            stopped = await _stop_import_server(server, server_task)
            if not stopped:
                logger.critical("Import service did not stop within the hard deadline")
                if application is not None:
                    with suppress(Exception):
                        application.state.imports.store.interrupt_active()
                if process is not None and process.poll() is None:
                    process.kill()
                backend_log.close()
                for handler in logger.handlers:
                    handler.flush()
                # MediaLab deliberately shields native ASR work from asyncio
                # cancellation.  At this point a normal interpreter shutdown
                # would wait forever for the same task.  The durable stores
                # were marked interrupted above; terminate the abnormal host.
                os._exit(70)
        else:
            import_socket.close()
        if process is not None:
            await _stop_child(process, public_url, internal_token)
        ready_file.unlink(missing_ok=True)
        backend_log.close()
        logger.info("Echo Reader stopped")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="EchoReader")
    parser.add_argument("--backend", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--web-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--migrate-from", type=Path, metavar="SOURCE",
        help="selectively migrate durable data from an existing Echo Reader checkout",
    )
    return parser


def _show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "Echo Reader", 0x10)
    else:
        print(message, file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test_worker:
        try:
            _run_worker_process_smoke()
        except (OSError, RuntimeError) as exc:
            _show_error(f"Echo Reader 工作进程自检失败：{exc}")
            return 1
        return 0
    paths = AppPaths.resolve(install_mode=True)
    instance_file = paths.state_root / INSTANCE_FILE_NAME
    mutex = SingleInstance()
    try:
        acquired = mutex.acquire()
    except OSError as exc:
        _show_error(f"无法启动 Echo Reader：{exc}")
        return 1
    if not acquired:
        if args.migrate_from is not None:
            _show_error("迁移前请先从侧栏退出正在运行的 Echo Reader。")
            return 1
        if not _open_existing(instance_file):
            _show_error(
                "Echo Reader 已在本机的另一个 Windows 会话中运行，"
                "但当前用户无法打开该实例。请先退出该实例。"
            )
            return 1
        return 0
    try:
        if recover_interrupted_migration(paths):
            paths = AppPaths.resolve(install_mode=True)
            instance_file = paths.state_root / INSTANCE_FILE_NAME
        if args.migrate_from is not None:
            migrate_from(args.migrate_from, paths)
        paths.ensure_directories()
        boot_id = secrets.token_urlsafe(24)
        _atomic_write_json(instance_file, {
            "schema_version": 1,
            "state": "starting",
            "pid": os.getpid(),
            "boot_id": boot_id,
            "started_at": time.time(),
        })
        return asyncio.run(run_desktop(
            paths=paths, boot_id=boot_id, backend=args.backend,
            web_root=args.web_root, open_browser=not args.no_browser,
        ))
    except (DesktopError, MigrationError, OSError, ValueError) as exc:
        _show_error(f"Echo Reader 启动失败：{exc}")
        return 1
    finally:
        mutex.close()


if __name__ == "__main__":
    raise SystemExit(main())
