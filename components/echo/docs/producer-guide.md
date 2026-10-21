# 内容生产指南（给内容处理 AI）

本指南说明如何把抓取/下载/转录/整理的结果编排成 Echo 可直接展示的资料库文件夹。**一个文件夹 = 一条信息**。

> 说明：本文件是写给"内容处理 AI/模块"的产出规范。展示端的读取规则见 [file-format.md](file-format.md)。

---

## 1. 总原则

1. **所有文件放进一个文件夹**，该文件夹直接放在资料库根目录（`LIBRARY_ROOT`）下。
2. **文件夹名用中文标题**，例如 `访谈：人工智能与个人知识管理`、`某期播客：xxx`。
3. 文件夹里可以同时有：视频、音频、文字（原文转录 + AI 整理文章）、图片。
4. 处理完成后，应用侧通过"刷新"或 rescan 接口即可看到（见第 9 节）。
5. **一个来源 URL 只产出一个 item**：同一页面中出现的多个媒体写入同一 manifest 的
   `primary_media` 与 `media_items`。这是 producer/MediaLab 的产出与去重政策；Echo 不会
   根据 URL 合并、拆分或推断 item。

## 2. 目录结构模板（完整形态）

```
访谈：人工智能与个人知识管理/
├── manifest.json        # 必须
├── article.md           # AI 整理后的文章（推荐直接产出成品）
├── content.json         # 可选：初始内容结构（无 article.md 时后端用它生成）
├── transcript.json      # 可选：带时间戳的转录原文
├── media/
│   ├── original.mkv     # 原始下载文件（保留原样）
│   ├── playback.mp4     # 浏览器播放版（必须转码，见第 6 节）
│   └── audio.mp3        # 可选：从视频提取的音频或独立音频
├── images/
│   ├── 0001.webp        # 文章插图（按引用顺序命名）
│   └── 0002.jpg
└── .trash/              # 不需要你创建，删除图片时应用自动使用
```

## 3. manifest.json（必须）

```json
{
  "schema_version": 1,
  "id": "01J4R7ZQ6YEPNXQFSX3B9S8A2V",
  "created_at": "2026-08-09T10:00:00+08:00",
  "updated_at": "2026-08-09T10:00:00+08:00",
  "source": {
    "type": "mixed",
    "url": "https://example.com/source",
    "title": "Original English Title",
    "author": "频道名",
    "published_at": "2026-08-01T08:00:00Z",
    "description": "正文、完整访谈与补充音频。"
  },
  "article": { "path": "article.md", "content_source": "content.json" },
  "transcript": { "path": "transcript.json", "language": "zh-CN" },
  "primary_media": {
    "type": "video",
    "original_path": "media/original.mkv",
    "playback_path": "media/playback.mp4",
    "mime_type": "video/mp4",
    "title": "原始访谈视频"
  },
  "media_items": [
    {
      "id": "primary",
      "role": "primary",
      "type": "video",
      "title": "原始访谈视频",
      "original_path": "media/original.mkv",
      "playback_path": "media/playback.mp4",
      "poster_path": null,
      "mime_type": "video/mp4"
    },
    {
      "id": "bonus-audio",
      "role": "attachment",
      "type": "audio",
      "title": "补充音频",
      "description": "访谈后的听众问答。",
      "original_path": "media/bonus-original.wav",
      "playback_path": "media/bonus.mp3",
      "poster_path": null,
      "mime_type": "audio/mpeg"
    }
  ]
}
```

### 字段规则

| 字段 | 规则 |
|---|---|
| `id` | **必须稳定唯一**：用固定前缀 + 内容标识（如 `vid-<来源平台>-<视频ID>` 或 UUID）。文件夹重命名后 id 不变；重复 id 会被报告为资料问题，不能依赖扫描顺序选择其中一个 |
| `source.type` | `article` / `video` / `audio` / `mixed`；正文与媒体共存或媒体多于一个时填 `mixed` |
| `source.url` | 可持久化的来源链接（展示在阅读页头部“查看来源”）；写入前应删除 fragment、临时签名、凭据与常见追踪参数，但保留决定内容身份的业务 query |
| `source.title` | 原标题（如英文视频的原始标题），作为副标题展示在阅读页头部 |
| `source.author` | 作者/频道名（可选，展示在阅读页头部） |
| `source.published_at` | 发布时间（可选，展示在阅读页头部） |
| `source.description` | 条目简介（可选，展示在阅读页头部） |
| `primary_media` | 主媒体兼容字段；纯文章或没有主播放器时填 `null`。**一个文件夹最多一个主媒体** |
| `media_items` | 完整媒体集合，可省略。主媒体也以 `role: primary` 写入并镜像到 `primary_media`；其他媒体用 `inline` / `attachment` |
| `mime_type` | 必须是浏览器真实能播的格式对应值（见第 6 节） |

