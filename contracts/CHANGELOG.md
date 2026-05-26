# Contract changelog

Record every change that affects **Purpulse Platform, SPA, or blob/HTTP contracts**.
Internal parser refactors with no output shape change do **not** need an entry.

## Format

```markdown
## YYYY-MM-DD — [additive | breaking] — short title

**Schemas:** orbitbrief.input.v2 | rebuild-latest.response.v1 | parser-manifest.v1 | PM_HANDOFF | other

**Author:** @handle

**Summary:** One sentence.

**Details:**
- Added optional envelope key `foo.bar`
- OR: Renamed `documents` → … (breaking — requires v3)

**Purpulse action required:** none | update UI | coordinated deploy
```

---

## Entries

<!-- Developer: add newest entries at the top -->

### 2026-05-27c — additive — parser-os-service + SowSmith verified end-to-end

**Schemas:** DEVELOPER_INTEGRATION_PLAYBOOK.md §2.1, §2.4, §5.1 (added service env vars), §5.3 (compile_options state), §7.2 (full scope_process_v1 paths), §8 (master functions)

**Author:** @lilli (parser-os)

**Summary:** Cloned Purtera-IT/parser-os-service + Purtera-IT/SowSmith and verified every contract-relevant code path. §7.2 `scope_process_v1` paths are now fully VERIFIED against `projector.to_scope_process_v1` source; §2.1 rebuild-latest behavior is documented down to the hardcoded `compile_project` arg defaults; SowSmith API surface is documented to the function signature.

**Details:**
- **rebuild-latest route** at `parser-os-service/src/parser_os_service/server/routes/orbitbrief_latest.py`: hardcoded `allow_errors=True, allow_unverified_receipts=True, use_cache=False, domain_pack=_domain_pack_from_manifest(manifest), persistence_hook=None`. Reads `manifest.domain_pack` → `manifest.context.domain_pack` → None (auto-route). Writes `.parser_manifest.json` sidecar for envelope builder. Overrides `extractedReview.lastOrbitBriefRunId/At` after projection.
- **No `compile_options` reader** exists in parser-os-service. Proposed compile_options keys in §5.3 remain WORK ITEM. Wiring belongs in `_run_compile_project` and is `contract:additive` once shipped.
- **scope_process_v1 paths** (VERIFIED): `version`, `lastIngestAt`, `lastIngestSource`, `sowHandoff.{scope_in,scope_out,assumptions,risks,open_questions,decisions,action_items}`, `projectNeeds.{active_domains,site_list,notes}`, `sowReadiness`, `extractedReview.{contradictions,lastOrbitBriefRunId,lastOrbitBriefRunAt}`, `orbitbriefAudit.{evidenceMap,confidence,missing,reasonMap,artifactArchive.manifestBlobUrl}`, `selectedArtifacts`. Pre-existing Platform/SPA fields (`currentStep`, `fieldStateMap`, `generationHistory`, etc.) survive only via the `prior` deep-merge from `manifest.context.prior_scope_process_v1`.
- **Regression-test subset** from `projector.mapping_subset(scope)` listed in §7.2 — these are the keys parser-os-service asserts stability on; renaming any is `contract:breaking`.
- **parser-os-service env vars** (now documented in §5.1): `BANG_INTERNAL_BEARER` (auth), `DATABASE_URL` (Postgres), `PARSER_OS_SERVICE_LOCAL_BLOB_ROOT` (dev mode — local filesystem instead of Blob).
- **SowSmith API surface** (verified): single public function `build_sow_markdown(envelope: dict) -> str` and constant `SOW_VERSION = "sowsmith_v1"`. CLI `sowsmith render <envelope.json> [--out path]`. Repo: Purtera-IT/SowSmith, public, pip-installable, optional dep in the parser-os shim.

**Purpulse action required:** none — purely documentation completion. All previously-marked Open Questions are now closed.

---

### 2026-05-27b — additive — PM_HANDOFF.json field catalog filled (54 fields)

**Schemas:** DEVELOPER_INTEGRATION_PLAYBOOK.md §7.3

**Author:** @lilli (parser-os)

