# Echo Reader

[English](README.md) | **简体中文**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB.svg)](pyproject.toml)
[![Rust 2024](https://img.shields.io/badge/rust-2024_edition-orange.svg)](components/echo/backend/Cargo.toml)
[![GitHub](https://img.shields.io/badge/GitHub-PakhomLeo%2Fecho--reader-181717.svg?logo=github)](https://github.com/PakhomLeo/echo-reader)

Echo Reader 是一个本地优先的网页内容阅读应用。粘贴文章、帖子、播客或视频链接，它会把内容
保存为你磁盘上的普通文件（Markdown 正文、图片、媒体、带时间轴的转写），并提供阅读器、全文
检索、转写与播放器联动、Markdown 编辑，以及可选的朗读和图片文字识别（OCR）。

所有功能都在你自己的电脑上运行：没有账号、没有服务器、没有遥测。云端服务（在线语音识别、
大模型整理、云端 OCR）都是可选项，不配置就不会启用。

仓库地址：<https://github.com/PakhomLeo/echo-reader>。本项目（包名 `echo-reader`）与 GitHub 或其他地方
同名、近似名称的项目没有任何关系。

![架构图](docs/assets/architecture.svg)

## 功能

- **批量导入**：一次粘贴最多 50 个链接（或导入 `.txt` / `.csv`），每个链接形成一个稳定的资料条目。
  任务持久保存在 SQLite 中，重启后仍在，可在任务页重试、取消或重新索引。
- **正文抽取**：通用网页使用 Trafilatura；另有微信公众号、小红书、知乎、今日头条、微博、
  X/Twitter 的站点适配器。
- **视频与音频**：通过 yt-dlp 下载（Bilibili、YouTube、抖音及其他 yt-dlp 支持的网站），用 FFmpeg
  提取音频并转写。
- **语音识别**
  - 本地：OpenVINO Whisper-small INT8 + Silero VAD + 匿名说话人分离（sherpa-onnx），或通过 FunASR
    运行 Fun-ASR-Nano（CPU / CUDA / MPS / XPU）；
  - 在线（可选）：阿里云 DashScope 语音识别，音频经你自己的 OSS 存储桶中转。
  质量门（字符密度与时间轴覆盖率）会拒绝基本没有内容的转写（例如纯音乐视频），不会把它当成功发布。
- **可选的大模型整理**：用 DashScope、任意 OpenAI 兼容接口或本机 Ollama，把原始转写整理成文章。
- **阅读器**：React 界面，支持 Markdown 渲染、CodeMirror 编辑器、图片上传、点击转写跳转播放、
  编辑转写。
- **全文检索**：SQLite FTS5 索引（支持中日韩文字），覆盖标题、正文、转写和 OCR 文本。索引只是
  从资料库派生的缓存，随时可以重建。
- **朗读（可选）**：离线 Kokoro-82M 语音合成（`tts` extra）。
- **图片文字识别（可选）**：本地 RapidOCR（`ocr` extra），或在你明确允许上传后使用 PaddleOCR-VL 云端 API。
- **浏览器会话桥（可选）**：Chrome 扩展，把**你授权的网站**的 Cookie 借给本机导入服务，用于需要
  登录才能访问的内容。Cookie 只保存在内存中，不写入资料库。
- **Windows 桌面版**：每用户安装包；Rust 单源网关、私有 Python 导入服务、每次启动随机生成的
  会话 token，以及单实例处理。

## 工作方式

![导入流程](docs/assets/import-flow.svg)

仓库是一个 monorepo，包含两个相互独立的组件和一个集成层：

| 路径 | 作用 | 技术栈 |
|---|---|---|
| `components/medialab` | 生产者：探测、下载、抽取、转写并发布条目 | Python 3.12、yt-dlp、curl_cffi、Trafilatura、OpenVINO / FunASR |
| `components/echo/backend` | 网关与阅读 API：扫描资料库、提供媒体、检索索引、代理导入 API | Rust、Axum、rusqlite（FTS5） |
| `components/echo/frontend` | 阅读界面 | React 19、Vite、CodeMirror 6 |
| `src/echo_reader` | 导入服务（批次、任务、增强功能）、桌面宿主、迁移 | FastAPI、SQLite |
| `components/medialab/browser-extension` | 可选的会话桥（Chrome MV3） | JavaScript |

**资料库目录是唯一的数据来源**：每个条目是一个目录，包含 `manifest.json`，以及可选的 `article.md`、
`transcript.json`、`images/`、`media/` 和 `ocr.json`。Echo 与 MediaLab 互不导入对方代码；
文件格式（[`components/echo/docs/file-format.md`](components/echo/docs/file-format.md)）和 HTTP API
（[`components/echo/docs/api.md`](components/echo/docs/api.md)）就是两者之间的合同。

## 环境要求

- Python 3.12 与 [uv](https://docs.astral.sh/uv/)
- Rust（stable，edition 2024），含 `rustfmt` 和 `clippy`
- 前端使用 [Bun](https://bun.sh/)；运行 Vitest 需要 Node.js ≥ 22
- 导入音视频需要 `PATH` 中有 FFmpeg
- 可选：用 [mise](https://mise.jdx.dev) 按 `mise.toml` 安装固定版本的工具

本地 Whisper 识别通过 OpenVINO 使用 Intel GPU，不会悄悄退回 CPU；Fun-ASR-Nano 可在 CPU、CUDA、
Apple MPS 或 Intel XPU 上运行。

## 快速开始（Linux / macOS）

```bash
git clone https://github.com/PakhomLeo/echo-reader.git echo-reader && cd echo-reader
uv sync                                   # Python workspace（按需加 --extra local-asr 等）
(cd components/echo/frontend && bun install --frozen-lockfile && bun run build)
mkdir -p content .runtime

# 终端 1：导入服务（只监听本机回环地址）
uv run echo-reader-service --host 127.0.0.1 --port 3100 \
  --library-root content --runtime-root .runtime --echo-url http://127.0.0.1:3000

# 终端 2：Rust 网关 + 阅读界面，地址 http://127.0.0.1:3000
cd components/echo/backend
LIBRARY_ROOT=../../../content IMPORT_API_URL=http://127.0.0.1:3100 cargo run --release
```

浏览器打开 <http://127.0.0.1:3000>。开发前端时在 `components/echo/frontend` 运行 `bun run dev`
（Vite 监听 5173，并把 `/api` 代理到网关）。

## 快速开始（Windows 源码）

```powershell
mise install
.\scripts\setup.ps1
.\scripts\run-reader.ps1
```

`scripts\build-package.ps1 -Version <x.y.z>` 构建免安装程序束；装有 Inno Setup 时同时生成每用户
安装包。详见[安装与本机运行](docs/installation.md)和[开发指南](docs/development.md)。

## 可选功能

| 功能 | 安装 | 说明 |
|---|---|---|
| 本地 Whisper 识别 | `uv sync --extra local-asr` | 首次使用时按固定 revision 下载 `OpenVINO/whisper-small-int8-ov` 并校验 |
| Fun-ASR-Nano 识别 | `uv sync --extra funasr` | 设置 `[local_asr].engine = "funasr_nano"`；会安装 PyTorch。默认不执行模型仓库代码（`trust_remote_code = false`） |
| 朗读 | `uv sync --extra tts`，再运行 `uv run python scripts/download_kokoro_models.py` | 下载约 350 MB 的 Kokoro v1.0 文件并校验固定的 SHA-256。该 extra 只会在你自己的电脑上安装 GPL-3.0 依赖（eSpeak NG、phonemizer-fork），本项目从不随附分发，见[许可证](#许可证) |
| 本地 OCR | `uv sync --extra ocr` | RapidOCR，PP-OCR ONNX 模型随 wheel 提供 |
| 云端 OCR | `[ocr] engine = "paddleocr_api"`、`allow_cloud = true`、HTTPS `endpoint`，token 写在 `secrets.toml` | **会把条目图片上传到所配置的服务** |
| 在线识别 / 大模型整理 | 把 `components/medialab/src/medialab/config/secrets.toml.example` 复制为 `config/secrets.toml` | 音频会上传到你的 OSS 存储桶和 DashScope；整理也可改用 OpenAI 兼容接口或本机 Ollama |

缺少可选依赖或模型时，API 返回 `503 feature_disabled` 并说明原因，界面隐藏对应入口，其他功能不受影响。

## 配置

- `config/secrets.toml`（已被 Git 忽略）：API key 和 token，切勿提交。
- `components/medialab/src/medialab/config/default_config.toml`：带注释的默认配置；在
  `config/config.toml` 中覆盖。
- 网关环境变量：`LIBRARY_ROOT`、`IMPORT_API_URL`、`ECHO_BIND`（仅限回环地址）、`ECHO_WEB_ROOT`、
  `ECHO_SEARCH_INDEX`、`ECHO_PUBLIC_TOKEN`、`ECHO_INTERNAL_TOKEN`。
- 朗读模型目录：`ECHO_TTS_MODELS_PATH` 或 `[tts] model_dir`。

## 安全模型

Echo Reader 是单用户本机应用。

- 网关和导入服务都拒绝监听非回环地址；拒绝 `Host` 不是回环地址的请求（防 DNS rebinding）；
  对修改状态的请求，拒绝 `Sec-Fetch-Site: cross-site` 和不匹配的 `Origin`。
- 桌面版另外为每次启动生成随机会话 token（HttpOnly、SameSite=Strict Cookie），所有页面和 API
  都需要它；网关与私有导入服务之间使用另一个内部 token。
- 对外抓取逐跳校验 HTTPS 与公网 IP，并固定解析结果。
- 会话桥借来的 Cookie 只保存在进程内存中。

发现漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。

## 负责任地使用

Echo Reader 是个人阅读和笔记工具，如何使用由你自己负责。

- **只导入你有权访问和复制的内容。** 遵守著作权、各网站的服务条款以及 robots / 频率限制。
  为个人学习保存副本，不代表你有权再次发布。
- **不要用它绕过付费墙、DRM、访问控制或地区限制**，也不要用于批量爬取、镜像或再分发。
- **会话桥使用的是你自己的浏览器登录态。** 只授权你自己的网站和账号，保持扩展的授权域名单尽量小；
  用你的账号进行自动化访问可能违反网站条款，账号可能因此受限。
- **YouTube PO Token 生成（mint）。** YouTube 有时要求下载请求带上与真实浏览器会话一致的“来源证明”
  （PO）令牌和 visitor data，否则拒绝下载。给浏览器扩展授予 `youtube.com` 访问权限后，yt-dlp 可以经本地会话桥
  请求扩展：在**你自己的** Chrome 配置里打开一个后台 YouTube 标签页，读取页面上的 visitor data，并尽可能
  在页面内生成 PO 令牌。令牌和 visitor data 交给 yt-dlp 用于下载，只保存在导入服务的内存里，不写入磁盘；这个请求只接受 YouTube 视频地址或视频 ID。
  这样下载请求看起来就像来自你登录的浏览器，这可能与 YouTube 服务条款（除 YouTube 提供的下载方式外
  限制下载）相冲突。**是否允许这样使用（例如你自己上传的视频、知识共享许可视频或已获授权的内容），
  以及对账号造成的后果，完全由你自己负责。** 如果不想使用，不要给扩展授予 `youtube.com` 权限（或不安装扩展），
  Echo Reader 的其他功能不受影响。
- **转写、说话人标签、OCR 和大模型输出都是机器生成的，可能出错。** 说话人分离只给出匿名编号，
  不是声纹识别，不要用它识别具体的人。引用或依赖重要文字前请先人工核对。
- **云端功能会把数据发给第三方。** 在线识别把音频上传到你的 OSS 和 DashScope；云端 OCR 上传图片；
  大模型整理把转写文本发给你配置的服务商。不要对你无权分享的机密或个人数据启用这些功能。
- **资料库里的个人信息由你负责。** 导入的内容可能包含他人的个人信息，请按适用法律（例如《个人信息
  保护法》、GDPR）处理。
- 朗读音频使用 Kokoro 的合成音色，不要把它冒充成真人的声音。

## 开发与测试

```bash
CARGO_BUILD_JOBS=2 bash scripts/verify.sh       # pytest、cargo fmt/clippy/test、类型检查、oxlint、vitest、构建
bash scripts/verify.sh --e2e                    # 额外运行 Playwright
```

Windows 上使用 `scripts\verify.ps1`。贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，文档索引见
[docs/README.md](docs/README.md)。

## 项目状态

导入、阅读、检索和 Windows 打包的核心路径都有单元测试和集成测试。站点适配器依赖第三方页面结构，
网站改版后可能失效。当前版本 0.1.0，暂不承诺接口稳定。

## 许可证

Echo Reader 以 [MIT 许可证](LICENSE) 发布。

第三方组件保留各自的许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。需要特别注意：

- **eSpeak NG 和 `phonemizer-fork` 使用 GPL-3.0。** 只有可选的 `tts` extra 会引入它们（`kokoro-onnx`
  → 内置 eSpeak NG 的 `espeakng-loader`，以及 `phonemizer-fork`）。它们是**由用户自行安装的可选依赖**，
  本项目**不分发**它们：不在本仓库中，不在我们构建的 wheel / sdist 中，也不在 Windows 安装包、免安装
  程序束或任何其他 bundle / sidecar 中——Windows 构建会显式排除它们，一旦出现即构建失败。如果你再分发
  包含它们的环境，该分发须遵守 GPL-3.0；
- Windows 程序束附带未修改的 FFmpeg **LGPL-3.0** 构建（BtbN `lgpl-shared`，不含 libx264/libx265）。
  许可证原文和源码链接（`licenses/FFMPEG_SOURCE.txt`）都在程序束里，`tools\` 下的 FFmpeg DLL 可以换成
  你自己编译的版本；
- 模型权重（Whisper、Kokoro-82M、Fun-ASR-Nano、PP-OCR）在运行时下载，许可证由各自作者规定
  （Apache-2.0 / MIT）。
