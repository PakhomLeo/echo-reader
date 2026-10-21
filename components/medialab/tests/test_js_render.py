from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from unittest import mock

from medialab.config import load_config
from medialab.js_render import JsRenderError, render_page, should_render


@dataclass
class _Probe:
    render_recommended: bool


class _Request:
    def __init__(self, url, resource_type="document"):
        self.url = url
        self.resource_type = resource_type


class _Route:
    def __init__(self, request):
        self.request = request
        self.action = None

    def abort(self):
        self.action = "abort"

    def continue_(self):
        self.action = "continue"


class _Response:
    def __init__(self, length):
        self.length = length

    def header_value(self, name):
        return self.length if name == "content-length" else None


class _Page:
    def __init__(self, requests=(), *, fail=False):
        self.url = "https://mp.weixin.qq.com/s/final"
        self.requests = list(requests)
        self.routes = []
        self.handlers = {}
        self.fail = fail
        self.scrolled = 0

    def route(self, pattern, handler):
        self.route_pattern = pattern
        self.route_handler = handler

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url, **kwargs):
        self.goto_args = (url, kwargs)
        if self.fail:
            raise TimeoutError("navigation timed out")
        for request in self.requests:
            route = _Route(request)
            self.route_handler(route)
            self.routes.append(route)
            if route.action == "continue":
                self.handlers["response"](_Response("123"))

    def evaluate(self, expression):
        if expression.startswith("window.scrollTo"):
            self.scrolled += 1
            return None
        return 100 if self.scrolled == 0 else 200

    def wait_for_timeout(self, delay):
        self.delay = delay

    def content(self):
        return f"<html><article>lazy-{self.scrolled}</article></html>"


class _Context:
    def __init__(self, page):
        self.page = page
        self.cookies = []
        self.closed = False

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class _Browser:
    def __init__(self, context):
        self.context = context
        self.closed = False
        self.context_options = None

    def new_context(self, **kwargs):
        self.context_options = kwargs
        return self.context

    def close(self):
        self.closed = True


class _Chromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_options = None

    def launch(self, **kwargs):
        self.launch_options = kwargs
        return self.browser


class _Manager:
    def __init__(self, chromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _playwright_patch(page):
    context = _Context(page)
    browser = _Browser(context)
    chromium = _Chromium(browser)
    manager = _Manager(chromium)
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: manager
    package = types.ModuleType("playwright")
    package.sync_api = module
    patcher = mock.patch.dict(
        sys.modules, {"playwright": package, "playwright.sync_api": module}
    )
    return patcher, chromium, browser, context


class JsRenderPolicyTests(unittest.TestCase):
    def test_default_platform_policy_is_opt_in(self):
        cfg = load_config()
        quiet = _Probe(False)
        missing = _Probe(True)

        self.assertTrue(should_render("wechat", quiet, cfg))
        self.assertFalse(should_render("zhihu", quiet, cfg))
        self.assertTrue(should_render("zhihu", missing, cfg))
        self.assertFalse(should_render("toutiao", quiet, cfg))
        self.assertTrue(should_render("toutiao", missing, cfg))
        self.assertFalse(should_render("generic", missing, cfg))

    def test_global_off_overrides_platform_modes(self):
        cfg = {"js_render": {"enabled": False, "platform_modes": {"wechat": "always"}}}
        self.assertFalse(should_render("wechat", _Probe(True), cfg))

    def test_wechat_always_and_on_miss_modes(self):
        cfg = {"js_render": {"enabled": True, "platform_modes": {
            "wechat": "always", "zhihu": "on_miss", "toutiao": "off"
        }}}
        self.assertTrue(should_render("wechat", _Probe(False), cfg))
        self.assertTrue(should_render("zhihu", _Probe(True), cfg))
        self.assertFalse(should_render("zhihu", _Probe(False), cfg))
        self.assertFalse(should_render("toutiao", _Probe(True), cfg))


class JsRenderRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"js_render": {
            "timeout_seconds": 2, "max_requests": 10, "max_bytes": 10000,
            "scroll_steps": 4, "scroll_delay_ms": 1,
            "resource_domains": [".qq.com"],
        }}

    def test_edge_isolated_context_lazy_scroll_and_scoped_cookies(self):
        page = _Page([_Request("https://mp.weixin.qq.com/s/a")])
        patcher, chromium, browser, context = _playwright_patch(page)
        cookies = [
            {"name": "ok", "value": "1", "domain": ".weixin.qq.com", "path": "/",
             "sameSite": "no_restriction", "expirationDate": 1780000000},
            {"name": "unspecified", "value": "1", "domain": ".weixin.qq.com",
             "sameSite": "unspecified"},
            {"name": "bad", "value": "2", "domain": ".example.com", "path": "/"},
        ]
        with patcher:
            result = render_page("https://mp.weixin.qq.com/s/a", config=self.cfg, cookies=cookies)
        self.assertIn("lazy-", result.html)
        self.assertGreater(page.scrolled, 0)
        self.assertEqual(result.final_url, "https://mp.weixin.qq.com/s/final")
        self.assertEqual(result.request_count, 1)
        self.assertEqual(result.bytes_seen, 123)
        self.assertEqual(chromium.launch_options, {"channel": "msedge", "headless": True})
        self.assertEqual(browser.context_options, {
            "accept_downloads": False, "service_workers": "block"
        })
        self.assertEqual([item["name"] for item in context.cookies], ["ok", "unspecified"])
        self.assertEqual(context.cookies[0]["sameSite"], "None")
        self.assertEqual(context.cookies[0]["expires"], 1780000000.0)
        self.assertNotIn("sameSite", context.cookies[1])
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    def test_forbidden_and_download_requests_are_aborted(self):
        requests = [
            _Request("http://example.com/script.js", "script"),
            _Request("https://127.0.0.1/a", "xhr"),
            _Request("https://www.google-analytics.com/collect", "fetch"),
            _Request("https://cdn.example.com/movie.mp4", "other"),
            _Request("https://cdn.example.com/font.woff2", "font"),
            _Request("https://res.wx.qq.com/article.json", "xhr"),
        ]
        page = _Page(requests)
        patcher, _, _, _ = _playwright_patch(page)
        with patcher:
            result = render_page("https://mp.weixin.qq.com/s/a", config=self.cfg)
        self.assertEqual([route.action for route in page.routes], [
            "abort", "abort", "abort", "abort", "abort", "continue"
        ])
        self.assertEqual(result.request_count, 6)
        self.assertEqual(result.bytes_seen, 123)

    def test_navigation_error_still_closes_browser_and_context(self):
        page = _Page(fail=True)
        patcher, _, browser, context = _playwright_patch(page)
        with patcher, self.assertRaisesRegex(JsRenderError, "TimeoutError"):
            render_page("https://mp.weixin.qq.com/s/a", config=self.cfg)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    def test_rejects_local_navigation_before_loading_playwright(self):
        with self.assertRaisesRegex(JsRenderError, "local"):
            render_page("https://localhost/article", config=self.cfg)


if __name__ == "__main__":
    unittest.main()
