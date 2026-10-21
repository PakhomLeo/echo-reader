import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from medialab.browser_http import BrowserHttpError

from medialab.local_asr import transcript_quality_metrics
from medialab.service import DownloadService, _probe_local_runtime


class TestFinalizeDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.delivery = Path(self.tmp.name) / "delivery"
        self.delivery.mkdir()
        self.svc = DownloadService.__new__(DownloadService)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rename_to_title_and_paths_valid(self):
        staging = self.delivery / ".staging-t1"
        staging.mkdir()
        (staging / "video.mp4").write_bytes(b"v" * 100)
        task_dir, files = self.svc._finalize_dir(staging, "我的 标题: 测试?", "t1")
        self.assertEqual(task_dir, self.delivery / "我的 标题_ 测试_")
        self.assertFalse(staging.exists())
        self.assertTrue(task_dir.exists())
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].exists())
        self.assertEqual(files[0].name, "video.mp4")

    def test_same_title_different_id_gets_suffix(self):
        """决策 2：同名目录已存在但 manifest.id 不同 → 加后缀，不合并。"""
        (self.delivery / "同名").mkdir()
        (self.delivery / "同名" / "manifest.json").write_text(
            '{"schema_version":1,"id":"vid-a-1"}', encoding="utf-8")
        staging = self.delivery / ".staging-t2"
        staging.mkdir()
        (staging / "a.m4a").write_bytes(b"a" * 10)
        (staging / "manifest.json").write_text(
            '{"schema_version":1,"id":"vid-a-2"}', encoding="utf-8")
        task_dir, files = self.svc._finalize_dir(staging, "同名", "t2", manifest_id="vid-a-2")
        self.assertEqual(task_dir, self.delivery / "同名-2")
        self.assertEqual({f.name for f in files}, {"a.m4a", "manifest.json"})
        self.assertFalse(staging.exists())

    def test_same_id_merges_into_existing(self):
        """幂等重跑：manifest.id 相同 → 用完整新快照替换，不留下错指针副本。"""
        (self.delivery / "同名").mkdir()
        (self.delivery / "同名" / "manifest.json").write_text(
            '{"schema_version":1,"id":"vid-a-1"}', encoding="utf-8")
        (self.delivery / "同名" / "a.m4a").write_bytes(b"old")
        staging = self.delivery / ".staging-t3"
        staging.mkdir()
        (staging / "a.m4a").write_bytes(b"new")
        (staging / "manifest.json").write_text(
            '{"schema_version":1,"id":"vid-a-1"}', encoding="utf-8")
        task_dir, files = self.svc._finalize_dir(staging, "同名", "t3", manifest_id="vid-a-1")
        self.assertEqual(task_dir, self.delivery / "同名")
        self.assertEqual({f.name for f in files}, {"a.m4a", "manifest.json"})
        self.assertEqual((task_dir / "a.m4a").read_bytes(), b"new")
        self.assertFalse((self.delivery / ".previous-t3").exists())

    def test_existing_dir_without_manifest_gets_suffix(self):
        """旧产物目录（无 manifest）撞名 → 加后缀，旧目录不动。"""
        (self.delivery / "同名").mkdir()
        (self.delivery / "同名" / "old.m4a").write_bytes(b"old")
        staging = self.delivery / ".staging-t4"
        staging.mkdir()
        (staging / "b.m4a").write_bytes(b"b" * 10)
        (staging / "manifest.json").write_text(
            '{"schema_version":1,"id":"vid-a-9"}', encoding="utf-8")
        task_dir, _ = self.svc._finalize_dir(staging, "同名", "t4", manifest_id="vid-a-9")
        self.assertEqual(task_dir, self.delivery / "同名-2")
        self.assertTrue((self.delivery / "同名" / "old.m4a").exists())


