# Echo 交付文档（HANDOFF）

> **历史组件交接**：本文完整保留原 Echo 独立项目的功能、设计决策、测试数字与踩坑记录。正文中的
> Git 历史、阶段数字和质量门是当时快照，不代表当前 `echo-reader` monorepo。当前跨组件状态、
> 新三栏前端和导入服务请先读 [根 HANDOFF v2](../../../docs/HANDOFF.md)。

本文档说明原 Echo 组件做了什么、为什么这样做、踩过什么坑、每个功能怎么用和怎么实现。

配套文档：[README.md](../README.md)（运行入口）、[architecture.md](architecture.md)（架构）、[api.md](api.md)（接口契约）、[file-format.md](file-format.md)（数据格式）、[PROJECT_PLAN.md](../PROJECT_PLAN.md)（原始计划书，含全部需求细节）。

---

## 1. 这是什么

本地运行的**混合内容阅读、播放、转录校对与 Markdown 编辑工具**。一条信息 = 资料库根目录下一个一级子文件夹（含 manifest.json、文章、转录、媒体、图片），全部数据在本地文件系统，无数据库、无账号。

- 前端：React 19 + TypeScript（strict）+ Vite 8 + React Router 7 + react-markdown + CodeMirror 6，bun 管理
- 后端：Rust 1.97 + Axum 0.8 + Tokio + serde + pulldown-cmark + BLAKE3（已移除 tower-http/tower，静态托管与 Range 均自实现）
- 开发：前端 5173（vite proxy `/api` → 后端 3000）；生产：Rust 托管 `frontend/dist`（同源）
- 工具链：mise（`mise.toml`：rust 1.97.1 / bun 1.3.14）

## 2. 成果清单

原 Echo 当时按 8 个阶段交付并经历多轮 UI/修复迭代；其 `.git` 历史没有复制进 Echo Reader，
下表作为历史交付记录保留：

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 骨架 | axum + /health、Vite 工程、CSS tokens、AppShell、代理 | ✅ |
| 2 资料库导航 | manifest 扫描、ItemIndex、items API、Sidebar、路由 | ✅ |
| 3 Markdown 阅读 | content.json→article.md 生成、react-markdown、图片/外链安全 | ✅ |
| 4 媒体卡片 | Range/206、自研播放器控制栏、倍速持久化、错误状态 | ✅ |
| 5 时间线转录 | 转录 API、模式切换、二分查找、跟随高亮、点击跳转 | ✅ |
| 6 编辑 | CodeMirror 懒加载、文章保存/预览/取消、转录批量编辑 | ✅ |
| 7 图片操作 | 上传（文件头校验/UUID/20MB）、occurrence 删除、.trash | ✅ |
| 8 测试完善 | 43 Rust 测试、26 Vitest、Playwright 端到端、文档、许可证 | ✅ |
| UI 演进 | 白色+宝蓝主题、侧栏展开/折叠、音频迷你播放栏、视频动态置顶、响应式宽度、卡片限宽 | ✅ |
| 播放修复 | play() 自动重试、错误码显示、缓存策略（no-cache/no-store/immutable） | ✅ |
| 文件夹选择 | 后端 browse/switch API、单 item 模式、侧边栏目录浏览弹窗 | ✅ |

**原组件当时的质量门记录**：`cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`、`cargo test`（34 单元 + 11 集成）、`bun run typecheck`、`bun run lint`、`bun run test`（30 单测）、`bunx playwright test`（4 条端到端，串行 workers=1）。当前数字以根 HANDOFF 的新鲜全仓验证为准。

## 3. 核心设计决策与原因

### 3.1 页面统一 ≠ 数据塞进 Markdown
播放器状态、播放时间、转录时间轴是**结构化页面数据**，绝不写入 Markdown。否则更换播放器要迁移 Markdown、用户编辑会破坏媒体配置、时间戳会被文本编辑破坏。

