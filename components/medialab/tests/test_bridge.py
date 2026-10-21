import json
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from medialab import platforms
from medialab.bridge import (_path_aware_handler, _valid_pot_path,
                             _validated_extension_origin, SessionBridgeError,
                             SessionBridgeServer)
from medialab.session import BrowserSession, ExtensionSessionProvider, write_netscape_file


def make_server():
    s = SessionBridgeServer()
    s._server = mock.Mock()
    return s


def _fake_client(*, origin="chrome-extension://" + "a" * 32, role="extension", client_id=1):
    h = mock.Mock()
    h.connection_role = role
    h.extension_origin = origin if role == "extension" else None
    return {"handler": h, "id": client_id}


class TestBridgeOriginValidation(unittest.TestCase):
    def test_handler_preserves_duplicate_origin_and_rejects_malformed_bytes(self):
        handler_type = _path_aware_handler()
        handler = handler_type.__new__(handler_type)
        handler.keep_alive = True
        handler.rfile = io.BytesIO(
            b"GET / HTTP/1.1\r\nOrigin: chrome-extension://" + b"a" * 32
            + b"\r\nOrigin: chrome-extension://" + b"b" * 32 + b"\r\n\r\n"
        )
        handler.read_http_headers()
        self.assertEqual(len(handler.request_headers_multi["origin"]), 2)
        self.assertIsNone(_validated_extension_origin(handler.request_headers_multi["origin"]))

        malformed = handler_type.__new__(handler_type)
        malformed.keep_alive = True
        malformed.rfile = io.BytesIO(b"GET / HTTP/1.1\r\nOrigin: \xff\r\n\r\n")
        self.assertEqual(malformed.read_http_headers(), {})
        self.assertFalse(malformed.keep_alive)

    def test_invalid_origins_rejected(self):
        s = make_server()
        invalid = (None, "null", "http://example.com", "https://example.com",
                   "chrome-extension://" + "a" * 31,
                   "chrome-extension://" + "q" * 32,
                   "chrome-extension://" + "a" * 32 + "/",
                   "chrome-extension://user@" + "a" * 32)
        for value in invalid:
            with self.subTest(value=value):
                client = _fake_client(origin=value)
                s._on_new_client(client, s._server)
                self.assertIsNone(s._client)
                client["handler"].send_close.assert_called_once()

    def test_valid_origin_accepted_and_ready_contains_random_token(self):
        s = make_server()
        client = _fake_client()
        s._on_new_client(client, s._server)
        self.assertIs(s._client, client)
        ready = json.loads(s._server.send_message.call_args.args[1])
        self.assertEqual(ready["type"], "bridge_ready")
        self.assertEqual(ready["token"], s._client_token)
        self.assertGreaterEqual(len(ready["token"]), 43)

    def test_origin_helper_requires_exactly_one_value(self):
        valid = "chrome-extension://" + "p" * 32
        self.assertEqual(_validated_extension_origin([valid]), valid)
        self.assertIsNone(_validated_extension_origin([]))
        self.assertIsNone(_validated_extension_origin([valid, valid]))

    def test_client_left_clears_pending(self):
        s = make_server()
        client = _fake_client()
        s._on_new_client(client, s._server)
        from medialab.bridge import _PendingRequest
        p = _PendingRequest(req_id="req1")
        s._pending["req1"] = p
        s._on_client_left(client, s._server)
        self.assertIsNone(s._client)
        # 等待者被唤醒并标记断线错误
        self.assertTrue(p.event.is_set())
        self.assertEqual(p.error, "plugin_disconnected")

    def test_reconnect_rotates_token_and_invalidates_old_connection(self):
        s = make_server()
        old = _fake_client(client_id=1)
        new = _fake_client(origin="chrome-extension://" + "b" * 32, client_id=2)
        s._on_new_client(old, s._server)
        old_token = s._client_token
        s._on_new_client(new, s._server)
        self.assertIs(s._client, new)
        self.assertNotEqual(old_token, s._client_token)
        old["handler"].send_close.assert_called_once()

    def test_old_connection_token_response_is_rejected(self):
        from medialab.bridge import _PendingRequest

        s = make_server()
        old = _fake_client(client_id=1)
        new = _fake_client(origin="chrome-extension://" + "b" * 32, client_id=2)
        s._on_new_client(old, s._server)
        old_token = s._client_token
        s._on_new_client(new, s._server)
        pending = _PendingRequest(req_id="req")
        s._pending["req"] = pending
        s._on_message(new, s._server, json.dumps({
            "ok": True, "req_id": "req", "token": old_token,
        }))
        self.assertIn("req", s._pending)
        self.assertFalse(pending.event.is_set())
        s._on_message(new, s._server, json.dumps({
            "ok": True, "req_id": "req", "token": s._client_token,
        }))
        self.assertTrue(pending.event.is_set())


