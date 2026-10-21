# 架构说明

## 总览

Echo 是本地运行的前后端分离应用：

- `frontend/`：React + TypeScript + Vite 单页应用
- `backend/`：Rust + Axum 服务，提供 API 与静态资源托管
- 数据完全存储在本地文件系统（`LIBRARY_ROOT`），无数据库

未设置 `LIBRARY_ROOT` 时，组合项目默认使用仓库根目录的 `content/`（Windows 示例：
`<repo-root>\content`）。

开发时前端 5173 通过 Vite proxy 访问后端 3000；生产时后端直接托管 `frontend/dist`，同源部署。

## 核心设计原则

### 1. 页面统一，数据分离

阅读页面由结构化页面数据组合而成，而不是把一切塞进 Markdown：

```
DocumentPage
├── 文档元数据
├── 固定主媒体卡片数据
├── 附加媒体列表
├── AI 文章 Markdown
└── 时间线转录 JSON
```

播放器状态、播放时间、转录时间轴是页面数据，绝不写入 Markdown。媒体时间的推进通过 `MediaController` 的订阅机制分发，不经过 React 全局状态，因此不会导致整篇文章重渲染。

### 2. article.md 是用户正文唯一真实来源

`content.json` 是内容处理模块的初始输出（不可变的生成来源）。后端在第一次打开时用它生成 `article.md`；此后永远直接读取 `article.md`，用户修改不会被覆盖。

### 3. 文件系统是唯一数据源

应用启动时扫描资料库建立内存索引（`ItemIndex`）。新内容写入后调用 `POST /api/v1/library/rescan` 或点击前端刷新按钮重建索引。无效文件夹被隔离并记录到 `/api/v1/library/problems`，不影响服务运行。

**资料库根目录可切换**：侧边栏"选择文件夹"通过后端 `GET /library/browse`（列目录，空路径返回磁盘根）与 `POST /library/switch`（切换根并重建索引）实现——浏览器安全模型无法获取本机绝对路径，目录浏览必须由后端完成。AppState 的 `library_root` 为 `RwLock<PathBuf>`。所选文件夹自身含 `manifest.json` 时作为单个 item（`scan_library_or_single`），否则按一级子文件夹扫描。

manifest 保持 `schema_version: 1`，以可选 `source.description` 和默认空数组
`media_items` 做向后兼容扩展。`media_items` 保存完整媒体集合，`primary_media` 则镜像其中的
主媒体并仍是唯一主播放器来源；集合内媒体按稳定 ID 通过
`/api/v1/items/{id}/media/{media_id}` 读取。扫描阶段校验所有声明路径均为 item 内安全相对
路径，且目标存在并为文件；无效 item 进入 problems 列表。一个 URL 只生成一个 item 是
producer/MediaLab 的产出政策，Echo 不会按 URL 推断、合并或拆分条目。

## 后端模块

```
backend/src/
├── main.rs          # 启动：读配置、建索引、挂路由
├── app_state.rs     # AppState：配置 + 共享索引（RwLock）
├── config.rs        # LIBRARY_ROOT 解析与回落
├── error.rs         # 统一错误格式 {code, message, details}
├── routes/          # HTTP 层：items/article/transcript/images/media/assets/library
├── library/         # 资料库：scanner/index/manifest/assembler/paths
├── markdown/        # content.json→article.md 生成、图片 occurrence 定位
├── storage/         # 原子写入、BLAKE3 版本、.trash
└── models/          # 数据模型与 API 响应结构
```

### 并发控制

所有写接口（文章、转录、图片删除）都要求 `expected_version`（文件内容 BLAKE3 哈希）。版本不一致返回 409，绝不静默覆盖。写入采用"同目录临时文件 → 写入 → sync → rename 替换"的原子流程。

### 路径安全

所有文件访问按四步执行：连接 item 根目录与相对路径 → 拒绝 `../`、绝对路径、盘符 → canonicalize → 验证最终路径仍在 item 根目录内。`assets` 路由从 `OriginalUri` 手动解析并解码路径，避免通配符参数的规范化歧义。

## 前端模块

```
frontend/src/
├── app/             # AppShell、路由
├── components/
│   ├── sidebar/     # 导航（含 article/video/audio/mixed 独立筛选）
│   ├── document/    # 阅读页装配（模式切换、滚动记忆、媒体上下文、迷你播放栏）
│   ├── markdown/    # react-markdown 渲染、CodeMirror 编辑（懒加载）、图片块
│   ├── media/       # 主媒体卡片、附加媒体播放器、控制栏、MiniPlayer
│   └── transcript/  # 时间线转录、二分查找、跟随高亮、编辑
├── hooks/           # useItems/useDocument/useTranscript/useMediaController/useMediaStore
├── api/             # fetch 封装与类型
└── styles/          # 设计 tokens 与全局样式
```

### 媒体与转录同步

- `MediaController`：媒体元素的状态机（idle/loading/ready/playing/paused/ended/error）与操作；`currentTime` 保存在控制器内部，通过订阅（`onTime`/`onState`）分发，React 侧用 `useSyncExternalStore` 适配（getSnapshot 返回可比较值），React 状态只保存 `activeSegmentId`。
- `play()` 失败自动重试（最多 3 次）；被拒不进入 error 状态，真正的媒体问题由元素 error 事件兜底，错误卡片显示 `media.error.code`（1 中止/2 网络/3 解码/4 格式）便于诊断。
- 转录视图订阅媒体时间，用二分查找定位当前段，仅在段 ID 变化时更新高亮。
- 跟随模式开启时，段变化触发 `scrollIntoView(center)`；用户手动滚动会暂停自动跟随并显示"继续跟随"按钮。
- 切换文章/转录模式不卸载播放器；切换内容（itemId）时 `controller.reset()` 重置状态。
- 视频卡片仅播放时 sticky 置顶（暂停随滚动划走）；音频卡片不置顶，页面底部固定圆角矩形迷你播放栏（MiniPlayer）。
- `media_items` 中 `inline` / `attachment` 媒体由 `AdditionalMediaList` 分别渲染原生视频或
  音频播放器；`role: primary` 在详情装配时过滤，避免与主媒体卡片重复展示。

### 性能

- CodeMirror 通过动态 import 按需加载，不在首包。
- 转录数据仅在首次进入时间线模式时请求，切换回文章不清除。
- 图片懒加载；媒体 `preload="metadata"`。
- 文章视图 `React.memo`，媒体时间变化不触发其重渲染。
- 阅读区响应式宽度：基础 920px，宽屏（≥1536px）放大到 1080/1200px，两侧留白；媒体卡片限宽 720px 左对齐。

### 缓存策略

- `index.html` → `Cache-Control: no-cache`（新构建立即可见）
- 带 hash 的静态资源 → `immutable` 长缓存
- 媒体与图片响应 → `Cache-Control: no-store`

## 安全

- 服务默认监听 `127.0.0.1`，不绑定 `0.0.0.0`。
- 上传只接受 JPEG/PNG/WebP/GIF，按文件头识别，拒绝 SVG 与伪造 MIME；文件名使用 UUID。
- Markdown 渲染不启用原始 HTML，链接拒绝 `javascript:`/`data:`/`file:`/`vbscript:` 协议。
- 删除图片先移入 `.trash`，不硬删除。
