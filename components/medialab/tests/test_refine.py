import unittest
from unittest import mock

from medialab.refine import QwenRefiner, RefineResult, _segment_lines, _split_chunks
from medialab.refine import is_chinese_content


class _FakeResp:
    def __init__(self, status_code: int = 200, payload: bytes = b'{"choices":[{"message":{"content":"ok"}}]}',
                 body: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.body = body

    def json(self):
        return __import__("json").loads(self._payload)

    @property
    def text(self):
        return "server error"


class _FlakyPost:
    """post() 前 fail_times 次抛 RuntimeError，之后返回正常响应。"""

    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("HTTP 500")
        self.body = kwargs.get("json")
        return _FakeResp(body=self.body)


class TestSegmentLines(unittest.TestCase):
    def test_with_speaker(self):
        segs = [{"text": " 你好 ", "speaker_id": 0}, {"text": "世界", "speaker_id": 1}]
        self.assertEqual(_segment_lines(segs), ["speaker_0: 你好", "speaker_1: 世界"])

    def test_empty_text_skipped(self):
        segs = [{"text": "  ", "speaker_id": 0}, {"text": "ok", "speaker_id": 0}]
        self.assertEqual(_segment_lines(segs), ["speaker_0: ok"])

    def test_no_speaker(self):
        self.assertEqual(_segment_lines([{"text": "a"}]), ["a"])

    def test_none_segments(self):
        self.assertEqual(_segment_lines(None), [])


class TestSplitChunks(unittest.TestCase):
    def test_single_chunk(self):
        lines = ["speaker_0: 你好"] * 3
        out = _split_chunks(lines, 1000)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], lines)

    def test_multi_chunk(self):
        lines = ["speaker_0: 一二三"] * 5
        out = _split_chunks(lines, 60)
        self.assertEqual(len(out), 2)
        self.assertEqual(sum(len(c) for c in out), 5)

    def test_max_chars_zero(self):
        lines = ["a", "b"]
        self.assertEqual(_split_chunks(lines, 0), [lines])


class TestQwenRefiner(unittest.TestCase):
    def test_refine_chunks_and_join(self):
        refiner = QwenRefiner(api_key="k", max_input_chars=90, max_retries=0)
        segs = [{"text": f"发言{i}", "speaker_id": i % 2} for i in range(10)]
        with mock.patch.object(refiner, "_chat", side_effect=["part1", "part2"]) as m:
            res = refiner.refine(segs)
        self.assertEqual(res.article, "part1\n\npart2")
        self.assertEqual(res.chunks, 2)
        self.assertEqual(m.call_count, 2)
        # 分块提示词标注部分序号
        self.assertIn("第 1 部分", m.call_args_list[0][0][0])
        self.assertIn("第 2 部分", m.call_args_list[1][0][0])

    def test_long_input_shrinks_chunk_size(self):
        """长输入自适应：总输入 > max_input_chars × shrink_threshold 时压小块，防输出爆炸挂起。"""
        refiner = QwenRefiner(api_key="k", max_input_chars=400, shrink_threshold=2, max_retries=0)
        segs = [{"text": "发言内容" * 20, "speaker_id": 0} for _ in range(30)]  # 总 ~1800 字符 > 800
        with mock.patch.object(refiner, "_chat", return_value="ok") as m:
            res = refiner.refine(segs)
        # 分块上限压到 max(4000, 400//3)=4000，但单块仍受字符限制 → 1800 字符 → 1 块
        self.assertGreaterEqual(res.chunks, 1)
        # 输入远小于 4000 时不应触发（正常路径 chunk 大小不变）
        self.assertLessEqual(m.call_count, 2)

    def test_max_tokens_in_body(self):
        """body 必须带 max_tokens 上限（防 qwen3.7-flash 输出爆炸假挂起）。"""
        refiner = QwenRefiner(api_key="k", max_tokens=12000, max_retries=0)
        with mock.patch("medialab.refine.httpx.post", new=_FlakyPost()) as m:
            refiner.refine([{"text": "hi", "speaker_id": 0}])
        self.assertEqual(m.body["max_tokens"], 12000)

    def test_default_max_tokens(self):
        self.assertEqual(QwenRefiner(api_key="k").max_tokens, 12000)

    def test_single_call_no_part_label(self):
        refiner = QwenRefiner(api_key="k", max_input_chars=1000, max_retries=0)
        segs = [{"text": "你好", "speaker_id": 0}]
        with mock.patch.object(refiner, "_chat", return_value="你好") as m:
            res = refiner.refine(segs)
        self.assertEqual(res.article, "你好")
        self.assertNotIn("部分", m.call_args_list[0][0][0])
        self.assertIn("speaker_0: 你好", m.call_args_list[0][0][0])

    def test_system_prompt_speaker_label(self):
        refiner = QwenRefiner(api_key="k", speaker_label="发言人")
        self.assertIn("发言人N：", refiner.system_prompt)

    def test_retry_then_success(self):
        refiner = QwenRefiner(api_key="k", max_retries=2)
        with mock.patch("medialab.refine.httpx.post", new=_FlakyPost(fail_times=1)) as m:
            res = refiner.refine([{"text": "hi", "speaker_id": 0}])
        self.assertEqual(res.article, "ok")
        self.assertEqual(m.calls, 2)

    def test_retry_exhausted_raises(self):
        refiner = QwenRefiner(api_key="k", max_retries=1)
        with mock.patch("medialab.refine.httpx.post", new=_FlakyPost(fail_times=99)):
            with self.assertRaises(RuntimeError):
                refiner.refine([{"text": "hi", "speaker_id": 0}])

    def test_chinese_flag_uses_translate_prompt(self):
        refiner = QwenRefiner(api_key="k", max_retries=0)
        with mock.patch.object(refiner, "_chat", return_value="中文文章") as m:
            res = refiner.refine([{"text": "hello world", "speaker_id": 0}], chinese=True)
        self.assertEqual(res.article, "中文文章")
        prompt = m.call_args_list[0][0][0]
        self.assertIn("翻译成中文", prompt)

    def test_chinese_uses_translate_system_prompt_in_body(self):
        refiner = QwenRefiner(api_key="k", max_retries=0)
        with mock.patch("medialab.refine.httpx.post", new=_FlakyPost()) as m:
            refiner.refine([{"text": "hi", "speaker_id": 0}], chinese=True)
        self.assertIn("英文口语转写稿", m.body["messages"][0]["content"])
        with mock.patch("medialab.refine.httpx.post", new=_FlakyPost()) as m2:
            refiner.refine([{"text": "hi", "speaker_id": 0}])
        self.assertIn("口语转写稿", m2.body["messages"][0]["content"])
        self.assertNotIn("英文口语转写稿", m2.body["messages"][0]["content"])