### 3.2 article.md 是用户正文唯一真实来源
`content.json` 是内容处理模块的不可变初始输出；后端只在 `article.md` 不存在时用它生成一次，之后永远直读 `article.md`，任何情况下不得覆盖用户修改。

### 3.3 文件系统是唯一数据源，无数据库
启动时全量扫描建内存索引（`ItemIndex`，RwLock 共享）。新内容写入后调 `POST /library/rescan` 或前端刷新按钮重建。无效文件夹隔离进 `/library/problems`，不影响服务启动。

### 3.4 媒体时间不进 React 状态（关键性能决策）
`MediaController` 持有 `currentTime`，通过订阅（`onTime`/`onState`）分发；React 侧用 `useSyncExternalStore` 订阅。转录视图只保存 `activeSegmentId`（变化时才 setState）。因此媒体时间推进**不会触发文章重渲染**。

### 3.5 版本冲突控制
所有写接口要求 `expected_version`（BLAKE3 内容哈希 `blake3:<hex>`），不一致返回 409 带 `current_version`，绝不自动覆盖。保存用"同目录临时文件 → 写入 → flush → sync → rename 替换"原子流程。保存成功同时原子更新 manifest 的 `updated_at`（决定导航排序）。

### 3.6 图片删除按 occurrence 定位
前端在 HAST 渲染阶段为每个 img 按出现顺序编号（rehype 插件），删除请求携带 `{src, occurrence, version}`；后端用 pulldown-cmark 的 offset 迭代器定位第 N 个图片语法的字节范围，删除后若无剩余引用则把文件移入 `.trash/`（不硬删除）。

### 3.7 路径安全
`assets` 路由从 `OriginalUri`（原始 URI）手动解析并 percent-decode 路径，然后走统一校验（拒绝 `../`、绝对路径、盘符 → canonicalize → starts_with 验证）。选择 OriginalUri 是因为 axum 通配符参数的行为不可靠（见 5.1）。

### 3.8 自研播放器而非第三方
需求只有播放/暂停/跳转/时间/音量/倍速，原生 HTMLMediaElement 足够。控制栏自绘（无全屏/画中画按钮）。**播放器 UI 行为**：视频仅播放时 sticky 置顶（暂停随滚动划走）；音频永不置顶，页面底部常驻圆角矩形迷你播放栏。

### 3.9 缓存策略（播放问题修复的关键）
- `index.html` → `Cache-Control: no-cache`（新构建立即生效，防止浏览器长期缓存旧 JS）
- 带 hash 的静态资源 → `immutable` 长缓存
- 媒体/图片响应 → `Cache-Control: no-store`（浏览器不缓存媒体状态）

### 3.10 资料库根目录可切换
浏览器安全模型下无法获取本机绝对路径，文件夹选择必须由后端完成：`browse` 列目录（空路径=磁盘根）、`switch` 切换根并重建索引。AppState 的 `library_root` 用 `RwLock<PathBuf>` 持有（不再不可变）。选中文件夹自身含 manifest.json 时作为单个 item，否则扫描一级子文件夹。

## 4. 功能使用指南

### 4.1 左侧导航
- **用**：点击文件夹打开阅读页；📁 圆角矩形按钮**选择本机文件夹作为资料库根**（弹出目录浏览框：磁盘根 → 逐级进入 → "选择此文件夹"）；`⟳` 刷新；`«/»` 折叠/展开（276px 显示名称 / 60px 仅图标 + tooltip）；折叠状态存 `localStorage["reader.sidebar.collapsed"]`。
- **设计**：浏览器无法获取本机绝对路径，因此文件夹选择由**后端目录浏览 API**（`GET /library/browse`，空路径返回磁盘根、路径返回子项列表）+ **切换 API**（`POST /library/switch`）驱动；切换后 AppState 的 `library_root` 更新并重建索引。选中文件夹自身含 `manifest.json` 时作为**单个 item**（`scan_library_or_single`），否则按一级子文件夹扫描。
- **文件**：`components/sidebar/`（含 `FolderPickerModal.tsx`）、`hooks/useItems.ts`、`backend/src/routes/library.rs`。

