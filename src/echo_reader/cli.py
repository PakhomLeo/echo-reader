"""Command line entry point for importing content into Echo Reader."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Sequence

from .app_config import AppPaths
from .integration import ImportCoordinator, ImportFailed


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echo-reader-import",
        description="Import a URL with MediaLab and refresh the Echo library.",
    )
    parser.add_argument("url")
    parser.add_argument("--mode", choices=("auto", "download", "transcribe"), default="auto")
    parser.add_argument("--library-root", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--tmp-root", type=Path)
    parser.add_argument("--config-root", type=Path)
    parser.add_argument("--install-mode", action="store_true", default=None)
    parser.add_argument("--echo-url", default="http://127.0.0.1:3000")
    parser.add_argument("--no-rescan", action="store_true")
    parser.add_argument("--strict-rescan", action="store_true")
    parser.add_argument("--audio", help="Audio codec used in download mode")
    parser.add_argument("--audio-format", default="m4a", help="Audio codec used in transcribe mode")
    parser.add_argument("--speed-factor", type=float, default=1.0)
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--asr-backend",
        choices=("online", "local"),
        default="online",
        help="ASR backend used in transcribe mode (default: online)",
    )
    parser.add_argument(
        "--local-timestamp-mode",
        choices=("model", "vad"),
        default="vad",
        help="Timestamp source for local ASR (default: vad for mixed-language audio)",
    )
    parser.add_argument(
        "--local-diarization",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable anonymous speaker diarization for local ASR",
    )
    parser.add_argument(
        "--local-speaker-count",
        type=int,
        default=0,
        metavar="N",
        help="Expected local speaker count (0 lets the diarizer infer it)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


async def run(args: argparse.Namespace) -> dict:
    from medialab.bridge import SessionBridgeServer

    if args.asr_backend == "local":
        if args.mode != "transcribe":
            raise ValueError("local ASR requires --mode transcribe")
        if args.speed_factor != 1:
            raise ValueError("local ASR requires --speed-factor 1")
        if args.timeout is not None:
            raise ValueError("local ASR does not accept --timeout")
        if not 0 <= args.local_speaker_count <= 20:
            raise ValueError("--local-speaker-count must be between 0 and 20")
        if not args.local_diarization and args.local_speaker_count:
            raise ValueError(
                "--local-speaker-count must be 0 when diarization is disabled"
            )

    bridge = SessionBridgeServer()
    bridge.start()
    try:
        paths = AppPaths.resolve(
            library_root=args.library_root, runtime_root=args.runtime_root,
            state_root=args.state_root, cache_root=args.cache_root,
            tmp_root=args.tmp_root, config_root=args.config_root,
            install_mode=args.install_mode,
        )
        coordinator = ImportCoordinator(
            paths.library_root,
            paths.state_root,
            args.echo_url,
            bridge=bridge,
            cache_root=paths.cache_root,
            tmp_root=paths.tmp_root,
            config_root=paths.config_root,
        )
        await coordinator.initialize()
        kwargs = {
            "mode": args.mode,
            "audio": args.audio,
            "audio_format": args.audio_format,
            "speed_factor": args.speed_factor,
            "timeout": args.timeout,
            "rescan": not args.no_rescan,
            "strict_rescan": args.strict_rescan,
        }
        if args.asr_backend == "local":
            kwargs.update(
                asr_backend="local",
                local_timestamp_mode=args.local_timestamp_mode,
                local_diarization=args.local_diarization,
                local_speaker_count=args.local_speaker_count,
            )
        try:
            return await coordinator.import_url(args.url, **kwargs)
        finally:
            await coordinator.close()
    finally:
        bridge.stop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except (ImportFailed, ValueError) as exc:
        parser.exit(1, f"echo-reader-import: {exc}\n")
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        task = result["task"]
        print(f"Import complete: task {task.get('id', 'unknown')}")
        if result["rescan"] is not None:
            scan = result["rescan"]
            print(
                f"Echo library: {scan['item_count']} items, "
                f"{scan['invalid_item_count']} invalid"
            )
        if result["warning"]:
            print(f"Warning: {result['warning']}")
    return 0
