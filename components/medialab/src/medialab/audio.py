"""音频后处理：ffmpeg 探测与转码。

v1 实现 transcode（交付用）；slice/speed 为 v2 ASR 管线预留接口。
"""

from __future__ import annotations

import logging
import shutil
import shlex
import subprocess
from pathlib import Path

log = logging.getLogger("medialab.audio")

MISE_CMD = "mise"

# 目标扩展名 -> 降级重编码参数（-c:a 之后的参数）
_FALLBACK_ENCODE: dict[str, list[str]] = {
    ".mp3": ["libmp3lame", "-q:a", "2"],
    ".m4a": ["aac", "-b:a", "192k"],
    ".aac": ["aac", "-b:a", "192k"],
    ".flac": ["flac"],
    ".wav": ["pcm_s16le"],
    ".opus": ["libopus", "-b:a", "128k"],
    ".ogg": ["libopus", "-b:a", "128k"],
}


def _encode_args(dst: Path) -> list[str]:
    """按目标扩展名返回 ffmpeg 重编码参数；未知扩展名默认 aac。"""
    args = _FALLBACK_ENCODE.get(dst.suffix.lower(), ["aac", "-b:a", "192k"])
    return ["-c:a", *args]


class AudioProcessor:
    def __init__(self, ffmpeg_path: str = ""):
        self.ffmpeg_path = ffmpeg_path or self._probe()

    @staticmethod
    def _probe() -> str:
        """三级探测：配置路径 -> PATH -> mise。"""
        if shutil.which("ffmpeg"):
            return shutil.which("ffmpeg")
        mise_bin = shutil.which(MISE_CMD)
        if mise_bin:
            try:
                out = subprocess.run(
                    [mise_bin, "which", "ffmpeg"], capture_output=True, text=True, timeout=15
                )
                path = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
                if path and Path(path).exists():
                    return path
            except (OSError, subprocess.SubprocessError):
                pass
        raise RuntimeError("未找到 ffmpeg：请安装 ffmpeg 或配置 [audio].ffmpeg_path")

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = [self.ffmpeg_path] + args
        log.debug("ffmpeg: %s", shlex.join(cmd))
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg 失败: {(proc.stderr or '')[-500:]}")
        return proc

    def transcode(self, src: Path, dst: Path, codec: str, quality: str = "2") -> Path:
        """把音频转成指定格式。codec: mp3/m4a/aac/flac/wav/opus/ogg。"""
        dst.parent.mkdir(parents=True, exist_ok=True)
        args = ["-y", "-i", str(src), "-vn"]
        if codec == "mp3":
            args += ["-c:a", "libmp3lame", "-q:a", quality]
        elif codec in ("m4a", "aac"):
            args += ["-c:a", "aac", "-b:a", "192k"]
        elif codec == "flac":
            args += ["-c:a", "flac"]
        elif codec == "wav":
            args += ["-c:a", "pcm_s16le"]
        elif codec in ("opus", "ogg"):
            args += ["-c:a", "libopus", "-b:a", "128k"]
        else:
            raise ValueError(f"不支持的音频格式: {codec}")
        args.append(str(dst))
        self._run(args)
        return dst

    def slice(self, src: Path, start: float, end: float, dst: Path) -> Path:
        """音频切片：截取 [start, end) 秒。

        优先 -c copy 不重编码（保持音质/速度）；若尾部切片因容器时间基/
        edit list 问题产出空文件（无流），自动降级重编码（编码器按目标扩展名选择）。
        """
        dst.parent.mkdir(parents=True, exist_ok=True)
        base = ["-y", "-i", str(src), "-ss", f"{start:.3f}", "-to", f"{end:.3f}"]
        self._run(base + ["-c", "copy", str(dst)])
        if dst.exists() and dst.stat().st_size < 1024:
            log.warning("切片 %s 为空（-c copy 尾部问题），降级重编码", dst)
            dst.unlink(missing_ok=True)
            self._run(base + ["-vn"] + _encode_args(dst) + [str(dst)])
        return dst

    def speed(self, src: Path, factor: float, dst: Path) -> Path:
        """音频倍速（atempo 滤镜），factor>0；倍速后时长=原时长/factor。

        单滤镜 atempo 上限 2.0，更高倍速自动级联（如 3x -> 2x * 1.5x）。
        """
        if not 0.5 <= factor <= 16.0:
            raise ValueError("atempo 仅支持 0.5~16.0（单滤镜 2.0，超范围自动级联）")
        dst.parent.mkdir(parents=True, exist_ok=True)
        factors = []
        remaining = factor
        while remaining > 2.0:
            factors.append(2.0)
            remaining /= 2.0
        factors.append(remaining)
        filter_expr = ",".join(f"atempo={f:.4f}" for f in factors)
        args = ["-y", "-i", str(src), "-filter:a", filter_expr, "-vn", str(dst)]
        self._run(args)
        return dst
