"""Optional, bounded JavaScript rendering for article discovery.

The renderer deliberately uses a fresh Edge browser and context for every call.
It never resolves host names itself and never uses a persistent browser profile.
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from .cookies import normalize_chrome_cookie


_MODES = {"off", "always", "on_miss"}
_ANALYTICS_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "segment.io",
    "segment.com",
    "mixpanel.com",
    "amplitude.com",
)
_MEDIA_SUFFIXES = (
    ".aac", ".avi", ".flac", ".m3u8", ".m4a", ".m4v", ".mkv",
    ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".ogg", ".opus",
    ".ts", ".wav", ".webm",
)


class JsRenderError(RuntimeError):
    """Rendering could not safely produce a page."""


@dataclass(frozen=True)
class RenderedPage:
    html: str
    final_url: str
    warnings: list[str]
    request_count: int
    bytes_seen: int


def _settings(config: Mapping[str, Any]) -> Mapping[str, Any]:
    section = config.get("js_render", {})
    return section if isinstance(section, Mapping) else {}


def should_render(platform: str, static_probe: Any, config: Mapping[str, Any]) -> bool:
    """Return whether the configured platform policy requests JS rendering."""
    settings = _settings(config)
    if not settings.get("enabled", False):
        return False
    modes = settings.get("platform_modes", {})
    if not isinstance(modes, Mapping):
        return False
    mode = str(modes.get(platform, "off")).strip().lower()
    if mode not in _MODES:
        return False
    if mode == "always":
        return True
    if mode == "off":
        return False
    if static_probe is None:
        return True
    if isinstance(static_probe, Mapping):
        return bool(static_probe.get("render_recommended", False)) or (
            "article" in static_probe and static_probe.get("article") is None
        )
    return bool(getattr(static_probe, "render_recommended", False)) or (
        hasattr(static_probe, "article") and getattr(static_probe, "article") is None
    )


def _validated_https_url(url: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise JsRenderError("invalid render URL") from exc
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password:
        raise JsRenderError("render URL must be an unauthenticated HTTPS URL")
    if port not in (None, 443):
        raise JsRenderError("render URL must use the HTTPS default port")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise JsRenderError("local render hosts are forbidden")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise JsRenderError("private or local IP render hosts are forbidden")
    return url, host


def _host_matches(host: str, domain: str) -> bool:
    domain = domain.lstrip(".").rstrip(".").lower()
    return bool(domain) and (host == domain or host.endswith("." + domain))


def _scoped_cookies(
    cookies: Iterable[Mapping[str, Any]] | None, target_url: str, target_host: str,
) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    cookie_origin = f"https://{target_host}/"
    allowed = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
    for raw in cookies or ():
        if not isinstance(raw, Mapping):
            continue
        normalized = normalize_chrome_cookie(raw)
        if normalized is None:
            continue
        domain = raw.get("domain")
        cookie_url = raw.get("url")
        if isinstance(domain, str):
            if not _host_matches(target_host, domain):
                continue
            item = {key: normalized[key] for key in allowed if key in normalized}
            item.setdefault("path", "/")
        elif isinstance(cookie_url, str):
            try:
                _, cookie_host = _validated_https_url(cookie_url)
            except JsRenderError:
                continue
            if cookie_host != target_host:
                continue
            item = {key: normalized[key] for key in allowed if key in normalized}
            item["url"] = cookie_origin
        else:
            item = {key: normalized[key] for key in allowed if key in normalized}
            item["url"] = cookie_origin
        safe.append(item)
    return safe


def _is_analytics(host: str) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in _ANALYTICS_HOSTS)


def render_page(
    url: str,
    *,
    config: Mapping[str, Any],
    cookies: Iterable[Mapping[str, Any]] | None = None,
) -> RenderedPage:
    """Render ``url`` in an isolated installed Edge instance.

    Playwright is imported only when this function is called. No browser is
    installed or downloaded by this module.
    """
    target_url, target_host = _validated_https_url(url)
    settings = _settings(config)
    try:
        timeout_ms = min(60_000, max(1, int(float(settings.get("timeout_seconds", 20)) * 1000)))
        max_requests = min(200, max(1, int(settings.get("max_requests", 80))))
        max_bytes = min(52_428_800, max(1, int(settings.get("max_bytes", 10_485_760))))
        scroll_steps = min(20, max(0, int(settings.get("scroll_steps", 6))))
        scroll_delay_ms = min(2_000, max(0, int(settings.get("scroll_delay_ms", 300))))
    except (TypeError, ValueError, OverflowError) as exc:
        raise JsRenderError("invalid JS rendering limits") from exc

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise JsRenderError(
            "JS rendering requires the medialab[render] optional dependency"
        ) from exc

    warnings: list[str] = []
    configured_domains = settings.get("resource_domains", ())
    resource_domains = tuple(
        str(domain).strip().casefold() for domain in configured_domains
        if isinstance(domain, str) and str(domain).strip()
    ) if isinstance(configured_domains, (list, tuple)) else ()
    state = {"requests": 0, "bytes": 0, "limit": False}
    browser = None
    context = None
    started = time.monotonic()

    def remaining_ms() -> int:
        remaining = timeout_ms - int((time.monotonic() - started) * 1000)
        if remaining <= 0:
            raise JsRenderError("JS rendering exceeded its time limit")
        return remaining

    def route_request(route: Any) -> None:
        request = route.request
        state["requests"] += 1
        try:
            _, request_host = _validated_https_url(request.url)
        except JsRenderError:
            warnings.append("request_blocked:forbidden_url")
            route.abort()
            return
        if request_host != target_host and not any(
            _host_matches(request_host, domain) for domain in resource_domains
        ):
            warnings.append("request_blocked:forbidden_origin")
            route.abort()
            return
        path = urlsplit(request.url).path.lower()
        resource_type = str(request.resource_type).lower()
        blocked = resource_type in {"font", "media"} or _is_analytics(request_host) or path.endswith(_MEDIA_SUFFIXES)
        if blocked:
            warnings.append(f"request_blocked:{resource_type or 'resource'}")
            route.abort()
        elif state["requests"] > max_requests or state["bytes"] >= max_bytes:
            state["limit"] = True
            warnings.append("request_blocked:limit")
            route.abort()
        else:
            route.continue_()

    def record_response(response: Any) -> None:
        try:
            raw = response.header_value("content-length")
            size = int(raw) if raw else 0
        except (TypeError, ValueError):
            size = 0
        state["bytes"] += max(0, size)
        if state["bytes"] > max_bytes:
            state["limit"] = True

    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                )
                scoped = _scoped_cookies(cookies, target_url, target_host)
                if scoped:
                    context.add_cookies(scoped)
                page = context.new_page()
                page.route("**/*", route_request)
                page.on("response", record_response)
                page.goto(target_url, wait_until="domcontentloaded", timeout=remaining_ms())
                previous_height = None
                for _ in range(scroll_steps):
                    height = page.evaluate("document.documentElement.scrollHeight")
                    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                    if scroll_delay_ms:
                        page.wait_for_timeout(min(scroll_delay_ms, remaining_ms()))
                    if height == previous_height:
                        break
                    previous_height = height
                html = page.content()
                final_url, _ = _validated_https_url(page.url)
                html_size = len(html.encode("utf-8"))
                if html_size > max_bytes:
                    raise JsRenderError("rendered HTML exceeded the byte limit")
                if state["limit"]:
                    warnings.append("resource_limit_reached")
                return RenderedPage(
                    html=html,
                    final_url=final_url,
                    warnings=list(dict.fromkeys(warnings)),
                    request_count=state["requests"],
                    bytes_seen=state["bytes"],
                )
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass
    except JsRenderError:
        raise
    except Exception as exc:
        raise JsRenderError(f"JS rendering failed: {type(exc).__name__}") from exc
