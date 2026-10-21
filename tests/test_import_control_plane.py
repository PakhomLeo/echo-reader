from __future__ import annotations

import threading
import time
import difflib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from echo_reader.app_config import AppPaths
from echo_reader.import_store import ImportStore, sanitize_payload
from echo_reader.server import ImportService, _content_fingerprint, create_app

LOOPBACK = "http://127.0.0.1:3100"


class FakeCoordinator:
    def __init__(self, *, index_error: bool = False):
        self.initialized = 0
        self.closed = 0
        self.processed: list[str] = []
        self.calls: list[dict] = []
        self.index_error = index_error

    async def initialize(self): self.initialized += 1
    async def close(self): self.closed += 1
    async def index_health(self): return None
    def capabilities(self):
        return {"transcription": {"online": {"available": True, "blocker": None},
                                  "local": {"available": False, "blocker": "not installed"}}}
    async def plan_url(self, url, *, mode, asr_backend):
        if asr_backend == "local":
            return {"blocker": "local unavailable"}
        return {"requested_mode": mode, "mode": "download", "asr_backend": "online", "blocker": None}
    async def process_url(self, url, **kwargs):
        self.processed.append(url)
        self.calls.append(kwargs)
        callback = kwargs["on_event"]
        callback("task_progress", {"task_id": "fake", "stage": "downloading", "downloaded_bytes": 1, "total_bytes": 2})
        return {"status": "done"}
    async def rescan(self):
        if self.index_error:
            raise RuntimeError("index offline")
        return {"item_count": 1, "invalid_item_count": 0}


def wait_for(client: TestClient, job_id: str, status: str):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/import-jobs/{job_id}").json()
        if job["status"] == status:
            return job
        time.sleep(0.01)
    raise AssertionError(job)


def test_atomic_batch_validation_paging_sanitization_and_lifecycle(tmp_path: Path):
    coordinator = FakeCoordinator()
    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: coordinator, bridge=None)
    with TestClient(app, base_url=LOOPBACK) as client:
        assert client.post("/api/v1/import-batches", json={"urls": []}).status_code == 422
        assert client.post("/api/v1/import-batches", json={"urls": [f"https://x.test/{i}" for i in range(51)]}).status_code == 422
        assert client.get("/api/v1/import-jobs").json()["total"] == 0
        case_distinct = client.post("/api/v1/import-batches", json={
            "urls": ["https://x.test/Path?Key=A", "https://x.test/path?Key=A"]
        })
        assert case_distinct.status_code == 202
        for item in case_distinct.json()["items"]:
            wait_for(client, item["id"], "succeeded")
        response = client.post("/api/v1/import-batches", json={
            "urls": ["https://x.test/a?token=secret&item=1", "https://x.test/b"]
        })
        assert response.status_code == 202
        jobs = response.json()["items"]
        assert "secret" not in jobs[0]["url"]
        for job in jobs:
            wait_for(client, job["id"], "succeeded")
        page = client.get("/api/v1/import-jobs", params={"page": 1, "page_size": 1, "status": "succeeded"}).json()
        assert (page["total"], len(page["items"])) == (4, 1)
        assert page["counts"] == {"queued": 0, "running": 0, "pending_index": 0,
                                  "succeeded": 4, "failed": 0, "cancelled": 0, "interrupted": 0}
        assert client.get(f"/api/v1/import-jobs/{jobs[0]['id']}/timeline").json()["items"]
        assert any(url.endswith("token=secret&item=1") for url in coordinator.processed)
    assert (coordinator.initialized, coordinator.closed) == (1, 1)


def test_capability_rejection_is_atomic_and_pending_index_retry_only_rescans(tmp_path: Path):
    coordinator = FakeCoordinator(index_error=True)
    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: coordinator, bridge=None)
    with TestClient(app, base_url=LOOPBACK) as client:
        rejected = client.post("/api/v1/import-batches", json={
            "urls": ["https://x.test/a", "https://x.test/b"],
            "mode": "transcribe", "asr_backend": "local",
        })
        assert rejected.status_code == 422
        assert client.get("/api/v1/import-jobs").json()["total"] == 0
        created = client.post("/api/v1/import-batches", json={"urls": ["https://x.test/a"]}).json()
        pending = wait_for(client, created["items"][0]["id"], "pending_index")
        assert pending["can_retry"] is False
        assert client.post(f"/api/v1/import-jobs/{pending['id']}/retry").status_code == 409


