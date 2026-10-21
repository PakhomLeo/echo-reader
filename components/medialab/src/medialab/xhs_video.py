"""xhs_video.py — 小红书视频帖解析与下载（P4b）。

小红书 URL（/explore/{id}?xsec_token=）无法从 URL 区分图文/视频帖，
本模块负责从页面 HTML 解析 note.video 流信息，并委托任务级 XHS client 下载视频。

码流优先级：EF7 > EF6 > EF5 > EF4（越后越清晰）。
"""

from __future__ import annotations

import logging

from .site_adapters import find_xhs_note, normalize_xhs_resource_url, parse_xhs_initial_state

log = logging.getLogger("medialab.xhs_video")

MIN_VIDEO_BYTES = 100_000
CODEC_PRIORITY = ("EF7", "EF6", "EF5", "EF4")


def note_from_html(html: str, url: str) -> dict | None:
    """从页面 HTML 的 __INITIAL_STATE__ 提取 note 实体；非小红书页/无数据返回 None。"""
    data = parse_xhs_initial_state(html)
    if not data:
        return None
    return find_xhs_note(data, url)


def pick_stream(note: dict) -> tuple[str, str] | None:
    """按码流优先级挑选最高清 masterUrl；无可用流返回 None。"""
    stream = ((note.get("video") or {}).get("media") or {}).get("stream") or {}
    for codec in CODEC_PRIORITY:
        for it in stream.get(codec) or []:
            if isinstance(it, dict) and it.get("masterUrl"):
                url = normalize_xhs_resource_url(it["masterUrl"])
                if url:
                    return codec, url
    return None


def download(stream_url: str, out_path, *, client, referer: str) -> int:
    """Delegate the one selected stream to the task-scoped XHS client."""
    return client.download_video(stream_url, out_path, referer)
