# MediaLab

MediaLab 是进程内 Python 内容生产组件（Python ≥3.10）。给它一个 URL，现役 `submit()` 会先识别
页面实际暴露的正文、图片与有限音视频，再把它们发布成 Echo 可扫描的一个资料条目。它没有公网
HTTP API；Echo Reader 的本机导入控制面位于根 `src/echo_reader/`。

当前技术合同以 [ARCHITECTURE.md](docs/ARCHITECTURE.md) 和源码为准。历次交付、失败方案和经验
保存在 [HANDOFF](docs/HANDOFF.md)、[DELIVERY](docs/DELIVERY.md)、
[ROADMAP](docs/ROADMAP.md)、[DECISIONS](docs/DECISIONS.md) 与 [LESSONS](docs/LESSONS.md)，这些
文件是历史证据，不是现役接口说明。跨组件状态看根 [HANDOFF v5](../../docs/HANDOFF.md)。

## 现役自动入口

```python
handle = await service.submit("https://example.com/content")
task = await handle.wait(service)
```

`submit()` 不再先按平台二选一地决定“文章或视频”。非小红书 URL 的处理顺序是：

1. 静态页面 capture，并用纯 HTML probe 提取正文、图片、Open Graph、JSON-LD、HTML5
   video/audio/source、iframe/embed 与 oEmbed 线索；直链媒体跳过页面 capture。
2. 按平台策略选择性用本机 Edge 做有界 JS 渲染，再对渲染 HTML 重跑同一个 probe。
3. 最多展开一个 JSON oEmbed；随后对原 URL 和发现的 video/audio/embed URL 使用 yt-dlp
   dry-run resolver，去重后下载全部有限媒体。
4. 将正文/图片和成功下载的媒体写入同一个稳定-ID item。正文与媒体并存，或媒体多于一个时，
   `source.type` 为 `mixed`。

媒体 resolver 上限默认 50；超过上限明确失败，不静默截断。文章图片另受 `[article].max_images`
（默认 100）约束。每个 manifest 可用 `media_items` 声明多媒体，兼容消费者仍可读
`primary_media`；页面或媒体描述写入 `source.description`，单个媒体描述也可写入对应
`media_items[].description`。

JS 默认策略是微信 `always`，知乎和头条 `on_miss`，其他平台 `off`。微信渲染失败会使任务失败，
避免把缺少懒加载内容的静态页误报为完整导入；知乎/头条渲染失败保留静态 probe 并记录 warning。
渲染使用系统已安装的 Edge，不下载浏览器，也不使用持久 profile。

`router.py` 只服务显式旧辅助/兼容调用；它不再是 `submit(auto)` 的事实权威。

## 小红书单次 capture

小红书必须从 Chrome SessionBridge 取得 Cookie。一个任务创建一个独立 Chrome 146 curl session，
页面只做一次应用层 GET，并从同一份 HTML 的 `__INITIAL_STATE__` 同时取得正文、图片和视频类型：

- `: undefined` 会在解析前规范化为 JSON `null`。
- `//` 和 `http://` 的小红书 CDN 资源会先升级为 HTTPS，再做域与网络地址校验。
- 图文从 `imageList.infoList` 选择最高清 scene 并去重；没有时才用 `url/urlDefault`。下载后读取
  PNG/JPEG/GIF/WebP 的真实尺寸，任一边小于 64 px 或格式不可识别均失败，避免占位图伪装成功。
- 视频按 EF7 → EF6 → EF5 → EF4 选一个 stream，只下载一次；不改走另一流或图文 fallback。
- 同一 HTML 可同时产生 article/images 与单个视频，因此最终也可能是 `mixed`。
- 持久化 URL 会删除 fragment、token/signature/xsec/utm 等敏感 query；保留其余可能参与内容身份的
  query。跨源资源 Referer 只保留页面 origin，避免把 query 凭据带到 CDN。

## 其他显式能力

- `download()`：显式 yt-dlp 下载/转音频，支持 B站、抖音、YouTube、X、TikTok、Vimeo、Twitch、
  Instagram、Facebook 与 generic。
- `scrape()`：显式旧文章辅助入口。
- `transcribe()`：下载 → 转音频 → 切片/可选倍速 → OSS → 千问 ASR → `transcript.json` →
  可选文章化润色。
- `transcribe_local()`：OpenVINO multilingual Whisper-small INT8（Intel GPU）+ 可选 Silero VAD 与
  sherpa-onnx 匿名说话人分离；不使用 OSS、在线 ASR 或在线润色。
  `[local_asr].engine = "funasr_nano"` 时改用可选的 Fun-ASR-Nano（FunASR，`medialab[funasr]`
  extra；CPU/CUDA/MPS/XPU 自动选择）+ CAM++ 说话人分离。模型由 FunASR 下载到用户模型缓存。
