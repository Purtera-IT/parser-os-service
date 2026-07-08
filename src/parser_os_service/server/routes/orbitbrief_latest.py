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
    compile_options: dict[str, Any] | None = None,
):
    """Delegate to parser-os `compile_project` (patch in tests).

    Reads ``manifest.context.compile_options`` (passed as
    ``compile_options``) to override per-deal:

      compile_project args:
        allow_errors, allow_unverified_receipts, use_cache,
        domain_pack, abstain_threshold

      env-var-shaped LLM toggles (set on os.environ for this call so
      parser-os modules pick them up at LLM-call time):
        disable_site_llm        → SOWSMITH_SITE_LLM_DISABLE=1
        disable_multi_entity_llm → SOWSMITH_MULTI_ENTITY_DISABLE=1
        ollama_model            → OLLAMA_MODEL
        ollama_host             → OLLAMA_HOST
        llm_timeout_seconds     → SOWSMITH_LLM_TIMEOUT
        llm_parallel            → SOWSMITH_LLM_PARALLEL
        disable_ocr             → PARSER_OS_OCR_DISABLE=1

    Unknown keys are ignored (additive contract). Defaults preserve
    today's behavior, so the absence of compile_options is a no-op.
    """
    from app.core.compiler import compile_project

    opts = compile_options or {}

    # Apply env-var-shaped overrides for the duration of this compile.
    # We save+restore so concurrent compile requests don't bleed state.
    env_keys: dict[str, tuple[str, str | None]] = {}

    def _set_env(name: str, value: str) -> None:
        env_keys[name] = (name, os.environ.get(name))
        os.environ[name] = value

    if opts.get("disable_site_llm") is True:
        _set_env("SOWSMITH_SITE_LLM_DISABLE", "1")
    if opts.get("disable_multi_entity_llm") is True:
        _set_env("SOWSMITH_MULTI_ENTITY_DISABLE", "1")
    if opts.get("disable_ocr") is True:
        _set_env("PARSER_OS_OCR_DISABLE", "1")
    if isinstance(opts.get("ollama_model"), str) and opts["ollama_model"].strip():
        _set_env("OLLAMA_MODEL", opts["ollama_model"].strip())
    if isinstance(opts.get("ollama_host"), str) and opts["ollama_host"].strip():
        _set_env("OLLAMA_HOST", opts["ollama_host"].strip())
    if isinstance(opts.get("llm_timeout_seconds"), int):
        _set_env("SOWSMITH_LLM_TIMEOUT", str(int(opts["llm_timeout_seconds"])))
    if isinstance(opts.get("llm_parallel"), int):
        _set_env("SOWSMITH_LLM_PARALLEL", str(int(opts["llm_parallel"])))

    # Per-deal override for the chosen domain pack (else manifest-derived)
    effective_domain_pack = opts.get("domain_pack") or domain_pack

    try:
        return compile_project(
            project_dir=work,
            project_id=compile_id,
            allow_errors=bool(opts.get("allow_errors", True)),
            allow_unverified_receipts=bool(opts.get(
                "allow_unverified_receipts", True
            )),
            persistence_hook=None,
            domain_pack=effective_domain_pack,
            abstain_threshold=float(opts.get("abstain_threshold", 0.70)),
            use_cache=bool(opts.get("use_cache", False)),
        )
    finally:
        # Restore environment to pre-compile state
        for name, (_, prev) in env_keys.items():
            if prev is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prev


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

        from app.core.manifest_artifact_dedup import dedupe_manifest_email_artifacts

        arts = dedupe_manifest_email_artifacts(manifest.get("artifacts") or [])
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

        # Per-deal compile_options reader (additive — unknown keys
        # ignored, no compile_options = today's hardcoded defaults).
        # See contracts/DEVELOPER_INTEGRATION_PLAYBOOK.md §5.3 for
        # the supported key catalog.
        ctx_for_opts = manifest.get("context") or {}
        compile_options = ctx_for_opts.get("compile_options") if isinstance(ctx_for_opts, dict) else None
        result = _run_compile_project(
            work, compile_id, domain_pack,
            compile_options=compile_options if isinstance(compile_options, dict) else None,
        )
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
