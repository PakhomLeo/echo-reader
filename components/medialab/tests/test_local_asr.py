from __future__ import annotations

import io
import json
import multiprocessing
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from medialab.asr import Segment
from medialab.local_asr import (
    WHISPER_MODEL_ID,
    _MODEL_MARKERS,
    _MODEL_MANIFEST,
    _WhisperModelAssets,
    LocalAsrPipeline,
    TranscriptQualityError,
    _download,
    _extract_segmentation,
    local_model_readiness,
)


class FakeAudio:
    def _run(self, args: list[str]) -> None:
        Path(args[-1]).write_bytes(b"fake wav")


class FakeArray(list):
    pass


class FakeNumpy:
    float32 = "float32"

    @staticmethod
    def asarray(value, dtype=None):
        del dtype
        return FakeArray(value)


def _write_fake_snapshot(local_dir: str, content: bytes = b"model") -> None:
    directory = Path(local_dir)
    for name in _MODEL_MARKERS:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content + name.encode("utf-8"))


def _concurrent_model_ensure(
    model_root: str,
    counter: Any,
    release_download: Any,
) -> None:
    assets = _WhisperModelAssets(Path(model_root), WHISPER_MODEL_ID, "concurrent-revision")

    def download(**kwargs: Any) -> None:
        release_download.wait(10)
        with counter.get_lock():
            counter.value += 1
        time.sleep(0.2)
        _write_fake_snapshot(kwargs["local_dir"], b"concurrent")

    assets.ensure(auto_download=True, downloader_factory=lambda: download)


def _pipeline(tmp_path: Path, **overrides) -> LocalAsrPipeline:
    cfg = {
        "model_dir": str(tmp_path / "models"),
        "cache_dir": str(tmp_path / "cache"),
        "auto_download": False,
        **overrides,
    }
    return LocalAsrPipeline(cfg, FakeAudio())


def test_import_does_not_load_optional_runtimes() -> None:
    # The imports above loaded local_asr, but none of its optional runtimes.
    assert "openvino_genai" not in sys.modules
    assert "sherpa_onnx" not in sys.modules


@pytest.mark.parametrize("mode", ["", "word", "MODEL"])
def test_invalid_timestamp_mode_fails_before_dependencies(tmp_path: Path, mode: str) -> None:
    with pytest.raises(ValueError, match="timestamp_mode"):
        _pipeline(tmp_path).transcribe(Path("x"), "id", tmp_path / "w", tmp_path / "o", timestamp_mode=mode)


@pytest.mark.parametrize("count", [-1, 1.5, True])
def test_invalid_speaker_count_fails_before_dependencies(tmp_path: Path, count) -> None:
    with pytest.raises(ValueError, match="speaker_count"):
        _pipeline(tmp_path).transcribe(Path("x"), "id", tmp_path / "w", tmp_path / "o", speaker_count=count)


def test_model_timestamp_chunks() -> None:
    result = SimpleNamespace(chunks=[
        SimpleNamespace(text=" hello ", start_ts=0.25, end_ts=1.5),
        SimpleNamespace(text="世界", start_ts=1.5, end_ts=2.0),
    ])
    whisper = SimpleNamespace(generate=lambda samples, **kwargs: result)
    segments = LocalAsrPipeline._model_segments(whisper, FakeArray([0]))
    assert [(s.text, s.start, s.end) for s in segments] == [
        ("hello", 0.25, 1.5), ("世界", 1.5, 2.0)
    ]


def test_model_timestamp_requires_native_chunks() -> None:
    whisper = SimpleNamespace(generate=lambda samples, **kwargs: SimpleNamespace(text="hello", chunks=[]))
    with pytest.raises(RuntimeError, match="chunks"):
        LocalAsrPipeline._model_segments(whisper, FakeArray([0]))


def test_model_timestamp_accepts_empty_texts_list() -> None:
    whisper = SimpleNamespace(
        generate=lambda samples, **kwargs: SimpleNamespace(texts=[""], chunks=[])
    )
    assert LocalAsrPipeline._model_segments(whisper, FakeArray([0])) == []


