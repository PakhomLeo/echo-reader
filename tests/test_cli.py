from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch

import pytest

from echo_reader import cli


def _bridge_module(bridge: Mock) -> ModuleType:
    module = ModuleType("medialab.bridge")
    module.SessionBridgeServer = Mock(return_value=bridge)
    return module


def test_cli_defaults_to_project_content_and_preserves_explicit_override(tmp_path) -> None:
    default_args = cli.build_parser().parse_args(["https://example.com/article"])
    assert default_args.library_root is None

    override = tmp_path / "custom-content"
    explicit_args = cli.build_parser().parse_args(
        ["https://example.com/article", "--library-root", str(override)]
    )
    assert explicit_args.library_root == override


def test_online_cli_keeps_the_existing_coordinator_call() -> None:
    args = cli.build_parser().parse_args(["https://example.com/video", "--mode", "transcribe"])
    bridge = Mock()
    coordinator = Mock()
    coordinator.initialize = AsyncMock()
    coordinator.close = AsyncMock()
    coordinator.import_url = AsyncMock(return_value={"task": {}, "rescan": None, "warning": None})

    with (
        patch.dict(sys.modules, {"medialab.bridge": _bridge_module(bridge)}),
        patch.object(cli, "ImportCoordinator", return_value=coordinator),
    ):
        asyncio.run(cli.run(args))

    coordinator.import_url.assert_awaited_once_with(
        "https://example.com/video",
        mode="transcribe",
        audio=None,
        audio_format="m4a",
        speed_factor=1.0,
        timeout=None,
        rescan=True,
        strict_rescan=False,
    )
    bridge.start.assert_called_once_with()
    bridge.stop.assert_called_once_with()
    coordinator.initialize.assert_awaited_once_with()
    coordinator.close.assert_awaited_once_with()


def test_local_cli_passes_only_explicit_local_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "https://example.com/video",
            "--mode",
            "transcribe",
            "--asr-backend",
            "local",
            "--local-speaker-count",
            "2",
        ]
    )
    bridge = Mock()
    coordinator = Mock()
    coordinator.initialize = AsyncMock()
    coordinator.close = AsyncMock()
    coordinator.import_url = AsyncMock(return_value={"task": {}, "rescan": None, "warning": None})

    with (
        patch.dict(sys.modules, {"medialab.bridge": _bridge_module(bridge)}),
        patch.object(cli, "ImportCoordinator", return_value=coordinator),
    ):
        asyncio.run(cli.run(args))

    coordinator.import_url.assert_awaited_once_with(
        "https://example.com/video",
        mode="transcribe",
        audio=None,
        audio_format="m4a",
        speed_factor=1.0,
        timeout=None,
        rescan=True,
        strict_rescan=False,
        asr_backend="local",
        local_timestamp_mode="vad",
        local_diarization=True,
        local_speaker_count=2,
    )


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--mode", "download"], "requires --mode transcribe"),
        (["--mode", "transcribe", "--speed-factor", "1.5"], "--speed-factor 1"),
        (["--mode", "transcribe", "--timeout", "60"], "does not accept --timeout"),
        (["--mode", "transcribe", "--local-speaker-count", "21"], "between 0 and 20"),
        (
            ["--mode", "transcribe", "--no-local-diarization", "--local-speaker-count", "2"],
            "must be 0",
        ),
    ],
)
def test_local_cli_rejects_incompatible_options(extra: list[str], message: str) -> None:
    args = cli.build_parser().parse_args(
        ["https://example.com/video", "--asr-backend", "local", *extra]
    )
    with pytest.raises(ValueError, match=message):
        asyncio.run(cli.run(args))
