"""Optional per-item enrichment: Kokoro read-aloud audio and image OCR.

Both features depend on optional extras. When a feature is not available the API answers
``503 feature_disabled`` with a readable reason and the UI hides the entry point; nothing
else in the import service is affected. Results are written into the item folder (the
library stays the single source of truth) and Echo is asked to rescan, which also
refreshes the full-text index.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException

from .import_store import now

Feature = Literal["tts", "ocr"]
TTS_MEDIA_ID = "tts-article"
TTS_MEDIA_PATH = "media/tts-article.wav"
OCR_SIDECAR = "ocr.json"

_FEATURE_NAMES = {"tts": "朗读（Kokoro 语音合成）", "ocr": "图片文字识别（OCR）"}
_REASONS = {
    "disabled": "已在配置中关闭",
    "dependencies_missing": "未安装可选依赖",
    "model_missing": "未下载模型文件",
    "cloud_not_allowed": "未允许使用云端 OCR（[ocr] allow_cloud）",
    "missing_endpoint": "未配置 OCR 服务地址",
    "insecure_endpoint": "OCR 服务地址必须使用 https://",
    "missing_token": "未配置 OCR 服务 token",
}
_INSTALL_HINTS = {
    "tts": "安装：uv sync --extra tts，然后运行 uv run python scripts/download_kokoro_models.py",
    "ocr": "安装：uv sync --extra ocr（本地 RapidOCR），或在 config.toml 配置 PaddleOCR API",
}


def feature_message(feature: str, reason: str | None) -> str:
    text = _REASONS.get(reason or "", reason or "未知原因")
    return f"{_FEATURE_NAMES[feature]}功能未启用：{text}。{_INSTALL_HINTS[feature]}"


def _atomic_write_json(path: Path, value: Any) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _contained(folder: Path, relative: str) -> Path | None:
    """Resolve ``relative`` inside ``folder``; ``None`` if it escapes (``..``, absolute, symlink)."""
    root = folder.resolve()
    try:
        candidate = (root / relative).resolve()
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_relative_to(root) and candidate != root else None


class EnrichmentService:
    def __init__(self, imports: Any) -> None:
        self.imports = imports
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    # -- configuration -------------------------------------------------------------
    def config(self) -> dict[str, Any]:
        from medialab.config import load_config

        config_root = self.imports.paths.config_root
        cfg = load_config(config_root / "config.toml", config_root / "secrets.toml")
        tts = cfg.setdefault("tts", {})
        if not str(tts.get("model_dir") or "").strip():
            tts["model_dir"] = str(self.imports.paths.cache_root / "models" / "kokoro")
        return cfg

    def features(self) -> dict[str, dict[str, Any]]:
        from medialab.ocr import probe_ocr
        from medialab.tts import probe_tts

        cfg = self.config()
        result: dict[str, dict[str, Any]] = {}
        for name, probe in (("tts", probe_tts), ("ocr", probe_ocr)):
            try:
                info = probe(cfg)
            except Exception as exc:  # a broken optional install must never break the API
                info = {"available": False, "reason": f"probe_failed:{type(exc).__name__}"}
            public = {"available": bool(info.get("available")), "reason": info.get("reason")}
            if info.get("engine"):
                public["engine"] = info["engine"]
            if not public["available"]:
                public["message"] = feature_message(name, public["reason"])
            result[name] = public
        return result

    # -- items ---------------------------------------------------------------------
    def find_item(self, item_id: str) -> Path | None:
        root = Path(self.imports.content_root)
        candidates = [root] + sorted(
            child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")
        ) if root.is_dir() else []
        for folder in candidates:
            manifest = folder / "manifest.json"
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("id") == item_id:
                return folder
        return None

    # -- jobs ----------------------------------------------------------------------
    def public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if not key.startswith("_")}

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Enrichment job not found")
        return self.public_job(job)

    async def start(self, feature: Feature, item_id: str, options: dict[str, Any]) -> dict[str, Any]:
        state = self.features()[feature]
        if not state["available"]:
            raise HTTPException(503, {
                "code": "feature_disabled", "feature": feature,
                "reason": state["reason"], "message": state["message"],
            })
        folder = self.find_item(item_id)
        if folder is None:
            raise HTTPException(404, "该内容不存在或已被移除")
        for job in self.jobs.values():
            if job["item_id"] == item_id and job["kind"] == feature and job["status"] in {"queued", "running"}:
                return self.public_job(job)
        job_id = secrets.token_hex(8)
        job = {"id": job_id, "kind": feature, "item_id": item_id, "status": "queued",
               "created_at": now(), "finished_at": None, "error": None, "result": None}
        self.jobs[job_id] = job
        self.tasks[job_id] = asyncio.create_task(self._run(job, folder, options), name=f"enrich-{job_id}")
        return self.public_job(job)

    async def close(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    async def _run(self, job: dict[str, Any], folder: Path, options: dict[str, Any]) -> None:
        job["status"] = "running"
        try:
            cfg = self.config()
            work = self._tts if job["kind"] == "tts" else self._ocr
            job["result"] = await asyncio.to_thread(work, folder, cfg, options)
            coordinator = self.imports.coordinator
            if coordinator is not None:
                try:
                    job["result"]["rescan"] = await coordinator.rescan()
                except Exception as exc:  # the files are written; Echo picks them up later
                    job["result"]["rescan_error"] = str(exc) or type(exc).__name__
            job["status"] = "succeeded"
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            raise
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc) or type(exc).__name__
        finally:
            job["finished_at"] = now()
            self.tasks.pop(job["id"], None)

    # -- workers (run in a thread) -----------------------------------------------------
    @staticmethod
    def _manifest(folder: Path) -> dict[str, Any]:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))

    def _tts(self, folder: Path, cfg: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        from medialab import tts

        manifest = self._manifest(folder)
        article = _contained(folder, str((manifest.get("article") or {}).get("path") or "article.md"))
        markdown = article.read_text(encoding="utf-8") if article is not None and article.is_file() else ""
        text = tts.markdown_to_speech_text(markdown)
        if not text:
            raise ValueError("文章没有可朗读的文字")
        output = folder / TTS_MEDIA_PATH
        info = tts.synthesize(text, output, cfg, voice=options.get("voice"), speed=options.get("speed"))
        manifest = self._manifest(folder)  # re-read: the reader may have saved meanwhile
        items = [m for m in manifest.get("media_items") or [] if m.get("id") != TTS_MEDIA_ID]
        items.append({
            "id": TTS_MEDIA_ID, "role": "attachment", "type": "audio",
            "original_path": TTS_MEDIA_PATH, "playback_path": TTS_MEDIA_PATH,
            "mime_type": "audio/wav", "title": "文章朗读（Kokoro）",
            "description": f"voice={info['voice']} lang={info['language']}",
        })
        manifest["media_items"] = items
        manifest["updated_at"] = now()
        _atomic_write_json(folder / "manifest.json", manifest)
        return {"media_id": TTS_MEDIA_ID, "path": TTS_MEDIA_PATH, **{k: info[k] for k in (
            "duration_ms", "voice", "language", "truncated")}}

    def _ocr(self, folder: Path, cfg: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        from medialab import ocr

        engine, reason = ocr.resolve_engine(cfg)
        if engine is None:
            raise RuntimeError(feature_message("ocr", reason))
        images = ocr.item_images(folder)
        if not images:
            raise ValueError("该内容没有可识别的图片")
        entries = []
        for relative in images:
            entry: dict[str, Any] = {"path": relative}
            try:
                entry.update(ocr.recognize_image(folder / relative, cfg, engine=engine))
            except Exception as exc:
                entry.update(text="", error=str(exc) or type(exc).__name__)
            entries.append(entry)
        _atomic_write_json(folder / OCR_SIDECAR, {
            "schema_version": 1, "engine": engine, "created_at": now(), "images": entries,
        })
        recognized = sum(1 for entry in entries if entry.get("text"))
        return {"path": OCR_SIDECAR, "engine": engine, "images": len(entries), "recognized": recognized}