def test_timeline_is_sorted_clamped_and_empty_ranges_are_dropped() -> None:
    segments = [
        Segment("late", 8, 20), Segment("early", -2, 2), Segment("empty", 12, 13)
    ]
    result = LocalAsrPipeline._normalize_timeline(segments, 10)
    assert [(s.text, s.start, s.end) for s in result] == [
        ("early", 0.0, 2.0), ("late", 8.0, 10),
    ]


def test_transcribe_quality_gate_retries_alternate_before_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _pipeline(tmp_path)
    calls = []
    def infer(*args, timestamp_mode, **kwargs):
        del args, kwargs
        calls.append(timestamp_mode)
        if timestamp_mode == "vad":
            return [Segment("啊", 0, 2.87)], 32.6
        return [Segment("a useful transcript with enough text", 0, 30)], 32.6
    monkeypatch.setattr(pipeline, "infer", infer)
    monkeypatch.setattr(
        pipeline, "_save", lambda segments, outdir, label: (segments, outdir, label)
    )
    result = pipeline.transcribe(
        Path("x"), "id", tmp_path / "work", tmp_path / "out",
        timestamp_mode="vad", diarization_enabled=False,
    )
    assert calls == ["vad", "model"]
    assert result[0][0].text.startswith("a useful")


def test_transcribe_quality_gate_raises_typed_error_without_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _pipeline(tmp_path)
    monkeypatch.setattr(
        pipeline, "infer", lambda *args, **kwargs: ([Segment("啊", 0, 2.87)], 32.6)
    )
    monkeypatch.setattr(pipeline, "_save", lambda *args: pytest.fail("must not publish"))
    with pytest.raises(TranscriptQualityError) as error:
        pipeline.transcribe(Path("x"), "id", tmp_path / "w", tmp_path / "o")
    assert len(error.value.attempts) == 2


def test_both_low_quality_strategies_raise_with_exact_live_metrics_without_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _pipeline(tmp_path)
    calls = []

    def infer(*args, timestamp_mode, **kwargs):
        del args, kwargs
        calls.append(timestamp_mode)
        if timestamp_mode == "vad":
            return [Segment("啊", 0, 2.87)], 32.625
        return [
            Segment("一二三四", 0, 11),
            Segment("五六七八", 15, 26.061025),
        ], 32.625

    monkeypatch.setattr(pipeline, "infer", infer)
    monkeypatch.setattr(pipeline, "_save", lambda *args: pytest.fail("must not publish"))
    with pytest.raises(TranscriptQualityError) as error:
        pipeline.transcribe(
            Path("x"), "id", tmp_path / "w", tmp_path / "o", timestamp_mode="vad"
        )
    assert calls == ["vad", "model"]
    assert len(error.value.attempts) == 2
    model_metrics = error.value.attempts[1]
    assert model_metrics == {
        "audio_duration": 32.625,
        "nonblank_characters": 8,
        "segment_count": 2,
        "timeline_coverage": 0.6762,
        "character_density": 0.2452,
        "timestamp_mode": "model",
        "accepted": False,
    }
    assert "nonblank_characters" in str(error.value)
    assert not (tmp_path / "o" / "transcript.json").exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_timeline_rejects_non_finite_timestamps(value: float) -> None:
    with pytest.raises(RuntimeError, match="非有限"):
        LocalAsrPipeline._normalize_timeline([Segment("bad", value, 1)], 2)


