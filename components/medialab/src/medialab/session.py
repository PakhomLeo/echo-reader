"""session.py — 浏览器会话抽象（P1/P2 会话路线核心）。

- FailureCode：结构化错误分类（Q16），策略升级的机器可读依据
- BrowserSession / SessionProvider：DownloadService 可注入的会话接口（Q9）
- ExtensionSessionProvider：P2 实现（连接 browser-extension/ 的 WebSocket Cookie Broker）
- write_netscape_file：chrome.cookies 列表 → Netscape 格式文件（yt-dlp 可读）
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import resolve_dir
from .cookies import normalize_chrome_cookie

log = logging.getLogger("medialab.session")


def url_for_log(url: str) -> str:
    """Return a URL safe for logs by removing query parameters and fragments."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except (TypeError, ValueError):
        return "<invalid-url>"


class FailureCode(str, Enum):
    """结构化下载失败分类。

    ``tasks.error_code`` 是面向任务列表的四类摘要；精确分类写入
    ``error_detail`` 前缀，例如 ``[auth_required] ...``。
    """

    AUTH_REQUIRED = "auth_required"              # 需要登录/会话
    SESSION_REJECTED = "session_rejected"        # 会话被服务端拒绝
    COOKIE_ROTATED = "cookie_rotated"            # cookie 被轮换/撤销
    JS_RUNTIME_MISSING = "js_runtime_missing"    # 缺 JS runtime
    EJS_UNAVAILABLE = "ejs_unavailable"          # EJS 不可用
    PO_PROVIDER_UNAVAILABLE = "po_provider_unavailable"
    PO_TOKEN_REJECTED = "po_token_rejected"
    MEDIA_403 = "media_403"                      # 取流 403（GVS 等）
    RATE_LIMITED = "rate_limited"
    DRM_PROTECTED = "drm_protected"
    NETWORK_MISMATCH = "network_mismatch"        # 浏览器与下载器网络路径不一致
    FORMAT_UNAVAILABLE = "format_unavailable"
    CONTENT_NOT_FOUND = "content_not_found"      # 文章抓取：未提取到正文
    CAPTCHA_REQUIRED = "captcha_required"        # 文章抓取：需用户在页面完成验证
    NETWORK = "network"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    TRANSCRIPT_QUALITY = "transcript_quality"


# 精确短语匹配（避免路径中的 "cookies" 字样误判，如 session.cookies 转码失败）
_AUTH_MARKERS = (
    "cookie file", "cookie database", "cookies needed", "cookies are needed",
    "fresh cookies", "no longer valid",
    "not logged in", "sign in", "not a bot", "login required", "could not copy",
)
_NETWORK_MARKERS = ("timed out", "connection", "timeout",
                    "http error 503", "http error 412", "http error 429")
_UNSUPPORTED_MARKERS = ("unsupported", "not a supported")
_RATE_MARKERS = ("http error 429", "rate limit", "too many requests")
_DRM_MARKERS = ("drm", "sabr")
_CAPTCHA_MARKERS = ("captcha", "验证码", "安全验证", "请完成验证", "recaptcha")
_CONTENT_MARKERS = ("no article content", "未提取到正文", "文章内容为空", "no content extracted")
_PO_REJECTED_MARKERS = (
    "po token is invalid",
    "po token was rejected",
    "po token required",
    "missing a po token",
    "po_token rejected",
)


def classify_error(e: Exception) -> FailureCode:
    """把异常映射为结构化 FailureCode（Q16/Q8：auth 类是会话升级的依据）。"""
    msg = str(e).lower()
    if any(marker in msg for marker in _PO_REJECTED_MARKERS):
        return FailureCode.PO_TOKEN_REJECTED
    if any(m in msg for m in _AUTH_MARKERS):
        if "rotat" in msg or "no longer valid" in msg:
            return FailureCode.COOKIE_ROTATED
        return FailureCode.AUTH_REQUIRED
    if any(m in msg for m in _RATE_MARKERS):
        return FailureCode.RATE_LIMITED
    if any(m in msg for m in _CAPTCHA_MARKERS):
        return FailureCode.CAPTCHA_REQUIRED
    if any(m in msg for m in _CONTENT_MARKERS):
        return FailureCode.CONTENT_NOT_FOUND
    if any(m in msg for m in _NETWORK_MARKERS):
        return FailureCode.NETWORK
    if any(m in msg for m in _DRM_MARKERS):
        return FailureCode.DRM_PROTECTED
    if any(m in msg for m in _UNSUPPORTED_MARKERS):
        return FailureCode.UNSUPPORTED
    if "403" in msg:
        return FailureCode.MEDIA_403
    if "js runtime" in msg or "javascript runtime" in msg:
        return FailureCode.JS_RUNTIME_MISSING
    return FailureCode.UNKNOWN


