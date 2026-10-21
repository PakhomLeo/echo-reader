import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import yt_dlp
from yt_dlp import ImpersonateTarget

from medialab import downloader
from medialab.config import load_config
from medialab.downloader import (
    DownloadError,
    MediaProbeLimitError,
    build_opts,
    download_video,
    probe_media,
)
from medialab.session import BrowserSession


BASE_CFG = {
    "download": {
        "concurrent_fragment_downloads": 4,
        "retries": 10,
        "fragment_retries": 10,
        "risk_retries": 0,
        "fallback_format": "",
        "risk_backoff_base": 1,
        "session_first_platforms": ["bilibili"],
    },
    "progress": {"throttle": 1.0},
    "platforms": {"enabled": ["bilibili", "default", "generic"]},
}


class TestYdlConstruction(unittest.TestCase):
    def test_plugin_discovery_is_serialized_across_download_threads(self):
        active = 0
        peak = 0
        guard = threading.Lock()

        def construct(_opts):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return object()

        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", side_effect=construct):
            with ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(downloader._new_ydl, ({}, {}, {})))

        self.assertEqual(len(results), 3)
        self.assertEqual(peak, 1)


class TempDownloadDirs:
    def setUp(self):
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.out_dir = self.root / "out"
        self.tmp_dir = self.root / "tmp"
        self.out_dir.mkdir()
        self.tmp_dir.mkdir()

    def tearDown(self):
        self._temp_dir.cleanup()
        super().tearDown()


class TestBuildOpts(unittest.TestCase):
    def test_default_whitelist_loads_every_declared_video_platform(self):
        opts = {
            "allowed_extractors": downloader.platforms.allowed_extractors(load_config()),
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            loaded = set(ydl._ies)
        for extractor in (
            "BiliBili", "Douyin", "Youtube", "Twitter", "TikTok", "Vimeo",
            "TwitchVod", "Instagram", "Facebook", "Generic",
        ):
            self.assertIn(extractor, loaded)

    def test_uses_supported_chrome146_handler_and_task_cookie_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_file = Path(temp_dir) / "session.cookies"
            session = BrowserSession(cookie_file=cookie_file)
            opts = build_opts(
                BASE_CFG,
                "https://www.bilibili.com/video/BV1xx",
                None,
                "out.%(ext)s",
                None,
                None,
                session=session,
            )
            self.assertEqual(opts["cookiefile"], str(cookie_file))
            self.assertEqual(opts["impersonate"], ImpersonateTarget("chrome", "146"))
            self.assertNotIn("cookiesfrombrowser", opts)
            self.assertNotIn("cookiefile_from_browser", opts)

            with yt_dlp.YoutubeDL({**opts, "skip_download": True}) as ydl:
                handlers = {
                    type(handler).__name__
                    for handler in ydl._request_director.handlers.values()
                }
            self.assertIn("CurlCFFIRH", handlers)

    def test_session_data_and_youtube_bundle_are_injected(self):
        session = BrowserSession(
            cookie_file=Path("c.txt"),
            headers={"X-Test": "yes", "": "skip"},
            proxy="http://session:2",
            visitor_data="visitor",
            po_token="token",
            po_token_scope="player",
        )
        opts = build_opts(
            BASE_CFG,
            "https://www.youtube.com/watch?v=x",
            None,
            "out.%(ext)s",
            None,
            None,
            session=session,
        )
        self.assertEqual(opts["proxy"], "http://session:2")
        self.assertEqual(opts["http_headers"], {"X-Test": "yes"})
        self.assertEqual(
            opts["extractor_args"]["youtube"],
            ["visitor_data=visitor", "po_token=token:player"],
        )

class TestCredentialEscalation(TempDownloadDirs, unittest.TestCase):
    def test_session_first_acquires_before_first_download(self):
        session = BrowserSession(cookie_file=Path("cached.txt"))
        attempts = []
        factory = mock.Mock(return_value=session)

        def fake_run(opts, url):
            attempts.append(opts)
            return {"title": "ok", "requested_downloads": []}, []

        with mock.patch.object(downloader, "_run_ydl", side_effect=fake_run):
            result = download_video(
                BASE_CFG,
                "https://www.bilibili.com/video/BV1xx",
                None,
                self.out_dir,
                self.tmp_dir,
                session_factory=factory,
            )
        factory.assert_called_once_with()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["cookiefile"], "cached.txt")
        self.assertIs(result.session, session)

    def test_session_first_refreshes_once_only_on_auth_failure(self):
        stale = BrowserSession(cookie_file=Path("stale.txt"))
        fresh = BrowserSession(cookie_file=Path("fresh.txt"))
        attempts = []
        refresher = mock.Mock(return_value=fresh)

        def fake_run(opts, url):
            attempts.append(opts)
            if len(attempts) == 1:
                raise DownloadError("ERROR: cookies are no longer valid")
            return {"title": "ok", "requested_downloads": []}, []

        with mock.patch.object(downloader, "_run_ydl", side_effect=fake_run):
            result = download_video(
                BASE_CFG,
                "https://www.bilibili.com/video/BV1xx",
                None,
                self.out_dir,
                self.tmp_dir,
                session=stale,
                session_refresher=refresher,
            )
        refresher.assert_called_once_with(stale)
        self.assertEqual([item["cookiefile"] for item in attempts], ["stale.txt", "fresh.txt"])
        self.assertIs(result.session, fresh)

    def test_fresh_cookies_needed_triggers_the_single_refresh_path(self):
        stale = BrowserSession(cookie_file=Path("stale.txt"))
        fresh = BrowserSession(cookie_file=Path("fresh.txt"))
        attempts = []
        refresher = mock.Mock(return_value=fresh)

        def fake_run(opts, url):
            attempts.append(opts["cookiefile"])
            if len(attempts) == 1:
                raise DownloadError("ERROR: [bilibili] Fresh cookies for this account are needed")
            return {"title": "ok", "requested_downloads": []}, []

        with mock.patch.object(downloader, "_run_ydl", side_effect=fake_run):
            result = download_video(
                BASE_CFG, "https://www.bilibili.com/video/BV1xx", None,
                self.out_dir, self.tmp_dir, session=stale, session_refresher=refresher,
            )
        self.assertEqual(attempts, ["stale.txt", "fresh.txt"])
        refresher.assert_called_once_with(stale)
        self.assertIs(result.session, fresh)

    def test_anonymous_auth_acquires_then_refreshes_stale_cache_once(self):
        cfg = {
            **BASE_CFG,
            "download": {**BASE_CFG["download"], "session_first_platforms": []},
        }
        stale = BrowserSession(cookie_file=Path("stale.txt"))
        fresh = BrowserSession(cookie_file=Path("fresh.txt"))
        attempts = []
        refresher = mock.Mock(return_value=fresh)

        def fake_run(opts, url):
            attempts.append(opts)
            if len(attempts) <= 2:
                raise DownloadError("ERROR: Sign in to confirm you're not a bot")
            return {"title": "ok", "requested_downloads": []}, []

        with mock.patch.object(downloader, "_run_ydl", side_effect=fake_run):
            download_video(
                cfg,
                "https://example.com/video",
                None,
                self.out_dir,
                self.tmp_dir,
                session_factory=mock.Mock(return_value=stale),
                session_refresher=refresher,
            )
        self.assertNotIn("cookiefile", attempts[0])
        self.assertEqual(attempts[1]["cookiefile"], "stale.txt")
        self.assertEqual(attempts[2]["cookiefile"], "fresh.txt")
        refresher.assert_called_once_with(stale)

    def test_no_credentials_does_not_repeat_anonymous_auth_failure(self):
        cfg = {
            **BASE_CFG,
            "download": {**BASE_CFG["download"], "session_first_platforms": []},
        }
        run = mock.Mock(side_effect=DownloadError("ERROR: Sign in required"))
        with mock.patch.object(downloader, "_run_ydl", run):
            with self.assertRaises(DownloadError):
                download_video(
                    cfg,
                    "https://example.com/video",
                    None,
                    self.out_dir,
                    self.tmp_dir,
                )
        self.assertEqual(run.call_count, 1)

    def test_network_or_rate_failure_never_refreshes_credentials(self):
        stale = BrowserSession(cookie_file=Path("stale.txt"))
        refresher = mock.Mock()
        run = mock.Mock(side_effect=DownloadError("HTTP Error 429: Too Many Requests"))
        with mock.patch.object(downloader, "_run_ydl", run):
            with self.assertRaises(DownloadError):
                download_video(
                    BASE_CFG,
                    "https://www.bilibili.com/video/BV1xx",
                    None,
                    self.out_dir,
                    self.tmp_dir,
                    session=stale,
                    session_refresher=refresher,
                )
        refresher.assert_not_called()


