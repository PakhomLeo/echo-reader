"""Single Chrome-cookie normalization boundary for HTTP, Netscape, and Playwright."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SAME_SITE = {
    "strict": "Strict",
    "lax": "Lax",
    "no_restriction": "None",
    "none": "None",
}


def normalize_chrome_cookie(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    name = raw.get("name")
    value = raw.get("value")
    if not isinstance(name, str) or not name or not isinstance(value, str):
        return None
    item: dict[str, Any] = {"name": name, "value": value}
    for key in ("domain", "path", "url"):
        if isinstance(raw.get(key), str) and raw[key]:
            item[key] = raw[key]
    for key in ("httpOnly", "secure"):
        if key in raw:
            item[key] = bool(raw[key])
    expires = raw.get("expires", raw.get("expirationDate"))
    if expires is not None:
        try:
            item["expires"] = float(expires)
        except (TypeError, ValueError):
            pass
    same_site = raw.get("sameSite")
    if isinstance(same_site, str) and same_site.casefold() in _SAME_SITE:
        item["sameSite"] = _SAME_SITE[same_site.casefold()]
    return item