def test_exact_frontend_batch_contract_and_response(tmp_path: Path):
    coordinator = FakeCoordinator()
    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: coordinator, bridge=None)
    body = {"urls": ["https://a.test/"], "mode": "auto", "audio": None,
            "audio_format": "mp3", "speed_factor": 1, "timeout": 1800,
            "asr_backend": "auto", "local_timestamp_mode": "vad",
            "local_diarization": True, "local_speaker_count": 0}
    with TestClient(app, base_url=LOOPBACK) as client:
        response = client.post("/api/v1/import-batches", json=body)
        assert response.status_code == 202
        job = wait_for(client, response.json()["items"][0]["id"], "succeeded")
        assert {"id", "batch_id", "position", "requested_mode", "effective_mode",
                "asr_backend", "eta_seconds", "quality", "duplicate", "can_reindex",
                "item_path"} <= job.keys()
        assert "out_dir" not in job
        call = coordinator.calls[0]
        assert {key: call[key] for key in ("audio", "audio_format", "speed_factor", "timeout")} == {
            "audio": None, "audio_format": "mp3", "speed_factor": 1.0, "timeout": 1800.0}


def test_restart_interrupts_active_but_preserves_pending_index(tmp_path: Path):
    path = tmp_path / "imports.db"
    library = tmp_path / "library"; library.mkdir()
    store = ImportStore(path, library)
    batch = store.create_batch(["https://x.test/a", "https://x.test/b"], {"mode": "auto", "asr_backend": "auto",
        "local_timestamp_mode": "vad", "local_diarization": True, "local_speaker_count": 0},
        [{"mode": "download", "asr_backend": "online"}] * 2)
    first, second = batch["items"]
    store.update(first["id"], status="running")
    (library / "item").mkdir()
    store.update(second["id"], status="pending_index", item_path="item")
    store.close()
    reopened = ImportStore(path, library)
    assert reopened.get_job(first["id"])["status"] == "interrupted"
    pending = reopened.get_job(second["id"])
    assert pending["status"] == "pending_index"
    assert pending["error"]["code"] == "index_interrupted"
    assert pending["is_active"] is False and pending["can_reindex"] is True
    page = reopened.list_jobs(page=1, page_size=10)
    assert page["active_count"] == 0
    reopened.close()


def test_job_counts_ignore_status_and_page_but_respect_batch_scope(tmp_path: Path):
    store = ImportStore(tmp_path / "imports.db", tmp_path)
    options = {"mode": "auto", "audio": None, "audio_format": "m4a", "speed_factor": 1,
               "timeout": None, "asr_backend": "auto", "local_timestamp_mode": "vad",
               "local_diarization": True, "local_speaker_count": 0}
    plan = {"requested_mode": "auto", "mode": "download", "asr_backend": "online"}
    first_batch = store.create_batch(["https://x.test/a", "https://x.test/b"], options, [plan, plan])
    second_batch = store.create_batch(["https://x.test/c"], options, [plan])
    store.update(first_batch["items"][0]["id"], status="failed")
    store.update(second_batch["items"][0]["id"], status="succeeded")

    scoped = store.list_jobs(page=1, page_size=1, status="failed", batch_id=first_batch["id"])
    assert scoped["total"] == 1 and len(scoped["items"]) == 1
    assert scoped["counts"] == {"queued": 1, "running": 0, "pending_index": 0,
                                 "succeeded": 0, "failed": 1, "cancelled": 0, "interrupted": 0}
    global_page = store.list_jobs(page=1, page_size=1, status="succeeded")
    assert global_page["total"] == 1
    assert global_page["counts"]["queued"] == 1
    assert global_page["counts"]["failed"] == 1
    assert global_page["counts"]["succeeded"] == 1
    store.close()


