"""publishing.py — 按 echo 资料库规范（docs/producer-guide.md）编排产物目录。

一条信息 = 资料库根目录下一个中文标题文件夹：

    {标题}/
    ├── manifest.json        # 必须
    ├── article.md           # AI 整理文章（已存在则不覆盖）
    ├── transcript.json      # 带时间戳转录（echo 格式）
    ├── media/
    │   ├── original.*       # 原始下载文件（保留原样）
    │   └── playback.*       # 浏览器播放版（必须 H.264+AAC / MP3 / M4A）
    └── images/              # 文章插图

本模块只负责"目录内编排"（manifest/transcript/media），不关心下载/转写本身。
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


log = logging.getLogger("medialab.publishing")

MANIFEST_NAME = "manifest.json"
ARTICLE_NAME = "article.md"
TRANSCRIPT_NAME = "transcript.json"

# 浏览器保证可播的编码（echo file-format §6）
PLAYABLE_VIDEO_MIME = "video/mp4"
PLAYABLE_AUDIO_MIMES = {"audio/mpeg", "audio/mp4"}
COPY_VIDEO_EXTS = {".mp4"}          # 已是 H.264+AAC 时直接复制（需 ffprobe 确认）
COPY_AUDIO_EXTS = {".mp3", ".m4a"}  # 浏览器可直接播放，无需转码
TRANSCODE_AUDIO_EXT = ".m4a"        # 其余音频统一转 AAC

_SOURCE_TYPE_TO_ECHO = {
    "video": "video",
    "audio": "audio",
    "article": "article",
    "mixed": "mixed",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def derive_id(platform: str, video_id: str | None = None, url: str | None = None) -> str:
    """稳定唯一的 manifest id：文件夹重命名后不变，同一内容重复抓取相同。

    - 视频：vid-{platform}-{video_id}（yt-dlp info 的平台视频 id）
    - 文章/无视频 id：art-{platform}-{url sha256 前 8 位}
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "", platform or "unknown") or "unknown"
    if video_id:
        return f"vid-{safe}-{video_id}"
    digest = hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:8]
    return f"art-{safe}-{digest}"


def load_manifest_id(task_dir: Path) -> str | None:
    """读已存在目录的 manifest id；无 manifest 或解析失败返回 None。"""
    mf = task_dir / MANIFEST_NAME
    if not mf.is_file():
        return None
    try:
        return (json.loads(mf.read_text(encoding="utf-8")) or {}).get("id")
    except Exception:  # noqa: BLE001 目录里可能有用户手改坏的文件
        return None


def pick_task_dir(delivery: Path, title: str | None, task_id: str, manifest_id: str | None) -> Path:
    """选择产物目录（决策 2：同名加后缀）。

    - 目录不存在 → 新建
    - 目录已存在且 manifest.id == 本次 id → 并入（同内容幂等重跑）
    - 目录已存在但无 manifest 或 id 不同 → 加后缀 -2/-3/...（不同内容不得合并）
    """
    if manifest_id and delivery.is_dir():
        matches = [
            child for child in delivery.iterdir()
            if child.is_dir() and not child.name.startswith(".")
            and load_manifest_id(child) == manifest_id
        ]
        if len(matches) > 1:
            raise RuntimeError(f"资料库存在重复 manifest.id，拒绝继续覆盖: {manifest_id}")
        if matches:
            return matches[0]
    base = safe_title(title or task_id)
    cand = delivery / base
    idx = 2
    while cand.exists():
        if manifest_id and load_manifest_id(cand) == manifest_id:
            return cand
        cand = delivery / f"{base}-{idx}"
        idx += 1
    return cand


def safe_title(title: str, max_len: int = 80) -> str:
    """清洗为安全目录名（Windows 非法字符/首尾空白/长度）。"""
    name = re.sub(r'[<>:"/\\|?*]', "_", title)
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = name.strip(" .")
    if not name:
        name = "untitled"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name


