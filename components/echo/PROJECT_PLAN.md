# Echo 项目计划书（Project Plan）v1.1

> **历史来源文档（归档，不是当前计划）**：本文保留原 standalone `<original-echo-dir>` 的需求、评审结论和
> 八阶段实施计划，正文不删。Echo Reader 当前架构、前端与验证状态以
> [根 HANDOFF v2](../../docs/HANDOFF.md) 为准；当前 monorepo 没有导入原项目 `.git` 历史。

**项目名称：** echo
**文档原状态：** 已评审，冻结待执行（现已作为历史计划归档）
**依据：** 《Local Mixed Media Reader 产品需求与技术设计文档 v1.0》+ grill-me 两轮确认结果
**开发工具链：** mise（global mise config：bun 1.3.14 / node 26.5.1 / rust 1.97.1 / uv 0.12.0 / deno）
**目标平台：** 桌面浏览器，最低设计宽度 1024px

---

## 1. 项目概述

### 1.1 定位
本地运行、本地数据、无账号的**混合内容阅读、播放、转录校对与 Markdown 编辑工具**。一条信息 = 资料库根目录下一个一级子文件夹。

### 1.2 一句话
阅读 AI 整理的文章 → 播放音视频 → 对照时间线转录校对 → 直接修改文章和转录文字，全部发生在本地文件系统上。

### 1.3 核心价值流
```
打开网页 → 左侧选文件夹 → 右侧统一阅读页
→ 文章模式（默认）→ 媒体卡片固定播放
→ 时间线文本模式 → 跟随高亮校对转录
→ 编辑文章 / 编辑转录 / 粘贴图片 / 删除图片 / 打开来源链接
```

---

## 2. 已确认决策（grill-me 结论）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 项目根目录 | 直接 `<original-echo-dir>`，不套子目录；仓库/产品名 `echo` |
| 2 | LIBRARY_ROOT | 未设置时回落仓库内 `library.example/`；支持环境变量覆盖；示例内含可播放媒体（用 mise ffmpeg 生成） |
| 3 | 前端包管理器 | bun 1.3.14，lockfile = `bun.lock` |
| 4 | 交付节奏 | 一次性完成全部 8 阶段至验收标准；每阶段结束本地 git commit，不打断审核 |
| 5 | 前端测试 | Vitest + @testing-library/react（jsdom）单元测试 + Playwright 浏览器集成测试；Rust 用 `cargo test` |
| 6 | 仓库管理 | `git init`，每阶段一个 commit，message 按 `feat: echo phase N <name>` 风格 |
| 7 | `updated_at` 语义 | 保存文章/转录成功后，**原子重写 manifest.json 的 `updated_at`**（不动其他字段），导航排序随编辑浮动 |
| 8 | API 响应契约 | PATCH 转录成功 → `{version, saved_at}`；DELETE 图片引用成功 → `{article:{markdown,version}, saved_at}`；changes 中 id 不存在或携带时间戳字段 → `400`；JSON 结构不合法 → `422` |
| 9 | 示例媒体进 git | `library.example` 的生成媒体文件**提交进 git**（15-30s、640×360、H.264+AAC，总 ~8MB），clone 即测，不依赖现场 ffmpeg |

---

## 3. 架构原则（继承原文档，不可违背）

1. **页面统一 ≠ 数据塞进 Markdown**：播放器状态、播放时间、转录时间轴是结构化页面数据（`DocumentPage`），绝不允许写进 Markdown。
2. **`article.md` 是用户正文唯一真实来源**：`content.json` 只用于第一次生成；`article.md` 存在后永远直接读它，不得覆盖用户修改。
3. **后端不猜图片位置**：首版统一采用带顺序的 `content.json` 标准；允许内容处理模块直接产出 `article.md`。
4. **文件系统是唯一数据源**：无数据库、无实时监听；重新扫描靠 `POST /api/v1/library/rescan` 或前端刷新按钮。

