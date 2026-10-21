"""medialab-bridge PO Token Provider（P3b）。

yt-dlp 的 PO Token Provider 插件：当 YouTube 需要 Player/GVS PO Token 时，
通过本地 SessionBridgeServer 请求 Chrome 扩展（browser-extension/）在
当前受信 YouTube 页面中铸造 token——复用用户受信会话，不另起浏览器。

注意：必须放在 yt_dlp_plugins/extractor/ 下（extractor 插件规范会在启动时
加载本模块，@register_provider 随之注册到 POT 注册表）。

连接信息由 SessionBridgeServer 启动时自动发布到当前进程环境，用户无需配置。

依赖：websocket-client（pip install "medialab[bridge]"）
"""

from __future__ import annotations

import json
import os

from yt_dlp.extractor.youtube.pot.provider import (
    PoTokenContext,
    PoTokenProvider,
    PoTokenProviderError,
    PoTokenRequest,
    PoTokenResponse,
    register_provider,
)

DEFAULT_PORT = 19876
DEFAULT_HOST = "127.0.0.1"
TIMEOUT = 60.0


@register_provider
class MedialabBridgePTP(PoTokenProvider):
    PROVIDER_VERSION = "0.1.0"
    BUG_REPORT_LOCATION = "https://github.com/PakhomLeo/echo-reader/issues"
    PROVIDER_NAME = "medialab-bridge"

    _SUPPORTED_CONTEXTS = (PoTokenContext.PLAYER, PoTokenContext.GVS)
    _SUPPORTED_CLIENTS = ("MWEB", "WEB")

    def is_available(self) -> bool:
        return bool(os.environ.get("MEDIALAB_BRIDGE_POT_TOKEN"))

    def _bridge_request(self, msg: dict) -> dict:
        import websocket  # noqa: F401

        host = os.environ.get("MEDIALAB_BRIDGE_POT_HOST", DEFAULT_HOST)
        port = int(os.environ.get("MEDIALAB_BRIDGE_POT_PORT", DEFAULT_PORT))
        token = os.environ["MEDIALAB_BRIDGE_POT_TOKEN"]
        url = f"ws://{host}:{port}/?role=pot&token={token}"
        ws = websocket.create_connection(url, timeout=TIMEOUT)
        try:
            ws.send(json.dumps(msg))
            return json.loads(ws.recv())
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _real_request_pot(self, request: PoTokenRequest) -> PoTokenResponse:
        client = request.internal_client_name or "mweb"
        video_url = request.video_webpage or (
            f"https://www.youtube.com/watch?v={request.video_id}" if request.video_id else ""
        )
        resp = self._bridge_request({
            "type": "mint_yt_token",
            "video_url": video_url,
            "video_id": request.video_id or "",
            "client": client,
            "token_scope": request.context.value,
        })
        if not resp.get("ok"):
            raise PoTokenProviderError(
                f"medialab-bridge mint 失败: {resp.get('error') or resp.get('detail') or 'unknown'}",
                expected=True,
            )
        po_token = resp.get("po_token")
        if not po_token:
            raise PoTokenProviderError("medialab-bridge 未返回 po_token", expected=True)
        return PoTokenResponse(po_token=po_token)
