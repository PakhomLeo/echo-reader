# MediaLab Session Bridge 扩展 0.3.0

这个 MV3 扩展只为本机 MediaLab 提供当前 Chrome profile 的会话凭据。它不是下载器，也不代发
目标站内容请求。

## 现役协议

扩展固定作为 WebSocket 客户端连接 `ws://127.0.0.1:19876`，无需用户填写 token。服务端验证
单一 `chrome-extension://<32位ID>` Origin 后发送 `bridge_ready`，其中的随机连接 token 只存在
Service Worker 内存，断线即清空，重连时轮换。所有业务响应都必须回显当前 token 与 `req_id`。

| 消息 | 方向 | 用途 |
|---|---|---|
| `bridge_ready` | 服务 → 扩展 | 建立本次连接的内存 token |
| `get_session` | 服务 → 扩展 | 读取请求 URL 授权域的 Cookie 与 `navigator.userAgent` |
| `mint_yt_token` | 服务 → 扩展 | 尽力读取 YouTube Visitor Data / 生成 PO Token |
| `ping` | 服务 → 扩展 | 保活，不产生业务响应 |

不存在 `fetch_page`、`render_page`、Readability、图片/视频下载或任意内容二进制传输协议。Python
使用 curl-cffi/yt-dlp 请求页面和资源。

YouTube 铸造是凭据获取例外：扩展可能创建一个后台 YouTube 标签页，在页面主世界读取
`ytcfg` 并尽力调用 WebPoClient，随后关闭标签页。它不下载目标媒体。

## 加载与升级

1. 打开 `chrome://extensions`。
2. 启用“开发者模式”。
3. 选择“加载已解压的扩展程序”。
4. 选择 `<repo-root>\components\medialab\browser-extension`。
5. 启动 `scripts\run-import-service.ps1` 或 `scripts\run-reader.ps1`。
6. 打开扩展弹窗，确认“已连接”；首次使用点击“一键授权”。

从旧版本升级源码后必须在扩展页点击一次“重新加载”。0.3.0 弹窗没有配对 token 输入框；如果
仍看到旧 token UI，说明 Chrome 加载的不是当前目录或尚未重新加载。

内置域白名单覆盖当前支持的视频和文章平台。`getWhitelist()` 会把新版本默认域与旧版本保存的
用户域取并集，因此升级后不会因为旧 `storage.local` 缺少新平台而静默失效。可在弹窗管理授权域。

## 安全边界

- 只接受 HTTPS 目标；拒绝 localhost、私网、link-local、metadata 与明显无效域。
- 每次 `get_session` 都校验请求 URL host 属于请求域集合，再检查 Chrome host permission。
- 只读取明确请求的域，不提供全量 Cookie 导出。
- Cookie 仅发送到 `127.0.0.1` 的本机 bridge；不上传云端，不写入扩展持久化存储。
- 用户授权必须由 popup 手势触发，符合 MV3 权限模型。
- Origin 校验能阻止普通网页连接，但本机 raw socket 可以伪造 Origin，所以这不是密码学身份认证。
  端口 19876 绝不能暴露到局域网或公网；安全边界是可信本机进程。

## 文件

```text
browser-extension/
├── manifest.json  # 0.3.0；storage/cookies/scripting/alarms
├── background.js  # 自动连接、会话导出、YouTube 凭据铸造
├── popup.html
├── popup.js
├── popup.css
└── icons/
```

验证：

```powershell
node --check components\medialab\browser-extension\background.js
node --check components\medialab\browser-extension\popup.js
```