---

## 4. 明确不做（禁止自行添加）

账号、协作、云同步、移动端、PWA、标签、全文搜索、双链、知识图谱、AI 对话/改写、富文本、视频全屏/画中画/清晰度/字幕覆盖、音频波形、逐字高亮、转录段落拆分合并、修改时间戳、帧截图、图片放大/裁切/旋转/滤镜、文件夹拖拽排序、多级文件夹、插件、数据库。

---

## 5. 技术选型

### 5.1 前端（bun 管理，锁 `bun.lock`）
React（Vite 模板默认版本）+ TypeScript（strict）+ Vite + React Router + react-markdown + remark-gfm + CodeMirror 6（含 @codemirror/search）+ CSS Modules。
禁止：Tailwind、UI 组件库、第三方播放器、富文本编辑器、Redux/Zustand/MobX（只用 React 本地状态 + 自定义 Hook + 单个 Media Context）。

### 5.2 后端（cargo，锁 `Cargo.lock`）
Rust 1.97.1 + Axum + Tokio + tower-http + Serde + serde_json + pulldown-cmark + BLAKE3 + tempfile + tracing + mime_guess + uuid。

### 5.3 测试
前端：Vitest、@testing-library/react、jsdom、@testing-library/user-event、Playwright。
后端：标准 Rust test 模块 + `tests/` 集成测试。

### 5.4 端口与托管
- 开发：前端 `http://127.0.0.1:5173`（vite proxy `/api` → 后端 `http://127.0.0.1:3000`）；后端监听 `127.0.0.1:3000`（禁止默认 0.0.0.0）。
- 生产：Rust 托管 `frontend/dist`，同源，无 CORS。

---

## 6. 仓库结构

```
echo/
├── frontend/
│   ├── src/
│   │   ├── app/           (App.tsx, routes.tsx, AppShell.tsx)
│   │   ├── components/    (sidebar/ document/ markdown/ media/ transcript/)
│   │   ├── hooks/         (useItems, useDocument, useTranscript, useMediaController)
│   │   ├── api/           (client.ts, items.ts, types.ts)
│   │   ├── styles/        (tokens.css, global.css, markdown.css)
│   │   └── main.tsx
│   ├── public/
│   ├── package.json / tsconfig.json / vite.config.ts / index.html
│   └── tests/ (vitest + playwright)
├── backend/
│   ├── src/
│   │   ├── main.rs, app_state.rs, config.rs, error.rs
│   │   ├── routes/   (items, article, transcript, images, media, library)
│   │   ├── library/  (scanner, index, manifest, assembler, paths)
│   │   ├── markdown/ (generate, image_reference)
│   │   ├── storage/  (atomic_write, version, trash)
│   │   └── models/   (item, content, transcript, api)
│   ├── tests/ (integration)
│   └── Cargo.toml / Cargo.lock
├── library.example/
│   └── example-item/ (完整示例：manifest/content/article/transcript/media/images)
├── docs/
│   ├── architecture.md / api.md / file-format.md
├── PROJECT_PLAN.md
├── README.md
└── THIRD_PARTY_LICENSES.md
```

---

## 7. 资料库数据规范（写入 docs/file-format.md）

### 7.1 目录结构
每条信息 = 一个一级子文件夹：
```
library/<folder-name>/
├── manifest.json      # 必须
├── content.json       # 可选，初始顺序，不可变生成源
├── article.md         # 可选，用户正文唯一真实来源
├── transcript.json    # 可选，带时间戳转录
├── media/             # original.* / playback.*
├── images/            # 文章图片
└── .trash/            # 待清理图片
```

