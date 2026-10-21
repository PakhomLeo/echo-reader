"""Session Bridge 联调演示（P2a）：本地服务端 + Chrome 插件会话下载。

用法：
    python session_bridge_demo.py <url> [-a mp3]

流程：
    1. 启动本地 SessionBridgeServer
    2. 等你在 Chrome 中加载 browser-extension/ 并授权域名
    3. 连接成功后，用插件会话下载视频
"""

import argparse
import asyncio
import logging
import sys

from medialab import DownloadService
from medialab.bridge import SessionBridgeServer, wait_for_plugin
from medialab.session import ExtensionSessionProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main(url: str, audio: str | None):
    server = SessionBridgeServer()
    server.start()
    print("\n" + "=" * 60)
    print("  请在 Chrome 加载 browser-extension/ 并点击扩展图标")
    print("=" * 60 + "\n")

    if not wait_for_plugin(server, timeout=600):
        print("等待插件连接超时（10 分钟），请确认扩展图标状态为已连接")
        server.stop()
        sys.exit(1)
    print("插件已连接，开始下载…\n")

    svc = DownloadService(session_provider=ExtensionSessionProvider(server))
    await svc.initialize()
    t = await svc.download(url, audio=audio, wait=True)
    q = await svc.query(t.id)
    if q["status"] != "done":
        print("任务没有自动重提；修复授权或登录状态后，请由用户重新执行命令。")
    print(f"\n任务 {q['id']}: {q['status']} (error={q.get('error_code')} {str(q.get('error_detail'))[:120]})")
    for f in q["files"]:
        print(f"  [{f['kind']}] {f['path']}")
    await svc.close()
    server.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("-a", "--audio", choices=["mp3", "m4a", "aac", "flac", "wav", "opus", "ogg"])
    args = parser.parse_args()
    asyncio.run(main(args.url, args.audio))
