import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from medialab.article import ArticleExtractor, ScrapeError
from medialab.browser_http import BrowserHttpError
from medialab.site_adapters import (
    Article,
    GenericTrafilaturaAdapter,
    ToutiaoAdapter,
    WechatAdapter,
    XAdapter,
    XhsAdapter,
    ZhihuAdapter,
    find_adapter,
    html_to_markdown,
)
from medialab.config import load_config
from medialab.session import FailureCode

CFG = load_config()

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

WECHAT_HTML = """
<html><head><title>w</title></head><body>
<h1 id="activity-name">微信公众号文章标题</h1>
<span id="js_name">公众号作者</span>
<em id="publish_time">2024-01-01</em>
<div id="js_content">
<p>这是第一段正文内容。这篇文章的正文足够长，可以用于验证微信适配器的提取逻辑是否正常工作。继续补充一些文字来确保长度阈值通过。</p>
<img src="https://mmbiz.qpic.cn/mmbiz_jpg/abc/1.jpg" alt="配图"/>
<p>这是第二段正文，包含更多说明文字，同样用于填充长度。</p>
</div>
</body></html>
"""

ZHIHU_HTML = """
<html><head><title>z</title></head><body>
<script id="js-initialData" type="application/json">
{"initialState": {"entities": {"articles": {"123": {
  "title": "知乎文章标题",
  "content": "<p>知乎正文第一段，内容足够长以满足提取阈值。这一段描述了某个话题的背景与细节，涉及多个方面的讨论与示例说明，继续补充文字以保证总长度超过两百个字符的判定标准。这里加入更多背景信息与上下文描述，让段落内容更完整。</p><p>知乎正文第二段，继续展开叙述，包含更多背景信息与后续进展的说明文字，同时补充一些示例与细节，确保整篇正文的长度稳定超过提取器的最小要求。最后一段补充结论性的说明文字，让整体内容长度更加充裕，避免因恰好卡在阈值边缘而导致测试不稳定。</p>",
  "author": {"name": "知乎作者"},
  "createdTime": "2024-06-01"
}}}}, "action": ""}
</script>
</body></html>
"""

TOUTIAO_HTML = """
<html><head><title>t</title></head><body>
<script>window._SSR_HYDRATED_DATA = {"articleInfo": {
  "title": "今日头条文章标题",
  "content": "<p>头条正文第一段，内容足够长以满足提取阈值，介绍事件背景与相关细节，包含多条补充说明文字以确保正文长度达标。这里继续补充一些事件脉络的描述，让内容更加完整。</p><p>头条正文第二段，补充细节与后续进展，继续展开叙述让内容更完整，同时加入更多说明文字以覆盖提取器的最小长度要求。</p>",
  "author": {"name": "头条作者"},
  "publishTime": "2024-07-01"
}}</script>
</body></html>
"""

X_OEMBED_JSON = json.dumps({
    "author_name": "SomeUser",
    "html": '<blockquote class="twitter-tweet"><p lang="zh" dir="ltr">这是一条推文内容，用于测试 X 适配器。</p>&mdash; SomeUser (@handle)</blockquote>',
    "url": "https://x.com/u/status/1",
})

X_META_HTML = """
<html><head>
<meta name="description" content="推文描述文本，通过页面 meta 提取。">
<meta property="og:title" content="推文标题">
<meta property="og:image" content="https://pbs.twimg.com/media/abc.jpg">
</head><body></body></html>
"""

XHS_HTML = """
<html><head><title>xhs</title></head><body>
<script>window.__INITIAL_STATE__ = {"note": {"noteDetailMap": {"abc": {"note": {
  "title": "小红书笔记标题",
  "desc": "小红书笔记描述文字",
  "imageList": [{"urlDefault": "http://sns-webpic-qc.xhscdn.com/1.jpg?xsec_token=x"}],
  "user": {"nickname": "小红"}
}}}}}}</script>
</body></html>
"""

