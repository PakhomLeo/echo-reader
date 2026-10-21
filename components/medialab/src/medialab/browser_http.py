"""Task-scoped Chrome-impersonating HTTP client for content websites.

The browser extension exports only an authorized Cookie snapshot.  Python then
uses one curl_cffi Session for a task's page and resources.  Chrome 146 here is
a TLS/HTTP/default-header profile, not a JavaScript/Canvas/WebGL fingerprint.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests
from curl_cffi.const import CurlOpt

from .cookies import normalize_chrome_cookie
from .session import CredentialBroker, CredentialSnapshot, FailureCode


CHROME_IMPERSONATE = "chrome146"
_LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata.google.internal"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"}
_IMAGE_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class BrowserHttpError(RuntimeError):
    """Safe, structured content-site failure."""

    def __init__(self, code: FailureCode, detail: str):
        super().__init__(detail)
        self.code = code


class BrowserHttpClient:
    """One independent curl Session for one content import task."""

    def __init__(
        self,
        session: requests.Session,
        *,
        platform: str,
        source_url: str,
        job_id: str,
        page_timeout: float,
        resource_timeout: float,
        proxy: str = "",
        credential_broker: CredentialBroker | None = None,
        snapshot: CredentialSnapshot | None = None,
        resolve_dns: bool = False,
    ):
        self._validate_transport_mode(resolve_dns, proxy)
        self._session = session
        self.platform = platform
        self.source_url = source_url
        self.job_id = job_id
        self._page_timeout = page_timeout
        self._resource_timeout = resource_timeout
        self._proxy = proxy
        self._credential_broker = credential_broker
        self._snapshot = snapshot
        self._resolve_dns = resolve_dns
        self._request_lock = threading.RLock()
        self._closed = False

    @staticmethod
    def _validate_transport_mode(resolve_dns: bool, proxy: str) -> None:
        if resolve_dns and proxy:
            raise BrowserHttpError(
                FailureCode.NETWORK,
                "安全 DNS 固定模式不支持代理",
            )

    @classmethod
    def open(
        cls,
        *,
        url: str,
        platform: str,
        job_id: str,
        credential_broker: CredentialBroker | None = None,
        domains: list[str] | tuple[str, ...] = (),
        authenticated: bool = False,
        page_timeout: float = 30.0,
        resource_timeout: float = 60.0,
        proxy: str = "",
        require_cookies: bool = False,
        resolve_dns: bool = False,
    ) -> "BrowserHttpClient":
        """Create an anonymous or cached-credential task Session."""
        cls.validate_url(url, (), "页面地址不受信任")
        cls._validate_transport_mode(resolve_dns, proxy)
        if resolve_dns:
            cls.validate_public_dns(url, "页面域名解析到非公网地址")
        snapshot = None
        if authenticated:
            if credential_broker is None or not domains:
                raise BrowserHttpError(FailureCode.AUTH_REQUIRED, "平台会话不可用")
            try:
                snapshot = credential_broker.acquire(url, platform, job_id, domains)
            except Exception as exc:
                raise BrowserHttpError(FailureCode.AUTH_REQUIRED, "浏览器会话读取失败") from exc
            if require_cookies and not snapshot.cookies:
                raise BrowserHttpError(FailureCode.AUTH_REQUIRED, "浏览器会话没有可用 Cookie")

        session = requests.Session(
            impersonate=CHROME_IMPERSONATE,
            default_headers=True,
            retry=0,
            trust_env=False,
        )
        try:
            if snapshot is not None:
                cls._import_cookies(session, snapshot)
        except Exception as exc:
            session.close()
            raise BrowserHttpError(FailureCode.AUTH_REQUIRED, "Chrome Cookie 导入失败") from exc
        return cls(
            session,
            platform=platform,
            source_url=url,
            job_id=job_id,
            page_timeout=page_timeout,
            resource_timeout=resource_timeout,
            proxy=proxy,
            credential_broker=credential_broker,
            snapshot=snapshot,
            resolve_dns=resolve_dns,
        )

    @staticmethod
    def _import_cookies(session: requests.Session, snapshot: CredentialSnapshot) -> None:
        for cookie in snapshot.cookies:
            normalized = normalize_chrome_cookie(cookie)
            if normalized is None:
                continue
            session.cookies.set(
                normalized["name"], normalized["value"],
                domain=str(normalized.get("domain") or ""),
                path=str(normalized.get("path") or "/"),
                secure=bool(normalized.get("secure")),
            )

    @staticmethod
    def validate_url(
        url: str,
        domains: list[str] | tuple[str, ...],
        detail: str,
    ) -> str:
        """Accept only public HTTPS URLs, optionally constrained by domain."""
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").casefold().rstrip(".")
            if (
                parts.scheme.casefold() != "https"
                or not host
                or parts.username
                or parts.password
                or parts.port not in (None, 443)
            ):
                raise ValueError
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None:
                raise ValueError
            if (
                host in _LOCAL_HOSTNAMES
                or host.endswith(".localhost")
                or host.endswith(".local")
                or host.endswith(".internal")
            ):
                raise ValueError
            if domains and not any(
                host == domain.lstrip(".").casefold()
                or host.endswith("." + domain.lstrip(".").casefold())
                for domain in domains
            ):
                raise ValueError
            return host
        except (TypeError, ValueError):
            raise BrowserHttpError(FailureCode.NETWORK, detail) from None

    @classmethod
    def _resolve_public_dns(
        cls,
        url: str,
        detail: str,
    ) -> tuple[str, tuple[str, ...]]:
        """Resolve once and return only an entirely public answer set."""
        host = cls.validate_url(url, (), detail)
        try:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addresses = tuple(dict.fromkeys(
                ipaddress.ip_address(answer[4][0]) for answer in answers
            ))
        except (OSError, ValueError) as exc:
            raise BrowserHttpError(FailureCode.NETWORK, "域名解析失败") from exc
        if not addresses or any(not address.is_global for address in addresses):
            raise BrowserHttpError(FailureCode.NETWORK, detail)
        return host, tuple(str(address) for address in addresses)

    @classmethod
    def validate_public_dns(cls, url: str, detail: str) -> None:
        """Reject DNS answers containing any non-global address."""
        cls._resolve_public_dns(url, detail)

    def _request_resolution(
        self,
        url: str,
        domains: list[str] | tuple[str, ...],
        detail: str,
    ) -> tuple[str, tuple[str, ...]] | None:
        """Validate one request hop and resolve the exact addresses to pin."""
        self.validate_url(url, domains, detail)
        if not self._resolve_dns:
            return None
        return self._resolve_public_dns(url, detail)

    @staticmethod
    def _resolve_rule(host: str, addresses: tuple[str, ...]) -> str:
        rendered = ",".join(
            f"[{address}]" if ":" in address else address
            for address in addresses
        )
        return f"{host}:443:{rendered}"

    @contextmanager
    def _pinned_resolution(
        self,
        resolution: tuple[str, tuple[str, ...]] | None,
    ) -> Iterator[None]:
        """Apply a prevalidated DNS mapping only for the current transfer."""
        options = getattr(self._session, "curl_options", None)
        changed: dict[CurlOpt, object] = {}
        missing = object()
        if resolution is not None:
            if not isinstance(options, dict):
                raise BrowserHttpError(
                    FailureCode.NETWORK,
                    "HTTP 传输不支持安全 DNS 固定",
                )
            if getattr(self._session, "trust_env", False):
                raise BrowserHttpError(
                    FailureCode.NETWORK,
                    "安全 DNS 固定模式不支持环境代理",
                )
            host, addresses = resolution
            for option, value in (
                (CurlOpt.RESOLVE, [self._resolve_rule(host, addresses)]),
                (CurlOpt.FRESH_CONNECT, 1),
                (CurlOpt.FORBID_REUSE, 1),
            ):
                changed[option] = options.get(option, missing)
                options[option] = value
        try:
            yield
        finally:
            if resolution is not None:
                assert isinstance(options, dict)
                for option, previous in changed.items():
                    if previous is missing:
                        options.pop(option, None)
                    else:
                        options[option] = previous

    @contextmanager
    def _stream_resource_hop(
        self,
        url: str,
        *,
        headers: dict[str, str] | None,
        resolution: tuple[str, tuple[str, ...]] | None,
    ) -> Iterator[object]:
        """Stream one hop with a transfer-scoped, prevalidated DNS mapping."""
        with self._pinned_resolution(resolution):
            with self._session.stream(
                "GET",
                url,
                headers=headers,
                timeout=self._resource_timeout,
                allow_redirects=False,
                proxy=self._proxy or None,
            ) as response:
                yield response

    @staticmethod
    def _is_login_url(url: str) -> bool:
        try:
            path = urlsplit(url).path.casefold().rstrip("/")
        except (TypeError, ValueError):
            return False
        return any(
            path == prefix or path.startswith(prefix + "/")
            for prefix in ("/login", "/signin", "/sign-in", "/auth/login")
        )

    @staticmethod
    def _close_response(response: object) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    def _invalidate_credentials(self) -> None:
        if self._credential_broker is not None and self._snapshot is not None:
            try:
                self._credential_broker.refresh_if_current(
                    self._snapshot, self.source_url, self.job_id,
                )
            except Exception as exc:
                raise BrowserHttpError(
                    FailureCode.AUTH_REQUIRED,
                    "登录凭据失效且刷新失败，请重新提交",
                ) from exc
            raise BrowserHttpError(
                FailureCode.AUTH_REQUIRED,
                "登录凭据已刷新，请重新提交当前链接",
            )
        raise BrowserHttpError(FailureCode.AUTH_REQUIRED, "页面需要登录凭据")

    def fetch_page(
        self,
        url: str,
        *,
        allowed_domains: list[str] | tuple[str, ...] = (),
    ) -> dict:
        """Issue one application-level page GET; never retry or probe."""
        self.validate_url(url, allowed_domains, "页面地址不受信任")
        current_url = url
        response = None
        try:
            with self._request_lock:
                for hop in range(6):
                    detail = (
                        "页面地址不受信任" if hop == 0
                        else "页面跳转到不受信任的地址"
                    )
                    resolution = self._request_resolution(
                        current_url, allowed_domains, detail,
                    )
                    with self._pinned_resolution(resolution):
                        response = self._session.get(
                            current_url,
                            timeout=self._page_timeout,
                            allow_redirects=False,
                            proxy=self._proxy or None,
                        )
                    if response.status_code not in _REDIRECT_STATUSES:
                        break
                    location = response.headers.get("location")
                    self._close_response(response)
                    if not location:
                        raise BrowserHttpError(
                            FailureCode.NETWORK, "页面跳转缺少 Location",
                        )
                    if hop == 5:
                        raise BrowserHttpError(
                            FailureCode.NETWORK, "页面跳转次数过多",
                        )
                    current_url = urljoin(current_url, location)
        except BrowserHttpError:
            raise
        except Exception as exc:
            raise BrowserHttpError(FailureCode.NETWORK, "页面请求失败") from exc
        assert response is not None
        try:
            if response.status_code == 401:
                self._invalidate_credentials()
            if response.status_code == 429:
                raise BrowserHttpError(FailureCode.RATE_LIMITED, "页面请求受到频率限制")
            if not 200 <= response.status_code < 300:
                raise BrowserHttpError(
                    FailureCode.NETWORK,
                    f"页面 HTTP 状态无效: {response.status_code}",
                )
            final_url = str(response.url or current_url)
            self.validate_url(
                final_url, allowed_domains, "页面跳转到不受信任的地址",
            )
            if self._is_login_url(final_url):
                self._invalidate_credentials()
            html = response.text
            if not html or not html.strip():
                raise BrowserHttpError(FailureCode.NETWORK, "页面响应为空")
            return {
                "html": html,
                "final_url": final_url,
                "content_type": response.headers.get("content-type") or "",
                "status": response.status_code,
            }
        finally:
            self._close_response(response)

    @staticmethod
    def resource_referer(page_url: str, resource_url: str) -> str:
        """Apply strict-origin-when-cross-origin without leaking query tokens."""
        page = urlsplit(page_url)
        resource = urlsplit(resource_url)
        if (page.scheme.casefold(), page.hostname, page.port) == (
            resource.scheme.casefold(), resource.hostname, resource.port,
        ):
            return page._replace(fragment="").geturl()
        return f"{page.scheme}://{page.netloc}/"

    def download_image(
        self,
        url: str,
        destination: Path,
        referer: str,
        max_bytes: int,
        *,
        allowed_domains: list[str] | tuple[str, ...] = (),
    ) -> Path:
        _size, actual = self._download(
            url,
            destination,
            referer,
            minimum=1024,
            maximum=max_bytes,
            kind="image",
            allowed_domains=allowed_domains,
        )
        return actual

    def download_video(
        self,
        url: str,
        destination: Path,
        referer: str,
        *,
        allowed_domains: list[str] | tuple[str, ...] = (),
    ) -> int:
        size, _actual = self._download(
            url,
            destination,
            referer,
            minimum=100_000,
            maximum=None,
            kind="video",
            allowed_domains=allowed_domains,
        )
        return size

    def _download(
        self,
        url: str,
        destination: Path,
        referer: str,
        *,
        minimum: int,
        maximum: int | None,
        kind: str,
        allowed_domains: list[str] | tuple[str, ...],
    ) -> tuple[int, Path]:
        """Stream one resource GET to disk; never retry, HEAD, or probe."""
        self.validate_url(url, allowed_domains, "资源地址不受信任")
        if referer:
            self.validate_url(referer, (), "资源 Referer 不受信任")
        safe_referer = self.resource_referer(referer, url) if referer else ""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        headers = {"Referer": safe_referer} if safe_referer else None
        try:
            current_url = url
            with self._request_lock:
                for hop in range(6):
                    detail = (
                        "资源地址不受信任" if hop == 0
                        else "资源跳转到不受信任的地址"
                    )
                    resolution = self._request_resolution(
                        current_url, allowed_domains, detail,
                    )
                    with self._stream_resource_hop(
                        current_url,
                        headers=headers,
                        resolution=resolution,
                    ) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise BrowserHttpError(
                                    FailureCode.NETWORK,
                                    "资源跳转缺少 Location",
                                )
                            if hop == 5:
                                raise BrowserHttpError(
                                    FailureCode.NETWORK,
                                    "资源跳转次数过多",
                                )
                            current_url = urljoin(current_url, location)
                            continue
                        if response.status_code == 429:
                            raise BrowserHttpError(
                                FailureCode.RATE_LIMITED,
                                "资源请求受到频率限制",
                            )
                        if not 200 <= response.status_code < 300:
                            raise BrowserHttpError(
                                FailureCode.NETWORK,
                                f"资源 HTTP 状态无效: {response.status_code}",
                            )
                        content_type = (
                            response.headers.get("content-type") or ""
                        ).split(";", 1)[0].strip().lower()
                        suffix = Path(current_url.split("?", 1)[0]).suffix.lower()
                        actual_destination = destination
                        if kind == "image":
                            valid_type = content_type.startswith("image/") or (
                                not content_type and suffix in _IMAGE_EXTENSIONS
                            )
                            if content_type:
                                mapped_suffix = _IMAGE_MIME_EXTENSIONS.get(content_type)
                                if mapped_suffix is None:
                                    valid_type = False
                                else:
                                    actual_destination = destination.with_suffix(
                                        mapped_suffix,
                                    )
                        else:
                            valid_type = (
                                content_type.startswith("video/")
                                or content_type == "application/octet-stream"
                            )
                        if not valid_type:
                            raise BrowserHttpError(
                                FailureCode.NETWORK, "资源类型校验失败",
                            )

                        total = 0
                        actual_destination.unlink(missing_ok=True)
                        with actual_destination.open("xb") as output:
                            for chunk in response.iter_content(chunk_size=64 * 1024):
                                if not chunk:
                                    continue
                                total += len(chunk)
                                if maximum is not None and total > maximum:
                                    raise BrowserHttpError(
                                        FailureCode.NETWORK, "资源超过大小限制",
                                    )
                                output.write(chunk)
                        if total < minimum:
                            raise BrowserHttpError(
                                FailureCode.NETWORK, "资源小于大小下限",
                            )
                        return total, actual_destination
                raise AssertionError("unreachable")
        except BrowserHttpError:
            destination.unlink(missing_ok=True)
            if "actual_destination" in locals():
                actual_destination.unlink(missing_ok=True)
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            if "actual_destination" in locals():
                actual_destination.unlink(missing_ok=True)
            raise BrowserHttpError(FailureCode.NETWORK, "资源请求失败") from exc

    def commit_credentials(self) -> None:
        """Best-effort cookie CAS; cache maintenance never fails content."""
        try:
            if self._credential_broker is None or self._snapshot is None:
                return
            jar = getattr(self._session.cookies, "jar", None)
            if jar is None:
                return
            cookies = [
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain or "",
                    "path": cookie.path or "/",
                    "secure": bool(cookie.secure),
                    "expirationDate": cookie.expires or 0,
                }
                for cookie in jar
                if any(
                    (cookie.domain or "").casefold().lstrip(".")
                    == domain.casefold().lstrip(".")
                    or (cookie.domain or "").casefold().lstrip(".").endswith(
                        "." + domain.casefold().lstrip(".")
                    )
                    for domain in self._snapshot.domains
                )
            ]
            self._credential_broker.update_if_current(self._snapshot, cookies)
        except Exception:
            return

    @property
    def credential_domains(self) -> tuple[str, ...]:
        return self._snapshot.domains if self._snapshot is not None else ()

    @property
    def render_cookies(self) -> tuple[dict, ...]:
        """Return a copy of the already scoped snapshot for an isolated renderer."""
        if self._snapshot is None:
            return ()
        return tuple(dict(cookie) for cookie in self._snapshot.cookies)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._session.close()