### 7.2 manifest.json（schema_version=1）
- `id`：稳定、不随重命名变化、API 路径用 ID
- `source.type` ∈ {article, video, audio, mixed}；`source.url` 可选
- `article.path` / `article.content_source`
- `transcript.path` / `transcript.language`
- `primary_media`：可为 `null`（null 时前端不得渲染媒体卡片，不留占位）；`playback_path` 为播放器唯一来源；一条信息只允许一个主媒体
- `updated_at`：入库时间；**保存文章/转录成功后由后端原子更新**（见决策 7）

### 7.3 content.json（schema_version=1）
- `blocks[]`，允许类型：heading(1-6) / paragraph / image(path,alt,caption) / blockquote / unordered_list / ordered_list / code / horizontal_rule
- 禁止嵌套任意 HTML
- Markdown 生成规则：图片前后各一个空行；`![alt](path "caption")`

### 7.4 transcript.json（schema_version=1）
- `language`、`media_id`、`segments[]`
- 约束：`id` 非空唯一；`start_ms >= 0`；`end_ms > start_ms`；按 start_ms 升序；允许间隔与轻微重叠；`text` 非 null
- 首版可改：speaker、text；禁止改：start_ms、end_ms、id

### 7.5 article.md
- `article.md` 存在 → 直接读取，不读 content.json
- 首次生成后不得因 content.json 变化自动覆盖

---

## 8. 后端设计

### 8.1 启动流程
```
1. 读 LIBRARY_ROOT（未设置→回落 library.example）
2. 验证目录存在
3. 扫描一级子文件夹
4. 读每个 manifest.json
5. 校验 ID 唯一
6. 校验所有相对路径不逃逸文件夹（canonicalize + starts_with）
7. 建立内存 ItemIndex（AppState）
8. 启动 HTTP（127.0.0.1:3000）
```
无效文件夹：排除出导航、结构化错误日志、列入 `GET /api/v1/library/problems`，不影响服务启动。

### 8.2 API 契约（写入 docs/api.md；前缀 `/api/v1`）

| 端点 | 方法 | 请求 | 成功响应 | 错误语义 |
|---|---|---|---|---|
| `/health` | GET | — | `{status:"ok"}` | — |
| `/items` | GET | — | `{items:[{id, folder_name, source_type, has_article, has_transcript, has_media, updated_at}]}` | 排序：updated_at 降序 → folder_name 自然序 |
| `/library/rescan` | POST | — | `{item_count, invalid_item_count}` | 重建索引 |
| `/library/problems` | GET | — | 无效文件夹问题列表 | 启动时也收集 |
| `/items/:id` | GET | — | 元数据+source+media(可空)+article{available,markdown,version}+transcript{available,segment_count,version}；**不返回完整转录** | 404 未知 ID |
| `/items/:id/transcript` | GET | — | `{version, language, segments}` | 首次切时间线才调用 |
| `/items/:id/article` | PUT | `{markdown, expected_version}` | `{version, saved_at}` | 409 `ARTICLE_VERSION_CONFLICT` + current_version；body 坏 400；成功时原子更新 manifest.updated_at |
| `/items/:id/transcript` | PATCH | `{expected_version, changes:[{id,speaker,text}]}` | `{version, saved_at}` | 携带时间戳字段/id 不存在 → 400；JSON 结构坏 → 422；409 冲突；成功时原子更新 manifest.updated_at |
| `/items/:id/images` | POST | multipart `file` | `{relative_path, markdown}` | 限 jpeg/png/webp/gif；文件头校验；20MB(413)；SVG/伪造(415)；UUID 文件名 |
| `/items/:id/image-reference` | DELETE | `{src, occurrence, expected_version}` | `{article:{markdown,version}, saved_at}` | 按 occurrence 删图片语法→保存→无引用移 `.trash`；409 冲突；成功时原子更新 manifest.updated_at |
| `/items/:id/media` | GET | — | Range/Accept-Ranges/206/Content-Range/Content-Length/正确 Content-Type | 缺失文件 404 |
| `/items/:id/assets/*relative_path` | GET | — | 文件流 | `../`、绝对路径、盘符、符号链接逃逸、URL 编码穿越 → 403 |

