import unittest
from pathlib import Path

from medialab.asr import NoSpeechError, Segment, _dedupe_and_sort
from medialab.asr import AsrPipeline


class _FakeAudio:
    def __init__(self):
        self.calls: list[tuple[float, float]] = []

    def slice(self, src, start, end, dst):
        self.calls.append((start, end))

    def speed(self, src, factor, dst):
        raise AssertionError("speed 不应被调用")


class TestSliceAudio(unittest.TestCase):
    def test_skip_short_tail_piece(self):
        """尾部剩余 <1s 的碎片应跳过（如 300.0107s 时长 -> 5 片而非 6 片）。"""
        p = object.__new__(AsrPipeline)
        p.slice_seconds = 60.0
        p.overlap = 1.0
        p.speed_factor = 1.0
        fake = _FakeAudio()
        p.audio = fake
        p._duration_seconds = lambda path: 300.0107
        pieces = p.slice_audio(Path("x.m4a"), Path("work"))
        self.assertEqual(len(pieces), 5)
        self.assertEqual(pieces[-1]["offset"], 240.0)
        self.assertEqual(fake.calls[-1], (240.0, 300.0107))

    def test_speed_factor_calls_speed(self):
        p = object.__new__(AsrPipeline)
        p.slice_seconds = 60.0
        p.overlap = 1.0
        p.speed_factor = 1.5
        fake = _FakeAudio()
        fake.speed = lambda src, factor, dst: None
        p.audio = fake
        p._duration_seconds = lambda path: 60.9  # 尾部 0.9s < 1s，应跳过
        pieces = p.slice_audio(Path("x.m4a"), Path("work"))
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0]["speed"], 1.5)


class TestAsrParsing(unittest.TestCase):
    def test_extract_single_slice(self):
        raw = {"transcripts": [{"sentences": [
            {"begin_time": 980, "end_time": 1820, "text": "hello"},
            {"begin_time": 1820, "end_time": 2600, "text": "world"},
        ]}]}
        pipeline = object.__new__(AsrPipeline)
        segs = AsrPipeline._extract_segments_one(pipeline, raw, {"offset": 60.0, "speed": 1.5})
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0].start, 60.0 + 0.98 / 1.5)
        self.assertAlmostEqual(segs[0].end, 60.0 + 1.82 / 1.5)
        self.assertEqual(segs[1].text, "world")

    def test_dedupe_overlap(self):
        segs = [
            Segment("a", 10.0, 12.0),
            Segment("a", 11.5, 13.0),  # 重叠片重复文本
            Segment("b", 13.0, 15.0),
        ]
        out = _dedupe_and_sort(segs)
        self.assertEqual([s.text for s in out], ["a", "b"])
        self.assertAlmostEqual(out[0].end, 13.0)  # 合并延长到最大 end


class TestNoSpeechSkip(unittest.TestCase):
    def test_nospeech_code_detected(self):
        # 直接验证检测逻辑：构造 provider 返回 FAILED + 无语音 code
        from medialab import asr as asr_mod

        class FakeProvider:
            def poll(self, tid):
                return "FAILED", {"output": {"code": "SUCCESS_WITH_NO_VALID_FRAGMENT", "message": "SUCCESS_WITH_NO_VALID_FRAGMENT"}}

        p = object.__new__(AsrPipeline)
        p.cfg = {"asr": {"poll_interval": 0.01, "poll_max_interval": 0.01, "max_wait_seconds": 5}}
        p.provider = FakeProvider()
        with self.assertRaises(NoSpeechError):
            p._poll_until_done("t")

    def test_submit_one_reraises_nospeech_without_retry(self):
        class FakeProvider:
            def __init__(self):
                self.submit_calls = 0

            def submit(self, urls):
                self.submit_calls += 1
                return "t"

            def poll(self, tid):
                return "FAILED", {"output": {"code": "SUCCESS_WITH_NO_VALID_FRAGMENT", "message": "x"}}

        p = object.__new__(AsrPipeline)
        p.cfg = {"asr": {"poll_interval": 0.01, "poll_max_interval": 0.01, "max_wait_seconds": 5}}
        p.provider = FakeProvider()
        with self.assertRaises(NoSpeechError):
            p._submit_one(["u"], max_retries=3)
        self.assertEqual(p.provider.submit_calls, 1)  # 无语音不重试


if __name__ == "__main__":
    unittest.main()
