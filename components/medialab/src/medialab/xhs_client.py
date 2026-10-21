"""Xiaohongshu policy wrapper around the shared browser HTTP client."""

from __future__ import annotations

from pathlib import Path

from .browser_http import BrowserHttpClient, BrowserHttpError
from .session import CredentialBroker, FailureCode


XHS_PAGE_DOMAINS = (".xiaohongshu.com",)
XHS_RESOURCE_DOMAINS = (".xiaohongshu.com", ".xhscdn.com")
XHS_MIN_IMAGE_DIMENSION = 64


def _jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    with path.open("rb") as image:
        if image.read(2) != b"\xff\xd8":
            return None
        while True:
            prefix = image.read(1)
            if not prefix:
                return None
            if prefix != b"\xff":
                continue
            marker_bytes = image.read(1)
            while marker_bytes == b"\xff":
                marker_bytes = image.read(1)
            if not marker_bytes:
                return None
            marker = marker_bytes[0]
            if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            length_bytes = image.read(2)
            if len(length_bytes) != 2:
                return None
            segment_length = int.from_bytes(length_bytes, "big")
            if segment_length < 2:
                return None
            if marker in sof_markers:
                header = image.read(5)
                if len(header) != 5:
                    return None
                height = int.from_bytes(header[1:3], "big")
                width = int.from_bytes(header[3:5], "big")
                return (width, height) if width and height else None
            image.seek(segment_length - 2, 1)


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    offset = 12
    while offset + 8 <= len(data):
        chunk_type = data[offset:offset + 4]
        chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
        start = offset + 8
        end = start + chunk_size
        if end > len(data):
            return None
        payload = data[start:end]
        if chunk_type == b"VP8X" and len(payload) >= 10:
            width = int.from_bytes(payload[4:7], "little") + 1
            height = int.from_bytes(payload[7:10], "little") + 1
            return width, height
        if chunk_type == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            dimensions = int.from_bytes(payload[1:5], "little")
            return (dimensions & 0x3FFF) + 1, ((dimensions >> 14) & 0x3FFF) + 1
        if (
            chunk_type == b"VP8 "
            and len(payload) >= 10
            and payload[3:6] == b"\x9d\x01\x2a"
        ):
            width = int.from_bytes(payload[6:8], "little") & 0x3FFF
            height = int.from_bytes(payload[8:10], "little") & 0x3FFF
            return (width, height) if width and height else None
        offset = end + (chunk_size & 1)
    return None


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    """Read dimensions from image structure, without trusting MIME or suffix."""
    with path.open("rb") as image:
        header = image.read(32)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            if len(header) < 24 or header[12:16] != b"IHDR":
                return None
            width = int.from_bytes(header[16:20], "big")
            height = int.from_bytes(header[20:24], "big")
            return (width, height) if width and height else None
        if header[:6] in {b"GIF87a", b"GIF89a"} and len(header) >= 10:
            width = int.from_bytes(header[6:8], "little")
            height = int.from_bytes(header[8:10], "little")
            return (width, height) if width and height else None
        if header.startswith(b"\xff\xd8"):
            return _jpeg_dimensions(path)
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            image.seek(0)
            return _webp_dimensions(image.read())
    return None


class XhsClient(BrowserHttpClient):
    """One credentialed Chrome-146 network session for one XHS task."""

    @classmethod
    def from_broker(
        cls,
        broker: CredentialBroker,
        url: str,
        job_id: str,
        *,
        domains: list[str] | tuple[str, ...],
        page_timeout: float = 30.0,
        resource_timeout: float = 60.0,
        proxy: str = "",
        resolve_dns: bool = False,
    ) -> "XhsClient":
        return cls.open(
            url=url,
            platform="xiaohongshu",
            job_id=job_id,
            credential_broker=broker,
            domains=domains,
            authenticated=True,
            page_timeout=page_timeout,
            resource_timeout=resource_timeout,
            proxy=proxy,
            require_cookies=True,
            resolve_dns=resolve_dns,
        )

    def fetch_page(self, url: str) -> dict:
        return super().fetch_page(url, allowed_domains=XHS_PAGE_DOMAINS)

    def download_image(
        self,
        url: str,
        destination: Path,
        referer: str,
        max_bytes: int,
    ) -> Path:
        actual = super().download_image(
            url,
            destination,
            referer,
            max_bytes,
            allowed_domains=self.credential_domains or XHS_RESOURCE_DOMAINS,
        )
        try:
            dimensions = _image_dimensions(actual)
            if dimensions is None:
                raise BrowserHttpError(FailureCode.NETWORK, "图片格式校验失败")
            if min(dimensions) < XHS_MIN_IMAGE_DIMENSION:
                raise BrowserHttpError(FailureCode.NETWORK, "图片尺寸小于下限")
            return actual
        except BrowserHttpError:
            actual.unlink(missing_ok=True)
            raise
        except Exception as exc:
            actual.unlink(missing_ok=True)
            raise BrowserHttpError(FailureCode.NETWORK, "图片格式校验失败") from exc

    def download_video(self, url: str, destination: Path, referer: str) -> int:
        return super().download_video(
            url,
            destination,
            referer,
            allowed_domains=self.credential_domains or XHS_RESOURCE_DOMAINS,
        )