class TestImportPlanning(unittest.TestCase):
    def setUp(self):
        _probe_local_runtime.cache_clear()
        if importlib.util.find_spec("openvino") is None:
            # The optional local-asr extra is not installed (e.g. Linux CI). Tests
            # patch ``openvino.Core`` anyway, so provide a minimal stand-in module.
            stub = types.ModuleType("openvino")
            stub.Core = object
            patcher = mock.patch.dict(sys.modules, {"openvino": stub})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = DownloadService(config={
            "local_asr": {"model_dir": str(Path(self.tmp.name) / "models")},
        })

    def tearDown(self):
        _probe_local_runtime.cache_clear()
        self.tmp.cleanup()

    def test_douyin_video_auto_routes_to_preferred_online_backend(self):
        capabilities = {
            "transcription": {
                "online": {"available": True, "blocker": None},
                "local": {"available": True, "blocker": "download_required"},
            }
        }
        with mock.patch.object(self.svc, "capabilities", return_value=capabilities):
            plan = self.svc.plan_import(
                "https://www.douyin.com/video/123", mode="auto", asr_backend="auto"
            )
        self.assertEqual((plan["mode"], plan["asr_backend"], plan["blocker"]),
                         ("transcribe", "online", None))

    def test_ambiguous_auto_keeps_full_content_probe_mode(self):
        plan = self.svc.plan_import("https://example.com/an-article")
        self.assertEqual(plan["mode"], "auto")
        self.assertIsNone(plan["asr_backend"])

    def test_backend_blocker_is_reported(self):
        capabilities = {
            "transcription": {
                "online": {"available": False, "blocker": "unavailable"},
                "local": {"available": False, "blocker": "download_required"},
            }
        }
        with mock.patch.object(self.svc, "capabilities", return_value=capabilities):
            plan = self.svc.plan_import("https://youtu.be/abc", asr_backend="auto")
        self.assertEqual(plan["blocker"], "download_required")

    def test_catastrophic_quality_case_is_rejected(self):
        metrics = transcript_quality_metrics(
            [{"start": 0, "end": 2.87, "text": "啊"}], 32.6, "vad"
        )
        self.assertFalse(metrics["accepted"])
        self.assertEqual(metrics["nonblank_characters"], 1)
        self.assertAlmostEqual(metrics["timeline_coverage"], 0.088, places=3)

    def test_capabilities_require_initialized_audio_for_both_backends(self):
        self.svc._cfg["asr"]["api_key"] = "key"
        self.svc._oss = object()
        self.svc._audio = None
        with mock.patch("medialab.service.importlib.util.find_spec", return_value=object()), \
             mock.patch("openvino.Core") as core:
            core.return_value.available_devices = ["GPU"]
            caps = self.svc.capabilities()["transcription"]
        self.assertFalse(caps["online"]["available"])
        self.assertFalse(caps["local"]["available"])
        self.assertEqual(caps["online"]["blocker"], "ffmpeg_unavailable")
        self.assertEqual(caps["local"]["blocker"], "ffmpeg_unavailable")

    def test_capabilities_require_openvino_gpu_without_compiling_model(self):
        self.svc._audio = object()
        with mock.patch("medialab.service.importlib.util.find_spec", return_value=object()), \
             mock.patch("openvino.Core") as core:
            core.return_value.available_devices = ["CPU"]
            caps = self.svc.capabilities()["transcription"]["local"]
        self.assertFalse(caps["available"])
        self.assertEqual(caps["blocker"], "gpu_unavailable")

    def test_capabilities_accept_gpu_index_and_report_model_download(self):
        self.svc._audio = object()
        with mock.patch("medialab.service.importlib.util.find_spec", return_value=object()), \
             mock.patch("openvino.Core") as core:
            core.return_value.available_devices = ["GPU.0", "CPU"]
            caps = self.svc.capabilities()["transcription"]["local"]
        self.assertTrue(caps["available"])
        self.assertEqual(caps["blocker"], "download_required")

    def test_capabilities_cache_runtime_probe_but_refresh_model_presence(self):
        self.svc._audio = object()
        with mock.patch(
            "medialab.service.importlib.util.find_spec", return_value=object()
        ), mock.patch("openvino.Core") as core:
            core.return_value.available_devices = ["GPU"]
            with mock.patch(
                "medialab.service.local_model_readiness",
                side_effect=[(False, "missing"), (True, None), (True, None)],
            ):
                first = self.svc.capabilities()["transcription"]["local"]
                second = self.svc.capabilities()["transcription"]["local"]
                third = self.svc.capabilities()["transcription"]["local"]
        self.assertEqual(core.call_count, 1)
        self.assertEqual(first["blocker"], "download_required")
        self.assertIsNone(second["blocker"])
        self.assertIsNone(third["blocker"])

    def test_browser_http_failure_keeps_structured_code(self):
        from medialab.session import FailureCode

        store = mock.Mock()
        DownloadService._mark_failed(
            store, "job", BrowserHttpError(FailureCode.AUTH_REQUIRED, "凭据未授权"),
        )
        store.set_status.assert_called_once_with(
            "job", "failed", error_code="auth_required", error_detail="凭据未授权",
            error_source="content", error_retryable=False,
        )

    def test_explicit_secrets_path_is_forwarded_to_config_loader(self):
        configured = {"paths": {"db": "db", "delivery": "out", "temp": "tmp"}}
        with mock.patch("medialab.service.load_config", return_value=configured) as loader:
            DownloadService(
                config={"download": {"proxy": ""}},
                config_path=Path("cfg/config.toml"),
                secrets_path=Path("cfg/secrets.toml"),
            )
        loader.assert_called_once_with(
            config_path=Path("cfg/config.toml"),
            secrets_path=Path("cfg/secrets.toml"),
            overrides={"download": {"proxy": ""}},
        )

    def test_youtu_be_session_uses_canonical_youtube_identity(self):
        svc = DownloadService.__new__(DownloadService)
        svc._session_provider = mock.Mock()
        svc._session_provider.get_session.return_value = None
        with tempfile.TemporaryDirectory() as td:
            svc._acquire_session(
                "https://youtu.be/abc123?si=share&poc_token=secret",
                "youtube", "job", Path(td),
            )
        self.assertEqual(
            svc._session_provider.get_session.call_args.kwargs["url"],
            "https://www.youtube.com/watch?v=abc123",
        )


