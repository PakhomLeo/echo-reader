"""yt-dlp 封装：白名单、cookie、进度节流、分层重试、隔离目录。"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from yt_dlp import ImpersonateTarget
from yt_dlp.utils import DownloadError

from . import platforms
from .session import BrowserSession, is_auth_failure

log = logging.getLogger("medialab.downloader")

RISK_CODES = (503, 412, 429)
RISK_PLATFORMS = {"bilibili", "douyin"}
BROWSER_IMPERSONATE_TARGET = ImpersonateTarget("chrome", "146")
_YDL_CONSTRUCTOR_LOCK = threading.Lock()


@dataclass
class DownloadResult:
    files: list[Path] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    retries_used: int = 0
    session: BrowserSession | None = None


class MediaProbeLimitError(RuntimeError):
    """The page exposes more media entries than the configured automatic-import cap."""


@dataclass(frozen=True)
class MediaProbe:
    url: str
    kind: str
    media_id: str | None = None
    title: str | None = None
    description: str | None = None
    extractor: str | None = None


def _format_for_progress(d: dict) -> dict:
    return {
        "status": d.get("status"),
        "downloaded_bytes": d.get("downloaded_bytes"),
        "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate"),
        "speed": d.get("speed"),
        "eta": d.get("eta"),
        "filename": d.get("filename"),
    }


class ThrottledProgress:
    def __init__(self, interval: float, emit):
        self._interval = interval
        self._emit = emit
        self._last = 0.0

    def __call__(self, d):
        if d.get("status") in ("downloading", "finished"):
            now = time.monotonic()
            if now - self._last >= self._interval:
                self._last = now
                self._emit(_format_for_progress(d))


def build_opts(cfg: dict, url: str, fmt: str, outtmpl: str, on_progress, audio_codec: str | None,
               session: "BrowserSession | None" = None, with_cookies: bool = True) -> dict:
    platform = platforms.detect_platform(url)
    d = cfg["download"]
    opts: dict = {
        "outtmpl": outtmpl,
        "format": "ba/b" if audio_codec else fmt,
        "noplaylist": False,
        "merge_output_format": cfg["download"].get("merge_output_format", "mp4"),
        "writethumbnail": False,
        "concurrent_fragment_downloads": d.get("concurrent_fragment_downloads", 4),
        "retries": d.get("retries", 10),
        "fragment_retries": d.get("fragment_retries", 10),
        "progress_hooks": [ThrottledProgress(cfg["progress"].get("throttle", 1.0), on_progress)],
        "allowed_extractors": platforms.allowed_extractors(cfg),
        # yt-dlp's official curl_cffi request handler supplies one coherent
        # browser network profile for extractor pages, manifests and media.
        # curl-cffi 0.15.0 is pinned because yt-dlp 2026.7.4 rejects 0.16.x.
        "impersonate": BROWSER_IMPERSONATE_TARGET,
        "quiet": True,
        "no_warnings": False,
    }
    if audio_codec:
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": audio_codec}]
    if platform == "youtube":
        proxy = d.get("proxy")
        if proxy:
            opts["proxy"] = proxy
        js = d.get("js_runtimes")
        if js:
            opts["js_runtimes"] = {r: {} for r in js}
        rc = d.get("remote_components")
        if rc:
            opts["remote_components"] = list(rc)
    if with_cookies:
        _apply_cookie_opts(opts, platform, session)
    return opts


def _apply_cookie_opts(opts: dict, platform: str, session: "BrowserSession | None") -> None:
    """Inject the task session exported by SessionBridge, when present."""
    if session is not None:
        if session.proxy:
            opts["proxy"] = session.proxy
        if session.cookie_file:
            opts["cookiefile"] = str(session.cookie_file)
        if session.headers:
            merged = dict(opts.get("http_headers") or {})
            merged.update({k: v for k, v in session.headers.items() if k and v})
            if merged:
                opts["http_headers"] = merged
        # YouTube 会话绑定（P3b）：visitor_data / po_token 与 cookie 必须同源
        if platform == "youtube" and (session.visitor_data or session.po_token):
            args = opts.get("extractor_args") or {}
            yt_args = list(args.get("youtube", []))
            if session.visitor_data:
                yt_args.append(f"visitor_data={session.visitor_data}")
            if session.po_token:
                scope = session.po_token_scope or "player"
                yt_args.append(f"po_token={session.po_token}:{scope}")
            args["youtube"] = yt_args
            opts["extractor_args"] = args
        return


def _is_risk_error(err: str) -> bool:
    return any(str(code) in err for code in RISK_CODES) or "Service Temporarily Unavailable" in err


def _run_ydl(opts: dict, url: str) -> tuple[dict, list[Path]]:
    """返回 (info_dict, 已产出文件列表)。"""
    with _new_ydl(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        files: list[Path] = []
        for d in info.get("requested_downloads") or []:
            p = d.get("filepath")
            if p and Path(p).exists():
                files.append(Path(p))
        if not files:
            fallback = ydl.prepare_filename(info)
            if fallback and Path(fallback).exists():
                files.append(Path(fallback))
    return info, files


def _run_probe_ydl(opts: dict, url: str) -> dict:
    """Resolve metadata only; the returned object is yt-dlp's sanitized form."""
    with _new_ydl(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)


