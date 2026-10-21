from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from medialab import ocr


def test_engine_resolution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ocr, "_rapidocr_installed", lambda: False)
    assert ocr.resolve_engine({}) == (None, "dependencies_missing")
    assert ocr.resolve_engine({"ocr": {"enabled": False}}) == (None, "disabled")
    assert ocr.resolve_engine({"ocr": {"engine": "tesseract"}}) == (None, "unknown_engine:tesseract")
    assert ocr.resolve_engine({"ocr": {"engine": "rapidocr"}}) == (None, "dependencies_missing")
    cloud = {"engine": "paddleocr_api", "endpoint": "https://ocr.test/jobs", "token": ""}
    assert ocr.resolve_engine({"ocr": cloud}) == (None, "cloud_not_allowed")
    assert ocr.resolve_engine({"ocr": {**cloud, "allow_cloud": True}}) == (None, "missing_token")
    ready = {**cloud, "allow_cloud": True, "token": "t"}
    insecure = {**ready, "endpoint": "http://ocr.test/jobs"}
    assert ocr.resolve_engine({"ocr": insecure}) == (None, "insecure_endpoint")
    assert ocr.resolve_engine({"ocr": ready}) == ("paddleocr_api", None)
    assert ocr.resolve_engine({"ocr": {**ready, "engine": "auto"}}) == ("paddleocr_api", None)
    monkeypatch.setattr(ocr, "_rapidocr_installed", lambda: True)
    assert ocr.resolve_engine({"ocr": {**ready, "engine": "auto"}}) == ("rapidocr", None)
    assert ocr.probe_ocr({}) == {"available": True, "engine": "rapidocr", "reason": None}


def test_paddle_api_submit_poll_fetch(tmp_path: Path):
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG")
    polls = iter(["pending", "running", "done"])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers.get("authorization")))
        if request.method == "POST":
            assert b"PaddleOCR-VL-1.6" in request.content
            return httpx.Response(200, json={"data": {"jobId": "j1"}})
        if request.url.path == "/jobs/j1":
            state = next(polls)
            data = {"state": state}
            if state == "done":
                data["resultUrl"] = {"jsonUrl": "https://files.test/r.jsonl"}
            return httpx.Response(200, json={"data": data})
        lines = [json.dumps({"result": {"layoutParsingResults": [{"markdown": {"text": "第一页"}},
                                                                {"markdown": {"text": "第二页"}}]}}), ""]
        return httpx.Response(200, text="\n".join(lines))

    api = ocr.PaddleOcrApi("https://ocr.test/jobs/", "secret", poll_interval=0,
                           client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert api.recognize(image) == {"text": "第一页\n\n第二页", "confidence": None}
    assert seen[0] == ("POST", "https://ocr.test/jobs", "bearer secret")
    # The result file URL is fetched without the API token.
    assert seen[-1] == ("GET", "https://files.test/r.jsonl", None)


def test_paddle_api_failure_is_reported(tmp_path: Path):
    image = tmp_path / "a.png"
    image.write_bytes(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"jobId": "j"}})
        return httpx.Response(200, json={"data": {"state": "failed", "errorMsg": "quota"}})

    api = ocr.PaddleOcrApi("https://ocr.test/jobs", "t", poll_interval=0,
                           client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ocr.OcrError, match="quota"):
        api.recognize(image)


def test_item_images_are_sorted_and_filtered(tmp_path: Path):
    (tmp_path / "images" / "sub").mkdir(parents=True)
    for name in ("b.webp", "a.PNG", "notes.txt", "sub/c.jpg"):
        (tmp_path / "images" / name).write_bytes(b"x")
    assert ocr.item_images(tmp_path) == ["images/a.PNG", "images/b.webp", "images/sub/c.jpg"]
    assert ocr.item_images(tmp_path / "missing") == []


def test_unavailable_engine_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(ocr, "_rapidocr_installed", lambda: False)
    with pytest.raises(ocr.OcrError, match="dependencies_missing"):
        ocr.recognize_image(tmp_path / "x.png", {})


def test_item_images_ignores_symlinks_leaving_the_item(tmp_path: Path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG")
    item = tmp_path / "item"
    (item / "images").mkdir(parents=True)
    (item / "images" / "0001.png").write_bytes(b"\x89PNG")
    (item / "images" / "0002.png").symlink_to(outside)
    assert ocr.item_images(item) == ["images/0001.png"]


def test_paddle_api_rejects_unsafe_job_ids_and_plain_http_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"jobId": "../../admin"}})

    api = ocr.PaddleOcrApi("https://ocr.test/jobs", "secret", poll_interval=0,
                           client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ocr.OcrError, match="invalid jobId"):
        api.submit(Path(__file__))
    with pytest.raises(ocr.OcrError, match="https"):
        api.fetch("http://files.test/r.jsonl")