### 8.3 错误格式（统一）
```json
{ "code": "ERROR_CODE", "message": "适合用户阅读", "details": null }
```
状态码约定：400 参数 / 403 拒绝 / 404 不存在 / 409 版本冲突 / 413 过大 / 415 不支持类型 / 422 数据格式错 / 500 内部错误。前端不展示堆栈。

### 8.4 文件保存与并发
- 版本号 = `blake3:<hex>`（完整 64 位 hex，文件内容哈希）
- 原子保存：同目录临时文件 → 写全 → flush → sync_all → 原子替换 → 重算版本
- 禁止"打开-清空-写入"；任何写入带 `expected_version`；不一致一律 409，不自动覆盖
- 保存文章/转录成功时：同一原子流程内更新 manifest.json 的 `updated_at`

### 8.5 安全
- 所有文件访问四步：连根路径 → canonicalize → 验证仍在 item 根内 → 读取
- 上传：验证文件头（不只扩展名）、UUID 文件名、拒绝 SVG、限制请求体
- 媒体/图片 URL 全部后端生成，不向前端返回绝对路径

---

## 9. 前端设计

### 9.1 布局与滚动
- 全应用 `height:100vh; overflow:hidden`；左右各自滚动，无 body 滚动
- 左侧导航：展开 276px / 折叠 60px，160ms ease-out；折叠状态存 `reader.sidebar.collapsed`
- 阅读区 `overflow-y:auto; height:100vh`；内容容器 max-width 880px，padding top 32/左右 48/底部 96，水平居中

### 9.2 左侧导航
- 展开：产品名、折叠按钮、刷新按钮、文件夹列表（38px 高、10px padding、8px 圆角、14px、单行省略）
- 折叠：图标 + 刷新 + 文档图标（hover tooltip 显示名称）
- 选中态：低对比浅灰背景 + 深色文字，无彩色块、无粗左侧条

### 9.3 阅读区（DocumentPage）
- 文档头部：文件夹名（34px/600/1.16）、来源类型、来源外链、编辑文章按钮
- 外部链接一律 `target="_blank" rel="noopener noreferrer"`
- 状态机：loading / ready / empty / not_found / invalid_item / network_error / save_error / version_conflict / media_error
- 空态：未选择 →"从左侧选择一条内容开始阅读"；库空 →"资料库中还没有内容"+刷新按钮
- 无媒体：不渲染卡片不留占位

### 9.4 媒体卡片（StickyMediaCard）
- `position:sticky; top:16px; z-index:20`，不透明背景，滚动不穿透
- 视频：100% 宽、max-height 360px、16:9、纯黑底、16px 圆角；`preload="metadata" playsinline disablepictureinpicture`；自定义控制栏，无 controls 属性
- 音频：约 92px 高；标题、播放、时间、进度、音量、倍速、模式切换
- 控制：播放/暂停/拖进度/点击跳转/当前时间/总时长/音量/静音/倍速（0.75/1.0/1.25/1.5/2.0，默认 1.0，持久化 `reader.media.playbackRate`）
- 状态：idle/loading/ready/playing/paused/ended/error；错误文案固定"无法播放该媒体文件 / 文件可能不存在或编码不受浏览器支持"
- 保证格式：视频 MP4(H.264+AAC)、音频 MP3/M4A(AAC)；可选 WebM/OGG/WAV
- **切换模式不卸载播放器**（时间/播放/倍速/音量保持）

### 9.5 内容模式切换
- 卡片右上 `[文章] [时间线文本]`：都有→两选项默认文章；只有文章→不显示切换；只有转录→直接时间线；都无→错误空态
- 文章模式：滚动独立于媒体时间
- 时间线模式：首次进入才请求 transcript；切回不清理已加载数据；两模式各自记住滚动位置