def test_vad_timestamps_add_original_offsets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _pipeline(tmp_path)
    monkeypatch.setattr(pipeline, "_ensure_silero", lambda: tmp_path / "silero.onnx")

    queued = [
        SimpleNamespace(start=16000, samples=FakeArray([0] * 8000)),
        SimpleNamespace(start=48000, samples=FakeArray([0] * 16000)),
    ]

    events = []

    class Vad:
        def __init__(self, config, buffer):
            del config, buffer
            self.items = list(queued)
        def accept_waveform(self, samples):
            del samples
            events.append(("accept", len(self.items)))
        def flush(self): pass
        def empty(self): return not self.items
        @property
        def front(self): return self.items[0]
        def pop(self):
            self.items.pop(0)
            events.append(("pop", len(self.items)))

    sherpa = SimpleNamespace(
        VadModelConfig=lambda: SimpleNamespace(silero_vad=SimpleNamespace()),
        VoiceActivityDetector=Vad,
    )
    texts = iter([SimpleNamespace(text="one"), SimpleNamespace(text="two")])
    whisper = SimpleNamespace(generate=lambda samples, **kwargs: next(texts))
    segments = pipeline._vad_segments(whisper, FakeArray([0] * 64000), sherpa, FakeNumpy)
    assert [(s.text, s.start, s.end) for s in segments] == [
        ("one", 1.0, 1.5), ("two", 3.0, 4.0)
    ]
    # Both queued segments were drained before the second input window arrived.
    assert events[:4] == [("accept", 2), ("pop", 1), ("pop", 0), ("accept", 0)]


@pytest.mark.parametrize(
    ("key", "value"),
    [("vad_window_size", 0), ("vad_buffer_seconds", 0), ("vad_num_threads", 0)],
)
def test_vad_rejects_invalid_runtime_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value
) -> None:
    pipeline = _pipeline(tmp_path, **{key: value})
    monkeypatch.setattr(pipeline, "_ensure_silero", lambda: tmp_path / "silero.onnx")
    with pytest.raises(ValueError, match=key):
        pipeline._vad_segments(SimpleNamespace(), FakeArray([0]), SimpleNamespace(), FakeNumpy)


def test_speaker_assignment_uses_maximum_overlap() -> None:
    segments = [Segment("a", 0, 4), Segment("b", 5, 6), Segment("c", 8, 9)]
    LocalAsrPipeline._assign_speakers(segments, [(0, 1, 0), (1, 4, 2), (4.8, 6.5, 1)])
    assert [segment.speaker_id for segment in segments] == [1, 2, None]


def test_diarization_uses_current_sherpa_config_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _pipeline(tmp_path, cluster_threshold=0.8)
    monkeypatch.setattr(
        pipeline, "_ensure_diarization_models",
        lambda: (tmp_path / "seg.onnx", tmp_path / "emb.onnx"),
    )
    captured = {}

    class Config(SimpleNamespace):
        def validate(self): return True
    class Result:
        def sort_by_start_time(self):
            return [SimpleNamespace(start=1, end=2, speaker=3)]
    class Diarizer:
        def __init__(self, config): captured["config"] = config
        def process(self, samples): return Result()

    sherpa = SimpleNamespace(
        OfflineSpeakerSegmentationPyannoteModelConfig=lambda **kw: SimpleNamespace(**kw),
        OfflineSpeakerSegmentationModelConfig=lambda **kw: SimpleNamespace(**kw),
        SpeakerEmbeddingExtractorConfig=lambda **kw: SimpleNamespace(**kw),
        FastClusteringConfig=lambda **kw: SimpleNamespace(**kw),
        OfflineSpeakerDiarizationConfig=lambda **kw: Config(**kw),
        OfflineSpeakerDiarization=Diarizer,
    )
    assert pipeline._diarize(FakeArray([0]), sherpa, 0) == [(1.0, 2.0, 3)]
    assert captured["config"].clustering.num_clusters == -1
    assert captured["config"].clustering.threshold == 0.8


