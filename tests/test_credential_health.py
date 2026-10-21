from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from echo_reader.server import create_app
from test_import_control_plane import FakeCoordinator


class FakeBridge:
    def __init__(self):
        self.started = self.stopped = 0

    def start(self): self.started += 1
    def stop(self): self.stopped += 1

    def request_session(self, url, job_id, domains):
        return {"ok": True, "user_agent": "UA", "cookies": [
            {"name": "SUB", "value": "never-returned", "domain": ".weibo.com",
             "expires": time.time() + 30 * 86400},
        ]}


def test_credential_health_without_bridge(tmp_path: Path):
    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: FakeCoordinator(), bridge=None)
    with TestClient(app) as client:
        body = client.get("/api/v1/credential-health", params={"url": "https://www.xiaohongshu.com/explore/1"}).json()
    assert body["bridge_enabled"] is False and body["platforms"] == []
    assert body["target"]["platform"] == "xiaohongshu"
    assert body["target"]["status"] == "missing" and body["target"]["healthy"] is False


def test_credential_health_reports_cached_snapshot_without_values(tmp_path: Path):
    bridge = FakeBridge()
    app = create_app(library_root=tmp_path / "library", runtime_root=tmp_path / "runtime",
                     coordinator_factory=lambda paths: FakeCoordinator(), bridge=bridge)
    with TestClient(app) as client:
        app.state.credential_broker.acquire("https://weibo.com/1/2", "weibo", "job", [".weibo.com", ".weibo.cn"])
        response = client.get("/api/v1/credential-health", params={"url": "https://weibo.com/1/2"})
    assert response.status_code == 200
    body = response.json()
    assert "never-returned" not in response.text
    assert body["bridge_enabled"] is True
    assert body["target"]["status"] == "ok" and body["target"]["cached"] is True
    assert body["target"]["login_cookies_present"] == ["SUB"]
    assert bridge.started == 1 and bridge.stopped == 1