@dataclass
class BrowserSession:
    """一次下载任务使用的会话上下文（Q9）。

    cookie_file 是当前任务独占的临时 Netscape 文件路径。
    YouTube 专用字段（P3b，最小闭环）：
    - visitor_data：受信页面的访客数据（--extractor-args youtube:visitor_data=...）
    - po_token：尽力生成的 PO Token（player/gvs 按需，可选）
    - po_token_scope：token 作用域（player/gvs）
    """

    cookie_file: Path | None = None
    headers: dict[str, str] = field(default_factory=dict)
    proxy: str | None = None
    visitor_data: str | None = None
    po_token: str | None = None
    po_token_scope: str | None = None
    credential_snapshot: CredentialSnapshot | None = None


@dataclass(frozen=True, repr=False)
class CredentialSnapshot:
    """Immutable process-memory credentials; repr never exposes Cookie values."""

    platform: str
    domains: tuple[str, ...]
    generation: int
    revision: int
    user_agent: str
    cookies: tuple[dict, ...]
    visitor_data: str | None = None
    po_token: str | None = None
    po_token_scope: str | None = None

    def __repr__(self) -> str:
        return (
            "CredentialSnapshot("
            f"platform={self.platform!r}, domains={self.domains!r}, "
            f"generation={self.generation}, revision={self.revision}, "
            f"user_agent={self.user_agent!r}, cookies=<redacted:{len(self.cookies)}>, "
            f"visitor_data={bool(self.visitor_data)!r}, po_token={bool(self.po_token)!r}, "
            f"po_token_scope={self.po_token_scope!r})"
        )


@dataclass
class _CredentialState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    generation: int = 0
    snapshot: CredentialSnapshot | None = None


