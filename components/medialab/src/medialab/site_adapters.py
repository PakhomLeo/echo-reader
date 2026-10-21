"""site_adapters.py — 文章抓取的站点适配器注册表（Q5：5 家特殊排版 + trafilatura 兜底）。

每个适配器负责一个平台的针对性提取（CSS 选择器 / hydration JSON / meta），
统一输出 Article；提取逻辑全部在 Python 侧（路线 A，Q6）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlsplit

log = logging.getLogger("medialab.article")

TAG_RE = re.compile(r"<[^>]+>")
IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def strip_tags(html: str) -> str:
    return TAG_RE.sub("", html or "").strip()


# ---------------------------------------------------------------------------
# 产物模型
# ---------------------------------------------------------------------------

@dataclass
class Article:
    title: str
    url: str
    site_name: str = ""
    author: str | None = None
    date: str | None = None
    content_html: str = ""
    content_text: str = ""  # markdown 正文
    images: list[dict] = field(default_factory=list)  # [{src, alt}]
    capture_mode: str = "direct"


# ---------------------------------------------------------------------------
# HTML → Markdown
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "nav", "footer", "form", "iframe", "aside", "noscript", "template", "button", "svg", "header"}


def _inline_text(el) -> str:
    """渲染行内元素。"""
    tag = el.tag.lower() if isinstance(el.tag, str) else ""
    if tag == "br":
        return "\n"
    if tag in ("strong", "b"):
        inner = (el.text or "") + "".join(_inline_text(c) + (c.tail or "") for c in el)
        return f"**{inner}**"
    if tag in ("em", "i"):
        inner = (el.text or "") + "".join(_inline_text(c) + (c.tail or "") for c in el)
        return f"*{inner}*"
    if tag == "code":
        return "`" + (el.text or "") + "`"
    if tag == "a":
        href = el.get("href", "")
        text = ((el.text or "") + "".join(_inline_text(c) + (c.tail or "") for c in el)).strip()
        return f"[{text}]({href})" if href else text
    if tag == "img":
        src = el.get("src") or el.get("data-src") or ""
        if src.startswith("data:"):
            src = ""  # 懒加载 SVG 占位图不是真实内容
        alt = el.get("alt") or ""
        return f"![{alt}]({src})" if src else ""
    out = []
    if el.text:
        out.append(el.text)
    for c in el:
        out.append(_inline_text(c))
        if c.tail:
            out.append(c.tail)
    return "".join(out)


def _block_text(el, depth: int = 0) -> str:
    tag = el.tag.lower() if isinstance(el.tag, str) else ""
    if tag in _SKIP_TAGS:
        return ""
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return f"{'#' * int(tag[1])} {_inline_text(el).strip()}\n\n"
    if tag == "p":
        t = _inline_text(el).strip()
        return f"{t}\n\n" if t else ""
    if tag == "blockquote":
        inner = "".join(_block_text(c, depth + 1) for c in el).strip()
        lines = [f"> {ln}" for ln in inner.splitlines() if ln.strip()]
        return "\n".join(lines) + "\n\n"
    if tag == "pre":
        code = (el.text or "").strip() or "".join((c.text or "") for c in el).strip()
        return f"```\n{code}\n```\n\n"
    if tag in ("ul", "ol"):
        out = []
        for i, li in enumerate(el.findall(".//li") or [c for c in el if (c.tag if isinstance(c.tag, str) else "") == "li"]):
            marker = f"{i + 1}." if tag == "ol" else "-"
            out.append(f"{marker} {_inline_text(li).strip()}")
        return "\n".join(out) + "\n\n"
    if tag == "hr":
        return "---\n\n"
    if tag == "img":
        t = _inline_text(el).strip()
        return f"{t}\n\n" if t else ""
    out = []
    if el.text and el.text.strip():
        out.append(el.text.strip())
    for c in el:
        t = _block_text(c, depth + 1)
        if t:
            out.append(t)
        if c.tail and c.tail.strip():
            out.append(c.tail.strip())
    return "".join(out).strip("\n") + ("\n\n" if out else "")


def html_to_markdown(html: str) -> str:
    """HTML 正文 → Markdown（区块级转换，图片保留 URL 供本地化重写）。"""
    from lxml import html as lh

    if not html or not html.strip():
        return ""
    doc = lh.fromstring(html)
    parts = []
    for el in doc:
        t = _block_text(el)
        if t:
            parts.append(t)
    md = "\n".join(parts).strip()
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md


# ---------------------------------------------------------------------------
# SiteAdapter 基类与注册表
# ---------------------------------------------------------------------------

class SiteAdapter:
    """站点适配器：一个平台一个实现。

    - match(url)：域名匹配（子域宽匹配）
    - direct_source(url)：Python 请求的可选替代源（如 X oEmbed）；None = 原 URL
    - extract(html, url)：从响应 HTML 提取 Article；无法提取返回 None
    - image_referer(page_url)：图片下载的防盗链 Referer
    """

    platform: str = "generic_article"
    hosts: tuple[str, ...] = ()
    site_name: str = ""  # 中文站点名（manifest source 用）

    def match(self, url: str) -> bool:
        host = host_of(url)
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def direct_source(self, url: str) -> str | None:
        return None

    def extract(self, html: str, url: str) -> Article | None:
        raise NotImplementedError

    def image_referer(self, page_url: str) -> str | None:
        return None

    def looks_dead(self, title: str, text: str) -> bool:
        """页面是否是无正文的死页（404/登录墙/验证页）。默认不判断。"""
        return False


def _parse(html: str):
    from lxml import html as lh

    try:
        return lh.fromstring(html)
    except Exception:  # noqa: BLE001
        return None


def _first_text(doc, selector: str) -> str | None:
    try:
        nodes = doc.cssselect(selector)
    except Exception:  # noqa: BLE001
        return None
    for n in nodes:
        tag = n.tag.lower() if isinstance(n.tag, str) else ""
        if tag in ("meta", "title"):
            t = (n.get("content") or "").strip()
        else:
            t = (n.text_content() or "").strip()
        if t:
            return t
    return None


def _clean_el(el) -> None:
    """移除元素内 script/style/nav 等噪音节点（原地修改）。"""
    for tag in ("script", "style", "iframe", "noscript", "form", "button", "nav"):
        for n in el.iter(tag):
            p = n.getparent()
            if p is not None:
                p.remove(n)


def _collect_images(el) -> list[dict]:
    out: list[dict] = []
    for img in el.iter("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or src.startswith("data:"):
            continue
        out.append({"src": src, "alt": img.get("alt") or ""})
    return out


# ---------------------------------------------------------------------------
# 微信公众号
# ---------------------------------------------------------------------------

class WechatAdapter(SiteAdapter):
    platform = "wechat"
    hosts = ("mp.weixin.qq.com",)
    site_name = "微信公众号"

    def extract(self, html: str, url: str) -> Article | None:
        doc = _parse(html)
        if doc is None:
            return None
        title = _first_text(doc, "#activity-name")
        nodes = doc.cssselect("#js_content")
        if not title or not nodes:
            return None
        content = nodes[0]
        _clean_el(content)
        text = content.text_content().strip()
        if len(text) < 40:
            return None  # 空壳/风控（如"环境异常"页）
        author = _first_text(doc, "#js_name") or _first_text(doc, ".rich_media_meta_nickname")
        date = _first_text(doc, "#publish_time")
        from lxml import html as lh

        content_html = lh.tostring(content, encoding="unicode")
        return Article(
            title=title, url=url, site_name=self.site_name, author=author, date=date,
            content_html=content_html, content_text=html_to_markdown(content_html),
            images=_collect_images(content),
        )

    def image_referer(self, page_url: str) -> str | None:
        return "https://mp.weixin.qq.com/"


# ---------------------------------------------------------------------------
# 知乎（hydration JSON 优先，SSR 兜底）
# ---------------------------------------------------------------------------

class ZhihuAdapter(SiteAdapter):
    platform = "zhihu"
    hosts = ("zhihu.com", "zhuanlan.zhihu.com")
    site_name = "知乎"

    # 页面正文可能混入点赞/评论等 UI 噪音。
    _NOISE_RE = re.compile(
        r"(\d[\d,]*\s*人(赞同了该回答|评论了该回答|收藏了该回答|关注了该问题|浏览了该问题)"
        r"|收起|展开|写回答|邀请回答|分享|举报|推荐排序|默认排序|按时间排序)"
    )

    def clean_noise(self, text: str) -> str:
        return self._NOISE_RE.sub("", text or "").strip()

    def extract(self, html: str, url: str) -> Article | None:
        m = re.search(r'<script[^>]*id="js-initialData"[^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                data = None
            if data:
                art = self._from_hydration(data, url)
                if art:
                    return art
        return self._from_ssr(html, url)

    def _from_hydration(self, data: dict, url: str) -> Article | None:
        ents = ((data.get("initialState") or {}).get("entities")) or {}
        # 问题标题在 questions 实体（字符串）；answers 的 title 是嵌套 dict，取不到
        title = None
        for _eid, e in (ents.get("questions") or {}).items():
            if isinstance(e, dict) and e.get("title"):
                title = str(e["title"]).strip()
                break
        for coll in ("articles", "answers", "posts", "pins"):
            for _eid, e in (ents.get(coll) or {}).items():
                if not isinstance(e, dict):
                    continue
                content = self._entity_content(e.get("content"))
                text = strip_tags(content)
                if len(text) < 200:
                    continue
                art_title = (e.get("title") or "").strip() or title
                author = None
                au = e.get("author")
                if isinstance(au, dict):
                    author = au.get("name")
                created = e.get("createdTime") or e.get("created_time") or e.get("updatedTime") or e.get("updated_time")
                doc = _parse(content)
                return Article(
                    title=art_title or "知乎内容", url=url, site_name=self.site_name,
                    author=author, date=str(created) if created else None,
                    content_html=content, content_text=html_to_markdown(content),
                    images=_collect_images(doc) if doc is not None else [],
                )
        return None

    @staticmethod
    def _entity_content(content) -> str:
        """知乎 hydration 的 content 可能是 HTML 字符串或段落对象数组，统一转 HTML。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for c in content:
                if isinstance(c, str):
                    parts.append(f"<p>{c}</p>")
                elif isinstance(c, dict):
                    p = c.get("paragraph") or c.get("content") or c.get("text") or c.get("quote") or ""
                    if p:
                        parts.append(f"<p>{p}</p>")
            return "".join(parts)
        return ""

    def _from_ssr(self, html: str, url: str) -> Article | None:
        doc = _parse(html)
        if doc is None:
            return None
        for sel in (".Post-RichText", ".RichContent-inner", ".RichText"):
            nodes = doc.cssselect(sel)
            if nodes:
                content = nodes[0]
                _clean_el(content)
                text = content.text_content().strip()
                if len(text) < 100:
                    continue
                title = _first_text(doc, "h1") or _first_text(doc, ".Post-Title") or _first_text(doc, 'meta[property="og:title"]')
                from lxml import html as lh

                content_html = lh.tostring(content, encoding="unicode")
                return Article(
                    title=title or "知乎内容", url=url, site_name=self.site_name,
                    content_html=content_html, content_text=html_to_markdown(content_html),
                    images=_collect_images(content),
                )
        return None

    def image_referer(self, page_url: str) -> str | None:
        return "https://www.zhihu.com/"


