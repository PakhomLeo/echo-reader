"""ASR 适配器：可插拔接口 + qwen-audio-3.0-asr-flash-filetrans 实现。

流程：上传 OSS → 生成签名 URL → 验证 URL → 提交异步任务 → 轮询(2s→5s) →
下载 transcription_url（24h 有效，立即落盘）→ 规范化 segments（时间戳反算）。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .audio import AudioProcessor
from .oss import OssClient, verify_url

log = logging.getLogger("medialab.asr")

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_UNKNOWN = "UNKNOWN"

# 千问判定切片无有效语音（音乐/静音段）：不重试，直接跳过该片
NO_SPEECH_CODES = ("SUCCESS_WITH_NO_VALID_FRAGMENT",)
NO_SPEECH_MARKERS = ("have no audio",)


class NoSpeechError(RuntimeError):
    """切片无有效语音（音乐/静音段），应跳过而非重试。"""


@dataclass
class Segment:
    text: str
    start: float  # 秒（已对齐到原始时间轴）
    end: float
    speaker_id: int | None = None  # 说话人分离（diarization_enabled 时返回）

    def to_dict(self) -> dict:
        d = {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}
        if self.speaker_id is not None:
            d["speaker_id"] = self.speaker_id
        return d


@dataclass
class TranscriptResult:
    segments: list[Segment] = field(default_factory=list)
    transcript_path: Path | None = None

    def to_dict(self) -> dict:
        return {"segments": [s.to_dict() for s in self.segments]}


class AsrProvider:
    """ASR 提供方抽象。新增供应商时实现此接口。

    契约：submit 提交任务 → poll 轮询（返回 (status, 原始响应)）→
    状态 SUCCEEDED 时调用 fetch_result(响应) 取回供应商原始转写结果
    （transcription_url 的位置与认证方式因供应商而异，由 Provider 自己处理）。
    """

    def submit(self, file_urls: list[str]) -> str:
        raise NotImplementedError

    def poll(self, task_id: str) -> tuple[str, dict]:
        raise NotImplementedError

    def fetch_result(self, resp: dict) -> dict:
        raise NotImplementedError


class QwenFiletransProvider(AsrProvider):
    """qwen-audio-3.0-asr-flash-filetrans（DashScope 公共端点）。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 language_hints: list[str] | None = None, proxy: str | None = None,
                 diarization_enabled: bool = False, speaker_count: int = 0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.language_hints = language_hints
        self.proxy = proxy
        self.diarization_enabled = diarization_enabled
        self.speaker_count = speaker_count

    def _request(self, path: str, body: dict | None = None, method: str = "POST",
                 extra_headers: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            r = httpx.request(method, url, json=body, headers=headers, timeout=60)
        except httpx.HTTPError as e:
            raise RuntimeError(f"ASR 请求失败: {e}") from e
        if r.status_code >= 400:
            detail = r.text[:300]
            raise RuntimeError(f"ASR HTTP {r.status_code}: {detail}")
        return r.json()

    def submit(self, file_urls: list[str]) -> str:
        parameters: dict = {"channel_id": [0]}
        if self.language_hints:
            parameters["language_hints"] = self.language_hints
        if self.diarization_enabled:
            parameters["diarization_enabled"] = True
            if self.speaker_count > 0:
                parameters["speaker_count"] = self.speaker_count
        body = {
            "model": self.model,
            "input": {"file_urls": file_urls},
            "parameters": parameters,
        }
        resp = self._request(
            "/services/audio/asr/transcription", body,
            extra_headers={"X-DashScope-Async": "enable"},
        )
        task_id = (resp.get("output") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"ASR 提交失败: {resp}")
        log.info("ASR 任务已提交: %s", task_id)
        return task_id

    def poll(self, task_id: str) -> tuple[str, dict]:
        resp = self._request(f"/tasks/{task_id}", method="GET")
        output = resp.get("output") or {}
        status = output.get("task_status", STATUS_UNKNOWN).upper()
        return status, resp

    def fetch_result(self, resp: dict) -> dict:
        """从轮询成功（SUCCEEDED）的响应中解析 transcription_url 并下载原始转写 JSON。"""
        results = ((resp.get("output") or {}).get("results")) or []
        for r in results:
            if r.get("transcription_url"):
                return self._fetch_transcription(r["transcription_url"])
        raise RuntimeError(f"响应中无 transcription_url: {resp}")

    def _fetch_transcription(self, transcription_url: str) -> dict:
        try:
            r = httpx.get(transcription_url, headers={"User-Agent": "medialab"},
                          timeout=120, proxy=self.proxy or None)
        except httpx.HTTPError as e:
            raise RuntimeError(f"transcription 下载失败: {e}") from e
        if r.status_code >= 400:
            raise RuntimeError(f"transcription 下载 HTTP {r.status_code}: {r.text[:300]}")
        return r.json()


class AsrPipeline:
    """编排：切片 → 倍速 → 上传 OSS → 签名 URL → 提交 → 轮询 → 下载 → 对齐。"""

    def __init__(self, cfg: dict, audio: AudioProcessor, provider: AsrProvider, oss: OssClient | None):
        self.cfg = cfg
        self.audio = audio
        self.provider = provider
        self.oss = oss
        self.slice_seconds = float(cfg["oss"].get("slice_seconds", 60))
        self.overlap = float(cfg["oss"].get("overlap_seconds", 1))
        self.speed_factor = float(cfg.get("asr", {}).get("speed_factor", 1.0))
        self.presign_expires = int(cfg["oss"].get("presign_expires", 86400))

    def slice_audio(self, src: Path, workdir: Path) -> list[dict]:
        """切片并（可选）倍速，返回 [{path, offset, speed}]；offset 为原始时间轴起始秒。

        尾部剩余不足 1s 的碎片直接跳过（其内容已被前一片的重叠覆盖，
        且过短音频会导致 ASR 端 "have no audio" 拒绝）。
        """
        duration = self._duration_seconds(src)
        slices: list[dict] = []
        start = 0.0
        index = 0
        while start < duration:
            end = min(start + self.slice_seconds, duration)
            if end - start < 1.0:
                break
            s_start = start
            s_end = end
            if end < duration and self.overlap > 0:
                s_end = min(end + self.overlap, duration)
            piece = workdir / f"slice_{index:04d}.m4a"
            self.audio.slice(src, s_start, s_end, piece)
            if self.speed_factor != 1.0:
                fast = workdir / f"slice_{index:04d}_fast.m4a"
                self.audio.speed(piece, self.speed_factor, fast)
                piece = fast
            slices.append({"path": piece, "offset": start, "speed": self.speed_factor})
            index += 1
            start = end
        return slices

    def _duration_seconds(self, path: Path) -> float:
        import shutil
        import subprocess

        ffprobe = shutil.which("ffprobe")
        if not ffprobe and self.audio.ffmpeg_path:
            ffprobe = str(Path(self.audio.ffmpeg_path).with_name("ffprobe.exe"))
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffprobe 失败: {proc.stderr[-300:]}")
        return float(proc.stdout.strip())

    def transcribe(
        self,
        audio_path: Path,
        task_uuid: str,
        workdir: Path,
        outdir: Path,
        max_retries: int = 3,
        language: str | None = None,
        speaker_label: str = "说话人",
    ) -> TranscriptResult:
        """完整 ASR 流程。需要 OSS 已配置。

        注意：qwen filetrans 的 file_urls 只允许 1 个链接，因此每个切片
        单独提交一个异步任务，结果按切片偏移/倍速因子反算对齐。

        切片提交并行（默认 3 路，RPM 600 允许 3-5）：切片 → 上传（串行，快）→
        提交+轮询+取回（线程池并行）→ 按切片序合并。
        """
        if self.oss is None:
            raise RuntimeError("OSS 未配置：请填写 config/secrets.toml 的 [oss]")

        slices = self.slice_audio(audio_path, workdir)
        log.info("切片完成: %d 片 (speed=%s)", len(slices), self.speed_factor)

        for i, s in enumerate(slices):
            self.oss.upload(s["path"], task_uuid, filename=f"slice_{i:04d}.m4a", content_type="audio/mp4")
        log.info("已上传 %d 片到 OSS", len(slices))

        workers = int(self.cfg.get("asr", {}).get("slice_concurrency", 3))
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(self._submit_slice, i, s, task_uuid, max_retries)
                       for i, s in enumerate(slices)]
            for fut in futures:
                results.append(fut.result())

        raw_files: list[dict] = []
        all_segments: list[Segment] = []
        for i, r in enumerate(results):
            if r.get("skipped"):
                log.warning("切片 %d 无有效语音，跳过: %s", i, r["skipped"])
                continue
            raw_files.append({"slice": i, "offset": slices[i]["offset"], "speed": slices[i]["speed"],
                              "raw": r["raw"]})
            all_segments.extend(r["segments"])

        result = TranscriptResult(segments=_dedupe_and_sort(all_segments))
        return self._save(result, task_uuid, outdir, language, speaker_label)
    def _submit_slice(self, i: int, s: dict, task_uuid: str, max_retries: int) -> dict:
        """单个切片：签名 → 验证 → 提交 → 轮询 → 取回 → 提取 segments。

        无语音片（NoSpeechError）转为 {"skipped": ...} 标记，不重试不抛出。
        """
        url = self.oss.sign_url(self.oss.object_key(task_uuid, f"slice_{i:04d}.m4a"), self.presign_expires)
        if not verify_url(url, expected_size=s["path"].stat().st_size):
            raise RuntimeError(f"切片 {i} 签名 URL 验证失败")
        try:
            raw = self._submit_one([url], max_retries)
        except NoSpeechError as e:
            return {"skipped": str(e)}
        return {"raw": raw, "segments": self._extract_segments_one(raw, s)}

    def _submit_one(self, urls: list[str], max_retries: int) -> dict:
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                task_id = self.provider.submit(urls)
                resp = self._poll_until_done(task_id)
                return self.provider.fetch_result(resp)
            except NoSpeechError:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("ASR 尝试 %d/%d 失败: %s", attempt + 1, max_retries + 1, e)
                if attempt < max_retries:
                    time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"ASR 重试耗尽: {last_err}")

    def _poll_until_done(self, task_id: str) -> dict:
        interval = float(self.cfg["asr"].get("poll_interval", 2.0))
        max_interval = float(self.cfg["asr"].get("poll_max_interval", 5.0))
        max_wait = float(self.cfg["asr"].get("max_wait_seconds", 1800))
        waited = 0.0
        while waited < max_wait:
            status, resp = self.provider.poll(task_id)
            if status == STATUS_SUCCEEDED:
                return resp
            if status == STATUS_FAILED:
                out = resp.get("output") or {}
                code = str(out.get("code") or "")
                msg = str(out.get("message") or "")
                if code in NO_SPEECH_CODES or any(m in msg.lower() for m in NO_SPEECH_MARKERS):
                    raise NoSpeechError(f"切片无有效语音: {code} {msg[:100]}")
                raise RuntimeError(f"ASR 任务失败: {resp}")
            time.sleep(interval)
            waited += interval
            interval = min(interval * 1.5, max_interval)
        raise TimeoutError(f"ASR 轮询超时（{max_wait}s）")

    def _save(self, result: TranscriptResult, task_uuid: str, outdir: Path,
              language: str | None, speaker_label: str) -> TranscriptResult:
        """转录落盘为 echo 格式 transcript.json（毫秒/段 id/说话人标签）。

        language 未指定时按转录文本汉字占比自动判定（zh-CN / en）。
        切片原始响应不再落盘（echo 规范无位置）；诊断统计只记日志。
        """
        from .publishing import detect_language, write_transcript

        outdir.mkdir(parents=True, exist_ok=True)
        segs = [s.to_dict() for s in result.segments]
        lang = language or detect_language(segs)
        result.transcript_path = write_transcript(outdir, segs, lang, speaker_label)
        log.info("转录落盘: %s（%d 段，%s）", result.transcript_path.name, len(result.segments), lang)
        return result

    def _extract_segments_one(self, raw: dict, slice_meta: dict) -> list[Segment]:
        """单个切片结果的句子级时间戳，反算回原始时间轴。

        transcription_url 下载的 JSON 结构：
        {"transcripts": [{"sentences": [{"begin_time", "end_time", "text", "words": [...]}]}]}
        transcripts[0] 对应本次提交的唯一 file_url（即该切片）。
        T_orig = slice.offset + (t_slice / speed)
        """
        offset = float(slice_meta["offset"])
        speed = float(slice_meta["speed"])
        out: list[Segment] = []
        for tr in raw.get("transcripts") or []:
            for st in tr.get("sentences") or []:
                text = (st.get("text") or "").strip()
                if not text:
                    continue
                begin_ms = int(st.get("begin_time") or 0)
                end_ms = int(st.get("end_time") or begin_ms)
                start_s = offset + (begin_ms / 1000.0) / speed
                end_s = offset + (end_ms / 1000.0) / speed
                speaker = st.get("speaker_id")
                out.append(Segment(
                    text=text, start=start_s, end=end_s,
                    speaker_id=int(speaker) if speaker is not None else None,
                ))
        return out


def _dedupe_and_sort(segments: list[Segment]) -> list[Segment]:
    """按开始时间排序，过滤重叠切片产生的重复文本（相同文本且时间接近）。"""
    segments.sort(key=lambda s: s.start)
    deduped: list[Segment] = []
    for s in segments:
        if deduped and s.text == deduped[-1].text and abs(s.start - deduped[-1].start) < 3.0:
            deduped[-1].end = max(deduped[-1].end, s.end)
        else:
            deduped.append(s)
    return deduped
