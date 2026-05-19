"""Unit tests for /v1/orbitbrief/rebuild-latest without full parser-os compile."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from parser_os_service.server.app import app


@pytest.mark.usefixtures("bang_internal_bearer")
def test_orbitbrief_rebuild_latest_uploads_envelope_mocked(
    quantity_manifest_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid importing app.core.compiler (heavy deps); verify envelope upload path and response shape."""
    monkeypatch.setenv("PARSER_OS_SERVICE_LOCAL_BLOB_ROOT", str(tmp_path / "blobout"))

    fake_result = MagicMock()
    fake_result.atoms = []
    fake_result.packets = []
    fake_result.warnings = []
    fake_result.compile_id = "11111111-1111-4111-8111-111111111111"

    fake_envelope = {
        "schema_version": "orbitbrief.input.v2",
        "project_id": "11111111-1111-4111-8111-111111111111",
        "compile_id": "11111111-1111-4111-8111-111111111111",
        "documents": [],
        "atoms": [],
        "packets": [],
    }

    with (
        patch(
            "parser_os_service.server.routes.orbitbrief_latest._run_compile_project",
            return_value=fake_result,
        ),
        patch(
            "parser_os_service.server.routes.orbitbrief_latest._build_envelope",
            return_value=fake_envelope,
        ),
        patch("app.core.schemas.COMPILER_VERSION", "test-version"),
    ):
        client = TestClient(app)
        body = {"manifest_blob_url": quantity_manifest_path.as_uri()}
        r = client.post("/v1/orbitbrief/rebuild-latest", json=body)

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "succeeded"
    assert data["deal_id"] == "22222222-2222-4222-8222-222222222222"
    assert data["compile_id"] == "11111111-1111-4111-8111-111111111111"
    rel = f"deals/{data['deal_id']}/orbitbrief/latest/envelope.json"
    assert data["envelope_blob_path"] == rel
    env_path = tmp_path / "blobout" / rel
    assert env_path.is_file()
    assert json.loads(env_path.read_text(encoding="utf-8"))["schema_version"] == "orbitbrief.input.v2"
    assert "attachments_status" in data
