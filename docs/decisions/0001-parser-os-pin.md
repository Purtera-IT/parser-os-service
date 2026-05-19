# ADR 0001 — Parser OS dependency pin

**Status:** Proposed (Phase 0)

**Decision:** `parser-os-service` installs `purtera-evidence-mvp` from `Purtera-IT/parser-os` at Git ref:

- **Current placeholder:** `main` (see root `pyproject.toml`)
- **Target:** immutable **tag** once Parser OS release tagging is finalized

**Rationale:** Reproducible compiles and auditable diffs for SOC 2.

**Follow-up:** Update `pyproject.toml` and the Docker build to use the same ref; record the tag in release notes.