GENERIC_HTML = """
<html><head><title>Generic Article</title>
<meta property="og:title" content="Generic Article Title">
</head><body>
<article>
<h1>Generic Article Title</h1>
<p>这是通用文章的正文内容。trafilatura 应该能够从这个简单的 HTML 结构中提取出标题和正文。继续增加文字长度以满足提取器的最小输出要求，确保测试稳定。这里再补充一些说明性文字，让正文总长度明显超过两百个字符的判定阈值。</p>
<p>第二段内容继续展开，讲述更多背景信息，让整个页面更像一篇真实文章。段落中同样包含一定长度的中文文本用于验证提取效果，同时保留英文单词用于混合语言场景的覆盖。</p>
<p>第三段补充结尾信息，说明测试数据构造方式，确保 trafilatura 在 favor_recall 模式下能够稳定返回完整正文而不是因为页面过小被过滤掉。</p>
</article>
</body></html>
"""


# ---------------------------------------------------------------------------
# 适配器单元测试
# ---------------------------------------------------------------------------

class TestAdapters(unittest.TestCase):
    def test_wechat(self):
        art = WechatAdapter().extract(WECHAT_HTML, "https://mp.weixin.qq.com/s/abc")
        self.assertIsNotNone(art)
        self.assertEqual(art.title, "微信公众号文章标题")
        self.assertEqual(art.author, "公众号作者")
        self.assertEqual(art.site_name, "微信公众号")
        self.assertTrue(len(art.content_text) > 50)
        self.assertEqual(len(art.images), 1)
        self.assertEqual(art.images[0]["src"], "https://mmbiz.qpic.cn/mmbiz_jpg/abc/1.jpg")

    def test_wechat_shell_rejected(self):
        shell = "<html><body><h1 id='activity-name'>x</h1><div id='js_content'><p>短</p></div></body></html>"
        self.assertIsNone(WechatAdapter().extract(shell, "https://mp.weixin.qq.com/s/x"))

    def test_zhihu_hydration(self):
        art = ZhihuAdapter().extract(ZHIHU_HTML, "https://zhuanlan.zhihu.com/p/123")
        self.assertIsNotNone(art)
        self.assertEqual(art.title, "知乎文章标题")
        self.assertEqual(art.author, "知乎作者")
        self.assertIn("知乎正文第一段", art.content_text)

    def test_toutiao_hydrated(self):
        art = ToutiaoAdapter().extract(TOUTIAO_HTML, "https://www.toutiao.com/article/123/")
        self.assertIsNotNone(art)
        self.assertEqual(art.title, "今日头条文章标题")
        self.assertEqual(art.author, "头条作者")
        self.assertIn("头条正文第一段", art.content_text)

    def test_x_oembed(self):
        art = XAdapter().extract(X_OEMBED_JSON, "https://x.com/u/status/1")
        self.assertIsNotNone(art)
        self.assertEqual(art.author, "SomeUser")
        self.assertIn("这是一条推文内容", art.content_text)

    def test_x_meta_fallback(self):
        art = XAdapter().extract(X_META_HTML, "https://x.com/u/status/1")
        self.assertIsNotNone(art)
        self.assertIn("推文描述文本", art.content_text)
        self.assertEqual(len(art.images), 1)

    def test_xhs_initial_state(self):
        art = XhsAdapter().extract(XHS_HTML, "https://www.xiaohongshu.com/explore/abc")
        self.assertIsNotNone(art)
        self.assertEqual(art.title, "小红书笔记标题")
        self.assertEqual(art.author, "小红")
        self.assertEqual(len(art.images), 1)
        self.assertTrue(art.images[0]["src"].startswith("https://sns-webpic"))

    def test_xhs_protocol_relative_image_is_upgraded_to_https(self):
        images = XhsAdapter._image_urls({
            "imageList": [{
                "infoList": [{"imageScene": "WB_FHD", "url": "//sns-webpic-qc.xhscdn.com/2"}],
            }],
        })
        self.assertEqual(images, [{"src": "https://sns-webpic-qc.xhscdn.com/2", "alt": ""}])

    def test_generic_trafilatura(self):
        art = GenericTrafilaturaAdapter().extract(GENERIC_HTML, "https://example.com/post")
        self.assertIsNotNone(art)
        self.assertIn("Generic Article", art.title)
        self.assertTrue(len(art.content_text) > 100)

    def test_find_adapter(self):
        self.assertEqual(find_adapter("https://mp.weixin.qq.com/s/x").platform, "wechat")
        self.assertEqual(find_adapter("https://www.zhihu.com/question/1").platform, "zhihu")
        self.assertEqual(find_adapter("https://x.com/u/status/1").platform, "x")
        self.assertEqual(find_adapter("https://www.xiaohongshu.com/explore/a").platform, "xiaohongshu")
        self.assertEqual(find_adapter("https://unknown.example.com/p").platform, "generic_article")

    def test_html_to_markdown(self):
        md = html_to_markdown("<h1>标题</h1><p>正文 <strong>加粗</strong> 和 <a href='https://x.com'>链接</a></p><img src='https://x.com/a.jpg' alt='图'/>")
        self.assertIn("# 标题", md)
        self.assertIn("**加粗**", md)
        self.assertIn("[链接](https://x.com)", md)
        self.assertIn("![图](https://x.com/a.jpg)", md)


