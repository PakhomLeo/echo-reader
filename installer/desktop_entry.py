"""Frozen Windows entry point.

``freeze_support`` must run before importing application modules because the
local ASR engine uses a spawned process in packaged builds.
"""

from __future__ import annotations

import multiprocessing


def main() -> int:
    multiprocessing.freeze_support()
    from echo_reader.desktop import main as run_desktop

    return run_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
