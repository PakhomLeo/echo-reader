import tempfile
import unittest
from pathlib import Path
from unittest import mock

from curl_cffi import requests
from curl_cffi.const import CurlOpt

from medialab.browser_http import BrowserHttpClient, BrowserHttpError
from medialab.session import CredentialBroker, FailureCode

from test_xhs_client import FakeResponse, FakeSession, bridge_response


class _ClosingStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, *_args):
        self.response.exited = getattr(self.response, "exited", 0) + 1
        return False


class _PinnedFakeSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.curl_options = {}
        self.option_snapshots = []
        self.page_responses = []
        self.page_error = None

    def _snapshot_options(self):
        self.option_snapshots.append({
            option: list(value) if isinstance(value, list) else value
            for option, value in self.curl_options.items()
        })

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        self._snapshot_options()
        if self.page_error is not None:
            raise self.page_error
        if self.page_responses:
            return self.page_responses.pop(0)
        return self.page_response

    def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url, kwargs))
        self._snapshot_options()
        return _ClosingStream(self.stream_responses.pop(0))


class BrowserHttpClientTest(unittest.TestCase):
    def test_generic_authenticated_platform_reuses_broker_snapshot(self):
        bridge = mock.Mock()
        response = bridge_response()
        response["cookies"][0]["domain"] = ".zhihu.com"
        bridge.request_session.return_value = response
        broker = CredentialBroker(bridge)
        sessions = [FakeSession(), FakeSession()]
        for session in sessions:
            session.page_response = FakeResponse(
                body=b"<html>article</html>",
                url="https://zhuanlan.zhihu.com/p/1",
            )
        with mock.patch(
            "medialab.browser_http.requests.Session", side_effect=sessions,
        ):
            first = BrowserHttpClient.open(
                url="https://zhuanlan.zhihu.com/p/1",
                platform="zhihu",
                job_id="one",
                credential_broker=broker,
                domains=[".zhihu.com"],
                authenticated=True,
                require_cookies=True,
            )
            second = BrowserHttpClient.open(
                url="https://www.zhihu.com/question/1",
                platform="zhihu",
                job_id="two",
                credential_broker=broker,
                domains=[".zhihu.com"],
                authenticated=True,
                require_cookies=True,
            )
            first.fetch_page(
                "https://zhuanlan.zhihu.com/p/1",
                allowed_domains=first.credential_domains,
            )
        self.assertEqual(bridge.request_session.call_count, 1)
        self.assertIsNot(first._session, second._session)
        self.assertEqual(len(sessions[0].get_calls), 1)

    def test_anonymous_generic_uses_same_chrome146_session_without_broker(self):
        session = FakeSession()
        session.page_response = FakeResponse(
            body=b"<html>public</html>", url="https://example.com/article",
        )
        with mock.patch(
            "medialab.browser_http.requests.Session", return_value=session,
        ) as factory:
            client = BrowserHttpClient.open(
                url="https://example.com/article",
                platform="generic_article",
                job_id="job",
            )
            result = client.fetch_page("https://example.com/article")
        factory.assert_called_once_with(
            impersonate="chrome146", default_headers=True, retry=0,
            trust_env=False,
        )
        self.assertEqual(result["html"], "<html>public</html>")
        self.assertEqual(len(session.get_calls), 1)

    def test_generic_rejects_local_hostnames_before_opening_session(self):
        for url in (
            "https://localhost/article",
            "https://service.local/article",
            "https://host.docker.internal/article",
            "https://metadata.google.internal/computeMetadata/v1/",
            "https://127.0.0.1/article",
        ):
            with self.subTest(url=url), mock.patch(
                "medialab.browser_http.requests.Session",
            ) as factory, self.assertRaises(BrowserHttpError):
                BrowserHttpClient.open(
                    url=url,
                    platform="generic_article",
                    job_id="blocked",
                )
            factory.assert_not_called()

    def test_dns_validation_rejects_private_or_mixed_answers(self):
        answers = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]
        with mock.patch(
            "medialab.browser_http.socket.getaddrinfo", return_value=answers,
        ), mock.patch("medialab.browser_http.requests.Session") as factory, \
                self.assertRaises(BrowserHttpError):
            BrowserHttpClient.open(
                url="https://example.com/article",
                platform="generic_article",
                job_id="blocked",
                resolve_dns=True,
            )
        factory.assert_not_called()

    def test_dns_validation_accepts_public_answers(self):
        answers = [(2, 1, 6, "", ("93.184.216.34", 443))]
        session = FakeSession()
        with mock.patch(
            "medialab.browser_http.socket.getaddrinfo", return_value=answers,
        ), mock.patch("medialab.browser_http.requests.Session", return_value=session):
            client = BrowserHttpClient.open(
                url="https://example.com/article",
                platform="generic_article",
                job_id="allowed",
                resolve_dns=True,
            )
        client.close()

    def test_secure_dns_mode_rejects_explicit_proxy_before_session_open(self):
        with mock.patch(
            "medialab.browser_http.requests.Session",
        ) as factory, self.assertRaises(BrowserHttpError) as raised:
            BrowserHttpClient.open(
                url="https://example.com/article",
                platform="generic_article",
                job_id="blocked",
                proxy="http://proxy.example:8080",
                resolve_dns=True,
            )
        self.assertEqual(raised.exception.code, FailureCode.NETWORK)
        self.assertIn("不支持代理", str(raised.exception))
        factory.assert_not_called()

    def test_resource_redirect_reresolves_and_pins_each_dns_answer(self):
        first = FakeResponse(
            status=302,
            body=b"",
            url="https://media.example.com/start",
        )
        first.headers = {"location": "https://cdn.example.com/final"}
        final = FakeResponse(
            body=b"x" * 1024,
            content_type="image/png",
            url="https://cdn.example.com/final",
        )
        session = _PinnedFakeSession()
        session.stream_responses = [first, final]
        client = BrowserHttpClient(
            session,
            platform="generic_article",
            source_url="https://example.com/article",
            job_id="job",
            page_timeout=1,
            resource_timeout=1,
            resolve_dns=True,
        )
        answers = [
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("93.184.216.35", 443))],
        ]
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "medialab.browser_http.socket.getaddrinfo", side_effect=answers,
        ) as resolver:
            actual = client.download_image(
                "https://media.example.com/start",
                Path(directory) / "image.jpg",
                "https://example.com/article?token=secret",
                2048,
                allowed_domains=[".example.com"],
            )

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(
            session.option_snapshots[0][CurlOpt.RESOLVE],
            ["media.example.com:443:93.184.216.34"],
        )
        self.assertEqual(
            session.option_snapshots[1][CurlOpt.RESOLVE],
            ["cdn.example.com:443:93.184.216.35"],
        )
        self.assertTrue(all(
            snapshot[CurlOpt.FRESH_CONNECT] == 1
            and snapshot[CurlOpt.FORBID_REUSE] == 1
            for snapshot in session.option_snapshots
        ))
        self.assertEqual(
            [call[1] for call in session.stream_calls],
            [
                "https://media.example.com/start",
                "https://cdn.example.com/final",
            ],
        )
        self.assertTrue(all(
            call[2]["allow_redirects"] is False
            and "Host" not in (call[2]["headers"] or {})
            for call in session.stream_calls
        ))
        self.assertEqual(
            session.stream_calls[0][2]["headers"]["Referer"],
            "https://example.com/",
        )
        self.assertEqual(getattr(first, "exited", 0), 1)
        self.assertEqual(getattr(final, "exited", 0), 1)
        self.assertEqual(session.curl_options, {})
        self.assertEqual(actual.suffix, ".png")

    def test_resource_private_redirect_is_blocked_before_connect_and_cleans_up(self):
        redirect = FakeResponse(
            status=302,
            body=b"",
            url="https://media.example.com/start",
        )
        redirect.headers = {"location": "https://private.example.com/secret"}
        session = _PinnedFakeSession()
        session.stream_responses = [redirect]
        client = BrowserHttpClient(
            session,
            platform="generic_article",
            source_url="https://example.com/article",
            job_id="job",
            page_timeout=1,
            resource_timeout=1,
            resolve_dns=True,
        )
        answers = [
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("127.0.0.1", 443))],
        ]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "image.jpg"
            with mock.patch(
                "medialab.browser_http.socket.getaddrinfo", side_effect=answers,
            ) as resolver, self.assertRaises(BrowserHttpError):
                client.download_image(
                    "https://media.example.com/start",
                    destination,
                    "https://example.com/article",
                    2048,
                    allowed_domains=[".example.com"],
                )
            self.assertFalse(destination.exists())

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(len(session.stream_calls), 1)
        self.assertEqual(getattr(redirect, "exited", 0), 1)
        self.assertEqual(session.curl_options, {})

    def test_page_redirect_is_validated_before_next_request(self):
        redirect = mock.Mock()
        redirect.status_code = 302
        redirect.headers = {"location": "https://private.example.invalid/secret"}
        session = FakeSession()
        session.get = mock.Mock(return_value=redirect)
        client = BrowserHttpClient(
            session,
            platform="zhihu",
            source_url="https://www.zhihu.com/question/1",
            job_id="job",
            page_timeout=1,
            resource_timeout=1,
        )
        with self.assertRaises(BrowserHttpError):
            client.fetch_page(
                "https://www.zhihu.com/question/1", allowed_domains=[".zhihu.com"],
            )
        session.get.assert_called_once()
        redirect.close.assert_called_once()

    def test_page_redirect_reresolves_and_pins_each_dns_answer(self):
        redirect = FakeResponse(
            status=302,
            body=b"",
            url="https://www.example.com/start",
        )
        redirect.headers = {"location": "https://article.example.com/final"}
        redirect.close = mock.Mock()
        final = FakeResponse(
            body=b"<html>article</html>",
            url="https://article.example.com/final",
        )
        final.close = mock.Mock()
        session = _PinnedFakeSession()
        session.page_responses = [redirect, final]
        client = BrowserHttpClient(
            session,
            platform="generic_article",
            source_url="https://www.example.com/start",
            job_id="job",
            page_timeout=1,
            resource_timeout=1,
            resolve_dns=True,
        )
        answers = [
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("93.184.216.35", 443))],
        ]
        with mock.patch(
            "medialab.browser_http.socket.getaddrinfo", side_effect=answers,
        ) as resolver:
            result = client.fetch_page(
                "https://www.example.com/start",
                allowed_domains=[".example.com"],
            )

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(
            session.option_snapshots[0][CurlOpt.RESOLVE],
            ["www.example.com:443:93.184.216.34"],
        )
        self.assertEqual(
            session.option_snapshots[1][CurlOpt.RESOLVE],
            ["article.example.com:443:93.184.216.35"],
        )
        self.assertTrue(all(
            snapshot[CurlOpt.FRESH_CONNECT] == 1
            and snapshot[CurlOpt.FORBID_REUSE] == 1
            for snapshot in session.option_snapshots
        ))
        self.assertEqual(
            [call[0] for call in session.get_calls],
            [
                "https://www.example.com/start",
                "https://article.example.com/final",
            ],
        )
        self.assertTrue(all(
            call[1]["allow_redirects"] is False
            for call in session.get_calls
        ))
        redirect.close.assert_called_once_with()
        final.close.assert_called_once_with()
        self.assertEqual(session.curl_options, {})
        self.assertEqual(result["html"], "<html>article</html>")
        self.assertEqual(result["final_url"], "https://article.example.com/final")

    def test_page_private_redirect_is_blocked_before_connect_and_cleans_up(self):
        redirect = FakeResponse(
            status=302,
            body=b"",
            url="https://www.example.com/start",
        )
        redirect.headers = {"location": "https://private.example.com/secret"}
        redirect.close = mock.Mock()
        session = _PinnedFakeSession()
        session.page_responses = [redirect]
        client = BrowserHttpClient(
            session,
            platform="generic_article",
            source_url="https://www.example.com/start",
            job_id="job",
            page_timeout=1,
            resource_timeout=1,
            resolve_dns=True,
        )
        answers = [
            [(2, 1, 6, "", ("93.184.216.34", 443))],
            [(2, 1, 6, "", ("127.0.0.1", 443))],
        ]
        with mock.patch(
            "medialab.browser_http.socket.getaddrinfo", side_effect=answers,
        ) as resolver, self.assertRaises(BrowserHttpError):
            client.fetch_page(
                "https://www.example.com/start",
                allowed_domains=[".example.com"],
            )

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(len(session.get_calls), 1)
        redirect.close.assert_called_once_with()
        self.assertEqual(session.curl_options, {})

    def test_page_request_failure_restores_transfer_options(self):
        session = _PinnedFakeSession()
        session.page_error = OSError("connect failed")
        client = BrowserHttpClient(
            session,
            platform="generic_article",
            source_url="https://www.example.com/article",
            job_id="job",
            page_timeout=1,
            resource_timeout=1,
            resolve_dns=True,
        )
        answers = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with mock.patch(
            "medialab.browser_http.socket.getaddrinfo", return_value=answers,
        ), self.assertRaises(BrowserHttpError) as raised:
            client.fetch_page("https://www.example.com/article")

        self.assertEqual(raised.exception.code, FailureCode.NETWORK)
        self.assertEqual(
            session.option_snapshots[0][CurlOpt.RESOLVE],
            ["www.example.com:443:93.184.216.34"],
        )
        self.assertEqual(session.curl_options, {})

    def test_successful_task_commits_only_authorized_domain_cookies(self):
        bridge = mock.Mock()
        response = bridge_response("old")
        response["cookies"][0]["domain"] = ".zhihu.com"
        bridge.request_session.return_value = response
        broker = CredentialBroker(bridge)
        session = FakeSession()
        session.cookies = requests.Cookies()
        with mock.patch(
            "medialab.browser_http.requests.Session", return_value=session,
        ):
            client = BrowserHttpClient.open(
                url="https://www.zhihu.com/question/1",
                platform="zhihu",
                job_id="one",
                credential_broker=broker,
                domains=[".zhihu.com"],
                authenticated=True,
                require_cookies=True,
            )
        session.cookies.set("web_session", "new", domain=".zhihu.com", path="/")
        session.cookies.set("cdn", "ignored", domain=".example-cdn.com", path="/")
        client.commit_credentials()
        current = broker.acquire(
            "https://www.zhihu.com/question/2", "zhihu", "two", [".zhihu.com"],
        )
        self.assertEqual(current.revision, 1)
        self.assertEqual({cookie["domain"] for cookie in current.cookies}, {".zhihu.com"})
        self.assertIn("new", {cookie["value"] for cookie in current.cookies})


if __name__ == "__main__":
    unittest.main()