### 4.2 阅读页与文章渲染
- **用**：默认显示文章；顶部来源类型与外链（新标签）；无媒体时无播放器无占位。宽屏（≥1536px）阅读区自动加宽（1080/1200px），两侧留白；标题与正文同宽。
- **设计**：`DocumentPage` 是滚动容器，保存文章/转录两种模式的滚动位置，切换时恢复。react-markdown + remark-gfm，不启用原始 HTML；`a` 组件只允许 http/https/相对路径，外链 `target="_blank" rel="noopener noreferrer"`；图片懒加载 + 失败占位。
- **文件**：`components/document/`、`components/markdown/`。

### 4.3 媒体卡片
- **用**：视频卡片最大宽 720px 左对齐（与正文左缘对齐），播放中 sticky 置顶，暂停/结束随滚动划走；控制栏含播放/暂停、进度拖动、音量/静音、倍速（0.75-2.0，持久化 `reader.media.playbackRate`）。音频卡片同样限宽左对齐、不置顶，**页面底部固定迷你播放栏**（播放/暂停 + 时间 + 进度条）。错误卡片显示"无法播放该媒体文件"+ **错误码**（1 中止/2 网络/3 解码/4 格式）+ 重试按钮。
- **设计**：视频 `preload="metadata" playsinline disablepictureinpicture`，无 `controls`；状态机 idle/loading/ready/playing/paused/ended/error；**`play()` 失败自动重试（最多 3 次，400ms 间隔），被拒不显示错误卡片**（真正问题由元素 error 事件兜底）；切换文章/转录模式不卸载播放器；切换内容（itemId）时 `controller.reset()` 重置状态。
- **文件**：`components/media/`、`hooks/useMediaController.ts`、`useMediaStore.ts`。

### 4.4 时间线转录
- **用**：卡片右上 [文章][时间线文本]（两者都有才显示）；"跟随高亮"开启后当前段高亮并自动滚到中部；手动滚动暂停跟随并出现"继续跟随"；点时间戳跳转+播放+高亮；点正文不跳转。
- **设计**：二分查找 `start_ms <= t < end_ms`（`findActiveSegment.ts`）；只在段 ID 变化时更新高亮；程序滚动与用户滚动用 flag + 定时器区分。
- **文件**：`components/transcript/`。

### 4.5 转录编辑
- **用**：顶部"编辑转录"→ textarea 自动增高改说话人/文字（时间戳只读）→"保存修改"（只提交变更段）/取消；Esc 放弃当前段、Ctrl/Cmd+Enter 保存、Tab/Shift+Tab 移动焦点；409 保留草稿显示"重新加载"。
- **文件**：`components/transcript/TranscriptView.tsx`。

### 4.6 文章编辑
- **用**：头部"编辑文章"→ CodeMirror 按需加载（独立 chunk）；语法高亮/搜索替换/粘贴图片自动上传插入/保存（手动）/预览/取消（有改动弹确认）。
- **文件**：`components/markdown/MarkdownEditor.tsx`、`codemirrorSetup.ts`。

### 4.7 图片操作
- **用**：图片 hover 右上垃圾桶 → 确认 → 按 occurrence 删除；无剩余引用移入 `.trash/`；粘贴/上传 JPEG/PNG/WebP/GIF。
- **设计**：按文件头识别（拒 SVG/伪造 → 415）、20MB（413）、UUID 文件名；删除响应返回新 markdown + 版本，前端本地替换。
- **文件**：`backend/src/routes/images.rs`、`image_upload.rs`、`markdown/image_reference.rs`。

## 5. 经验总结（踩坑与教训）

### 5.1 后端 / Rust

