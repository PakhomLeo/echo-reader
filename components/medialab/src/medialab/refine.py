"""refine.py — ASR 转写稿 → 可读文章（LLM 润色，qwen3.7-flash）。

作为转写流程的最后一个步骤：把带说话人/时间戳的 segments 改写成适合阅读的文章：
- 按说话人分段（段首标注"说话人N"）
- 清理口癖、语气词、无意义重复
- 合并零碎短句、书面化
- 忠实原文：不增删事实、保留专业术语

所有后端都走 OpenAI 兼容的 ``/chat/completions``：

- ``dashscope``（默认）：DashScope 兼容模式，默认复用 [asr].api_key；
- ``openai_compatible``：任意 OpenAI 兼容服务（需 base_url + model，可选 api_key）；
- ``ollama``：本机 Ollama 的 ``/v1`` 兼容端点，无需密钥。

后端选择由 :func:`build_refiner` 根据 ``[refine].provider`` 决定（OpenAI 兼容 / Ollama 接入
移植自 UP 的 Provider 抽象）。超长输入按行分批（max_input_chars）逐批润色后合并。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("medialab.refine")

DEFAULT_MODEL = "qwen3.7-flash"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
PROVIDERS = ("dashscope", "openai_compatible", "ollama")

# {speaker_label} 在 __init__ 时替换为配置的说话人前缀
DEFAULT_SYSTEM_PROMPT = """你是资深文字编辑，负责把"带说话人标记的口语转写稿"改写成适合阅读的文章。

改写规则：
1. 忠实原文：不增删事实、不编造或遗漏内容；专业术语（期刊名、医学名词等）保留原文写法；不改变信息顺序。
2. 按说话人分段：同一说话人的相邻发言合并为一段；说话人切换时必须另起一段；同一说话人的长段发言（约 300 字以上）可按话题适当拆分为多段。
3. 每段开头标注说话人，格式："{speaker_label}N："（N 为原始编号 0/1/2...）。
4. 清理口癖与冗余：删除"呃、啊、嗯、就是说、然后、那个、对不对、对吧、其实"等语气词、口头禅与无意义重复。
5. 合并短句：把零碎的短句合并为通顺连贯的书面句子，可补充必要连接词，但不得改变原意。
6. 输出要求：只输出文章正文本身，段落之间空一行，不要任何前言、后记或 markdown 代码块。"""

# 英文转写稿 → 中文文章（chinese=True 时使用）
TRANSLATE_SYSTEM_PROMPT = """你是资深翻译兼文字编辑，负责把"带说话人标记的英文口语转写稿"翻译并改写成通顺的中文文章。

