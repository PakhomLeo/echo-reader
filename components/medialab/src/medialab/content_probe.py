"""Deterministic, network-free discovery of the content represented by a page.

The probe deliberately separates *discovery* from acquisition.  It consumes an
already captured HTML document, records the evidence exposed by common web
standards and site adapters, and never dereferences a discovered URL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urljoin

from .site_adapters import Article, GenericTrafilaturaAdapter, find_adapter


@dataclass(frozen=True)
class MediaCandidate:
    kind: str
    url: str
    role: str = "inline"
    title: str | None = None
    description: str | None = None
    mime_type: str | None = None
    poster_url: str | None = None
    evidence: tuple[str, ...] = ()


@dataclass
class ProbeResult:
    input_url: str
    final_url: str
    canonical_url: str
    title: str | None
    description: str | None
    author: str | None
    published_at: str | None
    article: Article | None
    media: list[MediaCandidate] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    render_recommended: bool = False


def _absolute_https(value: Any, base_url: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        joined = urljoin(base_url, value.strip())
    except ValueError:
        return None
    return joined if joined.lower().startswith("https://") else None


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    return value or None


def _meta(doc, *names: str) -> str | None:
    wanted = {name.lower() for name in names}
    for node in doc.xpath("//meta[@content]"):
        key = (node.get("property") or node.get("name") or "").strip().lower()
        if key in wanted:
            value = _clean(node.get("content"))
            if value:
                return value
    return None


def _attr_url(node, base_url: str, *attrs: str) -> str | None:
    for attr in attrs:
        value = _absolute_https(node.get(attr), base_url)
        if value:
            return value
    return None


def _jsonld_nodes(value: Any):
    """Yield every JSON-LD object, including nested @graph entries.

    Remote ``@context`` values remain inert strings and are never resolved.
    """
    if isinstance(value, dict):
        yield value
        for key, child in value.items():
            if key == "@context":
                continue
            yield from _jsonld_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _jsonld_nodes(child)


def _types(node: dict) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).rsplit(":", 1)[-1].lower() for v in values if v}


def _person_name(value: Any) -> str | None:
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        return _clean(value.get("name"))
    if isinstance(value, list):
        names = [_person_name(item) for item in value]
        return ", ".join(name for name in names if name) or None
    return None


def probe_html(
    url: str,
    html: str,
    *,
    final_url: str | None = None,
    platform: str | None = None,
) -> ProbeResult:
    """Build a content graph summary from captured HTML without network I/O."""
    from lxml import html as lxml_html

    page_url = final_url or url
    warnings: list[str] = []
    try:
        doc = lxml_html.fromstring(html or "<html></html>", base_url=page_url)
    except (ValueError, TypeError) as exc:
        doc = lxml_html.fromstring("<html></html>", base_url=page_url)
        warnings.append(f"html_parse_failed:{type(exc).__name__}")

    canonical = page_url
    canonical_nodes = doc.xpath(
        "//link[contains(concat(' ', translate(@rel, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz'), ' '), ' canonical ')]"
    )
    if canonical_nodes:
        canonical = _attr_url(canonical_nodes[0], page_url, "href") or page_url

    title = _meta(doc, "og:title", "twitter:title")
    if not title:
        title_nodes = doc.xpath("//title")
        title = _clean(title_nodes[0].text_content()) if title_nodes else None
    description = _meta(doc, "description", "og:description", "twitter:description")
    author = _meta(doc, "author", "article:author")
    published_at = _meta(
        doc, "article:published_time", "date", "datepublished", "pubdate"
    )

    adapter = find_adapter(page_url)
    article: Article | None = None
    try:
        article = adapter.extract(html, page_url)
    except Exception as exc:  # noqa: BLE001 - adapters are isolation boundaries
        warnings.append(f"adapter_failed:{adapter.platform}:{type(exc).__name__}")
    if article is None and not isinstance(adapter, GenericTrafilaturaAdapter):
        try:
            article = GenericTrafilaturaAdapter().extract(html, page_url)
            if article is not None:
                warnings.append(f"adapter_fallback:{adapter.platform}:generic_article")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"generic_adapter_failed:{type(exc).__name__}")

    if article is not None:
        title = _clean(article.title) or title
        author = _clean(article.author) or author
        published_at = _clean(article.date) or published_at

    candidates: dict[tuple[str, str], MediaCandidate] = {}

    def add_media(
        kind: str,
        raw_url: Any,
        evidence: str,
        *,
        role: str = "inline",
        media_title: Any = None,
        media_description: Any = None,
        mime_type: Any = None,
        poster_url: Any = None,
    ) -> None:
        absolute = _absolute_https(raw_url, page_url)
        if not absolute:
            return
        poster = _absolute_https(poster_url, page_url)
        candidate = MediaCandidate(
            kind=kind,
            url=absolute,
            role=role,
            title=_clean(media_title),
            description=_clean(media_description),
            mime_type=_clean(mime_type),
            poster_url=poster,
            evidence=(evidence,),
        )
        key = (kind, absolute)
        previous = candidates.get(key)
        if previous is None:
            candidates[key] = candidate
            return
        merged_evidence = tuple(dict.fromkeys((*previous.evidence, evidence)))
        candidates[key] = replace(
            previous,
            role="primary" if "primary" in (previous.role, role) else previous.role,
            title=previous.title or candidate.title,
            description=previous.description or candidate.description,
            mime_type=previous.mime_type or candidate.mime_type,
            poster_url=previous.poster_url or candidate.poster_url,
            evidence=merged_evidence,
        )

    images_by_url: dict[str, dict] = {}

    def add_image(raw_url: Any, evidence: str, *, alt: Any = None) -> None:
        absolute = _absolute_https(raw_url, page_url)
        if not absolute:
            return
        current = images_by_url.get(absolute)
        if current is None:
            images_by_url[absolute] = {
                "src": absolute,
                "alt": _clean(alt) or "",
                "evidence": [evidence],
            }
        elif evidence not in current["evidence"]:
            current["evidence"].append(evidence)
            if not current["alt"]:
                current["alt"] = _clean(alt) or ""

    # Open Graph is inspected before DOM so its primary-media role wins merges.
    for node in doc.xpath("//meta[@content]"):
        prop = (node.get("property") or node.get("name") or "").lower()
        value = node.get("content")
        if prop in {"og:video", "og:video:url", "og:video:secure_url"}:
            add_media("video", value, f"meta:{prop}", role="primary",
                      media_title=title, media_description=description)
        elif prop in {"og:audio", "og:audio:url", "og:audio:secure_url"}:
            add_media("audio", value, f"meta:{prop}", role="primary",
                      media_title=title, media_description=description)
        elif prop in {"og:image", "og:image:url", "og:image:secure_url"}:
            add_image(value, f"meta:{prop}")

    jsonld_article: dict | None = None
    for script in doc.xpath("//script[@type='application/ld+json']"):
        raw = script.text or script.text_content() or ""
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            warnings.append("jsonld_parse_failed")
            continue
        for node in _jsonld_nodes(payload):
            node_types = _types(node)
            if node_types & {"article", "newsarticle", "blogposting", "socialmediaposting"}:
                jsonld_article = jsonld_article or node
                for image in node.get("image", []) if isinstance(node.get("image"), list) else [node.get("image")]:
                    if isinstance(image, dict):
                        image = image.get("url") or image.get("contentUrl")
                    add_image(image, "jsonld:article:image")
            media_kind = None
            if "videoobject" in node_types:
                media_kind = "video"
            elif "audioobject" in node_types:
                media_kind = "audio"
            if media_kind:
                media_url = node.get("contentUrl") or node.get("embedUrl") or node.get("url")
                thumbnail = node.get("thumbnailUrl")
                if isinstance(thumbnail, list):
                    thumbnail = thumbnail[0] if thumbnail else None
                add_media(
                    media_kind, media_url, f"jsonld:{media_kind}object", role="primary",
                    media_title=node.get("name") or node.get("headline"),
                    media_description=node.get("description"),
                    mime_type=node.get("encodingFormat"), poster_url=thumbnail,
                )

    if jsonld_article:
        title = _clean(jsonld_article.get("headline") or jsonld_article.get("name")) or title
        description = _clean(jsonld_article.get("description")) or description
        author = _person_name(jsonld_article.get("author")) or author
        published_at = _clean(jsonld_article.get("datePublished")) or published_at

    for node in doc.xpath("//video|//audio"):
        kind = node.tag.lower()
        poster = node.get("poster") if kind == "video" else None
        add_media(kind, node.get("src") or node.get("data-src"), f"dom:{kind}",
                  mime_type=node.get("type"), poster_url=poster)
        for source in node.xpath(".//source"):
            add_media(kind, source.get("src") or source.get("data-src"),
                      f"dom:{kind}:source", mime_type=source.get("type"), poster_url=poster)
        for track in node.xpath(".//track"):
            add_media("track", track.get("src") or track.get("data-src"),
                      f"dom:{kind}:track", role=track.get("kind") or "track",
                      media_title=track.get("label"), mime_type="text/vtt")

    for node in doc.xpath("//iframe|//object|//embed"):
        raw = node.get("src") or node.get("data-src") or node.get("data")
        add_media("embed", raw, f"dom:{node.tag.lower()}", mime_type=node.get("type"))

    for node in doc.xpath("//link[@href]"):
        rel = {part.lower() for part in (node.get("rel") or "").split()}
        media_type = (node.get("type") or "").lower().split(";", 1)[0].strip()
        if "alternate" in rel and media_type in {"application/json+oembed", "text/xml+oembed"}:
            add_media("oembed", node.get("href"), "link:oembed", role="metadata",
                      media_title=node.get("title"), mime_type=media_type)

    for node in doc.xpath("//img"):
        add_image(node.get("src") or node.get("data-src"), "dom:img", alt=node.get("alt"))
    if article is not None:
        for image in article.images:
            add_image(image.get("src"), "adapter:article", alt=image.get("alt"))

    resolved_platform = platform or getattr(adapter, "platform", None)
    has_lazy = bool(doc.xpath("//*[@data-src or @data-original or @data-lazy-src]"))
    article_length = len((article.content_text if article else "").strip())
    render_recommended = resolved_platform == "wechat" and (
        has_lazy or article_length < 80 or (not candidates and not images_by_url)
    )

    return ProbeResult(
        input_url=url,
        final_url=page_url,
        canonical_url=canonical,
        title=title,
        description=description,
        author=author,
        published_at=published_at,
        article=article,
        media=list(candidates.values()),
        images=list(images_by_url.values()),
        warnings=warnings,
        render_recommended=render_recommended,
    )