def test_local_auto_normalization_quality_error_and_raw_retry(tmp_path: Path):
    class LocalCoordinator(FakeCoordinator):
        def __init__(self): super().__init__(); self.attempts = 0
        def capabilities(self):
            return {"transcription": {
                "online": {"available": False, "blocker": "unavailable"},
                "local": {"available": True, "blocker": "download_required"},
            }}
        async def plan_url(self, url, *, mode, asr_backend):
            return {"requested_mode": mode, "mode": "transcribe", "asr_backend": "local", "blocker": None}
        async def process_url(self, url, **kwargs):
            self.processed.append(url); self.calls.append(kwargs); self.attempts += 1
            kwargs["on_event"]("task_progress", {"stage": "quality_check", "quality": {"coverage": .97}})
            if self.attempts == 1:
                raise RuntimeError("local ASR quality gate rejected transcript")
            return {"status": "done"}

    coordinator = LocalCoordinator()
    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: coordinator, bridge=None)
    with TestClient(app, base_url=LOOPBACK) as client:
        capabilities = client.get("/api/v1/import-capabilities").json()
        assert capabilities["backends"]["local"] == {
            "available": True, "readiness": "download_required",
            "reason": "Model download required on first use",
        }
        created = client.post("/api/v1/import-batches", json={
            "urls": ["https://video.test/watch?token=secret&id=1"], "mode": "auto",
            "audio": None, "audio_format": "mp3", "speed_factor": 2,
            "timeout": 1800, "asr_backend": "local",
            "local_timestamp_mode": "vad", "local_diarization": True, "local_speaker_count": 0,
        })
        assert created.status_code == 202
        failed = wait_for(client, created.json()["items"][0]["id"], "failed")
        assert failed["error"]["code"] == "transcript_quality"
        retry = client.post(f"/api/v1/import-jobs/{failed['id']}/retry")
        assert retry.status_code == 202
        succeeded = wait_for(client, failed["id"], "succeeded")
        assert succeeded["quality"] == {"coverage": .97}
        assert coordinator.processed == [
            "https://video.test/watch?token=secret&id=1",
            "https://video.test/watch?token=secret&id=1",
        ]
        assert coordinator.calls[0]["speed_factor"] == 1.0
        assert coordinator.calls[0]["timeout"] is None


def test_eta_uses_successful_matching_sample_and_active_work_ahead(tmp_path: Path):
    service = ImportService(AppPaths.resolve(library_root=tmp_path / "library",
                                             runtime_root=tmp_path / "runtime"))
    options = {"mode": "transcribe", "audio": None, "audio_format": "m4a", "speed_factor": 1,
               "timeout": None, "asr_backend": "local", "local_timestamp_mode": "vad",
               "local_diarization": True, "local_speaker_count": 0}
    plan = {"requested_mode": "transcribe", "mode": "transcribe", "asr_backend": "local"}
    sample = service.store.create_batch(["https://x.test/sample"], options, [plan])["items"][0]
    service.store.update(sample["id"], status="succeeded", started_at="2026-01-01T00:00:00+00:00",
                         completed_at="2026-01-01T00:00:10+00:00")
    ahead = service.store.create_batch(["https://x.test/ahead"], options, [plan])["items"][0]
    target = service.store.create_batch(["https://x.test/target"], options, [plan])["items"][0]
    assert service.decorate(target)["eta_seconds"] == 20
    assert service.decorate(ahead)["eta_seconds"] == 10
    service.store.close()


def test_transcript_json_supports_suspected_similarity(tmp_path: Path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    (first / "transcript.json").write_text(
        '{"segments":[{"text":"The quick brown fox jumps over the lazy dog."},{"text":"A stable ending."}]}', encoding="utf-8")
    (second / "transcript.json").write_text(
        '{"segments":[{"text":"The quick brown fox jumps over the lazy dog!"},{"text":"A stable ending."}]}', encoding="utf-8")
    _, first_text = _content_fingerprint(first)
    _, second_text = _content_fingerprint(second)
    assert first_text and second_text
    assert difflib.SequenceMatcher(None, first_text, second_text, autojunk=False).ratio() >= .9
    service = ImportService(AppPaths.resolve(library_root=tmp_path,
                                             runtime_root=tmp_path / "runtime"))
    options = {"mode": "auto", "audio": None, "audio_format": "m4a", "speed_factor": 1,
               "timeout": None, "asr_backend": "auto", "local_timestamp_mode": "vad",
               "local_diarization": True, "local_speaker_count": 0}
    plan = {"requested_mode": "auto", "mode": "auto", "asr_backend": None}
    prior = service.store.create_batch(["https://x.test/first"], options, [plan])["items"][0]
    current = service.store.create_batch(["https://x.test/second"], options, [plan])["items"][0]
    service.store.update(prior["id"], status="succeeded", item_path="first", item_id="first")
    duplicate = service._find_duplicate(current["id"], second)
    assert duplicate and duplicate["kind"] == "suspected" and duplicate["job_id"] == prior["id"]
    service.store.close()


def test_pending_index_only_reindexes_and_batch_retry_failed_skips_it(tmp_path: Path):
    output = tmp_path / "library" / "item"
    output.mkdir(parents=True)
    (output / "manifest.json").write_text('{"id":"item-1"}', encoding="utf-8")

    class OutputCoordinator(FakeCoordinator):
        async def process_url(self, url, **kwargs):
            self.processed.append(url)
            return {"status": "done", "out_dir": str(output)}

    coordinator = OutputCoordinator(index_error=True)
    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: coordinator, bridge=None)
    with TestClient(app, base_url=LOOPBACK) as client:
        batch = client.post("/api/v1/import-batches", json={"urls": ["https://x.test/a"]}).json()
        job_id = batch["items"][0]["id"]
        deadline = time.monotonic() + 3
        while True:
            pending = client.get(f"/api/v1/import-jobs/{job_id}").json()
            if pending["status"] == "pending_index" and pending["can_reindex"]:
                break
            if time.monotonic() >= deadline:
                raise AssertionError(pending)
            time.sleep(.01)
        assert pending["can_retry"] is False and pending["can_reindex"] is True
        assert pending["is_active"] is False
        batch_state = client.get(f"/api/v1/import-batches/{batch['id']}").json()
        assert batch_state["status"] == "pending_index" and batch_state["active_count"] == 0
        capability = client.get("/api/v1/import-capabilities").json()["indexing"]
        assert capability["available"] is False and "offline" in capability["reason"]
        assert client.post(f"/api/v1/import-jobs/{job_id}/retry").status_code == 409
        unchanged = client.post(f"/api/v1/import-batches/{batch['id']}/retry-failed").json()
        assert unchanged["items"][0]["attempt"] == 1
        coordinator.index_error = False
        reindex = client.post(f"/api/v1/import-jobs/{job_id}/reindex")
        assert reindex.status_code == 202
        wait_for(client, job_id, "succeeded")
        assert coordinator.processed == ["https://x.test/a"]
        assert client.get("/api/v1/import-capabilities").json()["indexing"] == {
            "available": True, "reason": None,
        }


