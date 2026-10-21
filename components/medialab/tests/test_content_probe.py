from __future__ import annotations

import json

import medialab.content_probe as content_probe
from medialab.content_probe import probe_html
from medialab.site_adapters import Article, SiteAdapter


def _by_kind(result, kind):
    return [candidate for candidate in result.media if candidate.kind == kind]


def test_article_with_multiple_media_and_relative_urls():
    html = """
    <html><head>
      <title>Mixed page</title><meta name="description" content="Page summary">
      <link rel="canonical" href="/canonical">
    </head><body><article><h1>Mixed page</h1><p>This is enough useful article text for extraction.</p>
      <video poster="/poster.jpg"><source src="media/a.mp4" type="video/mp4"></video>
      <audio src="/sound.mp3"></audio>
      <iframe src="https://player.example/embed/7"></iframe>
    </article></body></html>
    """
    result = probe_html("https://example.com/posts/1", html)

    assert result.canonical_url == "https://example.com/canonical"
    assert result.description == "Page summary"
    assert [m.url for m in _by_kind(result, "video")] == ["https://example.com/posts/media/a.mp4"]
    assert _by_kind(result, "video")[0].poster_url == "https://example.com/poster.jpg"
    assert _by_kind(result, "audio")[0].url == "https://example.com/sound.mp3"
    assert _by_kind(result, "embed")[0].url == "https://player.example/embed/7"


def test_og_and_dom_media_are_deduplicated_and_evidence_is_merged():
    html = """
    <meta property="og:video" content="https://cdn.example/video.mp4">
    <video src="https://cdn.example/video.mp4"></video>
    """
    result = probe_html("https://example.com/page", html)
    videos = _by_kind(result, "video")

    assert len(videos) == 1
    assert videos[0].role == "primary"
    assert videos[0].evidence == ("meta:og:video", "dom:video")


def test_jsonld_graph_extracts_article_video_audio_and_metadata():
    payload = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "NewsArticle",
                "headline": "JSON-LD headline",
                "description": "Structured description",
                "author": {"@type": "Person", "name": "Ada"},
                "datePublished": "2026-08-11",
                "image": "/cover.jpg",
            },
            {
                "@type": "VideoObject",
                "name": "Clip",
                "contentUrl": "/clip.mp4",
                "thumbnailUrl": "/clip.jpg",
                "encodingFormat": "video/mp4",
            },
            {"@type": "AudioObject", "contentUrl": "https://cdn.example/audio.m4a"},
        ],
    }
    html = f'<script type="application/ld+json">{json.dumps(payload)}</script>'
    result = probe_html("https://example.com/story", html)

    assert result.title == "JSON-LD headline"
    assert result.description == "Structured description"
    assert result.author == "Ada"
    assert result.published_at == "2026-08-11"
    assert _by_kind(result, "video")[0].url == "https://example.com/clip.mp4"
    assert _by_kind(result, "audio")[0].url == "https://cdn.example/audio.m4a"
    assert result.images[0]["src"] == "https://example.com/cover.jpg"


def test_oembed_discovery_and_track_are_reported_without_loading_them():
    html = """
    <link rel="alternate" type="application/json+oembed" href="/oembed.json" title="embed data">
    <video><track kind="captions" label="中文" src="/captions.vtt"></video>
    """
    result = probe_html("https://example.com/watch", html)

    assert _by_kind(result, "oembed")[0].url == "https://example.com/oembed.json"
    assert _by_kind(result, "track")[0].role == "captions"
    assert _by_kind(result, "track")[0].title == "中文"


class _FailingAdapter(SiteAdapter):
    platform = "broken"

    def extract(self, html: str, url: str):
        raise RuntimeError("broken parser")


def test_specialized_adapter_failure_uses_generic_fallback(monkeypatch):
    monkeypatch.setattr(content_probe, "find_adapter", lambda _url: _FailingAdapter())
    monkeypatch.setattr(
        content_probe.GenericTrafilaturaAdapter,
        "extract",
        lambda self, html, url: Article(title="Fallback", url=url, content_text="body"),
    )

    result = probe_html("https://example.com/a", "<article>body</article>")

    assert result.article is not None
    assert result.article.title == "Fallback"
    assert result.warnings == ["adapter_failed:broken:RuntimeError", "adapter_fallback:broken:generic_article"]


def test_specialized_adapter_success_does_not_call_generic(monkeypatch):
    adapter = _FailingAdapter()
    adapter.extract = lambda html, url: Article(title="Special", url=url, content_text="body")
    monkeypatch.setattr(content_probe, "find_adapter", lambda _url: adapter)
    monkeypatch.setattr(
        content_probe.GenericTrafilaturaAdapter,
        "extract",
        lambda *args: (_ for _ in ()).throw(AssertionError("generic must not run")),
    )

    result = probe_html("https://example.com/a", "<article>body</article>")
    assert result.article.title == "Special"


def test_unsafe_and_non_https_candidates_are_rejected():
    html = """
    <video src="javascript:alert(1)"></video>
    <audio src="data:audio/mp3,abc"></audio>
    <iframe src="blob:https://example.com/id"></iframe>
    <video src="http://insecure.example/video.mp4"></video>
    <video src="//secure.example/video.mp4"></video>
    <img src="data:image/gif;base64,abc"><img src="/safe.jpg">
    """
    result = probe_html("https://example.com/page", html)

    assert [(m.kind, m.url) for m in result.media] == [
        ("video", "https://secure.example/video.mp4")
    ]
    assert [image["src"] for image in result.images] == ["https://example.com/safe.jpg"]


def test_wechat_lazy_media_is_absolute_and_recommends_rendering():
    html = """
    <h1 id="activity-name">公众号文章</h1><span id="js_name">作者</span>
    <div id="js_content"><p>这里有足够长的静态正文，用于确认专用适配器可以成功提取文章，同时页面仍然声明了懒加载的媒体元素。</p>
      <iframe data-src="/lazy/player"></iframe><img data-src="/lazy/cover.jpg">
    </div>
    """
    result = probe_html("https://mp.weixin.qq.com/s/abc", html, platform="wechat")

    assert result.article is not None
    assert _by_kind(result, "embed")[0].url == "https://mp.weixin.qq.com/lazy/player"
    assert any(image["src"] == "https://mp.weixin.qq.com/lazy/cover.jpg" for image in result.images)
    assert result.render_recommended is True