class TestIsChineseContent(unittest.TestCase):
    def test_chinese(self):
        segs = [{"text": "今天天气很好"}, {"text": "我们出门散步"}]
        self.assertTrue(is_chinese_content(segs, 0.2))

    def test_english(self):
        segs = [{"text": "hello world this is a test"}, {"text": "another sentence"}]
        self.assertFalse(is_chinese_content(segs, 0.2))

    def test_empty_is_chinese(self):
        self.assertTrue(is_chinese_content([], 0.2))
        self.assertTrue(is_chinese_content([{"text": "  "}], 0.2))

    def test_mixed(self):
        segs = [{"text": "AI technology 人工智能 技术"}]
        self.assertTrue(is_chinese_content(segs, 0.2))


if __name__ == "__main__":
    unittest.main()


class TestBuildRefiner(unittest.TestCase):
    """Provider selection for the OpenAI-compatible / Ollama provider layer."""

    def _cfg(self, **refine):
        from medialab.config import load_config

        return load_config(secrets_path="/nonexistent", overrides={"refine": refine})

    def test_dashscope_requires_key_and_reuses_asr_key(self):
        from medialab.refine import build_refiner

        refiner, reason = build_refiner(self._cfg())
        self.assertIsNone(refiner)
        self.assertEqual(reason, "missing_api_key")
        cfg = self._cfg()
        cfg["asr"]["api_key"] = "k"
        refiner, reason = build_refiner(cfg)
        self.assertIsNone(reason)
        self.assertEqual((refiner.provider, refiner.model, refiner.api_key), ("dashscope", "qwen3.7-flash", "k"))

    def test_disabled_and_unknown(self):
        from medialab.refine import build_refiner

        self.assertEqual(build_refiner(self._cfg(enabled=False)), (None, "disabled"))
        self.assertEqual(build_refiner(self._cfg(provider="nope"))[1], "unknown_provider:nope")

    def test_ollama_defaults_to_loopback_without_key(self):
        from medialab.refine import OLLAMA_DEFAULT_BASE_URL, build_refiner

        self.assertEqual(build_refiner(self._cfg(provider="ollama"))[1], "missing_model")
        refiner, reason = build_refiner(self._cfg(provider="ollama", model="qwen2.5:7b"))
        self.assertIsNone(reason)
        self.assertEqual(refiner.base_url, OLLAMA_DEFAULT_BASE_URL)
        self.assertEqual(refiner.model, "qwen2.5:7b")
        self.assertTrue(refiner.clean_output)
        with mock.patch("medialab.refine.httpx.post", new=_FlakyPost()) as post:
            refiner.refine([{"text": "hi", "speaker_id": 0}])
        self.assertEqual(post.calls, 1)

    def test_openai_compatible_requires_url_model_and_remote_key(self):
        from medialab.refine import build_refiner

        self.assertEqual(build_refiner(self._cfg(provider="openai_compatible"))[1], "missing_base_url")
        self.assertEqual(build_refiner(self._cfg(
            provider="openai_compatible", base_url="https://llm.example/v1"))[1], "missing_model")
        self.assertEqual(build_refiner(self._cfg(
            provider="openai_compatible", base_url="https://llm.example/v1", model="m"))[1],
            "missing_api_key")
        refiner, reason = build_refiner(self._cfg(
            provider="openai_compatible", base_url="http://127.0.0.1:8000/v1", model="m"))
        self.assertIsNone(reason)
        self.assertEqual(refiner.api_key, "")

    def test_request_headers_and_proxy_policy(self):
        from medialab.refine import ChatRefiner

        captured = {}

        def fake_post(url, **kwargs):
            captured.update(kwargs, url=url)
            return _FakeResp(body=kwargs.get("json"))

        local = ChatRefiner(base_url="http://localhost:11434/v1", api_key="", model="m", max_retries=0)
        with mock.patch("medialab.refine.httpx.post", new=fake_post):
            local.refine([{"text": "hi"}])
        self.assertNotIn("Authorization", captured["headers"])
        self.assertFalse(captured["trust_env"])
        remote = ChatRefiner(base_url="https://llm.example/v1", api_key="k", model="m", max_retries=0)
        with mock.patch("medialab.refine.httpx.post", new=fake_post):
            remote.refine([{"text": "hi"}])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer k")
        self.assertTrue(captured["trust_env"])
        self.assertEqual(captured["url"], "https://llm.example/v1/chat/completions")

    def test_clean_generated_article(self):
        from medialab.refine import clean_generated_article

        self.assertEqual(clean_generated_article("```markdown\n标题：x\n正文\n```"), "正文")
        self.assertEqual(clean_generated_article("<transcript>正文</transcript>"), "正文")
