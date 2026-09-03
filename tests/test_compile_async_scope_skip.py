"""The service-side unchanged-skip must not let a cut stand in for a full run.

/v1/compile/async dedupes BEFORE anything is queued: same artifact_key as the
last successful compile -> return that compile_id, enqueue nothing. Keyed on the
artifact set alone, a cut compile (documents up to a date) and a full one were
interchangeable whenever the deal's full corpus equalled the cut set -- an
explicit "All data" request came back as the cut, silently.
"""
from __future__ import annotations
import hashlib, json
from parser_os_service.server.routes import compile_async as ca

CUT = "2026-08-13T15:33:00.994Z"
SHAS = ["a", "b", "c"]
KEY = hashlib.sha256("\n".join(sorted(SHAS)).encode()).hexdigest()

class _Blob:
    def __init__(self, doc): self.doc = doc
    def download_blob(self):
        doc = self.doc
        class R:
            def readall(self_inner): return json.dumps(doc).encode()
        return R()

class _Svc:
    def __init__(self, manifest, record): self.m, self.r = manifest, record
    def get_blob_client(self, container, blob):
        return _Blob(self.m if "parser-manifests" in blob else self.r)

def _manifest(as_of):
    return {"artifacts": [{"content_sha256": s} for s in SHAS], "context": ({"as_of": as_of} if as_of else {})}

def _patch(monkeypatch, manifest, record):
    monkeypatch.setattr(ca, "_get_blob_service", lambda: _Svc(manifest, record))
    monkeypatch.setattr(ca, "_blob_container_and_path", lambda url: ("c", "deals/d/parser-manifests/x.json"))

def test_same_artifacts_same_scope_is_skipped(monkeypatch):
    _patch(monkeypatch, _manifest(CUT), {"artifact_key": KEY, "compile_id": "prior", "as_of": CUT})
    assert ca._unchanged_since_last_compile("d", "u") == "prior"

def test_full_request_after_a_cut_is_not_skipped(monkeypatch):
    _patch(monkeypatch, _manifest(None), {"artifact_key": KEY, "compile_id": "prior", "as_of": CUT})
    assert ca._unchanged_since_last_compile("d", "u") is None

def test_cut_request_after_a_full_is_not_skipped(monkeypatch):
    _patch(monkeypatch, _manifest(CUT), {"artifact_key": KEY, "compile_id": "prior", "as_of": None})
    assert ca._unchanged_since_last_compile("d", "u") is None

def test_legacy_record_without_as_of_matches_a_full_run_only(monkeypatch):
    _patch(monkeypatch, _manifest(None), {"artifact_key": KEY, "compile_id": "prior"})
    assert ca._unchanged_since_last_compile("d", "u") == "prior"
    _patch(monkeypatch, _manifest(CUT), {"artifact_key": KEY, "compile_id": "prior"})
    assert ca._unchanged_since_last_compile("d", "u") is None

def test_changed_artifacts_never_skip(monkeypatch):
    m = _manifest(CUT); m["artifacts"].append({"content_sha256": "NEW"})
    _patch(monkeypatch, m, {"artifact_key": KEY, "compile_id": "prior", "as_of": CUT})
    assert ca._unchanged_since_last_compile("d", "u") is None
