"""POST /v1/orbitbrief/rebuild-latest — compile deal manifest, write OrbitBrief envelope to Blob."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from parser_os_service.server import auth
from parser_os_service.server.blob_client import (
    infer_account_and_container_from_artifacts,
    read_manifest_json,
    download_blob_to_path,
    upload_json_blob,
)
from parser_os_service.server.projector import _finished_at_iso, to_scope_process_v1
from parser_os_service.server.routes.compile import (
    _attachments_status,
    _domain_pack_from_manifest,
    _hard_error_count,
    _packets_by_family_counts,
    _packets_by_severity_counts,
)

router = APIRouter(tags=["orbitbrief"])


class OrbitbriefRebuildBody(BaseModel):
    """Same manifest contract as POST /v1/compile; deal_id / compile_id are read from the manifest."""

    manifest_blob_url: str = Field(..., min_length=1)


def _orbitbrief_latest_envelope_blob_path(deal_id: str) -> str:
    return f"deals/{deal_id}/orbitbrief/latest/envelope.json"


def _run_compile_project(
    work: Path,
    compile_id: str,
    domain_pack: str | None,
):
    """Delegate to parser-os `compile_project` (patch in tests)."""
    from app.core.compiler import compile_project

    return compile_project(
        project_dir=work,
        project_id=compile_id,
        allow_errors=True,
        allow_unverified_receipts=True,
        persistence_hook=None,
        domain_pack=domain_pack,
        use_cache=False,
    )


def _build_envelope(work: Path, result: Any):
    """Delegate to `build_orbitbrief_envelope` (patch in tests)."""
    from app.core.orbitbrief_envelope import build_orbitbrief_envelope

    return build_orbitbrief_envelope(project_dir=work, compile_result=result)


@router.post("/v1/orbitbrief/rebuild-latest")
def orbitbrief_rebuild_latest_endpoint(
    body: OrbitbriefRebuildBody,
    _: None = Depends(auth.verify_bearer),
) -> dict[str, Any]:
    manifest = read_manifest_json(body.manifest_blob_url)
    deal_id = str(manifest.get("deal_id") or "").strip()
    compile_id = str(manifest.get("compile_id") or "").strip()
    if not deal_id or not compile_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="manifest must include deal_id and compile_id",
        )

    work = Path(f"/tmp/parser-os/orbitbrief-{compile_id}").resolve()
    art_dir = work / "artifacts"
    local_root = os.environ.get("PARSER_OS_SERVICE_LOCAL_BLOB_ROOT", "").strip()

    try:
        shutil.rmtree(work, ignore_errors=True)
        art_dir.mkdir(parents=True, exist_ok=True)

        arts = manifest.get("artifacts") or []
        if not isinstance(arts, list) or not arts:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="manifest has no artifacts")

        for row in arts:
            if not isinstance(row, dict) or not row.get("blob_url") or not row.get("filename"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid artifact row")
            dest = art_dir / str(row["filename"])
            download_blob_to_path(str(row["blob_url"]), dest)

        # Sidecar for envelope builder (CRM context, etc.)
        (work / ".parser_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        domain_pack = _domain_pack_from_manifest(manifest)
        from app.core.schemas import COMPILER_VERSION

        result = _run_compile_project(work, compile_id, domain_pack)
        result.compile_id = compile_id

        envelope = _build_envelope(work, result)
        rel = _orbitbrief_latest_envelope_blob_path(deal_id)

        if local_root:
            out_path = Path(local_root) / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
            envelope_blob_url = out_path.resolve().as_uri()
        else:
            account_url, container = infer_account_and_container_from_artifacts(manifest)
            envelope_blob_url = upload_json_blob(account_url, container, rel, envelope)

        summary = {
            "artifact_count": len(arts),
            "atom_count": len(result.atoms),
            "packet_count": len(result.packets),
            "hard_errors": _hard_error_count(result),
            "warnings": len([w for w in result.warnings if isinstance(w, str) and w.startswith("WARNING:")]),
            "packets_by_family": _packets_by_family_counts(result),
            "packets_by_severity": _packets_by_severity_counts(result),
            "parser_os_version": str(COMPILER_VERSION),
        }

        ctx = manifest.get("context")
        prior = None
        if isinstance(ctx, dict):
            prior = ctx.get("prior_scope_process_v1")
        scope_process_v1 = to_scope_process_v1(
            result,
            manifest,
            body.manifest_blob_url,
            prior if isinstance(prior, dict) else None,
        )
        er = scope_process_v1.setdefault("extractedReview", {})
        if isinstance(er, dict):
            er["lastOrbitBriefRunId"] = compile_id
            er["lastOrbitBriefRunAt"] = _finished_at_iso(result)

        return {
            "compile_id": compile_id,
            "deal_id": deal_id,
            "status": "succeeded",
            "parser_version": str(COMPILER_VERSION),
            "envelope_blob_url": envelope_blob_url,
            "envelope_blob_path": rel,
            "summary": summary,
            "attachments_status": _attachments_status(result, manifest),
            "scope_process_v1": scope_process_v1,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