### 9.6 转录同步（核心算法）
- `current_ms = currentTime * 1000`；当前段 = `start_ms <= current_ms < end_ms`；**二分查找**（findActiveSegment.ts），禁止全量遍历
- 只在 `timeupdate` 中 activeSegmentId 变化时更新；React 状态只存 `activeSegmentId`（禁止每段一个状态）
- 跟随开启（默认关）：变化时 `scrollIntoView({block:"center", behavior:"smooth"})` 一次
- 用户手动滚动 → 暂停自动跟随、保留高亮、顶部显示"继续跟随"按钮
- 点击段左侧时间戳 → 跳转 start_ms + 播放 + 高亮；点文字不跳转（保护选择）
- 展示：无卡片边框、段间距 12-16px、时间戳弱化等宽、说话人中字重；高亮 = 淡暖灰/淡蓝背景 + 2px 低饱和左侧线，不改变尺寸

### 9.7 转录编辑
- 顶部"编辑转录"；每段 textarea 自动增高；只可改 speaker/text
- 按钮切换为"保存修改/取消"；只提交变更段；成功后更新本地 version 退出编辑显示"已保存"；409 保留草稿 + 重新加载提示
- 键盘：Esc 取消当前段、Ctrl/Cmd+Enter 保存、Tab/Shift+Tab 移动焦点

### 9.8 Markdown 展示
- react-markdown + remark-gfm；禁用 rehype-raw；H1-6/段落/粗斜/删除线/行内码/代码块/列表/任务列表/引用/表格/链接/图片/分割线
- 图片：相对路径转 `/api/v1/items/:id/assets/...`；max-width 100%、保持比例、10px 圆角、上下 28px、`loading="lazy"`；失败占位"图片无法加载 + 路径"
- 图片删除：hover 右上 32px 半透明白垃圾桶按钮（有键盘焦点样式）→ 确认框 → 按 occurrence 删除；成功后用响应中的新 article 本地替换正文
- occurrence：AST 转换阶段按出现顺序编号 0,1,2...，删除必须携带 {src, occurrence, version}
- 链接安全：拒绝 javascript:/data:/file:/vbscript:；图片拒绝 data: base64

### 9.9 编辑器（CodeMirror 6）
- 按需加载（React.lazy + dynamic import），不在首包
- 支持：语法高亮、自动换行、中文输入、撤销/重做、搜索替换、当前行高亮、粘贴文字/图片、Tab 缩进、保存/取消
- 按钮：保存 / 预览（草稿交给 react-markdown 渲染，再点返回编辑）/ 取消（未变直接退出；变了弹"放弃未保存的修改？"确认）
- 粘贴图片：拦 paste → 检测图片文件 → 上传 → 光标处插入 → 失败不插入占位
- 文章手动保存，无自动保存

### 9.10 性能（继承原文档 29 节）
- 首屏不加载 CodeMirror / transcript / 视频全文 / 全部 Markdown / 图片原尺寸
- 按需：点击信息→manifest+article；切时间线→transcript；点编辑→CodeMirror
- ArticleView `React.memo`；媒体时间变化不触发 Markdown 重渲染
- 转录 ≤5000 段直接渲染，先不引虚拟列表

---

## 10. 视觉设计（写入 styles/tokens.css）

- 色彩：app-bg `#f5f5f2`、sidebar `#efefeb`、reading `#fbfbf9`、surface `#fff`；文字 primary `#191918` / secondary `#6b6b65` / tertiary `#969690`；border 9%/15% 透明度；hover/selected 5%/7.5% 透明度；accent `#356bd6`、accent-soft `#eaf0fc`、danger `#bc3f37`
- 圆角 8/12/16；卡片阴影轻量
- 正文 17px / 1.78 / -0.005em；中文段落垂直间距，无首行缩进；链接继承正文色+轻下划线，hover 变强调色
- 动画 120-180ms，仅颜色/透明度/侧栏宽/小位移；`prefers-reduced-motion: reduce` 时关闭
- 禁止：大面积渐变、玻璃拟态、霓虹蓝、发光、重投影、全卡片化、彩色 badge、粗描边、饱和蓝侧栏、悬浮按钮堆砌、纹理背景、儿童化圆润