def test_duplicate_warning_path_containment_and_single_origin_boundary(tmp_path: Path):
    library = tmp_path / "library"
    first_dir, second_dir = library / "first", library / "second"
    for index, directory in enumerate((first_dir, second_dir), 1):
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            f'{{"id":"item-{index}","warnings":["manifest-warning"]}}', encoding="utf-8")
        (directory / "media.mp3").write_bytes(b"identical-media")

    class OutputCoordinator(FakeCoordinator):
        def __init__(self):
            super().__init__(); self.outputs = [first_dir, second_dir, tmp_path.parent]
        async def process_url(self, url, **kwargs):
            self.processed.append(url)
            kwargs["on_event"]("task_progress", {"stage": "publishing",
                                                   "warning": "event-warning"})
            return {"status": "done", "out_dir": str(self.outputs.pop(0))}

    coordinator = OutputCoordinator()
    app = create_app(library_root=library, runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: coordinator, bridge=None)
    with TestClient(app, base_url=LOOPBACK) as client:
        response = client.options("/api/v1/import-batches", headers={
            "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"
        })
        assert "access-control-allow-origin" not in response.headers
        ids = []
        for suffix in ("one", "two", "escape"):
            created = client.post("/api/v1/import-batches", json={"urls": [f"https://x.test/{suffix}"]}).json()
            ids.append(created["items"][0]["id"])
            wait_for(client, ids[-1], "succeeded")
        duplicate = client.get(f"/api/v1/import-jobs/{ids[1]}").json()["duplicate"]
        assert duplicate == {"job_id": ids[0], "item_id": "item-1", "score": 1.0, "kind": "exact"}
        warning = client.get(f"/api/v1/import-jobs/{ids[1]}").json()["warning"]
        assert warning == "manifest-warning | event-warning | Possible duplicate content preserved"
        escaped = client.get(f"/api/v1/import-jobs/{ids[2]}").json()
        assert escaped["item_path"] is None and escaped["item_id"] is None