class TestPotAuthentication(unittest.TestCase):
    def test_pot_path_requires_exact_role_and_token(self):
        token = "x" * 43
        self.assertTrue(_valid_pot_path(f"/?role=pot&token={token}", token))
        for path in (f"/?role=pot&token=wrong", f"/?role=pot&token={token}&token={token}",
                     f"/?role=session&token={token}", f"/other?role=pot&token={token}"):
            self.assertFalse(_valid_pot_path(path, token))

    def test_pot_never_becomes_extension_or_receives_ready(self):
        s = make_server()
        pot = _fake_client(role="pot", client_id=9)
        s._on_new_client(pot, s._server)
        self.assertIsNone(s._client)
        self.assertIn(9, s._pot_clients)
        s._server.send_message.assert_not_called()

    def test_pot_only_receives_its_mint_response(self):
        s = make_server()
        pot = _fake_client(role="pot", client_id=9)
        extension = _fake_client(client_id=1)
        s._on_new_client(pot, s._server)
        s._on_new_client(extension, s._server)
        s._server.send_message.reset_mock()
        s._on_message(pot, s._server, json.dumps({
            "type": "mint_yt_token", "video_id": "abc",
        }))
        target, raw = s._server.send_message.call_args.args
        self.assertIs(target, extension)
        forwarded = json.loads(raw)
        self.assertEqual(forwarded["token"], s._client_token)
        self.assertEqual(forwarded["type"], "mint_yt_token")
        s._server.send_message.reset_mock()
        s._on_message(extension, s._server, json.dumps({
            "ok": True, "po_token": "po", "req_id": forwarded["req_id"],
            "token": s._client_token,
        }))
        target, raw = s._server.send_message.call_args.args
        self.assertIs(target, pot)
        response = json.loads(raw)
        self.assertEqual(response["po_token"], "po")
        self.assertNotIn("token", response)

    def test_start_rotates_and_publishes_private_pot_token(self):
        s = SessionBridgeServer()
        try:
            with mock.patch("websocket_server.websocket_server.WebsocketServer") as server_cls, \
                    mock.patch("medialab.bridge.threading.Thread"):
                server_cls.return_value = mock.Mock()
                os.environ.pop("MEDIALAB_BRIDGE_POT_TOKEN", None)
                s.start()
                first = s._pot_token
                self.assertGreaterEqual(len(first), 43)
                self.assertEqual(os.environ["MEDIALAB_BRIDGE_POT_TOKEN"], first)
                self.assertEqual(os.environ["MEDIALAB_BRIDGE_POT_HOST"], s.host)
                self.assertEqual(os.environ["MEDIALAB_BRIDGE_POT_PORT"], str(s.port))
                s.stop()
                self.assertNotIn("MEDIALAB_BRIDGE_POT_TOKEN", os.environ)
                self.assertNotIn("MEDIALAB_BRIDGE_POT_HOST", os.environ)
                self.assertNotIn("MEDIALAB_BRIDGE_POT_PORT", os.environ)
                s.start()
                second = s._pot_token
                self.assertNotEqual(first, second)
                os.environ["MEDIALAB_BRIDGE_POT_TOKEN"] = "other-server"
                s.stop()
                self.assertEqual(os.environ["MEDIALAB_BRIDGE_POT_TOKEN"], "other-server")
        finally:
            for key in ("MEDIALAB_BRIDGE_POT_TOKEN", "MEDIALAB_BRIDGE_POT_HOST",
                        "MEDIALAB_BRIDGE_POT_PORT"):
                os.environ.pop(key, None)
            for name in list(sys.modules):
                if name == "websocket_server" or name.startswith("websocket_server."):
                    sys.modules.pop(name, None)


