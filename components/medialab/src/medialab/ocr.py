"""Optional OCR for article images.

Two engines, both optional:

* ``rapidocr`` – fully local (``rapidocr-onnxruntime``, PP-OCR models ship inside the
  wheel). Install with the ``ocr`` extra.
* ``paddleocr_api`` – the PaddleOCR-VL cloud job API used by the original Echo app.
  Needs ``[ocr] endpoint`` and a token in ``secrets.toml``; images are uploaded to that
  service, so it is opt-in (``allow_cloud = true``).

``engine = "auto"`` (default) picks the local engine when installed, otherwise the cloud
engine when it is configured and allowed. :func:`probe_ocr` explains what is missing.
"""

from __future__ import annotations

import importlib.util
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

ENGINES = ("auto", "rapidocr", "paddleocr_api")
PADDLE_DEFAULT_MODEL = "PaddleOCR-VL-1.6"
PADDLE_OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

_RAPID: Any = None
_RAPID_LOCK = threading.Lock()


class OcrError(RuntimeError):
    pass


def ocr_section(cfg: dict | None) -> dict[str, Any]:
    return dict((cfg or {}).get("ocr") or {})


def _rapidocr_installed() -> bool:
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def _paddle_ready(section: dict[str, Any]) -> str | None:
    """``None`` when the cloud engine can be used, else the reason it cannot."""
    if not section.get("allow_cloud", False):
        return "cloud_not_allowed"
    endpoint = str(section.get("endpoint") or "").strip()
    if not endpoint:
        return "missing_endpoint"
    if not endpoint.lower().startswith("https://"):
        return "insecure_endpoint"
    if not str(section.get("token") or "").strip():
        return "missing_token"
    return None


def resolve_engine(cfg: dict | None) -> tuple[str | None, str | None]:
    """Return ``(engine, None)`` or ``(None, reason)``."""
    section = ocr_section(cfg)
    if not section.get("enabled", True):
        return None, "disabled"
    engine = str(section.get("engine") or "auto")
    if engine not in ENGINES:
        return None, f"unknown_engine:{engine}"
    if engine in ("auto", "rapidocr") and _rapidocr_installed():
        return "rapidocr", None
    if engine == "rapidocr":
        return None, "dependencies_missing"
    reason = _paddle_ready(section)
    if reason is None:
        return "paddleocr_api", None
    return None, "dependencies_missing" if engine == "auto" and reason == "cloud_not_allowed" else reason


def probe_ocr(cfg: dict | None) -> dict[str, Any]:
    engine, reason = resolve_engine(cfg)
    return {"available": engine is not None, "engine": engine, "reason": reason}


def _rapid_engine() -> Any:
    global _RAPID
    with _RAPID_LOCK:
        if _RAPID is None:
            from rapidocr_onnxruntime import RapidOCR

            _RAPID = RapidOCR()
        return _RAPID


def _rapidocr(image: Path) -> dict[str, Any]:
    result, _elapsed = _rapid_engine()(str(image))
    lines = []
    scores = []
    for entry in result or []:
        # entry = [box, text, score]
        text = str(entry[1]).strip()
        if text:
            lines.append(text)
            scores.append(float(entry[2]))
    return {"text": "\n".join(lines),
            "confidence": round(sum(scores) / len(scores), 4) if scores else None}


class PaddleOcrApi:
    """Client for the PaddleOCR-VL asynchronous job API (submit → poll → fetch JSONL)."""

    def __init__(self, endpoint: str, token: str, *, model: str = PADDLE_DEFAULT_MODEL,
                 poll_interval: float = 2.0, max_polls: int = 60,
                 client: httpx.Client | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.model = model
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.client = client or httpx.Client(timeout=60)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self.token}"}

    def submit(self, image: Path) -> str:
        with image.open("rb") as handle:
            response = self.client.post(
                self.endpoint, headers=self._headers(),
                data={"model": self.model, "optionalPayload": json.dumps(PADDLE_OPTIONAL_PAYLOAD)},
                files={"file": (image.name, handle)},
            )
        if response.status_code >= 400:
            raise OcrError(f"PaddleOCR submit returned HTTP {response.status_code}")
        job_id = (response.json().get("data") or {}).get("jobId")
        if not job_id:
            raise OcrError("PaddleOCR submit response has no jobId")
        job_id = str(job_id)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", job_id) or job_id in {".", ".."}:
            raise OcrError("PaddleOCR submit response has an invalid jobId")
        return job_id

    def wait(self, job_id: str) -> str:
        for _ in range(self.max_polls):
            response = self.client.get(f"{self.endpoint}/{job_id}", headers=self._headers())
            if response.status_code >= 400:
                raise OcrError(f"PaddleOCR poll returned HTTP {response.status_code}")
            body = response.json()
            data = body.get("data") or {}
            state = data.get("state") or body.get("state") or "unknown"
            if state == "done":
                url = (data.get("resultUrl") or {}).get("jsonUrl")
                if not url:
                    raise OcrError("PaddleOCR done response has no resultUrl.jsonUrl")
                return str(url)
            if state == "failed":
                raise OcrError(f"PaddleOCR job failed: {data.get('errorMsg') or data.get('error') or 'unknown'}")
            if state not in ("pending", "running"):
                raise OcrError(f"PaddleOCR returned unexpected state: {state}")
            time.sleep(self.poll_interval)
        raise OcrError("PaddleOCR job did not finish in time")

    def fetch(self, result_url: str) -> str:
        if not result_url.lower().startswith("https://"):
            raise OcrError("PaddleOCR result URL must use https://")
        response = self.client.get(result_url)
        if response.status_code >= 400:
            raise OcrError(f"PaddleOCR result returned HTTP {response.status_code}")
        pages = []
        for line in response.text.splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            for entry in (parsed.get("result") or {}).get("layoutParsingResults") or []:
                text = ((entry.get("markdown") or {}).get("text") or "").strip()
                if text:
                    pages.append(text)
        return "\n\n".join(pages)

    def recognize(self, image: Path) -> dict[str, Any]:
        return {"text": self.fetch(self.wait(self.submit(image))), "confidence": None}


def recognize_image(image: Path, cfg: dict | None, *, engine: str | None = None) -> dict[str, Any]:
    if engine is None:
        engine, reason = resolve_engine(cfg)
        if engine is None:
            raise OcrError(f"OCR unavailable: {reason}")
    if engine == "rapidocr":
        return _rapidocr(image)
    section = ocr_section(cfg)
    api = PaddleOcrApi(str(section["endpoint"]), str(section["token"]),
                       model=str(section.get("model") or PADDLE_DEFAULT_MODEL),
                       max_polls=int(section.get("max_poll_attempts") or 60))
    return api.recognize(image)


def item_images(folder: Path, limit: int = 200) -> list[str]:
    """Relative POSIX paths of images in ``<item>/images`` (sorted, bounded)."""
    images_dir = folder / "images"
    if not images_dir.is_dir() or images_dir.is_symlink():
        return []
    root = folder.resolve()
    found = sorted(
        path.relative_to(folder).as_posix()
        for path in images_dir.rglob("*")
        # Never follow symlinks out of the item: OCR may upload images to a cloud API.
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in IMAGE_SUFFIXES
        and path.resolve().is_relative_to(root)
    )
    return found[:limit]
