# API 契约

所有接口前缀 `/api/v1`，响应为 JSON（媒体与图片文件除外）。

安装版必须先访问实例记录中的 `/session/{token}`。成功时返回 `303 /` 并设置 session-only、
`HttpOnly`、`SameSite=Strict` cookie；之后公开 API、页面、媒体和静态文件都要求该 cookie。
`/health` 是无敏感数据的进程探针例外；`/internal/v1` 使用另一套内部 token，不属于浏览器 API。

## 错误格式

```json
{ "code": "ERROR_CODE", "message": "适合用户阅读的错误信息", "details": null }
```

常见状态码：

| 状态码 | 含义 |
|---|---|
| 400 | 请求参数错误 |
| 403 | 路径或资源访问被拒绝 |
| 404 | 信息或资源不存在 |
| 409 | 文件版本冲突 |
| 413 | 上传文件过大 |
| 415 | 不支持的文件类型 |
| 421 | Host 不是 loopback |
| 422 | manifest 或 JSON 数据格式错误 |
| 502 | 私有导入服务无法连接、读取或返回过大响应 |
| 500 | 后端内部错误 |

## 接口列表

### GET /health 与 GET /api/v1/health

`GET /api/v1/health` 返回：

```json
{ "status": "ok" }
```

`/health` 供生产/进程探针使用，另可包含当前 `boot_id`；`/api/v1/health` 供 API 客户端和
开发代理使用。

### GET /api/v1/items

导航列表，按 `updated_at` 降序、相同时按 `folder_name` 排序。

```json
{
  "items": [
    {
      "id": "01J4R7ZQ6YEPNXQFSX3B9S8A2V",
      "folder_name": "访谈：人工智能与个人知识管理",
      "source_type": "video",
      "source_title": "AI and Personal Knowledge Management",
      "source_author": "Example Channel",
      "published_at": "2026-08-05T12:00:00+08:00",
      "has_article": true,
      "has_transcript": true,
      "has_media": true,
      "updated_at": "2026-08-06T20:00:00+08:00"
    }
  ]
}
```

`source_title`、`source_author`、`published_at` 均可为 `null`；列表界面应在缺失时回退到
`folder_name`，不要为了展示列表逐条请求详情接口。

### GET /api/v1/library/browse

浏览本机文件夹（选择资料库根目录用）。

- `?path=` 为空 → 返回本机磁盘根列表
- `?path=C:\some\dir` → 返回该目录子项（目录优先排序）

```json
{
  "path": "C:\\path\\to\\echo-reader\\content",
  "parent": "C:\\path\\to\\echo-reader",
  "entries": [{ "name": "example-item", "is_dir": true }]
}
```

不存在的目录返回 404。

### POST /api/v1/library/switch

切换资料库根目录并重建索引。在安装版中，这是由根 Python 宿主所有的事务，Rust
同源网关只把精确路由代理给私有导入服务；Rust 内部 switch 需每次启动生成的 token。

请求：`{ "path": "C:\\some\\library" }`

成功：`{ "library_root": "C:\\some\\library", "item_count": 3, "invalid_item_count": 0 }`

规则：

- 目标必须是绝对、存在、可写且非文件系统根的目录
- 目标不得与 state/cache/config/logs/tmp 目录互相包含
- 存在活动导入时返回 409 `imports_active`
- 目标文件夹自身含 `manifest.json` 时，把该文件夹作为**单个 item**；否则按一级子文件夹扫描
- rescan 与 switch 共用 scan lock，旧扫描不能在新 root 后提交
- Echo 切换或 `app.json` 持久化失败时宿主恢复旧 root；回滚失败返回独立 typed error
- 切换后所有 `/items` 接口立即反映新库

### GET /api/v1/app-config

返回宿主当前 `library_root`、`state_root`、`cache_root`、`logs_root`、`tmp_root`、
`config_root` 和 `install_mode`。这些路径只用于本机运行状态，不应写入 item manifest。

