from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ART_DIR = FIXTURES / "quantity_conflict_artifacts"


@pytest.fixture()
def quantity_manifest_path(tmp_path: Path) -> Path:
    raw = (FIXTURES / "manifest_quantity_conflict.json").read_text(encoding="utf-8")
    m = json.loads(raw)
    for art in m["artifacts"]:
        fn = art["filename"]
        src = ART_DIR / fn
        assert src.is_file(), f"missing fixture {src}"
        art["blob_url"] = src.resolve().as_uri()
    man_path = tmp_path / "manifest.json"
    man_path.write_text(json.dumps(m), encoding="utf-8")
    return man_path


@pytest.fixture()
def scope_fixture_path() -> Path:
    return FIXTURES / "scope_process_v1.example.json"


@pytest.fixture()
def bang_internal_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BANG_INTERNAL_BEARER", raising=False)
