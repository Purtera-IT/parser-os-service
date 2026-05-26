# Developer integration playbook

**Audience:** Parser-os / Orbitbrief-Core / SowSmith engineer  
**Purpose:** Single place to document what you use today, what Purpulse depends on, and how to change flags/fields without blocking the platform team.

**How to use this doc**

1. Read §1–4 once (rules + stable boundaries — already filled from repo).
2. Fill in §5–9 (your inventory). Update whenever you add flags, pipeline steps, or UI-facing fields.
3. On integration-impacting PRs: update the matching YAML in `contracts/` + add a line to `contracts/CHANGELOG.md`.
4. Tag PRs: `contract:additive` (Purpulse can ignore) or `contract:breaking` (requires Purpulse review).

---

## 1. Architecture (three rings)

| Ring | Who changes freely | Purpulse cares? |
|------|-------------------|-----------------|
| **A — Internal** | You | No — function names, file layout, step order inside `compile_project` |
| **B — Service knobs** | You deploy | Rarely — env vars, `manifest.context.compile_options` |
| **C — Integration contract** | You + Purpulse | **Yes** — HTTP routes, blob paths, envelope keys, `scope_process_v1` paths UI reads |

**Golden rule:** Purpulse calls **master entrypoints** and reads **JSON artifacts**. It does not import your Python modules.

```text
  Purpulse                         Your repos
  ────────                         ──────────
  Queue / SPA  ──HTTP──►  parser-os-service
                │              └─► compile_project (internal)
                │              └─► build_orbitbrief_envelope (internal)
                ◄── JSON ──  envelope.json + HTTP body
  Postgres     ◄── scope_process_v1, attachments_status
  Blob         ◄── envelope.json
```

---

## 2. Stable entrypoints (do not rename without changelog)

Fill in **internal** orchestration in §6; these **external** names must stay callable.

### 2.1 Parser-os (production — Purpulse hot path) — VERIFIED 2026-05-27

| Surface | Value | Code reference |
|---------|-------|----------------|
| Repo | Purtera-IT/parser-os-service (private) | `src/parser_os_service/server/` |
| HTTP | `POST /v1/orbitbrief/rebuild-latest` | `src/parser_os_service/server/routes/orbitbrief_latest.py` |
| Auth | Bearer (`BANG_INTERNAL_BEARER`) via `auth.verify_bearer` | `src/parser_os_service/server/auth.py` |
| Request body | `{ "manifest_blob_url": "<https://.../parser-manifests/{compileId}.json>" }` (pydantic `OrbitbriefRebuildBody`) | route file |
| Master Python | `compile_project` → `build_orbitbrief_envelope` → `to_scope_process_v1` | parser-os `app/core/compiler.py`, `app/core/orbitbrief_envelope.py`, parser-os-service `projector.py` |
| Compile invocation defaults | `allow_errors=True, allow_unverified_receipts=True, use_cache=False, domain_pack=_domain_pack_from_manifest(manifest), persistence_hook=None` | route `_run_compile_project` |
| Domain-pack resolution | `manifest.domain_pack` → `manifest.context.domain_pack` → None (auto-route) | route `_domain_pack_from_manifest` |
| Sidecar written for envelope builder | `<work>/.parser_manifest.json` (full manifest JSON dump) | route inline |
| Output blob path | `deals/{dealId}/orbitbrief/latest/envelope.json` | constant `_orbitbrief_latest_envelope_blob_path` |
| Output local-dev path | `${PARSER_OS_SERVICE_LOCAL_BLOB_ROOT}/deals/{dealId}/orbitbrief/latest/envelope.json` | when env var set |
| Schema | `schema_version: orbitbrief.input.v2` | constant `ENVELOPE_SCHEMA_VERSION` in `orbitbrief_envelope.py` |
| Contract file | `contracts/orbitbrief.input.v2.yaml` | this folder |

**Alternate HTTP (not used by queue today):** `POST /v1/compile` — same manifest, different response shape. See `src/parser_os_service/server/routes/compile.py`.

**Job lifecycle (queue / status):** `POST /v1/jobs/...`. See `src/parser_os_service/server/routes/jobs.py`.

**Health:** `GET /v1/health/...`. See `src/parser_os_service/server/routes/health.py`.

### 2.2 Platform trigger (Purpulse-owned)

| Step | Component |
|------|-----------|
| Enqueue | `{ dealId, compileId }` on queue `PARSER_OS_ORBITBRIEF_QUEUE_NAME` |
| Worker | `Platform-infra/azure-function-api/parser-os-orbitbrief-queue` |
| Manifest upload | `deals/{dealId}/parser-manifests/{compileId}.json` |
| Complete | `shared/parser-os-orbitbrief-complete.js` |

See `functionsforparserorbit.md` for full route map.

### 2.3 Orbitbrief-Core (GPU / second stage)

| Surface | Typical artifact | UI contract doc |
|---------|------------------|-----------------|
| Run output | `PM_HANDOFF.json` per case | `Orbitbrief-Core/OUTPUTS_FOR_UI.md` or `parser-os/OUTPUTS_FOR_UI.md` |
| Portfolio | `PM_PORTFOLIO_DASHBOARD.json` | `purpulse-frontend/docs/FRONTEND_INTEGRATION_README.md` §4–5 |

Purpulse may read handoff from blob when `VITE_ORBITBRIEF_USE_PM_HANDOFF=true`.

### 2.4 SowSmith — VERIFIED 2026-05-27