def _new_ydl(opts: dict) -> yt_dlp.YoutubeDL:
    """Serialize plugin discovery while keeping actual downloads concurrent."""
    with _YDL_CONSTRUCTOR_LOCK:
        return yt_dlp.YoutubeDL(opts)


def _probe_entries(info: dict, fallback_url: str, max_entries: int) -> list[MediaProbe]:
    raw_entries = info.get("entries")
    entries = [entry for entry in (raw_entries or []) if isinstance(entry, dict)]
    if not entries:
        entries = [info]
    if len(entries) > max_entries:
        raise MediaProbeLimitError(
            f"页面包含至少 {len(entries)} 个媒体，超过自动抓取上限 {max_entries}"
        )
    result: list[MediaProbe] = []
    seen: set[tuple[str | None, str | None, str]] = set()
    for entry in entries:
        candidate_url = str(
            entry.get("webpage_url")
            or entry.get("original_url")
            or entry.get("url")
            or fallback_url
        ).strip()
        if not candidate_url.startswith("https://"):
            continue
        video_codec = str(entry.get("vcodec") or "").casefold()
        audio_codec = str(entry.get("acodec") or "").casefold()
        kind = "audio" if video_codec in {"", "none"} and audio_codec not in {"", "none"} else "video"
        extractor = str(entry.get("extractor_key") or entry.get("extractor") or "").strip() or None
        media_id = str(entry.get("id") or "").strip() or None
        key = (extractor, media_id, candidate_url)
        if key in seen:
            continue
        seen.add(key)
        result.append(MediaProbe(
            url=candidate_url,
            kind=kind,
            media_id=media_id,
            title=str(entry.get("title") or "").strip() or None,
            description=str(entry.get("description") or "").strip() or None,
            extractor=extractor,
        ))
    return result


def probe_media(
    cfg: dict,
    url: str,
    *,
    session: "BrowserSession | None" = None,
    session_factory=None,
    session_refresher=None,
    max_entries: int = 20,
) -> tuple[list[MediaProbe], BrowserSession | None]:
    """Resolve every finite media entry exposed by one URL without downloading bytes."""
    platform = platforms.detect_platform(url)
    session_first = platform in cfg["download"].get("session_first_platforms", [])
    if session is None and session_first and session_factory is not None:
        session = session_factory()

    def run(active_session, with_cookies: bool):
        opts = build_opts(
            cfg, url, cfg["download"].get("format", "bv*+ba/b"), "probe.%(ext)s",
            lambda _d: None, None, session=active_session, with_cookies=with_cookies,
        )
        opts.update({
            "skip_download": True,
            "simulate": True,
            "extract_flat": "in_playlist",
            "playlistend": max_entries + 1,
            "progress_hooks": [],
        })
        return _probe_entries(_run_probe_ydl(opts, url), url, max_entries)

    if session is not None or session_first:
        try:
            return run(session, True), session
        except DownloadError as exc:
            if session is not None and session_refresher and is_auth_failure(exc):
                refreshed = session_refresher(session)
                return run(refreshed, True), refreshed
            raise
    try:
        return run(None, False), None
    except DownloadError as exc:
        if not is_auth_failure(exc) or session_factory is None:
            raise
        upgraded = session_factory()
        try:
            return run(upgraded, True), upgraded
        except DownloadError as session_error:
            if upgraded is None or session_refresher is None or not is_auth_failure(session_error):
                raise
            refreshed = session_refresher(upgraded)
            return run(refreshed, True), refreshed


def _collect_outputs(tmp_dir: Path) -> list[Path]:
    return [p for p in tmp_dir.iterdir() if p.is_file() and not p.name.endswith(".part")]