1. **axum 0.8 通配符参数不可靠**：`{*relative}` 对 `assets/../x` 这类路径，matchit 先规范化（`..` 被吃掉）导致请求落不到 handler；`..%2F` 又不自动 decode，中文/空格文件名 404。**解法**：handler 用 `OriginalUri` 从原始 URI 手动 split + percent-decode，再走统一路径校验。
2. **axum 通配符参数数量必须匹配**：路由声明 2 个参数而 handler 只提取 1 个 → 运行时 500 "Wrong number of path arguments"。必须 `Path((id, _rel))`。
3. **tower-http ServeFile 难缠**：`not_found_service` 要求 `DefaultServeDirFallback`，`SetStatus<ServeFile>` 不满足；ServeFile 响应体转 axum Body 撞 Sync 约束。**解法**：SPA 托管和媒体 Range 自实现，代码更短且完全可控。
4. **Windows 原子写**：`std::fs::rename` 在 Windows 使用 `MOVEFILE_REPLACE_EXISTING`，可直接覆盖已存在文件。
5. **测试进程死锁**：`#[tokio::test]` 默认 current_thread runtime，测试里 join std 线程会饿死 runtime。**解法**：服务启动纯 async（`tokio::spawn(axum::serve)`），测试结束 `handle.abort()`。
6. **chrono `to_rfc3339()` 秒级精度**：同一秒内两次保存 `updated_at` 字符串相同，测试断言用 `>=`。
7. **PowerShell 写文件编码坑（重要）**：`Set-Content` 按系统 GBK 编码写入并可能加 BOM，曾损坏中文源码和让 serde_json 解析失败（BOM）。**教训**：写源码/JSON 用编辑工具或显式 UTF-8 无 BOM；JSON 解析失败先查 BOM。
8. **clippy 强制 `len` 配 `is_empty`**（`len_without_is_empty`），删一个要删两个。
9. **删除依赖前确认**：tower-http/tower 在自实现静态托管和 Range 后被完全移除（features fs/set-status/cors 均无用），减少依赖面。

### 5.2 前端 / React

1. **React StrictMode 双执行 + effect 依赖导致请求永久卡死**：`useTranscript` 的 effect 用 `loading` state 防重入，StrictMode 下第一次请求被取消、第二次直接 return → 永远 pending。**解法**：不在 effect 依赖里放 `loading`，用 `data === null` 判断 + cancelled 标记。
2. **react-markdown v10 不再读 remark 的 `hProperties`**：图片编号改用 rehype 插件直接写 `node.properties`。
3. **媒体时间与渲染隔离**：`currentTime` 若放 React state，`timeupdate`（~4Hz）会重渲染整个阅读区。**解法**：控制器类 + `useSyncExternalStore` 订阅。
4. **useSyncExternalStore 的 getSnapshot 必须返回可比较值**：曾返回固定 controller 引用导致订阅方永不重渲染（播放按钮状态不同步、sticky class 不更新）；返回 `state` 字符串后正确。
5. **scrollIntoView 触发 scroll 事件误判为手动滚动**：程序滚动前设 flag + 定时器清除。
6. **fast-refresh lint**：组件文件导出非组件（函数/常量）会警告，纯函数拆独立文件。
7. **`play()` 拒绝 ≠ 媒体损坏（重要排查结论）**：页面能显示画面但点击播放报错，原因往往是播放请求在数据缓冲未完成时被拒绝（AbortError/NotAllowedError 等瞬态错误）。**解法**：play() 失败自动重试，不再直接进 error 状态；真正的媒体问题由元素 error 事件驱动，并在错误卡片显示 `media.error.code`（1 中止/2 网络/3 解码/4 格式）便于诊断。
8. **切换内容时媒体状态残留**：从视频项切到音频项，controller 的 state（error/playing）会残留。**解法**：itemId 变化时 `controller.reset()`。

### 5.3 测试 / 工具链