| Surface | Value | Code reference |
|---------|-------|----------------|
| Repo | Purtera-IT/SowSmith (public, pip-installable) | `src/sowsmith/` |
| CLI | `sowsmith render <envelope.json> [--out path]` | `src/sowsmith/cli.py` |
| Master Python | `build_sow_markdown(envelope: dict) -> str` | `src/sowsmith/render.py` re-exported from `__init__.py` |
| Version constant | `SOW_VERSION = "sowsmith_v1"` (bake into output footer) | `render.py` |
| Input contract | `orbitbrief.input.v2` envelope dict — no other dependencies on parser-os / Orbitbrief-Core internals | — |
| Output | Single markdown string — caller writes to disk | — |
| Optional import in parser-os | `from sowsmith import build_sow_markdown` inside `write_orbitbrief_envelope` (now in Orbitbrief-Core post-migration); writes `<out>/sow.md` if installed | shim |

---

## 3. What Purpulse reads today (baseline — verify & extend in §7–8)

### 3.1 HTTP response (`rebuild-latest`)

Platform **requires** these keys on success (see `contracts/rebuild-latest.response.v1.yaml`):

- `attachments_status` → SQL `attachments`
- `scope_process_v1` → SQL `opportunities.quote_data`
- `envelope_blob_url`, `parser_version`, `summary`

### 3.2 Envelope blob (`orbitbrief.input.v2`)

**Required top-level:** `schema_version`, `project_id`, `compile_id`, `generated_at`, `summary`, `documents`, `atoms`, `packets`, `entities`, `edges`, `indexes`

**Optional top-level (cockpit):** `pm_dashboard`, `sow_readiness_scorecard`, `srl_missing_checklist`, `scope_truth`, `change_order_timeline`, `site_readiness`, `stakeholder_load`, `project_vitals`, `drawings` (only when schematic)

**SPA reads today (Deal Artifacts):** `summary.*`, `documents[]`, `atoms[]`, `compile_id`, `generated_at` — see `purpulse-frontend/.../selectEnvelopeForDealArtifacts.ts`

**Platform reads from envelope (best-effort):** `crm.amount`, dollar heuristics in JSON text for `opportunities.amount` backfill

### 3.3 `scope_process_v1` (Postgres)

Purpulse TypeScript type: `purpulse-frontend/src/lib/scope-models/types.ts` → `ScopeProcessState`

**Heavily used UI paths (do not rename without breaking change):**

- `version` (must be `scope_process_v1`)
- `currentStep`
- `projectNeeds` (product_types, site_list, complexity, …)
- `orbitbriefAudit` (missing, risk, confidence, evidenceMap, artifactArchive, …)
- `extractedReview` (lastOrbitBriefRunId, lastOrbitBriefRunAt — Platform may set)
- `fieldStateMap`, `sowReadiness`, `sowHandoff`, `generationHistory`

Projector: `parser_os_service/server/projector.py` → `to_scope_process_v1`

---

## 4. Flags — who owns what

| Flag kind | Where to set | Purpulse code change? |
|-----------|--------------|------------------------|
| Parser compile behavior | Container App env **or** `manifest.context.compile_options` | **No** (if optional with defaults) |
| Per-deal domain pack | `manifest.domain_pack` or `context.domain_pack` or `compile_options.domain_pack` | **No** |
| Product / UI behavior | `VITE_*` (SPA), `ORBITBRIEF_*` (Function App) | **Yes** (Purpulse team) |

**Recommended:** All new parser toggles go in `context.compile_options` (see `contracts/parser-manifest.v1.yaml`). Platform already passes `context`; unknown keys are ignored.

---

## 5. Flags you use

### 5.1 Environment variables (parser-os-service / parser-os)

#### Site detection + multi-entity LLM (parser-only)

