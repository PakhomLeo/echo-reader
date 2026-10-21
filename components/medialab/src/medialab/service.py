"""DownloadService：进程内异步下载服务（v1 核心）。

用法：
    svc = DownloadService(config=None, on_event=cb)
    await svc.initialize()
    task = await svc.download("https://...", audio="mp3")
    status = await svc.query(task.id)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import multiprocessing
import shutil
import re
import threading
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import downloader
from . import events as ev
from . import platforms
from . import xhs_video
from .article import ArticleExtractor, ScrapeError
from .content_probe import MediaCandidate, ProbeResult, probe_html
from .content_urls import UrlContext, sanitize_persisted_url
from .site_adapters import Article, article_platform
from .asr import AsrPipeline, QwenFiletransProvider
from .audio import AudioProcessor
from .local_asr import (WHISPER_MODEL_ID, WHISPER_REVISION,
                        TranscriptQualityError, local_model_readiness,
                        transcript_quality_metrics)
from .config import load_config, resolve_dir
from .oss import OssClient, OssConfig
from .publishing import (MediaEntry, derive_id, detect_language, pick_task_dir,
                         publish_media, write_manifest, write_transcript)
from .refine import ChatRefiner, build_refiner, is_chinese_content
from .browser_http import BrowserHttpClient, BrowserHttpError
from .session import (CredentialBroker, ExtensionSessionProvider, FailureCode,
                      SessionProvider, classify_error)
from .store import Store, STATUS_DONE, STATUS_FAILED, STATUS_RUNNING
from .xhs_client import XhsClient

log = logging.getLogger("medialab.service")

AUDIO_CODECS = ("mp3", "m4a", "aac", "flac", "wav", "opus", "ogg")
AUDIO_SUFFIXES = (".mp3", ".m4a", ".aac", ".flac", ".wav", ".opus", ".ogg")


_LOCAL_WORKER_PIPELINE = None


@lru_cache(maxsize=1)
def _probe_local_runtime() -> tuple[tuple[str, ...], bool, str | None]:
    """Probe process-stable Python dependencies and OpenVINO GPU availability once."""
    missing = tuple(
        name for name in ("numpy", "openvino", "openvino_genai", "sherpa_onnx")
        if importlib.util.find_spec(name) is None
    )
    if missing:
        return missing, False, None
    try:
        import openvino as ov
        devices = list(ov.Core().available_devices)
        gpu_ready = any(device == "GPU" or device.startswith("GPU.") for device in devices)
        return missing, gpu_ready, None
    except Exception as exc:  # noqa: BLE001 - capability must remain serializable
        return missing, False, str(exc)


def local_asr_engine(cfg: dict) -> str:
    """Return the configured local ASR engine name."""
    engine = str((cfg.get("local_asr") or {}).get("engine") or "openvino_whisper").strip().casefold()
    if engine not in {"openvino_whisper", "funasr_nano"}:
        raise ValueError("local_asr.engine must be one of: openvino_whisper, funasr_nano")
    return engine


def _local_pipeline(cfg: dict, ffmpeg_path: str):
    """Create (once per worker process) the pipeline for the configured engine."""
    global _LOCAL_WORKER_PIPELINE
    if _LOCAL_WORKER_PIPELINE is None:
        if local_asr_engine(cfg) == "funasr_nano":
            from .funasr_asr import FunAsrNanoPipeline

            _LOCAL_WORKER_PIPELINE = FunAsrNanoPipeline(cfg, AudioProcessor(ffmpeg_path))
        else:
            from .local_asr import LocalAsrPipeline

            _LOCAL_WORKER_PIPELINE = LocalAsrPipeline(cfg, AudioProcessor(ffmpeg_path))
    return _LOCAL_WORKER_PIPELINE


def _run_local_asr_worker(cfg: dict, ffmpeg_path: str, audio_path: str, workdir: str,
                          timestamp_mode: str, diarization_enabled: bool,
                          speaker_count: int) -> dict:
    """Process entry point; all OpenVINO/sherpa/FunASR objects stay in this process."""
    pipeline = _local_pipeline(cfg, ffmpeg_path)
    segments, duration = pipeline.infer(
        Path(audio_path), Path(workdir), timestamp_mode=timestamp_mode,
        diarization_enabled=diarization_enabled, speaker_count=speaker_count,
    )
    return {"segments": [segment.to_dict() for segment in segments], "audio_duration": duration}


def _run_local_diarization_worker(cfg: dict, ffmpeg_path: str, workdir: str,
                                  segments: list[dict], speaker_count: int) -> list[dict]:
    """Diarize only the quality-accepted transcript in the owning worker process."""
    pipeline = _local_pipeline(cfg, ffmpeg_path)
    return [
        segment.to_dict()
        for segment in pipeline.diarize_accepted(
            Path(workdir), segments, speaker_count,
        )
    ]


@dataclass
class TaskHandle:
    id: str

    async def wait(self, svc: "DownloadService", timeout: float | None = None) -> dict:
        return await svc.wait(self.id, timeout=timeout)


class DownloadService:
    def __init__(self, config: dict | None = None, on_event=None, db_path: str | Path | None = None,
                 session_provider: SessionProvider | None = None, bridge=None,
                 credential_broker: CredentialBroker | None = None,
                 config_path: str | Path | None = None,
                 secrets_path: str | Path | None = None):
        self._cfg = load_config(
            config_path=config_path, secrets_path=secrets_path, overrides=config
        )
        self._emitter = ev.Emitter(on_event)
        self._db_path = Path(db_path) if db_path else resolve_dir(self._cfg["paths"], "db")
        self._session_provider = session_provider
        self._bridge = bridge
        self._store: Store | None = None
        self._audio: AudioProcessor | None = None
        self._oss: OssClient | None = None
        self._refiner: ChatRefiner | None = None
        self._download_sem: asyncio.Semaphore | None = None
        self._scrape_sem: asyncio.Semaphore | None = None
        self._asr_sem: asyncio.Semaphore | None = None
        self._local_asr_sem: asyncio.Semaphore | None = None
        self._local_asr_executor: ProcessPoolExecutor | None = None
        self._refine_sem: asyncio.Semaphore | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._closing = False
        self._close_task: asyncio.Task | None = None
        session_bridge = bridge
        if session_bridge is None and session_provider is not None and hasattr(session_provider, "bridge"):
            session_bridge = session_provider.bridge
        self._credentials_available = credential_broker is not None or session_bridge is not None
        provider_broker = getattr(session_provider, "credential_broker", None)
        self._credentials = (
            credential_broker
            if credential_broker is not None
            else provider_broker
            if provider_broker is not None
            else CredentialBroker(session_bridge)
        )
        if self._session_provider is None and (
            credential_broker is not None or session_bridge is not None
        ):
            self._session_provider = ExtensionSessionProvider(
                session_bridge,
                cfg=self._cfg,
                credential_broker=self._credentials,
            )

    async def initialize(self) -> dict:
        self._ensure_lifecycle_state()
        async with self._lifecycle_lock:
            self._ensure_open()
            return self._initialize_unlocked()

    def _initialize_unlocked(self) -> dict:
        """Initialize once while the caller holds ``_lifecycle_lock``."""
        self._ensure_open()
        if getattr(self, "_store", None) is not None:
            return self._initialize_result()
        self._emitter.bind_loop(asyncio.get_running_loop())
        self._store = Store(self._db_path)
        self._store.mark_interrupted()  # 上次进程遗留的 queued/running 任务标记为 interrupted
        # 缺 ffmpeg 时 _audio 明确为 None（而不是缺失属性），下游统一按“转音频不可用”处理。
        self._audio = None
        try:
            self._audio = AudioProcessor(self._cfg["audio"].get("ffmpeg_path", ""))
        except RuntimeError as e:
            log.warning("%s（转音频功能不可用）", e)
        oss_conf = OssConfig.from_config(self._cfg)
        if oss_conf:
            try:
                self._oss = OssClient(oss_conf)
                log.info("OSS 已配置: bucket=%s", oss_conf.bucket)
            except Exception as e:  # noqa: BLE001
                log.warning("OSS 初始化失败: %s", e)
        else:
            log.warning("OSS 未配置（secrets.toml [oss] 为空），转写功能不可用")
        self._refiner, refine_reason = build_refiner(self._cfg)
        if self._refiner is not None:
            log.info("refine 已启用: provider=%s model=%s",
                     self._refiner.provider, self._refiner.model)
        elif refine_reason != "disabled":
            log.warning("refine 未启用：%s（见 [refine].provider / secrets.toml）", refine_reason)
        maxc = int(self._cfg["concurrency"].get("max_concurrent", 3))
        self._download_sem = asyncio.Semaphore(maxc)
        self._scrape_sem = asyncio.Semaphore(maxc)
        self._asr_sem = asyncio.Semaphore(maxc)
        self._local_asr_sem = asyncio.Semaphore(1)
        self._refine_sem = asyncio.Semaphore(maxc)
        return self._initialize_result()

    def _initialize_result(self) -> dict:
        return {
            "db": str(self._db_path),
            "ffmpeg": getattr(getattr(self, "_audio", None), "ffmpeg_path", None),
        }

    def _ensure_lifecycle_state(self) -> None:
        if not hasattr(self, "_lifecycle_lock"):
            self._lifecycle_lock = asyncio.Lock()
            self._closing = False
            self._close_task = None

    @property
    def store(self) -> Store:
        if self._store is None:
            raise RuntimeError("请先 await initialize()")
        return self._store

    @property
    def config(self) -> dict:
        return self._cfg

    # ---------- 公共 API ----------

    def capabilities(self) -> dict:
        """Return serializable readiness for transcription backends."""
        asr_cfg = self._cfg.get("asr", {})
        audio_ready = self._audio is not None
        online_ready = audio_ready and bool(asr_cfg.get("api_key")) and self._oss is not None
        online_blocker = (
            None if online_ready else "ffmpeg_unavailable" if not audio_ready else "unavailable"
        )
        local_cfg = self._cfg.get("local_asr", {})
        try:
            engine = local_asr_engine(self._cfg)
        except ValueError:
            engine = "invalid"
        gpu_error = None
        model_reason = None
        if engine == "funasr_nano":
            from .funasr_asr import probe_funasr_runtime

            missing = list(probe_funasr_runtime())
            # FunASR manages its own model cache and runs on CPU when no accelerator exists.
            local_selectable = audio_ready and not missing
            local_blocker = (
                "ffmpeg_unavailable" if not audio_ready
                else "dependencies_unavailable" if missing
                else None
            )
            model_reason = "managed_by_funasr"
        elif engine == "invalid":
            missing = []
            local_selectable = False
            local_blocker = "invalid_engine"
        else:
            missing_tuple, gpu_ready, gpu_error = _probe_local_runtime()
            missing = list(missing_tuple)
            model_root = Path(local_cfg.get("model_dir", ".runtime/models/local-asr"))
            if not model_root.is_absolute():
                model_root = Path.cwd() / model_root
            model_ready, model_reason = local_model_readiness(
                model_root,
                model_id=str(local_cfg.get("model_id", WHISPER_MODEL_ID)),
                revision=str(local_cfg.get("revision", WHISPER_REVISION)),
            )
            auto_download = bool(local_cfg.get("auto_download", True))
            local_selectable = (
                audio_ready and not missing and gpu_ready
                and bool(auto_download or model_ready)
            )
            local_blocker = (
                "ffmpeg_unavailable" if not audio_ready
                else "dependencies_unavailable" if missing
                else "gpu_unavailable" if not gpu_ready
                else "download_required" if not model_ready and auto_download
                else model_reason if not model_ready
                else None
            )
        return {
            "transcription": {
                "online": {
                    "available": online_ready,
                    "blocker": online_blocker,
                },
                "local": {
                    "available": local_selectable,
                    "blocker": local_blocker,
                    "engine": engine,
                    "missing_dependencies": missing,
                    "runtime_error": gpu_error,
                    "model_readiness": model_reason,
                },
            }
        }

    def plan_import(self, url: str, *, mode: str = "auto", asr_backend: str = "auto") -> dict:
        """Resolve import intent without creating a task or performing I/O."""
        if mode not in {"auto", "article", "download", "transcribe"}:
            raise ValueError("mode must be one of: auto, article, download, transcribe")
        if asr_backend not in {"auto", "online", "local"}:
            raise ValueError("asr_backend must be one of: auto, online, local")
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        host = parts.netloc.casefold().split(":", 1)[0]
        path = parts.path.casefold()
        clear_video = (
            (host.endswith("douyin.com") and "/video/" in path)
            or (host.endswith("bilibili.com") and "/video/" in path)
            or host in {"youtu.be", "www.youtu.be"}
            or (host.endswith("youtube.com") and path == "/watch")
            or (host.endswith("tiktok.com") and "/video/" in path)
            or (host.endswith("vimeo.com") and path.strip("/").isdigit())
            or (host.endswith("instagram.com") and any(token in path for token in ("/reel/", "/reels/")))
            or (host.endswith("twitter.com") or host.endswith("x.com")) and "/status/" in path
        )
        resolved_mode = "transcribe" if mode == "auto" and clear_video else mode
        backend = None
        blocker = None
        if resolved_mode == "transcribe":
            caps = self.capabilities()["transcription"]
            if asr_backend == "auto":
                backend = "online" if caps["online"]["available"] else (
                    "local" if caps["local"]["available"] else None
                )
            else:
                backend = asr_backend if caps[asr_backend]["available"] else None
            if backend is None:
                requested = asr_backend if asr_backend != "auto" else "local"
                blocker = caps[requested]["blocker"] or "unavailable"
        return {
            "requested_mode": mode,
            "mode": resolved_mode,
            "requested_asr_backend": asr_backend,
            "asr_backend": backend,
            "blocker": blocker,
            "unambiguous_video": clear_video,
        }

    async def download(
        self,
        url: str,
        format: str | None = None,
        audio: str | None = None,
        out_dir: str | Path | None = None,
        wait: bool = False,
    ) -> TaskHandle:
        """提交下载任务。audio 指定转音频格式（mp3/m4a/...）；wait=True 则等待完成。"""
        if audio and audio not in AUDIO_CODECS:
            raise ValueError(f"不支持的音频格式: {audio}，可选 {AUDIO_CODECS}")
        async with self._lifecycle_lock:
            self._ensure_open()
            if self._download_sem is None:
                self._initialize_unlocked()
            platform = platforms.detect_platform(url)
            task_id = self.store.create_task(
                sanitize_persisted_url(url), platform, {"format": format, "audio": audio},
            )
            handle = TaskHandle(id=task_id)

            async def runner():
                try:
                    async with self._download_sem:
                        await asyncio.to_thread(self._run_download, task_id, url, platform, format, audio, out_dir)
                finally:
                    self._tasks.pop(task_id, None)  # 结束后释放，避免长驻宿主内存增长

            self._tasks[task_id] = asyncio.create_task(runner())
        if wait:
            await self.wait(task_id)
        return handle

    async def transcribe(
        self,
        url: str,
        format: str | None = None,
        audio_format: str = "m4a",
        out_dir: str | Path | None = None,
        speed_factor: float = 1.0,
        wait: bool = False,
        timeout: float | None = None,
    ) -> TaskHandle:
        """下载并做 ASR 转写（切片→倍速→OSS→qwen→时间戳对齐）。

        流水线分三阶段并行化：下载（_download_sem）→ ASR（_asr_sem）→ 润色（_refine_sem），
        各阶段独立信号量，互不占槽：下载慢的任务不阻塞其他任务的 ASR/润色阶段。

        timeout：任务级超时保护（秒）——外部系统（OSS/ASR/LLM）偶发挂起时，
        超时即标记任务失败并清理，避免一个任务卡死阻塞后续（宿主可失败跳过继续）。

        需要 config/secrets.toml 已配置 [oss] 和 [asr]。
        """
        if audio_format not in AUDIO_CODECS:
            raise ValueError(f"不支持的音频格式: {audio_format}")
        async def runner():
            tmp = resolve_dir(self._cfg["paths"], "temp") / task_id
            delivery = Path(out_dir) if out_dir else resolve_dir(self._cfg["paths"], "delivery")
            staging = delivery / f".staging-{task_id}"
            publish_fence = threading.Event()
            publish_fence.set()
            try:
                async def run_pipeline():
                    async with self._download_sem:
                        ctx = await asyncio.to_thread(self._transcribe_download, task_id, url, platform, format, audio_format, out_dir)
                    self._progress(task_id, "waiting_for_asr", asr_backend="online")
                    async with self._asr_sem:
                        self._progress(task_id, "transcribing", asr_backend="online")
                        transcript = await asyncio.to_thread(self._transcribe_asr, task_id, ctx, speed_factor)
                        quality = transcript_quality_metrics(
                            transcript["segments"], transcript["audio_duration"], "online",
                        )
                        self._progress(task_id, "quality_check", quality=quality, asr_backend="online")
                        if not quality["accepted"]:
                            raise TranscriptQualityError(
                                "online ASR quality gate rejected transcript; "
                                f"attempts={json.dumps([quality], ensure_ascii=False)}",
                                [quality],
                            )
                    if not publish_fence.is_set():
                        return None
                    async with self._refine_sem:
                        await asyncio.to_thread(self._transcribe_refine, task_id, ctx, transcript)
                    if not publish_fence.is_set():
                        return None
                    self._progress(task_id, "publishing", asr_backend="online", quality=quality)
                    ctx = await asyncio.to_thread(
                        self._publish_local_transcription, task_id, ctx, transcript, url,
                    )
                    return ctx
                pipeline_task = asyncio.create_task(run_pipeline())
                if timeout:
                    ctx = await asyncio.wait_for(asyncio.shield(pipeline_task), timeout=timeout)
                else:
                    ctx = await pipeline_task
                if ctx is None:
                    return
                self._cleanup_oss()
                store = self.store
                store.set_out_dir(task_id, str(ctx["task_dir"]))
                store.set_status(task_id, STATUS_DONE)
                self._commit_download_credentials(ctx.get("session"))
                self._emitter.emit(ev.EVENT_TASK_DONE,
                                   {"task_id": task_id, "out_dir": str(ctx["task_dir"]),
                                    "mode": "transcribe", "warnings": []})
            except asyncio.TimeoutError:
                publish_fence.clear()
                store = self.store
                detail = f"转写任务超过 {timeout}s 未完成"
                store.set_status(
                    task_id, STATUS_FAILED, error_code="timeout", error_detail=detail,
                    error_source="lifecycle", error_retryable=True,
                )
                self._emitter.emit(ev.EVENT_TASK_FAILED, {
                    "task_id": task_id, "error_code": "timeout", "error_source": "lifecycle",
                    "retryable": True, "error": detail, "mode": "transcribe",
                })
                log.warning("[%s] %s", task_id, detail)
                try:
                    await asyncio.shield(pipeline_task)
                except Exception as exc:  # noqa: BLE001 - terminal failure already recorded
                    log.info("[%s] 超时后底层转写已退出: %s", task_id, type(exc).__name__)
            except Exception as e:  # noqa: BLE001
                failure = self._mark_failed(self.store, task_id, e)
                self._emitter.emit(ev.EVENT_TASK_FAILED, {**failure, "mode": "transcribe"})
                log.exception("[%s] 转写任务失败: %s", task_id, e)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
                shutil.rmtree(staging, ignore_errors=True)
                self._tasks.pop(task_id, None)

        async with self._lifecycle_lock:
            self._ensure_open()
            if self._download_sem is None:
                self._initialize_unlocked()
            platform = platforms.detect_platform(url)
            params = {"mode": "transcribe", "format": format, "audio_format": audio_format,
                      "speed_factor": speed_factor, "timeout": timeout}
            task_id = self.store.create_task(sanitize_persisted_url(url), platform, params)
            handle = TaskHandle(id=task_id)
            self._tasks[task_id] = asyncio.create_task(runner())
        if wait:
            await self.wait(task_id)
        return handle

    async def transcribe_local(
        self,
        url: str,
        format: str | None = None,
        audio_format: str = "m4a",
        out_dir: str | Path | None = None,
        wait: bool = False,
        local_timestamp_mode: str = "vad",
        local_diarization: bool = True,
        local_speaker_count: int = 0,
    ) -> TaskHandle:
        """下载音频并在独立单线程中执行本地 ASR。"""
        if audio_format not in AUDIO_CODECS:
            raise ValueError(f"不支持的音频格式: {audio_format}")
        if local_timestamp_mode not in {"model", "vad"}:
            raise ValueError("local_timestamp_mode must be one of: model, vad")
        if not 0 <= local_speaker_count <= 20:
            raise ValueError("local_speaker_count must be between 0 and 20")
        if not local_diarization and local_speaker_count:
            raise ValueError("local_speaker_count must be 0 when diarization is disabled")
        async def operation():
            tmp = resolve_dir(self._cfg["paths"], "temp") / task_id
            delivery = Path(out_dir) if out_dir else resolve_dir(self._cfg["paths"], "delivery")
            staging = delivery / f".staging-{task_id}"
            try:
                async with self._download_sem:
                    ctx = await asyncio.to_thread(
                        self._transcribe_download_common,
                        task_id, url, platform, format, audio_format, out_dir,
                        asr_backend="local", defer_publish=True,
                    )
                if self._audio is None:
                    # 本地 ASR worker 需要 ffmpeg 解码音频；不要把 None 传进 worker。
                    raise RuntimeError("ffmpeg 不可用，无法本地转写")
                self._progress(task_id, "waiting_for_asr", asr_backend="local")
                async with self._local_asr_sem:
                    if self._local_asr_executor is None:
                        self._local_asr_executor = self._create_local_asr_executor()
                    loop = asyncio.get_running_loop()
                    worker_cfg = {"local_asr": dict(self._cfg.get("local_asr", {}))}
                    attempts = []
                    transcript = None
                    funasr_engine = local_asr_engine(self._cfg) == "funasr_nano"
                    # FunASR owns segmentation and diarization, so one attempt is enough.
                    strategies = (local_timestamp_mode,) if funasr_engine else (
                        local_timestamp_mode,
                        "model" if local_timestamp_mode == "vad" else "vad",
                    )
                    for timestamp_mode in strategies:
                        self._progress(task_id, "transcribing", asr_backend="local",
                                       timestamp_mode=timestamp_mode)
                        inferred = await loop.run_in_executor(
                            self._local_asr_executor, _run_local_asr_worker,
                            worker_cfg, self._audio.ffmpeg_path, str(ctx["audio_src"]),
                            str(ctx["tmp"] / "local-asr"), timestamp_mode,
                            funasr_engine and local_diarization, local_speaker_count,
                        )
                        quality = transcript_quality_metrics(
                            inferred["segments"], inferred["audio_duration"], timestamp_mode,
                        )
                        attempts.append(quality)
                        self._progress(task_id, "quality_check", quality=quality,
                                       asr_backend="local")
                        if quality["accepted"]:
                            accepted_segments = inferred["segments"]
                            if local_diarization:
                                accepted_segments = await loop.run_in_executor(
                                    self._local_asr_executor, _run_local_diarization_worker,
                                    worker_cfg, self._audio.ffmpeg_path,
                                    str(ctx["tmp"] / "local-asr"), accepted_segments,
                                    local_speaker_count,
                                )
                            transcript = self._save_local_transcript(ctx, accepted_segments)
                            transcript["quality"] = quality
                            break
                    if transcript is None:
                        raise TranscriptQualityError(
                            "local ASR quality gate rejected both timestamp strategies; "
                            f"attempts={json.dumps(attempts, ensure_ascii=False)}",
                            attempts,
                        )
                self._progress(task_id, "publishing", asr_backend="local",
                               quality=transcript["quality"])
                ctx = await asyncio.to_thread(
                    self._publish_local_transcription, task_id, ctx, transcript, url,
                )
                store = self.store
                store.set_out_dir(task_id, str(ctx["task_dir"]))
                store.set_status(task_id, STATUS_DONE)
                self._commit_download_credentials(ctx.get("session"))
                self._emitter.emit(ev.EVENT_TASK_DONE, {
                    "task_id": task_id,
                    "out_dir": str(ctx["task_dir"]),
                    "mode": "transcribe",
                    "asr_backend": "local",
                    "warnings": [],
                })
            except Exception as e:  # noqa: BLE001
                failure = self._mark_failed(self.store, task_id, e)
                self._emitter.emit(ev.EVENT_TASK_FAILED, {
                    **failure,
                    "mode": "transcribe",
                    "asr_backend": "local",
                })
                log.exception("[%s] 本地转写任务失败: %s", task_id, e)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
                shutil.rmtree(staging, ignore_errors=True)

        async def runner():
            operation_task = asyncio.create_task(operation())
            while not operation_task.done():
                try:
                    await asyncio.shield(operation_task)
                except asyncio.CancelledError:
                    log.info("[%s] 忽略本地任务 wrapper 取消，等待底层操作自然结束", task_id)
            try:
                operation_task.result()
            finally:
                self._tasks.pop(task_id, None)

        async with self._lifecycle_lock:
            self._ensure_open()
            if self._download_sem is None:
                self._initialize_unlocked()
            platform = platforms.detect_platform(url)
            params = {
                "mode": "transcribe",
                "format": format,
                "audio_format": audio_format,
                "asr_backend": "local",
                "local_timestamp_mode": local_timestamp_mode,
                "local_diarization": local_diarization,
                "local_speaker_count": local_speaker_count,
            }
            task_id = self.store.create_task(sanitize_persisted_url(url), platform, params)
            handle = TaskHandle(id=task_id)
            self._tasks[task_id] = asyncio.create_task(runner())
        if wait:
            await self.wait(task_id)
        return handle

    async def query(self, task_id: str) -> dict | None:
        return await asyncio.to_thread(self.store.get_task, task_id)

    async def asr_result(self, task_id: str) -> dict | None:
        return await asyncio.to_thread(self.store.get_asr, task_id)

    async def refine(self, task_id: str) -> dict:
        """对已完成转写的任务做文章润色（不重新下载/转写），产出 article.md。"""
        return await asyncio.to_thread(self._refine_existing, task_id)

    async def submit(
        self, url: str, out_dir: str | Path | None = None, wait: bool = False, *,
        mode: str = "auto", asr_backend: str = "auto",
        local_timestamp_mode: str = "vad", local_diarization: bool = True,
        local_speaker_count: int = 0,
    ) -> TaskHandle:
        """内容驱动的统一入口：一个 URL 探测、下载并发布为一个条目。

        平台只决定会话、解析器和 JS 渲染策略，不再预先把 URL 二分为文章或视频。
        同一页面的正文、图片及所有有限音视频会共同进入一个 article/video/audio/mixed
        条目。显式 ``download`` 与 ``transcribe`` 仍保留原有意图覆盖语义。
        """
        async with self._lifecycle_lock:
            self._ensure_open()
            if self._download_sem is None:
                self._initialize_unlocked()
        plan = self.plan_import(url, mode=mode, asr_backend=asr_backend)
        if plan["blocker"]:
            raise RuntimeError(
                f"transcription backend {asr_backend!r} blocked: {plan['blocker']}"
            )
        if plan["mode"] == "transcribe":
            if plan["asr_backend"] == "online":
                return await self.transcribe(url, out_dir=out_dir, wait=wait)
            return await self.transcribe_local(
                url, out_dir=out_dir, wait=wait,
                local_timestamp_mode=local_timestamp_mode,
                local_diarization=local_diarization,
                local_speaker_count=local_speaker_count,
            )
        if plan["mode"] == "download":
            return await self.download(url, out_dir=out_dir, wait=wait)
        if mode == "article":
            return await self.scrape(url, out_dir=out_dir, wait=wait)

        async def runner():
            try:
                async with self._scrape_sem:
                    await asyncio.to_thread(self._run_auto_capture, task_id, url, platform, out_dir)
            finally:
                self._tasks.pop(task_id, None)

        async with self._lifecycle_lock:
            self._ensure_open()
            if self._scrape_sem is None:
                self._initialize_unlocked()
            platform = platforms.detect_platform(url)
            task_id = self.store.create_task(
                sanitize_persisted_url(url), platform, {"mode": "auto", "strategy": "content_probe"},
            )
            handle = TaskHandle(id=task_id)
            self._tasks[task_id] = asyncio.create_task(runner())
        if wait:
            await self.wait(task_id)
        return handle

    async def scrape(self, url: str, out_dir: str | Path | None = None, wait: bool = False) -> TaskHandle:
        """文章/图片抓取（任务级 Python Chrome 146 近似网络会话）。

        产物：delivery/{文章标题}/article.md + images/ + manifest.json（meta 并入 source 字段）。
        需要登录的平台由 SessionBridge 只读取 Cookie；内容请求始终由 Python 发起。
        """
        async def runner():
            try:
                async with self._scrape_sem:
                    await asyncio.to_thread(self._run_scrape, task_id, url, platform, out_dir)
            finally:
                self._tasks.pop(task_id, None)

        async with self._lifecycle_lock:
            self._ensure_open()
            if self._scrape_sem is None:
                self._initialize_unlocked()
            platform = article_platform(url)
            task_id = self.store.create_task(
                sanitize_persisted_url(url), platform, {"mode": "scrape"},
            )
            handle = TaskHandle(id=task_id)
            self._tasks[task_id] = asyncio.create_task(runner())
        if wait:
            await self.wait(task_id)
        return handle

    async def wait(self, task_id: str, timeout: float | None = None) -> dict:
        t = self._tasks.get(task_id)
        if t:
            await asyncio.wait_for(asyncio.shield(t), timeout=timeout)
        return await self.query(task_id)

    async def list(self, limit: int = 50) -> list[dict]:
        return await asyncio.to_thread(self.store.list_tasks, limit)

    async def close(self):
        """停止接收新任务，等待现有任务自然完成后关闭存储。

        任务在 to_thread 线程中运行（yt-dlp/ffmpeg 无法被 asyncio 取消），
        因此这里不做"强制取消"：宿主需要立即中断时直接结束进程即可。
        """
        self._ensure_lifecycle_state()
        async with self._lifecycle_lock:
            if self._close_task is None:
                self._closing = True
                self._close_task = asyncio.create_task(self._close_impl())
            close_task = self._close_task
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                log.info("忽略 close 调用者取消，等待 DownloadService 完成关闭")
        close_task.result()

    async def _close_impl(self):
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        executor = getattr(self, "_local_asr_executor", None)
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, True)
            self._local_asr_executor = None
        if self._store is not None:
            await asyncio.to_thread(self._store.close)

    def _ensure_open(self) -> None:
        if getattr(self, "_closing", False):
            raise RuntimeError("DownloadService 正在关闭，不能提交新任务")

    def _progress(self, task_id: str, stage: str, **payload) -> None:
        self._emitter.emit(ev.EVENT_TASK_PROGRESS, {
            "task_id": task_id, "stage": stage, **payload,
        })

    @staticmethod
    def _create_local_asr_executor() -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=1, mp_context=multiprocessing.get_context("spawn"),
        )

    # ---------- 内部 ----------

    def _run_download(self, task_id: str, url: str, platform: str, fmt: str | None, audio: str | None, out_dir):
        store = self.store
        delivery = Path(out_dir) if out_dir else resolve_dir(self._cfg["paths"], "delivery")
        tmp = resolve_dir(self._cfg["paths"], "temp") / task_id
        tmp.mkdir(parents=True, exist_ok=True)
        staging = delivery / f".staging-{task_id}"  # 先下载到临时名，拿到标题后再重命名为标题目录
        staging.mkdir(parents=True, exist_ok=True)
        try:
            store.set_status(task_id, STATUS_RUNNING)
            self._emitter.emit(ev.EVENT_TASK_STARTED, {"task_id": task_id, "url": sanitize_persisted_url(url), "platform": platform})
            self._progress(task_id, "acquiring")

            def on_progress(d):
                if d.get("status") in ("downloading", "finished"):
                    self._progress(task_id, "downloading", **d)

            session_first = platform in self._cfg["download"].get("session_first_platforms", [])
            session = None
            factory = None
            refresher = None
            if self._session_provider is not None:
                factory = lambda: self._acquire_session(url, platform, task_id, tmp)
                refresher = lambda stale: self._acquire_session(
                    url, platform, task_id, tmp, stale_session=stale,
                )
                if session_first:
                    session = factory()
            result = downloader.download_video(
                self._cfg, url, fmt, staging, tmp, audio_codec=audio, on_progress=on_progress,
                session=session, session_factory=factory, session_refresher=refresher,
            )
            if audio:
                if self._audio is None:
                    raise RuntimeError("ffmpeg 不可用，无法转音频")
                result.files = self._transcode_outputs(result, audio)

            self._progress(task_id, "publishing")
            manifest_id = derive_id(platform, result.info.get("id"), sanitize_persisted_url(url))
            task_dir = self._finalize_media_manifest(staging, task_id, result.info.get("title"),
                                                     manifest_id, result, url)
            for f in sorted(p for p in task_dir.rglob("*") if p.is_file()):
                store.add_file(task_id, self._file_kind(f), str(f), f.stat().st_size)

            store.set_out_dir(task_id, str(task_dir))
            store.set_status(task_id, STATUS_DONE)
            self._commit_download_credentials(result.session)
            self._emitter.emit(ev.EVENT_TASK_DONE, {
                "task_id": task_id, "out_dir": str(task_dir), "warnings": [],
            })
        except Exception as e:  # noqa: BLE001
            failure = self._mark_failed(store, task_id, e)
            self._emitter.emit(ev.EVENT_TASK_FAILED, failure)
            log.exception("[%s] 任务失败: %s", task_id, e)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)

    def _acquire_session(self, url: str, platform: str, task_id: str, tmp: Path,
                         stale_session=None):
        """从 SessionProvider 获取会话（Q9）；临时 cookie 文件落 tmp/{task_id}/session/ 用后即删。

        注意：不能放 tmp/{task_id}/ 根目录——下载器会把该目录顶层文件当作下载产物
        （_collect_outputs），cookie 文件会被误收去转码。
        """
        session_url = UrlContext.from_url(url).session_url
        if self._session_provider is None:
            return None
        if stale_session is None:
            session = self._session_provider.get_session(
                url=session_url, platform=platform, job_id=task_id,
            )
        else:
            session = self._session_provider.refresh_session(
                stale_session=stale_session,
                url=session_url,
                platform=platform,
                job_id=task_id,
            )
        if session and session.cookie_file:
            session_dir = tmp / "session"
            session_dir.mkdir(parents=True, exist_ok=True)
            dst = session_dir / "cookies.txt"
            if session.cookie_file.resolve() != dst.resolve():
                source = session.cookie_file
                try:
                    dst.unlink(missing_ok=True)
                    shutil.move(str(source), dst)
                    session.cookie_file = dst
                except Exception:
                    source.unlink(missing_ok=True)
                    raise
        return session

    @staticmethod
    def _mark_failed(store: Store, task_id: str, e: Exception):
        """Persist and return the exact, public-safe task failure payload."""
        if isinstance(e, (ScrapeError, BrowserHttpError)):
            code = e.code
            source = "content"
        elif isinstance(e, TranscriptQualityError):
            code = FailureCode.TRANSCRIPT_QUALITY
            source = "asr"
        else:
            code = classify_error(e)
            source = "runtime"
        retryable = code in {
            FailureCode.NETWORK, FailureCode.RATE_LIMITED, FailureCode.COOKIE_ROTATED,
            FailureCode.SESSION_REJECTED,
        }
        detail = re.sub(
            r"https?://[^\s'\"<>]+",
            lambda match: sanitize_persisted_url(match.group(0)),
            str(e),
        )[:480]
        store.set_status(
            task_id, STATUS_FAILED, error_code=code.value, error_detail=detail,
            error_source=source, error_retryable=retryable,
        )
        payload = {
            "task_id": task_id,
            "error_code": code.value,
            "error_source": source,
            "retryable": retryable,
            "error": detail,
        }
        if isinstance(e, TranscriptQualityError):
            payload["quality"] = e.attempts
        return payload

    def _commit_download_credentials(self, session) -> None:
        """Best-effort CAS of yt-dlp's final jar; content success stays authoritative."""
        provider = self._session_provider
        if session is None or provider is None or not hasattr(provider, "commit_session"):
            return
        try:
            provider.commit_session(session)
        except Exception:  # cache maintenance must not turn a completed task into failure
            log.warning("下载会话 Cookie 快照回写失败（忽略）")

    def _finalize_dir(self, staging: Path, title: str | None, task_id: str,
                      manifest_id: str | None = None,
                      overwrite_fixed: bool = False) -> tuple[Path, list[Path]]:
        """Commit a complete staging directory without exposing partial items.

        New items are a same-volume directory rename.  A rerun of the same
        manifest id is a reversible old→backup, staging→target swap.  Existing
        user-owned article text and its image files are carried into the new
        snapshot before the swap.
        """
        delivery = staging.parent
        if manifest_id:
            self._validate_staging_manifest(staging, manifest_id)
        task_dir = pick_task_dir(delivery, title, task_id, manifest_id)
        if task_dir == staging:
            return task_dir, sorted(f for f in task_dir.rglob("*") if f.is_file())
        delivery.mkdir(parents=True, exist_ok=True)
        if not task_dir.exists():
            staging.replace(task_dir)
            return task_dir, sorted(f for f in task_dir.rglob("*") if f.is_file())

        self._merge_existing_item(task_dir, staging)
        if manifest_id:
            self._validate_staging_manifest(staging, manifest_id)
        backup = delivery / f".previous-{task_id}"
        if backup.exists():
            raise RuntimeError(f"发布备份目录已存在，请人工检查: {backup}")
        task_dir.replace(backup)
        try:
            staging.replace(task_dir)
        except Exception:
            if not task_dir.exists() and backup.exists():
                backup.replace(task_dir)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return task_dir, sorted(f for f in task_dir.rglob("*") if f.is_file())

    @staticmethod
    def _validate_staging_manifest(staging: Path, manifest_id: str) -> None:
        manifest_path = staging / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError("拒绝发布：staging 缺少 manifest.json")
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("拒绝发布：manifest.json 无法解析") from exc
        if value.get("id") != manifest_id:
            raise RuntimeError("拒绝发布：manifest.id 与任务计划不一致")
        declarations = []
        if isinstance(value.get("primary_media"), dict):
            declarations.append(value["primary_media"])
        declarations.extend(
            item for item in (value.get("media_items") or []) if isinstance(item, dict)
        )
        for declaration in declarations:
            for field in ("original_path", "playback_path", "poster_path"):
                raw = declaration.get(field)
                if field == "poster_path" and raw is None:
                    continue
                if not isinstance(raw, str) or not raw.strip():
                    raise RuntimeError(f"拒绝发布：媒体字段 {field} 为空")
                relative = Path(raw)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError(f"拒绝发布：媒体字段 {field} 路径不安全")
                candidate = (staging / relative).resolve()
                try:
                    candidate.relative_to(staging.resolve())
                except ValueError as exc:
                    raise RuntimeError(f"拒绝发布：媒体字段 {field} 越界") from exc
                if not candidate.is_file():
                    raise RuntimeError(f"拒绝发布：媒体字段 {field} 文件不存在")

    @staticmethod
    def _merge_existing_item(existing: Path, staging: Path) -> None:
        """Carry user-owned text and old media only when the new snapshot lacks it."""
        existing_article = existing / "article.md"
        if existing_article.is_file():
            shutil.copy2(existing_article, staging / "article.md")
            old_images = existing / "images"
            new_images = staging / "images"
            shutil.rmtree(new_images, ignore_errors=True)
            if old_images.is_dir():
                shutil.copytree(old_images, new_images)
        existing_transcript = existing / "transcript.json"
        if existing_transcript.is_file() and not (staging / "transcript.json").exists():
            shutil.copy2(existing_transcript, staging / "transcript.json")
        old_media = existing / "media"
        if old_media.is_dir() and not (staging / "media").exists():
            shutil.copytree(old_media, staging / "media")

        old_manifest = existing / "manifest.json"
        new_manifest = staging / "manifest.json"
        if not old_manifest.is_file() or not new_manifest.is_file():
            return
        try:
            old_value = json.loads(old_manifest.read_text(encoding="utf-8"))
            new_value = json.loads(new_manifest.read_text(encoding="utf-8"))
            if old_value.get("created_at"):
                new_value["created_at"] = old_value["created_at"]
            if not new_value.get("primary_media") and old_value.get("primary_media"):
                new_value["primary_media"] = old_value["primary_media"]
                if old_value.get("media_items"):
                    new_value["media_items"] = old_value["media_items"]
            article_meta = old_value.get("article") or {}
            if article_meta.get("content_source"):
                new_value.setdefault("article", {})["content_source"] = article_meta["content_source"]
            pending = new_manifest.with_name(".manifest.json.tmp")
            pending.write_text(json.dumps(new_value, ensure_ascii=False, indent=2), encoding="utf-8")
            pending.replace(new_manifest)
        except (OSError, ValueError, TypeError):
            log.warning("无法合并既有 manifest 元数据，将使用本次完整快照")

    def _open_xhs_client(self, url: str, task_id: str) -> XhsClient:
        """Read Chrome login state once and construct one task-scoped curl session."""
        article_cfg = self._cfg.get("article", {})
        try:
            return XhsClient.from_broker(
                self._credentials, url, task_id,
                domains=platforms.session_domains(
                    "xiaohongshu", self._cfg, url=url,
                ),
                page_timeout=float(article_cfg.get("direct_timeout", 15)),
                resource_timeout=float(article_cfg.get("image_timeout", 30)),
                proxy=str(article_cfg.get("proxy") or ""),
                resolve_dns=True,
            )
        except BrowserHttpError as exc:
            raise ScrapeError(exc.code, str(exc)) from exc

    def _open_article_client(self, url: str, task_id: str,
                             platform: str) -> BrowserHttpClient:
        """Create one Chrome-146 network session for an article task."""
        article_cfg = self._cfg.get("article", {})
        credential_platform = platform
        if platform == "generic_article":
            detected = platforms.detect_platform(url)
            if detected != "generic":
                credential_platform = detected
        domains = platforms.session_domains(
            credential_platform, self._cfg, url=url,
        )
        authenticated = (
            credential_platform not in {"generic", "generic_article"}
            and self._credentials_available
        )
        try:
            return BrowserHttpClient.open(
                url=url,
                platform=credential_platform,
                job_id=task_id,
                credential_broker=self._credentials,
                domains=domains,
                authenticated=authenticated,
                page_timeout=float(article_cfg.get("direct_timeout", 15)),
                resource_timeout=float(article_cfg.get("image_timeout", 30)),
                proxy=str(article_cfg.get("proxy") or ""),
                # 空 Cookie 是公开站点的有效快照；只有 XHS 专用客户端强制 Cookie。
                require_cookies=False,
                resolve_dns=True,
            )
        except BrowserHttpError as exc:
            raise ScrapeError(exc.code, str(exc)) from exc

    @staticmethod
    def _fetch_xhs(url: str, client: XhsClient) -> dict:
        """Issue one application-level page GET through the task-scoped curl session."""
        try:
            return client.fetch_page(url)
        except BrowserHttpError as exc:
            raise ScrapeError(exc.code, str(exc)) from exc

    @staticmethod
    def _commit_credentials(client: BrowserHttpClient) -> None:
        """Credential cache maintenance must never turn completed content into failure."""
        try:
            client.commit_credentials()
        except Exception:  # cache is best-effort and contains no content artifact
            log.warning("浏览器内存 Cookie 快照回写失败（忽略）")

    def _download_xhs_video(self, url: str, task_id: str, staging: Path,
                            note: dict, capture: dict, client: XhsClient) -> dict:
        """Download the single selected stream from a captured XHS note."""
        pick = xhs_video.pick_stream(note)
        if pick is None:
            raise ScrapeError(FailureCode.CONTENT_NOT_FOUND, "小红书视频帖无可用码流")
        codec, src = pick
        out_mp4 = staging / "video.mp4"
        final_url = capture.get("final_url") or url
        try:
            size = xhs_video.download(
                src, out_mp4, client=client, referer=final_url,
            )
        except BrowserHttpError as exc:
            raise ScrapeError(exc.code, "小红书视频下载失败") from exc
        title = (note.get("title") or "小红书视频").strip() or "小红书视频"
        author = (((note.get("user") or {}).get("nickname")) or "").strip() or None
        note_id = str(note.get("noteId") or note.get("id") or "").strip() or None
        log.info("[%s] 小红书视频下载完成: %s（%dKB，%s）", task_id, title[:40], size // 1024, codec)
        return {"title": title, "mp4": out_mp4, "size": size, "codec": codec,
                "author": author, "note_id": note_id, "note_title": title}

    def _transcode_outputs(self, result: downloader.DownloadResult, codec: str) -> list[Path]:
        """把下载产物转成音频；源视频保留。返回全部产物路径（原始 + 转码）。"""
        produced: list[Path] = list(result.files)
        for f in list(result.files):
            if f.suffix.lower() in AUDIO_SUFFIXES:
                continue
            dst = f.with_name(f"{f.stem}.{codec}")
            if not dst.exists():
                dst = self._audio.transcode(f, dst, codec)
            produced.append(dst)
        return produced

    def _finalize_media_manifest(self, staging: Path, task_id: str, title: str | None,
                                 manifest_id: str, result: downloader.DownloadResult, url: str,
                                 source_title: str | None = None) -> Path:
        """下载/转写阶段产物落盘：staging 上编排 media → 移动进标题目录 → 写 manifest。

        返回 task_dir。媒体编排必须发生在 staging（finalize 前），
        否则整个目录快照（含 manifest.json/media 旧文件）会被误当主媒体。
        """
        info = result.info or {}
        media = publish_media(staging, list(result.files),
                              self._cfg["audio"].get("ffmpeg_path", ""),
                              media_title=str(info.get("title") or ""),
                              description=str(info.get("description") or "").strip() or None)
        kind = "audio" if media and media.kind == "audio" else "video"
        write_manifest(
            staging,
            manifest_id=manifest_id,
            source_type=kind,
            source_url=sanitize_persisted_url(url),
            source_title=source_title or str(info.get("title") or "").strip() or None,
            source_description=str(info.get("description") or "").strip() or None,
            source_author=info.get("uploader") or info.get("channel") or None,
            source_published_at=info.get("upload_date"),
            media=media,
        )
        task_dir, _ = self._finalize_dir(
            staging, title, task_id, manifest_id, overwrite_fixed=True,
        )
        return task_dir

    def _transcribe_download(self, task_id, url, platform, fmt, audio_format, out_dir) -> dict:
        """Download online-ASR media into private staging until quality succeeds."""
        if self._oss is None:
            raise RuntimeError("OSS 未配置：请填写 config/secrets.toml 的 [oss]")
        return self._transcribe_download_common(
            task_id, url, platform, fmt, audio_format, out_dir,
            asr_backend="online", defer_publish=True,
        )

    def _transcribe_download_common(
        self, task_id, url, platform, fmt, audio_format, out_dir, *, asr_backend=None,
        defer_publish=False,
    ) -> dict:
        """在线与本地转写共用的下载和 Echo 媒体交付阶段。"""
        store = self.store
        delivery = Path(out_dir) if out_dir else resolve_dir(self._cfg["paths"], "delivery")
        tmp = resolve_dir(self._cfg["paths"], "temp") / task_id
        tmp.mkdir(parents=True, exist_ok=True)
        staging = delivery / f".staging-{task_id}"
        staging.mkdir(parents=True, exist_ok=True)
        if self._audio is None:
            raise RuntimeError("ffmpeg 不可用，无法转写")
        store.set_status(task_id, STATUS_RUNNING)
        event_payload = {"task_id": task_id, "url": sanitize_persisted_url(url), "platform": platform, "mode": "transcribe"}
        if asr_backend is not None:
            event_payload["asr_backend"] = asr_backend
        self._emitter.emit(ev.EVENT_TASK_STARTED, event_payload)
        self._progress(task_id, "acquiring", **({"asr_backend": asr_backend} if asr_backend else {}))
        log.info("[%s] 阶段1/3 下载音频开始", task_id)

        session_first = platform in self._cfg["download"].get("session_first_platforms", [])
        session = None
        factory = None
        refresher = None
        if self._session_provider is not None:
            factory = lambda: self._acquire_session(url, platform, task_id, tmp)
            refresher = lambda stale: self._acquire_session(
                url, platform, task_id, tmp, stale_session=stale,
            )
            if session_first:
                session = factory()
        def on_progress(payload):
            if payload.get("status") in ("downloading", "finished"):
                extra = {"asr_backend": asr_backend} if asr_backend else {}
                self._progress(task_id, "downloading", **extra, **payload)

        result = downloader.download_video(
            self._cfg, url, fmt, staging, tmp, audio_codec=audio_format, on_progress=on_progress,
            session=session, session_factory=factory, session_refresher=refresher,
        )
        manifest_id = derive_id(platform, result.info.get("id"), sanitize_persisted_url(url))
        if defer_publish:
            info = result.info or {}
            media = publish_media(
                staging,
                list(result.files),
                self._cfg["audio"].get("ffmpeg_path", ""),
                media_title=str(info.get("title") or ""),
            )
            task_dir = staging
        else:
            media = None
            task_dir = self._finalize_media_manifest(staging, task_id, result.info.get("title"),
                                                     manifest_id, result, url)
        media_dir = task_dir / "media"
        audio_src = next((media_dir / f for f in ("original.m4a", "original.mp3", "original.wav",
                                                  "original.flac", "original.opus", "original.ogg",
                                                  "original.aac", "original.mp4")
                          if (media_dir / f).is_file()), None)
        if audio_src is None:
            raise RuntimeError(f"转写音频未找到: {task_dir}")
        if not defer_publish:
            store.add_file(task_id, "audio", str(audio_src), audio_src.stat().st_size)
        log.info("[%s] 阶段1/3 下载音频完成: %s", task_id, audio_src.name)
        return {"task_dir": task_dir, "audio_src": audio_src, "tmp": tmp,
                "session": result.session, "manifest_id": manifest_id,
                "video_title": str(result.info.get("title") or ""),
                "source_info": result.info or {}, "media": media, "staging": staging}

    def _save_local_transcript(self, ctx: dict, segments: list[dict]) -> dict:
        """Write a normalized transcript only after the quality gate accepts it."""
        speaker_label = str(self._cfg.get("refine", {}).get("speaker_label", "说话人"))
        path = write_transcript(
            ctx["task_dir"], segments, detect_language(segments), speaker_label,
        )
        return {"segments": segments, "transcript_path": path, "normalized_path": str(path)}

    def _publish_local_transcription(self, task_id, ctx, transcript, url) -> dict:
        """本地 ASR 成功后才把 staging 原子提升为 Echo 可见条目并入库。"""
        staging = ctx["staging"]
        audio_rel = ctx["audio_src"].relative_to(staging)
        transcript_path = transcript.get("transcript_path")
        transcript_rel = transcript_path.relative_to(staging) if transcript_path else None
        info = ctx["source_info"]
        media = ctx["media"]
        kind = "audio" if media and media.kind == "audio" else "video"
        write_manifest(
            staging,
            manifest_id=ctx["manifest_id"],
            source_type=kind,
            source_url=sanitize_persisted_url(url),
            source_title=str(info.get("title") or "").strip() or None,
            source_description=str(info.get("description") or "").strip() or None,
            source_author=info.get("uploader") or info.get("channel") or None,
            source_published_at=info.get("upload_date"),
            media=media,
        )
        task_dir, _ = self._finalize_dir(
            staging,
            info.get("title"),
            task_id,
            ctx["manifest_id"],
            overwrite_fixed=True,
        )
        audio_path = task_dir / audio_rel
        store = self.store
        store.add_file(task_id, "audio", str(audio_path), audio_path.stat().st_size)
        normalized_path = str(task_dir / transcript_rel) if transcript_rel else None
        store.add_asr(
            task_id,
            "done",
            segments=transcript["segments"],
            normalized_path=normalized_path,
        )
        if transcript_rel:
            final_transcript = task_dir / transcript_rel
            store.add_file(
                task_id,
                "transcript",
                str(final_transcript),
                final_transcript.stat().st_size,
            )
        article_path = task_dir / "article.md"
        if article_path.is_file():
            store.add_file(task_id, "article", str(article_path), article_path.stat().st_size)
        return {**ctx, "task_dir": task_dir, "audio_src": audio_path}

    def _transcribe_asr(self, task_id, ctx, speed_factor) -> dict:
        """转写流水线阶段 2：切片→OSS→千问→对齐，转录落 transcript.json 并入库。"""
        audio_src = ctx["audio_src"]
        task_dir = ctx["task_dir"]
        asr_cfg = self._cfg.get("asr", {})
        provider = QwenFiletransProvider(
            base_url=asr_cfg.get("base_url", "https://dashscope.aliyuncs.com/api/v1"),
            api_key=asr_cfg.get("api_key", ""),
            model=asr_cfg.get("model", "qwen-audio-3.0-asr-flash-filetrans"),
            language_hints=asr_cfg.get("language_hints"),
            proxy=self._cfg["download"].get("proxy"),
            diarization_enabled=bool(asr_cfg.get("diarization_enabled", True)),
            speaker_count=int(asr_cfg.get("speaker_count", 0)),
        )
        pipeline = AsrPipeline(
            self._cfg, self._audio, provider, self._oss,
        )
        pipeline.speed_factor = speed_factor
        workdir = ctx["tmp"] / "asr"
        workdir.mkdir(parents=True, exist_ok=True)
        speaker_label = str(self._cfg.get("refine", {}).get("speaker_label", "说话人"))
        log.info("[%s] 阶段2/3 ASR 转写开始（%s）", task_id, audio_src.name)
        transcript = pipeline.transcribe(
            audio_src, task_id, workdir, task_dir,
            language=None, speaker_label=speaker_label,
        )
        segs = [s.to_dict() for s in transcript.segments]

        duration = pipeline._duration_seconds(audio_src)
        log.info("[%s] 阶段2/3 ASR 完成（%d 段）", task_id, len(segs))
        return {
            "segments": segs,
            "transcript_path": transcript.transcript_path,
            "normalized_path": str(transcript.transcript_path) if transcript.transcript_path else None,
            "audio_duration": duration,
        }

    def _transcribe_refine(self, task_id, ctx, transcript: dict):
        """转写流水线阶段 3：转写稿 → article.md（失败不致命）。

        决策 1（echo producer-guide §4）：article.md 是唯一正文文件——
        中文内容写入中文润色；英文内容直接写入中文版（原标题进 manifest source.title 作副标题）。
        已存在的 article.md 不覆盖（用户可能已修改）。
        """
        store = self.store
        if self._refiner is None:
            return
        try:
            task_dir = ctx["task_dir"]
            segs = transcript["segments"]
            zh_threshold = float(self._cfg.get("refine", {}).get("zh_threshold", 0.2))
            is_zh = is_chinese_content(segs, zh_threshold)
            article_path = task_dir / "article.md"
            if article_path.exists():
                log.info("[%s] article.md 已存在，跳过润色覆盖（echo §8）", task_id)
                return
            log.info("[%s] 阶段3/3 文章润色开始（%s，%d 段）", task_id, "中文" if is_zh else "英文→中文", len(segs))
            refined = self._refiner.refine(segs, chinese=not is_zh)
            article_path = self._refiner.save(refined, task_dir)
            log.info("[%s] 文章润色完成: %s（%d 段 / %d 分块 / %d 字%s）",
                     task_id, article_path, len(segs), refined.chunks, refined.output_chars,
                     "，中文版" if not is_zh else "")
            write_manifest(task_dir, manifest_id=ctx["manifest_id"], source_type="audio",
                           source_title=(ctx.get("video_title") or None) if not is_zh else None)
        except Exception as e:  # noqa: BLE001 润色失败不影响转写结果
            log.warning("[%s] 文章润色失败（转写结果仍有效）: %s", task_id, e)

    def _render_probe_if_needed(
        self,
        url: str,
        platform: str,
        probe: ProbeResult,
        http_client: BrowserHttpClient | None,
    ) -> ProbeResult:
        """Apply the per-platform JS policy and re-run the pure probe on rendered HTML."""
        from .js_render import JsRenderError, render_page, should_render

        if not should_render(platform, probe, self._cfg):
            return probe
        try:
            rendered = render_page(
                url,
                config=self._cfg,
                cookies=http_client.render_cookies if http_client is not None else (),
            )
        except JsRenderError as exc:
            # WeChat's configured "always" mode is a correctness requirement: silently
            # falling back would miss lazy-loaded media while claiming a complete import.
            if platform == "wechat":
                raise ScrapeError(FailureCode.NETWORK, f"微信页面 JS 渲染失败: {exc}") from exc
            probe.warnings.append(f"js_render_failed:{type(exc).__name__}")
            return probe
        rendered_probe = probe_html(
            url,
            rendered.html,
            final_url=rendered.final_url,
            platform=platform,
        )
        rendered_probe.warnings = list(dict.fromkeys([
            *probe.warnings,
            *rendered.warnings,
            *rendered_probe.warnings,
        ]))
        return rendered_probe

    @staticmethod
    def _expand_oembed(probe: ProbeResult, http_client: BrowserHttpClient) -> ProbeResult:
        """Resolve at most one discovered JSON oEmbed document without executing its HTML."""
        candidates = [
            item for item in probe.media
            if item.kind == "oembed" and item.mime_type == "application/json+oembed"
        ]
        if not candidates:
            return probe
        candidate = candidates[0]
        try:
            capture = http_client.fetch_page(candidate.url, allowed_domains=())
            raw = capture["html"]
            if len(raw.encode("utf-8")) > 262_144:
                raise ValueError("oEmbed response too large")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("oEmbed response must be an object")
            embedded_html = payload.get("html") if isinstance(payload.get("html"), str) else ""
            if embedded_html:
                embedded = probe_html(
                    probe.input_url,
                    embedded_html,
                    final_url=probe.final_url,
                )
                existing = {(item.kind, item.url) for item in probe.media}
                probe.media.extend(
                    item for item in embedded.media
                    if (item.kind, item.url) not in existing
                )
            direct_url = payload.get("url")
            media_type = str(payload.get("type") or "").casefold()
            if (isinstance(direct_url, str) and direct_url.startswith("https://")
                    and media_type in {"video", "audio"}):
                if (media_type, direct_url) not in {(item.kind, item.url) for item in probe.media}:
                    probe.media.append(MediaCandidate(
                        kind=media_type,
                        url=direct_url,
                        role="inline",
                        title=str(payload.get("title") or "").strip() or None,
                        evidence=("oembed:url",),
                    ))
            probe.title = probe.title or str(payload.get("title") or "").strip() or None
            probe.author = probe.author or str(payload.get("author_name") or "").strip() or None
        except Exception as exc:  # noqa: BLE001
            probe.warnings.append(f"oembed_failed:{type(exc).__name__}")
        return probe

    def _resolve_auto_media(
        self,
        task_id: str,
        url: str,
        platform: str,
        probe: ProbeResult | None,
        tmp: Path,
    ) -> tuple[list[downloader.MediaProbe], list[str]]:
        """Resolve the original page and every discovered media/embed candidate."""
        max_items = int(self._cfg.get("content_capture", {}).get("max_media_items", 50))
        if max_items < 1:
            raise RuntimeError("content_capture.max_media_items 必须大于 0")
        candidate_urls = [url]
        if probe is not None:
            candidate_urls.extend(
                item.url for item in probe.media
                if item.kind in {"video", "audio", "embed"}
            )
        candidate_urls = list(dict.fromkeys(candidate_urls))
        if len(candidate_urls) - 1 > max_items:
            raise downloader.MediaProbeLimitError(
                f"页面发现至少 {len(candidate_urls) - 1} 个媒体候选，超过自动抓取上限 {max_items}"
            )
        resolved: list[downloader.MediaProbe] = []
        seen: set[tuple[str, str]] = set()
        warnings: list[str] = []
        for candidate_url in candidate_urls:
            if len(resolved) >= max_items:
                raise downloader.MediaProbeLimitError(
                    f"页面媒体数量超过自动抓取上限 {max_items}"
                )
            candidate_platform = platforms.detect_platform(candidate_url)
            factory = None
            refresher = None
            if self._session_provider is not None:
                factory = lambda u=candidate_url, p=candidate_platform: self._acquire_session(
                    u, p, task_id, tmp,
                )
                refresher = lambda stale, u=candidate_url, p=candidate_platform: self._acquire_session(
                    u, p, task_id, tmp, stale_session=stale,
                )
            try:
                values, _session = downloader.probe_media(
                    self._cfg,
                    candidate_url,
                    session_factory=factory,
                    session_refresher=refresher,
                    max_entries=max_items - len(resolved),
                )
            except downloader.MediaProbeLimitError:
                raise
            except Exception as exc:  # one resolver must not erase a valid article
                warnings.append(
                    f"media_probe_failed:{candidate_platform}:{type(exc).__name__}"
                )
                continue
            for value in values:
                identity = (value.extractor or "", value.media_id or value.url)
                if identity in seen or any(item.url == value.url for item in resolved):
                    continue
                seen.add(identity)
                resolved.append(value)
                if len(resolved) > max_items:
                    raise downloader.MediaProbeLimitError(
                        f"页面媒体数量超过自动抓取上限 {max_items}"
                    )
        return resolved, warnings

    def _download_auto_media(
        self,
        task_id: str,
        resolved: list[downloader.MediaProbe],
        staging: Path,
        tmp: Path,
    ) -> tuple[list[MediaEntry], list[dict], list[str], list]:
        """Download and normalize each resolved media as a distinct manifest item."""
        entries: list[MediaEntry] = []
        infos: list[dict] = []
        warnings: list[str] = []
        sessions: list = []
        for index, item in enumerate(resolved, start=1):
            candidate_platform = platforms.detect_platform(item.url)
            media_tmp = tmp / f"media-{index:03d}"
            media_out = staging / f".media-{index:03d}"
            media_tmp.mkdir(parents=True, exist_ok=True)
            media_out.mkdir(parents=True, exist_ok=True)
            factory = None
            refresher = None
            if self._session_provider is not None:
                factory = lambda u=item.url, p=candidate_platform: self._acquire_session(
                    u, p, task_id, tmp,
                )
                refresher = lambda stale, u=item.url, p=candidate_platform: self._acquire_session(
                    u, p, task_id, tmp, stale_session=stale,
                )
            try:
                result = downloader.download_video(
                    self._cfg,
                    item.url,
                    None,
                    media_out,
                    media_tmp,
                    on_progress=lambda _d: None,
                    session_factory=factory,
                    session_refresher=refresher,
                )
                info = result.info or {}
                role = "primary" if not entries else "inline"
                media_id = "primary" if role == "primary" else f"media-{index:03d}"
                entry = publish_media(
                    staging,
                    list(result.files),
                    self._cfg["audio"].get("ffmpeg_path", ""),
                    media_title=str(info.get("title") or item.title or ""),
                    media_id=media_id,
                    role=role,
                    description=str(info.get("description") or item.description or "").strip() or None,
                )
                if entry is None:
                    raise RuntimeError("媒体下载未产生文件")
                entries.append(entry)
                infos.append(info)
                if result.session is not None:
                    sessions.append(result.session)
            except Exception as exc:  # partial success is retained and audited in manifest warnings
                warnings.append(
                    f"media_download_failed:{candidate_platform}:{type(exc).__name__}"
                )
                log.warning("[%s] 自动媒体下载失败，继续其余候选: %s", task_id, exc)
            finally:
                shutil.rmtree(media_tmp, ignore_errors=True)
                shutil.rmtree(media_out, ignore_errors=True)
        return entries, infos, warnings, sessions

    @staticmethod
    def _article_from_probe(
        probe: ProbeResult | None,
        media_infos: list[dict],
        fallback_url: str,
    ) -> Article | None:
        title = (probe.title if probe is not None else None) or next(
            (str(info.get("title") or "").strip() for info in media_infos if info.get("title")),
            "",
        )
        description = (probe.description if probe is not None else None) or next(
            (str(info.get("description") or "").strip()
             for info in media_infos if info.get("description")),
            "",
        )
        if probe is None:
            if not description:
                return None
            return Article(
                title=title or "未命名内容",
                url=fallback_url,
                content_text=description,
                content_html=f"<p>{description}</p>",
            )
        article = probe.article
        if article is None and (description or probe.images):
            article = Article(
                title=title or "未命名内容",
                url=probe.canonical_url or fallback_url,
                author=probe.author,
                date=probe.published_at,
                content_text=description,
                content_html=f"<p>{description}</p>" if description else "",
            )
        if article is not None:
            article.title = article.title or title or "未命名内容"
            article.url = probe.canonical_url or fallback_url
            article.author = article.author or probe.author
            article.date = article.date or probe.published_at
            if probe.images:
                article.images = [
                    {"src": image.get("src") or "", "alt": image.get("alt") or ""}
                    for image in probe.images
                    if image.get("src")
                ]
        return article

    def _run_auto_capture(self, task_id: str, url: str, platform: str, out_dir) -> None:
        """Probe one URL, acquire every selected artifact and atomically publish one item."""
        store = self.store
        delivery = Path(out_dir) if out_dir else resolve_dir(self._cfg["paths"], "delivery")
        delivery.mkdir(parents=True, exist_ok=True)
        tmp = resolve_dir(self._cfg["paths"], "temp") / task_id
        tmp.mkdir(parents=True, exist_ok=True)
        staging = delivery / f".staging-{task_id}"
        staging.mkdir(parents=True, exist_ok=True)
        client: BrowserHttpClient | None = None
        sessions: list = []
        try:
            store.set_status(task_id, STATUS_RUNNING)
            self._emitter.emit(ev.EVENT_TASK_STARTED, {
                "task_id": task_id, "url": sanitize_persisted_url(url), "platform": platform,
                "mode": "auto", "strategy": "content_probe",
            })
            warnings: list[str] = []
            probe: ProbeResult | None = None
            media_entries: list[MediaEntry] = []
            media_infos: list[dict] = []

            if platform == "xiaohongshu":
                client = self._open_xhs_client(url, task_id)
                capture = self._fetch_xhs(url, client)
                probe = probe_html(
                    url,
                    capture["html"],
                    final_url=capture.get("final_url") or url,
                    platform=platform,
                )
                note = xhs_video.note_from_html(capture["html"], url)
                if note is None:
                    raise ScrapeError(FailureCode.CONTENT_NOT_FOUND, "小红书 HTML 中未找到 note")
                if note.get("type") == "video" or note.get("video"):
                    downloaded = self._download_xhs_video(
                        url, task_id, staging, note, capture, client,
                    )
                    entry = publish_media(
                        staging,
                        [downloaded["mp4"]],
                        self._cfg["audio"].get("ffmpeg_path", ""),
                        media_title=downloaded["title"],
                        description=probe.description,
                    )
                    if entry is None:
                        raise ScrapeError(FailureCode.CONTENT_NOT_FOUND, "小红书视频未产生媒体文件")
                    media_entries.append(entry)
                    media_infos.append({
                        "title": downloaded["title"],
                        "description": probe.description,
                    })
            else:
                direct_media_suffixes = {
                    ".aac", ".flac", ".m3u8", ".m4a", ".m4v", ".mkv", ".mov",
                    ".mp3", ".mp4", ".ogg", ".opus", ".wav", ".webm",
                }
                is_direct_media = Path(url.split("?", 1)[0]).suffix.casefold() in direct_media_suffixes
                if not is_direct_media:
                    client = self._open_article_client(url, task_id, platform)
                    try:
                        capture = client.fetch_page(
                            url,
                            allowed_domains=client.credential_domains,
                        )
                        probe = probe_html(
                            url,
                            capture["html"],
                            final_url=capture.get("final_url") or url,
                            platform=platform,
                        )
                        probe = self._render_probe_if_needed(url, platform, probe, client)
                        probe = self._expand_oembed(probe, client)
                    except BrowserHttpError as exc:
                        warnings.append(f"page_fetch_failed:{exc.code.value}")
                resolved, resolve_warnings = self._resolve_auto_media(
                    task_id, url, platform, probe, tmp,
                )
                warnings.extend(resolve_warnings)
                media_entries, media_infos, download_warnings, sessions = self._download_auto_media(
                    task_id, resolved, staging, tmp,
                )
                warnings.extend(download_warnings)

            article = self._article_from_probe(probe, media_infos, url)
            article_result = None
            if article is not None and (article.content_text.strip() or article.images):
                article_result = ArticleExtractor(self._cfg, http_client=client).persist_article(
                    article,
                    probe.final_url if probe is not None else url,
                    task_id,
                    staging,
                    on_event=lambda event, payload: self._emitter.emit(event, payload),
                )
            has_article = article_result is not None and article_result.article_md.is_file()
            if not has_article and not media_entries:
                raise ScrapeError(FailureCode.CONTENT_NOT_FOUND, "链接中未发现可发布的正文或媒体")

            title = (
                (article.title if article is not None else None)
                or (probe.title if probe is not None else None)
                or next((entry.title for entry in media_entries if entry.title), None)
                or task_id
            )
            description = (
                (probe.description if probe is not None else None)
                or next((entry.description for entry in media_entries if entry.description), None)
            )
            source_author = (
                (probe.author if probe is not None else None)
                or next((str(info.get("uploader") or info.get("channel") or "").strip()
                         for info in media_infos
                         if info.get("uploader") or info.get("channel")), None)
            )
            source_published_at = (
                (probe.published_at if probe is not None else None)
                or next((str(info.get("upload_date") or "").strip()
                         for info in media_infos if info.get("upload_date")), None)
            )
            if has_article and media_entries:
                source_type = "mixed"
            elif len(media_entries) > 1:
                source_type = "mixed"
            elif media_entries:
                source_type = media_entries[0].kind
            else:
                source_type = "article"
            persisted_url = sanitize_persisted_url(
                probe.canonical_url if probe is not None else url,
            )
            manifest_id = derive_id(platform, url=sanitize_persisted_url(url))
            existing_target = pick_task_dir(delivery, title, task_id, manifest_id)
            all_warnings = list(dict.fromkeys([*(probe.warnings if probe else []), *warnings]))
            incomplete_prefixes = (
                "page_fetch_failed:",
                "js_render_failed:",
                "oembed_failed:",
                "media_probe_failed:",
                "media_download_failed:",
            )
            if existing_target.exists() and any(
                warning.startswith(incomplete_prefixes) for warning in all_warnings
            ):
                raise ScrapeError(
                    FailureCode.NETWORK,
                    "本次重抓内容获取不完整；已保留原有完整条目，未用部分结果覆盖",
                )
            write_manifest(
                staging,
                manifest_id=manifest_id,
                source_type=source_type,
                source_url=persisted_url,
                source_title=title,
                source_description=description,
                source_author=source_author,
                source_published_at=source_published_at,
                has_article=has_article,
                media_items=media_entries,
                warnings=all_warnings,
            )
            task_dir, final_files = self._finalize_dir(
                staging,
                title,
                task_id,
                manifest_id=manifest_id,
                overwrite_fixed=True,
            )
            for file in final_files:
                store.add_file(task_id, self._file_kind(file), str(file), file.stat().st_size)
            store.set_out_dir(task_id, str(task_dir))
            store.set_warnings(task_id, all_warnings)
            store.set_status(task_id, STATUS_DONE)
            for session in sessions:
                self._commit_download_credentials(session)
            if client is not None:
                self._commit_credentials(client)
            self._emitter.emit(ev.EVENT_TASK_DONE, {
                "task_id": task_id,
                "out_dir": str(task_dir),
                "mode": "auto",
                "source_type": source_type,
                "media_count": len(media_entries),
                "warnings": all_warnings,
            })
        except Exception as exc:  # noqa: BLE001
            failure = self._mark_failed(store, task_id, exc)
            self._emitter.emit(ev.EVENT_TASK_FAILED, {
                **failure, "mode": "auto",
            })
            log.exception("[%s] 自动内容抓取失败: %s", task_id, exc)
        finally:
            if client is not None:
                client.close()
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)

    def _run_scrape(self, task_id: str, url: str, platform: str, out_dir):
        """文章抓取任务：Python 单任务会话请求页面与资源，产物落 delivery/{标题}/。"""
        store = self.store
        delivery = Path(out_dir) if out_dir else resolve_dir(self._cfg["paths"], "delivery")
        tmp = resolve_dir(self._cfg["paths"], "temp") / task_id
        tmp.mkdir(parents=True, exist_ok=True)
        staging = delivery / f".staging-{task_id}"
        staging.mkdir(parents=True, exist_ok=True)
        http_client = None
        try:
            store.set_status(task_id, STATUS_RUNNING)
            self._emitter.emit(ev.EVENT_TASK_STARTED,
                               {"task_id": task_id, "url": sanitize_persisted_url(url), "platform": platform, "mode": "scrape"})
            if platform == "xiaohongshu":
                http_client = self._open_xhs_client(url, task_id)
                capture = self._fetch_xhs(url, http_client)
                note = xhs_video.note_from_html(capture["html"], url)
                if note is None:
                    raise ScrapeError(FailureCode.CONTENT_NOT_FOUND, "小红书 HTML 中未找到 note")
                if note.get("type") == "video" or note.get("video"):
                    vid = self._download_xhs_video(
                        url, task_id, staging, note, capture, http_client,
                    )
                    manifest_id = derive_id("xiaohongshu", vid.get("note_id"), sanitize_persisted_url(url))
                    media = publish_media(staging, [staging / "video.mp4"],
                                          self._cfg["audio"].get("ffmpeg_path", ""), media_title=vid["title"])
                    write_manifest(staging, manifest_id=manifest_id, source_type="video",
                                   source_url=sanitize_persisted_url(url), source_title=vid.get("note_title"),
                                   source_author=vid.get("author"), media=media)
                    task_dir, _ = self._finalize_dir(staging, vid["title"], task_id,
                                                     manifest_id=manifest_id, overwrite_fixed=True)
                    for f in sorted(p for p in task_dir.rglob("*") if p.is_file()):
                        store.add_file(task_id, self._file_kind(f), str(f), f.stat().st_size)
                    store.set_out_dir(task_id, str(task_dir))
                    store.set_status(task_id, STATUS_DONE)
                    self._emitter.emit(ev.EVENT_TASK_DONE,
                                       {"task_id": task_id, "out_dir": str(task_dir),
                                        "mode": "scrape", "warnings": []})
                    self._commit_credentials(http_client)
                    return
                extractor = ArticleExtractor(
                    self._cfg, http_client=http_client,
                )
                result = extractor.scrape_captured(
                    url, task_id, staging, html=capture["html"],
                    final_url=capture.get("final_url") or url,
                    on_event=lambda evp, p: self._emitter.emit(evp, p),
                )
            else:
                http_client = self._open_article_client(url, task_id, platform)
                extractor = ArticleExtractor(
                    self._cfg, http_client=http_client,
                )
                result = extractor.scrape(url, task_id, staging,
                                          on_event=lambda evp, p: self._emitter.emit(evp, p))
            manifest_id = derive_id(platform, url=sanitize_persisted_url(url))
            self._write_scrape_manifest(staging, manifest_id, result)
            task_dir, final_files = self._finalize_dir(staging, result.article.title, task_id,
                                                       manifest_id=manifest_id, overwrite_fixed=True)
            for f in final_files:
                kind = self._file_kind(f)
                store.add_file(task_id, kind, str(f), f.stat().st_size)
            store.set_out_dir(task_id, str(task_dir))
            store.set_status(task_id, STATUS_DONE)
            self._emitter.emit(ev.EVENT_TASK_DONE,
                               {"task_id": task_id, "out_dir": str(task_dir),
                                "mode": "scrape", "warnings": []})
            if http_client is not None:
                self._commit_credentials(http_client)
        except Exception as e:  # noqa: BLE001
            failure = self._mark_failed(store, task_id, e)
            self._emitter.emit(ev.EVENT_TASK_FAILED,
                               {**failure, "mode": "scrape"})
            log.exception("[%s] 文章抓取失败: %s", task_id, e)
        finally:
            if http_client is not None:
                http_client.close()
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)

    def _write_scrape_manifest(self, task_dir: Path, manifest_id: str, result) -> None:
        """文章抓取：元数据并入 manifest source（决策 4：meta.json 废弃）。

        article.md 已存在（echo §8 用户可能改过）→ 只更新 manifest，不覆盖正文。
        """
        art = result.article
        write_manifest(
            task_dir,
            manifest_id=manifest_id,
            source_type="article",
            source_url=sanitize_persisted_url(art.url),
            source_title=art.title,
            source_author=art.author,
            source_published_at=art.date,
            has_article=(task_dir / "article.md").is_file(),
        )

    @staticmethod
    def _file_kind(f: Path) -> str:
        name = f.name
        if name == "article.md":
            return "article"
        if name == "manifest.json":
            return "manifest"
        if f.parent.name == "images":
            return "image"
        if any(parent.name == "media" for parent in f.parents):
            return "video" if f.name.startswith(("original", "playback")) else "media_extra"
        return "article"

    def _cleanup_oss(self):
        """滚动清理：保留最近 max_batches 批 + TTL 双条件。"""
        if self._oss is None:
            return
        try:
            deleted = self._oss.cleanup(
                int(self._cfg["oss"].get("max_batches", 10)),
                int(self._cfg["oss"].get("ttl_seconds", 10800)),
            )
            if deleted:
                log.info("OSS 滚动清理删除 %d 个对象", len(deleted))
        except Exception as e:  # noqa: BLE001
            log.warning("OSS 清理失败: %s", e)

    def _refine_existing(self, task_id: str) -> dict:
        """内部实现：读取任务 ASR 结果 → 润色 → 落盘 article.md（echo 格式，幂等）。"""
        if self._refiner is None:
            raise RuntimeError("refine 未启用：请配置 secrets.toml [asr].api_key")
        store = self.store
        task = store.get_task(task_id)
        if not task:
            raise RuntimeError(f"任务不存在: {task_id}")
        asr = store.get_asr(task_id)
        if not asr or not asr.get("segments"):
            raise RuntimeError(f"任务 {task_id} 无 ASR 结果")
        outdir = Path(task["out_dir"]) if task.get("out_dir") else resolve_dir(self._cfg["paths"], "delivery") / task_id
        article_path = outdir / "article.md"
        if article_path.exists():
            raise RuntimeError(f"article.md 已存在，不覆盖（echo §8）: {article_path}")
        segments = asr["segments"]
        zh_threshold = float(self._cfg.get("refine", {}).get("zh_threshold", 0.2))
        is_zh = is_chinese_content(segments, zh_threshold)
        refined = self._refiner.refine(segments, chinese=not is_zh)
        article_path = self._refiner.save(refined, outdir)
        store.add_file(task_id, "article", str(article_path), article_path.stat().st_size)
        result = {
            "task_id": task_id,
            "article_path": str(article_path),
            "chunks": refined.chunks,
            "chars": refined.output_chars,
            "elapsed": round(refined.elapsed, 1),
            "chinese": is_zh,
        }
        return result