- 文章化润色后端由 `[refine].provider` 选择：`dashscope`（默认）、任意 `openai_compatible`
  服务或本机 `ollama`；未配置时润色步骤跳过，不影响转写。
- 微博：`WeiboAdapter` 支持移动端 `$render_data`、桌面页面状态、头条文章与 DOM 兜底。
- Cookie 健康检查：`medialab.cookie_health.check_cookie_health()` 与
  `CredentialBroker.health_report()` 只检查内存快照里的 Cookie 名称与过期时间，不联网、不返回
  Cookie 值；导入服务通过 `GET /api/v1/credential-health` 暴露。

以上四项移植自独立项目 UP（Unified Processor），已改写为 MediaLab 的适配器/配置契约。

可选增强（缺依赖时 `probe_*()` 报告原因，其他功能不受影响）：

- `medialab.tts`：Kokoro 离线朗读（`medialab[tts]`：kokoro-onnx 0.5、onnxruntime、numpy）。
  模型 `kokoro-v1.0.onnx` / `voices-v1.0.bin` 不在仓库中，用 `scripts/download_kokoro_models.py`
  下载；`ECHO_TTS_MODELS_PATH` 或 `[tts] model_dir` 指定目录。中文占比高时自动用 `cmn` + `zf_xiaoxiao`。
- `medialab.ocr`：图片文字识别。本地 RapidOCR（`medialab[ocr]`，PP-OCR ONNX 模型随 wheel 提供），
  或 PaddleOCR-VL 云端任务 API（`[ocr] engine = "paddleocr_api"`，需 `allow_cloud = true` 与
  `secrets.toml [ocr].token`，会上传图片）。

## 发布与默认目录

一个原始 URL 对应一个由“检测平台 + 脱敏后的原始 URL”派生的稳定 manifest ID。默认发布根是
monorepo 根 `content/`；独立安装时是当前工作目录的 `content/`。`LIBRARY_ROOT`、配置文件和宿主
`paths.delivery` override 可按加载优先级覆盖。旧 `delivery/` 内容不会自动迁移。

```text
content/<标题>/
├── manifest.json
├── article.md
├── transcript.json
├── media/original.*
├── media/playback.*
└── images/*
```

发布先在同一资料根写 `.staging-<task>` 完整快照，校验 manifest 和相对文件引用后再提升。重抓同一
ID 会先合并需保留的用户 `article.md`/旧补充文件，再以可回滚的 old→backup、staging→target 交换
替换；若本次媒体只部分成功，不会覆盖已有完整条目。

## 会话、网络与安全边界

Chrome 扩展只导出授权域 Cookie 和 profile UA；页面、图片、视频仍由 Python 或隔离 Edge 发出。
进程级 `CredentialBroker` 缓存 generation/revision 快照并用 CAS 回写，快照不永久落盘。文章使用
`curl_cffi==0.15.0` 的 Chrome 146 impersonation；视频使用 yt-dlp 官方 curl-cffi handler。0.15.0
是与锁定的 yt-dlp 2026.7.4 的兼容上限，不能单独升级到 0.16。

Python capture/resource 路径拒绝非 HTTPS、用户信息、非 443 端口、IP literal、本地 hostname，并
在请求前要求 DNS 答案全部是 global 地址。但当前实现让 curl 执行 `CurlFollow.SAFE` 后再校验
history/final URL，没有逐跳手动 redirect，也没有把已验证 DNS 地址 pin 到实际连接；这是明确的
剩余 SSRF/重绑定边界。Edge 渲染器也只做 URL/host allowlist，不做连接 IP pin。不要把这些检查
描述为完整的逐跳网络隔离。

真实微信、知乎、头条、小红书、oEmbed 与多媒体第三方 E2E 尚未在本轮自动验证；单元测试不能替代
线上页面结构、风控和重定向行为的实测。

## 配置与运行

默认配置位于 `src/medialab/config/default_config.toml`。重点分组：`[platforms]`、`[download]`、
`[article]`、`[js_render]`、`[content_capture]`、`[paths]`、`[oss]`、`[asr]`、`[local_asr]`、
`[refine]`。密钥只放真实 `secrets.toml`；模板在
`src/medialab/config/secrets.toml.example`。

```powershell
uv run --package medialab python components\medialab\examples\session_bridge_demo.py <url>
uv run python -m pytest components\medialab\tests -q
```

完整仓库质量门使用根 `scripts/verify.ps1`。