def _iso_date(value: str | None) -> str | None:
    """发布时间归一化：yt-dlp upload_date（YYYYMMDD）→ ISO；已是 ISO 的返回原样；无效返回 None。"""
    if not value:
        return None
    v = value.strip()
    if len(v) == 8 and v.isdigit():
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}T00:00:00+08:00"
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v
    return None


@dataclass
class MediaEntry:
    kind: str                 # "video" | "audio"
    original_path: str        # 相对 manifest 的 media/original.ext
    playback_path: str        # 相对 manifest 的 media/playback.ext
    mime_type: str
    title: str
    id: str = "primary"
    role: str = "primary"    # "primary" | "inline" | "attachment"
    description: str | None = None
    poster_path: str | None = None

    def to_manifest(self) -> dict:
        value = {
            "id": self.id,
            "role": self.role,
            "type": self.kind,
            "original_path": self.original_path,
            "playback_path": self.playback_path,
            "mime_type": self.mime_type,
            "title": self.title,
            "description": self.description,
            "poster_path": self.poster_path,
        }
        return {key: item for key, item in value.items() if item not in (None, "")}


def write_manifest(
    task_dir: Path,
    *,
    manifest_id: str,
    source_type: str,                    # video / audio / article
    source_url: str | None = None,
    source_title: str | None = None,     # 原标题（英文视频等），作副标题
    source_description: str | None = None,
    source_author: str | None = None,
    source_published_at: str | None = None,
    has_article: bool = False,
    transcript_language: str | None = None,
    media: MediaEntry | None = None,
    media_items: list[MediaEntry] | None = None,
    warnings: list[str] | None = None,
) -> Path:
    """生成/更新 manifest.json（幂等：同 id 保留 created_at；重跑只更新 updated_at）。"""
    existing = None
    mf = task_dir / MANIFEST_NAME
    if mf.is_file():
        try:
            existing = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = None

    created = (existing or {}).get("created_at") or now_iso()
    article: dict = {"path": ARTICLE_NAME}
    if existing and existing.get("article", {}).get("content_source"):
        article["content_source"] = existing["article"]["content_source"]
    transcript: dict = {"path": TRANSCRIPT_NAME}
    if transcript_language:
        transcript["language"] = transcript_language
    elif existing and existing.get("transcript", {}).get("language"):
        transcript["language"] = existing["transcript"]["language"]

    source = {
        "type": source_type,
        "url": source_url or ((existing or {}).get("source") or {}).get("url"),
        "title": source_title,
        "description": source_description,
        "author": source_author,
        "published_at": _iso_date(source_published_at) if source_published_at else None,
    }
    source = {k: v for k, v in source.items() if v}
    if existing and existing.get("source"):
        source = {**existing["source"], **{k: v for k, v in source.items() if v}}
        source["type"] = source_type

    manifest = {
        "schema_version": 1,
        "id": manifest_id,
        "created_at": created,
        "updated_at": now_iso(),
        "source": source,
        "article": article,
        "transcript": transcript,
        "primary_media": None,
    }
    declared_media = list(media_items or ([] if media is None else [media]))
    if declared_media:
        primary = next((item for item in declared_media if item.role == "primary"), declared_media[0])
        manifest["primary_media"] = {
            "type": primary.kind,
            "original_path": primary.original_path,
            "playback_path": primary.playback_path,
            "mime_type": primary.mime_type,
            "title": primary.title,
        }
        manifest["media_items"] = [item.to_manifest() for item in declared_media]
    elif existing and existing.get("primary_media"):
        manifest["primary_media"] = existing["primary_media"]
        if existing.get("media_items"):
            manifest["media_items"] = existing["media_items"]
    declared_warnings = list(dict.fromkeys(warning for warning in (warnings or []) if warning))
    if declared_warnings:
        manifest["warnings"] = declared_warnings
    elif existing and existing.get("warnings"):
        manifest["warnings"] = existing["warnings"]
    task_dir.mkdir(parents=True, exist_ok=True)
    pending = mf.with_name(f".{mf.name}.tmp")
    pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(mf)
    return mf


# ---------- transcript.json（echo 格式） ----------

