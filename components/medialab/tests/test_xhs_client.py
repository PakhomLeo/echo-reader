import binascii
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from medialab.browser_http import BrowserHttpError
from medialab.session import CredentialBroker, FailureCode
from medialab.xhs_client import XhsClient


def fake_png(width=640, height=480):
    def chunk(kind, payload):
        checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = b"".join(b"\x00" + bytes([row % 256]) * width for row in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"tEXt", b"fixture\x00" + b"x" * 1024)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


class FakeResponse:
    def __init__(self, *, status=200, body=b"body", content_type="text/html",
                 url="https://www.xiaohongshu.com/explore/a?token=secret"):
        self.status_code = status
        self._body = body
        self.headers = {"content-type": content_type}
        self.url = url
        self.text = body.decode("utf-8", errors="replace")

    def iter_content(self, chunk_size=None):
        yield self._body


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, *_args):
        return False


class FakeSession:
    def __init__(self):
        self.cookies = mock.Mock()
        self.page_response = FakeResponse(body=b"<html>ok</html>")
        self.stream_responses = []
        self.get_calls = []
        self.stream_calls = []
        self.closed = 0

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.page_response

    def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url, kwargs))
        return FakeStream(self.stream_responses.pop(0))

    def close(self):
        self.closed += 1


def bridge_response(value="secret"):
    return {
        "ok": True,
        "user_agent": "Chrome profile UA",
        "cookies": [{
            "name": "web_session",
            "value": value,
            "domain": ".xiaohongshu.com",
            "path": "/explore",
            "secure": True,
        }],
    }