class TestXhsSingleCapture(unittest.TestCase):
    @staticmethod
    def _service(root, bridge, capture=None):
        from medialab.config import load_config

        svc = DownloadService.__new__(DownloadService)
        svc._cfg = load_config(overrides={"paths": {"delivery": root, "temp": root, "db": str(Path(root) / "db")}})
        svc._bridge = bridge
        svc._session_provider = None
        svc._emitter = mock.Mock()
        svc._store = mock.Mock()
        client = mock.Mock()
        client.fetch_page.return_value = capture
        svc._test_xhs_client = client
        svc._open_xhs_client = mock.Mock(return_value=client)
        return svc

    def test_image_note_fetches_once_without_session_or_render(self):
        from medialab.article import ArticleResult
        from medialab.site_adapters import Article

        html = ('<script>window.__INITIAL_STATE__ = {"note":{"noteDetailMap":{"abc":{"note":'
                '{"title":"图文","desc":"正文","imageList":[]}}}}};</script>')
        bridge = mock.Mock()
        capture = {
            "ok": True, "status": 200, "final_url": "https://www.xiaohongshu.com/explore/abc?x=t",
            "content_type": "text/html", "html": html,
        }
        with tempfile.TemporaryDirectory() as td:
            svc = self._service(td, bridge, capture)
            captured = ArticleResult(Article("图文", "https://x", "小红书", content_text="正文"),
                                     Path(td), Path(td) / "article.md", [])
            (Path(td) / ".staging-job").mkdir(exist_ok=True)
            with mock.patch("medialab.service.ArticleExtractor.scrape_captured", return_value=captured) as scrape:
                svc._run_scrape("job", "https://www.xiaohongshu.com/explore/abc", "xiaohongshu", td)
        svc._test_xhs_client.fetch_page.assert_called_once_with(
            "https://www.xiaohongshu.com/explore/abc",
        )
        bridge.request_session.assert_not_called()
        scrape.assert_called_once()

    def test_video_note_fetches_once_and_downloads_once(self):
        import json
        from medialab.publishing import MediaEntry

        note = {"note": {"noteDetailMap": {"abc": {"note": {
            "noteId": "abc", "title": "视频", "type": "video",
            "video": {"media": {"stream": {"EF7": [{"masterUrl": "https://a.xhscdn.com/v.mp4"}]}}},
        }}}}}
        html = f"<script>window.__INITIAL_STATE__ = {json.dumps(note)};</script>"
        bridge = mock.Mock()
        capture = {"ok": True, "html": html, "status": 200,
                   "final_url": "https://www.xiaohongshu.com/explore/abc?x=t"}
        with tempfile.TemporaryDirectory() as td:
            svc = self._service(td, bridge, capture)

            def download_once(_src, out, **_kwargs):
                out.write_bytes(b"v")
                return 100_001

            def publish_once(staging, *_args, **_kwargs):
                media_dir = staging / "media"
                media_dir.mkdir(parents=True, exist_ok=True)
                (media_dir / "original.mp4").write_bytes(b"original")
                (media_dir / "playback.mp4").write_bytes(b"playback")
                return MediaEntry(
                    "video", "media/original.mp4", "media/playback.mp4", "video/mp4", "视频",
                )

            with mock.patch("medialab.service.xhs_video.download", side_effect=download_once) as download, \
                    mock.patch("medialab.service.publish_media", side_effect=publish_once):
                svc._run_scrape("job", "https://www.xiaohongshu.com/explore/abc", "xiaohongshu", td)
        self.assertEqual(svc._test_xhs_client.fetch_page.call_count, 1)
        bridge.request_session.assert_not_called()
        download.assert_called_once()
        svc._test_xhs_client.commit_credentials.assert_called_once()
        svc._test_xhs_client.close.assert_called_once()

    def test_video_client_auth_failure_preserves_code_without_retry(self):
        from medialab.article import ScrapeError
        from medialab.session import FailureCode

        client = mock.Mock()
        client.download_video.side_effect = BrowserHttpError(
            FailureCode.AUTH_REQUIRED, "sensitive detail",
        )
        note = {
            "title": "视频", "type": "video",
            "video": {"media": {"stream": {"EF7": [{"masterUrl": "https://x/v.mp4"}]}}},
        }
        with tempfile.TemporaryDirectory() as td:
            svc = self._service(td, mock.Mock())
            with self.assertRaises(ScrapeError) as ctx:
                svc._download_xhs_video(
                    "https://www.xiaohongshu.com/explore/a", "job", Path(td), note,
                    {"final_url": "https://www.xiaohongshu.com/explore/a?token=secret"},
                    client,
                )
        self.assertEqual(ctx.exception.code, FailureCode.AUTH_REQUIRED)
        self.assertNotIn("secret", str(ctx.exception))
        self.assertEqual(client.download_video.call_count, 1)

    def test_capture_parse_failure_does_not_refetch_render_or_download(self):
        bridge = mock.Mock()
        capture = {
            "ok": True, "status": 200, "html": "<html>missing note</html>",
        }
        with tempfile.TemporaryDirectory() as td:
            svc = self._service(td, bridge, capture)
            with mock.patch("medialab.service.xhs_video.download") as download, \
                    mock.patch("medialab.service.ArticleExtractor.scrape") as scrape:
                svc._run_scrape("job", "https://www.xiaohongshu.com/explore/bad", "xiaohongshu", td)
        self.assertEqual(svc._test_xhs_client.fetch_page.call_count, 1)
        bridge.request_session.assert_not_called()
        download.assert_not_called()
        scrape.assert_not_called()
        svc._store.set_status.assert_any_call(
            "job", "failed", error_code="content_not_found", error_detail=mock.ANY,
            error_source="content", error_retryable=False,
        )

    def test_non_2xx_capture_fails_once_without_fallback(self):
        from medialab.session import FailureCode

        bridge = mock.Mock()
        with tempfile.TemporaryDirectory() as td:
            svc = self._service(td, bridge)
            svc._test_xhs_client.fetch_page.side_effect = BrowserHttpError(
                FailureCode.AUTH_REQUIRED, "小红书登录凭据已刷新，请重新提交当前链接",
            )
            with mock.patch("medialab.service.xhs_video.download") as download, \
                    mock.patch("medialab.service.ArticleExtractor.scrape") as scrape:
                svc._run_scrape("job", "https://www.xiaohongshu.com/explore/denied", "xiaohongshu", td)
        self.assertEqual(svc._test_xhs_client.fetch_page.call_count, 1)
        bridge.request_session.assert_not_called()
        download.assert_not_called()
        scrape.assert_not_called()
        self.assertEqual(svc._store.set_status.call_args_list[-1].kwargs["error_code"], "auth_required")

    def test_bridge_session_error_is_auth_required(self):
        from medialab.article import ScrapeError
        from medialab.session import FailureCode

        bridge = mock.Mock()
        with tempfile.TemporaryDirectory() as td:
            svc = self._service(td, bridge)
            svc._open_xhs_client.side_effect = ScrapeError(FailureCode.AUTH_REQUIRED, "插件未连接")
            svc._run_scrape("job", "https://www.xiaohongshu.com/explore/abc", "xiaohongshu", td)
        failed = svc._store.set_status.call_args_list[-1]
        self.assertEqual(failed.kwargs["error_code"], "auth_required")
        self.assertEqual(failed.kwargs["error_source"], "content")

    def test_plugin_rejection_is_auth_required_without_retry(self):
        from medialab.article import ScrapeError
        from medialab.session import FailureCode

        bridge = mock.Mock()
        with tempfile.TemporaryDirectory() as td:
            svc = self._service(td, bridge)
            client = mock.Mock()
            client.fetch_page.side_effect = BrowserHttpError(FailureCode.AUTH_REQUIRED, "permission_required")
            with self.assertRaises(ScrapeError) as ctx:
                svc._fetch_xhs("https://www.xiaohongshu.com/explore/abc", client)
        self.assertEqual(ctx.exception.code, FailureCode.AUTH_REQUIRED)
        self.assertEqual(client.fetch_page.call_count, 1)

    def test_successful_commit_failure_does_not_mark_task_failed(self):
        from medialab.article import ArticleResult
        from medialab.site_adapters import Article

        html = ('<script>window.__INITIAL_STATE__ = {"note":{"noteDetailMap":{"abc":{"note":'
                '{"title":"图文","desc":"正文","imageList":[]}}}}};</script>')
        bridge = mock.Mock()
        capture = {"html": html, "final_url": "https://www.xiaohongshu.com/n"}
        with tempfile.TemporaryDirectory() as td:
            svc = self._service(td, bridge, capture)
            svc._test_xhs_client.commit_credentials.side_effect = RuntimeError("cache failed")
            captured = ArticleResult(Article("图文", "https://x", "小红书", content_text="正文"),
                                     Path(td), Path(td) / "article.md", [])
            with mock.patch("medialab.service.ArticleExtractor.scrape_captured", return_value=captured):
                svc._run_scrape("job", "https://www.xiaohongshu.com/explore/abc", "xiaohongshu", td)
        statuses = [call.args[1] for call in svc._store.set_status.call_args_list]
        self.assertIn("done", statuses)
        self.assertNotIn("failed", statuses)
        svc._test_xhs_client.close.assert_called_once()


