import json
from pathlib import Path
from unittest import mock

import pytest

from medialab.config import load_config
from medialab.content_probe import MediaCandidate, ProbeResult
from medialab.downloader import DownloadResult, MediaProbe
from medialab.js_render import RenderedPage
from medialab.service import DownloadService
from medialab.site_adapters import Article


PAGE_URL = "https://example.com/story"


def _probe(
    *,
    url: str = PAGE_URL,
    title: str = "Captured story",
    description: str = "A useful page summary",
    media: list[MediaCandidate] | None = None,
    images: list[dict] | None = None,
) -> ProbeResult:
    article = Article(
        title=title,
        url=url,
        author="Reporter",
        content_text="The complete article body selected from the captured page.",
    )
    return ProbeResult(
        input_url=url,
        final_url=url,
        canonical_url=url,
        title=title,
        description=description,
        author="Reporter",
        published_at="2026-08-10T12:00:00+08:00",
        article=article,
        media=list(media or []),
        images=list(images or []),
    )


def _service(root: Path, capture: dict | None = None) -> tuple[DownloadService, mock.Mock]:
    delivery = root / "delivery"
    temp = root / "temp"
    svc = DownloadService.__new__(DownloadService)
    svc._cfg = load_config(overrides={
        "paths": {
            "delivery": str(delivery),
            "temp": str(temp),
            "db": str(root / "medialab.db"),
        },
    })
    svc._emitter = mock.Mock()
    svc._store = mock.Mock()
    svc._session_provider = None
    client = mock.Mock()
    client.credential_domains = ()
    client.render_cookies = ()
    client.fetch_page.return_value = capture or {
        "html": "<html></html>",
        "final_url": PAGE_URL,
        "status": 200,
    }
    svc._open_article_client = mock.Mock(return_value=client)
    svc._open_xhs_client = mock.Mock(return_value=client)
    return svc, client


def _manifest_dir(root: Path) -> tuple[Path, dict]:
    manifests = list((root / "delivery").glob("*/manifest.json"))
    assert len(manifests) == 1
    return manifests[0].parent, json.loads(manifests[0].read_text(encoding="utf-8"))


def _download_factory(*, fail_urls: set[str] | None = None, payload: bytes = b"media"):
    fail_urls = fail_urls or set()

    def download(_cfg, url, _audio, out_dir, _tmp, **_kwargs):
        if url in fail_urls:
            raise RuntimeError("synthetic media failure")
        path = out_dir / "download.mp3"
        path.write_bytes(payload + url.encode("utf-8"))
        return DownloadResult(
            files=[path],
            info={"title": f"Media for {url.rsplit('/', 1)[-1]}"},
        )

    return download


def _assert_safe_media_paths(item_dir: Path, manifest: dict) -> None:
    for item in manifest["media_items"]:
        for key in ("original_path", "playback_path"):
            value = item[key]
            assert not Path(value).is_absolute()
            assert ".." not in Path(value).parts
            assert (item_dir / value).is_file()


def test_static_article_and_two_media_publish_one_mixed_item(tmp_path: Path):
    media_urls = ["https://cdn.example.com/one", "https://cdn.example.com/two"]
    probe = _probe(media=[
        MediaCandidate("video", media_urls[0]),
        MediaCandidate("audio", media_urls[1]),
    ])
    svc, client = _service(tmp_path)

    def resolve(_cfg, url, **_kwargs):
        if url == PAGE_URL:
            return ([
                MediaProbe(media_urls[0], "video", media_id="one", extractor="Fake"),
                MediaProbe(media_urls[1], "audio", media_id="two", extractor="Fake"),
            ], None)
        return ([], None)

    with mock.patch("medialab.service.probe_html", return_value=probe), \
            mock.patch("medialab.service.downloader.probe_media", side_effect=resolve), \
            mock.patch(
                "medialab.service.downloader.download_video",
                side_effect=_download_factory(),
            ) as download:
        svc._run_auto_capture("job", PAGE_URL, "generic", None)

    item_dir, manifest = _manifest_dir(tmp_path)
    assert manifest["source"]["type"] == "mixed"
    assert manifest["source"]["description"] == "A useful page summary"
    assert manifest["primary_media"] is not None
    assert len(manifest["media_items"]) == 2
    assert (item_dir / "article.md").is_file()
    _assert_safe_media_paths(item_dir, manifest)
    assert download.call_count == 2
    client.fetch_page.assert_called_once_with(PAGE_URL, allowed_domains=())


def test_duplicate_resolved_media_downloads_once(tmp_path: Path):
    media_url = "https://cdn.example.com/repeated"
    probe = _probe(media=[
        MediaCandidate("video", media_url),
        MediaCandidate("embed", media_url),
    ])
    duplicate = MediaProbe(media_url, "video", media_id="same", extractor="Fake")
    svc, _client = _service(tmp_path)

    with mock.patch("medialab.service.probe_html", return_value=probe), \
            mock.patch(
                "medialab.service.downloader.probe_media",
                return_value=([duplicate, duplicate], None),
            ), \
            mock.patch(
                "medialab.service.downloader.download_video",
                side_effect=_download_factory(),
            ) as download:
        svc._run_auto_capture("job", PAGE_URL, "generic", None)

    _item_dir, manifest = _manifest_dir(tmp_path)
    assert len(manifest["media_items"]) == 1
    download.assert_called_once()


