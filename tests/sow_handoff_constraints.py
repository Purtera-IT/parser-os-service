"""Subset of PurPulse ``sowHandoffSchema`` / ``validateSOWHandoff`` (contracts.ts).

Full validation runs in TypeScript (Zod). These checks catch structural regressions in CI.
"""

from __future__ import annotations

import uuid
from typing import Any


def _uuid_ok(value: str | None) -> bool:
    if value is None:
        return True
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def validate_sow_handoff_contract(handoff: dict[str, Any]) -> None:
    """Raise AssertionError if ``handoff`` violates SOWHandoff contract rules."""

    required = (
        "id",
        "version",
        "requirements_version",
        "policy_pack_hash",
        "field_catalog_hash",
        "active_domains",
        "global_fields",
        "site_registry",
        "site_fields",
        "clause_choices",
        "evidence_map_refs",
        "constraints_state",
        "readiness_metadata",
        "review_audit_trail",
        "contract_provenance",
        "compiled_at",
        "compiled_by",
    )
    for key in required:
        _assert(key in handoff, f"missing required key: {key}")

    _assert(handoff["version"] == "sow_handoff_v1", "version must be sow_handoff_v1")
    _assert(_uuid_ok(str(handoff["id"])), "id must be UUID-shaped")

    cp = handoff["contract_provenance"]
    _assert(isinstance(cp, dict), "contract_provenance must be object")
    _assert(
        cp.get("requirements_version") == handoff["requirements_version"],
        "contract_provenance.requirements_version must match requirements_version",
    )
    _assert(
        cp.get("policy_pack_hash") == handoff["policy_pack_hash"],
        "contract_provenance.policy_pack_hash must match policy_pack_hash",
    )
    _assert(
        cp.get("field_catalog_hash") == handoff["field_catalog_hash"],
        "contract_provenance.field_catalog_hash must match field_catalog_hash",
    )

    sid = cp.get("scope_snapshot_id")
    mid = cp.get("model_run_id")
    _assert(_uuid_ok(str(sid)) if sid is not None else True, "scope_snapshot_id must be UUID or null")
    _assert(_uuid_ok(str(mid)) if mid is not None else True, "model_run_id must be UUID or null")

    cs = handoff["constraints_state"]
    _assert(isinstance(cs, dict), "constraints_state must be object")
    _assert("violations" in cs and isinstance(cs["violations"], list), "constraints_state.violations")
    _assert("allPassed" in cs and isinstance(cs["allPassed"], bool), "constraints_state.allPassed")

    rm = handoff["readiness_metadata"]
    _assert(isinstance(rm, dict), "readiness_metadata must be object")
    sc = rm.get("siteCompleteness")
    ea = rm.get("evidenceAdequacy")
    _assert(isinstance(sc, dict) and isinstance(ea, dict), "readiness_metadata nested")

    em = handoff["evidence_map_refs"]
    _assert(isinstance(em, dict), "evidence_map_refs must be object")
    for field_id, refs in em.items():
        _assert(isinstance(field_id, str) and field_id, "evidence_map_refs keys must be non-empty strings")
        _assert(isinstance(refs, list), "evidence_map_refs values must be arrays")
        for ref in refs:
            _assert(isinstance(ref, dict), "each evidence ref must be object")
            _assert("text" in ref and "source" in ref, "evidence ref needs text and source")
