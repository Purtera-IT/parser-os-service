from __future__ import annotations

from fastapi.testclient import TestClient

from parser_os_service.server.app import app


def test_health_ok() -> None:
    client = TestClient(app)
    r = client.get("/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "parser_os_version" in data
    assert "projector_version" in data
    assert data["uptime_seconds"] >= 0.0
