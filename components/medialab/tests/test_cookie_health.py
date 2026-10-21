"""Offline Cookie health checks (ported from UP's cookie checker)."""

import unittest

from medialab.cookie_health import check_cookie_health, requirement_for
from medialab.session import CredentialBroker

NOW = 1_800_000_000.0
DAY = 86400.0


def cookie(name, expires=None, domain=".bilibili.com", value="secret-value"):
    item = {"name": name, "value": value, "domain": domain}
    if expires is not None:
        item["expires"] = expires
    return item


class TestCookieHealth(unittest.TestCase):
    def test_requirements(self):
        self.assertEqual(requirement_for("xiaohongshu"), "required")
        self.assertEqual(requirement_for("BILIBILI"), "recommended")
        self.assertEqual(requirement_for("weibo"), "optional")
        self.assertEqual(requirement_for("generic"), "not_needed")

    def test_statuses(self):
        cases = [
            ("bilibili", [cookie("SESSDATA", NOW + 30 * DAY)], "ok", True),
            ("bilibili", [cookie("SESSDATA", NOW + DAY)], "expiring_soon", True),
            ("bilibili", [cookie("SESSDATA", NOW - 1)], "expired", False),
            ("bilibili", [cookie("buvid3", NOW + 30 * DAY)], "anonymous", False),
            ("bilibili", [], "missing", False),
            ("weibo", [], "missing", True),
            ("generic", None, "not_needed", True),
            ("bilibili", [cookie("SESSDATA", -1)], "ok", True),  # session cookie
            ("medium", [cookie("sid", NOW - 5)], "expired", False),
        ]
        for platform, cookies, status, healthy in cases:
            with self.subTest(platform=platform, status=status):
                result = check_cookie_health(platform, cookies, now=NOW)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["healthy"], healthy)

    def test_never_exposes_values(self):
        result = check_cookie_health("bilibili", [cookie("SESSDATA", NOW + 30 * DAY)], now=NOW)
        self.assertNotIn("secret-value", repr(result))
        self.assertEqual(result["login_cookies_present"], ["SESSDATA"])
        self.assertEqual(result["cookie_count"], 1)
        self.assertTrue(result["earliest_login_expiry"].endswith("Z"))

    def test_broker_health_report_uses_cache_only(self):
        class Bridge:
            calls = 0

            def request_session(self, url, job_id, domains):
                Bridge.calls += 1
                return {"ok": True, "user_agent": "UA",
                        "cookies": [cookie("SESSDATA", NOW + 30 * DAY)]}

        broker = CredentialBroker(Bridge(), mint_youtube=False)
        self.assertEqual(broker.health_report(now=NOW), [])
        broker.acquire("https://www.bilibili.com/video/BV1", "bilibili", "job",
                       [".bilibili.com", ".bilivideo.com"])
        report = broker.health_report(now=NOW)
        self.assertEqual(Bridge.calls, 1)
        broker.health_report(now=NOW)
        self.assertEqual(Bridge.calls, 1)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["status"], "ok")
        self.assertEqual(report[0]["domains"], [".bilibili.com", ".bilivideo.com"])
        self.assertTrue(report[0]["cached"])
        self.assertNotIn("secret-value", repr(report))


if __name__ == "__main__":
    unittest.main()