1. **Playwright chromium 下载超时** → 用系统浏览器 `channel: "msedge"`（Windows 自带，H.264 正常，headless 可用）。
2. **Playwright 测试库污染**：端到端测试真实写文章/转录，必须复制 `library.example` 到独立目录（config 加载时 `fs.cpSync`），后端用 `LIBRARY_ROOT` 指向副本。
3. **webServer 复用进程的坑**：`reuseExistingServer: true` 会复用外部后端（LIBRARY_ROOT 不对 → 测试污染示例库）。**解法**：后端 `reuseExistingServer: false` 强制独立启动；vite 可复用（无状态）。
4. **Vitest 误拾取 Playwright 的 `tests/e2e`** → `test.exclude` 排除。
5. **Bun 运行时无 localStorage**（实验性警告）→ setup 里给 jsdom 补内存实现。
6. **播放相关断言**：段窗口短（2.2s）时靠播放推进断言会错过；用"暂停 → seek → 等待高亮"的确定性方式。`waitForFunction` 高频设置 currentTime 会持续打断 seek 导致 timeupdate 永不完成。
7. **"视频未重新加载"验证**：切换模式前用 `addEventListener("loadeddata")` 计数，切换后断言计数为 0 且 currentTime 单调不减。
8. **浏览器缓存旧构建的坑（重要排查结论）**：生产模式托管若不给 `index.html` 加 no-cache，浏览器会长期缓存旧 JS，表现为"怎么修都没用"。修复后给 HTML no-cache、hash 资源 immutable、媒体 no-store。排查浏览器是否加载新代码：看新 UI 特征（如错误卡片是否显示错误码）。

## 6. 已知限制与后续建议

- **转录未虚拟化**：≤5000 段直接渲染（二分查找 + 仅 activeSegmentId 更新已保证流畅）；超限再引入虚拟列表。
- **无文件系统实时监听**：靠 rescan 接口/刷新按钮。
- **raw `../` 请求返回 404 而非 403**：matchit 规范化使请求落入 SPA fallback（对 `/api/` 前缀返回 404），安全但语义不同；需严格 403 可加全局中间件。
- **图片删除按整行删除**：图片语法与文字同行时会连文字一起删。
- **媒体编码保障范围**：仅保证 MP4(H.264+AAC)、MP3、M4A；其他格式由上游内容模块生成 playback 文件，后端不转码。
- **倍速持久化无 UI 重置入口**：localStorage key `reader.media.playbackRate`。
- **未来迁移 Tauri**：数据层（文件格式、API、React 页面）可原样保留，只需替换传输层。

## 7. 文件地图

### 后端（`backend/src/`）
| 文件 | 作用 |
|---|---|
| `main.rs` / `lib.rs` | 启动入口；lib 暴露模块供集成测试引用 |
| `app_state.rs` | AppState：配置 + RwLock<ItemIndex>，rescan |
| `config.rs` | LIBRARY_ROOT 解析，未设置回落 `library.example`（基于 CARGO_MANIFEST_DIR，不依赖 cwd） |
| `error.rs` | 统一错误 `{code, message, details}` |
| `routes/mod.rs` | 路由表 + SPA 静态托管（含缓存头策略） |
| `routes/items.rs` | 导航列表、单条详情（含 article 装配） |
| `routes/article.rs` | PUT 文章（409、touch manifest） |
| `routes/transcript.rs` | GET/PATCH 转录（400/422/409 校验、touch manifest） |
| `routes/images.rs` | 图片上传、图片引用删除（.trash） |
| `routes/image_upload.rs` | 文件头识别（JPEG/PNG/WebP/GIF）与大小校验 |
| `routes/media.rs` | 手写 Range/206 媒体流（含 no-store） |
| `routes/assets.rs` | OriginalUri 解析 + 安全校验的资源流 |
| `routes/library.rs` | rescan、problems、**browse（目录浏览）、switch（切换根）** |
| `library/scanner.rs` | 目录扫描、无效项收集、**单 item 模式（scan_library_or_single）** |
| `library/index.rs` | 内存索引与排序 |
| `library/manifest.rs` | manifest 解析/校验（含单元测试） |
| `library/assembler.rs` | article.md 确保生成（存在即直读） |
| `library/paths.rs` | 路径规范化/穿越防护 |
| `markdown/generate.rs` | content.json → article.md |
| `markdown/image_reference.rs` | 图片 occurrence 定位与删除 |
| `storage/atomic_write.rs` / `version.rs` | 原子写、BLAKE3 版本 |
| `models/` | item/content/transcript/api 数据模型 |

