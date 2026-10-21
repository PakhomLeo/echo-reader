from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from echo_reader.echo_api import EchoApiClient, EchoApiError
from echo_reader.integration import ImportCoordinator, ImportFailed


class FakeHandle:
    id = "task-1"
    def __init__(self, result): self.result = result
    async def wait(self, service): return self.result


class EchoApiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_clients_ignore_proxies_and_switch_has_no_timeout(self):
        client_kwargs = []
        switch_request_kwargs = []

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, url, **kwargs):
                if url.endswith("/internal/v1/library/switch"):
                    switch_request_kwargs.append(kwargs)
                    await asyncio.sleep(0.01)
                    path = kwargs["json"]["path"]
                    return httpx.Response(200, json={
                        "library_root": path,
                        "item_count": 1,
                        "invalid_item_count": 0,
                    }, request=httpx.Request("POST", url))
                return httpx.Response(
                    200,
                    json={"item_count": 1, "invalid_item_count": 0},
                    request=httpx.Request("POST", url),
                )

            async def get(self, url, **kwargs):
                return httpx.Response(
                    200, json={"status": "ok"}, request=httpx.Request("GET", url)
                )

        def make_client(**kwargs):
            client_kwargs.append(kwargs)
            return Client()

        with patch("echo_reader.echo_api.httpx.AsyncClient", side_effect=make_client):
            echo = EchoApiClient(
                "http://127.0.0.1:3000", timeout=0.001, internal_token="token"
            )
            await echo.switch_library("C:/library")
            await echo.rescan()
            await echo.health()

        self.assertEqual(client_kwargs, [
            {"trust_env": False, "timeout": None},
            {"trust_env": False},
            {"trust_env": False},
        ])
        self.assertNotIn("timeout", switch_request_kwargs[0])

    async def test_rescan_posts_and_validates_counts(self):
        seen = []
        async def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"item_count": 4, "invalid_item_count": 1})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await EchoApiClient("http://echo.test/", client=client).rescan()
        self.assertEqual(result, {"item_count": 4, "invalid_item_count": 1})
        self.assertEqual(str(seen[0].url), "http://echo.test/api/v1/library/rescan")

    async def test_rescan_rejects_invalid_shape(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"item_count": True}))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(EchoApiError):
                await EchoApiClient("http://echo.test", client=client).rescan()

    async def test_read_only_health_contract(self):
        seen = []
        async def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"status": "ok"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await EchoApiClient("http://echo.test/", client=client).health()
        self.assertEqual(str(seen[0].url), "http://echo.test/health")
        self.assertEqual(seen[0].method, "GET")


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.library, self.runtime = root / "library", root / "runtime"

    async def asyncTearDown(self): self.temp.cleanup()

    async def test_long_lived_service_new_submission_seam_and_explicit_rescan(self):
        service = Mock()
        service.initialize = AsyncMock()
        service.close = AsyncMock()
        service.submit = AsyncMock(return_value=FakeHandle({"id": "task-1", "status": "done"}))
        service.capabilities.return_value = {"transcription": {}}
        service.plan_import.return_value = {"mode": "transcribe", "asr_backend": "online"}
        echo = Mock(rescan=AsyncMock(return_value={"item_count": 1, "invalid_item_count": 0}))
        coordinator = ImportCoordinator(self.library, self.runtime,
            service_factory=Mock(return_value=service), echo_client=echo)
        await coordinator.initialize()
        self.assertEqual(coordinator.capabilities(), {"transcription": {}})
        self.assertEqual((await coordinator.plan_url("https://x.test", mode="auto", asr_backend="auto"))["mode"], "transcribe")
        task = await coordinator.process_url("https://x.test", mode="auto", asr_backend="auto")
        self.assertEqual(task["status"], "done")
        service.submit.assert_awaited_once_with(
            "https://x.test", out_dir=self.library.resolve(), mode="auto", asr_backend="auto",
            local_timestamp_mode="vad", local_diarization=True, local_speaker_count=0,
        )
        self.assertEqual(await coordinator.rescan(), {"item_count": 1, "invalid_item_count": 0})
        await coordinator.close()
        service.initialize.assert_awaited_once(); service.close.assert_awaited_once()

    async def test_failed_medialab_task_is_explicit(self):
        service = Mock(initialize=AsyncMock(), close=AsyncMock())
        service.capabilities.return_value = {"transcription": {}}
        service.submit = AsyncMock(return_value=FakeHandle({"status": "failed", "error_detail": "blocked"}))
        coordinator = ImportCoordinator(self.library, self.runtime, service_factory=Mock(return_value=service))
        await coordinator.initialize()
        with self.assertRaisesRegex(ImportFailed, "failed.*blocked"):
            await coordinator.process_url("https://x.test")
        await coordinator.close()

    async def test_failed_medialab_task_preserves_structured_error(self):
        service = Mock(initialize=AsyncMock(), close=AsyncMock())
        service.capabilities.return_value = {"transcription": {}}
        service.submit = AsyncMock(return_value=FakeHandle({
            "id": "task-structured", "status": "failed", "error_detail": "network denied",
            "error_code": "network", "error_source": "browser_http", "error_retryable": False,
        }))
        coordinator = ImportCoordinator(
            self.library, self.runtime, service_factory=Mock(return_value=service)
        )
        await coordinator.initialize()
        with self.assertRaises(ImportFailed) as raised:
            await coordinator.process_url("https://x.test")
        self.assertEqual(raised.exception.code, "network")
        self.assertEqual(raised.exception.source, "browser_http")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.task_id, "task-1")
        await coordinator.close()

    async def test_initialize_prewarms_capabilities_once_off_event_loop(self):
        event_loop_thread = threading.get_ident()
        probe_threads: list[int] = []

        class Service:
            def __init__(self):
                self.initialize_calls = 0
                self.close_calls = 0
            async def initialize(self): self.initialize_calls += 1
            async def close(self): self.close_calls += 1
            def capabilities(self):
                probe_threads.append(threading.get_ident())
                return {"transcription": {}}

        service = Service()
        coordinator = ImportCoordinator(
            self.library, self.runtime, service_factory=lambda **kwargs: service
        )
        await coordinator.initialize()
        await coordinator.initialize()
        self.assertEqual(service.initialize_calls, 1)
        self.assertEqual(len(probe_threads), 1)
        self.assertNotEqual(probe_threads[0], event_loop_thread)

        self.assertEqual(coordinator.capabilities(), {"transcription": {}})
        self.assertEqual(probe_threads, [probe_threads[0], event_loop_thread])
        await coordinator.close()
        self.assertEqual(service.close_calls, 1)
