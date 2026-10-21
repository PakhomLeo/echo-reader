import unittest

from medialab.platforms import allowed_extractors, detect_platform, session_domains


class TestPlatforms(unittest.TestCase):
    def test_detect(self):
        cases = {
            "https://www.bilibili.com/video/BV1A0000000A/": "bilibili",
            "https://b23.tv/abc": "bilibili",
            "https://v.douyin.com/AbCdEf12345/": "douyin",
            "https://www.douyin.com/video/123": "douyin",
            "https://youtu.be/aaaaaaaaaaa": "youtube",
            "https://www.youtube.com/watch?v=abc": "youtube",
            "https://x.com/user/status/1": "x",
            "https://www.tiktok.com/@user/video/1": "tiktok",
            "https://vimeo.com/123": "vimeo",
            "https://www.twitch.tv/videos/123": "twitch",
            "https://www.instagram.com/reel/abc/": "instagram",
            "https://fb.watch/abc/": "facebook",
            "https://mp.weixin.qq.com/s/abc": "wechat",
            "https://zhuanlan.zhihu.com/p/123": "zhihu",
            "https://www.toutiao.com/article/123": "toutiao",
            "https://medium.com/@user/post": "medium",
            "https://juejin.cn/post/1": "juejin",
            "https://sspai.com/post/1": "sspai",
            "https://www.36kr.com/p/1": "36kr",
            "https://www.cnblogs.com/user/p/1.html": "cnblogs",
            "https://www.infoq.cn/article/1": "infoq",
            "https://www.jianshu.com/p/1": "jianshu",
            "https://example.com/video.mp4": "generic",
        }
        for url, expected in cases.items():
            self.assertEqual(detect_platform(url), expected, url)

    def test_allowed_filters_default(self):
        cfg = {"platforms": {"enabled": ["bilibili.*", "twitch.*", "generic"]}}
        self.assertEqual(allowed_extractors(cfg), ["bilibili.*", "twitch.*", "generic"])

    def test_known_and_generic_session_domains(self):
        self.assertIn(".tiktok.com", session_domains("tiktok"))
        self.assertEqual(session_domains("medium"), [".medium.com"])
        self.assertEqual(
            session_domains("generic", url="https://media.example.com/video"),
            [".media.example.com"],
        )

    def test_lookalike_hosts_not_misdetected(self):
        """子串相似域名不得误识别（如 notyoutube.com / douyin.com.evil.example）。"""
        cases = {
            "https://notyoutube.com/watch?v=x": "generic",
            "https://douyin.com.evil.example/video/1": "generic",
            "https://bilibili.com.evil.example/video/1": "generic",
            "https://youtube.comm/x": "generic",
            "https://www.bilibili.com/video/BV1xx": "bilibili",
            "https://m.douyin.com/video/1": "douyin",
        }
        for url, expected in cases.items():
            self.assertEqual(detect_platform(url), expected, url)


if __name__ == "__main__":
    unittest.main()