def test_discovered_candidate_count_is_bounded_before_resolver_calls(tmp_path: Path):
    svc, _client = _service(tmp_path)
    svc._cfg["content_capture"]["max_media_items"] = 2
    probe = _probe(media=[
        MediaCandidate("video", "https://cdn.example.com/one"),
        MediaCandidate("audio", "https://cdn.example.com/two"),
        MediaCandidate("embed", "https://player.example.com/three"),
    ])

    with mock.patch("medialab.service.downloader.probe_media") as resolver, \
            pytest.raises(RuntimeError, match="超过自动抓取上限"):
        svc._resolve_auto_media(
            "job", PAGE_URL, "generic", probe, tmp_path / "resolver-temp",
        )

    resolver.assert_not_called()


def test_one_media_failure_keeps_success_and_records_warning(tmp_path: Path):
    good = "https://cdn.example.com/good"
    bad = "https://cdn.example.com/bad"
    svc, _client = _service(tmp_path)
    resolved = [
        MediaProbe(good, "audio", media_id="good", extractor="Fake"),
        MediaProbe(bad, "video", media_id="bad", extractor="Fake"),
    ]

    with mock.patch("medialab.service.probe_html", return_value=_probe()), \
            mock.patch(
                "medialab.service.downloader.probe_media",
                side_effect=[(resolved, None)],
            ), \
            mock.patch(
                "medialab.service.downloader.download_video",
                side_effect=_download_factory(fail_urls={bad}),
            ):
        svc._run_auto_capture("job", PAGE_URL, "generic", None)

    item_dir, manifest = _manifest_dir(tmp_path)
    assert len(manifest["media_items"]) == 1
    assert manifest["media_items"][0]["original_path"].endswith(".mp3")
    assert any(value.startswith("media_download_failed:") for value in manifest["warnings"])
    assert (item_dir / "article.md").is_file()
    _assert_safe_media_paths(item_dir, manifest)


def test_xhs_video_article_and_images_stay_in_one_item_and_fetch_once(tmp_path: Path):
    url = "https://www.xiaohongshu.com/explore/note-1"
    image_url = "https://sns-img.example.com/picture.jpg"
    note = {
        "noteId": "note-1",
        "title": "XHS mixed note",
        "type": "video",
        "video": {"media": {"stream": {"EF7": [{"masterUrl": "https://sns-video.example.com/v.mp4"}]}}},
    }
    probe = _probe(
        url=url,
        title="XHS mixed note",
        images=[{"src": image_url, "alt": "cover"}],
    )
    svc, client = _service(tmp_path, {"html": "captured xhs", "final_url": url})

    def download_video(_url, destination, _referer):
        destination.write_bytes(b"video")
        return 100_001

    def download_image(_url, destination, _referer, _max_bytes):
        destination.write_bytes(b"image")
        return destination

    client.download_video.side_effect = download_video
    client.download_image.side_effect = download_image
    with mock.patch("medialab.service.probe_html", return_value=probe), \
            mock.patch("medialab.service.xhs_video.note_from_html", return_value=note), \
            mock.patch(
                "medialab.publishing._ffprobe",
                return_value={"video_codec": "h264", "audio_codec": "aac"},
            ):
        svc._run_auto_capture("job", url, "xiaohongshu", None)

    item_dir, manifest = _manifest_dir(tmp_path)
    assert manifest["source"]["type"] == "mixed"
    assert len(manifest["media_items"]) == 1
    assert (item_dir / "article.md").is_file()
    assert (item_dir / "images" / "0000.jpg").is_file()
    _assert_safe_media_paths(item_dir, manifest)
    client.fetch_page.assert_called_once_with(url)


def test_default_wechat_policy_invokes_renderer(tmp_path: Path):
    url = "https://mp.weixin.qq.com/s/example"
    static = _probe(url=url, title="Static WeChat")
    rendered = _probe(url=url, title="Rendered WeChat")
    svc, client = _service(tmp_path, {"html": "static", "final_url": url})
    page = RenderedPage(
        html="rendered",
        final_url=url,
        warnings=[],
        request_count=1,
        bytes_seen=8,
    )

    with mock.patch("medialab.service.probe_html", side_effect=[static, rendered]), \
            mock.patch("medialab.js_render.render_page", return_value=page) as render, \
            mock.patch(
                "medialab.service.downloader.probe_media",
                return_value=([], None),
            ):
        svc._run_auto_capture("job", url, "wechat", None)

    _item_dir, manifest = _manifest_dir(tmp_path)
    assert manifest["source"]["title"] == "Rendered WeChat"
    render.assert_called_once()
    client.fetch_page.assert_called_once_with(url, allowed_domains=())


