"""Optional local ASR engine based on FunASR + Fun-ASR-Nano.

Select it with ``[local_asr].engine = "funasr_nano"`` and install the ``funasr`` extra.
Like :mod:`medialab.local_asr`, every heavy dependency (``funasr``, ``torch``) is imported
lazily inside :meth:`FunAsrNanoPipeline.infer`, so importing MediaLab never requires them.

Model weights are downloaded by FunASR itself (Hugging Face for the main model, ModelScope
for VAD / speaker models by default) into the user's model cache on first use; they are
never stored in the repository or in the content library.
"""

from __future__ import annotations

import importlib.util
import logging
import math
import threading
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

from .asr import Segment
from .audio import AudioProcessor

log = logging.getLogger("medialab.funasr_asr")

ENGINE_NAME = "funasr_nano"
DEFAULT_MODEL = "FunAudioLLM/Fun-ASR-Nano-2512"
REQUIRED_MODULES = ("funasr", "torch")


@lru_cache(maxsize=1)
def probe_funasr_runtime() -> tuple[str, ...]:
    """Return the missing Python modules for the FunASR engine (empty = ready)."""
    return tuple(name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None)


def detect_torch_device(preferred: str = "auto") -> str:
    """Pick a torch device: explicit value, else CUDA → Apple MPS → Intel XPU → CPU."""
    if preferred and preferred != "auto":
        return preferred
    try:
        import torch
    except ImportError:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    try:
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass
    xpu = getattr(torch, "xpu", None)
    try:
        if xpu is not None and xpu.is_available():
            return "xpu"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _ms_to_seconds(value: Any) -> float | None:
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) else None


def _speaker_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_funasr_result(result: Any, duration: float, *, keep_speakers: bool) -> list[Segment]:
    """Convert FunASR ``generate()`` output into MediaLab segments on the audio timeline."""
    item = result[0] if isinstance(result, list) and result else result
    if not isinstance(item, dict):
        text = str(item or "").strip()
        return [Segment(text=text, start=0.0, end=duration)] if text and duration > 0 else []
    segments: list[Segment] = []
    for sentence in item.get("sentence_info") or []:
        if not isinstance(sentence, dict):
            continue
        text = str(sentence.get("text") or sentence.get("sentence") or "").strip()
        start = _ms_to_seconds(sentence.get("start"))
        end = _ms_to_seconds(sentence.get("end"))
        if not text or start is None or end is None:
            continue
        start = min(duration, max(0.0, start))
        end = min(duration, max(0.0, end))
        if end <= start:
            continue
        speaker = _speaker_id(sentence.get("spk")) if keep_speakers else None
        segments.append(Segment(text=text, start=start, end=end, speaker_id=speaker))
    if not segments:
        text = str(item.get("text") or "").strip()
        if text and duration > 0:
            segments.append(Segment(text=text, start=0.0, end=duration))
    segments.sort(key=lambda s: (s.start, s.end))
    return segments