def download_video(
    cfg: dict,
    url: str,
    fmt: str | None,
    out_dir: Path,
    tmp_dir: Path,
    audio_codec: str | None = None,
    on_progress=None,
    max_risk_retries: int | None = None,
    session: "BrowserSession | None" = None,
    session_factory=None,
    session_refresher=None,
) -> DownloadResult:
    """分层重试下载。

    1. yt-dlp 内置 retries（配置，默认 10）
    2. 平台风控（503/412）退避重试（risk_retries）
    3. 任务降档重跑（fallback_format，1 次）
    4. 匿名优先（Q7/Q8）：
       - 显式 session 或 session_first 平台：直接带会话尝试
       - 其余平台：先匿名；auth 类失败后经 session_factory 取会话（或配置 cookie 源）重试一次
    """
    platform = platforms.detect_platform(url)
    session_first = platform in cfg["download"].get("session_first_platforms", [])
    if session is None and session_first and session_factory is not None:
        session = session_factory()
    if session is not None or session_first:
        try:
            return _attempt_download(cfg, url, fmt, out_dir, tmp_dir, audio_codec, on_progress,
                                     max_risk_retries, session=session, with_cookies=True)
        except DownloadError as e:
            if session is not None and session_refresher and is_auth_failure(e):
                log.warning("[%s] 缓存会话失效（auth），刷新浏览器凭据后重试", platform)
                return _attempt_download(cfg, url, fmt, out_dir, tmp_dir, audio_codec, on_progress,
                                         max_risk_retries,
                                         session=session_refresher(session),
                                         with_cookies=True)
            raise
    # 匿名优先（Q7）
    try:
        return _attempt_download(cfg, url, fmt, out_dir, tmp_dir, audio_codec, on_progress,
                                 max_risk_retries, session=None, with_cookies=False)
    except DownloadError as e:
        if not is_auth_failure(e):
            raise
        if session_factory is None:
            raise
        upgraded = session_factory()
        log.warning("[%s] 匿名失败（auth），升级带会话重试", platform)
        try:
            return _attempt_download(
                cfg, url, fmt, out_dir, tmp_dir, audio_codec, on_progress,
                max_risk_retries, session=upgraded, with_cookies=True,
            )
        except DownloadError as session_error:
            if (upgraded is None or session_refresher is None
                    or not is_auth_failure(session_error)):
                raise
            log.warning("[%s] 缓存会话失效（auth），刷新浏览器凭据后重试", platform)
            return _attempt_download(
                cfg, url, fmt, out_dir, tmp_dir, audio_codec, on_progress,
                max_risk_retries,
                session=session_refresher(upgraded),
                with_cookies=True,
            )


def _attempt_download(
    cfg: dict, url: str, fmt: str | None, out_dir: Path, tmp_dir: Path,
    audio_codec: str | None, on_progress, max_risk_retries: int | None,
    session: "BrowserSession | None", with_cookies: bool,
) -> DownloadResult:
    """单次（匿名或带会话）下载：内置重试 + 风控退避；风控耗尽后降档格式再试一次。"""
    platform = platforms.detect_platform(url)
    d = cfg["download"]
    max_risk = max_risk_retries if max_risk_retries is not None else d.get("risk_retries", 3)
    backoff = d.get("risk_backoff_base", 30)
    fallback = d.get("fallback_format", "b/best")
    used_fmt = fmt or d.get("format", "bv*+ba/b")

    def run(fmt_used: str) -> DownloadResult:
        outtmpl = str(tmp_dir / "%(title).100s [%(id)s]%(format_id)s.%(ext)s")
        opts = build_opts(cfg, url, fmt_used, outtmpl, on_progress or (lambda d: None), audio_codec,
                          session=session, with_cookies=with_cookies)
        info, _ = _run_ydl(opts, url)
        files = _collect_outputs(tmp_dir)
        return DownloadResult(
            files=[_move_to_delivery(p, out_dir) for p in files],
            info=info,
            session=session,
        )

    last_err: DownloadError | None = None
    fallback_due = False
    for attempt in range(max_risk + 1):
        try:
            return run(used_fmt)
        except DownloadError as e:
            last_err = e
            msg = str(e)
            if _is_risk_error(msg) and platform in RISK_PLATFORMS and attempt < max_risk:
                wait = backoff * (2 ** attempt)
                log.warning("[%s] 风控错误(%s)，%.0fs 后重试 (%d/%d)", platform, msg.strip()[:60], wait, attempt + 1, max_risk)
                time.sleep(wait)
                continue
            fallback_due = attempt == max_risk  # 最后一次尝试失败才降档
            break
    if fallback_due and fallback and fallback != used_fmt:
        log.warning("[%s] 降档重试: %s", platform, fallback)
        time.sleep(2)
        try:
            return run(fallback)
        except DownloadError as e:
            last_err = e
    raise last_err or DownloadError("重试全部失败")


def _move_to_delivery(src: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / src.name
    if src.resolve() != dst.resolve():
        shutil.move(str(src), str(dst))
    return dst
