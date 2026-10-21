"""OSS 上传 / 预签名 URL / URL 验证 / 滚动清理（v2）。

对象命名：{prefix}/{YYYY-MM-DD}/{task_uuid}/audio.m4a
规范要点：
- 不用视频标题做对象路径（特殊字符/隐私/覆盖问题），原始文件名存本地元数据
- 预签名：GET、V4 签名、24h、公网 HTTPS endpoint，完整 URL（含查询参数）传给千问
- 禁用 internal 域名
- 提交前验证：状态码 200/206、Content-Length 与元数据一致、Content-Type 音频类
- 滚动清理：保留最近 max_batches 批（任务级）+ TTL 双条件

oss2 是可选依赖（`pip install "medialab[cloud]"`），模块导入时不加载；
OssClient 构造时才导入，缺包时报出明确错误。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("medialab.oss")

AUDIO_CONTENT_TYPES = ("audio/", "application/octet-stream", "video/")


@dataclass
class OssConfig:
    access_key_id: str
    access_key_secret: str
    endpoint: str
    bucket: str
    prefix: str
    region: str = "cn-beijing"

    @classmethod
    def from_config(cls, cfg: dict) -> "OssConfig | None":
        """启用规则：密钥齐全即启用（不存在独立开关）。"""
        s = cfg.get("oss", {})
        if not s.get("access_key_id") or not s.get("access_key_secret") or not s.get("bucket"):
            return None
        return cls(
            access_key_id=s["access_key_id"],
            access_key_secret=s["access_key_secret"],
            endpoint=s.get("endpoint", "https://oss-cn-beijing.aliyuncs.com"),
            bucket=s["bucket"],
            prefix=s.get("prefix", "asr-temp/"),
            region=s.get("region", "cn-beijing"),
        )


class OssClient:
    def __init__(self, conf: OssConfig):
        try:
            import oss2
        except ImportError as e:
            raise RuntimeError("OSS 功能需要可选依赖：pip install 'medialab[cloud]'") from e

        self.conf = conf
        self._auth = oss2.AuthV4(conf.access_key_id, conf.access_key_secret)
        self._bucket = oss2.Bucket(self._auth, conf.endpoint, conf.bucket, region=conf.region)
        self._oss2 = oss2

    def object_key(self, task_uuid: str, filename: str = "audio.m4a") -> str:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"{self.conf.prefix.rstrip('/')}/{day}/{task_uuid}/{filename}"

    def upload(self, local_path: str | Path, task_uuid: str, filename: str = "audio.m4a",
               content_type: str | None = None) -> str:
        """上传文件，返回 object key。content_type 缺省按扩展名推断。"""
        key = self.object_key(task_uuid, filename)
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        with open(local_path, "rb") as f:
            result = self._bucket.put_object(key, f, headers=headers)
        if result.status != 200:
            raise RuntimeError(f"OSS 上传失败: HTTP {result.status}")
        log.info("OSS 上传完成: %s", key)
        return key

    def sign_url(self, key: str, expires: int = 86400) -> str:
        """GET 预签名 URL（V4，公网 HTTPS），默认 24 小时。"""
        return self._bucket.sign_url("GET", key, expires)

    def head(self, key: str) -> dict:
        """读取对象元数据（大小/类型）。"""
        h = self._bucket.head_object(key)
        return {"size": h.content_length, "content_type": h.headers.get("Content-Type", "")}

    def delete(self, key: str):
        try:
            self._bucket.delete_object(key)
            log.info("OSS 删除: %s", key)
        except self._oss2.exceptions.OssError as e:
            log.warning("OSS 删除失败 %s: %s", key, e)

    def list_objects(self, prefix: str | None = None) -> list[tuple[str, float]]:
        """返回 (key, last_modified 时间戳) 列表；last_modified 来自列表接口，无需额外 head。

        oss2 的 last_modified 在不同版本/响应下可能是 datetime、epoch 数字或 ISO 字符串，
        统一归一化为 epoch 秒。
        """
        p = prefix or self.conf.prefix
        out: list[tuple[str, float]] = []
        for o in self._oss2.ObjectIterator(self._bucket, prefix=p):
            lm = o.last_modified
            if isinstance(lm, (int, float)):
                ts = float(lm)
            elif isinstance(lm, str):
                try:
                    ts = datetime.fromisoformat(lm.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = 0.0
            else:
                ts = lm.timestamp()
            out.append((o.key, ts))
        return out

    def cleanup(self, max_batches: int, ttl_seconds: int) -> list[str]:
        """滚动清理：按任务批保留最近 max_batches 批；超过 ttl 的批删除。

        返回删除的 key 列表。任务批 = 日期前缀下的 task_uuid 目录；
        批的时间取该批对象 last_modified 的最新值（同一天内多个任务也能正确排序）。
        """
        objs = self.list_objects()
        if not objs:
            return []
        # 提取 (task_uuid, 批次最新时间, key)；key 形如 {prefix}/{date}/{uuid}/audio.m4a
        now = time.time()
        batches: dict[str, dict] = {}
        for key, ts in objs:
            parts = key.split("/")
            if len(parts) < 4:
                continue
            date_part, task_uuid = parts[-3], parts[-2]
            batch_key = "/".join(parts[:-1])  # 批前缀
            batch = batches.setdefault(batch_key, {"uuid": task_uuid, "date": date_part, "time": 0.0, "keys": []})
            batch["keys"].append(key)
            batch["time"] = max(batch["time"], ts)
        # 按批次最新上传时间排序；TTL 用同一时间判断
        ordered = sorted(batches.values(), key=lambda b: b["time"])
        keep = max_batches
        ttl_cutoff = now - ttl_seconds
        delete_keys: list[str] = []
        for i, batch in enumerate(ordered):
            expired = batch["time"] < ttl_cutoff
            overflow = i < len(ordered) - keep
            if expired or overflow:
                for k in batch["keys"]:
                    self.delete(k)
                    delete_keys.append(k)
        return delete_keys


def verify_url(url: str, expected_size: int | None = None, timeout: float = 15.0) -> bool:
    """提交前验证签名 URL：GET+Range 请求，状态码 200/206、大小一致、Content-Type 音频类。

    注：sign_url('GET') 生成的 V4 签名只对 GET 方法有效，因此用 Range 请求而非 HEAD。
    """
    import urllib.request

    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "medialab", "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status not in (200, 206):
                log.warning("URL 验证失败: HTTP %s", r.status)
                return False
            ctype = r.headers.get("Content-Type", "")
            if ctype and not ctype.startswith(AUDIO_CONTENT_TYPES):
                log.warning("URL Content-Type 非音频: %s", ctype)
                return False
            if expected_size is not None:
                if r.status == 206:
                    content_range = r.headers.get("Content-Range", "")
                    total = content_range.rsplit("/", 1)[-1]
                    length = int(total) if total.isdigit() else None
                else:
                    length = r.headers.get("Content-Length")
                    length = int(length) if length and length.isdigit() else None
                if length is not None and length != expected_size:
                    log.warning("URL 大小不一致: 期望 %s 实际 %s", expected_size, length)
                    return False
            return True
    except Exception as e:  # noqa: BLE001
        log.warning("URL 验证异常: %s", e)
        return False