class XhsClientTest(unittest.TestCase):
    def test_uses_shared_chrome146_client_and_preserves_cookie_scope(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = bridge_response()
        session = FakeSession()
        broker = CredentialBroker(bridge)

        with mock.patch(
            "medialab.browser_http.requests.Session", return_value=session,
        ) as factory:
            client = XhsClient.from_broker(
                broker,
                "https://www.xiaohongshu.com/explore/a?token=secret",
                "job",
                domains=[".xiaohongshu.com", ".xhscdn.com"],
            )

        factory.assert_called_once_with(
            impersonate="chrome146", default_headers=True, retry=0,
            trust_env=False,
        )
        session.cookies.set.assert_called_once_with(
            "web_session",
            "secret",
            domain=".xiaohongshu.com",
            path="/explore",
            secure=True,
        )
        bridge.request_session.assert_called_once()
        client.close()
        self.assertEqual(session.closed, 1)

    def test_snapshot_reused_but_task_sessions_are_independent(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = bridge_response()
        broker = CredentialBroker(bridge)
        sessions = [FakeSession(), FakeSession()]
        with mock.patch(
            "medialab.browser_http.requests.Session", side_effect=sessions,
        ):
            first = XhsClient.from_broker(
                broker, "https://www.xiaohongshu.com/n/1", "one",
                domains=[".xiaohongshu.com", ".xhscdn.com"],
            )
            second = XhsClient.from_broker(
                broker, "https://www.xiaohongshu.com/n/2", "two",
                domains=[".xiaohongshu.com", ".xhscdn.com"],
            )
        self.assertEqual(bridge.request_session.call_count, 1)
        self.assertIsNot(first._session, second._session)

    def test_page_and_resources_share_session_and_each_get_once(self):
        session = FakeSession()
        session.stream_responses = [
            FakeResponse(body=fake_png(), content_type="image/png",
                         url="https://img.xhscdn.com/a"),
            FakeResponse(body=b"v" * 100_001, content_type="video/mp4",
                         url="https://video.xhscdn.com/v"),
        ]
        client = XhsClient(
            session,
            platform="xiaohongshu",
            source_url="https://www.xiaohongshu.com/explore/a?token=secret",
            job_id="job",
            page_timeout=1,
            resource_timeout=2,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            page = client.fetch_page(client.source_url)
            image = client.download_image(
                "https://img.xhscdn.com/no-extension?sig=x",
                root / "0000.jpg",
                page["final_url"],
                10_000,
            )
            size = client.download_video(
                "https://video.xhscdn.com/v?sig=x",
                root / "video.mp4",
                page["final_url"],
            )
            self.assertEqual(image.suffix, ".png")
            self.assertTrue(image.exists())
            self.assertEqual(size, 100_001)
        self.assertEqual(len(session.get_calls), 1)
        self.assertEqual(len(session.stream_calls), 2)
        self.assertFalse(session.get_calls[0][1]["allow_redirects"])
        self.assertTrue(all(
            call[2]["allow_redirects"] is False
            for call in session.stream_calls
        ))
        self.assertTrue(all(
            call[2]["headers"]["Referer"] == "https://www.xiaohongshu.com/"
            for call in session.stream_calls
        ))

    def test_rejects_placeholder_dimensions_and_invalid_image_magic(self):
        session = FakeSession()
        session.stream_responses = [
            FakeResponse(
                body=fake_png(32, 32),
                content_type="image/png",
                url="https://img.xhscdn.com/placeholder.png",
            ),
            FakeResponse(
                body=b"not-an-image" + b"x" * 2036,
                content_type="image/jpeg",
                url="https://img.xhscdn.com/invalid.jpg",
            ),
        ]
        client = XhsClient(
            session,
            platform="xiaohongshu",
            source_url="https://www.xiaohongshu.com/n",
            job_id="job",
            page_timeout=1,
            resource_timeout=2,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, url in (
                ("placeholder.png", "https://img.xhscdn.com/placeholder.png"),
                ("invalid.jpg", "https://img.xhscdn.com/invalid.jpg"),
            ):
                with self.subTest(name=name):
                    out = root / name
                    with self.assertRaises(BrowserHttpError) as ctx:
                        client.download_image(
                            url,
                            out,
                            "https://www.xiaohongshu.com/n",
                            10_000,
                        )
                    self.assertEqual(ctx.exception.code, FailureCode.NETWORK)
                    self.assertFalse(out.exists())

    def test_401_refreshes_for_next_task_without_retrying_current(self):
        bridge = mock.Mock()
        bridge.request_session.side_effect = [bridge_response("old"), bridge_response("new")]
        broker = CredentialBroker(bridge)
        old_session = FakeSession()
        old_session.page_response = FakeResponse(status=401, body=b"denied")
        new_session = FakeSession()
        with mock.patch(
            "medialab.browser_http.requests.Session",
            side_effect=[old_session, new_session],
        ):
            current = XhsClient.from_broker(
                broker, "https://www.xiaohongshu.com/n", "one",
                domains=[".xiaohongshu.com"],
            )
            with self.assertRaises(BrowserHttpError) as ctx:
                current.fetch_page("https://www.xiaohongshu.com/n?token=secret")
            following = XhsClient.from_broker(
                broker, "https://www.xiaohongshu.com/next", "two",
                domains=[".xiaohongshu.com"],
            )
        self.assertEqual(ctx.exception.code, FailureCode.AUTH_REQUIRED)
        self.assertNotIn("secret", str(ctx.exception))
        self.assertEqual(len(old_session.get_calls), 1)
        self.assertEqual(bridge.request_session.call_count, 2)
        self.assertEqual(following._snapshot.generation, 2)

    def test_403_429_network_and_resource_failure_do_not_refresh_or_retry(self):
        cases = (
            (FakeResponse(status=403, body=b"forbidden"), FailureCode.NETWORK),
            (FakeResponse(status=429, body=b"rate limited"), FailureCode.RATE_LIMITED),
            (RuntimeError("network ?token=secret"), FailureCode.NETWORK),
        )
        for index, (outcome, expected) in enumerate(cases):
            with self.subTest(index=index):
                bridge = mock.Mock()
                bridge.request_session.return_value = bridge_response()
                session = FakeSession()
                if isinstance(outcome, Exception):
                    session.get = mock.Mock(side_effect=outcome)
                else:
                    session.page_response = outcome
                with mock.patch(
                    "medialab.browser_http.requests.Session", return_value=session,
                ):
                    client = XhsClient.from_broker(
                        CredentialBroker(bridge),
                        "https://www.xiaohongshu.com/n",
                        f"job-{index}",
                        domains=[".xiaohongshu.com"],
                    )
                    with self.assertRaises(BrowserHttpError) as ctx:
                        client.fetch_page("https://www.xiaohongshu.com/n?token=secret")
                self.assertEqual(ctx.exception.code, expected)
                self.assertNotIn("secret", str(ctx.exception))
                self.assertEqual(bridge.request_session.call_count, 1)

        class BrokenResponse(FakeResponse):
            def iter_content(self, chunk_size=None):
                yield b"x" * 2048
                raise RuntimeError("token=secret")

        session = FakeSession()
        session.stream_responses = [
            BrokenResponse(content_type="image/jpeg", url="https://img.xhscdn.com/a.jpg"),
        ]
        client = XhsClient(
            session,
            platform="xiaohongshu",
            source_url="https://www.xiaohongshu.com/n",
            job_id="job",
            page_timeout=1,
            resource_timeout=2,
        )
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "image.jpg"
            with self.assertRaises(BrowserHttpError):
                client.download_image(
                    "https://img.xhscdn.com/a.jpg?token=secret",
                    out,
                    "https://www.xiaohongshu.com/n?token=secret",
                    10_000,
                )
            self.assertFalse(out.exists())
        self.assertEqual(len(session.stream_calls), 1)

    def test_login_redirect_refreshes_and_untrusted_targets_are_rejected(self):
        bridge = mock.Mock()
        bridge.request_session.side_effect = [bridge_response("old"), bridge_response("new")]
        session = FakeSession()
        session.page_response = FakeResponse(
            body=b"login",
            url="https://www.xiaohongshu.com/login?redirect=x",
        )
        with mock.patch(
            "medialab.browser_http.requests.Session", return_value=session,
        ):
            client = XhsClient.from_broker(
                CredentialBroker(bridge),
                "https://www.xiaohongshu.com/n",
                "job",
                domains=[".xiaohongshu.com"],
            )
            with self.assertRaises(BrowserHttpError) as ctx:
                client.fetch_page("https://www.xiaohongshu.com/n")
        self.assertEqual(ctx.exception.code, FailureCode.AUTH_REQUIRED)
        self.assertEqual(bridge.request_session.call_count, 2)

        direct = XhsClient(
            FakeSession(),
            platform="xiaohongshu",
            source_url="https://www.xiaohongshu.com/n",
            job_id="job",
            page_timeout=1,
            resource_timeout=1,
        )
        with self.assertRaises(BrowserHttpError):
            direct.fetch_page("http://www.xiaohongshu.com/n")
        with tempfile.TemporaryDirectory() as td, self.assertRaises(BrowserHttpError):
            direct.download_image(
                "https://127.0.0.1/a.jpg",
                Path(td) / "a.jpg",
                "https://www.xiaohongshu.com/n",
                10_000,
            )
        self.assertEqual(direct._session.get_calls, [])
        self.assertEqual(direct._session.stream_calls, [])


if __name__ == "__main__":
    unittest.main()
