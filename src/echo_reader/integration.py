"""Long-lived orchestration between MediaLab's data plane and Echo's control plane."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Callable

from .echo_api import EchoApiClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTENT_ROOT = PROJECT_ROOT / "content"


class ImportFailed(RuntimeError):
    """Structured failure crossing the MediaLab/import-service boundary."""

    def __init__(self, message: str, *, code: str = "import_failed",
                 source: str = "medialab", retryable: bool = True,
                 task_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.source = source
        self.retryable = retryable
        self.task_id = task_id


def _default_service_factory(**kwargs: Any) -> Any:
    from medialab import DownloadService

    return DownloadService(**kwargs)


class ImportCoordinator:
    """Own one MediaLab service for the lifetime of an application."""

    def __init__(
        self,
        library_root: str | Path,
        runtime_root: str | Path,
        echo_base_url: str = "http://127.0.0.1:3000",
        *,
        service_factory: Callable[..., Any] | None = None,
        echo_client: EchoApiClient | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        bridge: Any | None = None,
        credential_broker: Any | None = None,
        cache_root: str | Path | None = None,
        tmp_root: str | Path | None = None,
        config_root: str | Path | None = None,
        internal_token: str | None = None,
    ) -> None:
        self.library_root = self._absolute_path(library_root, "library_root")
        self.runtime_root = self._absolute_path(runtime_root, "runtime_root")
        self.cache_root = self._absolute_path(cache_root or runtime_root, "cache_root")
        self.tmp_root = self._absolute_path(tmp_root or (Path(runtime_root) / "tmp"), "tmp_root")
        self.config_root = self._absolute_path(
            config_root or (PROJECT_ROOT / "config"), "config_root"
        )
        self.echo_client = echo_client or EchoApiClient(echo_base_url, internal_token=internal_token)
        self.service_factory = service_factory or _default_service_factory
        self.on_event = on_event
        self.bridge = bridge
        if bridge is not None and credential_broker is None:
            from medialab.session import CredentialBroker

            credential_broker = CredentialBroker(bridge)
        self.credential_broker = credential_broker
        self._service: Any | None = None
        self._event_routes: dict[str, Callable[[str, dict[str, Any]], None]] = {}

    @staticmethod
    def _absolute_path(value: str | Path, name: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError(f"{name} must be an absolute path: {path}")
        return path.resolve()

    @property
    def config(self) -> dict[str, dict[str, str]]:
        return {
            "paths": {
                "delivery": str(self.library_root),
                "temp": str(self.tmp_root),
                "db": str(self.runtime_root / "medialab.db"),
            },
            "local_asr": {
                "model_dir": str((self.cache_root / "models" / "local-asr").resolve()),
                "cache_dir": str((self.cache_root / "openvino").resolve()),
            },
        }

    async def initialize(self) -> None:
        if self._service is not None:
            return
        self.library_root.mkdir(parents=True, exist_ok=True)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "config": self.config,
            "on_event": self._route_event,
            "config_path": self.config_root / "config.toml",
            "secrets_path": self.config_root / "secrets.toml",
        }
        if self.bridge is not None:
            from medialab.session import ExtensionSessionProvider

            kwargs.update(
                bridge=self.bridge,
                session_provider=ExtensionSessionProvider(
                    self.bridge, cfg=self.config, credential_broker=self.credential_broker
                ),
            )
        if self.credential_broker is not None:
            kwargs["credential_broker"] = self.credential_broker
        service = self.service_factory(**kwargs)
        try:
            await service.initialize()
            capabilities = await asyncio.to_thread(service.capabilities)
            self._validate_capabilities(capabilities)
        except BaseException:
            await service.close()
            raise
        self._service = service

    async def close(self) -> None:
        service, self._service = self._service, None
        self._event_routes.clear()
        if service is not None:
            await service.close()

    def capabilities(self) -> dict[str, Any]:
        service = self._require_service()
        result = service.capabilities()
        if inspect.isawaitable(result):
            raise RuntimeError("MediaLab capabilities() must be synchronous")
        self._validate_capabilities(result)
        return result

    @staticmethod
    def _validate_capabilities(result: Any) -> None:
        if inspect.isawaitable(result):
            raise RuntimeError("MediaLab capabilities() must be synchronous")
        if not isinstance(result, dict):
            raise ImportFailed("MediaLab capabilities response must be an object", retryable=False)

    async def plan_url(self, url: str, *, mode: str, asr_backend: str) -> dict[str, Any]:
        """Use MediaLab's public preflight seam before a job is persisted."""
        service = self._require_service()
        result = service.plan_import(url, mode=mode, asr_backend=asr_backend)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise ImportFailed("MediaLab plan response must be an object", retryable=False)
        if result.get("available") is False:
            raise ImportFailed(str(result.get("reason") or "requested backend is unavailable"),
                               code="capability_unavailable", retryable=False)
        return result

    async def process_url(
        self,
        url: str,
        *,
        mode: str = "auto",
        audio: str | None = None,
        audio_format: str = "m4a",
        speed_factor: float = 1.0,
        timeout: float | None = None,
        asr_backend: str = "online",
        local_timestamp_mode: str = "vad",
        local_diarization: bool = True,
        local_speaker_count: int = 0,
        rescan: bool = False,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if rescan:
            raise ValueError("process_url does not rescan; call rescan() explicitly")
        service = self._require_service()
        if mode not in {"auto", "download", "transcribe"} or asr_backend not in {"auto", "online", "local"}:
            raise ValueError("invalid import mode/backend combination")
        if mode == "auto":
            handle = await service.submit(
                url, out_dir=self.library_root, mode=mode, asr_backend=asr_backend,
                local_timestamp_mode=local_timestamp_mode,
                local_diarization=local_diarization,
                local_speaker_count=local_speaker_count,
            )
        elif mode == "download":
            handle = await service.download(url, audio=audio, out_dir=self.library_root)
        elif asr_backend == "local":
            handle = await service.transcribe_local(
                url, audio_format=audio_format, out_dir=self.library_root,
                local_timestamp_mode=local_timestamp_mode,
                local_diarization=local_diarization,
                local_speaker_count=local_speaker_count,
            )
        else:
            handle = await service.transcribe(
                url, audio_format=audio_format, out_dir=self.library_root,
                speed_factor=speed_factor, timeout=timeout,
            )
        task_id = getattr(handle, "id", None)
        if not isinstance(task_id, str) or not task_id:
            raise ImportFailed("MediaLab returned a task handle without an id",
                               code="invalid_task", retryable=False)
        if on_event is not None:
            self._event_routes[task_id] = on_event
        try:
            task = await self._wait_for_task(handle, service)
        finally:
            self._event_routes.pop(task_id, None)
        if not isinstance(task, dict) or task.get("status") != "done":
            status = task.get("status") if isinstance(task, dict) else None
            detail = task.get("error_detail") if isinstance(task, dict) else None
            code = str(task.get("error_code") or "import_failed") if isinstance(task, dict) else "import_failed"
            source = str(task.get("error_source") or task.get("source") or "medialab") if isinstance(task, dict) else "medialab"
            retryable_value = task.get("error_retryable") if isinstance(task, dict) else None
            retryable = True if retryable_value is None else bool(retryable_value)
            raise ImportFailed(
                f"MediaLab task {task_id} ended with status {status!r}"
                + (f": {detail}" if detail else ""),
                code=code, source=source, retryable=retryable, task_id=task_id,
            )
        return task

    async def rescan(self) -> dict[str, int]:
        return await self.echo_client.rescan()

    async def index_health(self) -> None:
        await self.echo_client.health()

    async def switch_library(self, path: Path) -> dict[str, Any]:
        return await self.echo_client.switch_library(str(path))

    async def import_url(self, url: str, *, rescan: bool = True, strict_rescan: bool = False,
                         **kwargs: Any) -> dict[str, Any]:
        """CLI convenience operation using the explicit coordinator lifecycle."""
        task = await self.process_url(url, rescan=False, **kwargs)
        result: dict[str, Any] = {"task": task, "rescan": None, "warning": None}
        if rescan:
            try:
                result["rescan"] = await self.rescan()
            except Exception as exc:
                message = f"导入成功，但 Echo 刷新失败：{exc}"
                if strict_rescan:
                    raise ImportFailed(message) from exc
                result["warning"] = message
        return result

    def _route_event(self, event: str, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event, payload)
        task_id = payload.get("id") or payload.get("task_id")
        callback = self._event_routes.get(task_id)
        if callback is not None:
            callback(event, payload)

    def _require_service(self) -> Any:
        if self._service is None:
            raise RuntimeError("ImportCoordinator must be initialized")
        return self._service

    @staticmethod
    async def _wait_for_task(handle: Any, service: Any) -> dict[str, Any] | None:
        waiter = getattr(handle, "wait", None)
        result = waiter(service) if waiter is not None else service.wait(handle.id)
        return await result if inspect.isawaitable(result) else result
