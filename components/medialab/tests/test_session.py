import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from medialab.session import (
    CredentialBroker,
    FailureCode,
    classify_error,
    is_auth_failure,
    url_for_log,
    write_netscape_file,
)


class TestClassifyError(unittest.TestCase):
    def test_auth_codes(self):
        cases = {
            "ERROR: Sign in to confirm you're not a bot": FailureCode.AUTH_REQUIRED,
            "ERROR: Fresh cookies are needed": FailureCode.AUTH_REQUIRED,
            "ERROR: The provided YouTube account cookies are no longer valid": FailureCode.COOKIE_ROTATED,
            "ERROR: Could not copy Chrome cookie database": FailureCode.AUTH_REQUIRED,
            "ERROR: This content is missing a PO Token": FailureCode.PO_TOKEN_REJECTED,
        }
        for msg, expected in cases.items():
            self.assertEqual(classify_error(Exception(msg)), expected, msg)

    def test_network_rate_drm(self):
        self.assertEqual(classify_error(Exception("HTTP Error 429: Too Many Requests")), FailureCode.RATE_LIMITED)
        self.assertEqual(classify_error(Exception("Connection timed out")), FailureCode.NETWORK)
        self.assertEqual(classify_error(Exception("HTTP Error 503")), FailureCode.NETWORK)
        self.assertEqual(classify_error(Exception("This content is DRM protected")), FailureCode.DRM_PROTECTED)
        self.assertEqual(classify_error(Exception("HTTP Error 403")), FailureCode.MEDIA_403)
        self.assertEqual(classify_error(Exception("Unsupported URL")), FailureCode.UNSUPPORTED)
        self.assertEqual(classify_error(Exception("weird")), FailureCode.UNKNOWN)

    def test_is_auth_failure(self):
        self.assertTrue(is_auth_failure(Exception("Sign in to confirm you're not a bot")))
        self.assertTrue(is_auth_failure(Exception("cookies are no longer valid")))
        self.assertTrue(is_auth_failure(Exception("PO Token was rejected")))
        self.assertFalse(is_auth_failure(Exception("HTTP Error 503")))
        self.assertFalse(is_auth_failure(Exception("random")))

    def test_url_for_log_removes_query_and_fragment(self):
        self.assertEqual(
            url_for_log("https://example.test/image.jpg?xsec_token=secret#part"),
            "https://example.test/image.jpg",
        )


class TestWriteNetscape(unittest.TestCase):
    def test_write(self):
        with tempfile.TemporaryDirectory() as td:
            p = write_netscape_file([
                {"domain": ".bilibili.com", "name": "SESSDATA", "value": "abc",
                 "path": "/", "secure": True, "expirationDate": 1780000000},
                {"domain": "example.com", "name": "x", "value": "y",
                 "path": "/", "secure": False, "expirationDate": None},
            ], Path(td) / "cookies.txt")
            text = p.read_text(encoding="utf-8")
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), 3)  # header + 2
        self.assertIn(".bilibili.com\tTRUE\t/\tTRUE\t1780000000\tSESSDATA\tabc", lines[1])
        self.assertIn("example.com\tFALSE\t/\tFALSE\t0\tx\ty", lines[2])


def _cookie(domain=".example.com", value="secret"):
    return {"domain": domain, "name": "sid", "value": value, "path": "/"}