def test_rerun_replaces_media_snapshot_and_preserves_user_article(tmp_path: Path):
    media_url = "https://cdn.example.com/versioned"
    resolved = [MediaProbe(media_url, "audio", media_id="versioned", extractor="Fake")]
    svc, _client = _service(tmp_path)

    def run(task_id: str, payload: bytes) -> tuple[Path, dict]:
        with mock.patch("medialab.service.probe_html", return_value=_probe()), \
                mock.patch(
                    "medialab.service.downloader.probe_media",
                    return_value=(resolved, None),
                ), \
                mock.patch(
                    "medialab.service.downloader.download_video",
                    side_effect=_download_factory(payload=payload),
                ):
            svc._run_auto_capture(task_id, PAGE_URL, "generic", None)
        return _manifest_dir(tmp_path)

    item_dir, first = run("first", b"old-")
    (item_dir / "article.md").write_text("user edited article", encoding="utf-8")
    old_media = item_dir / first["primary_media"]["original_path"]
    assert old_media.read_bytes().startswith(b"old-")

    item_dir, second = run("second", b"new-")
    new_media = item_dir / second["primary_media"]["original_path"]
    assert new_media.read_bytes().startswith(b"new-")
    assert (item_dir / "article.md").read_text(encoding="utf-8") == "user edited article"
    assert sorted(
        path.relative_to(item_dir).as_posix()
        for path in (item_dir / "media").rglob("*")
        if path.is_file()
    ) == ["media/original.mp3", "media/playback.mp3"]
    assert len(list((tmp_path / "delivery").glob("*/manifest.json"))) == 1


def test_partial_rerun_keeps_previous_complete_media_snapshot(tmp_path: Path):
    first_url = "https://cdn.example.com/first"
    second_url = "https://cdn.example.com/second"
    resolved = [
        MediaProbe(first_url, "audio", media_id="first", extractor="Fake"),
        MediaProbe(second_url, "audio", media_id="second", extractor="Fake"),
    ]
    svc, _client = _service(tmp_path)

    with mock.patch("medialab.service.probe_html", return_value=_probe()), \
            mock.patch("medialab.service.downloader.probe_media", return_value=(resolved, None)), \
            mock.patch(
                "medialab.service.downloader.download_video",
                side_effect=_download_factory(payload=b"complete-"),
            ):
        svc._run_auto_capture("first", PAGE_URL, "generic", None)
    item_dir, before = _manifest_dir(tmp_path)
    before_payloads = {
        item["id"]: (item_dir / item["original_path"]).read_bytes()
        for item in before["media_items"]
    }

    with mock.patch("medialab.service.probe_html", return_value=_probe()), \
            mock.patch("medialab.service.downloader.probe_media", return_value=(resolved, None)), \
            mock.patch(
                "medialab.service.downloader.download_video",
                side_effect=_download_factory(fail_urls={second_url}, payload=b"partial-"),
            ):
        svc._run_auto_capture("second", PAGE_URL, "generic", None)

    same_dir, after = _manifest_dir(tmp_path)
    assert same_dir == item_dir
    assert after == before
    assert {
        item["id"]: (item_dir / item["original_path"]).read_bytes()
        for item in after["media_items"]
    } == before_payloads
    assert any(
        "已保留原有完整条目" in str(call)
        for call in svc._store.set_status.call_args_list
    )


def test_resolver_failure_on_rerun_keeps_previous_complete_snapshot(tmp_path: Path):
    first_url = "https://cdn.example.com/first"
    second_url = "https://cdn.example.com/second"
    probe = _probe(media=[
        MediaCandidate("audio", first_url),
        MediaCandidate("audio", second_url),
    ])
    complete = [
        MediaProbe(first_url, "audio", media_id="first", extractor="Fake"),
        MediaProbe(second_url, "audio", media_id="second", extractor="Fake"),
    ]
    svc, _client = _service(tmp_path)

    with mock.patch("medialab.service.probe_html", return_value=probe), \
            mock.patch("medialab.service.downloader.probe_media", return_value=(complete, None)), \
            mock.patch(
                "medialab.service.downloader.download_video",
                side_effect=_download_factory(payload=b"complete-"),
            ):
        svc._run_auto_capture("first", PAGE_URL, "generic", None)
    item_dir, before = _manifest_dir(tmp_path)

    def partial_resolve(_cfg, candidate_url, **_kwargs):
        if candidate_url == second_url:
            raise RuntimeError("synthetic resolver failure")
        return ([MediaProbe(first_url, "audio", media_id="first", extractor="Fake")], None)

    with mock.patch("medialab.service.probe_html", return_value=probe), \
            mock.patch("medialab.service.downloader.probe_media", side_effect=partial_resolve), \
            mock.patch(
                "medialab.service.downloader.download_video",
                side_effect=_download_factory(payload=b"partial-"),
            ):
        svc._run_auto_capture("second", PAGE_URL, "generic", None)

    same_dir, after = _manifest_dir(tmp_path)
    assert same_dir == item_dir
    assert after == before
    assert any(
        "已保留原有完整条目" in str(call)
        for call in svc._store.set_status.call_args_list
    )
