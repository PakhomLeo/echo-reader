"""Small async client for Echo's public library API."""

from __future__ import annotations

from typing import Any

import httpx


class EchoApiError(ValueError):
    """Echo returned a response that does not satisfy its documented contract."""


class EchoApiClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:3000",
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        internal_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url:
            raise ValueError("Echo base_url must not be empty")
        self.timeout = timeout
        self._client = client
        self.internal_token = internal_token

    async def switch_library(self, path: str) -> dict[str, Any]:
        """Switch Echo through its authenticated host-only endpoint."""
        if not self.internal_token:
            raise EchoApiError("Echo internal token is not configured")
        url = f"{self.base_url}/internal/v1/library/switch"
        headers = {"X-Echo-Internal-Token": self.internal_token}
        if self._client is not None:
            response = await self._client.post(
                url, json={"path": path}, headers=headers, timeout=self.timeout
            )
        else:
            async with httpx.AsyncClient(trust_env=False, timeout=None) as client:
                response = await client.post(
                    url, json={"path": path}, headers=headers
                )
        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise EchoApiError("Echo library switch returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("library_root") != path:
            raise EchoApiError("Echo library switch response has an invalid root")
        result: dict[str, Any] = {"library_root": path}
        for field in ("item_count", "invalid_item_count"):
            value = payload.get(field)
            if type(value) is not int or value < 0:
                raise EchoApiError(f"Echo library switch field {field!r} must be a non-negative integer")
            result[field] = value
        return result

    async def rescan(self) -> dict[str, int]:
        url = f"{self.base_url}/api/v1/library/rescan"
        if self._client is not None:
            response = await self._client.post(url, timeout=self.timeout)
        else:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(url, timeout=self.timeout)
        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise EchoApiError("Echo rescan returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise EchoApiError("Echo rescan response must be a JSON object")

        result: dict[str, int] = {}
        for field in ("item_count", "invalid_item_count"):
            value = payload.get(field)
            # bool is an int subclass in Python, but is not valid for this API.
            if type(value) is not int or value < 0:
                raise EchoApiError(
                    f"Echo rescan field {field!r} must be a non-negative integer"
                )
            result[field] = value
        return result

    async def health(self) -> None:
        """Verify that Echo's read-only health endpoint is reachable."""
        url = f"{self.base_url}/health"
        if self._client is not None:
            response = await self._client.get(url, timeout=self.timeout)
        else:
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(url, timeout=self.timeout)
        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise EchoApiError("Echo health returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise EchoApiError("Echo health response must report status=ok")
