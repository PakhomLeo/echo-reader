"""bridge.py — 本地 Session Bridge 服务端（P2a 闭环）。

Chrome 扩展（browser-extension/）是 WebSocket 客户端，主动连接本地服务；
本模块提供常驻监听的服务端：
- 校验 Chrome 扩展 Origin，并为每个连接下发仅存内存的随机 token
- 管理插件连接（断线重连由插件侧负责）
- medialab 通过进程内 request_session()/request_mint() 方法发起
  会话与页面请求，由服务端转发给插件并等待响应

依赖：websocket-server（可选包，`pip install "medialab[bridge]"`）。

websocket-server 是可选依赖，因此模块导入时不加载它；真正起服务时才导入。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger("medialab.bridge")

DEFAULT_PORT = 19876
REQUEST_TIMEOUT = 20.0
_EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}\Z")


def _validated_extension_origin(values) -> str | None:
    """Return one canonical Chrome-extension Origin, rejecting every ambiguity."""
    if not isinstance(values, (list, tuple)) or len(values) != 1:
        return None
    origin = values[0]
    if not isinstance(origin, str) or not _EXTENSION_ORIGIN.fullmatch(origin):
        return None
    return origin


def _valid_pot_path(request_path: str, expected_token: str) -> bool:
    """Validate the private loopback POT-provider route without ambiguity."""
    try:
        path = urlsplit(request_path)
        query = parse_qs(path.query, keep_blank_values=True)
        supplied = query.get("token", [])
        return (
            path.path in ("", "/")
            and query.get("role") == ["pot"]
            and set(query) == {"role", "token"}
            and len(supplied) == 1
            and bool(expected_token)
            and secrets.compare_digest(supplied[0], expected_token)
        )
    except (TypeError, ValueError):
        return False


def _path_aware_handler():
    """websocket-server 0.6.4 adapter with strict Origin handshake validation."""
    from websocket_server.websocket_server import WebSocketHandler

    class _PathAwareHandler(WebSocketHandler):
        request_path: str = ""
        request_headers_multi: dict[str, list[str]]
        extension_origin: str | None = None
        connection_role: str | None = None

        def read_http_headers(self):
            headers = {}
            multi: dict[str, list[str]] = {}
            self.request_headers_multi = multi
            try:
                http_get = self.rfile.readline().decode("ascii").strip()
            except (OSError, UnicodeDecodeError, ValueError):
                self.keep_alive = False
                self.request_path = ""
                return headers
            request_parts = http_get.split(" ")
            valid_request = (
                len(request_parts) == 3
                and request_parts[0] == "GET"
                and request_parts[2] == "HTTP/1.1"
                and bool(request_parts[1])
            )
            self.request_path = request_parts[1] if valid_request else ""
            if not valid_request:
                self.keep_alive = False
                self.request_headers_multi = multi
                return headers
            while True:
                try:
                    header = self.rfile.readline().decode("ascii").strip()
                except (OSError, UnicodeDecodeError, ValueError):
                    self.keep_alive = False
                    break
                if not header:
                    break
                if ":" not in header:
                    self.keep_alive = False
                    continue
                head, value = header.split(":", 1)
                key = head.lower().strip()
                clean_value = value.strip()
                if not key:
                    self.keep_alive = False
                    continue
                multi.setdefault(key, []).append(clean_value)
                headers[key] = clean_value
            self.request_headers_multi = multi
            return headers

        def handshake(self):
            headers = self.read_http_headers()
            extension_candidate = self.request_path == "/"
            self.extension_origin = _validated_extension_origin(
                self.request_headers_multi.get("origin", [])
            ) if extension_candidate else None
            pot_token = getattr(self.server, "_medialab_pot_token", None)
            pot_candidate = isinstance(pot_token, str) and _valid_pot_path(
                self.request_path, pot_token,
            )
            self.connection_role = "extension" if self.extension_origin else (
                "pot" if pot_candidate else None
            )
            if (not self.keep_alive or self.connection_role is None
                    or headers.get("upgrade", "").lower() != "websocket"
                    or "sec-websocket-key" not in headers):
                self.keep_alive = False
                return
            response = self.make_handshake_response(headers["sec-websocket-key"])
            with self._send_lock:
                self.handshake_done = self.request.send(response.encode())
            self.valid_client = True
            self.server._new_client_(self)

        def read_bytes(self, num):
            """覆写：原实现单次 rfile.read(num) 在 TCP 分片时读到不完整数据
            （大消息如文章 HTML ~130KB+ 会截断 → utf-8 解码失败 → bad_response）。
            循环读取直到读满 num 字节或 EOF。"""
            chunks = []
            remaining = num
            while remaining > 0:
                try:
                    chunk = self.rfile.read(remaining)
                except (OSError, ValueError):  # 超时/连接中断：交给外层 keep_alive 处理
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

        def read_next_message(self):
            """完整覆写：原库实现不支持：
            1) continuation 帧（Chrome 会把大消息分片发送，>16KB 消息必触发）
            2) 大 payload 的可靠读取
            这里实现帧聚合：TEXT/BINARY 起始帧 + CONTINUATION 续帧累加，fin=1 时交付。
            """
            import struct as _struct

            buf = bytearray()
            while True:
                head = self.read_bytes(2)
                if len(head) < 2:
                    self.keep_alive = False
                    return
                b1, b2 = head[0], head[1]
                opcode = b1 & 0x0F
                masked = b2 & 0x80
                length = b2 & 0x7F
                if opcode == 0x8:  # close
                    self.keep_alive = False
                    return
                if not masked:
                    self.keep_alive = False
                    return
                if length == 126:
                    ext = self.read_bytes(2)
                    if len(ext) < 2:
                        self.keep_alive = False
                        return
                    length = _struct.unpack(">H", ext)[0]
                elif length == 127:
                    ext = self.read_bytes(8)
                    if len(ext) < 8:
                        self.keep_alive = False
                        return
                    length = _struct.unpack(">Q", ext)[0]
                masks = self.read_bytes(4)
                if len(masks) < 4:
                    self.keep_alive = False
                    return
                payload = bytearray()
                data = self.read_bytes(length)
                for i, b in enumerate(data):
                    payload.append(b ^ masks[i % 4])
                if opcode == 0x9:  # ping → 回 pong
                    self.send_pong(bytes(payload))
                    continue
                if opcode == 0xA:  # pong
                    continue
                if opcode == 0x0:  # continuation
                    buf.extend(payload)
                elif opcode in (0x1, 0x2):  # text/binary 起始帧
                    buf = payload
                else:
                    continue
                if b1 & 0x80:  # fin：消息完整
                    try:
                        text = bytes(buf).decode("utf-8")
                    except UnicodeDecodeError:
                        log.warning("WS 消息解码失败（%d 字节）", len(buf))
                        self.keep_alive = False
                        return
                    self.server._message_received_(self, text)
                    return

    return _PathAwareHandler


class SessionBridgeError(RuntimeError):
    """桥接层错误（Origin/连接认证失败、无插件连接、请求超时等）。"""


class _PendingRequest:
    def __init__(self, reply_to=None, req_id=None):
        self.event = threading.Event()
        self.result: dict | None = None
        self.error: str | None = None
        # req_id：请求消息携带的随机 id，响应必须回显匹配（防止 ping 等无关回复被误认）
        self.req_id = req_id
        # 非 None 时表示请求来自外部 WS 客户端（POT provider）：
        # 响应到达后直接回写给该客户端，而不是交给进程内等待者
        self.reply_to = reply_to


class SessionBridgeServer:
    """本地 WS 服务端：接收插件连接，转发会话、页面捕获和渲染请求。

    用法：
        server = SessionBridgeServer()
        server.start()          # 启动后台监听线程
        resp = server.request_session(url, job_id, domains)
        server.stop()
    """

    def __init__(self):
        self.port = DEFAULT_PORT
        self.host = "127.0.0.1"
        self._server = None
        self._thread: threading.Thread | None = None
        self._client = None  # 当前扩展连接（简单起见只维护一个）
        self._client_token: str | None = None
        self._pot_token = ""
        self._pot_clients: dict = {}
        # 进程内/外部等待中的请求：按 req_id 索引，支持并发请求（下载服务默认 3 并发）
        self._pending: dict[str, _PendingRequest] = {}
        self._lock = threading.Lock()
        self._ping_stop = threading.Event()
        self._ping_thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._client is not None

    # ---------- 生命周期 ----------

    def start(self):
        try:
            from websocket_server.websocket_server import WebsocketServer
        except ImportError as e:
            raise RuntimeError("SessionBridge 需要可选依赖：pip install 'medialab[bridge]'") from e

        self._pot_token = secrets.token_urlsafe(32)
        self._server = WebsocketServer(host=self.host, port=self.port, loglevel=logging.WARNING)
        self._server._medialab_pot_token = self._pot_token
        self._server.RequestHandlerClass = _path_aware_handler()  # 捕获请求路径用于 token 校验
        self._server.set_fn_new_client(self._on_new_client)
        self._server.set_fn_message_received(self._on_message)
        self._server.set_fn_client_left(self._on_client_left)
        self._thread = threading.Thread(target=self._server.run_forever, daemon=True)
        self._thread.start()
        os.environ["MEDIALAB_BRIDGE_POT_TOKEN"] = self._pot_token
        os.environ["MEDIALAB_BRIDGE_POT_HOST"] = self.host
        os.environ["MEDIALAB_BRIDGE_POT_PORT"] = str(self.port)
        # 每 5s ping 一次：探测 SW 挂起/断网导致的半死连接
        self._ping_stop = threading.Event()
        self._ping_thread = threading.Thread(
            target=self._ping_loop, args=(self._ping_stop,), daemon=True,
        )
        self._ping_thread.start()
        log.info("SessionBridge 已启动: ws://%s:%s", self.host, self.port)

    @staticmethod
    def _wake_pending(pendings, error: str):
        for pending in pendings:
            pending.error = error
            pending.event.set()

    def _ping_loop(self, stop_event: threading.Event):
        while not stop_event.wait(5):
            with self._lock:
                client = self._client
                token = self._client_token
            if client is None:
                continue
            try:
                self._server.send_message(client, json.dumps({"type": "ping", "token": token}))
            except Exception:  # noqa: BLE001 半死连接：清除，等待插件重连
                log.warning("ping 失败，清除失联连接（等待插件重连）")
                pendings = []
                with self._lock:
                    if self._client is client:
                        self._client = None
                        self._client_token = None
                        pendings = list(self._pending.values())
                        self._pending.clear()
                self._wake_pending(pendings, "plugin_disconnected")

    def stop(self):
        self._ping_stop.set()
        if self._ping_thread is not None:
            self._ping_thread.join(timeout=1.0)
            self._ping_thread = None
        if self._server:
            try:
                self._server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        self._thread = None
        with self._lock:
            self._client = None
            self._client_token = None
            self._pot_clients.clear()
            pendings = list(self._pending.values())
            self._pending.clear()
        self._wake_pending(pendings, "plugin_disconnected")
        owns_environment = os.environ.get("MEDIALAB_BRIDGE_POT_TOKEN") == self._pot_token
        if owns_environment:
            os.environ.pop("MEDIALAB_BRIDGE_POT_TOKEN", None)
            if os.environ.get("MEDIALAB_BRIDGE_POT_HOST") == self.host:
                os.environ.pop("MEDIALAB_BRIDGE_POT_HOST", None)
            if os.environ.get("MEDIALAB_BRIDGE_POT_PORT") == str(self.port):
                os.environ.pop("MEDIALAB_BRIDGE_POT_PORT", None)

    # ---------- 插件连接 ----------

    def _on_new_client(self, client, server):
        handler = client.get("handler")
        role = getattr(handler, "connection_role", None) if handler else None
        if role == "pot":
            with self._lock:
                self._pot_clients[client["id"]] = client
            log.info("本机 POT provider 已连接")
            return
        origin = getattr(handler, "extension_origin", None) if handler else None
        if role != "extension" or not _validated_extension_origin([origin] if origin else []):
            log.warning("拒绝无效 Chrome 扩展 Origin 的连接")
            try:
                handler.send_close(1008, "invalid origin")
                self._server._terminate_client_handler(handler)
            except Exception:  # noqa: BLE001
                pass
            return
        connection_token = secrets.token_urlsafe(32)
        ready_failed = False
        with self._lock:
            stale = self._client
            stale_pending = []
            if stale is not None and stale is not client:
                # Drain old-generation requests before the new client is visible.
                stale_pending = list(self._pending.values())
                self._pending.clear()
            self._client = None
            self._client_token = None
            try:
                self._server.send_message(client, json.dumps({
                    "type": "bridge_ready", "token": connection_token,
                }))
            except Exception:  # noqa: BLE001
                ready_failed = True
            else:
                self._client = client
                self._client_token = connection_token
        for pending in stale_pending:
            pending.error = "plugin_disconnected"
            pending.event.set()
        if stale is not None and stale is not client:
            try:
                stale_handler = stale.get("handler")
                stale_handler.send_close(1008, "connection replaced")
            except Exception:  # noqa: BLE001
                pass
        if ready_failed:
            try:
                handler.send_close(1011, "handshake failed")
            except Exception:  # noqa: BLE001
                pass
            return
        log.info("Chrome 扩展已通过 Origin 握手")

    def _on_client_left(self, client, server):
        pendings = []
        with self._lock:
            if self._client is client:
                self._client = None
                self._client_token = None
                pendings = list(self._pending.values())
                self._pending.clear()
            elif client.get("id") in self._pot_clients:
                self._pot_clients.pop(client.get("id"), None)
                return
            else:
                return
        self._wake_pending(pendings, "plugin_disconnected")

    # ---------- 请求转发 ----------

    def _on_message(self, client, server, message):
        with self._lock:
            is_pot = client.get("id") in self._pot_clients
        if is_pot:
            self._forward_external_request(client, message)
            return
        try:
            resp = json.loads(message)
        except (json.JSONDecodeError, ValueError):
            resp = None
        with self._lock:
            if (client is not self._client or not isinstance(resp, dict)
                    or resp.get("token") != self._client_token):
                return
            req_id = resp.get("req_id") or ""
            pending = self._pending.pop(req_id, None)
        if not pending:
            return
        if resp is None:
            pending.error = "bad_response"
            pending.event.set()
            return
        resp.pop("token", None)
        if pending.reply_to is not None:
            try:
                self._server.send_message(pending.reply_to, json.dumps(resp))
            except Exception:  # noqa: BLE001
                pending.error = "reply_send_failed"
        else:
            pending.result = resp
        pending.event.set()

    def _forward_external_request(self, pot_client, message):
        """Forward one locally authenticated POT request to the current extension."""
        try:
            msg = json.loads(message)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(msg, dict) or msg.get("type") != "mint_yt_token":
            return
        with self._lock:
            ext = self._client
            token = self._client_token
            if ext is None or token is None:
                self._server.send_message(pot_client, json.dumps({
                    "ok": False, "error": "extension_not_connected"}))
                return
            req_id = secrets.token_urlsafe(8)
            self._pending[req_id] = _PendingRequest(reply_to=pot_client, req_id=req_id)
        payload = {**msg, "req_id": req_id, "token": token}
        try:
            self._server.send_message(ext, json.dumps(payload))
        except Exception:  # noqa: BLE001
            with self._lock:
                self._pending.pop(req_id, None)
            self._server.send_message(pot_client, json.dumps({
                "ok": False, "error": "forward_failed"}))

    def _request(self, msg: dict, timeout: float) -> dict:
        """向插件发送任意请求消息，阻塞等待响应。"""
        with self._lock:
            client = self._client
            token = self._client_token
            if client is None or token is None:
                raise SessionBridgeError("插件未连接：请在 Chrome 加载 browser-extension/ 并确认 popup 显示已连接")
            req_id = secrets.token_urlsafe(8)
            pending = _PendingRequest(req_id=req_id)
            self._pending[req_id] = pending
        payload = json.dumps({**msg, "req_id": req_id, "token": token})
        try:
            self._server.send_message(client, payload)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._pending.pop(req_id, None)
            raise SessionBridgeError(f"向插件发送请求失败: {e}") from e
        if not pending.event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise SessionBridgeError(f"插件响应超时（{timeout}s）")
        if pending.error:
            raise SessionBridgeError(f"插件请求失败: {pending.error}")
        resp = pending.result or {}
        resp.pop("req_id", None)  # 内部字段不暴露给调用方
        return resp

    def request_session(self, url: str, job_id: str, domains: list[str], timeout: float = REQUEST_TIMEOUT) -> dict:
        """向插件发起 get_session 请求，阻塞等待响应。"""
        return self._request({
            "type": "get_session",
            "url": url,
            "job_id": job_id,
            "domains": domains,
        }, timeout)

    def request_mint(self, video_url: str, video_id: str, client: str = "mweb",
                     token_scope: str = "player", timeout: float = 60.0) -> dict:
        """向插件发起 YouTube 会话铸造（visitor_data + 尽力 PO Token），阻塞等待响应。

        返回 {ok, visitor_data, po_token, client_version, ...}；mint 失败（ok=False）时
        调用方应降级为仅 Cookie 会话。
        """
        return self._request({
            "type": "mint_yt_token",
            "video_url": video_url,
            "video_id": video_id,
            "client": client,
            "token_scope": token_scope,
        }, timeout)

# 便利函数：等待插件连接（联调提示用）
def wait_for_plugin(server: SessionBridgeServer, timeout: float = 120.0) -> bool:
    """阻塞等待插件连接；返回是否已连接。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with server._lock:
            if server._client is not None:
                return True
        time.sleep(0.5)
    return False
