"""BANG_IMPLEMENTATION_PLAN §4.4.4 — CompileResult → scope_process_v1 projection."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from importlib import resources
from typing import Any, cast

from app.core.schemas import (
    AuthorityClass,
    CompileResult,
    EvidenceAtom,
    EvidencePacket,
    PacketFamily,
    PacketStatus,
)

PROJECTOR_VERSION = "0.1.0"

# Shipped in scope_process_v1.example.json — must not persist on real compiles.
_FIXTURE_NOTES_MARKERS = (
    "Acme Corp multi-site Axis rollout",
    "North DC loading lanes",
    "reconcile quote vs roster before final labor load",
)

_BRIEF_SOURCE_HINTS = (
    "executive_brief",
    "deal_overview",
    "01_deal",
    "statement_of_work",
    "02_statement",
)

_SITE_NAME_NOISE = (
    "mock_msa",
    "hs_deal",
    "docx",
    "site_surveys",
    "pm et",
    "monday thursday",
    "contracting_procurement",
)


def _resource_scope_template() -> dict[str, Any]:
    root = resources.files("parser_os_service.data")
    raw = root.joinpath("scope_process_v1.example.json").read_text(encoding="utf-8")
    return cast(dict[str, Any], json.loads(raw))


def _deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        out: dict[str, Any] = dict(base)
        for k, v in overlay.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = cast(dict[str, Any], _deep_merge(out[k], v))
            else:
                out[k] = copy.deepcopy(v)
        return out
    return copy.deepcopy(overlay)


def _is_active(p: EvidencePacket) -> bool:
    return p.status in (PacketStatus.active, PacketStatus.needs_review)


def _packets_by_family(result: CompileResult, family: PacketFamily) -> list[EvidencePacket]:
    return [p for p in result.packets if p.family == family and _is_active(p)]


def _packets_by_severity(result: CompileResult, levels: tuple[str, ...]) -> list[EvidencePacket]:
    out: list[EvidencePacket] = []
    for p in result.packets:
        if not _is_active(p) or p.risk is None:
            continue
        if p.risk.severity in levels:
            out.append(p)
    return out


def _packet_to_row(p: EvidencePacket) -> dict[str, Any]:
    row: dict[str, Any] = {
        "packet_id": p.id,
        "family": p.family.value if hasattr(p.family, "value") else str(p.family),
        "status": p.status.value if hasattr(p.status, "value") else str(p.status),
        "anchor_key": p.anchor_key,
        "reason": p.reason,
        "confidence": p.confidence,
    }
    if p.risk is not None:
        row["risk"] = {
            "severity": p.risk.severity,
            "risk_score": p.risk.risk_score,
            "risk_reasons": list(p.risk.risk_reasons),
            "review_priority": p.risk.review_priority,
        }
    return row


def _governing_assumption_atoms(result: CompileResult) -> list[EvidenceAtom]:
    wanted = {AuthorityClass.contractual_scope, AuthorityClass.approved_site_roster}
    return [a for a in result.atoms if a.authority_class in wanted]


def _atom_anchor_key(atom: EvidenceAtom) -> str:
    val = atom.value or {}
    ni = val.get("normalized_item")
    if isinstance(ni, str) and ni.strip():
        return str(ni.strip())
    if atom.entity_keys:
        return ":".join(atom.entity_keys)
    return str(atom.id)


def _evidence_map(result: CompileResult) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for atom in result.atoms:
        key = _atom_anchor_key(atom)
        src = atom.source_refs[0] if atom.source_refs else None
        filename = src.filename if src is not None else "unknown"
        snippet = atom.normalized_text or atom.raw_text
        out.setdefault(key, []).append({"text": snippet[:2000], "source": filename})
    return out


def _reason_map(result: CompileResult) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in result.packets:
        pid = p.packet_id or p.id
        if p.certificate is not None:
            out[pid] = f"existence_reason: {p.certificate.existence_reason}"
    return out


def _confidence_block(result: CompileResult) -> dict[str, float]:
    if not result.atoms:
        return {"overall": 0.0, "atoms_weighted": 0.0, "receipts_verified_ratio": 0.0}
    conf_sum = sum(a.confidence for a in result.atoms)
    atoms_weighted = conf_sum / max(len(result.atoms), 1)
    total_rc = 0
    verified_rc = 0
    for a in result.atoms:
        for r in a.receipts:
            total_rc += 1
            if r.replay_status == "verified":
                verified_rc += 1
    ratio = verified_rc / total_rc if total_rc else 1.0
    overall = atoms_weighted * 0.7 + ratio * 0.3
    return {"overall": round(overall, 4), "atoms_weighted": round(atoms_weighted, 4), "receipts_verified_ratio": round(ratio, 4)}


def _site_list(result: CompileResult) -> list[dict[str, Any]]:
    sites = [e for e in result.entities if e.entity_type == "site"]
    site_access = _packets_by_family(result, PacketFamily.site_access)
    access_by_key: dict[str, list[str]] = {}
    for p in site_access:
        access_by_key.setdefault(p.anchor_key, []).append(p.reason)
    rows: list[dict[str, Any]] = []
    for s in sites:
        rows.append(
            {
                "name": s.canonical_name,
                "canonical_key": s.canonical_key,
                "access_notes": access_by_key.get(s.canonical_key, []),
            }
        )
    return rows


def _active_domains_from_packets(result: CompileResult) -> list[str]:
    domains: set[str] = set()
    mapping: dict[PacketFamily, str] = {
        PacketFamily.site_access: "site_operations",
        PacketFamily.scope_inclusion: "structured_cabling",
        PacketFamily.scope_exclusion: "structured_cabling",
        PacketFamily.quantity_conflict: "procurement",
        PacketFamily.vendor_mismatch: "procurement",
        PacketFamily.missing_info: "discovery",
        PacketFamily.meeting_decision: "collaboration",
        PacketFamily.action_item: "collaboration",
    }
    for p in result.packets:
        if _is_active(p):
            slug = mapping.get(p.family)
            if slug:
                domains.add(slug)
    return sorted(domains)


def _sow_readiness(result: CompileResult) -> dict[str, Any]:
    hard_errors = [w for w in result.warnings if isinstance(w, str) and w.startswith("ERROR:")]
    missing = _packets_by_family(result, PacketFamily.missing_info)
    blockers = [p.id for p in missing]
    strengths: list[str] = []
    for p in result.packets:
        if p.risk and p.risk.severity in ("low", "medium") and _is_active(p):
            strengths.append(f"{p.family.value}:{p.anchor_key}")
    return {
        "ready": len(hard_errors) == 0,
        "blockers": blockers,
        "strengths": strengths[:25],
    }


def _selected_artifacts(manifest: Mapping[str, Any]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {"calls": [], "emails": [], "docs": [], "notes": []}
    for a in manifest.get("artifacts", []) or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("attachment_id")
        if not aid:
            continue
        hint = str(a.get("modality_hint") or "").lower()
        fn = str(a.get("filename") or "").lower()
        if "email" in hint or fn.endswith(".eml"):
            buckets["emails"].append(str(aid))
        elif "transcript" in hint or fn.endswith((".vtt", ".srt")):
            buckets["calls"].append(str(aid))
        elif fn.endswith((".pdf", ".docx", ".xlsx", ".csv", ".txt")):
            buckets["docs"].append(str(aid))
        else:
            buckets["notes"].append(str(aid))
    return buckets


def _contradictions_packets(result: CompileResult) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in result.packets:
        if p.family not in (PacketFamily.quantity_conflict, PacketFamily.vendor_mismatch):
            continue
        if not _is_active(p):
            continue
        sev = p.risk.severity if p.risk else "medium"
        out.append(
            {
                "id": p.id,
                "family": p.family.value,
                "severity": sev,
                "confidence": p.confidence,
                "summary": p.reason,
            }
        )
    return out


def _crm_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    crm = manifest.get("crm")
    if isinstance(crm, dict):
        return crm
    ctx = manifest.get("context")
    if isinstance(ctx, dict) and isinstance(ctx.get("crm"), dict):
        return cast(dict[str, Any], ctx["crm"])
    return {}


def _normalize_note_sentence(text: str, *, max_len: int = 480) -> str:
    one = " ".join(text.split()).strip()
    if len(one) <= max_len:
        return one
    cut = one[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or one[: max_len - 1]).rstrip() + "…"


def _brief_atoms_text(result: CompileResult) -> list[str]:
    snippets: list[str] = []
    for atom in result.atoms:
        src = atom.source_refs[0] if atom.source_refs else None
        fn = (src.filename if src is not None else "").lower()
        if not any(h in fn for h in _BRIEF_SOURCE_HINTS):
            continue
        text = (atom.normalized_text or atom.raw_text or "").strip()
        if len(text) >= 48:
            snippets.append(text)
    return snippets


def _scope_inclusion_summaries(result: CompileResult, *, limit: int = 2) -> list[str]:
    out: list[str] = []
    for packet in _packets_by_family(result, PacketFamily.scope_inclusion):
        reason = (packet.reason or "").strip()
        if len(reason) >= 36:
            out.append(reason)
        if len(out) >= limit:
            break
    return out


def _site_names_for_notes(result: CompileResult, *, limit: int = 5) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for entity in result.entities:
        if entity.entity_type != "site":
            continue
        name = (entity.canonical_name or "").strip()
        if len(name) < 3 or len(name) > 56:
            continue
        key = name.lower()
        if key in seen:
            continue
        if any(noise in key for noise in _SITE_NAME_NOISE):
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= limit:
            break
    return names


def _project_needs_notes(result: CompileResult, manifest: Mapping[str, Any]) -> str:
    """Deal-specific synopsis for OrbitBrief — not the Bang fixture template."""

    crm = _crm_from_manifest(manifest)
    deal_name = str(crm.get("deal_name") or crm.get("name") or "").strip()
    account_name = str(crm.get("account_name") or crm.get("account") or "").strip()

    parts: list[str] = []
    if deal_name:
        lead = deal_name
        if account_name and account_name.lower() not in deal_name.lower():
            lead = f"{account_name} — {deal_name}"
        parts.append(_normalize_note_sentence(lead, max_len=160))

    brief_snippets = _brief_atoms_text(result)
    if brief_snippets:
        # Prefer narrative scope sentences over SKU tables / contact blocks.
        narrative = [
            s
            for s in brief_snippets
            if any(
                token in s.lower()
                for token in (
                    "refresh",
                    "rollout",
                    "moderniz",
                    "deploy",
                    "install",
                    "site",
                    "office",
                    "scope",
                    "project",
                )
            )
            and "units x $" not in s.lower()
            and "@" not in s[:40]
        ]
        pool = narrative if narrative else brief_snippets
        parts.append(_normalize_note_sentence(max(pool, key=len)))
    else:
        for summary in _scope_inclusion_summaries(result):
            parts.append(_normalize_note_sentence(summary))
            if sum(len(p) for p in parts) > 420:
                break

    sites = _site_names_for_notes(result)
    if sites:
        parts.append(f"Sites in scope: {', '.join(sites)}.")

    notes = " ".join(parts).strip()
    if not notes:
        notes = (
            "Scope compiled from discovery artifacts; "
            "review blockers in the action inbox before release."
        )
    return notes[:2000]


def _finished_at_iso(result: CompileResult) -> str:
    if result.manifest and result.manifest.completed_at:
        return str(result.manifest.completed_at)
    if result.trace and result.trace.stages:
        return str(result.trace.stages[-1].started_at)
    return "1970-01-01T00:00:00Z"


def _dict_slot(container: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``container[key]`` as a dict, replacing a non-dict value in place.

    ``scope`` is the resource-scope template deep-merged with the PRIOR
    scope_process_v1 the manifest carries. A prior legitimately stores
    ``"sowHandoff": null`` for a deal that has not reached SOW yet, and
    ``setdefault`` hands that ``None`` straight back because the key exists.
    Asserting on it aborted the entire compile at the projection step — after
    every parse, enrich, graph and packet stage had already run (~40 min of
    work thrown away, and no envelope written).

    A null slot means "no prior content for this section", which is exactly
    what an empty dict expresses. Start fresh and carry on.
    """
    value = container.get(key)
    if not isinstance(value, dict):
        value = {}
        container[key] = value
    return value