class TestCredentialBrokerInjection(unittest.TestCase):
    def test_explicit_broker_is_used_verbatim(self):
        broker = mock.Mock()
        svc = DownloadService(credential_broker=broker)
        self.assertIs(svc._credentials, broker)
        self.assertIs(svc._session_provider.credential_broker, broker)

    def test_generic_adapter_on_known_platform_uses_same_platform_cache_key(self):
        broker = mock.Mock()
        bridge = mock.Mock()
        svc = DownloadService(bridge=bridge, credential_broker=broker)
        client = mock.Mock()
        with mock.patch(
            "medialab.service.BrowserHttpClient.open", return_value=client,
        ) as opener:
            self.assertIs(
                svc._open_article_client(
                    "https://www.bilibili.com/read/cv1", "job", "generic_article",
                ),
                client,
            )
        kwargs = opener.call_args.kwargs
        self.assertEqual(kwargs["platform"], "bilibili")
        self.assertTrue(kwargs["authenticated"])
        self.assertFalse(kwargs["require_cookies"])
        self.assertIn(".bilibili.com", kwargs["domains"])

    def test_unknown_generic_article_stays_anonymous(self):
        broker = mock.Mock()
        bridge = mock.Mock()
        svc = DownloadService(bridge=bridge, credential_broker=broker)
        client = mock.Mock()
        with mock.patch(
            "medialab.service.BrowserHttpClient.open", return_value=client,
        ) as opener:
            self.assertIs(
                svc._open_article_client(
                    "https://unknown.example/story", "job", "generic",
                ),
                client,
            )
        kwargs = opener.call_args.kwargs
        self.assertEqual(kwargs["platform"], "generic")
        self.assertFalse(kwargs["authenticated"])

    def test_download_cookie_commit_is_best_effort(self):
        provider = mock.Mock()
        provider.commit_session.side_effect = RuntimeError("cache failed")
        svc = DownloadService.__new__(DownloadService)
        svc._session_provider = provider
        session = object()
        svc._commit_download_credentials(session)
        provider.commit_session.assert_called_once_with(session)

    def test_refresh_replaces_task_cookie_file_and_removes_provider_temp(self):
        from medialab.session import BrowserSession

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_source = root / "first.cookies"
            fresh_source = root / "fresh.cookies"
            first_source.write_text("first", encoding="utf-8")
            fresh_source.write_text("fresh", encoding="utf-8")
            provider = mock.Mock()
            provider.get_session.return_value = BrowserSession(cookie_file=first_source)
            provider.refresh_session.return_value = BrowserSession(cookie_file=fresh_source)
            svc = DownloadService.__new__(DownloadService)
            svc._session_provider = provider
            task_tmp = root / "task"

            stale = svc._acquire_session("https://example.com", "generic", "job", task_tmp)
            current = svc._acquire_session(
                "https://example.com",
                "generic",
                "job",
                task_tmp,
                stale_session=stale,
            )

            self.assertEqual(current.cookie_file.read_text(encoding="utf-8"), "fresh")
            self.assertFalse(first_source.exists())
            self.assertFalse(fresh_source.exists())
            provider.refresh_session.assert_called_once()