---

## 11. 开发环境与命令

| 项 | 值 |
|---|---|
| mise | 项目内 `mise.toml`：rust 1.97.1、bun 1.3.14（node 兼容层由 mise 全局提供） |
| 前端 dev | `bun run dev`（vite，5173，proxy /api→3000） |
| 后端 dev | `cargo run`（3000） |
| 构建 | `bun run build` → `frontend/dist` 由 Rust 托管（--release 时若 dist 存在） |
| 质量门 | `bun run lint`、`bun run typecheck`、`bun run test`；`cargo fmt --check`、`cargo clippy -- -D warnings`、`cargo test` |
| 示例库 | `library.example/example-item/`：manifest + content + article + transcript + 生成的 16:9 测试 MP4（H.264+AAC，15-30s）+ M4A + 3 张图 + 校验用文件头样例；媒体文件提交 git |

---

## 12. 分阶段实施计划（每阶段末 git commit）

### 阶段一：项目骨架
任务：git init + .gitignore + mise.toml；backend cargo 工程（依赖见 5.2）+ config.rs（LIBRARY_ROOT 回落逻辑）+ error.rs 统一错误 + `/health`；frontend Vite react-ts + tokens.css/global.css + AppShell + 空路由 + vite proxy；验证：前端页面调 `/health` 显示后端版本；`cargo fmt/clippy` 通过。
验收：前端能调通后端 /health；生产构建由 Rust 托管（静态文件路由打通）。
Commit：`feat: echo phase 1 project skeleton`

### 阶段二：资料库与导航
任务：models（manifest/item/api）+ paths.rs 安全路径 + scanner + ItemIndex + AppState；`GET /items`、`GET /items/:id`、`POST /library/rescan`、`GET /library/problems`；导航排序规则；前端 Sidebar（折叠/展开/localStorage/tooltip/选中态/刷新）+ 路由 `/`、`/items/:itemId` + useItems + 各状态空态；library.example 落地（含 ffmpeg 生成媒体，提交 git）。
验收：导航显示文件夹、点击更新 URL、刷新恢复、折叠持久化、无效文件夹隔离。
Commit：`feat: echo phase 2 library and navigation`

### 阶段三：Markdown 阅读
任务：markdown/generate.rs（content.json→article.md，图片前后空行）+ assembler（article.md 存在则直读）+ 首生成写入；react-markdown + remark-gfm + MarkdownRenderer（自定义 a/img）+ ExternalLink + 图片 URL 转换 + ArticleView(memo) + 文档头部/来源。
验收：示例文章渲染正确、图片在正确位置、外链新标签、`article.md` 生成且二次不覆盖。
Commit：`feat: echo phase 3 markdown reading`

### 阶段四：媒体卡片
任务：media 路由（Range/206 自实现或 tower-http 文件服务 + 实测 MP4）；useMediaController（状态机/时间/倍速持久化/音量）；StickyMediaCard + AudioPlayer + VideoPlayer + MediaControls + ProgressSlider + PlaybackRateMenu + 错误卡片；ContentModeSwitch 占位。
验收：真实 MP4 播放/暂停/拖动/音量/倍速；滚动不中断；无媒体不渲染；错误态文案正确。
Commit：`feat: echo phase 4 media card`

### 阶段五：时间线转录
任务：transcript 路由（GET/PATCH：校验时间戳字段拒绝→400、id 不存在→400、JSON 坏→422、409 冲突、成功后原子更新 manifest.updated_at）+ models/transcript；前端 useTranscript（首次加载+缓存）、TranscriptView + TranscriptSegment + FollowPlaybackControl + findActiveSegment（二分）+ 高亮 + 跟随/手动滚动暂停/"继续跟随" + 点击时间戳跳转 + 双模式滚动位置记忆。
验收：切换加载一次、二分查找正确、跟随高亮、点击跳转播放、手动滚动暂停跟随、PATCH 响应契约符合决策 8。
Commit：`feat: echo phase 5 timeline transcript`