### POST /api/v1/app/shutdown

仅安装模式可用。无活动导入时返回 `202 { "status": "shutting_down" }`；有活动导入时
返回 409 `imports_active`。此接口请求宿主停止整个应用，不只是关闭浏览器标签。

### POST /api/v1/library/rescan

重建内存索引（对当前已切换的根目录）。

```json
{ "item_count": 35, "invalid_item_count": 2 }
```

### GET /api/v1/library/problems

无效文件夹列表。

```json
{ "problems": [{ "folder": "坏目录", "reason": "manifest.json 解析失败: ..." }] }
```

### GET /api/v1/items/:id

单条信息（不返回完整转录）。

```json
{
  "id": "...",
  "folder_name": "...",
  "source": {
    "type": "mixed",
    "url": "https://example.com/source",
    "title": "AI and Personal Knowledge Management",
    "author": "Example Channel",
    "published_at": "2026-08-05T12:00:00+08:00",
    "description": "正文、完整访谈与补充音频。"
  },
  "media": {
    "type": "video",
    "title": "原始访谈视频",
    "url": "/api/v1/items/<id>/media",
    "mime_type": "video/mp4"
  },
  "media_items": [
    {
      "id": "bonus-audio",
      "role": "attachment",
      "type": "audio",
      "title": "补充音频",
      "description": "访谈后的听众问答。",
      "url": "/api/v1/items/<id>/media/bonus-audio",
      "mime_type": "audio/mpeg"
    }
  ],
  "article": { "available": true, "markdown": "...", "version": "blake3:<hex>" },
  "transcript": { "available": true, "segment_count": 853, "version": "blake3:<hex>" },
  "updated_at": "2026-08-06T20:00:00+08:00"
}
```

`media` 可为 `null`，它始终表示 manifest 的 `primary_media`。`media_items` 是附加媒体列表；
manifest 中 `role: primary` 的条目不会在此重复返回。`source.description` 和每个媒体的
`title` / `description` 可为 `null`。旧版 v1 manifest 缺少新增字段时，详情响应分别返回
`source.description: null` 与 `media_items: []`，无需客户端迁移版本。

### GET /api/v1/items/:id/transcript

仅在用户切换到时间线模式时调用。

```json
{ "version": "blake3:<hex>", "language": "zh-CN", "segments": [] }
```

### PUT /api/v1/items/:id/article

请求：`{ "markdown": "...", "expected_version": "blake3:<hex>" }`

成功：`{ "version": "blake3:<hex>", "saved_at": "..." }`

版本冲突（409）：`{ "code": "ARTICLE_VERSION_CONFLICT", "message": "文章已被其他进程修改。", "details": { "current_version": "blake3:<hex>" } }`

保存成功时原子更新 manifest 的 `updated_at`。

### PATCH /api/v1/items/:id/transcript

请求：`{ "expected_version": "blake3:<hex>", "changes": [{ "id": "segment_000001", "speaker": "主持人", "text": "..." }] }`

成功：`{ "version": "blake3:<hex>", "saved_at": "..." }`

约束：

- 只允许修改 `speaker` 和 `text`；携带 `start_ms`/`end_ms` 返回 400
- 修改项 id 不存在返回 400
- 修改项 JSON 结构不合法返回 422
- 版本冲突返回 409（`TRANSCRIPT_VERSION_CONFLICT`）
- 保存成功时原子更新 manifest 的 `updated_at`

### POST /api/v1/items/:id/images

`multipart/form-data`，字段 `file`。

允许：`image/jpeg`、`image/png`、`image/webp`、`image/gif`（按文件头识别）。

- 超过 20MB → 413
- SVG、伪造 MIME、未知格式 → 415
- 文件名由后端生成 UUID，不使用用户上传文件名

成功：

```json
{ "relative_path": "images/<uuid>.webp", "markdown": "![图片](images/<uuid>.webp)" }
```

### DELETE /api/v1/items/:id/image-reference

