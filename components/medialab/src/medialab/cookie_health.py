"""Offline Cookie health check for SessionBridge credential snapshots.

Ported from UP's cookie checker/validator, adapted to MediaLab's model where Cookie only
lives in process memory (``CredentialBroker``) and is never written to disk.  The check is
purely local: it inspects cookie *names* and expiry timestamps of a snapshot, performs no
network request and never returns cookie values.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

# How strongly a platform depends on a logged-in browser session in MediaLab.
REQUIRED = "required"        # import fails without a session (XHS single-capture contract)
RECOMMENDED = "recommended"  # public content often works, but login walls / HD need a session
OPTIONAL = "optional"        # session only improves success for some pages
NOT_NEEDED = "not_needed"

COOKIE_REQUIREMENT: dict[str, str] = {
    "xiaohongshu": REQUIRED,
    "youtube": RECOMMENDED,
    "bilibili": RECOMMENDED,
    "douyin": RECOMMENDED,
    "zhihu": OPTIONAL,
    "weibo": OPTIONAL,
    "x": OPTIONAL,
    "tiktok": OPTIONAL,
    "instagram": OPTIONAL,
    "facebook": OPTIONAL,
    "wechat": OPTIONAL,
    "toutiao": OPTIONAL,
}

# Cookie names that indicate an authenticated session (values are never inspected).
LOGIN_COOKIE_NAMES: dict[str, tuple[str, ...]] = {
    "bilibili": ("SESSDATA",),
    "youtube": ("SAPISID", "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO"),
    "xiaohongshu": ("web_session",),
    "zhihu": ("z_c0",),
    "weibo": ("SUB",),
    "douyin": ("sessionid", "sessionid_ss"),
    "x": ("auth_token",),
    "tiktok": ("sessionid",),
    "instagram": ("sessionid",),
    "facebook": ("c_user", "xs"),
}

DEFAULT_EXPIRING_WITHIN = 3 * 24 * 3600


def requirement_for(platform: str) -> str:
    return COOKIE_REQUIREMENT.get(str(platform).casefold(), NOT_NEEDED)


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _expiry(cookie: Mapping[str, Any]) -> float | None:
    raw = cookie.get("expires", cookie.get("expirationDate"))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # Chrome uses -1 / 0 for session cookies.
    return value if value > 0 else None


def check_cookie_health(platform: str, cookies: Iterable[Mapping[str, Any]] | None, *,
                        now: float | None = None,
                        expiring_within: float = DEFAULT_EXPIRING_WITHIN) -> dict[str, Any]:
    """Summarize a cookie set for ``platform`` without exposing any cookie value.

    ``status`` is one of ``ok``, ``expiring_soon``, ``expired``, ``anonymous`` (cookies
    present but no known login cookie), ``missing`` or ``not_needed``.
    """
    platform = str(platform).casefold()
    now = time.time() if now is None else float(now)
    requirement = requirement_for(platform)
    items = [c for c in (cookies or []) if isinstance(c, Mapping) and isinstance(c.get("name"), str)]
    login_names = LOGIN_COOKIE_NAMES.get(platform, ())
    expired = [c for c in items if (_expiry(c) or float("inf")) <= now]
    live = [c for c in items if c not in expired]
    login_live = [c for c in live if c["name"] in login_names]
    login_expired = [c for c in expired if c["name"] in login_names]
    expiries = [e for e in (_expiry(c) for c in login_live) if e is not None]
    earliest = min(expiries) if expiries else None

    if not items:
        status = "not_needed" if requirement == NOT_NEEDED else "missing"
    elif login_names and not login_live:
        status = "expired" if login_expired else "anonymous"
    elif not login_names and not live:
        status = "expired"
    elif earliest is not None and earliest - now <= expiring_within:
        status = "expiring_soon"
    else:
        status = "ok"

    messages = {
        "ok": "会话可用",
        "expiring_soon": "登录 Cookie 即将过期，请在浏览器中重新登录后刷新会话",
        "expired": "登录 Cookie 已过期，请在浏览器中重新登录",
        "anonymous": "仅有匿名 Cookie，需要登录态的内容可能失败",
        "missing": "尚未取得该平台的浏览器会话",
        "not_needed": "该平台不需要浏览器会话",
    }
    return {
        "platform": platform,
        "requirement": requirement,
        "status": status,
        "healthy": status in {"ok", "expiring_soon", "not_needed"}
        or (status in {"anonymous", "missing"} and requirement in {OPTIONAL, NOT_NEEDED}),
        "cookie_count": len(items),
        "expired_count": len(expired),
        "login_cookies_present": sorted({c["name"] for c in login_live}),
        "earliest_login_expiry": _iso(earliest),
        "message": messages[status],
    }