# ---------------------------------------------------------------------------
# 统一 curl_cffi 会话
# ---------------------------------------------------------------------------


def page_client(html=GENERIC_HTML, *, final_url="https://example.com/post"):
    client = mock.Mock()
    client.credential_domains = ()
    client.fetch_page.return_value = {
        "html": html,
        "final_url": final_url,
        "status": 200,
        "content_type": "text/html",
    }
    return client


class TestExtractorSession(unittest.TestCase):
    def test_python_chrome146_page_success_is_one_call(self):
        client = page_client()
        ext = ArticleExtractor(CFG, http_client=client)
        with tempfile.TemporaryDirectory() as td:
            res = ext.scrape("https://example.com/post", "job1", Path(td))
            self.assertEqual(res.article.capture_mode, "curl_cffi")
            self.assertTrue(res.article_md.exists())
        client.fetch_page.assert_called_once_with(
            "https://example.com/post", allowed_domains=(),
        )

    def test_empty_python_page_fails_without_second_request(self):
        client = page_client("<html><body><div id='root'></div></body></html>")
        ext = ArticleExtractor(CFG, http_client=client)
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ScrapeError) as ctx:
            ext.scrape("https://example.com/post", "job1", Path(td))
        self.assertEqual(ctx.exception.code, FailureCode.CONTENT_NOT_FOUND)
        client.fetch_page.assert_called_once()

    def test_page_failure_preserves_code_and_never_retries(self):
        for code in (
            FailureCode.NETWORK,
            FailureCode.AUTH_REQUIRED,
            FailureCode.RATE_LIMITED,
            FailureCode.CAPTCHA_REQUIRED,
        ):
            with self.subTest(code=code):
                client = page_client()
                client.fetch_page.side_effect = BrowserHttpError(code, "blocked")
                with tempfile.TemporaryDirectory() as td, self.assertRaises(ScrapeError) as ctx:
                    ArticleExtractor(
                        CFG, http_client=client,
                    ).scrape("https://example.com/post", "job1", Path(td))
                self.assertEqual(ctx.exception.code, code)
                client.fetch_page.assert_called_once()

    def test_missing_python_session_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ScrapeError) as ctx:
            ArticleExtractor(CFG).scrape(
                "https://example.com/post", "job1", Path(td),
            )
        self.assertEqual(ctx.exception.code, FailureCode.NETWORK)


# ---------------------------------------------------------------------------
# 资源与产物
# ---------------------------------------------------------------------------


def image_client(*, fail_url=None, failure_code=FailureCode.NETWORK):
    client = page_client()
    calls = []

    def download(url, path, referer, max_bytes):
        calls.append((url, path.name, referer))
        if url == fail_url:
            raise BrowserHttpError(failure_code, "safe failure")
        path.write_bytes(b"x" * 2048)
        return path

    client.download_image.side_effect = download
    client.calls = calls
    return client


