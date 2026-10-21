"""Optional Fun-ASR-Nano engine; runs without funasr/torch installed."""

from __future__ import annotations

import sys
import types
import wave
from pathlib import Path
from unittest import mock

import pytest

from medialab import funasr_asr
from medialab.funasr_asr import FunAsrNanoPipeline, normalize_funasr_result
from medialab.service import DownloadService, _probe_local_runtime, local_asr_engine


class FakeAudio:
    """Writes one second of 16 kHz mono silence instead of running ffmpeg."""

    def __init__(self, seconds: float = 2.0):
        self.seconds = seconds
        self.calls: list[list[str]] = []

    def _run(self, args: list[str]) -> None:
        self.calls.append(args)
        with wave.open(args[-1], "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\0\0" * int(16000 * self.seconds))


class FakeAutoModel:
    instances: list["FakeAutoModel"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.generate_calls: list[dict] = []
        FakeAutoModel.instances.append(self)

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return [{
            "text": "你好 世界",
            "sentence_info": [
                {"text": "世界", "start": 900, "end": 1800, "spk": 1},
                {"text": "你好", "start": 0, "end": 800, "spk": 0},
                {"text": "", "start": 1800, "end": 1900, "spk": 0},
                {"text": "越界", "start": 1900, "end": 9000, "spk": "x"},
            ],
        }]


@pytest.fixture()
def fake_funasr(monkeypatch):
    FakeAutoModel.instances.clear()
    module = types.ModuleType("funasr")
    module.AutoModel = FakeAutoModel
    monkeypatch.setitem(sys.modules, "funasr", module)
    monkeypatch.setattr(funasr_asr, "detect_torch_device", lambda preferred="auto": "cpu")
    return module


def test_import_does_not_require_funasr():
    assert "funasr" not in sys.modules or isinstance(sys.modules["funasr"], types.ModuleType)
    assert funasr_asr.ENGINE_NAME == "funasr_nano"


def test_normalize_sorts_clamps_and_maps_speakers():
    result = FakeAutoModel().generate()
    segments = normalize_funasr_result(result, 2.0, keep_speakers=True)
    assert [s.text for s in segments] == ["你好", "世界", "越界"]
    assert segments[0].speaker_id == 0 and segments[1].speaker_id == 1
    assert segments[2].speaker_id is None and segments[2].end == 2.0
    plain = normalize_funasr_result(result, 2.0, keep_speakers=False)
    assert all(s.speaker_id is None for s in plain)


def test_normalize_falls_back_to_plain_text():
    segments = normalize_funasr_result([{"text": "整段文本"}], 3.5, keep_speakers=True)
    assert [(s.text, s.start, s.end) for s in segments] == [("整段文本", 0.0, 3.5)]
    assert normalize_funasr_result("", 3.5, keep_speakers=True) == []


def test_pipeline_infer_with_and_without_diarization(tmp_path: Path, fake_funasr):
    audio = FakeAudio()
    pipeline = FunAsrNanoPipeline({"local_asr": {"funasr": {"hotwords": ["Echo"], "language": "zh"}}}, audio)
    segments, duration = pipeline.infer(tmp_path / "in.m4a", tmp_path / "work", diarization_enabled=True)
    assert duration == pytest.approx(2.0)
    assert [s.speaker_id for s in segments][:2] == [0, 1]
    model = FakeAutoModel.instances[-1]
    assert model.kwargs["model"] == "FunAudioLLM/Fun-ASR-Nano-2512"
    assert model.kwargs["spk_model"] == "cam++"
    assert model.kwargs["vad_kwargs"]["hub"] == "ms" and model.kwargs["hub"] == "hf"
    # Remote model code is never executed unless explicitly enabled.
    assert model.kwargs["trust_remote_code"] is False
    assert "model_revision" not in model.kwargs
    assert model.generate_calls[-1]["hotwords"] == ["Echo"]
    assert model.generate_calls[-1]["language"] == "zh"

    segments, _ = pipeline.infer(tmp_path / "in.m4a", tmp_path / "work", diarization_enabled=False)
    assert all(s.speaker_id is None for s in segments)
    assert "spk_model" not in FakeAutoModel.instances[-1].kwargs
    # Models are cached per diarization flag.
    pipeline.infer(tmp_path / "in.m4a", tmp_path / "work", diarization_enabled=True)
    assert len(FakeAutoModel.instances) == 2


def test_diarize_accepted_keeps_infer_speakers():
    pipeline = FunAsrNanoPipeline({}, FakeAudio())
    out = pipeline.diarize_accepted(Path("."), [{"text": "a", "start": 0, "end": 1, "speaker_id": 2}], 0)
    assert out[0].speaker_id == 2 and out[0].end == 1.0


def test_missing_dependency_is_explicit(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(sys.modules, "funasr", None)
    pipeline = FunAsrNanoPipeline({}, FakeAudio())
    with pytest.raises(RuntimeError, match="medialab\\[funasr\\]"):
        pipeline.infer(tmp_path / "in.m4a", tmp_path / "work")


def test_engine_selection_and_capabilities(tmp_path: Path):
    assert local_asr_engine({}) == "openvino_whisper"
    assert local_asr_engine({"local_asr": {"engine": "FunASR_Nano"}}) == "funasr_nano"
    with pytest.raises(ValueError):
        local_asr_engine({"local_asr": {"engine": "other"}})

    _probe_local_runtime.cache_clear()
    funasr_asr.probe_funasr_runtime.cache_clear()
    svc = DownloadService(config={"local_asr": {"engine": "funasr_nano"}})
    svc._audio = object()
    with mock.patch.object(funasr_asr, "probe_funasr_runtime", return_value=("funasr",)):
        local = svc.capabilities()["transcription"]["local"]
    assert local["engine"] == "funasr_nano"
    assert local["available"] is False and local["blocker"] == "dependencies_unavailable"
    with mock.patch.object(funasr_asr, "probe_funasr_runtime", return_value=()):
        local = svc.capabilities()["transcription"]["local"]
        plan = svc.plan_import("https://www.bilibili.com/video/BV1xx", asr_backend="local")
    assert local["available"] is True and local["blocker"] is None
    assert plan["mode"] == "transcribe" and plan["asr_backend"] == "local"

    bad = DownloadService(config={"local_asr": {"engine": "nope"}})
    bad._audio = object()
    assert bad.capabilities()["transcription"]["local"]["blocker"] == "invalid_engine"


def test_worker_uses_funasr_pipeline(tmp_path: Path, fake_funasr, monkeypatch):
    from medialab import service

    monkeypatch.setattr(service, "_LOCAL_WORKER_PIPELINE", None)
    monkeypatch.setattr(service, "AudioProcessor", lambda ffmpeg: FakeAudio())
    out = service._run_local_asr_worker(
        {"local_asr": {"engine": "funasr_nano"}}, "ffmpeg", str(tmp_path / "a.m4a"),
        str(tmp_path / "w"), "vad", True, 0,
    )
    assert isinstance(service._LOCAL_WORKER_PIPELINE, FunAsrNanoPipeline)
    assert out["segments"][0] == {"text": "你好", "start": 0.0, "end": 0.8, "speaker_id": 0}
    monkeypatch.setattr(service, "_LOCAL_WORKER_PIPELINE", None)
