# Third-party notices

echo-reader (https://github.com/PakhomLeo/echo-reader) is Copyright (c) 2026 PakhomLeo and contributors and is licensed under the
[MIT License](LICENSE). It depends on third-party
software and models that keep their own licenses. This file lists the notable ones; the
lockfiles (`uv.lock`, `components/echo/backend/Cargo.lock`, `components/echo/frontend/bun.lock`)
are the authoritative dependency lists, and the full license review with primary-source links
is kept with the release notes.

## GPL-3.0 optional dependencies – NOT distributed

The optional read-aloud feature (`tts` extra) uses `kokoro-onnx` (MIT), which depends on:

| Package | License | What it contains |
|---|---|---|
| [`phonemizer-fork`](https://pypi.org/project/phonemizer-fork/) | GPL-3.0-or-later | Python phonemizer |
| [`espeakng-loader`](https://github.com/thewh1teagle/espeakng-loader) | loader code MIT; the wheel bundles the **eSpeak NG** shared library and data, which are [GPL-3.0](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING) | `libespeak-ng` + `espeak-ng-data` |

- These packages are **optional dependencies that each user installs on their own**
  (`uv sync --extra tts`). They are **not part of this repository and are not redistributed by
  this project**.
- They are **never included in any package we build**: not in the Python wheels/sdists (which
  only declare `kokoro-onnx` as an optional `tts` requirement), not in the Windows standalone
  bundle or installer, and not in any other bundle or sidecar. The Windows build installs only the
  `local-asr` extra, passes `--exclude-module` for `kokoro_onnx`, `phonemizer` and
  `espeakng_loader` to PyInstaller, and fails if the assembled bundle contains any of them.
- If **you** redistribute an environment, image or bundle that includes them, that distribution
  must comply with the GPL-3.0.

## Python

| Component | License | Notes |
|---|---|---|
| FastAPI, Uvicorn, httpx | MIT / BSD-3-Clause | Import service |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp), yt-dlp-ejs | Unlicense (+ MIT/ISC) | Media download |
| [curl_cffi](https://github.com/lexiforest/curl_cffi) | MIT | Chrome-compatible transport; its native libraries keep their notices |
| Trafilatura, cssselect | Apache-2.0 / BSD-3-Clause | Article extraction |
| Playwright | Apache-2.0 | Optional JS rendering |
| websocket-server, websocket-client | MIT / Apache-2.0 | Session Bridge |
| oss2 | MIT | Optional online ASR upload |
| [OpenVINO](https://github.com/openvinotoolkit/openvino), [OpenVINO GenAI](https://github.com/openvinotoolkit/openvino.genai) | Apache-2.0 | `local-asr` extra |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Apache-2.0 | VAD and diarization runtime |
| [FunASR](https://github.com/modelscope/FunASR), ModelScope, PyTorch | MIT / Apache-2.0 / BSD-style | `funasr` extra |
| kokoro-onnx, onnxruntime, numpy | MIT / MIT / BSD-3-Clause | `tts` extra (see GPL section above) |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) (`rapidocr-onnxruntime`), opencv-python, shapely, pyclipper | Apache-2.0 / Apache-2.0 / BSD-3-Clause / MIT | `ocr` extra |
| PyInstaller | GPL-2.0-or-later with the bootloader exception | Windows build tool; only the bootloader is shipped, under the exception |

## Rust and frontend

All Rust crates in `Cargo.lock` are under permissive licenses (mostly `MIT OR Apache-2.0`; also
ISC, BSD-2/3-Clause, Zlib, Unicode-3.0, CC0-1.0, BSL-1.0 and CDLA-Permissive-2.0 for the
`webpki-root-certs` data). `r-efi` is offered under `MIT OR Apache-2.0 OR LGPL-2.1-or-later`; we
use it under MIT. SQLite (bundled by `rusqlite`) is in the public domain.

Frontend runtime packages (React, React Router, react-markdown, remark, CodeMirror 6,
lucide-react) are MIT or ISC. Build tooling additionally uses `lightningcss` (MPL-2.0, through
Vite) and a few MIT-0 / BlueOak-1.0.0 / CC0-1.0 packages; none of them is copied into the built
`dist/`. See [`components/echo/THIRD_PARTY_LICENSES.md`](components/echo/THIRD_PARTY_LICENSES.md).

Python transitive packages with weak-copyleft options: `certifi` (MPL-2.0, unmodified CA bundle)
and `tld` (`MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later`, used under MPL-1.1).

## Models (downloaded at runtime, never committed)

| Model | License | Source |
|---|---|---|
| OpenVINO/whisper-small-int8-ov (converted from OpenAI Whisper small) | Apache-2.0 (model card); original Whisper code and weights MIT | Hugging Face, pinned revision, SHA-256 verified |
| Silero VAD, pyannote segmentation 3.0, 3D-Speaker ERes2Net | MIT / MIT / Apache-2.0 | sherpa-onnx release assets, pinned URLs, SHA-256 verified |
| Kokoro-82M v1.0 (ONNX export by kokoro-onnx) | Apache-2.0 | kokoro-onnx GitHub release, pinned SHA-256 |
| Fun-ASR-Nano-2512, FSMN-VAD, CAM++ | Apache-2.0 (model card) | Downloaded by FunASR on first use |
| PP-OCR (bundled in the RapidOCR wheel) | Apache-2.0 | `rapidocr-onnxruntime` wheel |

PaddleOCR-VL is only used through a cloud API that you configure; using it is subject to that
service's terms.

## Windows bundle

The Windows bundle additionally ships:

- **FFmpeg / FFprobe** – an unmodified **LGPL-3.0-or-later** build from
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), pinned in `mise.toml` by release
  tag, asset name and SHA-256:
  `ffmpeg-n8.1.2-34-g9b6c8969e0-win64-lgpl-shared-8.1.zip`
  (`sha256:c222a490dde4e7059f45495deef6bfb98dbcacc2b43df5b607546252037aa95c`, release
  [`autobuild-2026-07-31-14-10`](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-07-31-14-10)).
  It is configured with `--enable-version3` and without `--enable-gpl` / `--enable-nonfree`, so it
  contains no GPL-only components such as libx264 or libx265; H.264 is encoded with OpenH264
  (BSD-2-Clause). The build script refuses any FFmpeg that reports `--enable-gpl`, `--enable-nonfree`
  or a libx264/libx265 encoder.
  - *License text*: FFmpeg's `LICENSE.txt` is copied to `licenses/FFMPEG_LICENSE.txt`.
  - *Corresponding source*: FFmpeg commit
    [`9b6c8969e0`](https://github.com/FFmpeg/FFmpeg/tree/9b6c8969e0) and the build scripts with every
    pinned library version at
    [BtbN/FFmpeg-Builds@autobuild-2026-07-31-14-10](https://github.com/BtbN/FFmpeg-Builds/tree/autobuild-2026-07-31-14-10);
    releases that ship the bundle also attach the source archive. See
    `licenses/FFMPEG_SOURCE.txt` in the bundle (`installer/licenses/FFMPEG_SOURCE.txt` here).
  - *Replacing the libraries*: echo-reader does not link FFmpeg; it runs `ffmpeg.exe` / `ffprobe.exe`
    as separate processes. The shared build ships the LGPL libraries as separate DLLs
    (`avcodec-62.dll`, `avformat-62.dll`, `avutil-60.dll`, …) in `tools\`; you may replace them, or
    the executables, with your own build of the same major version.
  - When you run from source, FFmpeg comes from your own installation (or `mise install`); any
    build works, and the code uses libx264 if your FFmpeg has it, otherwise OpenH264 or Media
    Foundation.
- **Deno** – MIT, copied to `licenses/DENO_LICENSE.txt`.
- **PyInstaller bootloader** – GPL-2.0-or-later with the bootloader exception; license texts are
  copied to `licenses/`.