当前仍使用 `schema_version: 1`。`source.description` 与 `media_items` 是加法字段，旧产物
缺少它们时分别按空值与空数组处理。

### `media_items` 字段规则

- `id`：去除首尾空白后非空并全局唯一；建议使用稳定、URL 友好的标识（如 `bonus-audio`）
- `role`：只能是 `primary` / `inline` / `attachment`。额外媒体通常用 `inline` 或
  `attachment`；`primary` 不会在 Echo 的附加媒体列表重复显示
- `type`：只能是 `video` / `audio`
- `title`、`description`：可选展示文字
- `original_path`、`playback_path`：必填；`poster_path` 可选；`mime_type` 必填且与播放文件一致
- 上述所有媒体路径都必须是 item 文件夹内的安全相对路径，目标必须已存在且是文件；禁止
  绝对路径、盘符、`..` 和符号链接逃逸

> **抓取元数据（meta.json）已废弃**：原 meta.json 中的标题、作者、发布时间、来源链接等全部并入 manifest.json 的 `source` 字段，不要再单独产出 meta.json。

### 三种主媒体形态

```json
// 纯文章
"primary_media": null

// 视频
"primary_media": { "type": "video", "original_path": "media/original.mkv", "playback_path": "media/playback.mp4", "mime_type": "video/mp4", "title": "原始视频" }

// 音频
"primary_media": { "type": "audio", "original_path": "media/original.mp3", "playback_path": "media/playback.mp3", "mime_type": "audio/mpeg", "title": "播客音频" }
```

## 4. 文字内容：两套并存

同一个文件夹内同时放：

1. **`transcript.json`**：带时间线的转录**原文**（供时间线模式校对）
2. **`article.md`**：AI 整理后的**文章**（供阅读模式展示，默认视图）

两者可以并存：阅读页默认显示文章，右上角可切换到时间线文本。

### 语言规则（重要）

- **`article.md` 是唯一正文文件，语言以中文为主**：外文内容（如英文视频）整理成中文文章，直接写入 `article.md`；原标题放 `manifest.json` 的 `source.title`（显示为副标题）。不产生 `article.zh.md`、`article.en.md` 等多语言文件。
- **转录原文保留原始语言**：`transcript.json` 里的文字按原样保存（如英文转录就存英文），用 `language` 字段声明（如 `"en"`）。时间线模式展示原文用于逐句校对，与中文文章互不干扰。

### transcript.json

```json
{
  "schema_version": 1,
  "language": "zh-CN",
  "media_id": "primary",
  "segments": [
    { "id": "s0001", "start_ms": 0, "end_ms": 4280, "speaker": "主持人", "text": "欢迎大家来到今天的节目。" },
    { "id": "s0002", "start_ms": 4280, "end_ms": 9120, "speaker": "嘉宾", "text": "今天我们讨论个人知识管理。" }
  ]
}
```

约束：

- `start_ms`/`end_ms` 用**毫秒**；`end_ms > start_ms`；段按 `start_ms` 升序
- `id` 非空且唯一（建议 `s0001` 起顺序编号）
- 段落允许有间隔（静音段可省略）

### article.md（推荐直接产出成品）

应用优先读 `article.md`。**一旦它存在，即使以后 content.json 变化也不会覆盖**（防止覆盖用户修改）。因此：

- **首次产出时直接写 `article.md`**，标准 Markdown（标题、段落、列表、引用、粗体、链接、图片均可）
- 图片用相对路径：`![说明](images/0001.webp)`，图片必须真实存在于 `images/`
- 外部链接直接用 `https://...`（应用会新标签打开）

### content.json（可选，二选一）

如果不方便直接生成 `article.md`，可以产出带顺序的内容结构，后端会在第一次打开时生成 `article.md`：

```json
{
  "schema_version": 1,
  "blocks": [
    { "type": "heading", "level": 1, "text": "人工智能与个人知识管理" },
    { "type": "paragraph", "markdown": "第一段……" },
    { "type": "image", "path": "images/0001.webp", "alt": "访谈现场", "caption": "嘉宾在访谈现场" },
    { "type": "blockquote", "markdown": "技术的目标应当是减少人的认知负担。" }
  ]
}
```