### 阶段六：编辑
任务：article PUT（版本冲突 409 + 成功后更新 manifest.updated_at）；前端 MarkdownEditor（CodeMirror 懒加载、高亮、搜索替换、预览、保存/取消、放弃确认）；转录编辑器（textarea 增高、Esc/Ctrl+Enter/Tab、只提交变更段、409 草稿保留）；粘贴图片接口对接。
验收：编辑保存不破坏图片位置；冲突不覆盖；CodeMirror 不进入首包。
Commit：`feat: echo phase 6 editing`

### 阶段七：图片操作
任务：images 路由（multipart、文件头验证、UUID、20MB、413/415）+ image_reference.rs（AST 解析、occurrence 删除、剩余引用检查、移 .trash、返回 `{article:{markdown,version}, saved_at}`）；前端 ImageBlock 删除按钮 + 确认框 + 粘贴上传流程。
验收：粘贴 4 种格式成功；SVG/伪造 MIME 拒绝；按 occurrence 删除；无引用文件进 `.trash`；响应契约符合决策 8。
Commit：`feat: echo phase 7 image operations`

### 阶段八：测试与视觉完善
任务：Rust 单元测试 12 组 + 集成测试 9 组（含 Range 0-1023 → 206/1024 字节、updated_at 更新、400/422/409 语义）；Vitest 9 组；Playwright 12 步集成测试（真实 MP4 播放、切换保持、偏差<0.2s）；reduced-motion；键盘可达性走查；README（中文，含安装/运行/LIBRARY_ROOT 说明）、docs/architecture.md、docs/api.md（含契约决策 8）、docs/file-format.md、THIRD_PARTY_LICENSES.md；全量质量门通过。
验收：全部验收标准（见 §14）满足；三项质量门零错误。
Commit：`feat: echo phase 8 tests and polish`

---

## 13. 测试计划

### 13.1 Rust 单元测试
manifest 解析（合法/缺字段/坏 JSON）；重复 ID；非法相对路径；URL 编码穿越；content.json→Markdown（各 block 类型+图片空行）；图片顺序；occurrence 删除（第 0 次/第 N 次/不存在）；BLAKE3 版本格式；版本冲突判定；transcript 时间顺序与约束校验；原子保存（替换后内容完整、临时文件清理、updated_at 更新）；无引用图片移 `.trash`。

### 13.2 Rust 集成测试（tests/）
`GET /items`（排序、无效项排除）；`GET /items/:id`（完整/缺 article/缺 transcript/404）；article PUT（成功、409、非法体 400、updated_at 更新）；transcript PATCH（改 text/speaker 成功、携带时间戳 400、id 不存在 400、JSON 坏 422、409、updated_at 更新）；图片上传（4 种合法 MIME、SVG 415、超限 413、伪造头 415）；图片删除（occurrence、无引用→.trash、响应契约）；媒体 Range（`bytes=0-1023` → 206、Accept-Ranges、Content-Range、长度 1024）；assets 穿越（`../`、绝对路径、盘符、编码穿越 → 403）；不存在文件 404。
> 每个测试用 `tempdir` 构建独立临时资料库，不污染 library.example。

### 13.3 前端单元测试（Vitest）
findActiveSegment 二分查找（命中/间隙/边界 start==end 开区间/首末段）；模式切换显示规则 4 分支；无媒体隐藏卡片；无转录隐藏切换；ExternalLink 属性（target/rel）与危险协议拒绝；图片相对路径→API URL 转换；倍速 localStorage 读写；sidebar 折叠 localStorage。