class CredentialBroker:
    """Process-local, thread-safe, no-TTL credential cache for every platform."""

    def __init__(self, bridge, *, mint_youtube: bool = True):
        self._bridge = bridge
        self._mint_youtube = mint_youtube
        self._states_lock = threading.Lock()
        self._states: dict[tuple[str, tuple[str, ...]], _CredentialState] = {}

    @staticmethod
    def _key(platform: str, domains) -> tuple[str, tuple[str, ...]]:
        normalized_platform = str(platform).strip().casefold()
        if not normalized_platform:
            raise ValueError("平台名不能为空")
        canonical = tuple(sorted({
            "." + str(domain).strip().casefold().lstrip(".")
            for domain in domains
            if str(domain).strip().lstrip(".")
        }))
        if not canonical:
            raise ValueError("会话域集合不能为空")
        return normalized_platform, canonical

    def _state(self, key: tuple[str, tuple[str, ...]]) -> _CredentialState:
        with self._states_lock:
            return self._states.setdefault(key, _CredentialState())

    def acquire(self, url: str, platform: str, job_id: str,
                domains) -> CredentialSnapshot:
        key = self._key(platform, domains)
        state = self._state(key)
        with state.lock:
            if state.snapshot is None:
                state.generation += 1
                state.snapshot = self._request(
                    url, job_id, key, generation=state.generation,
                )
            return state.snapshot

    def refresh_if_current(self, snapshot: CredentialSnapshot, url: str,
                           job_id: str) -> CredentialSnapshot:
        key = self._key(snapshot.platform, snapshot.domains)
        state = self._state(key)
        with state.lock:
            current = state.snapshot
            if current is not None and current.generation != snapshot.generation:
                return current
            if current is None and state.generation != snapshot.generation:
                raise RuntimeError("凭据快照已由其他任务判定失效")
            state.generation = max(state.generation, snapshot.generation) + 1
            state.snapshot = None
            state.snapshot = self._request(
                url, job_id, key, generation=state.generation,
            )
            return state.snapshot

    def update_if_current(self, snapshot: CredentialSnapshot, cookies: list[dict]) -> bool:
        """CAS a successful curl cookie jar back into this process's cache."""
        key = self._key(snapshot.platform, snapshot.domains)
        state = self._state(key)
        with state.lock:
            current = state.snapshot
            if (current is None or current.generation != snapshot.generation
                    or current.revision != snapshot.revision
                    or not self._cookies_allowed(cookies, snapshot.domains)):
                return False
            state.snapshot = CredentialSnapshot(
                platform=current.platform,
                domains=current.domains,
                generation=current.generation,
                revision=current.revision + 1,
                user_agent=current.user_agent,
                cookies=tuple(dict(cookie) for cookie in cookies),
                visitor_data=current.visitor_data,
                po_token=current.po_token,
                po_token_scope=current.po_token_scope,
            )
            return True

    def health_report(self, *, now: float | None = None) -> list[dict]:
        """Offline health of every cached snapshot; never contacts the browser."""
        from .cookie_health import check_cookie_health

        with self._states_lock:
            items = list(self._states.items())
        report = []
        for (platform, domains), state in items:
            with state.lock:
                snapshot = state.snapshot
            entry = check_cookie_health(platform, snapshot.cookies if snapshot else None, now=now)
            entry["domains"] = list(domains)
            entry["cached"] = snapshot is not None
            entry["generation"] = state.generation
            report.append(entry)
        report.sort(key=lambda e: (e["platform"], e["domains"]))
        return report

    @staticmethod
    def _cookies_allowed(cookies, domains: tuple[str, ...]) -> bool:
        for cookie in cookies:
            if not isinstance(cookie, dict):
                return False
            domain = str(cookie.get("domain") or "").strip().casefold().lstrip(".")
            if not domain or not any(
                domain == allowed.lstrip(".")
                or domain.endswith("." + allowed.lstrip("."))
                for allowed in domains
            ):
                return False
        return True

    def _request(self, url: str, job_id: str,
                 key: tuple[str, tuple[str, ...]], generation: int) -> CredentialSnapshot:
        if self._bridge is None:
            raise RuntimeError("插件未连接")
        platform, domains = key
        response = self._bridge.request_session(url, job_id, list(domains))
        if not isinstance(response, dict) or not response.get("ok"):
            error = str((response or {}).get("error") or "session_rejected")
            raise RuntimeError(f"插件拒绝提供会话: {error}")
        cookies = response.get("cookies")
        if cookies is None:
            cookies = []
        if not isinstance(cookies, list) or not self._cookies_allowed(cookies, domains):
            raise RuntimeError("插件未返回有效的授权域 Cookie")
        visitor_data = po_token = po_token_scope = None
        if platform == "youtube" and self._mint_youtube:
            try:
                minted = self._bridge.request_mint(
                    url, job_id, client="mweb", token_scope="player",
                )
                if isinstance(minted, dict) and minted.get("ok"):
                    visitor_data = minted.get("visitor_data") or None
                    po_token = minted.get("po_token") or None
                    po_token_scope = minted.get("token_scope") or None
                else:
                    log.warning("YouTube 会话铸造失败（仅使用 Cookie）: %s",
                                (minted or {}).get("error"))
            except Exception as exc:  # noqa: BLE001
                log.warning("YouTube 会话铸造异常（仅使用 Cookie）: %s", exc)
        return CredentialSnapshot(
            platform=platform,
            domains=domains,
            generation=generation,
            revision=0,
            user_agent=str(response.get("user_agent") or "").strip(),
            cookies=tuple(dict(cookie) for cookie in cookies),
            visitor_data=visitor_data,
            po_token=po_token,
            po_token_scope=po_token_scope,
        )


