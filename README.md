# Echo Reader

**English** | [简体中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB.svg)](pyproject.toml)
[![Rust 2024](https://img.shields.io/badge/rust-2024_edition-orange.svg)](components/echo/backend/Cargo.toml)
[![GitHub](https://img.shields.io/badge/GitHub-PakhomLeo%2Fecho--reader-181717.svg?logo=github)](https://github.com/PakhomLeo/echo-reader)

Echo Reader is a local-first reading app for web content. Paste links to articles, posts,
podcasts or videos; it saves them as plain files on your disk (Markdown article, images,
media, time-coded transcript) and gives you a fast reader with full-text search, a
synchronized transcript player, a Markdown editor and optional read-aloud and OCR.

Everything runs on your own machine. There is no account, no server and no telemetry.
Cloud services (online speech recognition, LLM clean-up, cloud OCR) are optional and off
until you configure them.

Repository: <https://github.com/PakhomLeo/echo-reader>. This project (package name `echo-reader`)
is not affiliated with other projects on GitHub or elsewhere that use the same or a similar name.

![Echo Reader showing a video item: the player is paused at 00:04 and the matching transcript line is highlighted](docs/assets/item-transcript.png)

*A video item in the reader (the interface is in Chinese): the player is paused at 00:04 and the
matching line of the speaker-labelled transcript is highlighted. The sample item is a generated test
pattern shipped in `components/echo/library.example`.*

## Table of contents

- [Highlights](#highlights)
- [Screenshots](#screenshots)
- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Quick start (Linux / macOS)](#quick-start-linux--macos)
- [Quick start (Windows, source)](#quick-start-windows-source)
- [Optional dependencies](#optional-dependencies)
- [Configuration](#configuration)
- [Security model](#security-model)
- [Responsible use](#responsible-use)
- [FAQ](#faq)
- [Development and tests](#development-and-tests)
- [Project status](#project-status)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## Highlights

- **One link, one item** – articles, images, video, audio and transcript of a URL end up in one
  folder with a `manifest.json`; the library folder is the single source of truth.
- **Local-first** – reading, search, local speech recognition, read-aloud and OCR work offline;
  every cloud feature is opt-in.
- **Synchronized transcript** – click a line to seek, follow the highlight while playing, edit
  the transcript in place.
- **Full-text search across everything** – titles, articles, transcripts and OCR text, with
  CJK-aware matching.
- **Your browser login, on your terms** – an optional Chrome extension lends cookies only for the
  domains you authorize, and only to the local importer.
- **Loopback only** – both servers refuse non-loopback addresses and check `Host`, `Origin` and
  `Sec-Fetch-Site`.

## Screenshots

![Echo Reader library view with four sample items and the empty reading pane](docs/assets/library.png)

The library view: a sidebar with type filters (all, articles, video, audio, mixed), the import
task list, full-text search and library switching; the item list in the middle; the reading pane on
the right. Both screenshots were taken from a local instance with the bundled sample items.

## Features

### Import

- Paste 1–50 URLs at once, or load a `.txt` / `.csv` file. Each URL becomes one stable library item.
- Batches and jobs are stored in SQLite (`imports.db`), survive restarts and run on three workers.
- Before a job starts, the import service checks that the required capability (for example an
  ASR backend) is available, so jobs fail early with a readable reason.
- The task page shows a timeline per job and lets you retry, cancel or re-index.
- Modes: `auto` (probe the page and fetch everything it links to, up to 50 media items),
  `article`, `download` and `transcribe`. Unambiguous video URLs (YouTube, Bilibili, Douyin,
  TikTok, Vimeo, Instagram reels, X posts) go straight to transcription in `auto` mode.

### Article extraction

- Pages are requested with a task-scoped [curl_cffi](https://github.com/lexiforest/curl_cffi)
  session that presents a Chrome 146 TLS and HTTP/2 profile; no hand-written User-Agent.
- Site adapters for WeChat Official Accounts, Xiaohongshu, Zhihu, Toutiao, Weibo and X/Twitter, with
  [Trafilatura](https://trafilatura.readthedocs.io/) as the generic fallback. Dead pages and login
  walls are rejected instead of being saved as articles.
- Optional JavaScript rendering with Playwright and an installed Microsoft Edge for pages that
  only load content in the browser (WeChat always; Zhihu and Toutiao only when the static page has
  no article).
- Images are downloaded by the same session, with limits on count (100), size (15 MiB) and time.

### Video and audio

- Downloads through [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Bilibili, Douyin, YouTube, X,
  TikTok, Vimeo, Twitch, Instagram, Facebook and a generic extractor for direct media links),
  using yt-dlp's curl_cffi handler with Chrome impersonation and Deno + `yt-dlp-ejs` for
  JavaScript challenges.
- Layered retries: yt-dlp retries, back-off on platform rate limiting (HTTP 412/503), and one
  attempt with a fallback format.
- FFmpeg keeps the original file and writes a browser-playable copy (H.264 + AAC, MP3 or M4A).
  H.264 uses libx264 when your FFmpeg has it, otherwise libopenh264, otherwise `h264_mf`.

### Speech recognition

- **Local, Whisper**: OpenVINO Whisper-small INT8 on an Intel GPU, with Silero VAD and anonymous
  speaker diarization (sherpa-onnx). The model is downloaded at a pinned revision.
- **Local, Fun-ASR-Nano**: FunASR on CPU, CUDA, Apple MPS or Intel XPU, with CAM++ speaker
  labels. Remote model code is never executed unless you set `trust_remote_code = true`.
- **Online (optional)**: audio is cut into 60-second slices with 1 second of overlap, uploaded to
  your own Alibaba Cloud OSS bucket and transcribed by DashScope
  (`qwen-audio-3.0-asr-flash-filetrans`); timestamps are mapped back to the original timeline.
- **Quality gate**: an empty transcript is always rejected; for audio longer than 10 seconds the
  transcript also needs at least 0.5 characters per second and 15 % timeline coverage. Local
  Whisper tries a second timestamp strategy before giving up. Rejected jobs publish nothing.

### LLM clean-up (optional)

- After online transcription, a chat model can turn the speaker-labelled transcript into a readable
  `article.md`: DashScope (default), any OpenAI-compatible endpoint or a local Ollama model.
- English content is written as a Chinese article (the original title is kept as subtitle);
  an existing `article.md` is never overwritten; a failed clean-up keeps the transcript.

### Reader

- React 19 UI with Markdown rendering, a CodeMirror 6 editor and image upload.
- Media player with transcript seek and highlight, transcript editing, and tabs for the article
  and the time-coded text.
- Library switching from the sidebar, with validation (no file-system roots, no overlap with
  state, cache, config, log or temp directories) and rollback on failure.

### Full-text search

- SQLite FTS5 index over titles, article blocks, transcript segments and OCR text.
- CJK text is also indexed as 1- to 3-character n-grams, so Chinese, Japanese and Korean words
  match without a dictionary. Queries are quoted to be injection-safe; question-style queries are reduced
  to their keywords.
- The index is a cache: it lives in your user cache directory (never inside the library), is
  synchronized on start, after every rescan and before each search, and can be rebuilt at any time.

### Read-aloud (optional)

- Offline [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) text-to-speech through
  `kokoro-onnx` (`tts` extra). The model files are downloaded separately and verified by SHA-256.
- The article is stripped to plain text (images and code blocks are skipped), limited to 20,000
  characters and read with a Chinese voice when CJK characters dominate, otherwise an English voice.
- The result is saved as `media/tts-article.wav` and appears as an audio attachment of the item.

### Image OCR (optional)

- Local [RapidOCR](https://github.com/RapidAI/RapidOCR) with the PP-OCR ONNX models from the wheel
  (`ocr` extra), or the PaddleOCR-VL job API (model `PaddleOCR-VL-1.5`) when you explicitly set
  `allow_cloud = true`.
- Up to 200 images of an item are recognized; the text goes into `ocr.json` and into search.

### Browser Session Bridge (optional)

- A Chrome MV3 extension in `components/medialab/browser-extension` connects to
  `ws://127.0.0.1:19876` and returns the cookies of domains *you* authorize in its popup.
- Cookies are kept in the import service's memory and handed to curl_cffi or yt-dlp for one task;
  temporary `cookies.txt` files are deleted after use. Nothing is written to the library.
- For YouTube it can also mint visitor data and a PO token – see [Responsible use](#responsible-use).

### Windows desktop build

- Per-user Inno Setup installer and standalone bundle (`scripts\build-package.ps1`).
- A host process starts the Rust gateway and a private Python import service on dynamic loopback
  ports, with a random per-launch session token, a separate internal token and single-instance
  handling. User data stays under `%LOCALAPPDATA%\EchoReader` and `Documents\Echo Reader Library`.

## Architecture

![Architecture](docs/assets/architecture.svg)

The repository is a monorepo with two independent components and an integration layer:

| Path | Role | Stack |
|---|---|---|
| `components/medialab` | Producer: probes, downloads, extracts, transcribes and publishes items | Python 3.12, yt-dlp, curl_cffi, Trafilatura, OpenVINO / FunASR |
| `components/echo/backend` | Gateway and reader API: scans the library, serves media, search index, proxies the import API | Rust, Axum, rusqlite (FTS5) |
| `components/echo/frontend` | Reader UI | React 19, Vite, CodeMirror 6 |
| `src/echo_reader` | Import service (batches, jobs, enrichment), desktop host, migration | FastAPI, SQLite |
| `components/medialab/browser-extension` | Optional Session Bridge (Chrome MV3) | JavaScript |

The **library folder is the single source of truth**: each item is a directory with
`manifest.json`, optional `article.md`, `transcript.json`, `images/`, `media/` and `ocr.json`.
Echo never imports MediaLab code and vice versa; the file format
([`components/echo/docs/file-format.md`](components/echo/docs/file-format.md)) and the HTTP API
([`components/echo/docs/api.md`](components/echo/docs/api.md)) are the contracts.

### Import flow

![Import flow](docs/assets/import-flow.svg)

### Acquisition chain

![Acquisition chain: curl_cffi page requests, policy-based JS rendering, yt-dlp downloads and optional browser credentials](docs/assets/acquisition-chain.svg)

Where each network technique comes in:

| Step | Technique | When it is used |
|---|---|---|
| Page request | curl_cffi session with a Chrome 146 profile | Every non-media URL; one session per task, also used for images |
| Cookies for pages | Session Bridge → credential broker | Known platforms (not generic sites) while the extension is connected; Xiaohongshu always needs them |
| JS rendering | Playwright + installed Microsoft Edge, fresh context | `[js_render].platform_modes`: WeChat `always`, Zhihu/Toutiao `on_miss` |
| Media download | yt-dlp with curl_cffi impersonation (`chrome-146`) | Clear video URLs, direct media links and media found on a page |
| Cookies for media | Netscape `cookies.txt` passed to yt-dlp | Platforms in `session_first_platforms` from the start; others only after an auth-type error |
| YouTube visitor data / PO token | Extension mint → `youtube:visitor_data=…;po_token=…` extractor arguments; PO-token provider plugin for extra player/GVS tokens | YouTube, only when the extension has `youtube.com` access |

All outbound requests validate HTTPS and public IP addresses on every hop and pin the resolved
address. When the extension is missing or refuses, public content still works and login-only
content fails with `auth_required`.

### Media processing

![Media processing: transcription, full-text search, read-aloud and OCR](docs/assets/media-pipeline.svg)

Transcription runs during import; read-aloud and OCR run on demand from the reader (“生成朗读音频”
and “识别图片文字” on an item). Their results are written into the item folder and the gateway is
asked to rescan, which also refreshes the search index.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Rust (stable, edition 2024) with `rustfmt` and `clippy`
- [Bun](https://bun.sh/) for the frontend, and Node.js ≥ 22 for Vitest
- FFmpeg on `PATH` for audio/video imports
- [Deno](https://deno.com/) for yt-dlp's JavaScript challenges (YouTube and others)
- Optional: [mise](https://mise.jdx.dev) installs the pinned tool versions from `mise.toml`,
  including an LGPL FFmpeg build
- Optional: Microsoft Edge for JavaScript rendering; Chrome for the Session Bridge extension

Local Whisper ASR targets an Intel GPU through OpenVINO and does not silently fall back to the
CPU; Fun-ASR-Nano runs on CPU, CUDA, Apple MPS or Intel XPU.

## Quick start (Linux / macOS)

```bash
git clone https://github.com/PakhomLeo/echo-reader.git echo-reader && cd echo-reader
uv sync                                   # Python workspace (add --extra local-asr etc. as needed)
(cd components/echo/frontend && bun install --frozen-lockfile && bun run build)
mkdir -p content .runtime

# terminal 1 – import service (loopback only)
uv run echo-reader-service --host 127.0.0.1 --port 3100 \
  --library-root content --runtime-root .runtime --echo-url http://127.0.0.1:3000

# terminal 2 – Rust gateway + reader UI on http://127.0.0.1:3000
cd components/echo/backend
LIBRARY_ROOT=../../../content IMPORT_API_URL=http://127.0.0.1:3100 cargo run --release
```

Open <http://127.0.0.1:3000>. For frontend development run `bun run dev` in
`components/echo/frontend` (Vite on port 5173 proxies `/api` to the gateway). To try the reader
without importing anything, point `LIBRARY_ROOT` at a copy of `components/echo/library.example`.

## Quick start (Windows, source)

```powershell
mise install
.\scripts\setup.ps1
.\scripts\run-reader.ps1
```

`scripts\build-package.ps1 -Version <x.y.z>` builds the standalone bundle and, when Inno Setup is
available, the per-user installer. See [docs/installation.md](docs/installation.md) and
[docs/development.md](docs/development.md).

## Optional dependencies

| Feature | Install | Notes |
|---|---|---|
| Local Whisper ASR | `uv sync --extra local-asr` | Model `OpenVINO/whisper-small-int8-ov` is downloaded at a pinned revision and verified on first use |
| Fun-ASR-Nano ASR | `uv sync --extra funasr` | Set `[local_asr].engine = "funasr_nano"`; pulls PyTorch. Remote model code stays disabled (`trust_remote_code = false`) |
| Read-aloud | `uv sync --extra tts` then `uv run python scripts/download_kokoro_models.py` | Downloads ~350 MB of Kokoro v1.0 files and checks pinned SHA-256 digests. The extra installs GPL-3.0 packages (eSpeak NG, phonemizer-fork) on your machine only; they are never shipped by this project – see [License](#license) |
| Local OCR | `uv sync --extra ocr` | RapidOCR with bundled PP-OCR ONNX models |
| Cloud OCR | `[ocr] engine = "paddleocr_api"`, `allow_cloud = true`, HTTPS `endpoint`, token in `secrets.toml` | **Uploads item images to the configured service** |
| Online ASR / LLM clean-up | copy `components/medialab/src/medialab/config/secrets.toml.example` to `config/secrets.toml` | Audio is uploaded to your OSS bucket and DashScope; clean-up can use an OpenAI-compatible endpoint or local Ollama instead |
| JS rendering | Playwright is installed by default; install Microsoft Edge yourself | Playwright never downloads a browser here. Without Edge, WeChat imports fail (rendering is required there); Zhihu and Toutiao fall back to the static page with a `js_render_failed` warning |
| Session Bridge | load `components/medialab/browser-extension` as an unpacked extension in Chrome | Open the popup, check “connected”, then authorize the sites you need |

When an optional dependency or model is missing, the API answers `503 feature_disabled` with the
reason and the UI hides the action; nothing else is affected.

### eSpeak NG (read-aloud only)

eSpeak NG is an **optional dependency that you install yourself**. It is pulled in only by
`uv sync --extra tts` (through `kokoro-onnx` → `espeakng-loader`, which contains eSpeak NG, plus
`phonemizer-fork`), both GPL-3.0. Echo Reader does **not** distribute it: it is not in this
repository, our wheels or sdists, or the Windows installer and bundle (the Windows build fails if it
finds them). If you do not install the `tts` extra, nothing GPL-licensed is on your machine because
of Echo Reader, and read-aloud is simply unavailable.

### FFmpeg (LGPL build)

Echo Reader runs `ffmpeg` and `ffprobe` as separate programs and works with any recent FFmpeg on
your `PATH` (or `[audio].ffmpeg_path`). `mise install` pins an LGPL build from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) (`autobuild-2026-07-31-14-10`,
FFmpeg `n8.1.2-34-g9b6c8969e0` from the `release/8.1` branch) with fixed asset names and SHA-256
checksums; it has no libx264/libx265 and encodes H.264 with OpenH264. The Windows bundle ships the
`lgpl-shared` variant as replaceable DLLs together with `licenses/FFMPEG_SOURCE.txt`; the matching
FFmpeg source archive is attached to each GitHub release. Details are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Configuration

### Files and precedence

| File | Purpose |
|---|---|
| `components/medialab/src/medialab/config/default_config.toml` | Documented defaults (do not edit) |
| `config/config.toml` | Your overrides, same sections and keys as the defaults (not git-ignored: keep secrets out of it) |
| `config/secrets.toml` | API keys and tokens, from `secrets.toml.example` (git-ignored, never commit it) |
| `config/app.json` | Library folder chosen in the UI (written by the app, git-ignored) |

Values are merged as defaults < `LIBRARY_ROOT` < `config.toml` < `secrets.toml`. In source mode the
config folder is `<repo>/config`; the installed app uses `%LOCALAPPDATA%\EchoReader\config`.

### Settings reference

Defaults below are taken from `default_config.toml`.

| Key | Default | Meaning |
|---|---|---|
| `[concurrency] max_concurrent` | `3` | Concurrency limit for each pipeline stage (download, scrape, ASR, clean-up) |
| `[platforms] enabled` | Bilibili, Douyin, YouTube, Twitter, TikTok, Vimeo, Twitch, Instagram, Facebook, `generic` | Regexes of allowed yt-dlp extractors |
| `[paths] delivery` / `temp` / `db` | `""` / `"tmp"` / `"medialab.db"` | Library folder (empty = `content/` or `LIBRARY_ROOT`), temp folder, MediaLab task DB |
| `[download] format` / `merge_output_format` | `"bv*+ba/b"` / `"mp4"` | yt-dlp format selection and container |
| `[download] retries` / `fragment_retries` / `concurrent_fragment_downloads` | `10` / `10` / `4` | yt-dlp built-in retries and fragment parallelism |
| `[download] risk_retries` / `risk_backoff_base` | `3` / `30` | Extra attempts and base back-off (seconds) on HTTP 412/503 |
| `[download] fallback_format` | `"b/best"` | Format for the one fallback attempt |
| `[download] proxy` | `""` | Proxy for yt-dlp and online ASR |
| `[download] js_runtimes` / `remote_components` | `["deno"]` / `[]` | JavaScript runtime for yt-dlp challenges; remote components stay off |
| `[download] session_first_platforms` | Bilibili, Douyin, YouTube, X, TikTok, Vimeo, Twitch, Instagram, Facebook | Platforms that use browser cookies from the first attempt |
| `[article] direct_timeout` / `proxy` | `15` / `""` | Page timeout (seconds) and proxy for page requests |
| `[article] min_text_chars` | `200` | Shorter extracted text without images counts as “no article” |
| `[article] max_images` / `image_timeout` / `max_image_bytes` | `100` / `30` / `15728640` | Image download limits |
| `[js_render] enabled` / `platform_modes` | `true` / `{wechat="always", zhihu="on_miss", toutiao="on_miss"}` | JS rendering policy per platform; unlisted platforms are never rendered |
| `[js_render] resource_domains` | WeChat, QQ, Zhihu, Toutiao CDNs | Domains the rendered page may load resources from |
| `[js_render] timeout_seconds` / `max_requests` / `max_bytes` | `20` / `80` / `10485760` | Render limits |
| `[js_render] scroll_steps` / `scroll_delay_ms` | `6` / `300` | Scrolling to trigger lazy loading |
| `[content_capture] max_media_items` | `50` | Maximum media files per URL in `auto` mode; more fails explicitly |
| `[audio] ffmpeg_path` | `""` | FFmpeg path; empty = configured path → `PATH` → mise |
| `[progress] throttle` | `1.0` | Seconds between progress events |
| `[oss] presign_expires` / `max_batches` / `ttl_seconds` | `86400` / `10` / `10800` | Signed URL lifetime (s); uploaded slices are cleaned up keeping the latest 10 task batches and nothing older than 3 hours |
| `[oss] slice_seconds` / `overlap_seconds` | `60` / `1` | Online ASR slice length and overlap |
| `[asr] model` / `language_hints` | `"qwen-audio-3.0-asr-flash-filetrans"` / `["zh", "en"]` | Online ASR model and language hints |
| `[asr] diarization_enabled` / `speaker_count` | `true` / `0` | Online speaker labels; 0 = detect the number of speakers |
| `[asr] poll_interval` / `poll_max_interval` / `max_wait_seconds` / `slice_concurrency` | `2.0` / `5.0` / `1800` / `3` | Polling and parallelism of online jobs |
| `[local_asr] engine` | `"openvino_whisper"` | `openvino_whisper` or `funasr_nano` |
| `[local_asr] model_id` / `revision` / `model_dir` / `cache_dir` | `OpenVINO/whisper-small-int8-ov` / pinned commit / `.runtime/models/local-asr` / `.runtime/cache/openvino/local-asr` | Whisper model, pinned revision and storage |
| `[local_asr] device` / `auto_download` | `"GPU"` / `true` | OpenVINO device; download the model on first use |
| `[local_asr] vad_*`, `cluster_threshold`, `diarization_*` | see file | Silero VAD and sherpa-onnx diarization tuning |
| `[local_asr.funasr] model` / `model_revision` | `FunAudioLLM/Fun-ASR-Nano-2512` / `""` | Fun-ASR-Nano model and optional pinned commit |
| `[local_asr.funasr] trust_remote_code` | `false` | `true` lets FunASR install and import code from the model repository – only after reviewing it |
| `[local_asr.funasr] hub` / `auxiliary_hub` | `"hf"` / `"ms"` | Hugging Face for the model, ModelScope for VAD/speaker models |
| `[local_asr.funasr] vad_model` / `punc_model` / `spk_model` / `spk_mode` | `fsmn-vad` / `""` / `cam++` / `vad_segment` | Auxiliary models |
| `[local_asr.funasr] device` / `language` / `hotwords` | `"auto"` / `"auto"` / `[]` | Device, language and hot words |
| `[refine] enabled` / `provider` | `true` / `"dashscope"` | LLM clean-up after online ASR: `dashscope`, `openai_compatible` or `ollama` |
| `[refine] model` / `base_url` | `""` / `""` | DashScope: empty = `qwen3.7-flash` on the DashScope compatible endpoint; Ollama: `model` required, `base_url` defaults to `http://127.0.0.1:11434/v1`; OpenAI-compatible: both required |
| `[refine] temperature` / `max_input_chars` / `max_tokens` / `shrink_threshold` | `0.3` / `24000` / `12000` / `2` | Generation and chunking limits |
| `[refine] timeout_seconds` / `max_retries` | `300` / `2` | Request timeout and retries |
| `[refine] speaker_label` / `zh_threshold` | `"说话人"` / `0.2` | Speaker prefix; below this share of CJK characters the content is treated as English |
| `[tts] enabled` / `model_dir` | `true` / `""` | Read-aloud switch; empty dir = `<cache>/models/kokoro` (`ECHO_TTS_MODELS_PATH` wins) |
| `[tts] voice` / `voice_zh` / `speed` | `"af_heart"` / `"zf_xiaoxiao"` / `1.0` | English and Chinese voices, speed |
| `[ocr] enabled` / `engine` | `true` / `"auto"` | `auto` (RapidOCR if installed, else the cloud API if allowed), `rapidocr` or `paddleocr_api` |
| `[ocr] allow_cloud` / `endpoint` / `model` / `max_poll_attempts` | `false` / PaddleOCR job API / `"PaddleOCR-VL-1.5"` / `60` | Cloud OCR is used only when `allow_cloud = true` and the endpoint is HTTPS |

### Secrets (`config/secrets.toml`)

| Key | Used for |
|---|---|
| `[oss] access_key_id`, `access_key_secret`, `region`, `endpoint`, `bucket`, `prefix` | Online ASR uploads; OSS is enabled only when all keys are set |
| `[asr] api_key`, `base_url` | DashScope ASR (and DashScope clean-up when `[refine] api_key` is empty) |
| `[refine] api_key` | OpenAI-compatible endpoint that is not on localhost; not needed for Ollama |
| `[ocr] token` | PaddleOCR API |

### Environment variables

| Variable | Component | Meaning |
|---|---|---|
| `LIBRARY_ROOT` | gateway, import service | Library folder |
| `IMPORT_API_URL` | gateway | Import service URL (default `http://127.0.0.1:3100`, loopback only) |
| `ECHO_BIND` | gateway | Listen address (default `127.0.0.1:3000`, loopback only) |
| `ECHO_WEB_ROOT` | gateway | Built frontend (default `components/echo/frontend/dist`) |
| `ECHO_SEARCH_INDEX` / `ECHO_CACHE_DIR` | gateway | Search index file / cache directory for the default index path |
| `ECHO_PUBLIC_TOKEN` | gateway | Browser session token (set by the desktop host) |
| `ECHO_INTERNAL_TOKEN` | gateway, import service | Token between the gateway and the private import service (set by the desktop host) |
| `ECHO_BOOT_ID`, `ECHO_HOST_PID`, `ECHO_READY_FILE` | gateway | Desktop host integration |
| `ECHO_READER_LIBRARY_ROOT`, `ECHO_READER_CONFIG_ROOT`, `ECHO_READER_STATE_ROOT`, `ECHO_READER_CACHE_ROOT`, `ECHO_READER_LOGS_ROOT`, `ECHO_READER_TMP_ROOT` | import service | Override individual data folders |
| `ECHO_READER_INSTALL_MODE` | import service | `1` = use the installed-app folder layout |
| `ECHO_TTS_MODELS_PATH` | import service | Kokoro model folder |

`echo-reader-service` accepts `--host`, `--port`, `--library-root`, `--runtime-root`,
`--state-root`, `--cache-root`, `--logs-root`, `--tmp-root`, `--config-root`, `--install-mode`,
`--echo-url` and `--internal-token`. `echo-reader-import <url>` imports one URL from the command line.

## Security model

Echo Reader is a single-user local application.

- Both the gateway and the import service refuse to listen on non-loopback addresses, reject
  requests whose `Host` is not loopback (DNS-rebinding defence), and reject state-changing
  requests with a cross-site `Sec-Fetch-Site` or a foreign `Origin`.
- The desktop build adds a random per-launch session token (HttpOnly, SameSite=Strict cookie)
  for every page and API call, and a separate internal token between the gateway and the
  private import service.
- Outbound fetches validate HTTPS and public IP addresses per hop and pin the resolved address.
- Cookies lent by the Session Bridge stay in process memory.

Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Responsible use

Echo Reader is a personal reading and note-taking tool. You are responsible for how you use it.

- **Only import content you are allowed to access and copy.** Respect copyright, the terms of
  service of each website, and robots/rate-limit rules. Saving a copy for personal study does not
  give you the right to republish it.
- **Do not use it to bypass paywalls, DRM, access controls or geo-restrictions**, and do not use it
  for bulk scraping, mirroring or redistribution.
- **The Session Bridge uses your own browser login.** Authorize only sites and accounts you own,
  keep the extension's allow-list small, and remember that automated access with your account may
  violate a site's terms and could get the account restricted.
- **YouTube PO-token minting.** YouTube sometimes refuses downloads unless the request carries a
  "proof of origin" (PO) token and visitor data that match a real browser session. When you grant the
  browser extension access to `youtube.com`, yt-dlp can ask the extension (through the local bridge)
  to open a background YouTube tab in *your* Chrome profile, read the visitor data from the page
  and, where possible, generate a PO token there. The token and visitor data are passed to
  yt-dlp for the download and kept only in the import service's memory (never written to disk),
  and the extension only accepts YouTube video URLs or IDs for this request. This
  makes downloads look like they come from your own signed-in browser. Doing so may conflict with
  YouTube's Terms of Service, which restrict downloading except where YouTube offers it; **you
  alone are responsible** for deciding whether your use is permitted (for example your own
  uploads, Creative Commons videos or content you have permission for) and for any consequences
  for your account. If you do not want this, do not grant the extension access to
  `youtube.com` (or do not install the extension); the rest of Echo Reader works without it.
- **Transcripts, speaker labels, OCR and LLM output are machine-generated and can be wrong.**
  Speaker diarization only assigns anonymous numbered labels and is not voice identification;
  do not use it to identify people. Review important text before you rely on or quote it.
- **Cloud features send data to third parties.** Online ASR uploads audio to your OSS bucket and
  DashScope; cloud OCR uploads images; LLM clean-up sends transcript text to the provider you
  configure. Do not enable them for confidential or personal data you are not allowed to share.
- **Personal data in your library stays your responsibility.** Content you import may contain
  other people's personal information; handle it according to the laws that apply to you
  (for example GDPR or PIPL).
- Generated read-aloud audio uses the Kokoro voices; do not present it as a real person's voice.

## FAQ

**An import fails with `auth_required`. What do I do?**
The page or video needs your login. Load the Session Bridge extension in Chrome, make sure the popup
says it is connected, authorize the site and retry the job from the task page. Xiaohongshu always
needs this.

**YouTube says “Sign in to confirm you're not a bot”.**
Grant the extension access to `youtube.com` so the import can use your session, visitor data and a
PO token. Read [Responsible use](#responsible-use) first; if you prefer not to, YouTube imports may
fail.

**The import dialog says local and online recognition are “unavailable”.**
Neither ASR backend is set up. Install `--extra local-asr` (Intel GPU) or `--extra funasr`
(CPU/GPU), or fill in the OSS and DashScope keys in `config/secrets.toml`.

**A music video was rejected by the quality gate.**
That is intended: a transcript with almost no text is not published as a success. Use `download`
mode if you only want the media.

**WeChat imports fail with a JS rendering error.**
WeChat pages are always rendered so that lazy-loaded images and media are not missed. Install
Microsoft Edge (Playwright uses the installed browser and never downloads one) and retry.

**Where are my files, and can I move them?**
Each item is a normal folder in the library (`content/` in source mode, `Documents\Echo Reader
Library` in the installed app). Use “切换资料库” (switch library) in the sidebar or `LIBRARY_ROOT`
to point elsewhere. The search index is only a cache and is rebuilt automatically.

**The read-aloud or OCR button is missing.**
The feature is not available. `GET /api/v1/features` shows the reason; install the `tts` or `ocr`
extra and, for read-aloud, download the Kokoro model files.

**Does OCR upload my images?**
Not by default. Images are only sent to the PaddleOCR API when `[ocr] allow_cloud = true` and an
HTTPS endpoint and token are set; otherwise RapidOCR runs locally.

**Can I open the reader from another device on my network?**
No. Both servers only listen on loopback and reject foreign `Host` headers. Echo Reader is a
single-user local app.

**Do I need FFmpeg with libx264?**
No. Any recent FFmpeg works; playback copies use libx264 if present, otherwise libopenh264 or
`h264_mf`. The pinned LGPL build from `mise.toml` is enough.

**Is this related to other projects named Echo or Echo Reader?**
No. This project is not affiliated with other projects that use the same or a similar name.

## Development and tests

```bash
CARGO_BUILD_JOBS=2 bash scripts/verify.sh       # pytest, cargo fmt/clippy/test, typecheck, oxlint, vitest, build
bash scripts/verify.sh --e2e                    # additionally runs Playwright
```

On Windows use `scripts\verify.ps1`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

## Project status

The core import, reader, search and Windows packaging paths are covered by unit and integration
tests. Site adapters depend on third-party page structures and can break when those sites change.
The project is versioned 0.1.0 and has no stability guarantees yet.

Known limitations:

- Local Whisper needs an Intel GPU; there is no CPU fallback for that engine.
- The Windows installer has not yet been built and tested on a clean machine for this release.
- The read-aloud chain depends on GPL-3.0 packages; replacing eSpeak with a G2P without
  that dependency is planned.

## Documentation

| Document | Content |
|---|---|
| [docs/README.md](docs/README.md) | Documentation map (most design documents are in Chinese) |
| [docs/installation.md](docs/installation.md) | Windows installation, data folders, migration |
| [docs/development.md](docs/development.md) | Development setup and packaging |
| [docs/architecture.md](docs/architecture.md) | Architecture notes |
| [components/echo/docs/file-format.md](components/echo/docs/file-format.md) | Library item format |
| [components/echo/docs/api.md](components/echo/docs/api.md) | Reader HTTP API |
| [components/medialab/browser-extension/README.md](components/medialab/browser-extension/README.md) | Session Bridge protocol and security boundary |

## Contributing

Issues and pull requests are welcome at <https://github.com/PakhomLeo/echo-reader/issues>. Please read
[CONTRIBUTING.md](CONTRIBUTING.md) first and report security problems privately as described in
[SECURITY.md](SECURITY.md).

## License

Echo Reader is released under the [MIT License](LICENSE).

Third-party components keep their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Notably:

- **eSpeak NG and `phonemizer-fork` are GPL-3.0.** They are pulled in only by the optional
  `tts` extra (`kokoro-onnx` → `espeakng-loader`, which bundles eSpeak NG, and `phonemizer-fork`).
  They are **optional dependencies that you install yourself**; this project does **not**
  distribute them. They are not in this repository, not in our wheels or sdists, and not in the
  Windows installer, standalone bundle or any other bundle or sidecar — the Windows build
  excludes them explicitly and fails if they appear. If you redistribute an environment that
  includes them, the GPL-3.0 applies to that distribution;
- the Windows bundle ships an unmodified **LGPL-3.0** FFmpeg build (BtbN `lgpl-shared`, no
  libx264/libx265). Its license text and source links (`licenses/FFMPEG_SOURCE.txt`) are in the
  bundle, and the FFmpeg DLLs in `tools\` can be replaced with your own build;
- model weights (Whisper, Kokoro-82M, Fun-ASR-Nano, PP-OCR) are downloaded at runtime and are
  licensed by their authors (Apache-2.0 / MIT).
