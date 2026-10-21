"""Download the Kokoro v1.0 ONNX model files used by the optional TTS feature.

The files (~330 MB) are published by the kokoro-onnx project and are never committed to
this repository. Re-running the script resumes an interrupted download.

    uv run python scripts/download_kokoro_models.py [--dir PATH]

Default directory: $ECHO_TTS_MODELS_PATH, else .runtime/models/kokoro (the dev cache root).
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "components" / "medialab" / "src"))
from medialab.tts import MODEL_MIN_BYTES, MODEL_URLS, MODELS_ENV  # noqa: E402

DEFAULT_DIR = Path(__file__).resolve().parents[1] / ".runtime" / "models" / "kokoro"


def download(url: str, destination: Path, minimum: int) -> None:
    if destination.is_file() and destination.stat().st_size >= minimum:
        print(f"Already present: {destination}")
        return
    partial = destination.with_name(destination.name + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
    with urllib.request.urlopen(request, timeout=60) as response:
        mode = "ab" if offset and response.status == 206 else "wb"
        with partial.open(mode) as handle:
            while chunk := response.read(1 << 20):
                handle.write(chunk)
    size = partial.stat().st_size
    if size < minimum:
        raise SystemExit(f"{destination.name} is unexpectedly small ({size} bytes); re-run to resume")
    os.replace(partial, destination)
    print(f"Ready: {destination} ({size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path(os.environ.get(MODELS_ENV) or DEFAULT_DIR))
    args = parser.parse_args()
    args.dir.mkdir(parents=True, exist_ok=True)
    for name, url in MODEL_URLS.items():
        download(url, args.dir / name, MODEL_MIN_BYTES[name])
    print(f"Done. Point {MODELS_ENV} at {args.dir} or set [tts] model_dir in config.toml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