def test_strict_gpu_and_output_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _pipeline(tmp_path)
    monkeypatch.setattr(pipeline, "_read_pcm", lambda path, np: FakeArray([0] * 16000))
    monkeypatch.setattr(pipeline, "_ensure_whisper_model", lambda: tmp_path / "model")
    chunk = SimpleNamespace(text="你好", start_ts=0.1, end_ts=0.9)
    captured = {}

    class Whisper:
        def __init__(self, model, device, **kwargs):
            captured.update(model=model, device=device, kwargs=kwargs)
        def generate(self, samples, **kwargs):
            return SimpleNamespace(chunks=[chunk])

    ov = SimpleNamespace(Core=lambda: SimpleNamespace(available_devices=["GPU", "CPU"]))
    monkeypatch.setattr(pipeline, "_load_asr_dependencies", lambda: (FakeNumpy, ov, SimpleNamespace(WhisperPipeline=Whisper)))
    result = pipeline.transcribe(
        Path("input.mp3"), "id", tmp_path / "work", tmp_path / "out", diarization_enabled=False
    )
    assert captured["device"] == "GPU"
    assert captured["kwargs"]["PERFORMANCE_HINT"] == "LATENCY"
    assert captured["kwargs"]["CACHE_DIR"] == str(tmp_path / "cache")
    payload = json.loads(result.transcript_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["segments"] == [{
        "id": "s0001", "start_ms": 100, "end_ms": 900,
        "speaker": "未知说话人", "text": "你好",
    }]


def test_missing_gpu_never_constructs_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _pipeline(tmp_path)
    monkeypatch.setattr(pipeline, "_read_pcm", lambda path, np: FakeArray([0]))
    monkeypatch.setattr(pipeline, "_ensure_whisper_model", lambda: tmp_path / "model")
    constructed = False
    def constructor(*args, **kwargs):
        nonlocal constructed
        constructed = True
    ov = SimpleNamespace(Core=lambda: SimpleNamespace(available_devices=["CPU"]))
    monkeypatch.setattr(pipeline, "_load_asr_dependencies", lambda: (FakeNumpy, ov, SimpleNamespace(WhisperPipeline=constructor)))
    with pytest.raises(RuntimeError, match="不会回退"):
        pipeline.transcribe(Path("x"), "id", tmp_path / "w", tmp_path / "o", diarization_enabled=False)
    assert constructed is False


def test_gpu_index_device_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _pipeline(tmp_path)
    monkeypatch.setattr(pipeline, "_read_pcm", lambda path, np: FakeArray([0] * 16000))
    monkeypatch.setattr(pipeline, "_ensure_whisper_model", lambda: tmp_path / "model")
    constructed = []
    class Whisper:
        def __init__(self, model, device, **kwargs): constructed.append(device)
        def generate(self, samples, **kwargs):
            return SimpleNamespace(chunks=[SimpleNamespace(text="ok", start_ts=0, end_ts=1)])
    ov = SimpleNamespace(Core=lambda: SimpleNamespace(available_devices=["GPU.0", "CPU"]))
    monkeypatch.setattr(
        pipeline, "_load_asr_dependencies",
        lambda: (FakeNumpy, ov, SimpleNamespace(WhisperPipeline=Whisper)),
    )
    pipeline.transcribe(Path("x"), "id", tmp_path / "w", tmp_path / "o", diarization_enabled=False)
    assert constructed == ["GPU"]


def test_pipeline_reuses_compiled_whisper_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _pipeline(tmp_path)
    monkeypatch.setattr(pipeline, "_read_pcm", lambda path, np: FakeArray([0] * 16000))
    verified = []

    def ensure_model() -> Path:
        verified.append(True)
        return tmp_path / "model"

    monkeypatch.setattr(pipeline, "_ensure_whisper_model", ensure_model)
    constructed = []
    class Whisper:
        def __init__(self, *args, **kwargs): constructed.append((args, kwargs))
        def generate(self, samples, **kwargs): return SimpleNamespace(chunks=[])
    ov = SimpleNamespace(Core=lambda: SimpleNamespace(available_devices=["GPU"]))
    monkeypatch.setattr(
        pipeline, "_load_asr_dependencies",
        lambda: (FakeNumpy, ov, SimpleNamespace(WhisperPipeline=Whisper)),
    )
    pipeline.infer(Path("a"), tmp_path / "w", diarization_enabled=False)
    pipeline.infer(Path("b"), tmp_path / "w", diarization_enabled=False)
    assert len(constructed) == 1
    assert len(verified) == 1


def test_empty_pcm_is_rejected_without_model_or_native_pipeline_or_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _pipeline(tmp_path)
    monkeypatch.setattr(pipeline, "_read_pcm", lambda path, np: FakeArray())
    monkeypatch.setattr(
        pipeline, "_ensure_whisper_model",
        lambda: pytest.fail("empty PCM must not download a model"),
    )
    ov_genai = SimpleNamespace(
        WhisperPipeline=lambda *args, **kwargs: pytest.fail("must not compile")
    )
    monkeypatch.setattr(
        pipeline, "_load_asr_dependencies", lambda: (FakeNumpy, SimpleNamespace(), ov_genai)
    )
    with pytest.raises(TranscriptQualityError):
        pipeline.transcribe(Path("x"), "id", tmp_path / "w", tmp_path / "o")
    assert not (tmp_path / "o" / "transcript.json").exists()


@pytest.mark.parametrize(("segments", "duration"), [([], 0.0), ([], 5.0), ([Segment(" ", 0, 1)], 5.0)])
def test_quality_metrics_reject_empty_output_at_every_duration(segments, duration: float) -> None:
    from medialab.local_asr import transcript_quality_metrics

    assert transcript_quality_metrics(segments, duration, "model")["accepted"] is False


def test_quality_metrics_accept_short_nonempty_output() -> None:
    from medialab.local_asr import transcript_quality_metrics

    metrics = transcript_quality_metrics([Segment("好", 0, 0.2)], 2.0, "model")
    assert metrics["accepted"] is True


def test_missing_model_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="auto_download"):
        _pipeline(tmp_path)._ensure_whisper_model()