class TestRiskFallback(TempDownloadDirs, unittest.TestCase):
    def test_risk_retries_and_format_fallback_are_yt_dlp_policy_not_cookie_refresh(self):
        cfg = {
            **BASE_CFG,
            "download": {
                **BASE_CFG["download"],
                "risk_retries": 1,
                "fallback_format": "b/best",
                "session_first_platforms": [],
            },
        }
        formats = []

        def fake_run(opts, url):
            formats.append(opts["format"])
            raise DownloadError("HTTP Error 503: Service Temporarily Unavailable")

        with mock.patch.object(downloader, "_run_ydl", side_effect=fake_run), self.assertRaises(DownloadError):
            download_video(
                cfg,
                "https://www.bilibili.com/video/BV1xx",
                None,
                self.out_dir,
                self.tmp_dir,
            )
        self.assertEqual(formats, ["bv*+ba/b", "bv*+ba/b", "b/best"])


class TestMediaProbe(unittest.TestCase):
    def test_probe_returns_every_sanitized_playlist_entry_without_download(self):
        info = {
            "_type": "playlist",
            "entries": [
                {"id": "v1", "webpage_url": "https://video.example/1", "title": "One", "vcodec": "h264"},
                {"id": "a2", "url": "https://audio.example/2.m4a", "title": "Two", "vcodec": "none", "acodec": "aac"},
            ],
        }
        with mock.patch.object(downloader, "_run_probe_ydl", return_value=info) as run:
            values, session = probe_media(BASE_CFG, "https://example.com/post", max_entries=4)
        self.assertIsNone(session)
        self.assertEqual([(v.media_id, v.kind, v.url) for v in values], [
            ("v1", "video", "https://video.example/1"),
            ("a2", "audio", "https://audio.example/2.m4a"),
        ])
        self.assertTrue(run.call_args.args[0]["skip_download"])
        self.assertEqual(run.call_args.args[0]["playlistend"], 5)

    def test_probe_fails_explicitly_instead_of_silently_truncating(self):
        info = {"entries": [{"id": str(i), "url": f"https://example.com/{i}"} for i in range(3)]}
        with mock.patch.object(downloader, "_run_probe_ydl", return_value=info):
            with self.assertRaises(MediaProbeLimitError):
                probe_media(BASE_CFG, "https://example.com/post", max_entries=2)


if __name__ == "__main__":
    unittest.main()
