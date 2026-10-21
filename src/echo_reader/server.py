"""Durable local import control plane for Echo Reader."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import logging
import re
import secrets
import time
import math
import os
import threading
import ipaddress
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .app_config import AppPaths
from .enrichment import EnrichmentService
from .import_store import ImportStore, TERMINAL, now, sanitize_text
from .integration import ImportCoordinator, ImportFailed


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"queued", "running", "pending_index", "succeeded", "failed", "cancelled", "interrupted"}
AudioCodec = Literal["mp3", "m4a", "aac", "flac", "wav", "opus", "ogg"]
_DEFAULT_BRIDGE = object()


class SharedOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "download", "transcribe"] = "auto"
    audio: AudioCodec | None = None
    audio_format: AudioCodec = "m4a"
    speed_factor: float = Field(default=1.0, gt=0, le=10)
    timeout: float | None = Field(default=None, gt=0, le=86400)
    asr_backend: Literal["auto", "online", "local"] = "auto"
    local_timestamp_mode: Literal["model", "vad"] = "vad"
    local_diarization: bool = True
    local_speaker_count: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def compatible(self) -> "SharedOptions":
        if self.asr_backend == "local" and self.mode == "download":
            raise ValueError("local backend cannot be used with mode=download")
        if not self.local_diarization and self.local_speaker_count:
            raise ValueError("local_speaker_count must be 0 when diarization is disabled")
        return self


class BatchRequest(SharedOptions):
    urls: list[str] = Field(min_length=1, max_length=50)

    @field_validator("urls")
    @classmethod
    def valid_urls(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value or len(value) > 4096:
                raise ValueError("each URL must contain 1..4096 characters")
            try:
                parsed = urlsplit(value)
            except ValueError as exc:
                raise ValueError("each URL must be an absolute HTTPS URL") from exc
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("each URL must be an absolute HTTPS URL without credentials")
            normalized = value
            if normalized in seen:
                raise ValueError("URLs must be unique")
            seen.add(normalized)
        return values


class TtsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice: str | None = Field(default=None, pattern=r"^[a-z]{2}_[a-z0-9]{1,32}$")
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


class LibrarySwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=32767)


class ImportService:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.content_root = paths.library_root
        self.store = ImportStore(paths.state_root / "imports.db", self.content_root)
        self.coordinator: Any | None = None
        self.coordinator_factory: Callable[[AppPaths], Any] | None = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.workers: list[asyncio.Task[None]] = []
        self.transient_urls: dict[str, str] = {}
        self.indexing = {"available": False, "reason": "Echo health check pending"}
        self.indexing_revision = 0
        self.health_task: asyncio.Task[None] | None = None
        self.state_lock = asyncio.Lock()
        self.warning_lock = threading.Lock()
        self.event_warnings: dict[str, list[str]] = {}
        self.accepting = False

    async def start(self, coordinator_factory: Callable[[AppPaths], Any]) -> None:
        self.coordinator_factory = coordinator_factory
        coordinator = coordinator_factory(self.paths)
        await coordinator.initialize()
        self.coordinator = coordinator
        for job_id in self.store.queued_ids():
            self.queue.put_nowait(job_id)
        self.workers = [asyncio.create_task(self._worker(), name=f"import-worker-{index}") for index in range(3)]
        self.health_task = asyncio.create_task(self._probe_indexing(), name="echo-index-health")
        self.accepting = True

    async def close(self) -> None:
        self.accepting = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        if self.health_task is not None:
            self.health_task.cancel()
            await asyncio.gather(self.health_task, return_exceptions=True)
        self.store.interrupt_active()
        try:
            if self.coordinator is not None:
                await self.coordinator.close()
        finally:
            self.transient_urls.clear()
            self.event_warnings.clear()
            self.store.close()

    async def create_batch(self, request: BatchRequest) -> dict[str, Any]:
        async with self.state_lock:
            if not self.accepting or self.coordinator is None:
                raise HTTPException(503, "Import service is not accepting work")
            options = request.model_dump(exclude={"urls"})
            plans: list[dict[str, Any]] = []
            for url in request.urls:
                try:
                    plan = await self.coordinator.plan_url(
                        url, mode=request.mode, asr_backend=request.asr_backend
                    )
                except (ImportFailed, ValueError) as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                blocker = plan.get("blocker")
                if blocker:
                    message = blocker.get("reason") if isinstance(blocker, dict) else str(blocker)
                    raise HTTPException(status_code=422, detail=message)
                blockers = plan.get("blockers")
                if blockers:
                    message = blockers[0].get("reason") if isinstance(blockers[0], dict) else str(blockers[0])
                    raise HTTPException(status_code=422, detail=message)
                plans.append(plan)
            batch = self.store.create_batch(request.urls, options, plans)
            for job, raw_url in zip(batch["items"], request.urls):
                self.transient_urls[job["id"]] = raw_url
                self.queue.put_nowait(job["id"])
            return self.decorate_batch(batch)

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                job = self.store.claim_queued(job_id)
                if job is not None:
                    await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                current = self.store.get_job(job_id)
                if current and current["status"] not in TERMINAL and current["status"] != "pending_index":
                    message = _exception_message(exc)
                    if isinstance(exc, ImportFailed):
                        code, source, retryable, task_id = (
                            exc.code, exc.source, exc.retryable, exc.task_id,
                        )
                    else:
                        quality_failure = ("quality gate" in message.casefold() or
                                           "transcriptquality" in type(exc).__name__.casefold())
                        code = "transcript_quality" if quality_failure else "import_failed"
                        source, retryable, task_id = "import_service", True, None
                    self.store.update(
                        job_id, status="failed", stage="failed", message="Import failed",
                        error={"code": code, "message": message, "source": source,
                               "retryable": retryable, "task_id": task_id},
                        completed_at=now(), event="failed",
                    )
                    with self.warning_lock:
                        self.event_warnings.pop(job_id, None)
            finally:
                self.queue.task_done()

    async def _execute(self, job: dict[str, Any]) -> None:
        assert self.coordinator is not None
        request = self.store.request_for(job["id"])
        if request.pop("reindex_only", False):
            await self._index(job["id"])
            return
        def on_event(event: str, payload: dict[str, Any]) -> None:
            current = self.store.get_job(job["id"])
            if current is None or current["status"] != "running":
                return
            stage = payload.get("stage") if isinstance(payload.get("stage"), str) else current["stage"]
            progress = _progress(payload)
            changes: dict[str, Any] = {"stage": stage, "message": _stage_message(stage)}
            if progress is not None:
                changes["progress"] = progress
            if isinstance(payload.get("quality"), dict):
                changes["quality"] = payload["quality"]
            warnings = _warning_values(payload)
            if warnings:
                with self.warning_lock:
                    target = self.event_warnings.setdefault(job["id"], [])
                    target.extend(value for value in warnings if value not in target)
            self.store.update(job["id"], event=event, payload=payload, **changes)

        kwargs = {key: request[key] for key in (
            "mode", "audio", "audio_format", "speed_factor", "timeout", "asr_backend",
            "local_timestamp_mode", "local_diarization", "local_speaker_count"
        )}
        kwargs["mode"] = job["effective_mode"]
        kwargs["asr_backend"] = job["asr_backend"] or "auto"
        processing_url = self.transient_urls.get(job["id"], request["url"])
        task = await self.coordinator.process_url(processing_url, on_event=on_event, rescan=False, **kwargs)
        out_dir, item_path, item_id, manifest_warnings = self._public_item(task.get("out_dir"))
        changes: dict[str, Any] = {}
        if isinstance(task.get("quality"), dict):
            changes["quality"] = task["quality"]
        safe_task = dict(task)
        safe_task.pop("out_dir", None)
        safe_task["item_path"] = item_path
        with self.warning_lock:
            event_warnings = self.event_warnings.pop(job["id"], [])
        warnings = _merge_warnings(manifest_warnings, event_warnings, _warning_values(task))
        self.store.update(
            job["id"], status="pending_index", stage="pending_index",
            message="Content ready; waiting for library index", progress=100,
            item_id=item_id, item_path=item_path, warning=_warning_text(warnings),
            result=safe_task, error=None, event="content_ready", **changes,
        )
        self.transient_urls.pop(job["id"], None)
        duplicate = await asyncio.to_thread(self._find_duplicate, job["id"], out_dir)
        if duplicate:
            duplicate_warning = "Possible duplicate content preserved"
            warnings = _merge_warnings(warnings, [duplicate_warning])
            self.store.update(job["id"], duplicate=duplicate, warning=_warning_text(warnings),
                              event="duplicate_detected", payload=duplicate)
        await self._index(job["id"])

    async def _index(self, job_id: str) -> None:
        assert self.coordinator is not None
        self.store.update(job_id, status="pending_index", stage="indexing", message="Refreshing Echo library", event="indexing")
        try:
            result = await self.coordinator.rescan()
        except Exception as exc:
            self.indexing_revision += 1
            self.indexing = {"available": False, "reason": sanitize_text(_exception_message(exc))}
            self.store.update(
                job_id, status="pending_index", stage="pending_index", message="Content ready; indexing failed",
                error={"code": "index_failed", "message": _exception_message(exc),
                       "retryable": True, "source": "import_service", "task_id": None},
                event="index_failed",
            )
            return
        self.indexing_revision += 1
        self.indexing = {"available": True, "reason": None}
        self.store.update(
            job_id, status="succeeded", stage="complete", message="Import complete", progress=100,
            error=None, completed_at=now(), event="succeeded", payload={"rescan": result},
        )

    def _public_item(self, value: Any) -> tuple[Path | None, str | None, str | None, list[str]]:
        if not isinstance(value, str) or not value:
            return None, None, None, []
        try:
            out_dir = Path(value).resolve()
            out_dir.relative_to(self.content_root)
        except (OSError, ValueError):
            return None, None, None, []
        if out_dir == self.content_root:
            return None, None, None, []
        relative = out_dir.relative_to(self.content_root)
        item_path = relative.as_posix()
        try:
            manifest_path = out_dir / "manifest.json"
            if manifest_path.stat().st_size > 1_000_000:
                raise ValueError("manifest is too large")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            item_id = manifest.get("id") if isinstance(manifest, dict) else None
        except (OSError, ValueError, UnicodeError):
            manifest, item_id = {}, None
        warnings = _warning_values(manifest) if isinstance(manifest, dict) else []
        safe_id = item_id if isinstance(item_id, str) and 0 < len(item_id) <= 512 else None
        return out_dir, item_path, safe_id, warnings

    def _find_duplicate(self, job_id: str, out_dir: Path | None) -> dict[str, Any] | None:
        if out_dir is None:
            return None
        current_hash, current_text = _content_fingerprint(out_dir)
        for other in self.store.completed_for_duplicates(job_id):
            item_path = other["item_path"]
            if not item_path:
                continue
            other_dir = self.content_root / Path(*PurePosixPath(item_path).parts)
            try:
                other_dir = other_dir.resolve()
                other_dir.relative_to(self.content_root)
            except (OSError, ValueError):
                continue
            media_hash, text = _content_fingerprint(other_dir)
            if current_hash and media_hash == current_hash:
                return {"job_id": other["id"], "item_id": other["item_id"], "score": 1.0, "kind": "exact"}
            if current_text and text:
                score = round(difflib.SequenceMatcher(None, current_text, text, autojunk=False).ratio(), 3)
                if score >= 0.9:
                    return {"job_id": other["id"], "item_id": other["item_id"], "score": score, "kind": "suspected"}
        return None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get_job(job_id)
        return self.decorate(job) if job else None

    def decorate(self, job: dict[str, Any]) -> dict[str, Any]:
        job = dict(job)
        if job["status"] == "queued":
            queued = self.store.queued_ids()
            job["queue_position"] = queued.index(job["id"]) + 1 if job["id"] in queued else None
            sample = self.store.median_duration(job["effective_mode"], job["asr_backend"])
            if sample is not None:
                ahead = self.store.active_ahead(job["id"])
                capacity = 1 if job["effective_mode"] == "transcribe" and job["asr_backend"] == "local" else 3
                job["eta_seconds"] = max(1, math.ceil(sample * math.ceil((ahead + 1) / capacity)))
        return job

    def decorate_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        batch = dict(batch); batch["items"] = [self.decorate(item) for item in batch["items"]]
        return batch

    async def retry(self, job: dict[str, Any], *, force_reindex: bool = False) -> dict[str, Any]:
        async with self.state_lock:
            retry = self.store.retry_job(job["id"], reindex=force_reindex)
            if retry is None:
                raise HTTPException(409, "Job state changed; action was not applied")
            raw_url = self.transient_urls.get(job["id"])
            if raw_url is not None:
                self.transient_urls[retry["id"]] = raw_url
            self.queue.put_nowait(retry["id"])
            return self.decorate(retry)

    async def retry_failed(self, batch_id: str) -> dict[str, Any]:
        async with self.state_lock:
            jobs, exists = self.store.retry_failed_batch(batch_id)
            if not exists:
                raise HTTPException(404, "Import batch not found")
            for job in jobs:
                self.queue.put_nowait(job["id"])
            batch = self.store.get_batch(batch_id)
            assert batch is not None
            return self.decorate_batch(batch)

    async def switch_library(self, path: str) -> dict[str, Any]:
        try:
            candidate = self.paths.validate_library_root(path)
        except ValueError as exc:
            raise HTTPException(422, {
                "code": "invalid_library", "message": str(exc),
            }) from exc
        async with self.state_lock:
            if self.store.active_count():
                raise HTTPException(409, {"code": "imports_active",
                                          "message": "Wait for active imports before switching libraries"})
            if self.coordinator_factory is None or self.coordinator is None:
                raise HTTPException(503, "Import service is not ready")
            if candidate == self.content_root:
                counts = await self.coordinator.rescan()
                return {"library_root": str(candidate), **counts}
            old_paths, old_coordinator = self.paths, self.coordinator
            new_paths = old_paths.with_library(candidate)
            new_coordinator = self.coordinator_factory(new_paths)
            echo_attempted = False
            try:
                await new_coordinator.initialize()
                echo_attempted = True
                result = await new_coordinator.switch_library(candidate)
                new_paths.persist()
            except Exception as exc:
                if echo_attempted:
                    try:
                        await old_coordinator.switch_library(old_paths.library_root)
                    except Exception as rollback_exc:
                        await new_coordinator.close()
                        raise HTTPException(500, {
                            "code": "library_switch_rollback_failed",
                            "message": _exception_message(rollback_exc),
                        }) from exc
                await new_coordinator.close()
                raise HTTPException(502, {"code": "library_switch_failed",
                                          "message": _exception_message(exc)}) from exc
            self.paths = new_paths
            self.content_root = candidate
            self.store.set_content_root(candidate)
            self.coordinator = new_coordinator
            try:
                await old_coordinator.close()
            except Exception:
                logging.getLogger(__name__).exception("Old import coordinator failed to close after library switch")
            self.indexing_revision += 1
            self.indexing = {"available": True, "reason": None}
            return result

    async def _probe_indexing(self) -> None:
        assert self.coordinator is not None
        revision = self.indexing_revision
        try:
            await self.coordinator.index_health()
        except Exception as exc:
            result = {"available": False, "reason": sanitize_text(_exception_message(exc))}
        else:
            result = {"available": True, "reason": None}
        if self.indexing_revision == revision:
            self.indexing = result


def _stage_message(stage: str) -> str:
    return {
        "acquiring": "Acquiring content", "downloading": "Downloading content",
        "waiting_for_asr": "Waiting for ASR capacity",
        "transcribing": "Transcribing audio", "quality_check": "Checking transcript quality",
        "publishing": "Publishing library item",
    }.get(stage, stage.replace("_", " ").capitalize())


def _warning_values(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    raw = value.get("warnings", value.get("warning"))
    candidates = raw if isinstance(raw, list) else [raw]
    return [sanitize_text(item) for item in candidates if isinstance(item, str) and item.strip()]


def _merge_warnings(*groups: list[str]) -> list[str]:
    result: list[str] = []
    for group in groups:
        for warning in group:
            clean = sanitize_text(warning).strip()
            if clean and clean not in result:
                result.append(clean)
    return result


def _warning_text(warnings: list[str]) -> str | None:
    return " | ".join(warnings) if warnings else None


def _exception_message(exc: BaseException) -> str:
    """Render and sanitize an exception chain without leaking signed URLs."""
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current) or type(current).__name__)
        current = current.__cause__ or current.__context__
    return sanitize_text("; caused by: ".join(messages))


def _loopback_authority(value: str) -> tuple[str, int] | None:
    try:
        parsed = urlsplit(f"http://{value}")
        host = parsed.hostname
        if not host or parsed.username or parsed.password or parsed.port is None:
            return None
        if host.casefold() != "localhost" and not ipaddress.ip_address(host).is_loopback:
            return None
        return host.casefold(), parsed.port
    except (ValueError, UnicodeError):
        return None


def _same_loopback_origin(origin: str, authority: tuple[str, int]) -> bool:
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname
        if (parsed.scheme != "http" or not host or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            return False
        if host.casefold() != "localhost" and not ipaddress.ip_address(host).is_loopback:
            return False
        return (host.casefold(), parsed.port or 80) == authority
    except (ValueError, UnicodeError):
        return False


def _progress(payload: dict[str, Any]) -> float | None:
    raw = payload.get("progress")
    if isinstance(raw, (int, float)):
        return round(max(0.0, min(100.0, float(raw))), 1)
    downloaded, total = payload.get("downloaded_bytes"), payload.get("total_bytes") or payload.get("total_bytes_estimate")
    if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return round(max(0.0, min(100.0, downloaded / total * 100)), 1)
    return None


def _content_fingerprint(directory: Path) -> tuple[str | None, str | None]:
    media = next((p for p in sorted(directory.rglob("*")) if p.is_file() and p.suffix.casefold() in {".mp3", ".m4a", ".aac", ".flac", ".wav", ".opus", ".ogg", ".mp4", ".webm", ".mkv"}), None)
    media_hash = None
    if media:
        digest = hashlib.sha256()
        with media.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        media_hash = digest.hexdigest()
    transcript_json = directory / "transcript.json"
    if transcript_json.is_file():
        try:
            payload = json.loads(transcript_json.read_text(encoding="utf-8"))
            segments = payload.get("segments") if isinstance(payload, dict) else None
            if isinstance(segments, list):
                combined = " ".join(
                    segment["text"] for segment in segments
                    if isinstance(segment, dict) and isinstance(segment.get("text"), str)
                )
                normalized = re.sub(r"\s+", " ", combined).strip().casefold()
                if normalized:
                    return media_hash, normalized
        except (OSError, UnicodeError, ValueError):
            pass
    transcript = next((p for name in ("transcript.txt", "transcript.md", "content.md") if (p := directory / name).is_file()), None)
    text = None
    if transcript:
        try:
            text = re.sub(r"\s+", " ", transcript.read_text(encoding="utf-8")).strip().casefold()
        except (OSError, UnicodeError):
            pass
    return media_hash, text


def _create_default_bridge() -> Any:
    from medialab.bridge import SessionBridgeServer
    return SessionBridgeServer()


def create_app(*, library_root: str | Path | None = None,
               runtime_root: str | Path | None = None,
               state_root: str | Path | None = None,
               cache_root: str | Path | None = None,
               logs_root: str | Path | None = None,
               tmp_root: str | Path | None = None,
               config_root: str | Path | None = None,
               install_mode: bool | None = None,
               echo_url: str = "http://127.0.0.1:3000",
               internal_token: str | None = None,
               shutdown_callback: Callable[[], None] | None = None,
               coordinator_factory: Callable[[AppPaths], Any] | None = None,
               bridge: Any = _DEFAULT_BRIDGE) -> FastAPI:
    paths = AppPaths.resolve(
        library_root=library_root, runtime_root=runtime_root, state_root=state_root,
        cache_root=cache_root, logs_root=logs_root, tmp_root=tmp_root,
        config_root=config_root, install_mode=install_mode,
    )
    service_internal_token = internal_token or os.environ.get("ECHO_INTERNAL_TOKEN")
    if paths.install_mode and not service_internal_token:
        raise ValueError("Installed import service requires an internal token")
    imports = ImportService(paths)
    enrichment = EnrichmentService(imports)
    durations: deque[dict[str, Any]] = deque(maxlen=200)
    logger = logging.getLogger(f"echo_reader.imports.{id(imports)}")
    logger.setLevel(logging.INFO); logger.propagate = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        paths.ensure_directories()
        handler = RotatingFileHandler(paths.logs_root / "imports.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        logger.addHandler(handler)
        active_bridge = _create_default_bridge() if bridge is _DEFAULT_BRIDGE else bridge
        started = False
        try:
            if active_bridge is not None:
                active_bridge.start(); started = True
                from medialab.session import CredentialBroker
                broker = CredentialBroker(active_bridge)
            else:
                broker = None
            app.state.credential_broker = broker
            def build_coordinator(current: AppPaths) -> Any:
                if coordinator_factory is not None:
                    return coordinator_factory(current)
                return ImportCoordinator(
                    current.library_root, current.state_root, echo_url,
                    bridge=active_bridge, credential_broker=broker,
                    cache_root=current.cache_root, tmp_root=current.tmp_root,
                    config_root=current.config_root,
                    internal_token=service_internal_token,
                )
            await imports.start(build_coordinator)
            yield
        finally:
            try:
                await enrichment.close()
                await imports.close()
            finally:
                if started:
                    active_bridge.stop()
                logger.removeHandler(handler); handler.close()

    app = FastAPI(title="Echo Reader Import Service", version="1", lifespan=lifespan)
    app.state.imports = imports
    app.state.enrichment = enrichment
    app.state.credential_broker = None

    @app.middleware("http")
    async def loopback_browser_boundary(request: Request, call_next: Callable[..., Any]):
        # Applies in every mode: a loopback listener alone does not stop DNS rebinding
        # (attacker-controlled Host) or cross-site form/fetch POSTs from a browser.
        authority = _loopback_authority(request.headers.get("host", ""))
        if authority is None:
            return JSONResponse(status_code=421, content={"detail": "Host must be a loopback address"})
        if request.method not in {"GET", "HEAD"}:
            if request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
                return JSONResponse(
                    status_code=403, content={"detail": "Cross-site requests are not allowed"}
                )
            origin = request.headers.get("origin")
            if origin is not None and not _same_loopback_origin(origin, authority):
                return JSONResponse(
                    status_code=403, content={"detail": "Origin does not match Host"}
                )
        if imports.paths.install_mode:
            provided_token = request.headers.get("x-echo-internal-token", "")
            if not secrets.compare_digest(provided_token, service_internal_token or ""):
                return JSONResponse(
                    status_code=401, content={"detail": "Invalid internal token"}
                )
        return await call_next(request)

    @app.middleware("http")
    async def duration_middleware(request: Request, call_next: Callable[..., Any]):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        entry = {"method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": elapsed, "at": now()}
        durations.append(entry); logger.info(json.dumps(entry))
        return response

    @app.get("/api/v1/health")
    async def health(): return {"status": "ok", "content_root": str(imports.content_root)}

    @app.get("/api/v1/app-config")
    async def app_config():
        return imports.paths.public_dict()

    @app.post("/api/v1/app/shutdown", status_code=202)
    async def shutdown(background_tasks: BackgroundTasks):
        if not imports.paths.install_mode or shutdown_callback is None:
            raise HTTPException(404, "Application shutdown is available only in the installed app")
        async with imports.state_lock:
            if imports.store.active_count():
                raise HTTPException(409, {
                    "code": "imports_active",
                    "message": "Wait for active imports before closing Echo Reader",
                })
            imports.accepting = False
        async def notify_host() -> None:
            shutdown_callback()

        background_tasks.add_task(notify_host)
        return {"status": "shutting_down"}

    @app.post("/api/v1/library/switch")
    async def switch_library(request: LibrarySwitchRequest):
        return await imports.switch_library(request.path)

    @app.get("/api/v1/import-capabilities")
    async def capabilities():
        assert imports.coordinator is not None
        raw = imports.coordinator.capabilities()
        transcription = raw.get("transcription", {})
        backends = {}
        for name in ("online", "local"):
            value = transcription.get(name, {})
            available = bool(value.get("available"))
            blocker = value.get("blocker")
            download_required = blocker == "download_required"
            backends[name] = {
                "available": available,
                "readiness": "download_required" if download_required else "ready" if available else "unavailable",
                "reason": "Model download required on first use" if download_required else str(blocker) if blocker else None,
            }
            if name == "local" and value.get("engine"):
                backends[name]["engine"] = str(value["engine"])
        recommended = "online" if backends["online"]["available"] else "local" if backends["local"]["available"] else None
        return {"status": "ready", "content_root": str(imports.content_root), "batch_max_urls": 50,
                "recommended_backend": recommended, "backends": backends,
                "indexing": dict(imports.indexing),
                "features": await asyncio.to_thread(enrichment.features)}

    @app.get("/api/v1/features")
    async def features():
        return await asyncio.to_thread(enrichment.features)

    @app.post("/api/v1/items/{item_id}/tts", status_code=202)
    async def create_tts(item_id: str, request: TtsRequest | None = None):
        options = request.model_dump(exclude_none=True) if request else {}
        return await enrichment.start("tts", item_id, options)

    @app.post("/api/v1/items/{item_id}/ocr", status_code=202)
    async def create_ocr(item_id: str):
        return await enrichment.start("ocr", item_id, {})

    @app.get("/api/v1/enrichment-jobs/{job_id}")
    async def get_enrichment_job(job_id: str):
        return enrichment.get_job(job_id)

    @app.post("/api/v1/import-batches", status_code=202)
    async def create_batch(request: BatchRequest): return await imports.create_batch(request)

    @app.get("/api/v1/import-batches/{batch_id}")
    async def get_batch(batch_id: str):
        batch = imports.store.get_batch(batch_id)
        if batch is None: raise HTTPException(404, "Import batch not found")
        return imports.decorate_batch(batch)

    @app.get("/api/v1/import-jobs")
    async def list_jobs(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                        status: str | None = None, batch_id: str | None = None):
        if status is not None and status not in STATUSES: raise HTTPException(422, "Invalid status")
        result = imports.store.list_jobs(page=page, page_size=page_size, status=status, batch_id=batch_id)
        result["items"] = [imports.decorate(item) for item in result["items"]]
        return result

    def require_job(job_id: str) -> dict[str, Any]:
        job = imports.get_job(job_id)
        if job is None: raise HTTPException(404, "Import job not found")
        return job

    @app.get("/api/v1/import-jobs/{job_id}")
    async def get_job(job_id: str): return require_job(job_id)

    @app.get("/api/v1/import-jobs/{job_id}/timeline")
    async def timeline(job_id: str):
        require_job(job_id); return {"items": imports.store.timeline(job_id)}

    @app.post("/api/v1/import-jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        require_job(job_id)
        cancelled = imports.store.cancel_queued(job_id)
        if cancelled is None: raise HTTPException(409, "Only queued jobs can be cancelled")
        imports.transient_urls.pop(job_id, None)
        return imports.decorate(cancelled)

    @app.post("/api/v1/import-jobs/{job_id}/retry", status_code=202)
    async def retry(job_id: str):
        job = require_job(job_id)
        if job["status"] not in {"failed", "interrupted"}: raise HTTPException(409, "Job cannot be retried")
        return await imports.retry(job)

    @app.post("/api/v1/import-jobs/{job_id}/reindex", status_code=202)
    async def reindex(job_id: str):
        job = require_job(job_id)
        if job["status"] != "pending_index" or not job["item_path"]:
            raise HTTPException(409, "Only content awaiting index can be reindexed")
        return await imports.retry(job, force_reindex=True)

    @app.post("/api/v1/import-batches/{batch_id}/retry-failed", status_code=202)
    async def retry_failed(batch_id: str):
        return await imports.retry_failed(batch_id)

    @app.get("/api/v1/credential-health")
    async def credential_health(url: str | None = None):
        """Offline health of cached browser-session credentials; values are never returned."""
        from medialab.cookie_health import check_cookie_health
        from medialab.platforms import detect_platform

        broker = getattr(app.state, "credential_broker", None)
        report = broker.health_report() if broker is not None else []
        body: dict[str, Any] = {"bridge_enabled": broker is not None, "platforms": report}
        if url:
            platform = detect_platform(url)
            cached = [entry for entry in report if entry["platform"] == platform and entry["cached"]]
            body["target"] = cached[0] if cached else {
                **check_cookie_health(platform, None), "domains": [], "cached": False,
            }
        return body

    @app.get("/api/v1/import-diagnostics")
    async def diagnostics():
        recent = list(durations)
        return {"database": str(imports.store.path), "queue_depth": imports.queue.qsize(),
                "workers": len(imports.workers), "requests": recent,
                "average_duration_ms": round(sum(x["duration_ms"] for x in recent) / len(recent), 2) if recent else 0}
    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="echo-reader-service")
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=3100)
    parser.add_argument("--library-root", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--logs-root", type=Path)
    parser.add_argument("--tmp-root", type=Path)
    parser.add_argument("--config-root", type=Path)
    parser.add_argument("--install-mode", action="store_true", default=None)
    parser.add_argument("--echo-url", default="http://127.0.0.1:3000")
    parser.add_argument("--internal-token", default=os.environ.get("ECHO_INTERNAL_TOKEN"))
    return parser


def _is_loopback_bind(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not _is_loopback_bind(args.host):
        # The service is a single-user local control plane; development mode has no
        # network authentication, so it must never listen on a routable interface.
        parser.error("--host must be a loopback address (127.0.0.1, ::1 or localhost)")
    uvicorn.run(create_app(
        library_root=args.library_root, runtime_root=args.runtime_root,
        state_root=args.state_root, cache_root=args.cache_root, logs_root=args.logs_root,
        tmp_root=args.tmp_root, config_root=args.config_root, install_mode=args.install_mode,
        echo_url=args.echo_url, internal_token=args.internal_token,
    ), host=args.host, port=args.port)
    return 0