class TestBridgeRequest(unittest.TestCase):
    def test_request_session_exact_payload(self):
        s = make_server()
        client = _fake_client()
        s._on_new_client(client, s._server)
        sent = []

        def fake_send(c, msg):
            payload = json.loads(msg)
            sent.append(payload)
            s._on_message(c, s._server, json.dumps({
                "ok": True,
                "cookies": [],
                "req_id": payload["req_id"], "token": payload["token"],
            }))

        s._server.send_message.side_effect = fake_send
        domains = [".xiaohongshu.com", ".xhscdn.com"]
        s.request_session("https://www.xiaohongshu.com/explore/1", "job1", domains)

        self.assertEqual(len(sent), 1)
        payload = sent[0]
        self.assertEqual(payload["type"], "get_session")
        self.assertEqual(payload["url"], "https://www.xiaohongshu.com/explore/1")
        self.assertEqual(payload["job_id"], "job1")
        self.assertEqual(payload["domains"], domains)
        self.assertEqual(payload["token"], s._client_token)
        self.assertIn("req_id", payload)

    def test_request_forward_and_response(self):
        s = make_server()
        client = _fake_client()
        s._on_new_client(client, s._server)

        def fake_send(c, msg):
            # 模拟插件回包（回显 req_id）
            payload = json.loads(msg)
            s._on_message(c, s._server, json.dumps({"ok": True, "cookies": [],
                                                    "req_id": payload["req_id"],
                                                    "token": payload["token"]}))

        s._server.send_message.side_effect = fake_send
        result = s.request_session("https://www.bilibili.com/video/BV1xx", "job1", [".bilibili.com"])
        self.assertEqual(result, {"ok": True, "cookies": []})

    def test_ping_reply_not_misattributed(self):
        """无关回复（如 ping 的 unsupported_message）不得被当成请求响应（req_id 不匹配）。"""
        s = make_server()
        client = _fake_client()
        s._on_new_client(client, s._server)
        sent = []

        def fake_send(c, msg):
            sent.append(json.loads(msg))
            # 先到一条无关回复（无 req_id / 错误 req_id）→ 不应消耗 pending
            s._on_message(c, s._server, json.dumps({"ok": False, "error": "unsupported_message"}))
            # 再回真正的响应
            s._on_message(c, s._server, json.dumps({"ok": True, "cookies": [1],
                                                    "req_id": sent[-1]["req_id"],
                                                    "token": sent[-1]["token"]}))

        s._server.send_message.side_effect = fake_send
        result = s.request_session("https://www.bilibili.com/video/BV1xx", "job1", [".bilibili.com"])
        self.assertEqual(result, {"ok": True, "cookies": [1]})

    def test_no_plugin_connected(self):
        s = make_server()
        with self.assertRaises(SessionBridgeError):
            s.request_session("https://x.com/v", "job1", [])

    def test_timeout(self):
        s = make_server()
        client = _fake_client()
        s._on_new_client(client, s._server)
        with self.assertRaises(SessionBridgeError):
            s.request_session("https://x.com/v", "job1", [], timeout=0.01)

    def test_concurrent_requests_both_succeed(self):
        """两个并发请求各自按 req_id 取回自己的响应，互不覆盖。"""
        s = make_server()
        s._on_new_client(_fake_client(), s._server)
        responses = {}
        lock = threading.Lock()

        def fake_send(c, msg):
            payload = json.loads(msg)
            # 模拟插件乱序回包：后发的先回，先发的后回
            time.sleep(0.02 if payload["job_id"] == "job2" else 0.06)
            s._on_message(c, s._server, json.dumps(
                {"ok": True, "echo": payload["job_id"], "req_id": payload["req_id"],
                 "token": payload["token"]}))

        s._server.send_message.side_effect = fake_send
        results = []
        threads = []
        for i, job in enumerate(("job1", "job2")):
            def call(j=job):
                results.append(s.request_session(f"https://x.com/v/{j}", j, []))
            t = threading.Thread(target=call)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        self.assertEqual({r["echo"] for r in results}, {"job1", "job2"})

    def test_timeout_does_not_clear_other_pending(self):
        """一个请求超时不误删并发中另一个请求的等待者。"""
        s = make_server()
        s._on_new_client(_fake_client(), s._server)
        sent = []

        def fake_send(c, msg):
            payload = json.loads(msg)
            sent.append(payload)
            if payload["job_id"] == "job2":
                s._on_message(c, s._server, json.dumps(
                    {"ok": True, "echo": "job2", "req_id": payload["req_id"],
                     "token": payload["token"]}))

        s._server.send_message.side_effect = fake_send
        # job1 超时（20ms），job2 正常返回（响应在 job1 超时后才到也无妨）
        errs = []
        results = []
        threads = []

        def call1():
            try:
                s.request_session("https://x.com/v/1", "job1", [], timeout=0.02)
            except SessionBridgeError:
                errs.append(True)

        def call2():
            time.sleep(0.05)  # 等 job1 进入等待、触发超时清理
            results.append(s.request_session("https://x.com/v/2", "job2", []))

        t1 = threading.Thread(target=call1)
        t2 = threading.Thread(target=call2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(errs)  # job1 超时
        self.assertEqual([r["echo"] for r in results], ["job2"])  # job2 不受影响


class TestExtensionSessionProvider(unittest.TestCase):
    def test_two_tasks_share_credentials_but_get_distinct_temp_files(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = {
            "ok": True,
            "user_agent": "UA/1.0",
            "cookies": [{"domain": ".bilibili.com", "name": "SESSDATA", "value": "abc",
                         "path": "/", "secure": True, "expirationDate": 1780000000}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            p = ExtensionSessionProvider(
                bridge,
                cfg={"paths": {"temp": temp_dir}},
            )
            s = p.get_session(
                url="https://www.bilibili.com/video/BV1xx",
                platform="bilibili",
                job_id="job1",
            )
            self.assertIsInstance(s, BrowserSession)
            self.assertIsNotNone(s.cookie_file)
            self.assertEqual(s.cookie_file.parent, Path(temp_dir))
            text = s.cookie_file.read_text(encoding="utf-8")
            self.assertIn("SESSDATA", text)
            self.assertEqual(s.headers, {})
            second = p.get_session(
                url="https://www.bilibili.com/video/BV2yy",
                platform="bilibili",
                job_id="job2",
            )
            self.assertNotEqual(s.cookie_file, second.cookie_file)
            self.assertEqual(s.credential_snapshot, second.credential_snapshot)
            bridge.request_session.assert_called_once()
            self.assertIn(
                ".bilivideo.com",
                bridge.request_session.call_args[0][2],
            )  # 平台域集合

    def test_refresh_materializes_new_generation(self):
        bridge = mock.Mock()
        bridge.request_session.side_effect = [
            {"ok": True, "cookies": [{
                "domain": ".bilibili.com", "name": "SESSDATA", "value": "old",
            }]},
            {"ok": True, "cookies": [{
                "domain": ".bilibili.com", "name": "SESSDATA", "value": "new",
            }]},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = ExtensionSessionProvider(bridge, cfg={"paths": {"temp": temp_dir}})
            stale = provider.get_session(
                url="https://www.bilibili.com/video/1", platform="bilibili", job_id="one",
            )
            fresh = provider.refresh_session(
                stale_session=stale, url="https://www.bilibili.com/video/1",
                platform="bilibili", job_id="refresh",
            )
            self.assertEqual(fresh.credential_snapshot.generation, 2)
            self.assertNotEqual(stale.cookie_file, fresh.cookie_file)
            self.assertIn("new", fresh.cookie_file.read_text(encoding="utf-8"))
            self.assertEqual(bridge.request_session.call_count, 2)

    def test_successful_yt_dlp_cookie_jar_commits_to_shared_cache(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = {
            "ok": True,
            "cookies": [{
                "domain": ".bilibili.com", "name": "SESSDATA", "value": "old",
                "path": "/", "secure": True,
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = ExtensionSessionProvider(
                bridge, cfg={"paths": {"temp": temp_dir}},
            )
            session = provider.get_session(
                url="https://www.bilibili.com/video/1",
                platform="bilibili",
                job_id="one",
            )
            write_netscape_file([{
                "domain": ".bilibili.com", "name": "SESSDATA", "value": "new",
                "path": "/", "secure": True,
            }], session.cookie_file)
            self.assertTrue(provider.commit_session(session))
            current = provider.credential_broker.acquire(
                "https://www.bilibili.com/video/2",
                "bilibili",
                "two",
                [".bilibili.com", ".bilivideo.com"],
            )
        self.assertEqual(current.revision, 1)
        self.assertEqual(current.cookies[0]["value"], "new")
        bridge.request_session.assert_called_once()

    def test_youtube_bundle_is_cached_and_materialized(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = {
            "ok": True, "cookies": [{"domain": ".youtube.com", "name": "SID", "value": "x"}],
        }
        bridge.request_mint.return_value = {
            "ok": True, "visitor_data": "visitor", "po_token": "po", "token_scope": "player",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = ExtensionSessionProvider(bridge, cfg={"paths": {"temp": temp_dir}})
            first = provider.get_session(
                url="https://youtube.com/watch?v=1", platform="youtube", job_id="one",
            )
            second = provider.get_session(
                url="https://youtube.com/watch?v=2", platform="youtube", job_id="two",
            )
            self.assertEqual(first.visitor_data, "visitor")
            self.assertEqual(second.po_token, "po")
            self.assertEqual(bridge.request_session.call_count, 1)
            self.assertEqual(bridge.request_mint.call_count, 1)

    def test_plugin_rejects(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = {"ok": False, "error": "permission_required", "origins": ["https://x.com"]}
        p = ExtensionSessionProvider(bridge)
        with self.assertRaises(RuntimeError):
            p.get_session(url="https://x.com/v", platform="generic", job_id="job1")


class TestPlatformSessionDomainsConfig(unittest.TestCase):
    def test_cfg_override(self):
        cfg = {"platforms": {"session_domains": {"bilibili": [".custom.com"]}}}
        self.assertEqual(platforms.session_domains("bilibili", cfg), [".custom.com"])
        self.assertEqual(platforms.session_domains("bilibili"), [".bilibili.com", ".bilivideo.com"])
        self.assertEqual(platforms.session_domains("unknown"), [])


if __name__ == "__main__":
    unittest.main()
