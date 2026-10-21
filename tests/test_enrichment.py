"""Optional TTS / OCR enrichment: graceful degradation and file-library output."""

from __future__ import annotations

import json
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from echo_reader.server import create_app
from test_import_control_plane import FakeCoordinator

LOOPBACK = "http://127.0.0.1:3100"


class RescanCounter(FakeCoordinator):
    def __init__(self):
        super().__init__()
        self.rescans = 0

    async def rescan(self):
        self.rescans += 1
        return {"item_count": 1, "invalid_item_count": 0}


def make_item(library: Path, *, with_image: bool = True) -> Path:
    folder = library / "sample"
    (folder / "images").mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "id": "item-1",
        "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z",
        "source": {"type": "article", "title": "示例"},
        "article": {"path": "article.md", "content_source": "content.json"},
        "transcript": {"path": "transcript.json", "language": "zh-CN"},
        "primary_media": None,
        "custom_field": "kept",
    }), encoding="utf-8")
    (folder / "article.md").write_text("# 标题\n\n![图](images/1.png)\n\n这是 **正文**。\n", encoding="utf-8")
    if with_image:
        (folder / "images" / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    return folder


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ECHO_TTS_MODELS_PATH", raising=False)
    library = tmp_path / "library"
    library.mkdir()
    coordinator = RescanCounter()
    app = create_app(library_root=library, runtime_root=tmp_path / "runtime",
                     config_root=tmp_path / "config",
                     coordinator_factory=lambda paths: coordinator, bridge=None)
    return app, library, coordinator


def wait_job(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/enrichment-jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.02)
    raise AssertionError(job)


def test_features_disabled_without_optional_dependencies(env, monkeypatch):
    app, library, _ = env
    make_item(library)
    monkeypatch.setattr("medialab.tts.missing_modules", lambda: ["kokoro_onnx"])
    monkeypatch.setattr("medialab.ocr._rapidocr_installed", lambda: False)
    with TestClient(app, base_url=LOOPBACK) as client:
        features = client.get("/api/v1/import-capabilities").json()["features"]
        assert features["tts"]["available"] is False
        assert features["tts"]["reason"] == "dependencies_missing"
        assert "功能未启用" in features["tts"]["message"]
        assert features["ocr"] == client.get("/api/v1/features").json()["ocr"]
        assert features["ocr"]["available"] is False
        for kind in ("tts", "ocr"):
            response = client.post(f"/api/v1/items/item-1/{kind}")
            assert response.status_code == 503
            detail = response.json()["detail"]
            assert detail["code"] == "feature_disabled" and detail["feature"] == kind
            assert "功能未启用" in detail["message"]
    assert not (library / "sample" / "ocr.json").exists()
    assert not (library / "sample" / "media").exists()


def test_tts_reports_missing_model_files(env, monkeypatch):
    app, library, _ = env
    monkeypatch.setattr("medialab.tts.missing_modules", lambda: [])
    with TestClient(app, base_url=LOOPBACK) as client:
        tts = client.get("/api/v1/features").json()["tts"]
    assert tts == {"available": False, "reason": "model_missing", "engine": "kokoro",
                   "message": tts["message"]}
    assert "未下载模型文件" in tts["message"]


def test_tts_writes_attachment_and_rescans(env, monkeypatch):
    app, library, coordinator = env
    folder = make_item(library)
    monkeypatch.setattr("medialab.tts.probe_tts", lambda cfg: {"available": True, "engine": "kokoro"})
    seen = {}

    def fake_synthesize(text, output, cfg, *, voice=None, speed=None):
        seen["text"] = text
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 2400)
        return {"duration_ms": 100, "voice": voice or "zf_xiaoxiao", "language": "cmn", "truncated": False}

    monkeypatch.setattr("medialab.tts.synthesize", fake_synthesize)
    with TestClient(app, base_url=LOOPBACK) as client:
        assert client.post("/api/v1/items/missing/tts").status_code == 404
        assert client.post("/api/v1/items/item-1/tts", json={"voice": "../x"}).status_code == 422
        started = client.post("/api/v1/items/item-1/tts", json={"speed": 1.1})
        assert started.status_code == 202
        job = wait_job(client, started.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["media_id"] == "tts-article"
    assert seen["text"] == "标题\n这是 正文。"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["custom_field"] == "kept"
    assert manifest["media_items"] == [{
        "id": "tts-article", "role": "attachment", "type": "audio",
        "original_path": "media/tts-article.wav", "playback_path": "media/tts-article.wav",
        "mime_type": "audio/wav", "title": "文章朗读（Kokoro）", "description": "voice=zf_xiaoxiao lang=cmn",
    }]
    assert (folder / "media" / "tts-article.wav").is_file()
    assert coordinator.rescans == 1


def test_ocr_writes_sidecar_and_records_per_image_errors(env, monkeypatch):
    app, library, coordinator = env
    folder = make_item(library)
    (folder / "images" / "2.jpg").write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr("medialab.ocr.resolve_engine", lambda cfg: ("rapidocr", None))

    def fake_recognize(image, cfg, *, engine=None):
        if image.name == "2.jpg":
            raise RuntimeError("bad image")
        return {"text": "识别出的文字", "confidence": 0.9}

    monkeypatch.setattr("medialab.ocr.recognize_image", fake_recognize)
    with TestClient(app, base_url=LOOPBACK) as client:
        job = wait_job(client, client.post("/api/v1/items/item-1/ocr").json()["id"])
    assert job["status"] == "succeeded", job
    assert job["result"] == {"path": "ocr.json", "engine": "rapidocr", "images": 2, "recognized": 1,
                             "rescan": {"item_count": 1, "invalid_item_count": 0}}
    sidecar = json.loads((folder / "ocr.json").read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == 1 and sidecar["engine"] == "rapidocr"
    assert sidecar["images"][0] == {"path": "images/1.png", "text": "识别出的文字", "confidence": 0.9}
    assert sidecar["images"][1]["error"] == "bad image"
    assert coordinator.rescans == 1


def test_ocr_without_images_fails_cleanly(env, monkeypatch):
    app, library, _ = env
    make_item(library, with_image=False)
    monkeypatch.setattr("medialab.ocr.resolve_engine", lambda cfg: ("rapidocr", None))
    with TestClient(app, base_url=LOOPBACK) as client:
        job = wait_job(client, client.post("/api/v1/items/item-1/ocr").json()["id"])
        assert job["status"] == "failed" and "没有可识别的图片" in job["error"]
        assert client.get("/api/v1/enrichment-jobs/nope").status_code == 404


def test_article_path_cannot_escape_the_item_folder(tmp_path: Path):
    from echo_reader.enrichment import _contained

    folder = tmp_path / "library" / "item"
    folder.mkdir(parents=True)
    (folder / "article.md").write_text("ok", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    (folder / "link.md").symlink_to(secret)
    assert _contained(folder, "article.md") == (folder / "article.md").resolve()
    assert _contained(folder, "../../secret.txt") is None
    assert _contained(folder, str(secret)) is None
    assert _contained(folder, "link.md") is None
    assert _contained(folder, ".") is None