### 13.4 前端集成测试（Playwright）
1. 打开带视频 item → 2. 播放 → 3. 切时间线 → 4-6. 视频未重新加载（时间偏差<0.2s、播放状态保持）→ 7. 开启跟随 → 8. 当前段高亮正确 → 9. 点时间戳跳转 → 10. 编辑保存转录 → 11. 切回文章 → 12. 文章滚动位置恢复。
> 若 headless 下媒体加载受限：使用 chromium headless=new + 显式等待 media ready 事件；必要时 fallback 带 headed 运行说明。

---

## 14. 验收标准（对齐原文档 §34，六组全过才算 MVP 完成）

导航（扫描/显示/折叠/选中/持久化/URL）；阅读（排版/图片位置/外链/无媒体无区域）；媒体（播放暂停跳转/音量/倍速/无全屏无 PiP/固定/滚动不中断/模式切换不重置）；转录（切换/首载一次/跟随开关/时间映射/点击跳转/手动滚动暂停/可改 speaker+text/不可改时间戳/409）；文章编辑（懒加载/预览/保存取消/搜索替换/粘贴图片/图片位置不破坏/原子写/冲突不覆盖）；图片（懒加载/保比例/界面删除/occurrence 精确/无引用进 .trash/四格式粘贴/SVG 与伪造拒绝）。

---

## 15. 强制约束（不可违背，逐条写进 AGENTS 自检）

1. 媒体/转录不进 Markdown（无 Base64）；2. 转录不写进 article.md；3. 无数据库；4. 无 Next.js；5. 无 Tailwind；6. 无 UI 组件库；7. 无第三方播放器；8. 无富文本编辑器；9. 不启用原始 HTML；10. 前端不接触绝对路径；11. API 不可读资料库外文件；12. 模式切换不重挂播放器；13. 媒体时间不触发整文重渲染；14. article.md 存在时不得重生成；15. 冲突不自动覆盖；16. 图片先入 `.trash` 不硬删；17. 不扩大产品范围；18. TS strict；19. cargo fmt + clippy 通过；20. 前端 TS/ESLint/测试通过；21. 依赖锁 lockfile；22. THIRD_PARTY_LICENSES.md。

---

## 16. 许可证

MIT 栈：React、Vite、react-markdown、remark-gfm、CodeMirror、Axum（许可证原文链接收录）。交付 `THIRD_PARTY_LICENSES.md`，发布前核对所有依赖版权声明。

---

## 17. 风险与对策

| 风险 | 对策 |
|---|---|
| Windows 下原子替换/权限（AV 锁文件） | tempfile + rename 重试一次；失败返回 500 且原文件不损坏 |
| 中文/特殊字符文件名 URL 编码 | 后端统一 URL encode/decode，前端只传 ID + 相对路径，不做拼装 |
| 浏览器媒体兼容 | 仅保证 H.264+AAC MP4 / MP3 / M4A；用 ffmpeg 生成标准测试样本 |
| Playwright 媒体时间敏感断言 | 阈值 0.2s、等待 ready、避免 CPU 抖动时段断言 |
| 5000 segment 性能 | 二分查找 + 仅 activeSegmentId 状态更新；实测后必要时第二阶段虚拟化 |
| 首包体积 | CodeMirror 动态导入 + 图片懒加载 + 媒体 preload=metadata |
| manifest 更新竞态 | `updated_at` 与文章/转录同原子流程更新；manifest 被外部修改时保留其他字段仅改时间 |

---

## 18. 交付物清单

代码工程（frontend/backend）；`library.example/` 完整示例（含媒体，进 git）；`PROJECT_PLAN.md`；`README.md`（中文：安装、mise 环境、运行、LIBRARY_ROOT 配置）；`docs/architecture.md`、`docs/api.md`、`docs/file-format.md`；`THIRD_PARTY_LICENSES.md`；git 历史 8 个阶段 commit。