class SessionProvider:
    """会话提供方抽象（Q9）；现役实现只通过 SessionBridge 插件取会话。"""

    def get_session(self, *, url: str, platform: str, job_id: str) -> BrowserSession:
        raise NotImplementedError

    def refresh_session(self, *, stale_session: BrowserSession, url: str,
                        platform: str, job_id: str) -> BrowserSession:
        raise NotImplementedError


def write_netscape_file(cookies: list[dict], path: Path) -> Path:
    """chrome.cookies 列表 → Netscape cookies.txt（yt-dlp cookiefile 可读）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File"]
    for raw in cookies:
        c = normalize_chrome_cookie(raw)
        if c is None:
            continue
        domain = c.get("domain") or ""
        name = c.get("name") or ""
        value = c.get("value") or ""
        path_s = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = int(c.get("expires") or 0)
        sub = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append(f"{domain}\t{sub}\t{path_s}\t{secure}\t{exp}\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_netscape_file(path: Path) -> list[dict]:
    """Read yt-dlp's final Netscape jar without logging Cookie values."""
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return [
        {
            "domain": cookie.domain or "",
            "name": cookie.name,
            "value": cookie.value,
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "expirationDate": cookie.expires or 0,
        }
        for cookie in jar
    ]


class ExtensionSessionProvider(SessionProvider):
    """P2 实现：通过本地 SessionBridgeServer 向 Chrome 插件（browser-extension/）请求会话。

    方向说明：Chrome 扩展是 WS 客户端，主动连接本地 SessionBridgeServer；
    本 Provider 与 bridge 同进程（进程内 request_session），由 bridge 转发请求给插件。
    YouTube（P3b）：额外请求"会话铸造"（visitor_data + 尽力 PO Token），失败降级仅 Cookie。
    """

    def __init__(self, bridge, cfg: dict | None = None, mint_youtube: bool = True,
                 credential_broker: CredentialBroker | None = None):
        self.bridge = bridge
        self.cfg = cfg
        self.credential_broker = (
            credential_broker
            if credential_broker is not None
            else CredentialBroker(bridge, mint_youtube=mint_youtube)
        )

    def get_session(self, *, url: str, platform: str, job_id: str) -> BrowserSession:
        from .platforms import session_domains

        domains = session_domains(platform, self.cfg, url=url)
        snapshot = self.credential_broker.acquire(url, platform, job_id, domains)
        return self._materialize(snapshot)

    def refresh_session(self, *, stale_session: BrowserSession, url: str,
                        platform: str, job_id: str) -> BrowserSession:
        snapshot = stale_session.credential_snapshot
        if snapshot is None:
            raise RuntimeError("待刷新会话没有凭据快照")
        refreshed = self.credential_broker.refresh_if_current(snapshot, url, job_id)
        return self._materialize(refreshed)

    def commit_session(self, session: BrowserSession) -> bool:
        """CAS yt-dlp's final Cookie jar back into the process cache."""
        snapshot = session.credential_snapshot
        cookie_file = session.cookie_file
        if snapshot is None or cookie_file is None or not cookie_file.is_file():
            return False
        return self.credential_broker.update_if_current(
            snapshot, read_netscape_file(cookie_file),
        )

    def _materialize(self, snapshot: CredentialSnapshot) -> BrowserSession:
        paths = (self.cfg or {}).get("paths") or {}
        temp_dir = resolve_dir({"temp": "tmp", **paths}, "temp")
        cookie_file = write_netscape_file(
            list(snapshot.cookies),
            temp_dir / f"session-{uuid.uuid4().hex}.cookies",
        )
        return BrowserSession(
            cookie_file=cookie_file,
            visitor_data=snapshot.visitor_data,
            po_token=snapshot.po_token,
            po_token_scope=snapshot.po_token_scope,
            credential_snapshot=snapshot,
        )


def is_auth_failure(e: Exception) -> bool:
    """Q8：auth 类错误是"带会话重试一次"的触发条件。"""
    return classify_error(e) in (
        FailureCode.AUTH_REQUIRED,
        FailureCode.SESSION_REJECTED,
        FailureCode.COOKIE_ROTATED,
        FailureCode.NETWORK_MISMATCH,
        FailureCode.PO_TOKEN_REJECTED,
    )
