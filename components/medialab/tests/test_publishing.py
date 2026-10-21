"""publishing 模块测试：manifest / transcript / media 编排（echo producer-guide 规范）。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from medialab.publishing import (
    build_transcript,
    derive_id,
    detect_language,
    load_manifest_id,
    pick_task_dir,
    publish_media,
    safe_title,
    write_manifest,
)


class TestDeriveId(unittest.TestCase):
    def test_video_id(self):
        self.assertEqual(derive_id("bilibili", "BV1xx"), "vid-bilibili-BV1xx")

    def test_same_video_same_id(self):
        self.assertEqual(derive_id("youtube", "abc123"),
                         derive_id("youtube", "abc123"))

    def test_article_hash_stable_per_url(self):
        a = derive_id("zhihu", url="https://zhihu.com/question/1")
        b = derive_id("zhihu", url="https://zhihu.com/question/1")
        c = derive_id("zhihu", url="https://zhihu.com/question/2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("art-zhihu-"))

    def test_platform_sanitized(self):
        self.assertEqual(derive_id("mp.weixin.qq.com", url="u").split("-")[1], "mpweixinqqcom")


class TestSafeTitle(unittest.TestCase):
    def test_invalid_chars(self):
        self.assertEqual(safe_title('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_empty_fallback(self):
        self.assertEqual(safe_title("  "), "untitled")


class TestPickTaskDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.delivery = Path(self.tmp.name)
        self.delivery.mkdir(exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_manifest(self, folder: Path, mid: str):
        folder.mkdir(exist_ok=True)
        (folder / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "id": mid}), encoding="utf-8")

    def test_new_dir(self):
        self.assertEqual(pick_task_dir(self.delivery, "标题", "t1", "id-1"),
                         self.delivery / "标题")

    def test_same_id_merges(self):
        self._write_manifest(self.delivery / "标题", "id-1")
        self.assertEqual(pick_task_dir(self.delivery, "标题", "t1", "id-1"),
                         self.delivery / "标题")

    def test_same_id_is_reused_when_extracted_title_changes(self):
        self._write_manifest(self.delivery / "旧标题", "id-1")
        self.assertEqual(pick_task_dir(self.delivery, "新标题", "t1", "id-1"),
                         self.delivery / "旧标题")

    def test_different_id_suffix(self):
        self._write_manifest(self.delivery / "标题", "id-1")
        self.assertEqual(pick_task_dir(self.delivery, "标题", "t1", "id-2"),
                         self.delivery / "标题-2")

    def test_no_manifest_suffix(self):
        (self.delivery / "标题").mkdir()
        self.assertEqual(pick_task_dir(self.delivery, "标题", "t1", "id-2"),
                         self.delivery / "标题-2")

    def test_suffix_increments(self):
        self._write_manifest(self.delivery / "标题", "id-1")
        self._write_manifest(self.delivery / "标题-2", "id-2")
        self.assertEqual(pick_task_dir(self.delivery, "标题", "t1", "id-3"),
                         self.delivery / "标题-3")


class TestWriteManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self.tmp.name)
        self.task_dir.mkdir(exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_basic_manifest(self):
        p = write_manifest(self.task_dir, manifest_id="vid-b-BV1", source_type="video",
                           source_url="https://x.com/v", source_author="UP主",
                           source_published_at="20260801")
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["id"], "vid-b-BV1")
        self.assertEqual(data["source"]["type"], "video")
        self.assertEqual(data["source"]["author"], "UP主")
        self.assertEqual(data["source"]["published_at"], "2026-08-01T00:00:00+08:00")
        self.assertIsNone(data["primary_media"])
        self.assertEqual(data["article"]["path"], "article.md")
        self.assertEqual(data["transcript"]["path"], "transcript.json")

    def test_idempotent_created_at(self):
        p = write_manifest(self.task_dir, manifest_id="id-x", source_type="audio")
        first = json.loads(p.read_text(encoding="utf-8"))
        p = write_manifest(self.task_dir, manifest_id="id-x", source_type="audio",
                           transcript_language="en")
        second = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertEqual(second["transcript"]["language"], "en")

    def test_primary_media_preserved_on_update(self):
        from medialab.publishing import MediaEntry

        media = MediaEntry(kind="audio", original_path="media/original.m4a",
                           playback_path="media/playback.m4a", mime_type="audio/mp4", title="t")
        write_manifest(self.task_dir, manifest_id="id-x", source_type="audio", media=media)
        write_manifest(self.task_dir, manifest_id="id-x", source_type="audio",
                       transcript_language="zh-CN")
        data = json.loads((self.task_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["primary_media"]["playback_path"], "media/playback.m4a")

    def test_article_only_source(self):
        p = write_manifest(self.task_dir, manifest_id="art-x", source_type="article",
                           source_url="https://a.com/p", source_title="原标题",
                           source_author="作者", source_published_at="2026-01-02")
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["source"]["title"], "原标题")
        self.assertIsNone(data["primary_media"])


class TestBuildTranscript(unittest.TestCase):
    def test_segments_converted(self):
        segs = [
            {"text": "你好", "start": 0.0, "end": 4.28, "speaker_id": 0},
            {"text": "世界", "start": 5.0, "end": 9.12, "speaker_id": None},
        ]
        t = build_transcript(segs, "zh-CN", "说话人")
        self.assertEqual(t["schema_version"], 1)
        self.assertEqual(t["language"], "zh-CN")
        self.assertEqual(t["media_id"], "primary")
        self.assertEqual(t["segments"][0]["id"], "s0001")
        self.assertEqual(t["segments"][0]["start_ms"], 0)
        self.assertEqual(t["segments"][0]["end_ms"], 4280)
        self.assertEqual(t["segments"][0]["speaker"], "说话人0")
        self.assertEqual(t["segments"][1]["speaker"], "未知说话人")
        self.assertEqual(t["segments"][1]["start_ms"], 5000)

    def test_end_ms_forced_after_start(self):
        t = build_transcript([{"text": "x", "start": 1.5, "end": 1.5}], "zh-CN")
        seg = t["segments"][0]
        self.assertGreater(seg["end_ms"], seg["start_ms"])

    def test_ascending_starts(self):
        t = build_transcript([{"text": "a", "start": 3, "end": 4}, {"text": "b", "start": 1, "end": 2}], "zh-CN")
        starts = [s["start_ms"] for s in t["segments"]]
        self.assertEqual(starts, sorted(starts))

    def test_detect_language(self):
        self.assertEqual(detect_language([{"text": "今天天气很好"}]), "zh-CN")
        self.assertEqual(detect_language([{"text": "hello world test"}]), "en")
        self.assertEqual(detect_language([{"text": "今日はいい天気です"}]), "ja")
        self.assertEqual(detect_language([{"text": "오늘 날씨가 좋습니다"}]), "ko")
        self.assertEqual(detect_language([{"text": ""}]), "und")


class TestPublishMedia(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self.tmp.name)
        self.task_dir.mkdir(exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_audio_mp3_copied_as_playback(self):
        src = self.task_dir / "a.mp3"
        src.write_bytes(b"mp3data")
        entry = publish_media(self.task_dir, [src], "", "音频")
        self.assertEqual(entry.kind, "audio")
        self.assertEqual(entry.mime_type, "audio/mpeg")
        self.assertEqual(entry.original_path, "media/original.mp3")
        self.assertEqual(entry.playback_path, "media/playback.mp3")
        self.assertTrue((self.task_dir / "media" / "original.mp3").is_file())
        self.assertTrue((self.task_dir / "media" / "playback.mp3").is_file())
        self.assertFalse(src.exists())

    def test_video_mp4_h264_copied(self):
        src = self.task_dir / "a.mp4"
        src.write_bytes(b"mp4data")
        probe = {"video_codec": "h264", "audio_codec": "aac"}
        with mock.patch("medialab.publishing._ffprobe", return_value=probe):
            entry = publish_media(self.task_dir, [src], "", "视频")
        self.assertEqual(entry.kind, "video")
        self.assertEqual(entry.mime_type, "video/mp4")
        self.assertTrue((self.task_dir / "media" / "original.mp4").is_file())
        self.assertTrue((self.task_dir / "media" / "playback.mp4").is_file())

    def test_video_needs_transcode(self):
        src = self.task_dir / "a.mkv"
        src.write_bytes(b"mkvdata")
        probe = {"video_codec": "hevc", "audio_codec": "aac"}
        with mock.patch("medialab.publishing._ffprobe", return_value=probe), \
             mock.patch("medialab.publishing._transcode_media") as tr:
            entry = publish_media(self.task_dir, [src], "ffmpeg.exe", "视频")
        tr.assert_called_once()
        self.assertEqual(entry.kind, "video")
        self.assertEqual(entry.playback_path, "media/playback.mp4")
        self.assertTrue((self.task_dir / "media" / "original.mkv").is_file())

    def test_no_files_returns_none(self):
        self.assertIsNone(publish_media(self.task_dir, [], "", ""))

    def test_extra_audio_kept_in_media(self):
        src = self.task_dir / "a.mp4"
        extra = self.task_dir / "a.mp3"
        src.write_bytes(b"mp4data")
        extra.write_bytes(b"audiodata")
        probe = {"video_codec": "h264", "audio_codec": "aac"}
        with mock.patch("medialab.publishing._ffprobe", return_value=probe):
            entry = publish_media(self.task_dir, [src, extra], "", "视频")
        self.assertEqual(entry.kind, "video")
        self.assertTrue((self.task_dir / "media" / "a.mp3").is_file())


class TestLoadManifestId(unittest.TestCase):
    def test_reads_id(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "manifest.json").write_text('{"id": "vid-x-1"}', encoding="utf-8")
            self.assertEqual(load_manifest_id(d), "vid-x-1")

    def test_missing_or_bad(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self.assertIsNone(load_manifest_id(d))
            (d / "manifest.json").write_text("not json", encoding="utf-8")
            self.assertIsNone(load_manifest_id(d))


if __name__ == "__main__":
    unittest.main()


class TestH264EncoderSelection(unittest.TestCase):
    """不带 libx264 的 FFmpeg 构建（如 BtbN "lgpl"），转码必须能退到非 GPL 的编码器。"""

    LGPL_ENCODERS = (
        "Encoders:\n"
        " V....D libopenh264          OpenH264 H.264 / AVC (codec h264)\n"
        " V....D h264_mf              H264 via MediaFoundation (codec h264)\n"
        " A....D aac                  AAC (Advanced Audio Coding)\n"
    )
    GPL_ENCODERS = " V....D libx264              libx264 H.264 (codec h264)\n" + LGPL_ENCODERS

    def setUp(self):
        from medialab import publishing
        publishing._available_encoders.cache_clear()
        self.addCleanup(publishing._available_encoders.cache_clear)
        self.publishing = publishing

    def _args(self, listing):
        done = mock.Mock(stdout=listing, returncode=0)
        with mock.patch("medialab.publishing.subprocess.run", return_value=done):
            return self.publishing._h264_encoder_args("ffmpeg-test")

    def test_lgpl_build_uses_openh264(self):
        args = self._args(self.LGPL_ENCODERS)
        self.assertEqual(args[:2], ["-c:v", "libopenh264"])
        self.assertNotIn("libx264", args)

    def test_user_ffmpeg_with_x264_prefers_it(self):
        self.assertEqual(self._args(self.GPL_ENCODERS)[:2], ["-c:v", "libx264"])

    def test_audio_encoder_names_are_not_mistaken_for_video(self):
        with self.assertRaisesRegex(RuntimeError, "H.264"):
            self._args(" A....D aac  AAC\n A....D libopenh264x fake\n")

    def test_transcode_command_uses_selected_encoder(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(stdout=self.LGPL_ENCODERS, stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("medialab.publishing.subprocess.run", side_effect=fake_run):
            src, dst = Path(tmp) / "in.mkv", Path(tmp) / "out.mp4"
            self.publishing._transcode_media(src, dst, "ffmpeg-test", {"video_codec": "hevc"})
        encode = calls[-1]
        self.assertIn("libopenh264", encode)
        self.assertNotIn("libx264", encode)
        self.assertIn("aac", encode)
