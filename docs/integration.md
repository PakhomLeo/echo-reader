# 集成合同

## 共享资料库与 item

MediaLab 和 Echo 只通过同一个绝对 `LIBRARY_ROOT` 交换内容。资料库根的一级目录是 item；
目录中的 `manifest.json`、文章、转录、图片和媒体遵守 Echo file-format/producer-guide。

`auto` 以内容证据决定 article/media/mixed，不先按平台硬分文章或视频。一个输入 URL 派生
一个稳定 ID；标题变化不会产生新 item。产物先在资料库内 `.staging-*` 完成，验证
manifest 和引用文件后通过同卷 rename 提交。重抓会合并受保护的用户内容，再执行
old→backup、staging→target 的可回滚快照切换；不会用不完整结果覆盖已有完整 item。

## 批次和 imports schema v2

导入 API 接收 1–50 个 HTTPS URL。三个调度 worker 共享一个长生命周期 MediaLab。批次、job
和 event 在 `imports.db` schema v2 持久化，状态是 `queued`、`running`、`pending_index`、
`succeeded`、`failed`、`cancelled`、`interrupted`。只有 queued 可取消；failed/interrupted 可按
原请求 retry；已产出内容但索引失败的 inactive `pending_index` 只做 reindex。

成功产物使用 `library_key + item_path`。`item_path` 是当前资料库下的 POSIX 风格相对路径，
必须解析在 root 内；DB、result 和 timeline 不持久绝对 `out_dir`。v1 升级由 `ImportStore` 在打开
数据库时事务化完成并保留一份迁移前备份；高于当前 schema 的 DB 明确拒绝，不降级开启。

导入完成后先持久 `pending_index` 和相对 item 定位，再调 Echo rescan。只有 rescan 成功才
进入 `succeeded`。指纹重复只生成 warning 并保留条目，不自动合并或删除。

## 资料库切换

`POST /api/v1/library/switch` 是根宿主所有的事务。路径必须绝对、存在、可写、不是文件
系统根，且不与 state/cache/config/logs/tmp 互相包含。活动导入使切换返回 `409
imports_active`。

新 MediaLab 协调器先初始化；Echo 切换和扫描与 rescan 共用 scan lock；然后 `app.json` 原子
持久新 root。任一步失败会切回旧 Echo root、关闭新协调器并保留旧 AppPaths。若回滚本身
失败，返回独立的 `library_switch_rollback_failed`，不声称状态已恢复。

## URL、Cookie 和平台上下文

原始输入 URL 只在当前进程中用于真实请求和 retry。持久 URL 先去 fragment，删除 known-key
凭据/追踪 query，日志 URL 不含 query。这是明确清单，不是通用秘密检测；新平台未登记
query 仍可能被保留。

会话上下文用规范平台和凭据域集标识。`youtu.be/<id>` 仅在 YouTube 凭据/会话身份中规范为
YouTube watch URL，不改写用于内容身份的用户 URL。XHS 页面、图片和视频复用同一个任务级
会话上下文，资源 URL 先规范为 HTTPS，再进入严格域名/网络校验。

Chrome Cookie 进入 curl、Playwright 或回写 broker 前共用一个 normalization：无效 name/domain/path/
SameSite/expiration 被拒绝或规范，域和目标 host 的匹配在发送前重新检查。Cookie 快照仅在
进程内；yt-dlp 临时 Netscape 文件只位于任务 tmp 并在 finally 清理。

## 网络边界

MediaLab 页面/资源 HTTP 路径拒绝非 HTTPS、userinfo、非 443、IP literal、localhost/私网 host，
并限制资源域。开启安全解析的客户端不让 curl 自动跟随重定向：最多六次传输，每跳先校验
新 URL，要求 DNS 答案全是 global IP，再用 transfer-scoped `CURLOPT_RESOLVE` 将实际连接固定到
该次预验证答案，禁止连接复用。页面和资源两条路径都如此。

curl session 设置 `trust_env=False`。显式代理或环境代理无法同时保证 DNS/连接目标一致，
因此安全固定模式在发请求前 fail closed，不会退回“仅检查 DNS”或不安全代理。Edge
renderer 仍是受限浏览器进程，它的域/资源预算不等于上述 curl 级 IP 固定。

## 错误、warning 和发布门禁

MediaLab 失败结果保留 `error_code`、`error_source`、`retryable` 和 task ID。根集成层把它们映射到
typed job error，不再全部折叠成通用 `import_failed`。异常链文本在写 DB/API 前删除 query 中的
可能凭据。