### 前端（`frontend/src/`）
| 路径 | 作用 |
|---|---|
| `app/` | AppShell、路由 |
| `components/sidebar/` | 导航（展开/折叠 + localStorage 持久化 + **FolderPickerModal 目录浏览弹窗**） |
| `components/document/` | 阅读页装配（模式切换、滚动记忆、媒体上下文、迷你播放栏挂载） |
| `components/markdown/` | 渲染器、编辑器（懒加载）、图片块、URL 安全 |
| `components/media/` | 媒体卡片（动态 sticky）、播放器、控制栏、**MiniPlayer（音频底部迷你栏）** |
| `components/transcript/` | 转录视图、二分查找、跟随、编辑 |
| `hooks/useMediaController.ts` | 媒体状态机 + 订阅分发 + play 重试 + reset（核心） |
| `hooks/useMediaStore.ts` | useSyncExternalStore 订阅适配（getSnapshot 返回可比较值） |
| `api/` | fetch 封装（ApiError 解析）与类型 |

### 测试
| 位置 | 覆盖 |
|---|---|
| `backend/src/**` 内 `#[cfg(test)]` | 34 个单元测试 |
| `backend/tests/api.rs` | 9 个集成测试（真实 HTTP + 临时资料库） |
| `frontend/tests/*.test.ts(x)` | 26 个 Vitest 单测 |
| `frontend/tests/e2e/reader.spec.ts` | 2 条端到端（Edge 真实播放；完整阅读流程 + 动态 sticky/迷你播放栏） |
| `frontend/tests/e2e/folder-picker.spec.ts` | 2 条端到端（文件夹浏览选择 + 取消流程） |

## 8. 快速上手命令

```powershell
# 一键质量门（按顺序）
cd backend; cargo fmt --check; cargo clippy --all-targets -- -D warnings; cargo test
cd frontend; bun run typecheck; bun run lint; bun run test
cd backend; cargo build   # 先构建 exe 供 Playwright 使用（Playwright 强制独立启动后端）
cd frontend; bunx playwright test

# 开发运行
cd backend; cargo run          # 3000（LIBRARY_ROOT 未设置时回落 library.example）
cd frontend; bun run dev       # 5173
```

注意：Playwright 测试会杀掉/不占用 3000 端口的既有后端（`reuseExistingServer: false`），测试结束后需重启开发后端。

## 9. 给下一个开发者的建议

1. 改动前先跑一遍质量门确认基线；改完再跑一遍。
2. 涉及路径/上传/并发的地方，先看 `docs/api.md` 的契约再动手。
3. 新功能请先对照 `PROJECT_PLAN.md` 第 4 节"明确不做"清单，避免超出产品范围。
4. 若更换媒体实现（如引入播放器库），不要动 Markdown 与转录数据格式——这是未来迁移 Tauri 的边界。
5. 遇到 JSON 解析/中文乱码问题，先检查文件编码是否为 UTF-8 无 BOM。
6. 用户报"播放失败"时：先确认浏览器加载的是最新构建（index.html no-cache 已保证刷新生效），再看错误码（1 中止/2 网络/3 解码/4 格式），最后才怀疑代码——本项目的媒体链路已被 Chrome/Edge 双浏览器验证。