# ---------------------------------------------------------------------------
# 今日头条（SSR hydration JSON 优先）
# ---------------------------------------------------------------------------

class ToutiaoAdapter(SiteAdapter):
    platform = "toutiao"
    hosts = ("toutiao.com",)
    site_name = "今日头条"

    def extract(self, html: str, url: str) -> Article | None:
        art = self._from_hydrated(html, url)
        if art:
            return art
        doc = _parse(html)
        if doc is None:
            return None
        for sel in ("article", "#article"):
            nodes = doc.cssselect(sel)
            if nodes:
                content = nodes[0]
                _clean_el(content)
                text = content.text_content().strip()
                if len(text) < 100:
                    continue
                from lxml import html as lh

                content_html = lh.tostring(content, encoding="unicode")
                return Article(
                    title=_first_text(doc, "h1") or "今日头条", url=url, site_name=self.site_name,
                    content_html=content_html, content_text=html_to_markdown(content_html),
                    images=_collect_images(content),
                )
        return None

    def _from_hydrated(self, html: str, url: str) -> Article | None:
        i = html.find("window._SSR_HYDRATED_DATA")
        if i < 0:
            return None
        start = html.find("{", i)
        if start < 0:
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(html[start:])
        except (json.JSONDecodeError, ValueError):
            return None
        info = (obj or {}).get("articleInfo") or {}
        content = info.get("content") or ""
        if len(strip_tags(content)) < 100:
            return None
        title = (info.get("title") or "").strip() or "今日头条"
        author = None
        au = info.get("author")
        if isinstance(au, dict):
            author = au.get("name")
        doc = _parse(content)
        return Article(
            title=title, url=url, site_name=self.site_name, author=author,
            date=info.get("publishTime") or info.get("publish_time"),
            content_html=content, content_text=html_to_markdown(content),
            images=_collect_images(doc) if doc is not None else [],
        )

    def image_referer(self, page_url: str) -> str | None:
        return "https://www.toutiao.com/"


