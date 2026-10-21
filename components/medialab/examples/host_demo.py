"""模拟宿主 app 调用 medialab（进程内库）。

用法:
    python host_demo.py <url> [<url2> ...] [-a mp3] [-f "bv*[height<=720]+ba/b"]
"""

import argparse
import asyncio
import logging

from medialab import DownloadService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def on_event(event: str, payload: dict):
    if event == "task_progress":
        d = payload
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or 0
            done = d.get("downloaded_bytes") or 0
            print(f"  [{payload['task_id']}] {done/1048576:.1f}/{total/1048576:.1f} MiB", end="\r")
    else:
        print(f"[event] {event}: {payload}")


async def main(urls: list[str], audio: str | None, fmt: str | None):
    svc = DownloadService(on_event=on_event)
    info = await svc.initialize()
    print(f"初始化: db={info['db']} ffmpeg={info['ffmpeg']}")

    handles = [await svc.download(u, audio=audio, format=fmt) for u in urls]
    for h in handles:
        task = await h.wait(svc)
        print(f"\n任务 {task['id']}: {task['status']} (error={task.get('error_code')})")
        for f in task["files"]:
            print(f"  [{f['kind']}] {f['path']} ({f['size']/1048576:.1f} MiB)")

    await svc.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="+")
    parser.add_argument("-a", "--audio", choices=["mp3", "m4a", "aac", "flac", "wav", "opus", "ogg"])
    parser.add_argument("-f", "--format")
    args = parser.parse_args()
    asyncio.run(main(args.urls, args.audio, args.format))