**Summary:** Replaced the placeholder PM_HANDOFF table with a definitive 54-field catalog grouped by UI surface (Header, Scorecard, Intake quality, Money, Sites, Stakeholders, Schedule, Risks, Compliance, Scope, Action queue, Reconciliation, Deliverables, Strategic, Provenance, Telemetry, Quality). Source of truth: `Orbitbrief-Core/src/orbitbrief_core/pm_handoff/models.py` (PMHandoff dataclass) + `parser-os/OUTPUTS_FOR_UI.md` (729-line spec with real OPTBOT values for every field).

**Purpulse action required:** none — frontend engineer has the complete field list with types + required flags + UI hints.

**Open items remaining:**
- parser-os-service repo not on local disk — cannot verify `parser_os_service.server.projector.to_scope_process_v1` paths or the `manifest.context.compile_options` reader. Action: clone parser-os-service locally or zip + share so we can mark §7.2 rows as Verified and confirm the compile_options wiring.

---

### 2026-05-27 — additive — developer §5–8 inventory + multi-entity LLM upgrades documented

**Schemas:** DEVELOPER_INTEGRATION_PLAYBOOK.md, parser-manifest.v1.yaml, orbitbrief.input.v2.yaml

**Author:** @lilli (parser-os)

**Summary:** Filled in §5 (real env vars + compile_project args + proposed compile_options keys), §6 (real `compile_project` stage order + 13-stage telemetry + LLM hot paths inside enrich_entities + Orbitbrief-Core run), §7 (entity-content upgrades — no new top-level keys), and §8 (master functions map). Synced parser-manifest.v1.yaml `reserved_keys` to match. Documented this week's site + multi-entity LLM architecture and the envelope migration from parser-os → Orbitbrief-Core.

**Details:**
- **Env vars documented (parser-only):** `OLLAMA_HOST`, `OLLAMA_MODEL`, `SOWSMITH_LLM_TIMEOUT`, `SOWSMITH_LLM_PARALLEL`, `SOWSMITH_SITE_LLM_DISABLE`, `SOWSMITH_SITE_LLM_VERIFY` (legacy alias), `SOWSMITH_MULTI_ENTITY_DISABLE`, `PARSER_OS_OCR_DISABLE` and PDF/OCR-knob siblings.
- **Env vars documented (orbitbrief-only):** `ORBITBRIEF_LEARNING_LEDGER`, `ORBITBRIEF_CORPUS_HISTORY`, `ORBITBRIEF_POLISH_CACHE`, `ORBITBRIEF_FX_API_URL`, `ORBITBRIEF_FX_DISABLE`, `OLLAMA_BASE_URL`, `OLLAMA_NUM_PARALLEL`, `ORBITBRIEF_POLISH_MODEL`, `PARSER_OS_ROOT`.
- **No new top-level envelope keys** — `required_top_level` + `optional_top_level` lists unchanged. Content of `entities[]` is now LLM-first for `customer`, `stakeholder`, `milestone`, `requirement` (site already was). Field-label / org-token / jargon hygiene drops regex noise; LLM-trumps-regex when multi-entity LLM ran.
- **Envelope builder location:** moved from `parser-os/app/core/orbitbrief_envelope.py` → `Orbitbrief-Core/src/orbitbrief_core/envelope.py`. parser-os retains a deprecation shim. Analytical-surface builders (pm_dashboard, scope_truth, project_vitals, …) moved alongside to `Orbitbrief-Core/src/orbitbrief_core/envelope_builders.py`.
- **Proposed `manifest.context.compile_options` keys** (additive, default = no change): `domain_pack`, `allow_errors`, `allow_unverified_receipts`, `use_cache`, `abstain_threshold`, `disable_site_llm`, `disable_multi_entity_llm`, `ollama_model`, `ollama_host`, `llm_timeout_seconds`, `llm_parallel`, `disable_ocr`. Wiring lives in parser-os-service (which I couldn't inspect from this checkout — see Open Questions).

**Purpulse action required:** none — purely documentation + content upgrades to existing envelope shape. UI continues to work without code change; richer entity output flows through `scope_process_v1` automatically.

---

### 2026-05-26 — additive — contracts folder created

**Schemas:** all v1 baselines in `contracts/`

**Author:** Purpulse platform

**Summary:** Initial cross-repo contract docs and YAML baselines from production code paths.

**Purpulse action required:** none (documentation only)