def build_transcript(segments: list[dict], language: str, speaker_label: str = "说话人") -> dict:
    """medialab 段（秒，speaker_id 数字）→ echo transcript.json（毫秒，id，speaker 字符串）。

    约束：id 非空唯一；start_ms 升序；end_ms > start_ms（相邻允许重叠/间隔）。
    """
    ordered = sorted(segments or [], key=lambda s: float(s.get("start") or 0))
    out: list[dict] = []
    for i, s in enumerate(ordered, start=1):
        start_ms = max(0, int(round(float(s["start"]) * 1000)))
        end_ms = max(start_ms + 1, int(round(float(s["end"]) * 1000)))
        sid = s.get("speaker_id")
        speaker = f"{speaker_label}{sid}" if sid is not None else "未知说话人"
        out.append({
            "id": f"s{i:04d}",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "speaker": speaker,
            "text": s.get("text") or "",
        })
    return {
        "schema_version": 1,
        "language": language,
        "media_id": "primary",
        "segments": out,
    }


def detect_language(segments: list[dict], zh_threshold: float = 0.2) -> str:
    """Return a conservative language tag from transcript scripts.

    Provider/model language remains authoritative when supplied by online ASR.
    Script fallback deliberately returns ``und`` instead of mislabelling unknown
    scripts as English.
    """
    text = "".join(str(segment.get("text") or "") for segment in segments)
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return "und"
    if any("\u3040" <= char <= "\u30ff" for char in letters):
        return "ja"
    if any("\uac00" <= char <= "\ud7af" or "\u1100" <= char <= "\u11ff" for char in letters):
        return "ko"
    han = sum("\u3400" <= char <= "\u9fff" for char in letters)
    if han / len(letters) >= zh_threshold:
        return "zh-CN"
    latin = sum(
        "a" <= char.casefold() <= "z" or "\u00c0" <= char <= "\u024f"
        for char in letters
    )
    return "en" if latin / len(letters) >= 0.8 else "und"