def test_whisper_download_uses_official_endpoint_and_runtime_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = _pipeline(tmp_path, auto_download=True)
    captured = {}

    def snapshot_download(**kwargs):
        captured.update(kwargs)
        local_dir = Path(kwargs["local_dir"])
        for marker in _MODEL_MARKERS:
            path = local_dir / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"model")

    fake_hub = SimpleNamespace(snapshot_download=snapshot_download)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    model = pipeline._ensure_whisper_model()
    assert model.is_dir()
    assert captured["endpoint"] == "https://huggingface.co"
    assert Path(captured["cache_dir"]) == tmp_path / "models" / ".hf-cache"
    assert captured["revision"] == "8c593cfd15717c025c5ef99ea0b1879c88d03ed2"


def test_whisper_manifest_reuses_verified_snapshot_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads = []

    def snapshot_download(**kwargs: Any) -> None:
        downloads.append(kwargs["revision"])
        _write_fake_snapshot(kwargs["local_dir"], b"restart")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download)
    )
    first = _pipeline(tmp_path, auto_download=True)
    model_dir = first._ensure_whisper_model()
    manifest = json.loads((model_dir / _MODEL_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["model_id"] == WHISPER_MODEL_ID
    assert set(manifest["files"]) == set(_MODEL_MARKERS)

    def fail_download(**kwargs: Any) -> None:
        pytest.fail(f"verified restart must not redownload: {kwargs}")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=fail_download)
    )
    restarted = _pipeline(tmp_path, auto_download=True)
    assert restarted._ensure_whisper_model() == model_dir
    assert downloads == [manifest["revision"]]
    assert local_model_readiness(tmp_path / "models") == (True, None)