class TestFinalize(unittest.TestCase):
    def test_image_download_rewrite_and_article_idempotence(self):
        art = Article(
            title="测试文章",
            url="https://example.com/post",
            site_name="测试站",
            content_text="![配图](https://example.com/a.jpg)",
            content_html="<img src='https://example.com/a.jpg'/>",
            images=[{"src": "https://example.com/a.jpg", "alt": "配图"}],
        )
        client = image_client()
        ext = ArticleExtractor(CFG, http_client=client)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            res = ext._finalize(
                art, WechatAdapter(), art.url, "job1", root, None,
            )
            self.assertEqual(res.images[0]["local"], "images/0000.jpg")
            self.assertIn("images/0000.jpg", res.article_md.read_text(encoding="utf-8"))
            res.article_md.write_text("用户已修改的正文", encoding="utf-8")
            ext._finalize(art, WechatAdapter(), art.url, "job2", root, None)
            self.assertEqual(res.article_md.read_text(encoding="utf-8"), "用户已修改的正文")
        self.assertEqual(len(client.calls), 2)

    def test_generic_image_failure_is_nonfatal_and_not_retried(self):
        source = "https://example.com/a.jpg"
        art = Article(
            title="测试文章", url="https://example.com/post", content_text="正文",
            images=[{"src": source, "alt": ""}],
        )
        client = image_client(fail_url=source)
        with tempfile.TemporaryDirectory() as td:
            result = ArticleExtractor(CFG, http_client=client)._finalize(
                art, WechatAdapter(), art.url, "job", Path(td), None,
            )
        self.assertEqual(result.images, [])
        self.assertEqual(len(client.calls), 1)

    def test_xhs_capture_uses_same_client_and_each_unique_image_once(self):
        client = image_client()
        ext = ArticleExtractor(CFG, http_client=client)
        with tempfile.TemporaryDirectory() as td:
            result = ext.scrape_captured(
                "https://www.xiaohongshu.com/explore/abc",
                "job1",
                Path(td),
                html=XHS_HTML,
                final_url="https://www.xiaohongshu.com/explore/abc?xsec_token=t",
            )
            markdown = result.article_md.read_text(encoding="utf-8")
        self.assertEqual(result.article.capture_mode, "curl_cffi")
        self.assertEqual(len(client.calls), 1)
        self.assertIn("![图片 1](images/0000.jpg)", markdown)

    def test_xhs_any_image_failure_fails_whole_task_without_retry(self):
        html = """
        <script>window.__INITIAL_STATE__ = {"note":{"noteDetailMap":{"abc":{"note":{
          "title":"完整图文","desc":"正文","imageList":[
            {"urlDefault":"https://img.xhscdn.com/one.jpg"},
            {"urlDefault":"https://img.xhscdn.com/two.jpg"},
            {"urlDefault":"https://img.xhscdn.com/one.jpg"}
          ]}}}}};</script>
        """
        client = image_client(fail_url="https://img.xhscdn.com/two.jpg")
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ScrapeError) as ctx:
            ArticleExtractor(CFG, http_client=client).scrape_captured(
                "https://www.xiaohongshu.com/explore/abc",
                "job1",
                Path(td),
                html=html,
            )
        self.assertEqual(ctx.exception.code, FailureCode.NETWORK)
        self.assertEqual(
            [call[0] for call in client.calls],
            ["https://img.xhscdn.com/one.jpg", "https://img.xhscdn.com/two.jpg"],
        )

    def test_xhs_auth_error_code_is_preserved_without_secret_leak(self):
        source = "https://sns-webpic-qc.xhscdn.com/1.jpg?xsec_token=x"
        client = image_client(fail_url=source, failure_code=FailureCode.AUTH_REQUIRED)
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ScrapeError) as ctx:
            ArticleExtractor(CFG, http_client=client).scrape_captured(
                "https://www.xiaohongshu.com/explore/abc",
                "job1",
                Path(td),
                html=XHS_HTML,
            )
        self.assertEqual(ctx.exception.code, FailureCode.AUTH_REQUIRED)
        self.assertNotIn("xsec_token", str(ctx.exception))

    def test_xhs_generic_entry_and_bad_capture_never_refetch(self):
        client = image_client()
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ScrapeError):
            ArticleExtractor(CFG, http_client=client).scrape(
                "https://www.xiaohongshu.com/explore/abc", "job1", Path(td),
            )
        client.fetch_page.assert_not_called()

        with tempfile.TemporaryDirectory() as td, self.assertRaises(ScrapeError):
            ArticleExtractor(CFG, http_client=client).scrape_captured(
                "https://www.xiaohongshu.com/explore/abc",
                "job2",
                Path(td),
                html="<html>bad capture</html>",
            )
        client.fetch_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
