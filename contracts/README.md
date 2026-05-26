# Purtera integration contracts

Cross-repo boundary between **parser-os / Orbitbrief-Core / SowSmith** (developer-owned pipelines) and **Purpulse** (Platform + SPA).

| File | Purpose |
|------|---------|
| [DEVELOPER_INTEGRATION_PLAYBOOK.md](./DEVELOPER_INTEGRATION_PLAYBOOK.md) | **Start here** — rules, baseline inventory, fill-in templates for flags/fields/pipelines |
| [orbitbrief.input.v2.yaml](./orbitbrief.input.v2.yaml) | Envelope JSON contract (`deals/{id}/orbitbrief/latest/envelope.json`) |
| [rebuild-latest.response.v1.yaml](./rebuild-latest.response.v1.yaml) | `POST /v1/orbitbrief/rebuild-latest` HTTP response |
| [parser-manifest.v1.yaml](./parser-manifest.v1.yaml) | Input manifest written by Azure queue worker |
| [CHANGELOG.md](./CHANGELOG.md) | Contract change log (additive vs breaking) |

**Related docs (not replaced by this folder):**

- `purpulse-frontend/docs/FRONTEND_INTEGRATION_README.md` — UI capability reference
- `functionsforparserorbit.md` — Platform route map (HTTP + queue)
- `parser-os/OUTPUTS_FOR_UI.md` — 729-line field-level spec; PLAYBOOK §7.3 mirrors its PM_HANDOFF surface
- `SowSmith/README.md` — SOW render from envelope

**Workflow:** Developer updates inventory + YAML + `CHANGELOG.md` on every integration-impacting PR. Purpulse reads contracts; internal function renames do not require contract updates.

---

## What's in this drop (2026-05-27)

| File | Status |
|------|--------|
| `DEVELOPER_INTEGRATION_PLAYBOOK.md` | **§5–8 filled in.** Real env vars, real `compile_project` signature, all 13 pipeline stages, the LLM hot paths, all 54 `PM_HANDOFF.json` fields catalogued. |
| `orbitbrief.input.v2.yaml` | Synced — no new top-level keys; entity-content upgrade table added. |
| `parser-manifest.v1.yaml` | Synced — `compile_options.reserved_keys` table (12 keys + types + defaults + maps_to). |
| `rebuild-latest.response.v1.yaml` | Unchanged from baseline — HTTP response shape stable. |
| `CHANGELOG.md` | Two 2026-05-27 entries: §5–8 inventory + PM_HANDOFF catalog. |

**Open follow-ups** (need user input — see §7.2 in playbook and CHANGELOG): parser-os-service repo not on local disk; cannot verify the `scope_process_v1` projector paths or the `manifest.context.compile_options` reader. Once that repo is shared, those rows get marked Verified.
