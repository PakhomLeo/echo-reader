# Third-party notices

Echo Reader preserves the notices shipped with its components:

- Echo dependency notices: [`components/echo/THIRD_PARTY_LICENSES.md`](components/echo/THIRD_PARTY_LICENSES.md).
- MediaLab uses [`curl_cffi`](https://github.com/lexiforest/curl_cffi) under the MIT license for its maintained Chrome-compatible curl transport. Its packaged native dependencies retain their upstream notices.
- Optional local ASR uses Intel
  [`OpenVINO`](https://github.com/openvinotoolkit/openvino) and
  [`OpenVINO GenAI`](https://github.com/openvinotoolkit/openvino.genai), plus the converted
  [`OpenVINO/whisper-small-int8-ov`](https://huggingface.co/OpenVINO/whisper-small-int8-ov)
  model. These upstream projects/model identify Apache-2.0 licensing; OpenAI Whisper's original
  implementation and weights identify the MIT license.
- Local VAD and anonymous speaker diarization use
  [`sherpa-onnx`](https://github.com/k2-fsa/sherpa-onnx), Silero VAD, pyannote segmentation and
  3D-Speaker model assets. Downloads are pinned to immutable URLs and verified SHA-256; upstream
  archive/model notices remain authoritative and must accompany redistribution.
- The Windows bundle includes Deno under the MIT license and a PyInstaller
  bootloader under PyInstaller's GPLv2-or-later license with its special
  exception for bundled applications. Exact license texts are copied into the
  installer's `licenses/` directory at build time.
- The current Windows bundle uses the pinned BtbN FFmpeg GPLv3 build declared in
  `mise.toml`. Its `LICENSE.txt` is copied into the installer. External
  redistribution of that bundle requires satisfying FFmpeg and enabled-library
  GPL source/notice obligations; a private local build is not a public release.

Neither source project included a repository-wide `LICENSE` file at migration time. This notice does not grant a license for the combined project; choose and add an appropriate project license before redistribution.
