"""article.py — 文章/图片抓取组件（Q4/Q5/Q6/Q7）。

所有内容平台只使用任务级 curl_cffi Chrome 146 近似网络会话请求页面与资源。
小红书只接受 service 层单次页面请求后的 ``scrape_captured`` 输入，不提供重复抓取入口。
提取在 Python 侧（路线 A）：SiteAdapter 注册表（site_adapters.py）+ trafilatura 兜底。
产物：delivery/{标题}/article.md + images/（元数据由 service 层并入 manifest.json）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from .browser_http import BrowserHttpClient, BrowserHttpError
from .session import FailureCode, url_for_log
from .site_adapters import (
    Article,
    GenericTrafilaturaAdapter,
    SiteAdapter,
    find_adapter,
)

log = logging.getLogger("medialab.article")


class ScrapeError(RuntimeError):
    """文章抓取失败（携带结构化 FailureCode，service._mark_failed 识别）。"""

    def __init__(self, code: FailureCode, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass
class ArticleResult:
    article: Article
    out_dir: Path
    article_md: Path
    images: list[dict]  # [{src, alt, local}]


class ArticleExtractor:
    """编排 Python browser session、解析、资源与产物落盘。"""

    def __init__(self, cfg: dict, http_client: BrowserHttpClient | None = None):
        self.cfg = cfg
        self.http_client = http_client
        self.a = cfg.get("article", {})
        self.min_chars = int(self.a.get("min_text_chars", 200))

    # ---------- 编排 ----------

    def scrape(self, url: str, job_id: str, out_dir: Path, on_event=None) -> ArticleResult:
        adapter = find_adapter(url)
        if adapter.platform == "xiaohongshu":
            raise ScrapeError(
                FailureCode.AUTH_REQUIRED,
                "小红书必须由 DownloadService 完成单次 Python 页面请求后调用 scrape_captured",
            )
        if self.http_client is None:
            raise ScrapeError(FailureCode.NETWORK, "文章抓取缺少任务级浏览器 HTTP 会话")
        try:
            page = self._direct_fetch(adapter, url)
            html = page["html"]
            page_url = page.get("final_url") or url
        except BrowserHttpError as exc:
            raise ScrapeError(exc.code, str(exc)) from exc
        art = self._try_extract(adapter, html, page_url)
        if art is None:
            raise ScrapeError(FailureCode.CONTENT_NOT_FOUND, "Python 会话未提取到正文")

        art.capture_mode = "curl_cffi"
        return self._finalize(art, adapter, page_url, job_id, out_dir, on_event)

    def scrape_captured(self, url: str, job_id: str, out_dir: Path, *, html: str,
                        final_url: str | None = None, on_event=None) -> ArticleResult:
        """Parse and persist an already captured page without another page request."""
        page_url = final_url or url
        adapter = find_adapter(page_url)
        extraction_url = url if adapter.platform == "xiaohongshu" else page_url
        art = self._try_extract(adapter, html, extraction_url)
        if art is None:
            raise ScrapeError(FailureCode.CONTENT_NOT_FOUND,
                              "captured page did not contain an extractable article")
        art.capture_mode = "curl_cffi"
        if adapter.platform == "xiaohongshu" and self.http_client is None:
            raise ScrapeError(FailureCode.AUTH_REQUIRED, "小红书资源下载需要任务级 Python 会话")
        return self._finalize(art, adapter, page_url, job_id, out_dir, on_event,
                              http_client=self.http_client)

    def persist_article(self, article: Article, url: str, job_id: str, out_dir: Path,
                        on_event=None) -> ArticleResult:
        """Persist an article already selected by the content probe, without refetching."""
        adapter = find_adapter(url)
        article.capture_mode = article.capture_mode or "captured"
        return self._finalize(
            article, adapter, url, job_id, out_dir, on_event,
            http_client=self.http_client,
        )

    # ---------- 页面请求与解析 ----------

    def _direct_fetch(self, adapter: SiteAdapter, url: str) -> dict:
        fetch_url = adapter.direct_source(url) or url
        return self.http_client.fetch_page(
            fetch_url,
            allowed_domains=self.http_client.credential_domains,
        )

    def _try_extract(self, adapter: SiteAdapter, html: str, url: str) -> Article | None:
        if not html:
            return None
        try:
            art = adapter.extract(html, url)
        except Exception:  # noqa: BLE001 - a broken specialist must not block generic extraction
            log.warning("[%s] 专用文章适配器失败，尝试通用正文提取", url_for_log(url)[:60])
            art = None
        if art is None and not isinstance(adapter, GenericTrafilaturaAdapter):
            try:
                art = GenericTrafilaturaAdapter().extract(html, url)
            except Exception:  # noqa: BLE001
                art = None
        if art is None:
            return None
        if self._is_dead(adapter, art):
            log.info("[%s] 页面为死页（%r），拒绝提取", url_for_log(url)[:60], art.title[:40])
            return None
        # 正文过短且无图 = 空壳（小红书图片帖例外：有图即接受）
        if len(art.content_text) < self.min_chars and not art.images:
            return None
        return art

    def _is_dead(self, adapter: SiteAdapter, art: Article) -> bool:
        """调用适配器的死页钩子：404/登录墙页面不得当正文。"""
        try:
            return adapter.looks_dead(art.title or "", art.content_text or "")
        except Exception:  # noqa: BLE001
            return False

    # ---------- 产物 ----------

    def _finalize(self, art: Article, adapter: SiteAdapter, url: str, job_id: str,
                  out_dir: Path, on_event, http_client: BrowserHttpClient | None = None) -> ArticleResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        images_dir = out_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        max_images = int(self.a.get("max_images", 100))

        saved: list[dict] = []
        client = http_client or self.http_client
        referer = adapter.image_referer(url) or url
        strict_images = adapter.platform == "xiaohongshu"
        selected_images = art.images if strict_images else art.images[:max_images]
        if client is None and selected_images:
            raise ScrapeError(FailureCode.NETWORK, "文章资源下载缺少任务级浏览器 HTTP 会话")
        seen_sources: set[str] = set()
        for img in selected_images:
            src = urljoin(url, img.get("src") or "")
            if not src.startswith("https://"):
                continue
            if not src or src in seen_sources:
                continue
            seen_sources.add(src)
            i = len(saved)
            try:
                local = self._download_image(
                    src, images_dir, i, referer, on_event,
                    http_client=client,
                    strict=strict_images,
                )
            except BrowserHttpError as exc:
                if strict_images:
                    raise ScrapeError(exc.code, "小红书图片下载失败") from exc
                log.warning("图片下载失败（%s）: %s", exc.code.value, url_for_log(src)[:100])
                local = None
            if local is None and strict_images:
                raise ScrapeError(FailureCode.NETWORK,
                                  f"小红书图片下载失败: {url_for_log(src)[:160]}")
            if local is not None:
                rel = str(local.relative_to(out_dir)).replace("\\", "/")
                saved.append({"src": src, "alt": img.get("alt") or "", "local": rel})

        md_path = out_dir / "article.md"
        # echo §8：article.md 是唯一真实来源，已存在不覆盖（用户可能已修改）
        if md_path.exists():
            log.info("[%s] article.md 已存在，跳过正文写入（echo §8）；图片仍可更新", job_id)
        else:
            md = art.content_text
            for s in saved:
                md = md.replace(s["src"], s["local"])
            if strict_images and saved:
                image_blocks = [
                    f"![{s['alt'] or f'图片 {index + 1}'}]({s['local']})"
                    for index, s in enumerate(saved)
                ]
                md = md.rstrip()
                md = (md + "\n\n" if md else "") + "\n\n".join(image_blocks) + "\n"
            md_path.write_text(md, encoding="utf-8")
            log.info("[%s] 文章抓取完成: %s（%s 字，%d 图，capture=%s）",
                     job_id, md_path, len(md), len(saved), art.capture_mode)
        return ArticleResult(article=art, out_dir=out_dir, article_md=md_path, images=saved)

    def _download_image(self, src: str, images_dir: Path, index: int, referer: str | None,
                        on_event, *, http_client: BrowserHttpClient, strict: bool) -> Path | None:
        max_bytes = int(self.a.get("max_image_bytes", 15728640))
        ext = Path(src.split("?")[0]).suffix.lower() or ""
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"):
            ext = ".jpg"
        path = images_dir / f"{index:04d}{ext}"
        try:
            return http_client.download_image(src, path, referer or "", max_bytes)
        except BrowserHttpError as exc:
            log.warning("图片下载失败（单次请求，%s）: %s",
                        exc.code.value, url_for_log(src)[:100])
            if strict:
                raise
            return None