# ---------------------------------------------------------------------------
# X 推文（oEmbed 免登录优先，meta 兜底）
# ---------------------------------------------------------------------------

class XAdapter(SiteAdapter):
    platform = "x"
    hosts = ("x.com", "twitter.com")
    site_name = "X"

    def direct_source(self, url: str) -> str | None:
        # oEmbed 免登录（讨论 Part 1 的推荐路径）
        return f"https://publish.twitter.com/oembed?url={url}&omit_script=true"

    def extract(self, html: str, url: str) -> Article | None:
        art = self._from_oembed(html, url)
        if art:
            return art
        return self._from_meta(html, url)

    def _from_oembed(self, html: str, url: str) -> Article | None:
        try:
            data = json.loads(html)
        except (json.JSONDecodeError, ValueError):
            return None
        embed = data.get("html") if isinstance(data, dict) else None
        if not embed:
            return None
        text = strip_tags(embed).strip()
        if not text:
            return None
        author = data.get("author_name")
        return Article(
            title=f"推文 · {author}" if author else "推文", url=url, site_name=self.site_name,
            author=author, content_html=embed, content_text=text,
        )

    def _from_meta(self, html: str, url: str) -> Article | None:
        doc = _parse(html)
        if doc is None:
            return None
        desc = _first_text(doc, 'meta[name="description"]')
        if not desc:
            return None
        title = _first_text(doc, 'meta[property="og:title"]') or f"推文 · {url}"
        images = []
        og = _first_text(doc, 'meta[property="og:image"]')
        if og:
            images.append({"src": og, "alt": ""})
        return Article(
            title=title, url=url, site_name=self.site_name,
            content_html=f"<p>{desc}</p>", content_text=desc, images=images,
        )

    def image_referer(self, page_url: str) -> str | None:
        return "https://x.com/"


