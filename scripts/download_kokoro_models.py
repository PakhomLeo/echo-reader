"""Download the Kokoro v1.0 ONNX model files used by the optional TTS feature.

The files (~350 MB) are published by the kokoro-onnx project and are never committed to
this repository. Every file is checked against a pinned SHA-256 before it is moved into
place; re-running the script resumes an interrupted download.

    uv run python scripts/download_kokoro_models.py [--dir PATH]

Default directory: $ECHO_TTS_MODELS_PATH, else .runtime/models/kokoro (the dev cache root).
Licences: kokoro-onnx (MIT), Kokoro-82M weights (Apache-2.0). See reports/licenses.md.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "components" / "medialab" / "src"))
from medialab.tts import MODEL_MIN_BYTES, MODEL_SHA256, MODEL_URLS, MODELS_ENV  # noqa: E402

DEFAULT_DIR = Path(__file__).resolve().parents[1] / ".runtime" / "models" / "kokoro"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, minimum: int, expected_sha256: str) -> None:
    if destination.is_file():
        if sha256_of(destination) == expected_sha256:
            print(f"Already present and verified: {destination}")
            return
        raise SystemExit(f"{destination} exists but its SHA-256 does not match; remove it and re-run")
    if not url.startswith("https://"):
        raise SystemExit(f"refusing non-HTTPS model URL: {url}")
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
    actual = sha256_of(partial)
    if actual != expected_sha256:
        partial.unlink()
        raise SystemExit(
            f"{destination.name}: SHA-256 mismatch (expected {expected_sha256}, got {actual}); "
            "the partial file was removed"
        )
    os.replace(partial, destination)
    print(f"Ready: {destination} ({size} bytes, sha256 verified)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path(os.environ.get(MODELS_ENV) or DEFAULT_DIR))
    args = parser.parse_args()
    args.dir.mkdir(parents=True, exist_ok=True)
    for name, url in MODEL_URLS.items():
        download(url, args.dir / name, MODEL_MIN_BYTES[name], MODEL_SHA256[name])
    print(f"Done. Point {MODELS_ENV} at {args.dir} or set [tts] model_dir in config.toml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
