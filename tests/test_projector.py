from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

from app.core.schemas import (
    AnchorSignature,
    AuthorityClass,
    CompileResult,
    EvidenceAtom,
    EvidencePacket,
    PacketFamily,
    PacketRisk,
    PacketStatus,
    ReviewStatus,
    SourceRef,
    ArtifactType,
    AtomType,
)

from parser_os_service.server.projector import mapping_subset, to_scope_process_v1
from tests.sow_handoff_constraints import validate_sow_handoff_contract


def _atom(aid: str, authority: AuthorityClass) -> EvidenceAtom:
    return EvidenceAtom(
        id=aid,
        project_id="p1",
        artifact_id="art1",
        atom_type=AtomType.quantity,
        raw_text="t",
        normalized_text="t",
        value={"normalized_item": "cam:axis"},
        entity_keys=["site:a"],
        source_refs=[
            SourceRef(
                id="sr1",
                artifact_id="art1",
                artifact_type=ArtifactType.xlsx,
                filename="roster.xlsx",
                locator={},
                extraction_method="test",
                parser_version="1",
            )
        ],
        receipts=[],
        authority_class=authority,
        confidence=0.9,
        review_status=ReviewStatus.auto_accepted,
        review_flags=[],
        parser_version="1",
    )


def _packet(
    pid: str,
    family: PacketFamily,
    *,
    severity: str = "high",
    anchor_key: str = "material:cat6",
) -> EvidencePacket:
    return EvidencePacket(
        id=pid,
        project_id="p1",
        family=family,
        anchor_type="material",
        anchor_key=anchor_key,
        governing_atom_ids=["a1"],
        supporting_atom_ids=[],
        contradicting_atom_ids=[],
        related_edge_ids=[],
        confidence=0.85,
        status=PacketStatus.active,
        reason="test packet",
        review_flags=[],
        anchor_signature=AnchorSignature(
            anchor_type="material",
            canonical_key=anchor_key,
            entity_keys=["e1"],
            normalized_topic="t",
            hash="testhash0000000000000000000000000000000000000000000000000000",
        ),
        certificate=None,
        risk=PacketRisk(
            risk_score=0.8,
            severity=cast(Literal["low", "medium", "high", "critical"], severity),
            risk_reasons=["r1"],
            review_priority=2,
        ),
    )


def test_mapping_rows_from_synthetic_compile_result() -> None:
    atoms = [
        _atom("a1", AuthorityClass.contractual_scope),
        _atom("a2", AuthorityClass.approved_site_roster),
    ]
    packets = [
        _packet("p1", PacketFamily.scope_inclusion, severity="low", anchor_key="site:hq"),
        _packet("p2", PacketFamily.scope_exclusion, severity="medium", anchor_key="site:hq"),
        _packet("p3", PacketFamily.quantity_conflict, severity="critical", anchor_key="material:cam"),
        _packet("p4", PacketFamily.vendor_mismatch, severity="high", anchor_key="material:cam"),
        _packet("p5", PacketFamily.missing_info, severity="low", anchor_key="site:hq"),
        _packet("p6", PacketFamily.meeting_decision, severity="low", anchor_key="site:hq"),
        _packet("p7", PacketFamily.action_item, severity="low", anchor_key="site:hq"),
    ]
    cr = CompileResult(project_id="p1", atoms=atoms, entities=[], edges=[], packets=packets)
    manifest: dict[str, Any] = {
        "artifacts": [
            {"attachment_id": "x1", "filename": "a.xlsx", "blob_url": "https://example.blob.core.windows.net/c/x"},
        ]
    }
    scope = to_scope_process_v1(cr, manifest, "https://example.blob.core.windows.net/c/manifest.json")
    sub = mapping_subset(scope)
    assert sub["version"] == "scope_process_v1"
    assert sub["lastIngestSource"] == "bang-compile"
    assert len(sub["sowHandoff.scope_in"] or []) == 1
    assert len(sub["sowHandoff.scope_out"] or []) == 1
    assert len(sub["sowHandoff.assumptions"] or []) == 2
    assert len(sub["sowHandoff.risks"] or []) >= 1
    assert len(sub["sowHandoff.open_questions"] or []) == 1
    assert len(sub["extractedReview.contradictions"] or []) == 2


def test_sow_handoff_contract_after_projector_with_prior(scope_fixture_path: Path) -> None:
    template = json.loads(scope_fixture_path.read_text(encoding="utf-8"))
    atoms = [_atom("a1", AuthorityClass.contractual_scope)]
    packets = [_packet("p1", PacketFamily.scope_inclusion, severity="low", anchor_key="site:hq")]
    cr = CompileResult(project_id="p", atoms=atoms, entities=[], edges=[], packets=packets)
    manifest: dict[str, Any] = {
        "artifacts": [
            {
                "attachment_id": "x1",
                "filename": "a.xlsx",
                "blob_url": "https://example.blob.core.windows.net/c/a.xlsx",
            }
        ]
    }
    got = to_scope_process_v1(cr, manifest, "https://example.blob.core.windows.net/c/manifest.json", prior=template)
    validate_sow_handoff_contract(got["sowHandoff"])