def test_whisper_concurrent_processes_download_once(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    counter = context.Value("i", 0)
    release_download = context.Event()
    processes = [
        context.Process(
            target=_concurrent_model_ensure,
            args=(str(tmp_path / "models"), counter, release_download),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    release_download.set()
    for process in processes:
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("concurrent model download process deadlocked")
        assert process.exitcode == 0
    assert counter.value == 1
    assert local_model_readiness(
        tmp_path / "models", revision="concurrent-revision"
    ) == (True, None)


def test_whisper_corruption_invalidates_and_redownloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads = []

    def snapshot_download(**kwargs: Any) -> None:
        downloads.append(len(downloads) + 1)
        _write_fake_snapshot(kwargs["local_dir"], f"download-{downloads[-1]}".encode())

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download)
    )
    pipeline = _pipeline(tmp_path, auto_download=True)
    model_dir = pipeline._ensure_whisper_model()
    corrupted = model_dir / _MODEL_MARKERS[0]
    corrupted.write_bytes(b"x" * corrupted.stat().st_size)
    assert local_model_readiness(tmp_path / "models") == (False, "file_hash_mismatch")

    assert _pipeline(tmp_path, auto_download=True)._ensure_whisper_model() == model_dir
    assert downloads == [1, 2]
    assert local_model_readiness(tmp_path / "models") == (True, None)


def test_whisper_revision_change_replaces_snapshot_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads = []

    def snapshot_download(**kwargs: Any) -> None:
        downloads.append(kwargs["revision"])
        _write_fake_snapshot(kwargs["local_dir"], kwargs["revision"].encode())

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download)
    )
    _pipeline(tmp_path, auto_download=True, revision="revision-one")._ensure_whisper_model()
    assert local_model_readiness(
        tmp_path / "models", revision="revision-two"
    ) == (False, "identity_mismatch")
    model_dir = _pipeline(
        tmp_path, auto_download=True, revision="revision-two"
    )._ensure_whisper_model()
    manifest = json.loads((model_dir / _MODEL_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["revision"] == "revision-two"
    assert downloads == ["revision-one", "revision-two"]


def test_whisper_stale_partial_cleanup_is_scoped_to_exact_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def snapshot_download(**kwargs: Any) -> None:
        _write_fake_snapshot(kwargs["local_dir"], b"partial")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download)
    )
    pipeline = _pipeline(tmp_path, auto_download=True)
    pipeline._ensure_whisper_model()
    root = tmp_path / "models"
    stale = root / ".whisper-small-int8-ov.interrupted.part"
    stale.mkdir()
    (stale / "unfinished").write_bytes(b"partial")
    unrelated = root / ".another-model.interrupted.part"
    unrelated.mkdir()

    pipeline._ensure_whisper_model()
    assert not stale.exists()
    assert unrelated.is_dir()


def test_whisper_download_failure_releases_lock_and_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(**kwargs: Any) -> None:
        Path(kwargs["local_dir"], _MODEL_MARKERS[0]).write_bytes(b"partial")
        raise RuntimeError("interrupted")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=interrupted)
    )
    pipeline = _pipeline(tmp_path, auto_download=True)
    with pytest.raises(RuntimeError, match="interrupted"):
        pipeline._ensure_whisper_model()
    assert not list((tmp_path / "models").glob(".whisper-small-int8-ov.*.part"))

    def successful(**kwargs: Any) -> None:
        _write_fake_snapshot(kwargs["local_dir"], b"recovered")

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=successful)
    )
    assert pipeline._ensure_whisper_model().is_dir()
    assert local_model_readiness(tmp_path / "models") == (True, None)


def test_download_checksum_and_atomic_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"verified model"
    expected = __import__("hashlib").sha256(content).hexdigest()
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.BytesIO(content))
    target = tmp_path / "model.onnx"
    assert _download("https://example.invalid/model", target, expected) == target
    assert target.read_bytes() == content
    assert not list(tmp_path.glob("*.part"))
    with pytest.raises(RuntimeError, match="校验失败"):
        _download("https://example.invalid/bad", tmp_path / "bad.onnx", "0" * 64)
    assert not (tmp_path / "bad.onnx").exists()


def test_segmentation_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.bz2"
    with tarfile.open(archive, "w:bz2") as bundle:
        data = b"bad"
        member = tarfile.TarInfo("../model.int8.onnx")
        member.size = len(data)
        bundle.addfile(member, io.BytesIO(data))
    with pytest.raises(RuntimeError, match="不安全路径"):
        _extract_segmentation(archive, tmp_path / "out")


def test_optional_dependency_error_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __import__("builtins").__import__
    def missing(name, *args, **kwargs):
        if name == "openvino_genai":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr("builtins.__import__", missing)
    with pytest.raises(RuntimeError, match="uv sync --extra local-asr"):
        LocalAsrPipeline._load_asr_dependencies()