def test_three_dispatchers_bound_overlap_and_api_stays_responsive(tmp_path: Path):
    release = threading.Event()
    lock = threading.Lock()

    class BlockingCoordinator(FakeCoordinator):
        def __init__(self):
            super().__init__(); self.active = 0; self.maximum = 0
        async def process_url(self, url, **kwargs):
            import asyncio
            with lock:
                self.active += 1; self.maximum = max(self.maximum, self.active)
            try:
                kwargs["on_event"]("task_progress", {
                    "task_id": url, "stage": "waiting_for_asr",
                })
                await asyncio.to_thread(release.wait, 2)
                return {"status": "done"}
            finally:
                with lock: self.active -= 1

    coordinator = BlockingCoordinator()
    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: coordinator, bridge=None)
    with TestClient(app, base_url=LOOPBACK) as client:
        created = client.post("/api/v1/import-batches", json={
            "urls": [f"https://x.test/{i}" for i in range(4)]
        }).json()
        deadline = time.monotonic() + 2
        while coordinator.maximum < 3 and time.monotonic() < deadline: time.sleep(0.01)
        started = time.perf_counter()
        assert client.get("/api/v1/health").status_code == 200
        assert time.perf_counter() - started < 0.5
        assert coordinator.maximum == 3
        alternate = tmp_path / "alternate"; alternate.mkdir()
        blocked_switch = client.post("/api/v1/library/switch", json={"path": str(alternate)})
        assert blocked_switch.status_code == 409
        assert blocked_switch.json()["detail"]["code"] == "imports_active"
        waiting = client.get(f"/api/v1/import-jobs/{created['items'][0]['id']}").json()
        assert waiting["stage"] == "waiting_for_asr"
        assert waiting["message"] == "Waiting for ASR capacity"
        release.set()
        for job in created["items"]: wait_for(client, job["id"], "succeeded")
        assert coordinator.maximum == 3


def test_persistence_sanitizes_embedded_urls_and_exception_chains(tmp_path: Path):
    sanitized = sanitize_payload({
        "message": "first HTTPS://a.test/x?ToKeN=one&keep=1, second https://b.test/y?X-AmZ-Signature=two&utm_Source=track&ok=2"
    })["message"]
    assert "one" not in sanitized and "two" not in sanitized and "track" not in sanitized
    assert "keep=1" in sanitized and "ok=2" in sanitized

    class FailingCoordinator(FakeCoordinator):
        async def process_url(self, url, **kwargs):
            try:
                raise ValueError("inner https://inner.test/?Authorization=secret&safe=1")
            except ValueError as exc:
                raise RuntimeError("outer https://outer.test/?x-goog-token=hidden&item=2") from exc

    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: FailingCoordinator(), bridge=None)
    with TestClient(app, base_url=LOOPBACK) as client:
        created = client.post("/api/v1/import-batches", json={"urls": ["https://x.test/a"]}).json()
        failed = wait_for(client, created["items"][0]["id"], "failed")
        serialized = str(failed["error"])
        assert "secret" not in serialized and "hidden" not in serialized
        assert "safe=1" in serialized and "item=2" in serialized
        assert failed["error"]["source"] == "import_service"
        timeline = str(client.get(f"/api/v1/import-jobs/{failed['id']}/timeline").json())
        assert "secret" not in timeline and "hidden" not in timeline


def test_state_transitions_are_compare_and_swap(tmp_path: Path):
    store = ImportStore(tmp_path / "imports.db", tmp_path)
    options = {"mode": "auto", "audio": None, "audio_format": "m4a", "speed_factor": 1,
               "timeout": None, "asr_backend": "auto", "local_timestamp_mode": "vad",
               "local_diarization": True, "local_speaker_count": 0}
    plan = {"requested_mode": "auto", "mode": "download", "asr_backend": "online"}
    queued = store.create_batch(["https://x.test/a"], options, [plan])["items"][0]
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(lambda: (barrier.wait(), store.claim_queued(queued["id"]))[1])
        cancel = pool.submit(lambda: (barrier.wait(), store.cancel_queued(queued["id"]))[1])
        results = [claim.result(), cancel.result()]
    assert sum(result is not None for result in results) == 1
    assert store.get_job(queued["id"])["status"] in {"running", "cancelled"}

    retryable = store.create_batch(["https://x.test/b"], options, [plan])["items"][0]
    store.update(retryable["id"], status="failed")
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        retries = [pool.submit(lambda: (barrier.wait(), store.retry_job(retryable["id"]))[1]) for _ in range(2)]
        retry_results = [future.result() for future in retries]
    assert sum(result is not None for result in retry_results) == 1
    assert store.get_job(retryable["id"])["attempt"] == 2
    store.close()


def test_index_health_failure_is_async_and_sanitized(tmp_path: Path):
    class UnhealthyCoordinator(FakeCoordinator):
        async def index_health(self):
            raise RuntimeError("Echo unavailable at https://echo.test/health?TOKEN=secret&node=one")

    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: UnhealthyCoordinator(), bridge=None)
    with TestClient(app, base_url=LOOPBACK) as client:
        deadline = time.monotonic() + 2
        indexing = None
        while time.monotonic() < deadline:
            indexing = client.get("/api/v1/import-capabilities").json()["indexing"]
            if indexing["reason"] != "Echo health check pending":
                break
            time.sleep(.01)
        assert indexing["available"] is False
        assert "secret" not in indexing["reason"] and "node=one" in indexing["reason"]
