# 资料库文件格式

资料库根目录由 `LIBRARY_ROOT` 环境变量指定。每个一级子文件夹对应一条独立信息。

```text
content/
├── 访谈：人工智能与个人知识管理/
├── 某篇网页文章/
└── 某期播客/
```

## 单条信息的标准目录

```
访谈：人工智能与个人知识管理/
├── manifest.json      # 必须存在
├── content.json       # 可选：初始文章顺序（不可变生成来源）
├── article.md         # 可选：AI 文章与用户修改后的正文（唯一真实来源）
├── transcript.json    # 可选：带时间戳的原始转录
├── media/
│   ├── original.mkv   # 原始媒体
│   └── playback.mp4   # 浏览器播放版本（网页播放器只使用它）
├── images/            # 文章图片
└── .trash/            # 被删除但尚未永久清理的图片
```

## manifest.json

```json
{
  "schema_version": 1,
  "id": "01J4R7ZQ6YEPNXQFSX3B9S8A2V",
  "created_at": "2026-08-06T20:00:00+08:00",
  "updated_at": "2026-08-06T20:00:00+08:00",
  "source": {
    "type": "mixed",
    "url": "https://example.com/source",
    "description": "访谈正文、完整视频与补充音频。"
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

规则：

- `id` 必须稳定，文件夹重命名后不变；API 路径使用该 ID；重复 id 会被报告为无效资料问题，不能依赖扫描顺序选择其中一个
- 当前 schema 仍为 `schema_version: 1`；新增字段是向后兼容的加法扩展
- `source.type` 允许 `article` / `video` / `audio` / `mixed`；`mixed` 是独立类型，不由 Echo 根据文件组合推断
- `source` 可选字段：`title`（原标题/副标题）、`author`、`published_at`、`description`（条目简介）；详情 API 缺失字段返回 `null`
- `primary_media` 是页面的主媒体兼容字段，可为 `null`；它继续使用 `/api/v1/items/:id/media` 播放
- `media_items` 是完整媒体集合（可包含与 `primary_media` 对应的 `role: primary` 项）；旧 manifest 可省略（等同空数组）。每项字段规则如下：

| 字段 | 规则 |
|---|---|
| `id` | 去除首尾空白后必须非空并全局唯一；作为 API URL 中的稳定媒体标识 |
| `role` | `primary` / `inline` / `attachment`；`role: primary` 不会在详情 API 的附加媒体列表中重复展示 |
| `type` | `video` / `audio` |
| `title`、`description` | 可选的展示文本 |
| `original_path`、`playback_path` | 必填的资料库内相对路径 |
| `poster_path` | 可选的资料库内相对路径，未提供时填 `null` 或省略 |
| `mime_type` | `playback_path` 文件的有效 MIME 类型 |

- manifest 中声明的所有媒体路径（主媒体与 `media_items` 的原始/播放文件及可选海报）都必须是安全的相对路径；目标必须存在且是普通文件，不能使用绝对路径、盘符、`..` 或符号链接逃逸
- 网页播放器必须使用 `playback_path`，不能假设原始文件可在浏览器播放
- `updated_at` 在保存文章/转录成功后由后端原子更新（决定导航排序）
- 文件夹名允许重复（按 id 区分条目），建议同名内容加后缀（`标题-2`）避免混淆

## content.json

内容处理模块输出的原始文章结构（首次生成 `article.md` 后不再读取）。

```json
{
  "schema_version": 1,
  "blocks": [
    { "type": "heading", "level": 1, "text": "人工智能与个人知识管理" },
    { "type": "paragraph", "markdown": "第一段，支持 **粗体** 和 [链接](https://example.com)。" },
    { "type": "image", "path": "images/0001.webp", "alt": "访谈现场", "caption": "嘉宾在访谈现场" },
    { "type": "blockquote", "markdown": "技术的目标应当是减少人的认知负担。" },
    { "type": "unordered_list", "markdown": "- 甲\n- 乙" },
    { "type": "ordered_list", "markdown": "1. 一\n2. 二" },
    { "type": "code", "markdown": "fn main() {}" },
    { "type": "horizontal_rule" }
  ]
}
```

允许的 block 类型：`heading`(1-6) / `paragraph` / `image` / `blockquote` / `unordered_list` / `ordered_list` / `code` / `horizontal_rule`。禁止嵌套任意 HTML。

Markdown 生成规则：块之间空行分隔；图片前后各保留一个空行；blockquote 逐行加 `> ` 前缀。

## transcript.json

```json
{
  "schema_version": 1,
  "language": "zh-CN",
  "media_id": "primary",
  "segments": [
    { "id": "segment_000001", "start_ms": 0, "end_ms": 4280, "speaker": "主持人", "text": "欢迎大家来到今天的节目。" }
  ]
}
```

数据约束：

- `id` 非空且唯一
- `start_ms >= 0`，`end_ms > start_ms`
- segments 按 `start_ms` 升序；相邻段落允许间隔与轻微重叠
- `text` 不得为 null

通过 API 只允许修改 `speaker` 和 `text`，不允许修改 `start_ms` / `end_ms` / `id`。

## 可选生成文件

以下文件由可选功能生成，缺失时一切照常。

### ocr.json

图片文字识别结果，由导入服务 `POST /api/v1/items/{id}/ocr` 写入条目根目录，并进入全文检索：

```json
{
  "schema_version": 1,
  "engine": "rapidocr",
  "created_at": "2026-10-21T09:49:37+00:00",
  "images": [
    { "path": "images/0001.webp", "text": "识别出的文字", "confidence": 0.99 },
    { "path": "images/0002.webp", "text": "", "error": "单张失败的原因" }
  ]
}
```

### 朗读音频

`POST /api/v1/items/{id}/tts` 把文章合成为 `media/tts-article.wav`（单声道 16-bit PCM），并在
`manifest.json` 的 `media_items` 中写入（或替换）一项：

```json
{ "id": "tts-article", "role": "attachment", "type": "audio",
  "original_path": "media/tts-article.wav", "playback_path": "media/tts-article.wav",
  "mime_type": "audio/wav", "title": "文章朗读（Kokoro）" }
```

### 全文索引

全文索引不属于资料库格式：它保存在资料库之外的缓存目录，可以随时删除并从上述文件重建。