def write_transcript(task_dir: Path, segments: list[dict], language: str,
                     speaker_label: str = "说话人") -> Path:
    """转录段可以整体重写（时间戳不保证跨版本稳定）。"""
    task_dir.mkdir(parents=True, exist_ok=True)
    p = task_dir / TRANSCRIPT_NAME
    p.write_text(json.dumps(build_transcript(segments, language, speaker_label),
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------- media/ 编排 ----------

def _ffprobe(path: Path, ffmpeg_path: str | None) -> dict | None:
    """探测容器：{video_codec, audio_codec}；失败返回 None（保守转码）。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe and ffmpeg_path:
        ffprobe = str(Path(ffmpeg_path).with_name("ffprobe.exe"))
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "stream=codec_type,codec_name", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or "{}")
        video = audio = None
        for st in data.get("streams") or []:
            if st.get("codec_type") == "video":
                video = st.get("codec_name")
            elif st.get("codec_type") == "audio":
                audio = st.get("codec_name")
        return {"video_codec": video, "audio_codec": audio}
    except Exception:  # noqa: BLE001
        return None


def _is_playable_copy(src: Path, ffprobe: dict | None) -> bool:
    """决策 3：源已是浏览器可播编码时直接复制为 playback，跳过转码。"""
    ext = src.suffix.lower()
    if ffprobe is None:
        return ext in (".mp3", ".m4a")
    if ffprobe.get("video_codec"):
        return ext == ".mp4" and ffprobe["video_codec"] in ("h264", "avc1") and (ffprobe.get("audio_codec") or "").startswith("aac")
    return ext in COPY_AUDIO_EXTS


# H.264 编码器按优先级选择。mise 固定的 BtbN "lgpl" FFmpeg 构建不含 GPL 的 libx264，
# 其中可用 libopenh264（BSD-2-Clause）；用户自己的 FFmpeg 若带 libx264 则优先用它。
_H264_ENCODERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("libx264", ("-preset", "medium", "-crf", "23")),
    ("libopenh264", ("-rc_mode", "quality", "-b:v", "2500k")),
    ("h264_mf", ("-b:v", "2500k")),
)


@functools.lru_cache(maxsize=8)
def _available_encoders(ffmpeg_path: str) -> frozenset[str]:
    try:
        out = subprocess.run([ffmpeg_path, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in (out.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] == "V":
            names.add(parts[1])
    return frozenset(names)


def _h264_encoder_args(ffmpeg_path: str) -> list[str]:
    available = _available_encoders(ffmpeg_path)
    for name, extra in _H264_ENCODERS:
        if name in available:
            return ["-c:v", name, *extra]
    raise RuntimeError("playback 转码失败: FFmpeg 没有可用的 H.264 编码器（需要 libx264、libopenh264 或 h264_mf）")


def _transcode_media(src: Path, dst: Path, ffmpeg_path: str, ffprobe: dict | None) -> None:
    """转码 playback（H.264+AAC 视频 / AAC 音频），与 echo producer-guide §6 一致。"""
    binary = ffmpeg_path or "ffmpeg"
    cmd = [binary]
    if ffprobe and ffprobe.get("video_codec"):
        cmd += ["-i", str(src), *_h264_encoder_args(binary),
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", "-y", str(dst)]
    else:
        cmd += ["-i", str(src), "-c:a", "aac", "-b:a", "128k", "-y", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"playback 转码失败: {proc.stderr[-300:]}")


def _safe_media_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", value or "media").strip("-")
    return (safe or "media")[:64]


def publish_media(task_dir: Path, src_files: list[Path], ffmpeg_path: str = "",
                  media_title: str = "", *, media_id: str = "primary",
                  role: str = "primary", description: str | None = None,
                  poster_path: str | None = None) -> MediaEntry | None:
    """把下载产物编排进 media/：主媒体 → original.* + playback.*，其余文件保留原名放 media/。

    - 主媒体选择：优先视频文件，其次音频文件（第一个）
    - 原始文件保留原样；playback 按编码直接复制或 ffmpeg 转码
    - 返回主媒体声明（manifest primary_media）；无文件返回 None
    """
    if not src_files:
        return None
    safe_id = _safe_media_id(media_id)
    media_dir = task_dir / "media"
    if safe_id != "primary" or role != "primary":
        media_dir = media_dir / safe_id
    media_dir.mkdir(parents=True, exist_ok=True)

    def pick_main() -> Path:
        for f in src_files:
            if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v"):
                return f
        return src_files[0]

    main = pick_main()
    kind = "video" if main.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v") else "audio"
    original = media_dir / f"original{main.suffix.lower() or '.bin'}"
    if main.resolve() != original.resolve():
        shutil.move(str(main), str(original))

    others = [f for f in src_files if f != main]
    for f in others:
        if f.is_file():
            dst = media_dir / f.name
            if f.resolve() != dst.resolve():
                shutil.move(str(f), str(dst))

    if kind == "audio" and original.suffix.lower() in COPY_AUDIO_EXTS:
        playback = media_dir / "playback.mp3" if original.suffix.lower() == ".mp3" else media_dir / "playback.m4a"
        mime = "audio/mpeg" if original.suffix.lower() == ".mp3" else "audio/mp4"
        if not playback.exists():
            shutil.copy2(str(original), str(playback))
    else:
        probe = _ffprobe(original, ffmpeg_path or None)
        if _is_playable_copy(original, probe):
            playback = media_dir / "playback.mp4"
            mime = PLAYABLE_VIDEO_MIME
            if not playback.exists():
                shutil.copy2(str(original), str(playback))
        else:
            dst_name = "playback.mp4" if kind == "video" else "playback.m4a"
            playback = media_dir / dst_name
            mime = PLAYABLE_VIDEO_MIME if kind == "video" else "audio/mp4"
            if not playback.exists():
                _transcode_media(original, playback, ffmpeg_path, probe)

    return MediaEntry(
        id=safe_id,
        role=role,
        kind=kind,
        original_path=str(original.relative_to(task_dir)).replace("\\", "/"),
        playback_path=str(playback.relative_to(task_dir)).replace("\\", "/"),
        mime_type=mime,
        title=media_title,
        description=description,
        poster_path=poster_path,
    )
