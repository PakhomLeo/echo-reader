"""文章/图片抓取演示：插件只给凭据，Python curl_cffi 请求内容。

用法:
    python article_demo.py <url> [<url2> ...] [--bridge]

    --bridge   启动 SessionBridgeServer 并复用扩展会话（需 Chrome 已加载 browser-extension/）
               不加 --bridge 时：需要登录的平台会明确失败，公开文章仍使用 Chrome 146
               近似网络画像；扩展从不代发页面或资源请求。
"""

import argparse
import asyncio
import logging
import sys

from medialab import DownloadService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def on_event(event: str, payload: dict):
    print(f"[event] {event}: {payload}")


async def main(urls: list[str], use_bridge: bool):
    bridge = None
    provider = None
    if use_bridge:
        from medialab.bridge import SessionBridgeServer, wait_for_plugin
        from medialab.session import ExtensionSessionProvider

        bridge = SessionBridgeServer()
        bridge.start()
        if not wait_for_plugin(bridge, timeout=600):
            print("等待插件连接超时，请确认扩展已加载")
            bridge.stop()
            sys.exit(1)
        provider = ExtensionSessionProvider(bridge)
        print("插件已连接\n")

    svc = DownloadService(session_provider=provider, on_event=on_event)
    await svc.initialize()
    for url in urls:
        print(f"\n=== 抓取: {url} ===")
        t = await svc.submit(url, wait=True)  # 统一入口：router 自动分派视频/文章
        q = await svc.query(t.id)
        print(f"任务 {q['id']}: {q['status']} ({q['platform']}, mode={q['params'].get('mode')})")
        if q["status"] != "done":
            print(f"  失败: {str(q.get('error_detail'))[:300]}")
        for f in q["files"]:
            print(f"  [{f['kind']}] {f['path']}")
    await svc.close()
    if bridge:
        bridge.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--bridge", action="store_true", help="启动 SessionBridgeServer 用扩展会话")
    args = parser.parse_args()
    asyncio.run(main(args.urls, args.bridge))
