# Echo Reader

**English** | [简体中文](README.zh-CN.md)

Echo Reader is a local-first reading app for web content. Paste links to articles, posts,
podcasts or videos; it saves them as plain files on your disk (Markdown article, images,
media, time-coded transcript) and gives you a fast reader with full-text search, a
synchronized transcript player, a Markdown editor and optional read-aloud and OCR.

Everything runs on your own machine. There is no account, no server and no telemetry.
Cloud services (online speech recognition, LLM clean-up, cloud OCR) are optional and off
until you configure them.

![Architecture](docs/assets/architecture.svg)

## Features

- **Batch import** – paste up to 50 URLs (or load a `.txt`/`.csv`); each URL becomes one stable
  library item. Jobs are persisted in SQLite, survive restarts, and can be retried, cancelled or
  re-indexed from the task page.
- **Article extraction** – generic pages via Trafilatura plus site adapters for WeChat
  articles, Xiaohongshu, Zhihu, Toutiao, Weibo and X/Twitter.
- **Video and audio** – downloads through yt-dlp (Bilibili, YouTube, Douyin and other
  yt-dlp-supported sites), extracts audio with FFmpeg and transcribes it.
- **Speech recognition**
  - local: OpenVINO Whisper-small INT8 with Silero VAD and anonymous speaker diarization
    (sherpa-onnx), or Fun-ASR-Nano via FunASR (CPU/CUDA/MPS/XPU);
  - online (optional): Alibaba Cloud DashScope ASR through your own OSS bucket.
  A quality gate (character density and timeline coverage) rejects transcripts that are
  essentially empty, such as music-only videos, instead of publishing them as successes.
- **Optional LLM clean-up** – turn a raw transcript into an article with DashScope, any
  OpenAI-compatible endpoint, or a local Ollama model.
- **Reader** – React UI with Markdown rendering, a CodeMirror editor, image upload, media
  player with transcript seek, and transcript editing.
- **Full-text search** – SQLite FTS5 index (CJK-aware) over titles, articles, transcripts and
  OCR text. The index is a cache derived from the library and can be rebuilt at any time.
- **Read-aloud (optional)** – offline Kokoro-82M text-to-speech (`tts` extra).
- **Image OCR (optional)** – local RapidOCR (`ocr` extra), or the PaddleOCR-VL cloud API when
  you explicitly allow uploads.
- **Browser Session Bridge (optional)** – a Chrome extension that lends the cookies of sites
  *you* authorize to the local importer for content that requires your login. Cookies stay in
  memory and are never written to the library.
- **Windows desktop build** – a per-user installer with a single-origin Rust gateway, a private
  Python import service, random per-launch session tokens and single-instance handling.

## How it works

![Import flow](docs/assets/import-flow.svg)

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

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Rust (stable, edition 2024) with `rustfmt` and `clippy`
- [Bun](https://bun.sh/) for the frontend, and Node.js ≥ 22 for Vitest
- FFmpeg on `PATH` for audio/video imports
- Optional: [mise](https://mise.jdx.dev) installs the pinned tool versions from `mise.toml`

Local Whisper ASR targets an Intel GPU through OpenVINO and does not silently fall back to the
CPU; Fun-ASR-Nano runs on CPU, CUDA, Apple MPS or Intel XPU.

## Quick start (Linux / macOS)

```bash
git clone <repository-url> echo-reader && cd echo-reader
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
`components/echo/frontend` (Vite on port 5173 proxies `/api` to the gateway).

## Quick start (Windows, source)

```powershell
mise install
.\scripts\setup.ps1
.\scripts\run-reader.ps1
```

`scripts\build-package.ps1 -Version <x.y.z>` builds the standalone bundle and, when Inno Setup is
available, the per-user installer. See [docs/installation.md](docs/installation.md) and
[docs/development.md](docs/development.md).

## Optional features

| Feature | Install | Notes |
|---|---|---|
| Local Whisper ASR | `uv sync --extra local-asr` | Model `OpenVINO/whisper-small-int8-ov` is downloaded at a pinned revision and verified on first use |
| Fun-ASR-Nano ASR | `uv sync --extra funasr` | Set `[local_asr].engine = "funasr_nano"`; pulls PyTorch. Remote model code stays disabled (`trust_remote_code = false`) |
| Read-aloud | `uv sync --extra tts` then `uv run python scripts/download_kokoro_models.py` | Downloads ~350 MB of Kokoro v1.0 files and checks pinned SHA-256 digests. The extra installs GPL-3.0 packages (eSpeak NG, phonemizer-fork) on your machine only; they are never shipped by this project – see [License](#license) |
| Local OCR | `uv sync --extra ocr` | RapidOCR with bundled PP-OCR ONNX models |
| Cloud OCR | `[ocr] engine = "paddleocr_api"`, `allow_cloud = true`, HTTPS `endpoint`, token in `secrets.toml` | **Uploads item images to the configured service** |
| Online ASR / LLM clean-up | copy `components/medialab/src/medialab/config/secrets.toml.example` to `config/secrets.toml` | Audio is uploaded to your OSS bucket and DashScope; clean-up can use an OpenAI-compatible endpoint or local Ollama instead |

When an optional dependency or model is missing, the API answers `503 feature_disabled` with the
reason and the UI hides the action; nothing else is affected.

## Configuration

- `config/secrets.toml` (git-ignored) – API keys and tokens. Never commit it.
- `components/medialab/src/medialab/config/default_config.toml` – documented defaults; override
  them in `config/config.toml`.
- Gateway environment: `LIBRARY_ROOT`, `IMPORT_API_URL`, `ECHO_BIND` (loopback only),
  `ECHO_WEB_ROOT`, `ECHO_SEARCH_INDEX`, `ECHO_PUBLIC_TOKEN`, `ECHO_INTERNAL_TOKEN`.
- Read-aloud models: `ECHO_TTS_MODELS_PATH` or `[tts] model_dir`.

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

## Development and tests

```bash
CARGO_BUILD_JOBS=2 bash scripts/verify.sh       # pytest, cargo fmt/clippy/test, typecheck, oxlint, vitest, build
bash scripts/verify.sh --e2e                    # additionally runs Playwright
```

On Windows use `scripts\verify.ps1`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and
[docs/README.md](docs/README.md) for the documentation map (most design documents are in Chinese).

## Project status

The core import, reader, search and Windows packaging paths are covered by unit and integration
tests. Site adapters depend on third-party page structures and can break when those sites change.
The project is versioned 0.1.0 and has no stability guarantees yet.

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
- the Windows bundle ships an FFmpeg **GPLv3** build, so redistributing the bundle carries GPL
  source-offer obligations;
- model weights (Whisper, Kokoro-82M, Fun-ASR-Nano, PP-OCR) are downloaded at runtime and are
  licensed by their authors (Apache-2.0 / MIT).
