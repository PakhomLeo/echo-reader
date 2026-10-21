"""Canonical URL boundary for runtime, public metadata, identity, and sessions."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .platforms import detect_platform


_TRANSIENT_EXACT = {
    "access_token", "auth", "authorization", "credential", "expires",
    "app", "enter_from", "fbclid", "feature", "from_spmid", "gclid", "key",
    "policy", "poc_token", "previous_page", "share_medium", "share_plat",
    "share_source", "si", "signature", "sig", "spm_id_from", "token",
    "vd_source", "xsec_source", "xsec_token", "xhsshare",
}
_TRANSIENT_PREFIXES = ("utm_", "x-amz-", "x-goog-")


def _is_transient_query_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _TRANSIENT_EXACT or lowered.startswith(_TRANSIENT_PREFIXES)


def _scrub_nested_value(value: str, depth: int) -> str:
    if depth >= 3:
        return value
    try:
        parts = urlsplit(value)
    except (TypeError, ValueError):
        return value
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return value
    return _sanitize(value, depth + 1)


def _sanitize(url: str, depth: int = 0) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return url
    if not host:
        return url
    hostname = host.casefold()
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        netloc += f":{port}"
    query = urlencode(
        [
            (key, _scrub_nested_value(value, depth))
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_transient_query_key(key)
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme.casefold(), netloc, parts.path, query, ""))


def sanitize_persisted_url(url: str) -> str:
    """Return the credential-free URL permitted in metadata, events, and logs."""
    return _sanitize(url)


def _youtube_session_url(runtime_url: str) -> str:
    """Canonicalize a youtu.be link for YouTube credential/session identity only."""
    try:
        parts = urlsplit(runtime_url)
    except (TypeError, ValueError):
        return runtime_url
    if (parts.hostname or "").casefold().rstrip(".") != "youtu.be":
        return runtime_url
    video_id = parts.path.strip("/").split("/", 1)[0]
    if not video_id:
        return runtime_url
    return f"https://www.youtube.com/watch?v={video_id}"


@dataclass(frozen=True, slots=True)
class UrlContext:
    """The four URL forms used by MediaLab; only runtime may contain credentials."""

    runtime_url: str
    public_url: str
    identity_url: str
    session_url: str
    platform: str

    @classmethod
    def from_url(cls, url: str) -> "UrlContext":
        platform = detect_platform(url)
        public_url = sanitize_persisted_url(url)
        session_runtime = _youtube_session_url(url) if platform == "youtube" else url
        return cls(
            runtime_url=url,
            public_url=public_url,
            identity_url=public_url,
            session_url=sanitize_persisted_url(session_runtime),
            platform=platform,
        )
