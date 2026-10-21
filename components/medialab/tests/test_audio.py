import unittest
from pathlib import Path

from medialab.audio import AudioProcessor


class _FakeAudio(AudioProcessor):
    """替换 ffmpeg 调用，记录命令并模拟产物文件。"""

    def __init__(self, write_size: int | None = 1024):
        self.ffmpeg_path = "ffmpeg"
        self.calls: list[list[str]] = []
        self.write_size = write_size

    def _run(self, args: list[str]) -> None:
        self.calls.append(args)
        dst = args[-1]
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        if self.write_size is not None:
            Path(dst).write_bytes(b"\x00" * self.write_size)


class TestSpeed(unittest.TestCase):
    def tearDown(self):
        for p in Path("tmp").glob("speed_test_*"):
            p.unlink(missing_ok=True)

    def test_factor_3_cascades(self):
        a = _FakeAudio()
        dst = Path("tmp/speed_test_3.m4a")
        a.speed(Path("src.m4a"), 3.0, dst)
        self.assertEqual(len(a.calls), 1)
        args = a.calls[0]
        self.assertIn("atempo=2.0000,atempo=1.5000", args)
        self.assertTrue(dst.exists())

    def test_factor_2_single_filter(self):
        a = _FakeAudio()
        a.speed(Path("src.m4a"), 2.0, Path("tmp/speed_test_2.m4a"))
        self.assertIn("atempo=2.0000", a.calls[0])

    def test_factor_out_of_range_raises(self):
        a = _FakeAudio()
        with self.assertRaises(ValueError):
            a.speed(Path("src.m4a"), 0.4, Path("tmp/speed_test_x.m4a"))
        with self.assertRaises(ValueError):
            a.speed(Path("src.m4a"), 17.0, Path("tmp/speed_test_x.m4a"))


class TestSlice(unittest.TestCase):
    def tearDown(self):
        for p in Path("tmp").glob("slice_test_*"):
            p.unlink(missing_ok=True)

    def test_normal_copy_slice(self):
        a = _FakeAudio()
        dst = Path("tmp/slice_test_ok.m4a")
        a.slice(Path("src.m4a"), 10.0, 70.0, dst)
        self.assertEqual(len(a.calls), 1)
        self.assertIn("-c", a.calls[0])
        self.assertIn("copy", a.calls[0])

    def test_empty_output_falls_back_to_reencode(self):
        a = _FakeAudio(write_size=100)  # 第一次写出的文件 <1024B，视为空
        dst = Path("tmp/slice_test_fb.m4a")
        a.slice(Path("src.m4a"), 10.0, 70.0, dst)
        self.assertEqual(len(a.calls), 2)
        fallback = a.calls[1]
        self.assertIn("-c:a", fallback)
        self.assertIn("aac", fallback)

    def test_fallback_codec_matches_dst_ext(self):
        a = _FakeAudio(write_size=100)
        dst = Path("tmp/slice_test_fb.mp3")
        a.slice(Path("src.m4a"), 10.0, 70.0, dst)
        fallback = a.calls[1]
        self.assertIn("libmp3lame", fallback)
        self.assertNotIn("aac", fallback)


if __name__ == "__main__":
    unittest.main()
