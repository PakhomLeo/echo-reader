"""Weibo article adapter (ported from UP's extractor onto the SiteAdapter contract)."""

import json
import unittest

from medialab import platforms
from medialab.site_adapters import WeiboAdapter, find_adapter

STATUS = {
    "text": "今天整理了一份<b>本地优先</b>阅读工作流的笔记，欢迎讨论。<br/>第二行内容。",
    "created_at": "Tue Oct 20 10:00:00 +0800 2026",
    "user": {"screen_name": "示例作者"},
    "pics": [
        {"large": {"url": "https://wx1.sinaimg.cn/large/example1.jpg"}},
        {"url": "https://wx2.sinaimg.cn/orj360/example2.jpg"},
    ],
}

MOBILE_HTML = (
    "<html><body><script>var $render_data = "
    + json.dumps([{"status": STATUS}], ensure_ascii=False)
    + "[0] || {};</script></body></html>"
)

LONG_ARTICLE_HTML = """
<html><body>
<script>$CONFIG['onick'] = '长文作者';</script>
<div node-type="articleTitle">一篇示例头条文章</div>
<div class="authorinfo">长文作者 2026-10-01 发布</div>
<div node-type="contentBody"><p>这是一段足够长的示例正文，用来验证头条文章的提取逻辑可以正常工作。</p>
<img src="https://wx3.sinaimg.cn/large/body.jpg" alt="配图"/></div>
</body></html>
"""

DOM_HTML = """
<html><body><span class="profile-nick">DOM 作者</span>
<div class="weibo-text">只有 DOM 时的兜底内容</div></body></html>
"""


class TestWeiboAdapter(unittest.TestCase):
    def test_platform_detection_and_session_domains(self):
        for url in ("https://weibo.com/123/AbCd", "https://m.weibo.cn/status/1", "https://weibo.cn/x"):
            self.assertEqual(platforms.detect_platform(url), "weibo")
            self.assertIsInstance(find_adapter(url), WeiboAdapter)
        self.assertEqual(platforms.session_domains("weibo"), [".weibo.com", ".weibo.cn"])
        self.assertEqual(platforms.detect_platform("https://notweibo.com/x"), "generic")

    def test_mobile_render_data(self):
        art = WeiboAdapter().extract(MOBILE_HTML, "https://m.weibo.cn/status/1")
        self.assertIsNotNone(art)
        self.assertEqual(art.author, "示例作者")
        self.assertTrue(art.title.startswith("今天整理了一份本地优先"))
        self.assertIn("**本地优先**", art.content_text)
        self.assertEqual(art.date, "2026-10-20T10:00:00+08:00")
        self.assertEqual([i["src"] for i in art.images], [
            "https://wx1.sinaimg.cn/large/example1.jpg",
            "https://wx2.sinaimg.cn/orj360/example2.jpg",
        ])

    def test_long_text_preferred(self):
        status = dict(STATUS, isLongText=True, longTextContent="完整的长微博正文")
        html = "<script>var $render_data = " + json.dumps([{"status": status}], ensure_ascii=False) + "[0]</script>"
        art = WeiboAdapter().extract(html, "https://m.weibo.cn/status/2")
        self.assertIn("完整的长微博正文", art.content_text)

    def test_long_article(self):
        art = WeiboAdapter().extract(LONG_ARTICLE_HTML, "https://weibo.com/ttarticle/p/show?id=1")
        self.assertEqual(art.title, "一篇示例头条文章")
        self.assertEqual(art.author, "长文作者")
        self.assertEqual(art.date, "2026-10-01")
        self.assertEqual(art.images[0]["src"], "https://wx3.sinaimg.cn/large/body.jpg")

    def test_dom_fallback_and_empty(self):
        art = WeiboAdapter().extract(DOM_HTML, "https://weibo.com/1/2")
        self.assertEqual(art.author, "DOM 作者")
        self.assertIn("兜底内容", art.content_text)
        self.assertIsNone(WeiboAdapter().extract("<html></html>", "https://weibo.com/1/2"))

    def test_dead_page_and_referer(self):
        adapter = WeiboAdapter()
        self.assertTrue(adapter.looks_dead("微博", "该微博已被删除"))
        self.assertFalse(adapter.looks_dead("微博", "正常内容"))
        self.assertEqual(adapter.image_referer("https://m.weibo.cn/status/1"), "https://weibo.com/")


if __name__ == "__main__":
    unittest.main()
