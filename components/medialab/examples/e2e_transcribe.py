import asyncio
import sys
from medialab.service import DownloadService


# Pass a URL of content you are allowed to download and transcribe.
URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/<BV-id>/"


async def main():
    svc = DownloadService()
    info = await svc.initialize()
    print("初始化:", info["db"])
    h = await svc.transcribe(URL, wait=True)
    task = await svc.query(h.id)
    print("状态:", task["status"], task.get("error_code"), task.get("error_detail"))
    if task["status"] == "done":
        ar = await svc.asr_result(h.id)
        print("ASR segments 数:", len(ar["segments"]))
        for s in ar["segments"][:3]:
            print(f"  [{s['start']:.2f}-{s['end']:.2f}s] {s['text']}")
        print("产物:", [(f["kind"], f["path"].split("\\")[-1]) for f in task["files"]])
    await svc.close()


asyncio.run(main())