class TestClose(unittest.TestCase):
    """close() 是优雅关闭：等待运行中的任务线程完成后才关 Store（P2 回归）。"""

    @staticmethod
    def _make_svc(db_dir):
        from unittest import mock

        from medialab.config import load_config

        svc = DownloadService.__new__(DownloadService)
        svc._cfg = load_config(overrides={"paths": {"db": str(Path(db_dir) / "t.db"),
                                                    "delivery": db_dir, "temp": db_dir}})
        svc._emitter = mock.Mock()
        svc._db_path = Path(db_dir) / "t.db"
        svc._session_provider = None
        svc._bridge = None
        svc._tasks = {}
        return svc

    def test_close_waits_for_running_thread(self):
        import asyncio
        import tempfile
        import time
        from unittest import mock

        from medialab import downloader

        async def main():
            with tempfile.TemporaryDirectory() as d:
                svc = self._make_svc(d)
                await svc.initialize()
                state = {"finished": False, "store_closed": False}
                original_close = svc._store.close

                def slow_run(*a, **kw):
                    time.sleep(0.3)
                    state["finished"] = True

                def closed_store():
                    state["store_closed"] = True
                    original_close()

                svc._store.close = closed_store
                with mock.patch.object(downloader, "download_video", side_effect=slow_run):
                    handle = await svc.download("https://www.bilibili.com/video/BV1xx", wait=False)
                    await asyncio.wait_for(svc.close(), timeout=5)
                self.assertTrue(state["finished"], "close 应等待任务线程完成")
                self.assertTrue(state["store_closed"])

        asyncio.run(main())

    def test_close_removes_tasks_after_done(self):
        import asyncio
        import tempfile
        from unittest import mock

        from medialab import downloader

        async def main():
            with tempfile.TemporaryDirectory() as d:
                svc = self._make_svc(d)
                await svc.initialize()
                with mock.patch.object(downloader, "download_video",
                                       side_effect=lambda *a, **kw: (type("R", (), {"files": [], "info": {}}))()):
                    handle = await svc.download("https://www.bilibili.com/video/BV1xx", wait=False)
                    await asyncio.wait_for(handle.wait(svc), timeout=5)
                    await asyncio.sleep(0.05)  # 等 runner 的 finally 执行
                    self.assertNotIn(handle.id, svc._tasks)
                await svc.close()

        asyncio.run(main())