改写规则：
1. 忠实原文：完整翻译全部内容，不增删事实、不编造、不遗漏；专业术语、品牌名、人名、期刊名等保留原文或采用"中文（English）"形式。
2. 按说话人分段：同一说话人的相邻发言合并为一段；说话人切换时必须另起一段；同一说话人的长段发言（约 300 字以上）可按话题适当拆分为多段。
3. 每段开头标注说话人，格式："{speaker_label}N："（N 为原始编号 0/1/2...）。
4. 口语化表达改为书面中文：删除无意义重复与口头禅，把零碎短句合并为通顺连贯的句子。
5. 输出要求：只输出文章正文本身，段落之间空一行，不要任何前言、后记或 markdown 代码块。"""

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def is_chinese_content(segments: list[dict], threshold: float = 0.2) -> bool:
    """按转写文本的汉字占比判断是否为中文内容（占比 >= threshold 视为中文）。

    英文内容（占比低于阈值）时调用方应额外生成中文版文章。
    """
    text = "".join((s.get("text") or "") for s in segments or []).strip()
    if not text:
        return True  # 无文本视为中文，避免误触发翻译
    return len(CJK_RE.findall(text)) / len(text) >= threshold


@dataclass
class RefineResult:
    article: str = ""
    input_chars: int = 0
    output_chars: int = 0
    chunks: int = 1
    elapsed: float = 0.0
    paths: list[Path] = field(default_factory=list)


def _segment_lines(segments: list[dict]) -> list[str]:
    """segments -> 输入行列表；无 speaker_id 时只输出文本。"""
    lines: list[str] = []
    for s in segments or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        sid = s.get("speaker_id")
        lines.append(f"speaker_{sid}: {text}" if sid is not None else text)
    return lines


def _split_chunks(lines: list[str], max_chars: int) -> list[list[str]]:
    """按行贪心分批，保证每批字符数不超过 max_chars。"""
    if max_chars <= 0 or not lines:
        return [lines] if lines else []
    chunks: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for ln in lines:
        if cur and size + len(ln) + 1 > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        chunks.append(cur)
    return chunks


def is_loopback_url(url: str) -> bool:
    """Return whether an API base URL points at the local machine."""
    host = (urlsplit(url).hostname or "").casefold()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def clean_generated_article(content: str) -> str:
    """Remove prompt wrappers that small local models sometimes echo back."""
    cleaned = content.strip()
    fence = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    cleaned = re.sub(r"^标题\s*[:：].*?(?:\n+|$)", "", cleaned, count=1)
    cleaned = re.sub(r"</?transcript>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


class ChatRefiner:
    """转写稿润色器（任意 OpenAI 兼容 chat/completions 接口；默认 DashScope qwen3.7-flash）。

    长输入保护（LESSONS 实测）：
    - qwen3.7-flash 在"改写文章"任务上偶发输出爆炸（复述/扩展原文，可达 10 万+ token），
      非流式请求必须等全部输出完成 → 表现为 15-20 分钟"假挂起"。
      对策：body 带 max_tokens 上限截断爆炸输出（finish_reason=length 时可感知）；
      refine() 对超长输入自适应压小块（避免单块过大触发模型异常行为）。
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        temperature: float = 0.3,
        max_input_chars: int = 24000,
        timeout: float = 300.0,
        max_retries: int = 2,
        speaker_label: str = "说话人",
        max_tokens: int = 12000,
        shrink_threshold: int = 2,
        provider: str = "dashscope",
        clean_output: bool = False,
    ):
        self.provider = provider
        self.clean_output = bool(clean_output)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = float(temperature)
        self.max_input_chars = int(max_input_chars)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.speaker_label = speaker_label
        self.max_tokens = int(max_tokens)
        # 总输入超过 max_input_chars × shrink_threshold 时，单块上限压到 max(4000, max_input_chars//3)
        self.shrink_threshold = int(shrink_threshold)
        self.system_prompt = DEFAULT_SYSTEM_PROMPT.format(speaker_label=speaker_label)
        self.translate_prompt = TRANSLATE_SYSTEM_PROMPT.format(speaker_label=speaker_label)

    def _chat(self, user_content: str, chinese: bool = False) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.translate_prompt if chinese else self.system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # Loopback services (Ollama, local gateways) must not be routed through a system proxy.
        trust_env = not is_loopback_url(self.base_url)
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = httpx.post(url, json=body, headers=headers, timeout=self.timeout,
                               trust_env=trust_env)
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
                resp = r.json()
                content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content")
                if not content:
                    raise RuntimeError(f"响应无 content: {str(resp)[:200]}")
                return clean_generated_article(content) if self.clean_output else content.strip()
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("refine 尝试 %d/%d 失败: %s", attempt + 1, self.max_retries + 1, e)
                if attempt < self.max_retries:
                    time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"refine 调用失败（重试耗尽）: {last_err}")

    @staticmethod
    def _user_prompt(lines: list[str], part: int | None = None, chinese: bool = False) -> str:
        head = "以下是原始转写稿"
        if part is not None:
            head += f"的第 {part} 部分"
        head += "（每行一条发言，speaker_N 为说话人编号）：\n"
        tail = "请翻译成中文并按上述规则改写成文章。" if chinese else "请按上述规则改写成文章。"
        return head + "\n".join(lines) + "\n\n" + tail

    def refine(self, segments: list[dict], chinese: bool = False) -> RefineResult:
        """把 segments 改写成文章；chinese=True 时翻译并改写成中文文章。

        长输入自适应（LESSONS）：总输入超过 max_input_chars × shrink_threshold 时，
        单块上限压到 max(4000, max_input_chars // 3)——大块输入更易触发
        qwen3.7-flash 的输出爆炸（假挂起），小块可显著降低概率并缩短单次生成。
        """
        start = time.monotonic()
        lines = _segment_lines(segments)
        result = RefineResult(input_chars=sum(len(l) + 1 for l in lines))
        chunk_size = self.max_input_chars
        if result.input_chars > self.max_input_chars * self.shrink_threshold:
            chunk_size = max(4000, self.max_input_chars // 3)
            log.info("refine 输入 %d 字符超长，单块上限 %d → %d（防输出爆炸挂起）",
                     result.input_chars, self.max_input_chars, chunk_size)
        chunks = _split_chunks(lines, chunk_size)
        result.chunks = len(chunks)
        parts: list[str] = []
        for i, chunk in enumerate(chunks):
            text = self._chat(self._user_prompt(chunk, i + 1 if len(chunks) > 1 else None, chinese), chinese=chinese)
            if text:
                parts.append(text)
            log.info("refine%s 分块 %d/%d 完成（%d 行）", "（中文版）" if chinese else "", i + 1, len(chunks), len(chunk))
        result.article = "\n\n".join(parts)
        result.output_chars = len(result.article)
        result.elapsed = time.monotonic() - start
        return result

    def save(self, result: RefineResult, outdir: Path, filename: str = "article.md") -> Path:
        outdir.mkdir(parents=True, exist_ok=True)
        p = outdir / filename
        p.write_text(result.article, encoding="utf-8")
        result.paths.append(p)
        return p


# Backwards-compatible name used by earlier MediaLab hosts.
QwenRefiner = ChatRefiner


def build_refiner(cfg: dict) -> tuple[ChatRefiner | None, str | None]:
    """Create the configured refiner, or return ``(None, reason)`` when it is disabled.

    ``[refine].provider`` selects the backend; credentials come from ``[refine].api_key``
    (secrets.toml) and, for ``dashscope`` only, fall back to ``[asr].api_key``.
    """
    refine_cfg = cfg.get("refine", {}) or {}
    if not refine_cfg.get("enabled", True):
        return None, "disabled"
    provider = str(refine_cfg.get("provider") or "dashscope").strip().casefold()
    if provider not in PROVIDERS:
        return None, f"unknown_provider:{provider}"
    api_key = str(refine_cfg.get("api_key") or "").strip()
    base_url = str(refine_cfg.get("base_url") or "").strip()
    model = str(refine_cfg.get("model") or "").strip()
    if provider == "dashscope":
        api_key = api_key or str((cfg.get("asr") or {}).get("api_key") or "").strip()
        base_url = base_url or DEFAULT_BASE_URL
        model = model or DEFAULT_MODEL
        if not api_key:
            return None, "missing_api_key"
    elif provider == "ollama":
        base_url = base_url or OLLAMA_DEFAULT_BASE_URL
        if not model:
            return None, "missing_model"
    else:  # openai_compatible
        if not base_url:
            return None, "missing_base_url"
        if not model:
            return None, "missing_model"
        if not api_key and not is_loopback_url(base_url):
            return None, "missing_api_key"
    refiner = ChatRefiner(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=refine_cfg.get("temperature", 0.3),
        max_input_chars=refine_cfg.get("max_input_chars", 24000),
        timeout=refine_cfg.get("timeout_seconds", 300),
        max_retries=refine_cfg.get("max_retries", 2),
        speaker_label=refine_cfg.get("speaker_label", "说话人"),
        max_tokens=refine_cfg.get("max_tokens", 12000),
        shrink_threshold=refine_cfg.get("shrink_threshold", 2),
        provider=provider,
        clean_output=provider == "ollama",
    )
    return refiner, None
