"""xhs_video 单元测试：note 解析、码流挑选、任务级 client 委托。"""

import unittest
import tempfile
from pathlib import Path

from medialab import xhs_video
from medialab.site_adapters import XhsAdapter, parse_xhs_initial_state


def _page_html(note_json: str) -> str:
    return f"<html><body><script>window.__INITIAL_STATE__ = {note_json};</script></body></html>"


XHS_NOTE = {
    "note": {
        "noteDetailMap": {
            "6a0000000000000000000000": {
                "note": {
                    "noteId": "6a0000000000000000000000",
                    "title": "测试视频帖",
                    "type": "video",
                    "video": {
                        "media": {
                            "stream": {
                                "EF4": [{"masterUrl": "http://a.xhscdn.com/stream/4.mp4?sign=x"}],
                                "EF6": [],
                                "EF5": [{"masterUrl": "http://a.xhscdn.com/stream/5.mp4?sign=y"}],
                                "EF7": [{"masterUrl": "http://a.xhscdn.com/stream/7.mp4?sign=z"}],
                            }
                        }
                    },
                }
            }
        }
    }
}


class ParseInitialStateTest(unittest.TestCase):
    def test_extract(self):
        html = _page_html('{"note": {}}')
        data = parse_xhs_initial_state(html)
        self.assertIsInstance(data, dict)
        self.assertIn("note", data)

    def test_undefined_cleaned(self):
        html = _page_html('{"a": undefined, "b": 1}')
        data = parse_xhs_initial_state(html)
        self.assertIsNone(data.get("a"))
        self.assertEqual(data.get("b"), 1)

    def test_no_marker(self):
        self.assertIsNone(parse_xhs_initial_state("<html>nothing</html>"))

    def test_adapter_uses_module_func(self):
        html = _page_html('{"note": {}}')
        self.assertEqual(XhsAdapter._initial_state(html), parse_xhs_initial_state(html))


class NoteFromHtmlTest(unittest.TestCase):
    def test_note_extract(self):
        html = _page_html(json_dumps(XHS_NOTE))
        note = xhs_video.note_from_html(html, "https://www.xiaohongshu.com/explore/6a0000000000000000000000")
        self.assertIsNotNone(note)
        self.assertEqual(note["title"], "测试视频帖")
        self.assertEqual(note["type"], "video")

    def test_no_note(self):
        html = _page_html('{"note": {"noteDetailMap": {}}}')
        self.assertIsNone(xhs_video.note_from_html(html, "https://www.xiaohongshu.com/explore/missing"))

    def test_garbage(self):
        self.assertIsNone(xhs_video.note_from_html("<html>no state</html>", "https://www.xiaohongshu.com/explore/missing"))

    def test_selects_requested_note_in_discovery_alias_not_first_map_item(self):
        state = {"feed": {"noteDetailMap": {
            "wrong": {"note": {"noteId": "wrong", "title": "wrong"}},
            "wanted": {"note": {"noteId": "wanted", "title": "right"}},
        }}}
        html = f"<script>window.__INITIAL_SSR_STATE__={json_dumps(state)}</script>"
        note = xhs_video.note_from_html(
            html, "https://www.xiaohongshu.com/discovery/item/wanted?xsec_token=redacted",
        )
        self.assertEqual(note["title"], "right")


class PickStreamTest(unittest.TestCase):
    def test_priority(self):
        html = _page_html(json_dumps(XHS_NOTE))
        note = xhs_video.note_from_html(html, "https://www.xiaohongshu.com/explore/6a0000000000000000000000")
        codec, url = xhs_video.pick_stream(note)
        self.assertEqual(codec, "EF7")
        self.assertEqual(url, "https://a.xhscdn.com/stream/7.mp4?sign=z")

    def test_http_upgraded_to_https(self):
        html = _page_html(json_dumps(XHS_NOTE))
        note = xhs_video.note_from_html(html, "https://www.xiaohongshu.com/explore/6a0000000000000000000000")
        _, url = xhs_video.pick_stream(note)
        self.assertTrue(url.startswith("https://"))

    def test_empty_streams(self):
        note = {"video": {"media": {"stream": {}}}}
        self.assertIsNone(xhs_video.pick_stream(note))

    def test_no_video(self):
        self.assertIsNone(xhs_video.pick_stream({"title": "x"}))


class DownloadTest(unittest.TestCase):
    def test_delegates_to_client_once(self):
        class Client:
            def __init__(self):
                self.calls = []

            def download_video(self, url, out, referer):
                self.calls.append((url, out, referer))
                out.write_bytes(b"v" * 100_001)
                return 100_001

        client = Client()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "video.mp4"
            size = xhs_video.download(
                "https://a.xhscdn.com/v.mp4", out, client=client,
                referer="https://www.xiaohongshu.com/explore/note?xsec_token=t",
            )
        self.assertEqual(size, 100_001)
        self.assertEqual(client.calls, [(
            "https://a.xhscdn.com/v.mp4", out,
            "https://www.xiaohongshu.com/explore/note?xsec_token=t",
        )])

def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
