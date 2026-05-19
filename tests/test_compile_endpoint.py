from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from urllib.parse import urlparse

from parser_os_service.server.app import app
from tests.sow_handoff_constraints import validate_sow_handoff_contract


@pytest.mark.usefixtures("bang_internal_bearer")
def test_compile_quantity_conflict_happy_path(
    quantity_manifest_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARSER_OS_SERVICE_LOCAL_BLOB_ROOT", str(tmp_path / "blobout"))

    client = TestClient(app)
    body = {
        "compile_id": "11111111-1111-4111-8111-111111111111",
        "deal_id": "22222222-2222-4222-8222-222222222222",
        "manifest_blob_url": quantity_manifest_path.as_uri(),
    }
    r = client.post("/v1/compile", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "succeeded"
    assert data["summary"]["artifact_count"] == 2
    assert data["summary"]["packet_count"] >= 1
    assert data["summary"]["parser_os_version"]
    assert len(data["scope_process_v1"]["orbitbriefAudit"]["evidenceMap"]) > 0

    atoms = data.get("scope_process_v1", {}).get("orbitbriefAudit", {}).get("evidenceMap", {})
    assert len(atoms) > 0

    validate_sow_handoff_contract(data["scope_process_v1"]["sowHandoff"])

    result_path = Path(urlparse(data["result_blob_url"]).path)
    blob = json.loads(result_path.read_text(encoding="utf-8"))
    packets = blob.get("packets") or []
    qc = [p for p in packets if p.get("family") == "quantity_conflict"]
    assert qc, "expected at least one quantity_conflict packet in result.json"
    unknownish = sum(1 for p in qc if "unknown" in str(p.get("anchor_key", "")).lower())
    assert unknownish / max(len(qc), 1) <= 0.10

    assert data["summary"]["atom_count"] > 0