class FunAsrNanoPipeline:
    """Local Fun-ASR-Nano transcription with FunASR's VAD and optional CAM++ diarization."""

    def __init__(self, cfg: dict, audio: AudioProcessor):
        self.cfg = cfg.get("local_asr", cfg)
        self.audio = audio
        self._models: dict[bool, Any] = {}
        self._lock = threading.RLock()

    def _funasr_cfg(self) -> dict:
        return dict(self.cfg.get("funasr") or {})

    def _model_kwargs(self, diarization: bool, device: str) -> dict:
        fcfg = self._funasr_cfg()
        kwargs: dict[str, Any] = {
            "model": fcfg.get("model") or DEFAULT_MODEL,
            # FunASR >= 1.4 ships the FunASRNano model class, so no model-repo code is needed.
            # trust_remote_code=True would let FunASR pip-install a model repo's
            # requirements.txt and import arbitrary Python; keep it an explicit opt-in.
            "trust_remote_code": fcfg.get("trust_remote_code", False) is True,
            "vad_model": fcfg.get("vad_model", "fsmn-vad"),
            "vad_kwargs": {"max_single_segment_time": int(fcfg.get("vad_max_single_segment_time", 10000))},
            "device": device,
            "disable_update": True,
        }
        if fcfg.get("model_revision"):
            kwargs["model_revision"] = str(fcfg["model_revision"])
        hub = fcfg.get("hub", "hf")
        auxiliary_hub = fcfg.get("auxiliary_hub", "ms")
        if hub:
            kwargs["hub"] = hub
        if auxiliary_hub:
            kwargs["vad_kwargs"]["hub"] = auxiliary_hub
        if fcfg.get("punc_model"):
            kwargs["punc_model"] = fcfg["punc_model"]
            if auxiliary_hub:
                kwargs["punc_kwargs"] = {"hub": auxiliary_hub}
        if diarization:
            kwargs["spk_model"] = fcfg.get("spk_model", "cam++")
            kwargs["spk_mode"] = fcfg.get("spk_mode", "vad_segment")
            if auxiliary_hub:
                kwargs["spk_kwargs"] = {"hub": auxiliary_hub}
        return kwargs

    def _load_model(self, diarization: bool) -> Any:
        with self._lock:
            if diarization in self._models:
                return self._models[diarization]
            try:
                from funasr import AutoModel
            except ImportError as exc:
                raise RuntimeError(
                    f"FunASR 依赖缺失（{exc.name or 'funasr'}）；请安装 medialab[funasr]"
                ) from exc
            device = detect_torch_device(str(self._funasr_cfg().get("device", "auto")))
            kwargs = self._model_kwargs(diarization, device)
            log.info("FunASR 加载 model=%s device=%s diarization=%s", kwargs["model"], device, diarization)
            try:
                model = AutoModel(**kwargs)
            except Exception:
                if device == "cpu":
                    raise
                log.warning("FunASR 在 %s 上初始化失败，回退 CPU", device)
                kwargs["device"] = "cpu"
                model = AutoModel(**kwargs)
            self._models[diarization] = model
            return model

    def infer(
        self,
        audio_path: Path,
        workdir: Path,
        *,
        timestamp_mode: str = "vad",
        diarization_enabled: bool = True,
        speaker_count: int = 0,
    ) -> tuple[list[Segment], float]:
        """Transcribe one audio file; FunASR always segments with its own VAD."""
        del timestamp_mode, speaker_count  # FunASR decides segmentation and speaker count
        workdir.mkdir(parents=True, exist_ok=True)
        normalized = workdir / "funasr_16k_mono.wav"
        self.audio._run([
            "-y", "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(normalized),
        ])
        with wave.open(str(normalized), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 16000
        duration = frames / float(rate)
        if frames == 0:
            return [], duration
        fcfg = self._funasr_cfg()
        args: dict[str, Any] = {"input": str(normalized), "cache": {}, "batch_size": 1, "itn": True}
        language = fcfg.get("language", "auto")
        if language and language != "auto":
            args["language"] = language
        hotwords = fcfg.get("hotwords") or []
        if hotwords:
            args["hotwords"] = list(hotwords)
        with self._lock:
            result = self._load_model(bool(diarization_enabled)).generate(**args)
        return normalize_funasr_result(result, duration, keep_speakers=bool(diarization_enabled)), duration

    def diarize_accepted(self, workdir: Path, segments: list[dict], speaker_count: int) -> list[Segment]:
        """Speakers are assigned during :meth:`infer`; keep them as-is."""
        del workdir, speaker_count
        return [
            Segment(
                text=str(item.get("text") or ""),
                start=float(item.get("start") or 0),
                end=float(item.get("end") or 0),
                speaker_id=_speaker_id(item.get("speaker_id")),
            )
            for item in segments
        ]