probe、JS、oEmbed、resolver、媒体部分成功和 manifest warning 会去重后持久到 job，并通过 API/UI
显示。warning 不被当作失败，也不被静默丢弃。可能造成不完整 item 的 warning 会阻止用新
快照覆盖旧完整 item。

在线 ASR 下载媒体后先停在私有 staging。质量门禁通过且 publish fence 仍开放时才发布；
timeout 会关闭 fence、写入 typed lifecycle error 并清理 staging，底层不可强杀线程即使稍后返回也
不能再发布。

## 本地模型

Whisper 模型快照用 `asset-manifest.json` 记录 schema、model ID、固定 revision 和所有必需文件的
大小/摘要。可用性检查和真实加载都要验证 manifest。下载在临时目录完成后原子替换，进程内
lock + 跨进程文件锁保证同一模型只有一个写入者。同 revision 重启复用已验证快照；损坏或
revision 变化才重建。

## 可选功能：朗读与图片文字识别

导入服务提供两项可选功能，均依赖可选 extra，缺失时不影响其他功能：

| 功能 | extra | 额外条件 | 输出 |
| --- | --- | --- | --- |
| Kokoro 朗读 | `tts`（kokoro-onnx、onnxruntime、numpy） | 模型文件 `kokoro-v1.0.onnx`、`voices-v1.0.bin`，用 `uv run python scripts/download_kokoro_models.py` 下载；`ECHO_TTS_MODELS_PATH` 或 `[tts] model_dir` 指定目录，默认 `<cache_root>/models/kokoro` | `media/tts-article.wav` + `media_items` |
| 图片 OCR | `ocr`（rapidocr-onnxruntime，模型随 wheel） | 或 `[ocr] engine = "paddleocr_api"` + `allow_cloud = true` + `secrets.toml [ocr].token`（会上传图片） | `ocr.json` |

`GET /api/v1/features` 与 `import-capabilities.features` 返回
`{"tts": {"available", "reason", "engine"?, "message"?}, "ocr": {…}}`。`reason` 取值：
`disabled`、`dependencies_missing`、`model_missing`、`cloud_not_allowed`、`missing_endpoint`、
`missing_token`。功能不可用时 `POST /api/v1/items/{id}/tts|ocr` 返回
`503 {"detail": {"code": "feature_disabled", "feature", "reason", "message": "…功能未启用：…"}}`，
前端不显示对应按钮。可用时返回 `202` 任务对象，用 `GET /api/v1/enrichment-jobs/{id}` 轮询；
完成后导入服务请求 Echo 重新扫描，新文件随即进入阅读界面和全文检索。

## 安装版同源代理

安装版浏览器先访问宿主生成的 `/session/{token}`，Rust 用 constant-time 比较后设置
session-only、HttpOnly、SameSite=Strict cookie 并重定向 `/`。除无敏感数据的 `/health` 和
独立鉴权的 `/internal/v1` 外，公开 API、SPA、静态文件、媒体和资料库附件都要求该 cookie。
每次启动另生成独立内部 token；Rust 代理请求注入 `X-Echo-Internal-Token`，Python 安装模式对
全部端点强制验证，浏览器 cookie 不向 Python 转发。

Rust 只代理下列 `/api/v1` 路由：`app-config`、`app/shutdown`、`library/switch`、
`import-capabilities`、`import-batches`、`import-batches/{id}`、`import-batches/{id}/retry-failed`、
`import-jobs`、`import-jobs/{id}`、`import-jobs/{id}/timeline`、`import-jobs/{id}/cancel`、
`import-jobs/{id}/retry`、`import-jobs/{id}/reindex`、`import-diagnostics`、`credential-health`。未列出路径不是通用转发。

请求 body 上限 1 MiB，响应 body 上限 8 MiB；只转发 method、精确 path/query、body 和
`Content-Type`，保留上游 status/Content-Type。除 `library/switch` 需要完整本地扫描而不设代理
截止外，其他上游请求为 30 秒。上游必须是无凭据、无路径的 loopback HTTP origin，
不跟随重定向且不使用环境代理。

资料库媒体和附件来自不受信网络。Rust 对这些响应统一发送
`X-Content-Type-Options: nosniff`、`Content-Security-Policy: sandbox; default-src 'none'` 和
`Referrer-Policy: no-referrer`；Range 播放仍保留，但直接导航 HTML/SVG 不会获得应用 origin 的
脚本权限。