允许的块类型：`heading`(1-6) / `paragraph` / `image` / `blockquote` / `unordered_list` / `ordered_list` / `code` / `horizontal_rule`。**两种方式选一种即可，推荐直接产 `article.md`**。

## 5. 图片

- 从正文中提取的插图放 `images/`，文件名建议按出现顺序 `0001.webp`、`0002.jpg`……
- 允许格式：JPEG / PNG / WebP / GIF
- `article.md` 中的图片路径必须与文件名一致，且是相对路径（`images/xxx.webp`）
- 图片在文章中的位置 = 引用它的 Markdown 位置，请按内容逻辑插入

## 6. 媒体转码要求（重要）

网页播放器只读取 `playback_path` 指向的文件，且**只保证以下编码**：

| 类型 | 保证支持 | 推荐命令（ffmpeg） |
|---|---|---|
| 视频 | MP4，H.264 视频 + AAC 音频 | `ffmpeg -i in.mkv -c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart out.mp4` |
| 音频 | MP3、M4A（AAC） | `ffmpeg -i in.wav -c:a aac -b:a 128k out.m4a` |

规则：

- 原始下载文件保留为 `media/original.*`（不转码）
- **播放文件必须转成上表格式**，否则浏览器无法播放
- **可跳过转码的情况**：源文件本身已是 H.264+AAC 的 MP4（或 MP3/M4A），可直接复制为 playback 文件，不重转（省时）
- 从视频提取的音频：如需在 Echo 页面播放，转成 MP3/M4A 后写入 `media_items`；仅作为未展示的内部中间产物时可不声明
- `mime_type` 与播放文件一致：MP4 → `video/mp4`，MP3 → `audio/mpeg`，M4A → `audio/mp4`

## 7. 缺少某些内容时的形态

| 场景 | manifest | 文件 |
|---|---|---|
| 网页正文 + 视频/音频 + 转录 | `primary_media` 有值，source.type 填 mixed | media/ + transcript.json + article.md |
| 只有视频 + 转录（暂无文章） | 同上 | media/ + transcript.json（应用自动进入时间线模式） |
| 网页文章（无媒体无转录） | `primary_media: null`，type 填 article | 只 article.md（+ images/） |
| 音频 + 转录 | `primary_media` 音频 | media/ + transcript.json |
| 网页正文 + 多个音视频 | `source.type: mixed`；选一个 `primary_media`，其余写入 `media_items` | article.md + media/（可再含 transcript.json） |

## 8. 幂等与重跑规则

- **再次处理同一内容时，不要覆盖已存在的 `article.md`**（用户可能已修改）。若正文引用本地图片，应把旧正文与其整套旧 `images/` 一起保留，不能让相同文件名的新图片悄悄替换正文所指字节；媒体和转录可按生产者的完整快照策略更新
- `id` 保持不变（同一内容重复抓取用相同 id）
- 转录段可以整体重写（时间戳不保证跨版本稳定）

## 8.1 同名文件夹（防混淆）

应用按 `manifest.id` 区分条目，**文件夹名相同不会冲突**（导航里会显示两条同名列）。但为避免用户混淆，建议：

- 同一标题的不同内容（如两个同名视频），文件夹名加后缀：`标题`、`标题-2`、`标题-3`
- 同一内容的重复抓取保持原名（id 相同，覆盖式更新）

## 8.2 旧产物

历史任务在旧目录布局下产生的产物**不迁移**；本规范只约束新任务。

## 9. 产出后通知应用

新文件夹写入资料库后，应用不会自动感知。两种方式任选：

```text
方式一：调用接口通知
POST /api/v1/library/rescan
响应：{ "item_count": 35, "invalid_item_count": 2 }

方式二：用户手动点击侧边栏"刷新"按钮
```

如果处理模块无法调用 HTTP 接口，请在产出完成后提示用户点一次刷新。

## 10. 最小可用示例（纯文章）

```
某篇网页文章/
├── manifest.json
└── article.md
```

```json
{
  "schema_version": 1,
  "id": "art-20260809-001",
  "created_at": "2026-08-09T10:00:00+08:00",
  "updated_at": "2026-08-09T10:00:00+08:00",
  "source": { "type": "article", "url": "https://example.com/article" },
  "article": { "path": "article.md" },
  "transcript": { "path": "transcript.json" },
  "primary_media": null
}
```