请求：`{ "src": "images/0001.webp", "occurrence": 0, "expected_version": "blake3:<hex>" }`

行为：

1. 解析文章 Markdown，删除 URL 匹配的第 `occurrence` 个图片语法（按出现顺序 0 起编号）
2. 原子保存文章
3. 无剩余引用时把图片文件移入 `.trash/`（不硬删除）

成功：

```json
{ "article": { "markdown": "...", "version": "blake3:<hex>" }, "saved_at": "..." }
```

### GET /api/v1/items/:id/media

媒体文件流。支持：

- `Accept-Ranges: bytes`
- `Range` / `206 Partial Content` / `Content-Range` / `Content-Length`
- 正确的 `Content-Type`（来自 manifest 的 `mime_type`）
- 非法范围返回 416
- `Cache-Control: no-store`（浏览器不缓存媒体状态）
- `X-Content-Type-Options: nosniff` 与 sandbox CSP（阻止不受信媒体作为同源页面执行）

### GET /api/v1/items/{id}/media/{media_id}

按 manifest 的 `media_items[].id` 流式返回其 `playback_path` 文件。`media_id` 是 manifest
标识（URL 中须编码），而不是文件名或路径；不存在的 item 或媒体 ID 返回 404。

该接口与主媒体接口具有相同的流式契约：支持单段 `Range`、返回 `206 Partial Content`、
`Accept-Ranges` / `Content-Range` / `Content-Length`，使用该媒体声明的 `mime_type`，非法
范围返回 416，并设置 `Cache-Control: no-store`。
同时设置 `X-Content-Type-Options: nosniff`、sandbox CSP 和 `Referrer-Policy: no-referrer`。

### GET /api/v1/items/:id/assets/{relative_path}

图片与附件。路径按原始 URI 解码后校验，任何包含 `../`、绝对路径、盘符、符号链接逃逸、URL 编码穿越的请求被拒绝（403/404），无法访问资料库外文件。响应带 `nosniff`、sandbox CSP
和 `Referrer-Policy: no-referrer`，直接导航活动内容不会继承应用页面权限。

### 安装版导入代理

Rust 同源网关只把下列精确路由代理给私有 Python 服务：

- `GET /api/v1/app-config`、`POST /api/v1/app/shutdown`、`POST /api/v1/library/switch`
- `GET /api/v1/import-capabilities`、`POST /api/v1/import-batches`
- `GET /api/v1/import-batches/{id}`、`POST /api/v1/import-batches/{id}/retry-failed`
- `GET /api/v1/import-jobs`、`GET /api/v1/import-jobs/{id}`、`GET /api/v1/import-jobs/{id}/timeline`
- `POST /api/v1/import-jobs/{id}/cancel`、`retry`、`reindex`
- `GET /api/v1/import-diagnostics`
- `GET /api/v1/credential-health`（浏览器会话 Cookie 离线健康检查，只返回名称与过期时间，不返回值）

代理保留 method、精确 path/query、body、`Content-Type` 和上游 status/Content-Type；不是任意路径
反向代理。请求 body 上限 1 MiB，响应 body 上限 8 MiB。除了需执行完整本地扫描的
`library/switch`，其他上游请求超时为 30 秒。上游只允许无凭据、无路径的 loopback
HTTP origin；HTTP client 禁用环境代理且不跟随重定向。

整个 Rust 入口都拒绝非 loopback Host。GET/HEAD 以外的请求如带 cross-site `Sec-Fetch-Site` 或
与 Host 不匹配的 Origin，返回 403。安装模式下 Rust 代理还注入内部 token；Python 直接请求
没有该 token 时返回 401。

### 静态页面（生产模式，非 /api 前缀）

Rust 直接托管 `frontend/dist`：`index.html` → `no-cache`（新构建立即生效）；带 hash 的静态资源 → `public, max-age=31536000, immutable`；未匹配的 `/api/` 或 `/internal/` 路径返回 404（不落入 SPA fallback）。