class TestLocalTranscribe(unittest.TestCase):
    def test_online_download_entry_keeps_exact_common_call(self):
        svc = DownloadService.__new__(DownloadService)
        svc._oss = object()
        expected = {"task_dir": Path("item")}
        with mock.patch.object(
            svc, "_transcribe_download_common", return_value=expected
        ) as common:
            result = svc._transcribe_download(
                "task", "https://example.test", "generic", None, "m4a", Path("out")
            )
        self.assertIs(result, expected)
        common.assert_called_once_with(
            "task", "https://example.test", "generic", None, "m4a", Path("out"),
            asr_backend="online", defer_publish=True,
        )

    def test_online_progress_reports_waiting_before_transcribing(self):
        import asyncio

        async def main():
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                events = []
                svc = DownloadService(
                    config={"paths": {"db": str(root / "tasks.db"),
                                       "delivery": str(root / "delivery"),
                                       "temp": str(root / "tmp")}},
                    on_event=lambda name, payload: events.append((name, payload)),
                )
                await svc.initialize()
                task_dir = root / "delivery" / "item"
                task_dir.mkdir(parents=True)
                ctx = {"task_dir": task_dir, "session": None}
                with mock.patch.object(svc, "_transcribe_download", return_value=ctx), \
                 mock.patch.object(svc, "_transcribe_asr", return_value={
                     "segments": [{"text": "hello", "start": 0, "end": 1}],
                     "audio_duration": 1,
                 }), \
                 mock.patch.object(svc, "_transcribe_refine"), \
                 mock.patch.object(svc, "_publish_local_transcription", return_value=ctx), \
                     mock.patch.object(svc, "_cleanup_oss"):
                    await svc.transcribe("https://example.test/video", wait=True)
                stages = [payload["stage"] for name, payload in events
                          if name == "task_progress"]
                self.assertEqual(stages, [
                    "waiting_for_asr", "transcribing", "quality_check", "publishing",
                ])
                await svc.close()

        asyncio.run(main())

    def obsolete_local_thread_executor_contract(self):
        import asyncio
        import threading
        from types import SimpleNamespace

        async def main():
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                svc = DownloadService(
                    config={"paths": {
                        "db": str(root / "tasks.db"),
                        "delivery": str(root / "delivery"),
                        "temp": str(root / "tmp"),
                    }}
                )
                await svc.initialize()
                self.assertIsNone(svc._local_asr_executor)
                self.assertIsNone(svc._oss)
                thread_names = []
                calls = 0

                def fake_download(task_id, url, platform, fmt, audio_format, out_dir, **kwargs):
                    staging = root / "delivery" / f".staging-{task_id}"
                    staging.mkdir(parents=True, exist_ok=True)
                    audio_src = staging / "source.m4a"
                    audio_src.write_bytes(b"audio")
                    return {
                        "task_dir": staging,
                        "audio_src": audio_src,
                        "tmp": root / "tmp" / task_id,
                        "session": None,
                        "staging": staging,
                        "manifest_id": f"local-{task_id}",
                        "source_info": {"title": task_id},
                        "media": None,
                    }

                def fake_transcribe(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    thread_names.append(threading.current_thread().name)
                    if calls == 1:
                        raise RuntimeError("model failed")
                    transcript_path = Path(args[3]) / "transcript.json"
                    transcript_path.write_text("{}", encoding="utf-8")
                    segment = mock.Mock()
                    segment.to_dict.return_value = {"start": 0, "end": 1, "text": "ok"}
                    return SimpleNamespace(segments=[segment], transcript_path=transcript_path)

                with mock.patch.object(
                    svc, "_transcribe_download_common", side_effect=fake_download
                ), mock.patch(
                    "medialab.local_asr.LocalAsrPipeline"
                ) as pipeline_type, mock.patch.object(
                    svc, "_transcribe_asr"
                ) as online_asr, mock.patch.object(
                    svc, "_transcribe_refine"
                ) as refine, mock.patch.object(
                    svc, "_cleanup_oss"
                ) as cleanup_oss:
                    pipeline_type.return_value.transcribe.side_effect = fake_transcribe
                    failed = await svc.transcribe_local(
                        "https://example.test/first", wait=True
                    )
                    self.assertEqual(list((root / "delivery").iterdir()), [])
                    succeeded = await svc.transcribe_local(
                        "https://example.test/second",
                        wait=True,
                        local_timestamp_mode="vad",
                        local_diarization=True,
                        local_speaker_count=2,
                    )

                    failed_task = await svc.query(failed.id)
                    succeeded_task = await svc.query(succeeded.id)
                    self.assertEqual(failed_task["status"], "failed")
                    self.assertEqual(succeeded_task["status"], "done")
                    self.assertEqual(succeeded_task["params"]["asr_backend"], "local")
                    self.assertEqual(succeeded_task["params"]["local_timestamp_mode"], "vad")
                    self.assertEqual(succeeded_task["params"]["local_speaker_count"], 2)
                    self.assertEqual((await svc.asr_result(succeeded.id))["segments"][0]["text"], "ok")
                    self.assertTrue(all(name.startswith("medialab-local-asr") for name in thread_names))
                    online_asr.assert_not_called()
                    refine.assert_not_called()
                    cleanup_oss.assert_not_called()
                    executor = svc._local_asr_executor
                    await svc.close()
                    self.assertIsNone(svc._local_asr_executor)
                    self.assertTrue(executor._shutdown)

        asyncio.run(main())

    def obsolete_cancelled_thread_wrapper_contract(self):
        import asyncio
        import threading

        async def main():
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                svc = DownloadService(config={"paths": {
                    "db": str(root / "tasks.db"),
                    "delivery": str(root / "delivery"),
                    "temp": str(root / "tmp"),
                }})
                await svc.initialize()
                started = threading.Event()
                release = threading.Event()

                def fake_download(task_id, *args, **kwargs):
                    staging = root / "delivery" / f".staging-{task_id}"
                    staging.mkdir(parents=True)
                    audio = staging / "source.m4a"
                    audio.write_bytes(b"audio")
                    task_tmp = root / "tmp" / task_id
                    task_tmp.mkdir(parents=True)
                    return {
                        "task_dir": staging,
                        "staging": staging,
                        "audio_src": audio,
                        "tmp": task_tmp,
                        "session": None,
                        "manifest_id": f"local-{task_id}",
                        "source_info": {"title": task_id},
                        "media": None,
                    }

                def blocking_asr(task_id, ctx, *args):
                    workdir = ctx["tmp"] / "local-asr"
                    workdir.mkdir(parents=True)
                    started.set()
                    release.wait(5)
                    transcript = ctx["staging"] / "transcript.json"
                    transcript.write_text("{}", encoding="utf-8")
                    return {
                        "segments": [{"start": 0, "end": 1, "text": "ok"}],
                        "transcript_path": transcript,
                        "normalized_path": str(transcript),
                    }

                with mock.patch.object(
                    svc, "_transcribe_download_common", side_effect=fake_download
                ), mock.patch.object(
                    svc, "_transcribe_local_asr", side_effect=blocking_asr
                ):
                    handle = await svc.transcribe_local("https://example.test/cancel")
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    wrapper = svc._tasks[handle.id]
                    wrapper.cancel()
                    await asyncio.sleep(0.05)
                    self.assertFalse(wrapper.done())
                    self.assertTrue((root / "tmp" / handle.id / "local-asr").is_dir())
                    self.assertTrue((root / "delivery" / f".staging-{handle.id}").is_dir())
                    release.set()
                    await asyncio.wait_for(wrapper, timeout=3)
                    task = await svc.query(handle.id)
                    self.assertEqual(task["status"], "done")
                    self.assertFalse((root / "tmp" / handle.id).exists())
                    self.assertFalse((root / "delivery" / f".staging-{handle.id}").exists())
                await svc.close()

        asyncio.run(main())

    def test_quality_gate_retries_alternate_once_and_publishes_only_success(self):
        import asyncio
        import concurrent.futures

        class InlineExecutor:
            def __init__(self):
                self.calls = []
                self._shutdown = False
                self.reject_all = False
            def submit(self, fn, *args):
                del fn
                future = concurrent.futures.Future()
                mode = args[-3]
                self.calls.append(mode)
                if self.reject_all and mode == "model":
                    future.set_result({
                        "segments": [
                            {"start": 0, "end": 11, "text": "一二三四"},
                            {"start": 15, "end": 26.061025, "text": "五六七八"},
                        ],
                        "audio_duration": 32.625,
                    })
                elif mode == "vad":
                    future.set_result({
                        "segments": [{"start": 0, "end": 2.9, "text": "x"}],
                        "audio_duration": 32.6,
                    })
                else:
                    future.set_result({
                        "segments": [{"start": 0, "end": 30, "text": "a useful transcript with enough text"}],
                        "audio_duration": 32.6,
                    })
                return future
            def shutdown(self, wait=True):
                del wait
                self._shutdown = True

        async def main():
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                events = []
                svc = DownloadService(
                    config={"paths": {"db": str(root / "tasks.db"),
                                       "delivery": str(root / "delivery"),
                                       "temp": str(root / "tmp")}},
                    on_event=lambda name, payload: events.append((name, payload)),
                )
                await svc.initialize()
                executor = InlineExecutor()

                def fake_download(task_id, *args, **kwargs):
                    staging = root / "delivery" / f".staging-{task_id}"
                    (staging / "media").mkdir(parents=True)
                    audio = staging / "media" / "original.m4a"
                    audio.write_bytes(b"audio")
                    task_tmp = root / "tmp" / task_id
                    task_tmp.mkdir(parents=True)
                    return {"task_dir": staging, "staging": staging, "audio_src": audio,
                            "tmp": task_tmp, "session": None, "manifest_id": f"local-{task_id}",
                            "source_info": {"title": task_id}, "media": None}

                with mock.patch.object(svc, "_transcribe_download_common", side_effect=fake_download), \
                     mock.patch.object(svc, "_create_local_asr_executor", return_value=executor):
                    handle = await svc.transcribe_local(
                        "https://example.test/video", wait=True, local_diarization=False,
                    )
                self.assertEqual((await svc.query(handle.id))["status"], "done")
                self.assertEqual(executor.calls, ["vad", "model"])
                quality = [p["quality"] for n, p in events
                           if n == "task_progress" and p["stage"] == "quality_check"]
                self.assertEqual([item["accepted"] for item in quality], [False, True])
                stages = [p["stage"] for n, p in events if n == "task_progress"]
                self.assertLess(stages.index("waiting_for_asr"), stages.index("transcribing"))
                self.assertLess(stages.index("transcribing"), stages.index("quality_check"))
                self.assertLess(stages.index("quality_check", stages.index("quality_check") + 1),
                                stages.index("publishing"))
                self.assertEqual((await svc.asr_result(handle.id))["segments"][0]["text"],
                                 "a useful transcript with enough text")
                published_dirs = list((root / "delivery").iterdir())
                executor.reject_all = True
                with mock.patch.object(
                    svc, "_transcribe_download_common", side_effect=fake_download
                ):
                    failed = await svc.transcribe_local(
                        "https://example.test/still-low-quality", wait=True,
                        local_diarization=False,
                    )
                failed_task = await svc.query(failed.id)
                self.assertEqual(failed_task["status"], "failed")
                self.assertIn("nonblank_characters", failed_task["error_detail"])
                self.assertIsNone(await svc.asr_result(failed.id))
                self.assertEqual(list((root / "delivery").iterdir()), published_dirs)
                await svc.close()
                self.assertTrue(executor._shutdown)

        asyncio.run(main())


class TestServiceLifecycle(unittest.TestCase):
    @staticmethod
    def _make_service(root):
        return DownloadService(config={"paths": {
            "db": str(root / "tasks.db"),
            "delivery": str(root / "delivery"),
            "temp": str(root / "tmp"),
        }})

    def test_close_latches_rejects_all_submissions_and_is_idempotent(self):
        import asyncio
        import threading

        async def main():
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                svc = DownloadService(config={"paths": {
                    "db": str(root / "tasks.db"),
                    "delivery": str(root / "delivery"),
                    "temp": str(root / "tmp"),
                }})
                await svc.initialize()
                started = threading.Event()
                release = threading.Event()

                def slow_download(*args, **kwargs):
                    started.set()
                    release.wait(5)

                with mock.patch.object(svc, "_run_download", side_effect=slow_download):
                    handle = await svc.download("https://example.test/running")
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    closing = asyncio.create_task(svc.close())
                    while not svc._closing:
                        await asyncio.sleep(0)
                    for submitter in (
                        lambda: svc.download("https://example.test/new"),
                        lambda: svc.transcribe("https://example.test/new"),
                        lambda: svc.transcribe_local("https://example.test/new"),
                        lambda: svc.scrape("https://example.test/new"),
                        lambda: svc.submit("https://example.test/new"),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "正在关闭"):
                            await submitter()
                    self.assertIn(handle.id, svc._tasks)
                    release.set()
                    await asyncio.wait_for(closing, 3)
                    await svc.close()
                    self.assertNotIn(handle.id, svc._tasks)

        asyncio.run(main())

    def test_cancelled_close_caller_still_waits_for_shared_close(self):
        import asyncio
        import threading

        async def main():
            with tempfile.TemporaryDirectory() as d:
                svc = self._make_service(Path(d))
                await svc.initialize()
                started = threading.Event()
                release = threading.Event()
                original_close = svc._store.close

                def blocking_close():
                    started.set()
                    release.wait(5)
                    original_close()

                svc._store.close = blocking_close
                caller = asyncio.create_task(svc.close())
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                caller.cancel()
                await asyncio.sleep(0.05)
                self.assertFalse(caller.done())
                self.assertFalse(svc._close_task.done())
                release.set()
                await asyncio.wait_for(caller, 3)
                self.assertTrue(svc._close_task.done())

        asyncio.run(main())

    def test_initialize_is_idempotent_and_rejected_after_close(self):
        import asyncio

        async def main():
            with tempfile.TemporaryDirectory() as d:
                svc = self._make_service(Path(d))
                first = await svc.initialize()
                store = svc._store
                second = await svc.initialize()
                self.assertEqual(first, second)
                self.assertIs(svc._store, store)
                await svc.close()
                with self.assertRaisesRegex(RuntimeError, "正在关闭"):
                    await svc.initialize()

        asyncio.run(main())

    def test_initialize_and_close_are_linearized_without_reopen(self):
        import asyncio

        async def close_wins(root):
            svc = self._make_service(root / "close-wins")
            await svc._lifecycle_lock.acquire()
            closing = asyncio.create_task(svc.close())
            await asyncio.sleep(0)
            initializing = asyncio.create_task(svc.initialize())
            svc._lifecycle_lock.release()
            await closing
            with self.assertRaisesRegex(RuntimeError, "正在关闭"):
                await initializing
            self.assertIsNone(svc._store)

        async def initialize_wins(root):
            svc = self._make_service(root / "initialize-wins")
            await svc._lifecycle_lock.acquire()
            initializing = asyncio.create_task(svc.initialize())
            await asyncio.sleep(0)
            closing = asyncio.create_task(svc.close())
            svc._lifecycle_lock.release()
            await initializing
            store = svc._store
            await closing
            self.assertIs(svc._store, store)
            with self.assertRaisesRegex(RuntimeError, "正在关闭"):
                await svc.initialize()

        async def main():
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                await close_wins(root)
                await initialize_wins(root)

        asyncio.run(main())

if __name__ == "__main__":
    unittest.main()
