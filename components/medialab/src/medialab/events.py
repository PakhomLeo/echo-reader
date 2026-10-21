"""事件定义与分发。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

EVENT_TASK_STARTED = "task_started"
EVENT_TASK_PROGRESS = "task_progress"
EVENT_TASK_DONE = "task_done"
EVENT_TASK_FAILED = "task_failed"

EventCallback = Callable[[str, dict], None]


class Emitter:
    """事件分发：任务在工作线程（to_thread）中触发事件时，统一投递回事件循环线程，
    保证宿主回调始终在同一个线程被调用。

    loop 由 DownloadService.initialize 注入；未注入或 loop 已关闭时退化为直接调用。
    """

    def __init__(self, on_event: EventCallback | None = None):
        self._on_event = on_event
        self._loop = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind callbacks to the service owner loop before worker work starts."""
        if loop.is_closed():
            raise RuntimeError("cannot bind a closed event loop")
        self._loop = loop

    def emit(self, event: str, payload: dict):
        if self._on_event is None:
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._invoke, event, payload)
                return
            except RuntimeError:  # 竞争：loop 正在关闭
                pass
        self._invoke(event, payload)

    def _invoke(self, event: str, payload: dict):
        try:
            self._on_event(event, payload)
        except Exception:  # noqa: BLE001 宿主回调异常不影响下载
            logging.getLogger("medialab.events").exception("事件回调异常: %s", event)