# ---------------------------------------------------------------------------
# 小红书（响应 HTML 中的 hydration JSON）
# ---------------------------------------------------------------------------

_XHS_STATE_MARKERS = ("window.__INITIAL_STATE__", "window.__INITIAL_SSR_STATE__")


def parse_xhs_initial_state(html: str) -> dict | None:
    """Extract the bounded XHS hydration object without evaluating page JavaScript."""
    for marker in _XHS_STATE_MARKERS:
        i = html.find(marker)
        if i < 0:
            continue
        start = html.find("{", i + len(marker))
        if start < 0:
            continue
        try:
            cleaned = re.sub(r":\s*undefined\b", ":null", html[start:])
            obj, _ = json.JSONDecoder().raw_decode(cleaned)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _xhs_note_id(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except (TypeError, ValueError):
        return None
    path = [part for part in parts.path.split("/") if part]
    for marker in ("explore", "item"):
        if marker in path and path.index(marker) + 1 < len(path):
            return path[path.index(marker) + 1]
    return None


def find_xhs_note(state: dict, url: str) -> dict | None:
    """Find only the note named by the requested/final URL; never pick a feed item."""
    wanted = _xhs_note_id(url)
    if not wanted:
        return None
    stack: list[object] = [state]
    seen: set[int] = set()
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            marker = id(value)
            if marker in seen:
                continue
            seen.add(marker)
            note = value.get("note")
            if isinstance(note, dict) and str(note.get("noteId") or "") == wanted:
                return note
            direct = value.get(wanted)
            if isinstance(direct, dict):
                candidate = direct.get("note", direct)
                if isinstance(candidate, dict):
                    note_id = str(candidate.get("noteId") or wanted)
                    if note_id == wanted:
                        return candidate
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return None


def normalize_xhs_resource_url(value: str) -> str:
    """Normalize XHS CDN URLs before the strict HTTPS/domain validation step."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://"):
        return "https://" + value[len("http://"):]
    return value


class XhsAdapter(SiteAdapter):
    platform = "xiaohongshu"
    hosts = ("xiaohongshu.com",)
    site_name = "小红书"

    def extract(self, html: str, url: str) -> Article | None:
        data = self._initial_state(html)
        if not data:
            return None
        note = find_xhs_note(data, url)
        if not note:
            return None
        title = (note.get("title") or "").strip()
        desc = (note.get("desc") or "").strip()
        images = self._image_urls(note)
        if not title and not desc and not images:
            return None
        author = None
        user = note.get("user")
        if isinstance(user, dict):
            author = user.get("nickname")
        body = f"<p>{title}</p><p>{desc}</p>" if (title or desc) else ""
        return Article(
            title=title or "小红书笔记", url=url, site_name=self.site_name, author=author,
            content_html=body, content_text=f"{title}\n\n{desc}".strip(),
            images=images,
        )

    @staticmethod
    def _image_urls(note: dict) -> list[dict]:
        """imageList[].url 是带真实 CDN 地址的字段（含裁剪后缀）；
        urlDefault 在无签名时会返回 32x32 占位图，仅作兜底。

        多 scene（WB_PRV/WB_DFT/WB_FHD）取最高清晰度并去重。
        """
        out: list[dict] = []
        seen: set[str] = set()
        scene_rank = {"WB_PRV": 0, "WB_DFT": 1, "WB_FHD": 2}
        for img in note.get("imageList") or []:
            if not isinstance(img, dict):
                continue
            candidates = []
            for it in img.get("infoList") or []:
                if isinstance(it, dict) and it.get("url"):
                    candidates.append((scene_rank.get(it.get("imageScene"), 0), it["url"]))
            candidates.sort(key=lambda x: x[0])
            if candidates:
                src = normalize_xhs_resource_url(candidates[-1][1])  # 最高清 scene
            else:
                src = normalize_xhs_resource_url(img.get("url") or img.get("urlDefault") or "")
            if src and src not in seen:
                seen.add(src)
                out.append({"src": src, "alt": ""})
        if not out:
            for img in note.get("imageList") or []:
                src = normalize_xhs_resource_url(img.get("urlDefault") or "") if isinstance(img, dict) else ""
                if src and src not in seen:
                    seen.add(src)
                    out.append({"src": src, "alt": ""})
        return out

    @staticmethod
    def _initial_state(html: str) -> dict | None:
        return parse_xhs_initial_state(html)

    def looks_dead(self, title: str, text: str) -> bool:
        """小红书无 xsec_token 的链接会落到"页面不存在"死页（标题与导航文案固定）。"""
        dead_titles = ("你访问的页面不见了", "页面不存在", "无法访问", "抱歉，页面不存在")
        if any(t in title for t in dead_titles):
            return True
        # 死页正文 = 全站导航（发现/直播/发布/通知/消息 + 首页推荐），无笔记内容特征
        head = text[:300]
        if "你访问的页面不见了" in head or ("页面不存在" in head and "发布" in head and "发现" in head):
            return True
        return False

    def image_referer(self, page_url: str) -> str | None:
        return "https://www.xiaohongshu.com/"


# ---------------------------------------------------------------------------
# 微博（移动端 $render_data / 桌面端 hydration / 头条文章 / DOM 兜底）
# 微博文章提取器：基于 lxml，实现 SiteAdapter 契约。
# ---------------------------------------------------------------------------

_WEIBO_STATE_PATTERNS = (
    re.compile(r"var\s+\$render_data\s*=\s*(\[.*?\])\[0\]", re.S),
    re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", re.S),
)
_WEIBO_TITLE_CHARS = 50


def _weibo_title(text: str) -> str:
    clean = re.sub(r"\s+", " ", strip_tags(text)).strip()
    if len(clean) > _WEIBO_TITLE_CHARS:
        return clean[:_WEIBO_TITLE_CHARS] + "…"
    return clean or "微博"


def _weibo_status(data) -> dict | None:
    """Return the status dict from a parsed ``$render_data`` / initial-state payload."""
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else None
    if not isinstance(data, dict):
        return None
    status = data.get("status") if isinstance(data.get("status"), dict) else data
    if not isinstance(status, dict) or not (status.get("text") or status.get("longTextContent")):
        return None
    return status


class WeiboAdapter(SiteAdapter):
    platform = "weibo"
    hosts = ("weibo.com", "weibo.cn")
    site_name = "微博"

    def extract(self, html: str, url: str) -> Article | None:
        if "/ttarticle/p/show" in url:
            art = self._from_long_article(html, url)
            if art:
                return art
        status = self._status_from_scripts(html)
        if status:
            return self._from_status(status, url)
        return self._from_dom(html, url)

    @staticmethod
    def _status_from_scripts(html: str) -> dict | None:
        for pattern in _WEIBO_STATE_PATTERNS:
            for match in pattern.finditer(html or ""):
                try:
                    data = json.loads(match.group(1))
                except (json.JSONDecodeError, ValueError):
                    continue
                status = _weibo_status(data)
                if status:
                    return status
        return None

    def _from_status(self, status: dict, url: str) -> Article | None:
        text_html = status.get("longTextContent") if status.get("isLongText") else None
        text_html = text_html or status.get("text") or ""
        user = status.get("user") if isinstance(status.get("user"), dict) else {}
        author = user.get("screen_name") or None
        date = None
        created = status.get("created_at")
        if isinstance(created, str) and created:
            from datetime import datetime

            try:
                date = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").isoformat()
            except ValueError:
                date = created
        images: list[dict] = []
        for pic in status.get("pics") or []:
            if not isinstance(pic, dict):
                continue
            large = pic.get("large") if isinstance(pic.get("large"), dict) else {}
            src = large.get("url") or pic.get("url")
            if src:
                images.append({"src": src, "alt": ""})
        content_html = f"<p>{text_html}</p>" + "".join(f'<img src="{i["src"]}" />' for i in images)
        markdown = html_to_markdown(f"<div><p>{text_html}</p></div>").strip()
        if not markdown:
            return None
        return Article(
            title=_weibo_title(text_html), url=url, site_name=self.site_name, author=author,
            date=date, content_html=content_html, content_text=markdown, images=images,
        )

    def _from_long_article(self, html: str, url: str) -> Article | None:
        doc = _parse(html)
        if doc is None:
            return None
        bodies = doc.cssselect('[node-type="contentBody"]')
        if not bodies:
            return None
        body = bodies[0]
        _clean_el(body)
        from lxml import html as lh

        content_html = lh.tostring(body, encoding="unicode")
        markdown = html_to_markdown(content_html).strip()
        if len(markdown) < 20:
            return None
        title = _first_text(doc, '[node-type="articleTitle"]') or _weibo_title(body.text_content())
        author = None
        m = re.search(r"\$CONFIG\['onick'\]\s*=\s*'([^']+)'", html)
        if m:
            author = m.group(1).strip() or None
        date = None
        info = _first_text(doc, ".authorinfo")
        if info:
            dm = re.search(r"(20\d{2}-\d{2}-\d{2})", info)
            date = dm.group(1) if dm else None
        return Article(
            title=title, url=url, site_name=self.site_name, author=author, date=date,
            content_html=content_html, content_text=markdown, images=_collect_images(body),
        )

    def _from_dom(self, html: str, url: str) -> Article | None:
        doc = _parse(html)
        if doc is None:
            return None
        nodes = doc.cssselect(".weibo-text") or doc.cssselect('[class*="detail_wbtext"]')
        if not nodes:
            return None
        content = nodes[0]
        _clean_el(content)
        from lxml import html as lh

        content_html = lh.tostring(content, encoding="unicode")
        markdown = html_to_markdown(f"<div>{content_html}</div>").strip()
        if not markdown:
            return None
        author = _first_text(doc, ".profile-nick") or _first_text(doc, ".m-text-cut")
        return Article(
            title=_weibo_title(content.text_content()), url=url, site_name=self.site_name,
            author=author, content_html=content_html, content_text=markdown,
            images=_collect_images(content),
        )

    def looks_dead(self, title: str, text: str) -> bool:
        probe = f"{title}\n{text[:400]}"
        return any(marker in probe for marker in ("微博不存在", "该微博已被删除", "暂无查看权限"))

    def image_referer(self, page_url: str) -> str | None:
        return "https://weibo.com/"


# ---------------------------------------------------------------------------
# 通用兜底（trafilatura）
# ---------------------------------------------------------------------------

class GenericTrafilaturaAdapter(SiteAdapter):
    platform = "generic_article"
    hosts = ()
    site_name = ""

    def extract(self, html: str, url: str) -> Article | None:
        import trafilatura

        try:
            md = trafilatura.extract(
                html, url=url, output_format="markdown", with_metadata=True, favor_recall=True
            )
        except Exception as e:  # noqa: BLE001
            log.warning("trafilatura 提取失败: %s", e)
            return None
        if not md:
            return None
        # 去掉 with_metadata 附加的 YAML frontmatter，并从中取元数据（trafilatura 2.2
        # extract_metadata 无 url 参数，直接解析 frontmatter 更稳）
        title = author = date = None
        m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
        if m:
            fm = m.group(1)
            md = md[m.end():]

            def _fm(key: str) -> str | None:
                mm = re.search(rf"^{key}:\s*(.+?)\s*$", fm, re.M)
                if not mm:
                    return None
                return mm.group(1).strip().strip('"').strip("'") or None

            title = _fm("title")
            author = _fm("author")
            date = _fm("date")
        if not md.strip():
            return None
        images = [{"src": u, "alt": a} for a, u in IMG_MD_RE.findall(md)]
        return Article(
            title=title or "untitled",
            url=url, site_name=host_of(url) or "网页",
            author=author, date=date,
            content_html="", content_text=md.strip(), images=images,
        )

    def image_referer(self, page_url: str) -> str | None:
        p = urlparse(page_url)
        return f"{p.scheme}://{p.netloc}/" if p.netloc else None


ADAPTERS: list[SiteAdapter] = [
    WechatAdapter(), ZhihuAdapter(), ToutiaoAdapter(), XAdapter(), XhsAdapter(), WeiboAdapter(),
    GenericTrafilaturaAdapter(),
]


def find_adapter(url: str) -> SiteAdapter:
    for a in ADAPTERS:
        if a.match(url):
            return a
    return ADAPTERS[-1]


def article_platform(url: str) -> str:
    return find_adapter(url).platform