def to_scope_process_v1(
    result: CompileResult,
    manifest: Mapping[str, Any],
    manifest_blob_url: str,
    prior: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """§4.4.4 mapping — returns full ``scope_process_v1`` object (template + overlays)."""

    scope = copy.deepcopy(_resource_scope_template())
    prior_dict = dict(prior) if prior else {}
    ctx = manifest.get("context")
    if isinstance(ctx, dict):
        psp = ctx.get("prior_scope_process_v1")
        if isinstance(psp, dict) and not prior_dict:
            prior_dict = copy.deepcopy(psp)

    if prior_dict:
        scope = cast(dict[str, Any], _deep_merge(scope, prior_dict))

    finished = _finished_at_iso(result)
    scope["version"] = "scope_process_v1"
    scope["lastIngestAt"] = finished
    scope["lastIngestSource"] = "bang-compile"

    sh = _dict_slot(scope, "sowHandoff")
    sh["scope_in"] = [_packet_to_row(p) for p in _packets_by_family(result, PacketFamily.scope_inclusion)]
    sh["scope_out"] = [_packet_to_row(p) for p in _packets_by_family(result, PacketFamily.scope_exclusion)]
    sh["assumptions"] = [
        {"atom_id": a.id, "text": a.normalized_text or a.raw_text, "authority_class": a.authority_class.value}
        for a in _governing_assumption_atoms(result)
    ]
    sh["risks"] = [_packet_to_row(p) for p in _packets_by_severity(result, ("high", "critical"))]
    sh["open_questions"] = [_packet_to_row(p) for p in _packets_by_family(result, PacketFamily.missing_info)]
    sh["decisions"] = [_packet_to_row(p) for p in _packets_by_family(result, PacketFamily.meeting_decision)]
    sh["action_items"] = [_packet_to_row(p) for p in _packets_by_family(result, PacketFamily.action_item)]

    pn = _dict_slot(scope, "projectNeeds")
    pn["active_domains"] = _active_domains_from_packets(result)
    pn["site_list"] = _site_list(result)
    pn["notes"] = _project_needs_notes(result, manifest)

    scope["sowReadiness"] = _sow_readiness(result)

    er = _dict_slot(scope, "extractedReview")
    er["contradictions"] = _contradictions_packets(result)

    oa = _dict_slot(scope, "orbitbriefAudit")
    oa["evidenceMap"] = _evidence_map(result)
    oa["confidence"] = _confidence_block(result)
    oa["missing"] = [p.reason for p in _packets_by_family(result, PacketFamily.missing_info)]
    oa["reasonMap"] = _reason_map(result)
    archive = _dict_slot(oa, "artifactArchive")
    archive["manifestBlobUrl"] = manifest_blob_url

    scope["selectedArtifacts"] = _selected_artifacts(manifest)

    # OrbitBrief v2: carry human scope decisions (`resolutions[]`) forward across
    # recompiles. These are frontend-authored, append-only UI state (PATCHed onto
    # `scope_process_v1.resolutions`), not compile output, so a fresh projection
    # must never discard them. The generic prior deep-merge above already carries
    # unknown prior-only keys, but we set this explicitly so the contract holds
    # regardless of future template/merge changes.
    prior_resolutions = prior_dict.get("resolutions")
    if isinstance(prior_resolutions, list):
        scope["resolutions"] = copy.deepcopy(prior_resolutions)

    return scope


def mapping_subset(scope: Mapping[str, Any]) -> dict[str, Any]:
    """Stable subset for regression tests (§4.4.4 rows)."""

    sh = scope.get("sowHandoff")
    assert isinstance(sh, dict)
    oa = scope.get("orbitbriefAudit")
    assert isinstance(oa, dict)
    return {
        "version": scope.get("version"),
        "lastIngestSource": scope.get("lastIngestSource"),
        "sowHandoff.scope_in": sh.get("scope_in"),
        "sowHandoff.scope_out": sh.get("scope_out"),
        "sowHandoff.assumptions": sh.get("assumptions"),
        "sowHandoff.risks": sh.get("risks"),
        "sowHandoff.open_questions": sh.get("open_questions"),
        "sowHandoff.decisions": sh.get("decisions"),
        "sowHandoff.action_items": sh.get("action_items"),
        "projectNeeds.active_domains": (scope.get("projectNeeds") or {}).get("active_domains"),
        "projectNeeds.notes": (scope.get("projectNeeds") or {}).get("notes"),
        "projectNeeds.site_list": (scope.get("projectNeeds") or {}).get("site_list"),
        "sowReadiness": scope.get("sowReadiness"),
        "extractedReview.contradictions": (scope.get("extractedReview") or {}).get("contradictions"),
        "orbitbriefAudit.evidenceMap": oa.get("evidenceMap"),
        "orbitbriefAudit.confidence": oa.get("confidence"),
        "orbitbriefAudit.missing": oa.get("missing"),
        "orbitbriefAudit.reasonMap": oa.get("reasonMap"),
        "orbitbriefAudit.artifactArchive.manifestBlobUrl": (oa.get("artifactArchive") or {}).get(
            "manifestBlobUrl"
        ),
        "selectedArtifacts": scope.get("selectedArtifacts"),
    }
