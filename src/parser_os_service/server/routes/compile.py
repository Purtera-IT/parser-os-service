"""POST /v1/compile — BANG plan §4.4.2 body, §4.4.3 flow, §4.7 response."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, cast

from app.core.schemas import COMPILER_VERSION, EvidencePacket
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from parser_os_service.server import auth
from parser_os_service.server.blob_client import (
    build_run_blob_path,
    infer_account_and_container_from_artifacts,
    read_manifest_json,
    download_blob_to_path,
    upload_json_blob,
)
from parser_os_service.server.projector import to_scope_process_v1

router = APIRouter(tags=["compile"])


class CompileBody(BaseModel):
    compile_id: str = Field(..., min_length=1)
    deal_id: str = Field(..., min_length=1)
    manifest_blob_url: str = Field(..., min_length=1)
    callback_url: str | None = None


def _domain_pack_from_manifest(manifest: dict[str, Any]) -> str | None:
    if "domain_pack" in manifest and manifest["domain_pack"]:
        return str(manifest["domain_pack"])
    ctx = manifest.get("context")
    if isinstance(ctx, dict) and ctx.get("domain_pack"):
        return str(ctx["domain_pack"])
    return None


def _artifact_id_for_filename(result: Any, filename: str) -> str | None:
    if result.manifest is None:
        return None
    normalized = str(filename or "").replace("\\", "/").lstrip("/")
    candidates = {normalized}
    if normalized and not normalized.startswith("artifacts/"):
        candidates.add(f"artifacts/{normalized}")
    for fp in result.manifest.artifact_fingerprints:
        fp_filename = str(fp.filename or "").replace("\\", "/").lstrip("/")
        if fp_filename in candidates:
            return str(fp.artifact_id)
    return None


_ARTIFACT_PARSE_ERROR_PREFIX = "artifact_parse_error:"


def _artifact_parse_errors_from_warnings(result: Any) -> dict[str, str]:
    """Map compiler artifact_id → error code from tabular parser warnings."""
    out: dict[str, str] = {}
    for warning in getattr(result, "warnings", None) or []:
        if not isinstance(warning, str) or not warning.startswith(_ARTIFACT_PARSE_ERROR_PREFIX):
            continue
        payload = warning[len(_ARTIFACT_PARSE_ERROR_PREFIX) :]
        artifact_id, _, err = payload.partition(":")
        if artifact_id and err:
            out[artifact_id] = err
    return out


def _attachments_status(result: Any, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    arts = manifest.get("artifacts") or []
    if not isinstance(arts, list):
        return out
    parse_errors = _artifact_parse_errors_from_warnings(result)
    for row in arts:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("attachment_id") or "")
        fn = str(row.get("filename") or "")
        art_id = _artifact_id_for_filename(result, fn)
        atoms_count = 0
        receipts_total = 0
        receipts_verified = 0
        if art_id:
            for atom in result.atoms:
                if atom.artifact_id == art_id:
                    atoms_count += 1
                    receipts_total += len(atom.receipts)
                    receipts_verified += sum(1 for r in atom.receipts if r.replay_status == "verified")
        err = None
        if not fn:
            parse_status = "failed"
            err = "missing filename"
        elif art_id and art_id in parse_errors:
            parse_status = "failed"
            err = parse_errors[art_id]
        elif atoms_count > 0:
            parse_status = "parsed"
        else:
            parse_status = "intake_only"
        out[aid] = {
            "parse_status": parse_status,
            "atoms_count": atoms_count,
            "receipts_verified": receipts_verified,
            "receipts_total": receipts_total,
            "error": err,
        }
    return out


def _packets_response_list(
    result: Any,
    deal_id: str,
    compile_id: str,
    account_url: str,
    container: str,
    *,
    local_cert_dir: Path | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in cast(list[EvidencePacket], result.packets):
        cert_url: str | None = None
        if p.certificate is not None:
            if local_cert_dir is not None:
                cpath = local_cert_dir / f"{p.id}.json"
                cert_url = cpath.as_uri() if cpath.is_file() else None
            else:
                cert_path = build_run_blob_path(deal_id, compile_id, "certificates", f"{p.id}.json")
                cert_url = f"{account_url}/{container}/{cert_path}"
        rs = p.risk.risk_score if p.risk else 0.0
        sev = p.risk.severity if p.risk else "low"
        rp = p.risk.review_priority if p.risk else 1
        rows.append(
            {
                "packet_id": p.id,
                "family": p.family.value,
                "status": p.status.value,
                "severity": sev,
                "risk_score": rs,
                "review_priority": rp,
                "anchor_summary": p.reason,
                "evidence_completeness": p.confidence,
                "ambiguity_score": 1.0 - p.confidence,
                "certificate_blob_url": cert_url,
            }
        )
    return rows


def _packets_by_family_counts(result: Any) -> dict[str, int]:
    c: dict[str, int] = {}
    for p in result.packets:
        k = p.family.value if hasattr(p.family, "value") else str(p.family)
        c[k] = c.get(k, 0) + 1
    return c


def _packets_by_severity_counts(result: Any) -> dict[str, int]:
    c = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for p in result.packets:
        if p.risk is None:
            c["low"] += 1
            continue
        sev = p.risk.severity
        if sev in c:
            c[sev] += 1
        else:
            c["low"] += 1
    return c


def _hard_error_count(result: Any) -> int:
    return sum(1 for w in result.warnings if isinstance(w, str) and w.startswith("ERROR:"))


@router.post("/v1/compile")
def compile_endpoint(
    body: CompileBody,
    _: None = Depends(auth.verify_bearer),
) -> dict[str, Any]:
    manifest = read_manifest_json(body.manifest_blob_url)
    deal_id = str(manifest.get("deal_id") or body.deal_id)
    compile_id = str(manifest.get("compile_id") or body.compile_id)
    prior = None
    ctx = manifest.get("context")
    if isinstance(ctx, dict):
        prior = ctx.get("prior_scope_process_v1")

    work = Path(f"/tmp/parser-os/{compile_id}").resolve()
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

        domain_pack = _domain_pack_from_manifest(manifest)
        from app.core.compiler import compile_project

        result = compile_project(
            project_dir=work,
            project_id=compile_id,
            allow_errors=True,
            allow_unverified_receipts=True,
            persistence_hook=None,
            domain_pack=domain_pack,
            use_cache=False,
        )
        result.compile_id = compile_id

        from app.core.manifest import compute_output_signature

        sig = compute_output_signature(result)
        dumped = json.loads(result.model_dump_json())

        if local_root:
            out_dir = Path(local_root) / deal_id / compile_id
            out_dir.mkdir(parents=True, exist_ok=True)
            result_path = out_dir / "result.json"
            result_path.write_text(json.dumps(dumped, indent=2), encoding="utf-8")
            result_blob_url = result_path.resolve().as_uri()
            account_url = "https://local.invalid"
            container = "local"
            cert_base = out_dir / "certificates"
            cert_base.mkdir(exist_ok=True)
            for p in result.packets:
                if p.certificate is None:
                    continue
                cpath = cert_base / f"{p.id}.json"
                cpath.write_text(p.certificate.model_dump_json(indent=2), encoding="utf-8")
            packets = _packets_response_list(
                result, deal_id, compile_id, account_url, container, local_cert_dir=cert_base
            )
        else:
            account_url, container = infer_account_and_container_from_artifacts(manifest)
            blob_path = build_run_blob_path(deal_id, compile_id, "result.json")
            result_blob_url = upload_json_blob(account_url, container, blob_path, dumped)
            for p in result.packets:
                if p.certificate is None:
                    continue
                cert_path = build_run_blob_path(deal_id, compile_id, "certificates", f"{p.id}.json")
                upload_json_blob(
                    account_url,
                    container,
                    cert_path,
                    p.certificate.model_dump(mode="json"),
                )
            packets = _packets_response_list(result, deal_id, compile_id, account_url, container)

        scope = to_scope_process_v1(result, manifest, body.manifest_blob_url, cast(dict[str, Any] | None, prior))

        summary = {
            "artifact_count": len(arts),
            "atom_count": len(result.atoms),
            "packet_count": len(result.packets),
            "hard_errors": _hard_error_count(result),
            "warnings": len([w for w in result.warnings if w.startswith("WARNING:")]),
            "packets_by_family": _packets_by_family_counts(result),
            "packets_by_severity": _packets_by_severity_counts(result),
            "parser_os_version": str(COMPILER_VERSION),
        }

        return {
            "compile_id": compile_id,
            "status": "succeeded",
            "output_signature": sig,
            "parser_version": str(COMPILER_VERSION),
            "bang_version": str(COMPILER_VERSION),
            "result_blob_url": result_blob_url,
            "summary": summary,
            "attachments_status": _attachments_status(result, manifest),
            "packets": packets,
            "scope_process_v1": scope,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
