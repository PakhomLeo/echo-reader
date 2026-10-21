"""Optional offline text-to-speech with Kokoro (kokoro-onnx), ported from Echo.

Install with the ``tts`` extra and download the two model files (not shipped in git)::

    uv sync --extra tts
    uv run python scripts/download_kokoro_models.py

Without the extra or the model files :func:`probe_tts` reports why the feature is
unavailable and nothing else is affected.
"""

from __future__ import annotations

import importlib.util
import os
import re
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"
MODEL_URLS = {
    MODEL_FILE: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    VOICES_FILE: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}
MODEL_MIN_BYTES = {MODEL_FILE: 300_000_000, VOICES_FILE: 5_000_000}
MODELS_ENV = "ECHO_TTS_MODELS_PATH"
DEFAULT_MODEL_DIR = ".runtime/models/kokoro"
MAX_TEXT_CHARS = 20_000
REQUIRED_MODULES = ("kokoro_onnx", "onnxruntime", "numpy")

_MODELS: dict[tuple[str, str], Any] = {}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class TtsSettings:
    enabled: bool
    model_dir: Path
    voice: str
    voice_zh: str
    speed: float

    @property
    def model_path(self) -> Path:
        return self.model_dir / MODEL_FILE

    @property
    def voices_path(self) -> Path:
        return self.model_dir / VOICES_FILE


def tts_settings(cfg: dict | None) -> TtsSettings:
    section = dict((cfg or {}).get("tts") or {})
    raw_dir = os.environ.get(MODELS_ENV, "").strip() or str(section.get("model_dir") or DEFAULT_MODEL_DIR)
    model_dir = Path(os.path.expanduser(raw_dir))
    if not model_dir.is_absolute():
        model_dir = Path.cwd() / model_dir
    return TtsSettings(
        enabled=bool(section.get("enabled", True)),
        model_dir=model_dir,
        voice=str(section.get("voice") or "af_heart"),
        voice_zh=str(section.get("voice_zh") or "zf_xiaoxiao"),
        speed=float(section.get("speed") or 1.0),
    )


def missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def probe_tts(cfg: dict | None) -> dict[str, Any]:
    """Return ``{"available": bool, "reason": str | None, ...}`` without loading the model."""
    settings = tts_settings(cfg)
    info: dict[str, Any] = {"engine": "kokoro", "available": False, "reason": None,
                            "model_dir": str(settings.model_dir)}
    if not settings.enabled:
        info["reason"] = "disabled"
        return info
    missing = missing_modules()
    if missing:
        info["reason"] = "dependencies_missing"
        info["missing"] = missing
        return info
    absent = [name for name in (MODEL_FILE, VOICES_FILE) if not (settings.model_dir / name).is_file()]
    if absent:
        info["reason"] = "model_missing"
        info["missing"] = absent
        return info
    info["available"] = True
    return info


_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def markdown_to_speech_text(markdown: str) -> str:
    """Strip Markdown syntax so only readable prose is spoken (images are skipped)."""
    text = _IMAGE.sub("", markdown)
    text = _LINK.sub(r"\1", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~]{1,3}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def detect_language(text: str) -> str:
    """Kokoro language code: Mandarin when CJK characters dominate, else US English."""
    sample = text[:2000]
    cjk = len(_CJK.findall(sample))
    letters = sum(ch.isalpha() for ch in sample)
    return "cmn" if letters and cjk / letters > 0.3 else "en-us"


def _patch_kokoro(kokoro_onnx: Any, np: Any) -> None:
    """Fix float speed for ``input_ids`` model exports (same fix as the original Echo worker).

    kokoro-onnx 0.5 sends ``speed`` as int32 to "newer" exports such as the
    onnx-community Kokoro-82M build. The official ``kokoro-v1.0.onnx`` (``tokens`` input)
    goes through the unmodified upstream code path.
    """
    cls = kokoro_onnx.Kokoro
    if getattr(cls, "_echo_style_patch", False) or not hasattr(cls, "_create_audio"):
        return
    original = cls._create_audio

    def _create_audio(self, phonemes, voice, speed):
        if "input_ids" not in [i.name for i in self.sess.get_inputs()]:
            return original(self, phonemes, voice, speed)
        tokens = np.array(self.tokenizer.tokenize(phonemes[:510]), dtype=np.int64)
        style = np.expand_dims(np.array(voice[len(tokens)], dtype=np.float32), 0)
        audio = self.sess.run(None, {
            "input_ids": np.array([[0, *tokens.tolist(), 0]], dtype=np.int64),
            "style": style,
            "speed": np.array([speed], dtype=np.float32),
        })[0]
        return audio, kokoro_onnx.SAMPLE_RATE

    cls._create_audio = _create_audio
    cls._echo_style_patch = True


def _model(settings: TtsSettings) -> Any:
    import numpy as np
    import kokoro_onnx

    _patch_kokoro(kokoro_onnx, np)
    key = (str(settings.model_path), str(settings.voices_path))
    with _LOCK:
        model = _MODELS.get(key)
        if model is None:
            model = kokoro_onnx.Kokoro(*key)
            _MODELS[key] = model
    return model


def list_voices(cfg: dict | None) -> list[str]:
    import numpy as np

    settings = tts_settings(cfg)
    with np.load(settings.voices_path) as voices:
        return sorted(voices.files)


def synthesize(text: str, output_path: Path, cfg: dict | None, *, voice: str | None = None,
               speed: float | None = None, language: str | None = None) -> dict[str, Any]:
    """Synthesize ``text`` to a mono 16-bit WAV file."""
    import numpy as np

    settings = tts_settings(cfg)
    text = text.strip()
    if not text:
        raise ValueError("nothing to read aloud")
    truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]
    lang = language or detect_language(text)
    chosen_voice = voice or (settings.voice_zh if lang == "cmn" else settings.voice)
    model = _model(settings)
    samples, sample_rate = model.create(text, voice=chosen_voice, speed=speed or settings.speed, lang=lang)
    waveform = np.asarray(samples, dtype=np.float32).squeeze()
    pcm = (np.clip(waveform, -1.0, 1.0) * 32767.0).astype("<i2")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(output_path.name + ".part")
    with wave.open(str(partial), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(pcm.tobytes())
    os.replace(partial, output_path)
    return {
        "output_path": str(output_path),
        "sample_rate": int(sample_rate),
        "duration_ms": round(len(waveform) / sample_rate * 1000),
        "voice": chosen_voice,
        "language": lang,
        "truncated": truncated,
    }
