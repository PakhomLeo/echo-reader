"""Optional, fully local ASR based on OpenVINO GenAI and sherpa-onnx.

Heavy third-party modules are deliberately imported only while ``transcribe`` is
running.  Importing MediaLab (including this module) therefore does not require
the ``local-asr`` extra or initialize a local inference runtime.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import math
import os
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid
import wave
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from .asr import Segment, TranscriptResult
from .audio import AudioProcessor

log = logging.getLogger("medialab.local_asr")

WHISPER_MODEL_ID = "OpenVINO/whisper-small-int8-ov"
WHISPER_REVISION = "8c593cfd15717c025c5ef99ea0b1879c88d03ed2"

SILERO_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
SILERO_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
SEGMENTATION_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEGMENTATION_SHA256 = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
EMBEDDING_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
EMBEDDING_SHA256 = "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"

_MODEL_MARKERS = (
    "openvino_encoder_model.xml", "openvino_encoder_model.bin",
    "openvino_decoder_model.xml", "openvino_decoder_model.bin",
    "openvino_tokenizer.xml", "openvino_tokenizer.bin",
    "openvino_detokenizer.xml", "openvino_detokenizer.bin",
    "config.json", "generation_config.json", "preprocessor_config.json",
    "tokenizer_config.json", "tokenizer.json", "special_tokens_map.json",
    "added_tokens.json", "vocab.json", "merges.txt", "normalizer.json",
    "openvino_config.json",
)
_MODEL_MANIFEST = "asset-manifest.json"
_MODEL_MANIFEST_VERSION = 1
_MODEL_DIRECTORY = "whisper-small-int8-ov"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_LONG_AUDIO_SECONDS = 10.0
_LONG_AUDIO_MIN_CHARACTER_DENSITY = 0.5
_MIN_TIMELINE_COVERAGE = 0.15


class TranscriptQualityError(RuntimeError):
    """Both deterministic transcript-quality attempts were catastrophic."""

    def __init__(self, message: str, attempts: list[dict] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


def transcript_quality_metrics(segments: list[Any], duration: float,
                               timestamp_mode: str) -> dict:
    """Calculate deterministic, serializable transcript quality metrics."""
    def value(item: Any, key: str, default: Any) -> Any:
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)

    nonblank = sum(len("".join(str(value(item, "text", "") or "").split())) for item in segments)
    ranges = sorted(
        (max(0.0, float(value(item, "start", 0))),
         min(duration, float(value(item, "end", 0))))
        for item in segments
    )
    covered = 0.0
    cursor_start = cursor_end = 0.0
    for start, end in ranges:
        if end <= start:
            continue
        if start > cursor_end:
            covered += max(0.0, cursor_end - cursor_start)
            cursor_start, cursor_end = start, end
        else:
            cursor_end = max(cursor_end, end)
    covered += max(0.0, cursor_end - cursor_start)
    coverage = covered / duration if duration > 0 else 0.0
    density = nonblank / duration if duration > 0 else 0.0
    catastrophic = not segments or nonblank == 0 or duration > _LONG_AUDIO_SECONDS and (
        nonblank < math.ceil(duration * _LONG_AUDIO_MIN_CHARACTER_DENSITY)
        or coverage < _MIN_TIMELINE_COVERAGE
        or density < _LONG_AUDIO_MIN_CHARACTER_DENSITY
    )
    return {
        "audio_duration": round(duration, 3),
        "nonblank_characters": nonblank,
        "segment_count": len(segments),
        "timeline_coverage": round(coverage, 4),
        "character_density": round(density, 4),
        "timestamp_mode": timestamp_mode,
        "accepted": not catastrophic,
    }


def _path_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class _InterProcessFileLock:
    """Advisory one-byte lock that works with native Windows and POSIX Python."""

    def __init__(self, path: Path):
        self.path = path
        self._stream: Any | None = None

    def __enter__(self) -> _InterProcessFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        except BaseException:
            stream.close()
            raise
        self._stream = stream
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


@contextmanager
def _asset_lock(path: Path) -> Iterator[None]:
    """Serialize one asset across both threads and application processes."""
    lock_path = path.parent / f".{path.name}.lock"
    with _path_lock(lock_path), _InterProcessFileLock(lock_path):
        yield


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _remove_asset_partial(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _cleanup_asset_partials(parent: Path, asset_name: str) -> None:
    """Remove only interrupted publications created for one exact asset name."""
    prefix = f".{asset_name}."
    for candidate in parent.iterdir():
        if candidate.name.startswith(prefix) and candidate.name.endswith(".part"):
            try:
                _remove_asset_partial(candidate)
            except OSError:
                log.warning("无法清理中断的本地 ASR 资产: %s", candidate, exc_info=True)


class _WhisperModelAssets:
    """Manifest-authoritative persistent storage for one Whisper snapshot."""

    def __init__(self, root: Path, model_id: str, revision: str):
        self.root = root
        self.model_id = model_id
        self.revision = revision
        self.model_dir = root / _MODEL_DIRECTORY

    def readiness(self) -> tuple[bool, str | None]:
        if not self.model_dir.exists():
            return False, "missing"
        with _asset_lock(self.model_dir):
            return self._validate_directory(self.model_dir)

    def ensure(
        self,
        *,
        auto_download: bool,
        downloader_factory: Callable[[], Callable[..., Any]] | None,
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        with _asset_lock(self.model_dir):
            _cleanup_asset_partials(self.root, self.model_dir.name)
            ready, reason = self._validate_directory(self.model_dir)
            if ready:
                return self.model_dir
            if not auto_download:
                raise RuntimeError(
                    f"本地 Whisper 模型不可用 ({reason}): {self.model_dir}；"
                    "启用 auto_download 或预先下载当前配置的模型"
                )
            if downloader_factory is None:
                raise RuntimeError("本地 Whisper 模型下载器未配置")
            temporary = Path(tempfile.mkdtemp(
                prefix=f".{self.model_dir.name}.", suffix=".part", dir=self.root
            ))
            try:
                downloader = downloader_factory()
                downloader(
                    repo_id=self.model_id,
                    revision=self.revision,
                    local_dir=str(temporary),
                    cache_dir=str(self.root / ".hf-cache"),
                    endpoint="https://huggingface.co",
                )
                missing = [name for name in _MODEL_MARKERS if not (temporary / name).is_file()]
                if missing:
                    raise RuntimeError(f"Whisper 模型下载不完整，缺少: {', '.join(missing)}")
                self._write_manifest(temporary)
                staged_ready, staged_reason = self._validate_directory(temporary)
                if not staged_ready:
                    raise RuntimeError(f"Whisper 模型暂存校验失败: {staged_reason}")
                self._publish(temporary)
            except Exception as exc:  # noqa: BLE001 - retain provider/publish cause
                if isinstance(exc, RuntimeError):
                    raise
                raise RuntimeError(f"Whisper 模型下载失败: {exc}") from exc
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
            return self.model_dir

    def _validate_directory(self, directory: Path) -> tuple[bool, str | None]:
        manifest_path = directory / _MODEL_MANIFEST
        if not manifest_path.is_file():
            return False, "manifest_missing"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False, "manifest_invalid"
        if not isinstance(manifest, dict) or manifest.get("schema_version") != _MODEL_MANIFEST_VERSION:
            return False, "manifest_invalid"
        if manifest.get("model_id") != self.model_id or manifest.get("revision") != self.revision:
            return False, "identity_mismatch"
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != set(_MODEL_MARKERS):
            return False, "files_mismatch"
        for name in _MODEL_MARKERS:
            metadata = files.get(name)
            if not isinstance(metadata, dict):
                return False, "manifest_invalid"
            size = metadata.get("size")
            expected_hash = metadata.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                return False, "manifest_invalid"
            if (
                not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or any(character not in "0123456789abcdef" for character in expected_hash)
            ):
                return False, "manifest_invalid"
            path = directory / name
            try:
                if not path.is_file():
                    return False, "file_missing"
                if path.stat().st_size != size:
                    return False, "file_size_mismatch"
                if _sha256(path) != expected_hash:
                    return False, "file_hash_mismatch"
            except OSError:
                return False, "file_unreadable"
        return True, None

    def _write_manifest(self, directory: Path) -> None:
        files = {
            name: {"size": (directory / name).stat().st_size, "sha256": _sha256(directory / name)}
            for name in _MODEL_MARKERS
        }
        manifest = {
            "schema_version": _MODEL_MANIFEST_VERSION,
            "model_id": self.model_id,
            "revision": self.revision,
            "files": files,
        }
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{_MODEL_MANIFEST}.", suffix=".part", dir=directory
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(directory / _MODEL_MANIFEST)
        finally:
            temporary.unlink(missing_ok=True)

    def _publish(self, temporary: Path) -> None:
        retired: Path | None = None
        if self.model_dir.exists():
            retired = self.root / (
                f".{self.model_dir.name}.retired-{uuid.uuid4().hex}.part"
            )
            os.replace(self.model_dir, retired)
        try:
            os.replace(temporary, self.model_dir)
        except BaseException:
            if retired is not None and retired.exists() and not self.model_dir.exists():
                os.replace(retired, self.model_dir)
            raise
        if retired is not None:
            try:
                _remove_asset_partial(retired)
            except OSError:
                log.warning("无法清理旧 Whisper 模型目录: %s", retired, exc_info=True)


def local_model_readiness(
    model_root: str | os.PathLike[str],
    *,
    model_id: str = WHISPER_MODEL_ID,
    revision: str = WHISPER_REVISION,
) -> tuple[bool, str | None]:
    """Return manifest-and-content readiness without downloading or publishing."""
    return _WhisperModelAssets(Path(model_root), str(model_id), str(revision)).readiness()


def _download(url: str, destination: Path, expected_sha256: str) -> Path:
    """Download one immutable resource atomically and verify its digest."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _asset_lock(destination):
        _cleanup_asset_partials(destination.parent, destination.name)
        if destination.is_file() and _sha256(destination) == expected_sha256:
            return destination
        if destination.exists():
            destination.unlink()
        fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            try:
                with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
            except Exception as exc:  # noqa: BLE001 - preserve actionable network cause
                raise RuntimeError(f"本地 ASR 模型下载失败: {url}: {exc}") from exc
            actual = _sha256(temporary)
            if actual != expected_sha256:
                raise RuntimeError(
                    f"本地 ASR 模型校验失败: {destination.name}; expected {expected_sha256}, got {actual}"
                )
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def _extract_segmentation(archive: Path, destination: Path) -> Path:
    """Safely and atomically extract the three allow-listed archive files."""
    model = destination / "model.int8.onnx"
    if model.is_file():
        return model
    with _asset_lock(destination):
        _cleanup_asset_partials(destination.parent, destination.name)
        if model.is_file():
            return model
        temporary = Path(tempfile.mkdtemp(
            prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
        ))
        wanted = {"model.int8.onnx", "LICENSE", "README"}
        found: set[str] = set()
        try:
            with tarfile.open(archive, mode="r:bz2") as bundle:
                for member in bundle.getmembers():
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                        raise RuntimeError(f"说话人模型压缩包包含不安全路径: {member.name}")
                    if not member.isfile() or path.name not in wanted:
                        continue
                    source = bundle.extractfile(member)
                    if source is None:
                        raise RuntimeError(f"无法读取说话人模型文件: {member.name}")
                    with (temporary / path.name).open("wb") as output:
                        shutil.copyfileobj(source, output)
                    found.add(path.name)
            if "model.int8.onnx" not in found:
                raise RuntimeError("说话人模型压缩包缺少 model.int8.onnx")
            if destination.exists():
                shutil.rmtree(destination)
            temporary.replace(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return model


class LocalAsrPipeline:
    """Local Whisper transcription with optional VAD and speaker diarization."""

    def __init__(self, cfg: dict, audio: AudioProcessor):
        self.cfg = cfg.get("local_asr", cfg)
        self.audio = audio
        self._whisper: Any | None = None
        self._ov_core: Any | None = None

    def transcribe(
        self,
        audio_path: Path,
        task_uuid: str,
        workdir: Path,
        outdir: Path,
        *,
        timestamp_mode: str = "model",
        diarization_enabled: bool = True,
        speaker_count: int = 0,
        speaker_label: str = "说话人",
    ) -> TranscriptResult:
        del task_uuid  # local inference has no remote task identifier
        if timestamp_mode not in {"model", "vad"}:
            raise ValueError("timestamp_mode 必须是 'model' 或 'vad'")
        if isinstance(speaker_count, bool) or not isinstance(speaker_count, int) or speaker_count < 0:
            raise ValueError("speaker_count 必须是非负整数")

        attempts = []
        for mode in (timestamp_mode, "model" if timestamp_mode == "vad" else "vad"):
            segments, duration = self.infer(
                audio_path, workdir, timestamp_mode=mode,
                diarization_enabled=diarization_enabled, speaker_count=speaker_count,
            )
            quality = transcript_quality_metrics(segments, duration, mode)
            attempts.append(quality)
            if quality["accepted"]:
                return self._save(segments, outdir, speaker_label)
        raise TranscriptQualityError(
            "local ASR quality gate rejected both timestamp strategies; "
            f"attempts={attempts!r}", attempts,
        )

    def infer(
        self,
        audio_path: Path,
        workdir: Path,
        *,
        timestamp_mode: str = "model",
        diarization_enabled: bool = True,
        speaker_count: int = 0,
    ) -> tuple[list[Segment], float]:
        """Run inference without writing publishable transcript artifacts."""
        if timestamp_mode not in {"model", "vad"}:
            raise ValueError("timestamp_mode 必须是 'model' 或 'vad'")
        if isinstance(speaker_count, bool) or not isinstance(speaker_count, int) or speaker_count < 0:
            raise ValueError("speaker_count 必须是非负整数")
        np, ov, ov_genai = self._load_asr_dependencies()
        workdir.mkdir(parents=True, exist_ok=True)
        normalized = workdir / "local_asr_16k_mono.wav"
        self.audio._run([  # AudioProcessor owns ffmpeg selection and error reporting.
            "-y", "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(normalized),
        ])
        samples = self._read_pcm(normalized, np)
        duration = len(samples) / 16000.0
        if not len(samples):
            log.info("本地 ASR 输入为空，跳过模型下载与初始化")
            return [], duration

        device = str(self.cfg.get("device", "GPU"))
        if device != "GPU":
            raise RuntimeError(f"本地 ASR 仅允许 device='GPU'，当前配置为 {device!r}")
        if self._ov_core is None:
            self._ov_core = ov.Core()
        devices = list(self._ov_core.available_devices)
        if not any(candidate == "GPU" or candidate.startswith("GPU.") for candidate in devices):
            raise RuntimeError(f"OpenVINO GPU 不可用（available_devices={devices}）；不会回退到 CPU")
        cache_dir = self._resolve(self.cfg.get("cache_dir", ".runtime/cache/openvino/local-asr"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        compile_config = {"CACHE_DIR": str(cache_dir), "PERFORMANCE_HINT": "LATENCY"}
        if self._whisper is None:
            model_dir = self._ensure_whisper_model()
            log.info("本地 ASR compile-once device=%s cache=%s", device, cache_dir)
            try:
                self._whisper = ov_genai.WhisperPipeline(str(model_dir), device, **compile_config)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"OpenVINO WhisperPipeline 无法在 GPU 上初始化: {exc}") from exc
        whisper = self._whisper

        if timestamp_mode == "model":
            segments = self._model_segments(whisper, samples)
        else:
            sherpa = self._load_sherpa_dependency()
            segments = self._vad_segments(whisper, samples, sherpa, np)
        segments = self._normalize_timeline(segments, duration)

        if diarization_enabled and segments:
            sherpa = self._load_sherpa_dependency()
            turns = self._diarize(samples, sherpa, speaker_count)
            self._assign_speakers(segments, turns)

        return segments, duration

    def diarize_accepted(
        self, workdir: Path, segments: list[dict], speaker_count: int,
    ) -> list[Segment]:
        """Diarize one already accepted candidate without rerunning Whisper."""
        normalized = workdir / "local_asr_16k_mono.wav"
        if not normalized.is_file():
            raise RuntimeError("normalized ASR audio is unavailable for diarization")
        np, _ov, _ov_genai = self._load_asr_dependencies()
        samples = self._read_pcm(normalized, np)
        output = [
            Segment(
                text=str(item.get("text") or ""),
                start=float(item.get("start") or 0),
                end=float(item.get("end") or 0),
            )
            for item in segments
        ]
        if output:
            sherpa = self._load_sherpa_dependency()
            self._assign_speakers(output, self._diarize(samples, sherpa, speaker_count))
        return output

    @staticmethod
    def _save(segments: list[Segment], outdir: Path, speaker_label: str) -> TranscriptResult:
        from .publishing import detect_language, write_transcript

        outdir.mkdir(parents=True, exist_ok=True)
        result = TranscriptResult(segments=segments)
        serialized = [segment.to_dict() for segment in segments]
        result.transcript_path = write_transcript(
            outdir, serialized, detect_language(serialized), speaker_label
        )
        return result

    @staticmethod
    def _normalize_timeline(segments: list[Segment], duration: float) -> list[Segment]:
        """Sort and clamp segments; reject non-finite values and drop empty ranges."""
        if not math.isfinite(duration) or duration < 0:
            raise RuntimeError(f"音频时长无效: {duration!r}")
        output: list[Segment] = []
        for segment in segments:
            start = float(segment.start)
            end = float(segment.end)
            if not math.isfinite(start) or not math.isfinite(end):
                raise RuntimeError(
                    f"ASR 返回非有限时间戳: start={start!r}, end={end!r}"
                )
            start = min(duration, max(0.0, start))
            end = min(duration, max(0.0, end))
            if end <= start:
                log.warning("丢弃无有效时长的 ASR 段: %.3f-%.3f %r", start, end, segment.text)
                continue
            segment.start = start
            segment.end = end
            output.append(segment)
        output.sort(key=lambda item: (item.start, item.end))
        return output

    @staticmethod
    def _load_asr_dependencies() -> tuple[Any, Any, Any]:
        try:
            import numpy as np
            import openvino as ov
            import openvino_genai as ov_genai
        except ImportError as exc:
            raise RuntimeError(
                "本地 ASR 依赖未安装；请运行 `uv sync --extra local-asr`"
            ) from exc
        return np, ov, ov_genai

    @staticmethod
    def _load_sherpa_dependency() -> Any:
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "VAD/说话人分离需要 sherpa-onnx；请运行 `uv sync --extra local-asr`"
            ) from exc
        return sherpa_onnx

    def _resolve(self, value: str | os.PathLike[str]) -> Path:
        path = Path(value)
        return path if path.is_absolute() else Path.cwd() / path

    @staticmethod
    def _read_pcm(path: Path, np: Any) -> Any:
        with wave.open(str(path), "rb") as stream:
            if stream.getnchannels() != 1 or stream.getframerate() != 16000 or stream.getsampwidth() != 2:
                raise RuntimeError("ffmpeg 未生成 16 kHz mono PCM16 WAV")
            data = stream.readframes(stream.getnframes())
        return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

    def _ensure_whisper_model(self) -> Path:
        root = self._resolve(self.cfg.get("model_dir", ".runtime/models/local-asr"))
        assets = _WhisperModelAssets(
            root,
            str(self.cfg.get("model_id", WHISPER_MODEL_ID)),
            str(self.cfg.get("revision", WHISPER_REVISION)),
        )

        def downloader_factory() -> Callable[..., Any]:
            try:
                from huggingface_hub import snapshot_download
            except ImportError as exc:
                raise RuntimeError(
                    "下载本地 Whisper 模型需要 huggingface-hub；请安装 local-asr extra"
                ) from exc
            return snapshot_download

        return assets.ensure(
            auto_download=bool(self.cfg.get("auto_download", True)),
            downloader_factory=downloader_factory,
        )

    @staticmethod
    def _model_segments(whisper: Any, samples: Any) -> list[Segment]:
        result = whisper.generate(samples, return_timestamps=True)
        chunks = getattr(result, "chunks", None) or []
        segments = [
            Segment(str(chunk.text).strip(), float(chunk.start_ts), float(chunk.end_ts))
            for chunk in chunks if str(getattr(chunk, "text", "")).strip()
        ]
        if not segments and LocalAsrPipeline._result_text(result).strip():
            raise RuntimeError("OpenVINO Whisper 未返回模型时间戳 chunks")
        return segments

    def _ensure_silero(self) -> Path:
        root = self._resolve(self.cfg.get("model_dir", ".runtime/models/local-asr")) / "sherpa-onnx"
        path = root / "silero_vad.onnx"
        if path.is_file() and _sha256(path) == SILERO_SHA256:
            return path
        if not self.cfg.get("auto_download", True):
            raise RuntimeError(f"Silero VAD 模型不存在或校验失败: {path}")
        return _download(SILERO_URL, path, SILERO_SHA256)

    def _vad_segments(self, whisper: Any, samples: Any, sherpa: Any, np: Any) -> list[Segment]:
        threshold = float(self.cfg.get("vad_threshold", 0.5))
        min_silence = self._positive_float("vad_min_silence", 0.5)
        min_speech = self._positive_float("vad_min_speech", 0.25)
        max_speech = self._positive_float("vad_max_speech", 30.0)
        window = self._positive_int("vad_window_size", 512)
        buffer_seconds = self._positive_float("vad_buffer_seconds", 120)
        threads = self._positive_int("vad_num_threads", 1)
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("local_asr.vad_threshold 必须是 0 到 1 的有限数")
        config = sherpa.VadModelConfig()
        config.silero_vad.model = str(self._ensure_silero())
        config.silero_vad.threshold = threshold
        config.silero_vad.min_silence_duration = min_silence
        config.silero_vad.min_speech_duration = min_speech
        config.silero_vad.max_speech_duration = max_speech
        config.silero_vad.window_size = window
        config.sample_rate = 16000
        config.num_threads = threads
        vad = sherpa.VoiceActivityDetector(config, buffer_seconds)
        speech: list[tuple[int, Any]] = []

        def drain() -> None:
            while not vad.empty():
                item = vad.front
                speech.append((int(item.start), np.asarray(item.samples, dtype=np.float32)))
                vad.pop()

        for start in range(0, len(samples), window):
            vad.accept_waveform(samples[start:start + window])
            drain()
        vad.flush()
        drain()
        output: list[Segment] = []
        for start_sample, clip in speech:
            result = whisper.generate(clip, return_timestamps=False)
            text = self._result_text(result).strip()
            if text:
                start = start_sample / 16000.0
                output.append(Segment(text, start, start + len(clip) / 16000.0))
        return output

    def _positive_int(self, key: str, default: int) -> int:
        value = self.cfg.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"local_asr.{key} 必须是正整数")
        return value

    def _positive_float(self, key: str, default: float) -> float:
        try:
            value = float(self.cfg.get(key, default))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"local_asr.{key} 必须是正有限数") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"local_asr.{key} 必须是正有限数")
        return value

    @staticmethod
    def _result_text(result: Any) -> str:
        texts = getattr(result, "texts", None)
        if isinstance(texts, (list, tuple)):
            return " ".join(str(value) for value in texts)
        value = texts or getattr(result, "text", "")
        if not value and isinstance(result, str):
            value = result
        return str(value or "")

    def _ensure_diarization_models(self) -> tuple[Path, Path]:
        root = self._resolve(self.cfg.get("model_dir", ".runtime/models/local-asr")) / "sherpa-onnx"
        segmentation_dir = root / "pyannote-segmentation-3.0"
        segmentation_model = segmentation_dir / "model.int8.onnx"
        embedding = root / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
        if not segmentation_model.is_file():
            if not self.cfg.get("auto_download", True):
                raise RuntimeError(f"说话人 segmentation 模型不存在: {segmentation_model}")
            archive = _download(SEGMENTATION_URL, root / "pyannote-segmentation-3.0.tar.bz2", SEGMENTATION_SHA256)
            segmentation_model = _extract_segmentation(archive, segmentation_dir)
        if not embedding.is_file() or _sha256(embedding) != EMBEDDING_SHA256:
            if not self.cfg.get("auto_download", True):
                raise RuntimeError(f"说话人 embedding 模型不存在或校验失败: {embedding}")
            embedding = _download(EMBEDDING_URL, embedding, EMBEDDING_SHA256)
        return segmentation_model, embedding

    def _diarize(self, samples: Any, sherpa: Any, speaker_count: int) -> list[tuple[float, float, int]]:
        segmentation, embedding = self._ensure_diarization_models()
        threads = self._positive_int("diarization_num_threads", 1)
        config = sherpa.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(segmentation)
                ),
                num_threads=threads,
            ),
            embedding=sherpa.SpeakerEmbeddingExtractorConfig(
                model=str(embedding), num_threads=threads
            ),
            clustering=sherpa.FastClusteringConfig(
                num_clusters=speaker_count if speaker_count > 0 else -1,
                threshold=float(self.cfg.get("cluster_threshold", 0.9)),
            ),
            min_duration_on=float(self.cfg.get("diarization_min_duration_on", 0.3)),
            min_duration_off=float(self.cfg.get("diarization_min_duration_off", 0.5)),
        )
        if not config.validate():
            raise RuntimeError("说话人分离配置无效；请检查 segmentation/embedding 模型文件")
        diarizer = sherpa.OfflineSpeakerDiarization(config)
        return [
            (float(item.start), float(item.end), int(item.speaker))
            for item in diarizer.process(samples).sort_by_start_time()
        ]

    @staticmethod
    def _assign_speakers(segments: list[Segment], turns: list[tuple[float, float, int]]) -> None:
        raw_speakers: list[int | None] = []
        for segment in segments:
            best_overlap = 0.0
            best_speaker: int | None = None
            for start, end, speaker in turns:
                overlap = max(0.0, min(segment.end, end) - max(segment.start, start))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = speaker
            raw_speakers.append(best_speaker)
        dense_ids: dict[int, int] = {}
        for segment, raw_speaker in zip(segments, raw_speakers):
            if raw_speaker is None:
                segment.speaker_id = None
                continue
            if raw_speaker not in dense_ids:
                dense_ids[raw_speaker] = len(dense_ids) + 1
            segment.speaker_id = dense_ids[raw_speaker]
