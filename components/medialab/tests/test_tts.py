from __future__ import annotations

from pathlib import Path

import pytest

from medialab import tts


def test_markdown_is_reduced_to_readable_text():
    md = "# 标题\n\n![图](a.png)\n\n见 [链接](https://x.test) 与 `代码`。\n\n- 列表\n\n```\nskip\n```\n"
    assert tts.markdown_to_speech_text(md) == "标题\n见 链接 与 代码。\n列表"


def test_language_detection():
    assert tts.detect_language("这是中文内容，用来测试。") == "cmn"
    assert tts.detect_language("This is English text.") == "en-us"


def test_probe_reports_reasons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(tts.MODELS_ENV, raising=False)
    cfg = {"tts": {"model_dir": str(tmp_path)}}
    assert tts.probe_tts({"tts": {"enabled": False}})["reason"] == "disabled"
    monkeypatch.setattr(tts, "missing_modules", lambda: ["kokoro_onnx"])
    info = tts.probe_tts(cfg)
    assert info["available"] is False and info["reason"] == "dependencies_missing"
    monkeypatch.setattr(tts, "missing_modules", lambda: [])
    info = tts.probe_tts(cfg)
    assert info["reason"] == "model_missing" and info["missing"] == [tts.MODEL_FILE, tts.VOICES_FILE]
    (tmp_path / tts.MODEL_FILE).write_bytes(b"x")
    (tmp_path / tts.VOICES_FILE).write_bytes(b"x")
    assert tts.probe_tts(cfg) == {"engine": "kokoro", "available": True, "reason": None,
                                  "model_dir": str(tmp_path)}


def test_env_overrides_model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(tts.MODELS_ENV, str(tmp_path))
    assert tts.tts_settings({"tts": {"model_dir": "/elsewhere"}}).model_dir == tmp_path


def test_synthesize_writes_wav_with_fake_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    np = pytest.importorskip("numpy")
    calls = {}

    class FakeModel:
        def create(self, text, *, voice, speed, lang):
            calls.update(text=text, voice=voice, speed=speed, lang=lang)
            return np.zeros(2400, dtype=np.float32), 24000

    monkeypatch.setattr(tts, "_model", lambda settings: FakeModel())
    out = tmp_path / "media" / "a.wav"
    info = tts.synthesize("你好，世界。这是测试。", out, {"tts": {"speed": 1.2}})
    assert out.is_file() and not out.with_name("a.wav.part").exists()
    assert info["duration_ms"] == 100 and info["language"] == "cmn" and info["voice"] == "zf_xiaoxiao"
    assert calls["speed"] == 1.2
    with pytest.raises(ValueError):
        tts.synthesize("   ", out, {})
