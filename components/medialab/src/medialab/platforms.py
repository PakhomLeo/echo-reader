"""平台检测与 extractor 白名单。"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

def host_matches(host: str, domain: str) -> bool:
    """精确域名匹配：host 等于 domain 或以 .domain 结尾（子域宽匹配，不含无关域名）。"""
    return host == domain or host.endswith("." + domain)


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host_matches(host, "youtube.com") or host_matches(host, "youtu.be"):
        return "youtube"
    if host_matches(host, "douyin.com") or host_matches(host, "iesdouyin.com"):
        return "douyin"
    if host_matches(host, "bilibili.com") or host_matches(host, "b23.tv"):
        return "bilibili"
    if host_matches(host, "xiaohongshu.com"):
        return "xiaohongshu"
    if host_matches(host, "x.com") or host_matches(host, "twitter.com"):
        return "x"
    if host_matches(host, "tiktok.com"):
        return "tiktok"
    if host_matches(host, "vimeo.com"):
        return "vimeo"
    if host_matches(host, "twitch.tv"):
        return "twitch"
    if host_matches(host, "instagram.com"):
        return "instagram"
    if host_matches(host, "facebook.com") or host_matches(host, "fb.watch"):
        return "facebook"
    if host_matches(host, "mp.weixin.qq.com"):
        return "wechat"
    if host_matches(host, "zhihu.com"):
        return "zhihu"
    if host_matches(host, "toutiao.com"):
        return "toutiao"
    if host_matches(host, "weibo.com") or host_matches(host, "weibo.cn"):
        return "weibo"
    if host_matches(host, "medium.com"):
        return "medium"
    if host_matches(host, "juejin.cn"):
        return "juejin"
    if host_matches(host, "sspai.com"):
        return "sspai"
    if host_matches(host, "36kr.com"):
        return "36kr"
    if host_matches(host, "cnblogs.com"):
        return "cnblogs"
    if host_matches(host, "infoq.cn"):
        return "infoq"
    if host_matches(host, "jianshu.com"):
        return "jianshu"
    return "generic"


def allowed_extractors(cfg: dict) -> list[str]:
    return [e for e in cfg["platforms"].get("enabled", []) if e and e != "default"]


# 平台 → 会话域集合（Q10：登录/播放器/CDN 可能在不同域，授权读取按此集合）
PLATFORM_SESSION_DOMAINS: dict[str, list[str]] = {
    "bilibili": [".bilibili.com", ".bilivideo.com"],
    "douyin": [".douyin.com", ".iesdouyin.com"],
    "youtube": [".youtube.com", ".googlevideo.com", ".ytimg.com", ".google.com"],
    "xiaohongshu": [".xiaohongshu.com", ".xhscdn.com", ".sns-video-qc.xhscdn.com", ".sns-video-hw.xhscdn.com"],
    "wechat": [".mp.weixin.qq.com"],
    "zhihu": [".zhihu.com"],
    "toutiao": [".toutiao.com"],
    "weibo": [".weibo.com", ".weibo.cn"],
    "x": [".x.com", ".twitter.com"],
    "tiktok": [".tiktok.com", ".tiktokv.com", ".tiktokcdn.com", ".byteoversea.com"],
    "vimeo": [".vimeo.com"],
    "twitch": [".twitch.tv"],
    "instagram": [".instagram.com", ".facebook.com"],
    "facebook": [".facebook.com"],
    "medium": [".medium.com"],
    "juejin": [".juejin.cn"],
    "sspai": [".sspai.com"],
    "36kr": [".36kr.com"],
    "cnblogs": [".cnblogs.com"],
    "infoq": [".infoq.cn"],
    "jianshu": [".jianshu.com"],
    "generic": [],
    "generic_article": [],
}


def session_domains(platform: str, cfg: dict | None = None, *, url: str | None = None) -> list[str]:
    """平台适配器：返回需要授权的会话域集合（空 = 无需会话）。

    可配置覆盖：cfg["platforms"]["session_domains"][platform]。
    """
    if cfg:
        custom = (cfg.get("platforms") or {}).get("session_domains") or {}
        if platform in custom:
            return list(custom[platform])
    domains = PLATFORM_SESSION_DOMAINS.get(platform, PLATFORM_SESSION_DOMAINS["generic"])
    if domains or platform not in {"generic", "generic_article"} or not url:
        return list(domains)

    # Unknown sites use their exact public hostname as the cache key. Chrome
    # host permission is still required before the extension exports Cookie.
    host = (urlparse(url).hostname or "").casefold().rstrip(".")
    if not host:
        return []
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return [f".{host}"]
    return []