| Variable | Default | Effect | Purpulse impact |
|----------|---------|--------|-----------------|
| `OLLAMA_HOST` | `http://100.114.102.122:11434` | Ollama server URL (Griffin's Mac on tailnet today; vLLM box later) | Parser-only |
| `OLLAMA_MODEL` | `qwen3:14b` | Chat model for site + multi-entity extraction | Parser-only |
| `SOWSMITH_LLM_TIMEOUT` | `180` (seconds) | Per-LLM-call timeout | Parser-only |
| `SOWSMITH_LLM_PARALLEL` | `5` | Worker threads for the 5 parallel multi-entity extractors | Parser-only |
| `SOWSMITH_SITE_LLM_DISABLE` | unset | `=1` forces site detection to regex + hygiene only (CI / air-gap) | Parser-only |
| `SOWSMITH_SITE_LLM_VERIFY` | unset | Legacy alias — site LLM is now default-on; this var still honored for back-compat | Parser-only |
| `SOWSMITH_MULTI_ENTITY_DISABLE` | unset | `=1` skips the 5 multi-entity LLM calls (customer / stakeholder / milestone / requirement / site_clusters) | Parser-only |
| `OLLAMA_NUM_PARALLEL` | unset (server-side) | Ollama server's per-model concurrency cap; on vLLM unused | Parser-only |

#### PDF / OCR parsers (parser-only)

| Variable | Default | Effect | Purpulse impact |
|----------|---------|--------|-----------------|
| `PARSER_OS_OCR_DISABLE` | unset | `=1` disables Tesseract OCR fallback for scanned PDFs / images | Parser-only |
| `PARSER_OS_OCR_LANGUAGE` | `eng` | Tesseract language pack | Parser-only |
| `PARSER_OS_OCR_OLLAMA_BASE_URL` | unset | When set, enables vision-LLM OCR via Ollama (llava-class model) | Parser-only |
| `PARSER_OS_OCR_OLLAMA_VISION_MODEL` | unset | Vision-LLM name (`llava`, etc.) | Parser-only |
| `PARSER_OS_PDF_MAX_PAGES` / `MAX_PAGES_LARGE_PDF` | implementation default | Hard cap on per-PDF page parse | Parser-only |
| `PARSER_OS_PDF_SOFT_CAP_MB` / `LARGE_PDF_SOFT_CAP_MB` | implementation default | Soft size cap above which OCR + heavy passes are deferred | Parser-only |
| `PARSER_OS_SCHEMATIC_OVERLAYS` | unset | Enables schematic-drawing overlay analysis | Parser-only |

#### Orbitbrief-Core (orbitbrief-only)

| Variable | Default | Effect | Purpulse impact |
|----------|---------|--------|-----------------|
| `PARSER_OS_ROOT` | implementation-resolved | Path to parser-os checkout (needed when compile_brief runs case-dir mode) | Orbitbrief-only |
| `ORBITBRIEF_LEARNING_LEDGER` | implementation default | Path to learning-ledger JSONL | Orbitbrief-only |
| `ORBITBRIEF_CORPUS_HISTORY` | implementation default | Path to corpus-history JSON for pack prior | Orbitbrief-only |
| `ORBITBRIEF_POLISH_CACHE` | implementation default | Cache dir for the brief polish LLM call | Orbitbrief-only |
| `ORBITBRIEF_FX_API_URL` | `frankfurter` | Currency-FX API endpoint for money normalization | Orbitbrief-only |
| `ORBITBRIEF_FX_DISABLE` | unset | `=1` disables FX lookups (offline / air-gap) | Orbitbrief-only |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama URL for brains / planner / polish | Orbitbrief-only |
| `ORBITBRIEF_POLISH_MODEL` | `qwen3:14b` | Polish-pass chat model | Orbitbrief-only |

#### parser-os-service (service-only — Container App env)

| Variable | Default | Effect | Purpulse impact |
|----------|---------|--------|-----------------|
| `BANG_INTERNAL_BEARER` | (required) | Bearer token the route validates via `parser_os_service.server.auth.verify_bearer` | Service-only |
| `DATABASE_URL` | (required) | Postgres connection (compile job tracking) | Service-only |
| `PARSER_OS_SERVICE_LOCAL_BLOB_ROOT` | unset | When set, envelope writes go to local filesystem instead of Blob (dev/CI mode) | Service-only |

#### Platform-owned (needs-Purpulse)

| Variable | Default | Effect | Purpulse impact |
|----------|---------|--------|-----------------|
| `PARSER_OS_HTTP_TIMEOUT_MS` | Platform-set | Function App HTTP timeout when calling parser-os-service | **Platform only** |
| `ORBITBRIEF_AUTO_CORE_COMPILE` | Platform-set | Function App toggle for auto-firing Core after parser-os rebuild | **Platform only** |
| `VITE_ORBITBRIEF_USE_PM_HANDOFF` | SPA-set | SPA toggles reading PM_HANDOFF from blob | **Platform only (SPA)** |
| `PARSER_OS_SERVICE_URL` | Platform-set | Function App endpoint of parser-os-service | **Platform only** |

### 5.2 `compile_project` / rebuild-latest arguments (code defaults)

Actual signature from `app/core/compiler.py` `compile_project(...)`:

| Flag / param | Type | Code default | Service default | When you change it |
|--------------|------|--------------|-----------------|-------------------|
| `project_dir` | `Path` | (required) | from manifest → tmp dir | — |
| `project_id` | `str \| None` | `None` (defaults to dir name) | from manifest `compile_id` | — |
| `allow_errors` | `bool` | `False` | `True` | Service keeps `True` so synthetic / scan-noisy PDFs don't block compile |
| `allow_unverified_receipts` | `bool` | `False` | `True` | Service keeps `True` so source-replay failures don't block; atoms still carry `verified` field |
| `domain_pack` | `DomainPack \| str \| Path \| None` | `None` (auto-routed) | from manifest or `context.domain_pack` | Per-deal explicit override |
| `calibrator_path` | `Path \| None` | `None` | unset | Set when running calibrated confidence (off in prod today) |
| `abstain_threshold` | `float` | `0.70` | unchanged | Tune if calibrator is on |
| `use_cache` | `bool` | `True` | `False` | Service forces fresh compile per manifest |
| `persistence_hook` | callable | `None` | service-set | Where service taps to write attachments_status / blob during compile |

### 5.3 `manifest.context.compile_options` (per-deal flags)

**Current state (VERIFIED in parser-os-service 2026-05-27):**
- The route `routes/orbitbrief_latest.py` reads `_domain_pack_from_manifest(manifest)` which honors `manifest.domain_pack` then `manifest.context.domain_pack` (returns None → auto-route).
- Beyond `domain_pack`, **no `compile_options` reader exists yet** in parser-os-service. `allow_errors`, `allow_unverified_receipts`, `use_cache` are HARDCODED to `True / True / False` in `_run_compile_project`.
- The proposed compile_options table below is the work item — wiring belongs in `_run_compile_project` and would read each key with a default (so unknown keys remain ignored = `contract:additive`).

Once wired, the keys below SHOULD map to the existing args/env:

| Key | Type | Default | Maps to | Description |
|-----|------|---------|---------|-------------|
| `domain_pack` | string | auto-route | `compile_project(domain_pack=…)` | Force a specific domain pack |
| `allow_errors` | bool | `true` (service) | `compile_project(allow_errors=…)` | Per-deal override |
| `allow_unverified_receipts` | bool | `true` (service) | `compile_project(allow_unverified_receipts=…)` | Per-deal override |
| `use_cache` | bool | `false` (service) | `compile_project(use_cache=…)` | Reuse last compile result |
| `disable_site_llm` | bool | `false` | env `SOWSMITH_SITE_LLM_DISABLE` | Force regex-only site detection |
| `disable_multi_entity_llm` | bool | `false` | env `SOWSMITH_MULTI_ENTITY_DISABLE` | Skip the 5 LLM calls for cust/stake/mile/req/site_clusters |
| `ollama_model` | string | `qwen3:14b` | env `OLLAMA_MODEL` | Per-deal model override |
| `ollama_host` | string | tailnet default | env `OLLAMA_HOST` | Per-deal LLM backend (vLLM URL, etc.) |
| `llm_timeout_seconds` | int | `180` | env `SOWSMITH_LLM_TIMEOUT` | Per-deal LLM timeout |
| `llm_parallel` | int | `5` | env `SOWSMITH_LLM_PARALLEL` | Per-deal parallel-LLM cap |
| `disable_ocr` | bool | `false` | env `PARSER_OS_OCR_DISABLE` | Skip OCR on scanned PDFs |

All `contract:additive` once wired — Platform already passes `context` verbatim; unknown keys are ignored.

### 5.4 Planned flag changes

| Key | Change | Target date | contract:additive / breaking |
|-----|--------|-------------|------------------------------|
| `compile_options.*` (all rows above) | Wire parser-os-service to read these and override env / arg defaults | next service deploy | `contract:additive` |
| `OLLAMA_HOST` default | Switch from tailnet Mac to GPU vLLM box | when GPU box ships | Parser-only |
| `OLLAMA_MODEL` default | Possibly bump to `qwen3:32b` on GPU box for higher quality | when GPU box ships | Parser-only |
| Per-doc parallel site LLM calls | Wrap the per-doc site extraction loop in ThreadPoolExecutor (currently serial) | with GPU box | Parser-only |

---

## 6. Internal pipelines (safe to reorder — Purpulse does not call these)

### 6.1 Parser-os deal compile — `compile_project` stage order

All stages emit a `compile_stage_completed` telemetry event with duration + counts.

| Step # | Stage | Module / function | Notes |
|--------|-------|-------------------|-------|
| 1 | `discover_artifacts` | `app/core/compiler.py` (`_iter_artifacts`) | Walks `project_dir`, respects `.parserignore` |
| 2 | `parse_artifacts` | `app/parsers/parser_router.py` → per-format parser | PDF (PyMuPDF + OCR chain), DOCX, XLSX, PPTX, EML, MD, transcript, image, universal |
| 3 | `candidate_adjudication` | `app/core/adjudication.py` | When multiple parsers fire, picks winner |
| 4 | `source_replay` | `app/core/source_replay.py` | Verifies atoms against source bytes; sets `verified` |
| 5 | `confidence_floor` | `app/core/compiler.py` inline | Drops atoms below floor when `allow_errors=False` |
| 6 | `enrich_entities` | `app/core/entity_extraction.py` `enrich_atoms` | Regex emitters + **LLM-first site detection** + **5 parallel multi-entity LLM extractors** + hygiene |
| 7 | `entity_resolution` | `app/core/entity_resolution.py` | Fuzzy alias match + site/stakeholder co-mention fusion + alias-group hygiene |
| 8 | `graph_build` | `app/core/graph_builder.py` | Builds `EvidenceEdge` between atoms / entities |
| 9 | `packetize` | `app/core/packetize.py` | Groups atoms/edges into `EvidencePacket`s |
| 10 | `packet_certificates` | `app/core/packet_certificates.py` | Attaches certificate + risk score per packet |
| 11 | `confidence_calibration` | `app/core/calibration.py` (optional) | When `calibrator_path` provided |
| 12 | `quality_gates` | `app/core/quality_gates.py` | Produces warnings; never blocks |
| 13 | `persistence` | `persistence_hook` callable (optional) | Service taps here to write to blob / DB |

Then (separate function, not a stage): `build_orbitbrief_envelope(project_dir, compile_result)` → in-memory envelope dict. Optional `write_orbitbrief_envelope(...)` writes JSON + markdown + (if SowSmith installed) `sow.md`.

### 6.2 LLM hot paths inside `enrich_entities` (Step 6)

| Call | Module | Default backend | Bypass |
|------|--------|-----------------|--------|
| Site catalog (LLM-first) | `app/core/site_llm_verify.extract_sites_with_llm` | Ollama `qwen3:14b` | Set `SOWSMITH_SITE_LLM_DISABLE=1` |
| 5× parallel multi-entity | `app/core/multi_entity_llm.extract_all_entities_with_llm` (ThreadPoolExecutor) | Ollama `qwen3:14b` | Set `SOWSMITH_MULTI_ENTITY_DISABLE=1` |
| Reachability probe (2s) | `app/core/site_llm_verify.ollama_reachable` | — | — |
| Multi-entity injection | `app/core/entity_extraction._inject_multi_entity_keys` | (no LLM) | LLM-AUTHORITATIVE: when LLM ran, regex `customer:*` / `stakeholder:*` keys are dropped before injection |
| Final hygiene pass | `app/core/entity_extraction.enrich_atoms` tail | (no LLM) | Always runs |

### 6.3 Orbitbrief-Core run (`compile_brief.py`)

| Step # | Stage | Module | Output |
|--------|-------|--------|--------|
| 1 | Parse envelope (or auto-compile from case-dir via parser-os) | `compile_brief.py` | in-memory envelope |
| 2 | Pack-prior + retrieval bundle | `src/orbitbrief_core/orchestrator/` | retrieval bundle |
| 3 | Planner LLM call | `orchestrator/pipeline.py` | plan |
| 4 | **13+ domain brains** (managed_services, audio_visual, audit, BMS, camera_vms, datacenter, data, electrical, IMAC, low_voltage, network_maint, procurement_finance, professional_services, rack_stack, wireless) | `src/orbitbrief_core/brains/*/` | per-domain findings |
| 5 | Calibrator | `src/orbitbrief_core/calibrator/` | confidence verdicts |
| 6 | Composer | `src/orbitbrief_core/composer/composer.py` | composed brief |
| 7 | Polish LLM call | `compile_brief.py` (uses `OpenAIChatClient`) | polished `brief.md` + `brief.json` |
| 8 | PM_HANDOFF build | `src/orbitbrief_core/pm_handoff/builder.py` | `PM_HANDOFF.json` |
| 9 | (envelope builders now also live here post-migration) | `src/orbitbrief_core/envelope.py` + `envelope_builders.py` | envelope JSON + analytical surfaces |

### 6.4 SowSmith

| Step # | Stage | Module | Output |
|--------|-------|--------|--------|
| 1 | Consume `orbitbrief.input.v2` envelope dict | `sowsmith.build_sow_markdown(envelope)` | `sow.md` string |

### 6.5 Planned pipeline changes

| Pipeline | Change | Purpulse impact |
|----------|--------|-----------------|
| parser-os enrich_entities | Wrap per-doc site LLM loop in `ThreadPoolExecutor` (today serial when >1 doc) | none — same output shape |
| parser-os enrich_entities | When `OLLAMA_NUM_PARALLEL≥5` or vLLM detected, run site + multi-entity in one shared batch | none — same output shape |
| Orbitbrief-Core brains | Move from `qwen3:14b` to escalated tier (`qwen3:32b`) on GPU box | none — `model_used` field already exists |
| Envelope builders | Delete the parser-os deprecation shims after one release | none — call sites already import from `orbitbrief_core.envelope` |
| Multi-entity LLM | Cache per (model, sha256(prompt)) so repeat compiles skip the call | none |

---

## 7. Fields — envelope & handoff

### 7.1 New / changed envelope keys (`orbitbrief.input.v2`)

**No new TOP-LEVEL envelope keys this week.** All required + optional keys (§3.2) unchanged. What changed is **the entity-type richness** of the existing `entities[]` array (and the resulting `scope_truth` / `stakeholder_load` / `project_vitals` content downstream):

| Key path | Status | Type | What changed | UI / Platform consumer |
|----------|--------|------|--------------|------------------------|
| `entities[].entity_type == "customer"` | unchanged top-level — content upgraded | object | Now LLM-first (was regex); deduped to ~1 canonical per pack | scope_truth, stakeholder_load |
| `entities[].entity_type == "stakeholder"` | unchanged top-level — content upgraded | object | Now LLM-first (was regex); 3-layer hygiene drops field-labels / org-tokens / jargon | stakeholder_load, PM_HANDOFF |
| `entities[].entity_type == "milestone"` | unchanged top-level — content upgraded | object | Now LLM-first; catches named events (cutover, freezes, blackouts) plus dates | change_order_timeline, project_vitals |
| `entities[].entity_type == "requirement"` | unchanged top-level — content upgraded | object | Now LLM-first; categorized (sla/compliance/performance/security/deliverable/acceptance/other) | sow_readiness_scorecard, srl_missing_checklist |
| `entities[].entity_type == "site"` | unchanged top-level — content upgraded | object | LLM-first since prior release; 4-layer hygiene; LLM-extracted site_clusters available in compile result but not yet projected into envelope | site_readiness, scope_truth |
| `summary.degraded_files` | optional | array | unchanged — already used | Deal Artifacts |
| `summary.parser_os_version` | required | string | unchanged | rebuild-latest summary echo |

**Optional top-level (unchanged set, content now richer):** `pm_dashboard`, `sow_readiness_scorecard`, `srl_missing_checklist`, `scope_truth`, `change_order_timeline`, `site_readiness`, `stakeholder_load`, `project_vitals`, `drawings`.

**Experimental top-level (none today):** see §7.4 for candidates.

### 7.2 `scope_process_v1` paths written via projector — VERIFIED

Source: `parser-os-service/src/parser_os_service/server/projector.py` → `to_scope_process_v1(result, manifest, manifest_blob_url, prior)`.
Caller: `routes/orbitbrief_latest.py` `orbitbrief_rebuild_latest_endpoint`.

Behavior: deep-merges a fresh-projected scope object over an optional `prior` (read from `manifest.context.prior_scope_process_v1`). The HTTP route then overrides `extractedReview.lastOrbitBriefRunId/At` from `compile_id` + `_finished_at_iso(result)`.

| JSON path | Type | Written by | Source |
|-----------|------|------------|--------|
| `version` | string (must equal `"scope_process_v1"`) | projector | hardcoded |
| `lastIngestAt` | ISO 8601 string | projector | `result.manifest.completed_at` or last stage `started_at` |
| `lastIngestSource` | string (`"bang-compile"`) | projector | hardcoded |
| `sowHandoff.scope_in[]` | list[packet row] | projector | `PacketFamily.scope_inclusion` packets |
| `sowHandoff.scope_out[]` | list[packet row] | projector | `PacketFamily.scope_exclusion` packets |
| `sowHandoff.assumptions[]` | list[{atom_id, text, authority_class}] | projector | `_governing_assumption_atoms(result)` |
| `sowHandoff.risks[]` | list[packet row] | projector | packets with severity `high` or `critical` |
| `sowHandoff.open_questions[]` | list[packet row] | projector | `PacketFamily.missing_info` packets |
| `sowHandoff.decisions[]` | list[packet row] | projector | `PacketFamily.meeting_decision` packets |
| `sowHandoff.action_items[]` | list[packet row] | projector | `PacketFamily.action_item` packets |
| `projectNeeds.active_domains[]` | list[string] | projector | `_active_domains_from_packets(result)` |
| `projectNeeds.site_list[]` | list | projector | `_site_list(result)` — content upgrade-impact: now LLM-first + 4-layer hygiene clean |
| `projectNeeds.notes` | string | projector | `_project_needs_notes(result, manifest)` |
| `sowReadiness` | object | projector | `_sow_readiness(result)` — content upgrade-impact: richer `requirement` + `milestone` entities feed this |
| `extractedReview.contradictions[]` | list | projector | `_contradictions_packets(result)` |
| `extractedReview.lastOrbitBriefRunId` | string | **HTTP route overrides** | `compile_id` from manifest |
| `extractedReview.lastOrbitBriefRunAt` | ISO string | **HTTP route overrides** | `_finished_at_iso(result)` |
| `orbitbriefAudit.evidenceMap` | object | projector | `_evidence_map(result)` |
| `orbitbriefAudit.confidence` | object | projector | `_confidence_block(result)` |
| `orbitbriefAudit.missing[]` | list[string] | projector | `packet.reason` for each `missing_info` packet |
| `orbitbriefAudit.reasonMap` | object | projector | `_reason_map(result)` |
| `orbitbriefAudit.artifactArchive.manifestBlobUrl` | string | projector | `manifest_blob_url` (passed in from HTTP body) |
| `selectedArtifacts[]` | list | projector | `_selected_artifacts(manifest)` |
| Anything else (`currentStep`, `fieldStateMap`, `generationHistory`, `projectNeeds.product_types`, `projectNeeds.complexity`, `orbitbriefAudit.risk`, …) | — | NOT projected by parser-os-service | Pre-existing Platform/SPA fields; survive only via the `prior` deep-merge from `manifest.context.prior_scope_process_v1` |

**Regression-test subset** (`projector.mapping_subset(scope)`) — these are the keys the parser-os-service repo asserts stability on; renaming any of these is `contract:breaking`:

`version`, `lastIngestSource`, `sowHandoff.scope_in`, `sowHandoff.scope_out`, `sowHandoff.assumptions`, `sowHandoff.risks`, `sowHandoff.open_questions`, `sowHandoff.decisions`, `sowHandoff.action_items`, `projectNeeds.active_domains`, `projectNeeds.notes`, `projectNeeds.site_list`, `sowReadiness`, `extractedReview.contradictions`, `orbitbriefAudit.evidenceMap`, `orbitbriefAudit.confidence`, `orbitbriefAudit.missing`, `orbitbriefAudit.reasonMap`, `orbitbriefAudit.artifactArchive.manifestBlobUrl`, `selectedArtifacts`.

### 7.3 `PM_HANDOFF.json` fields — definitive catalog

**Source of truth:** `Orbitbrief-Core/src/orbitbrief_core/pm_handoff/models.py` (PMHandoff dataclass) + `pm_handoff/builder.py` (`build_pm_handoff(case_dir)`).
**Full output spec:** `parser-os/OUTPUTS_FOR_UI.md` (729 lines, every field documented with real OPTBOT values).
**Total top-level fields:** 54 (verified on OPTBOT compile).

#### Header & status (5)

| Field | Type | Required? | UI surface |
|-------|------|-----------|------------|
| `case_id` | str | ✅ | Header title |
| `status` | enum (`red`/`amber`/`green`) | ✅ | Status traffic light |
| `status_label` | str | ✅ | Header subtitle |
| `one_line_summary` | str | ✅ | Top banner |
| `executive_summary` | dict (`headline`, `health_line`, `next_action`) | ✅ | Above-the-fold exec callout |

#### Scorecard (1)

| Field | Type | Required? | Sub-keys |
|-------|------|-----------|----------|
| `metrics` | dict | ✅ | `blockers`, `warnings`, `info`, `evidence_groups_certified`, `evidence_items_extracted`, `missing_sow_items`, `pm_visible_fact_cards`, `sites_published`, `source_files`, `sow_validator_status`, `top_workstream` |

#### Intake quality (2)

| Field | Type | UI surface |
|-------|------|------------|
| `intake_completeness` | list[dict] | Completeness checklist + progress bar |
| `ocr_backend_status` | dict | OCR-status chip with install hints |

#### Money & commercial (10)

| Field | Type | UI surface |
|-------|------|------------|
| `money_mentions` | list[dict] | Money mention table |
| `reconciliation_flags` | list[dict] | "Needs attention" panel |
| `currency_mentions` | list[dict] | (USD-only deals empty) |
| `currency_conversions` | list[dict] | FX-converted view |
| `tax_clauses` | list[dict] | Tax surface |
| `margin_view` | dict (9 keys: `deal_total`, `hardware_cost_subtotal`, `services_subtotal`, `other_cost_subtotal`, `total_cost`, `gross_profit`, `margin_pct`, `confidence`, `notes`) | Margin gauge with red-band <15% |
| `engagement_model` | dict | T&M / Fixed Fee / Subscription detection |
| `license_items` | list[dict] | Recurring software tracker |
| `eol_flags` | list[dict] | End-of-life BOM alerts |

#### Sites & per-site rollups (3)

| Field | Type | UI surface |
|-------|------|------------|
| `sites` | list[SiteSummary] | Site cards |
| `site_rollups` | list[dict] (per site: `atom_count`, `devices`, `money_values`, `dates`, `stakeholders`) | Per-site coverage matrix |
| `site_allocations` | list[dict] (per-site BOM math) | BOM rollup table |

#### Stakeholders (2)

| Field | Type | UI surface |
|-------|------|------------|
| `stakeholder_contacts` | list[dict] (`name`, `role`, `email`, `phone`, `source`) | Contact directory table |
| `stakeholder_pagers` | list[dict] (3 fixed lenses: CFO / IT / Procurement, each with `summary_lines`, `money_lines`, `risk_lines`, `action_lines`) | 3-tab one-pager browser |

#### Schedule (6)

| Field | Type | UI surface |
|-------|------|------------|
| `schedule_phases` | list[dict] | Mermaid Gantt + fallback table |
| `critical_path` | list[dict] | Critical-path overlay |
| `critical_path_chain` | list[str] | Phase-chain string |
| `phase_dependencies` | list[dict] | Dependency edges |
| `resource_conflicts` | list[dict] | Owner-overlap red flags |
| `lead_time_flags` | list[dict] | BOM lead-time risk |

#### Risks (2)

| Field | Type | UI surface |
|-------|------|------------|
| `risk_register` | list[dict] (`risk_id`, `description`, `likelihood`, `impact`, `mitigation`, `owner`, `sites`, `source`) | Risk table sorted by L×I |
| `risk_aging` | list[dict] | Aging buckets (fresh/active/stale) |

#### Compliance & legal (3)

| Field | Type | UI surface |
|-------|------|------------|
| `compliance_callouts` | list[dict] | Compliance routing table |
| `sla_penalties` | list[dict] | Liquidated-damages surface |
| `change_order_triggers` | list[dict] | Change-order pre-flag panel |

#### Scope structure (8)

| Field | Type | UI surface |
|-------|------|------------|
| `exclusions` | list[dict] | Out-of-scope tab |
| `responsibilities` | list[dict] (customer vs provider split) | Responsibility split tab |
| `quantity_claims` | list[dict] | Quantity table |
| `quantity_contradictions` | list[dict] | Reconciliation queue |
| `acceptance_checks` | list[dict] | Acceptance checklist |
| `acceptance_by_site` | dict (keyed by site code) | Per-site acceptance tabs |
| `domains` | list[DomainSummary] (`domain_id`, `label`, `selected_by_router`, `active_for_sow`, `blockers`, `warnings`, `info`) | Workstream chips with severity |
| `subcontractor_mentions` | list[dict] | Vendor list |

#### PM action queue (4)

| Field | Type | UI surface |
|-------|------|------------|
| `gaps` | list[GapCard] (blocker / warning / info) | Gap browser |
| `customer_questions` | list[GapCard] (blocker + warning only, for customer email) | Customer-email starter |
| `action_items` | list[dict] (consolidated from gaps + risks + phases) | Action checklist |
| `actions_by_week` | dict (`this_week`, `next_week`, `later`, `no_date`) | Week-bucket tabs |

#### Cross-doc reconciliation (already counted above — money/date/quantity)

| Field | Type | UI surface |
|-------|------|------------|
| `date_mentions` | list[dict] | Date-mention reconciliation panel |

#### Output deliverables embedded in payload (3)

| Field | Type | UI surface |
|-------|------|------------|
| `sow_draft_markdown` | str (≈16K chars on OPTBOT) | SOW viewer tab |
| `rfp_draft_markdown` | str (≈10K chars on OPTBOT) | RFP viewer tab |
| `rfp_line_items` | list[dict] | RFP BOM structured data |

#### Strategic (1)

| Field | Type | UI surface |
|-------|------|------------|
| `comparable_deals` | list[dict] (`case_id`, `closed_at`, `deal_value_usd`, `domains`, `sites_count`, `phase_count`, `final_margin_pct`, `outcome`) | "Similar past deals" panel |

#### Source provenance (3)

| Field | Type | UI surface |
|-------|------|------------|
| `source_files` | list[SourceFileSummary] (`filename`, `artifact_type`, `parser_name`, `evidence_items`, `status`, `status_reason`) | Source-inventory table (audit chain) |
| `facts_by_category` | dict (categories: `sites_access`, `scope_deliverables`, `bom_procurement_pricing`, `network_vlans_circuits`, …) | Facts browser |
| `sa_focus` | list[str] | SA-review-lane tabs |

#### Domain detection (already counted as `domains` above)

#### Telemetry / drift / urgency / customer-answer (4)

| Field | Type | UI surface |
|-------|------|------------|
| `run_telemetry` | dict | "This brief took Ns to produce" badge |
| `drift_snapshot` | dict | "Changed vs last run" banner |
| `urgency_signals` | list[dict] | Time-sensitive alerts |
| `customer_answer_slots` | list[dict] | Customer-clarification email scaffold |

#### Quality (1)

| Field | Type | UI surface |
|-------|------|------------|
| `parser_quality_score` | dict (`score`, `grade`, `components`) — see audit dashboard | Giant score gauge + grade letter + breakdown chart |

#### Audit/UI extras (1)

| Field | Type | UI surface |
|-------|------|------------|
| `has_exclusion` | bool | UI flag |

> **Note:** `pm_dashboard`, `sow_readiness_scorecard`, `srl_missing_checklist`, `scope_truth`, `change_order_timeline`, `site_readiness`, `stakeholder_load`, `project_vitals` are computed INLINE in the envelope (orbitbrief.input.v2). Purpulse can read either the embedded versions in the envelope OR the corresponding rollups in `PM_HANDOFF.json` — the two share the same source atoms.

### 7.4 Planned field changes

| Path | Change type | Migration | contract:additive / breaking |
|------|-------------|-----------|------------------------------|
| `entities[].entity_type == "customer/stakeholder/milestone/requirement"` — add `evidence_source: "llm" \| "regex"` metadata | additive | none | `contract:additive` |
| `experimental_top_level: site_clusters` (LLM-derived alias groups) | additive | promote to `optional_top_level` once stable | `contract:additive` |
| `summary.llm_usage` — per-pack LLM-call count + total tokens | additive | none | `contract:additive` |
| `summary.parser_features` — list of which optional pipelines ran (`site_llm`, `multi_entity_llm`, `ocr`, …) | additive | none | `contract:additive` |
| Stakeholder name fusion across docs (collapse "Watkins" + "Renee Watkins") | content upgrade | none — same shape | `contract:additive` |
| Site canonical-cluster fusion (collapse the 13 OPTBOT surface forms to 5 canonical entities) | content upgrade | none — same shape | `contract:additive` |

---

## 8. Master functions map — VERIFIED 2026-05-27

One row per **external** callable.

| Product | Master entry (stable) | Repo | Internal orchestrator file |
|---------|----------------------|------|----------------------------|
| Deal compile (Purpulse hot path) | `POST /v1/orbitbrief/rebuild-latest` (Bearer auth) | **Purtera-IT/parser-os-service** | `src/parser_os_service/server/routes/orbitbrief_latest.py` → `_run_compile_project` (calls parser-os `compile_project`) → `_build_envelope` (calls parser-os `build_orbitbrief_envelope`) → `to_scope_process_v1` (projector) → returns HTTP body |
| Deal compile (alternate, not used by queue today) | `POST /v1/compile` (Bearer auth) | parser-os-service | `src/parser_os_service/server/routes/compile.py` |
| Compile job lifecycle | `POST /v1/jobs/...` (Bearer auth) | parser-os-service | `src/parser_os_service/server/routes/jobs.py` |
| Health / readiness | `GET /v1/health/...` | parser-os-service | `src/parser_os_service/server/routes/health.py` |
| Bearer auth | `parser_os_service.server.auth.verify_bearer` | parser-os-service | `src/parser_os_service/server/auth.py` |
| Blob I/O | `read_manifest_json`, `download_blob_to_path`, `upload_json_blob`, `infer_account_and_container_from_artifacts` | parser-os-service | `src/parser_os_service/server/blob_client.py` |
| scope_process_v1 projector | `to_scope_process_v1(result, manifest, manifest_blob_url, prior=None)` | parser-os-service | `src/parser_os_service/server/projector.py` |
| **Substrate API (parser-os)** | `compile_project(project_dir, project_id, allow_errors, allow_unverified_receipts, persistence_hook, domain_pack, calibrator_path, abstain_threshold, use_cache)` | parser-os | `app/core/compiler.py` |
| Local CLI compile | `python -m app.cli compile <project_dir>` | parser-os | `app/cli.py` |
| Internal HTTP (deal-local) | `POST /{project_id}/compile`, `/artifacts`, `/projects`, `/atoms`, `/edges`, `/entities`, `/packets` | parser-os | `app/api/routes_*.py` |
| Envelope build | `build_orbitbrief_envelope(project_dir, compile_result)` | parser-os (deprecation shim) → **Orbitbrief-Core** | `app/core/orbitbrief_envelope.py` (shim) → `src/orbitbrief_core/envelope.py` (canonical) |
| Envelope write | `write_orbitbrief_envelope(project_dir, envelope, out_dir)` | parser-os (deprecation shim) → **Orbitbrief-Core** | same as above |
| Analytical surfaces (pm_dashboard, scope_truth, project_vitals, …) | `build_*` family | parser-os (deprecation shim) → **Orbitbrief-Core** | `app/core/orbitbrief_core.py` (shim) → `src/orbitbrief_core/envelope_builders.py` (canonical) |
| Core brief run (CLI) | `python compile_brief.py <envelope.json \| case_dir> --out <dir> --ollama` | Orbitbrief-Core | `compile_brief.py` |
| Core corpus run | `python compile_corpus.py <root> --out <dir> --ollama` | Orbitbrief-Core | `compile_corpus.py` |
| Domain brain API | `Brain(model=…).analyze(envelope)` per service line | Orbitbrief-Core | `src/orbitbrief_core/brains/<domain>/__init__.py` (15+ domains) |
| Planner | `Pipeline(planner_default_model="qwen3:14b", planner_escalated_model="qwen3:32b")` | Orbitbrief-Core | `src/orbitbrief_core/orchestrator/pipeline.py` |
| PM_HANDOFF builder | `build_pm_handoff(case_dir) -> PMHandoff` | Orbitbrief-Core | `src/orbitbrief_core/pm_handoff/builder.py` (uses `models.py` dataclass with 54 fields) |
| **SOW render API** | `build_sow_markdown(envelope) -> str`, constant `SOW_VERSION = "sowsmith_v1"` | **Purtera-IT/SowSmith** (public pip pkg) | `src/sowsmith/__init__.py` re-exports from `src/sowsmith/render.py` |
| SOW CLI | `sowsmith render <envelope.json> [--out path]` | SowSmith | `src/sowsmith/cli.py` |

---

## 9. Change process & notifying Purpulse

### 9.1 Additive (you can ship alone)

- New **optional** top-level envelope key → list under `optional_top_level` or `experimental_top_level` in YAML
- New `compile_options` key with default
- New atom types (UI chips may ignore)
- New `attachments_status` fields

**Checklist:** Update `contracts/CHANGELOG.md` with `contract:additive` · run envelope smoke test · deploy parser-os-service

### 9.2 Breaking (requires Purpulse)

- Rename/remove keys in §3.3 or required envelope keys
- Change meaning of existing field (same name, different semantics)
- New **required** manifest or HTTP field
- Bump `schema_version` to `orbitbrief.input.v3`

**Checklist:** `contract:breaking` in CHANGELOG · coordinate PR · update `purpulse-frontend` types · feature-flag UI if needed

### 9.3 PR labels

- `contract:additive` — platform can merge independently after your deploy
- `contract:breaking` — blocks until Purpulse acknowledges

### 9.4 Tell the AI / platform engineer

Paste this in Slack or PR description:

```text
Contract update:
- CHANGELOG: contracts/CHANGELOG.md (YYYY-MM-DD entry)
- Schemas: orbitbrief.input.v2 | rebuild-latest | manifest | PM_HANDOFF
- Type: additive | breaking
- New keys: ...
- Purpulse action: none | update Deal Artifacts | update scope_process_v1 types
- Sample blob: deals/<dealId>/orbitbrief/latest/envelope.json (or attach snippet)
```

---

## 10. Verification commands (developer)

```bash
# Local envelope smoke (parser-os repo)
cd parser-os && python -m pytest tests/test_orbitbrief_envelope.py -q

# Full pipeline script (if present)
python scripts/run_sowsmith_e2e.py

# Service health
curl -s "$PARSER_OS_SERVICE_URL/v1/health"
```

Platform validation (Purpulse): `Platform-infra/azure-function-api/scripts/validate-phase8-parser-to-core.sh`

---

## 11. Quick reference — blob paths

| Path | Writer | Reader |
|------|--------|--------|
| `deals/{dealId}/artifacts/{sha256}/{filename}` | Upload / HubSpot mirror | parser-os download |
| `deals/{dealId}/parser-manifests/{compileId}.json` | Platform queue | rebuild-latest |
| `deals/{dealId}/orbitbrief/latest/envelope.json` | parser-os-service | SPA, Core, SowSmith, Platform backfill |

---

*Last baseline sync from repo: 2026-05-26. §5–8 filled in 2026-05-27. parser-os-service + SowSmith inspection verified 2026-05-27. Developer: keep CHANGELOG current on each integration-impacting PR.*