class TestCredentialBroker(unittest.TestCase):
    def test_reuses_same_key_and_isolates_platforms(self):
        bridge = mock.Mock()
        bridge.request_session.side_effect = lambda url, job, domains: {
            "ok": True, "user_agent": "Chrome profile UA",
            "cookies": [_cookie(domains[0], job)],
        }
        broker = CredentialBroker(bridge)
        first = broker.acquire("https://example.com/a", "generic", "one", [".example.com"])
        second = broker.acquire("https://example.com/b", "GENERIC", "two", ["example.com"])
        other = broker.acquire("https://example.com/c", "article", "three", [".example.com"])
        self.assertIs(first, second)
        self.assertIsNot(first, other)
        self.assertEqual(bridge.request_session.call_count, 2)

    def test_empty_cookie_snapshot_is_valid_and_cached(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = {
            "ok": True, "user_agent": "Chrome profile UA", "cookies": [],
        }
        broker = CredentialBroker(bridge)
        first = broker.acquire(
            "https://example.com/a", "generic", "one", [".example.com"],
        )
        second = broker.acquire(
            "https://example.com/b", "generic", "two", ["example.com"],
        )
        self.assertIs(first, second)
        self.assertEqual(first.cookies, ())
        bridge.request_session.assert_called_once()

    def test_concurrent_acquire_and_refresh_are_single_flight(self):
        bridge = mock.Mock()
        entered = threading.Event()
        release = threading.Event()

        def request(url, job, domains):
            entered.set()
            release.wait(1)
            return {"ok": True, "cookies": [_cookie(domains[0], job)]}

        bridge.request_session.side_effect = request
        broker = CredentialBroker(bridge)
        acquired = []
        threads = [threading.Thread(target=lambda: acquired.append(
            broker.acquire("https://example.com/a", "generic", "job", [".example.com"])
        )) for _ in range(4)]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(1))
        release.set()
        for thread in threads:
            thread.join()
        self.assertEqual(bridge.request_session.call_count, 1)

        bridge.request_session.reset_mock()
        refreshed = []
        threads = [threading.Thread(target=lambda: refreshed.append(
            broker.refresh_if_current(acquired[0], "https://example.com/a", "refresh")
        )) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(bridge.request_session.call_count, 1)
        self.assertEqual({item.generation for item in refreshed}, {2})

    def test_refresh_failure_clears_stale_and_generation_is_monotonic(self):
        bridge = mock.Mock()
        bridge.request_session.side_effect = [
            {"ok": True, "cookies": [_cookie()]},
            RuntimeError("offline"),
            {"ok": True, "cookies": [_cookie(value="fresh")]},
        ]
        broker = CredentialBroker(bridge)
        stale = broker.acquire("https://example.com/a", "generic", "one", [".example.com"])
        with self.assertRaisesRegex(RuntimeError, "offline"):
            broker.refresh_if_current(stale, "https://example.com/a", "refresh")
        fresh = broker.acquire("https://example.com/b", "generic", "two", [".example.com"])
        self.assertGreater(fresh.generation, stale.generation)
        self.assertEqual(fresh.cookies[0]["value"], "fresh")

    def test_domain_validation_repr_and_revision_cas(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = {
            "ok": True, "user_agent": "UA", "cookies": [_cookie()],
        }
        broker = CredentialBroker(bridge)
        snapshot = broker.acquire(
            "https://example.com/a", "generic", "one", [".example.com"],
        )
        self.assertNotIn("secret", repr(snapshot))
        self.assertIn("cookies=<redacted:1>", repr(snapshot))
        self.assertTrue(broker.update_if_current(snapshot, [_cookie(value="new")]))
        self.assertFalse(broker.update_if_current(snapshot, [_cookie(value="rollback")]))
        self.assertFalse(broker.update_if_current(snapshot, [_cookie(".evil.test")]))
        current = broker.acquire(
            "https://example.com/b", "generic", "two", ["example.com"],
        )
        self.assertEqual(current.revision, 1)
        self.assertEqual(current.cookies[0]["value"], "new")

        bad_bridge = mock.Mock()
        bad_bridge.request_session.return_value = {
            "ok": True, "cookies": [_cookie(".evil.test")],
        }
        with self.assertRaisesRegex(RuntimeError, "授权域"):
            CredentialBroker(bad_bridge).acquire(
                "https://example.com", "generic", "bad", [".example.com"],
            )

    def test_youtube_mints_once_per_generation_and_failure_is_cookie_only(self):
        bridge = mock.Mock()
        bridge.request_session.return_value = {
            "ok": True, "cookies": [_cookie(".youtube.com")],
        }
        bridge.request_mint.return_value = {
            "ok": True, "visitor_data": "visitor", "po_token": "po",
            "token_scope": "player",
        }
        broker = CredentialBroker(bridge)
        first = broker.acquire(
            "https://youtube.com/watch?v=1", "youtube", "one", [".youtube.com"],
        )
        broker.acquire(
            "https://youtube.com/watch?v=2", "youtube", "two", ["youtube.com"],
        )
        self.assertEqual(bridge.request_mint.call_count, 1)
        self.assertEqual(first.po_token, "po")
        broker.refresh_if_current(first, "https://youtube.com/watch?v=1", "refresh")
        self.assertEqual(bridge.request_mint.call_count, 2)

        failing = mock.Mock()
        failing.request_session.return_value = {
            "ok": True, "cookies": [_cookie(".youtube.com")],
        }
        failing.request_mint.side_effect = RuntimeError("mint offline")
        cookie_only = CredentialBroker(failing).acquire(
            "https://youtube.com/watch?v=3", "youtube", "three", [".youtube.com"],
        )
        self.assertIsNone(cookie_only.po_token)


if __name__ == "__main__":
    unittest.main()
