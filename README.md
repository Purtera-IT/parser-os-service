# parser-os-service

HTTP wrapper around [`Purtera-IT/parser-os`](https://github.com/Purtera-IT/parser-os) (`purtera-evidence-mvp`) implementing:

- `BANG_IMPLEMENTATION_PLAN.md` §4.4.2 `POST /v1/compile`
- §4.4.3 compile flow (Blob manifest → temp project dir → `compile_project` → Blob results)
- §4.4.4 projector (`CompileResult` → `scope_process_v1`)
- §4.6 manifest / §4.7 response shapes

Parser OS is consumed **only** as a Python dependency (this repo does not vendor `app/`).

## Local dev

```bash
cd parser-os-service
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn parser_os_service.server.app:app --reload --host 0.0.0.0 --port 8000
```

If `pip` cannot fetch the private Git dependency, install Parser OS first:

```bash
pip install -e ../parser-os
pip install -e ".[dev]" --no-deps
pip install azure-identity azure-storage-blob fastapi psycopg pydantic uvicorn
```

## Docker build

### Production / dev deploy (v45.2+)

Use the wrapper — it defeats the cached-layer trap that caused the v45.1
dev incident (deployed `d931a38`, ran `1a8176c`):

```bash
# Build (no push):
./scripts/build_image.sh dev v45.2

# Build + push to ACR (run `az acr login -n parserosacr` first):
PUSH=true ./scripts/build_image.sh dev v45.2

# Build a feature branch (any git ref works):
./scripts/build_image.sh dev claude/crazy-gauss-a9cfe0
```

The wrapper:

1. Resolves the requested ref to a concrete SHA via `git ls-remote`.
2. Re-clones parser-os fresh into a temp build context.
3. Runs `docker build --no-cache --pull` with `--build-arg GIT_SHA=<sha>`.
4. Asserts the resulting image's `PARSER_OS_SHA` env equals what was built.

### Verify the deployed image

After any deploy, confirm the running container actually contains the commit
you expect:

```bash
curl -sS https://<service>/v1/version | jq '.parser_os_sha, .build_label'
# Must match the GIT_SHA you passed to build_image.sh.
```

Or use the parser-os verify script which automates this + an end-to-end
recompile smoke (entity count + polish-stage warning):

```bash
./<parser-os>/scripts/verify_deployed_sha.sh \
  --url https://<service> \
  --expected-sha $(git -C <parser-os> rev-parse v45.2) \
  --expected-label v45.2
```

### Diagnose polish-stage all-fallback

If `pm-handoff.json` shows `polish_stage.items_polished == 0` and a high
`items_fallback`, the Core worker can't reach Mac Ollama via the proxy.
Run:

```bash
./scripts/diagnose_ollama_proxy.sh
# Or with Mac SSH access for end-to-end:
./scripts/diagnose_ollama_proxy.sh --mac-ssh user@mac-host
```

### Local (no PAT in image)

```bash
DOCKER_BUILDKIT=1 docker build \
  --secret id=github_pat,src=$HOME/.cursor/github_pat \
  -t parser-os-service:local .
```

Do not bake the PAT into any image layer.

## Env

| Variable | Purpose |
|----------|---------|
| `BANG_INTERNAL_BEARER` | When set, `Authorization: Bearer …` required on routes that use `verify_bearer` |
| `DATABASE_URL` | Optional async Postgres pool (not written in PR5) |
| `PARSER_OS_SERVICE_LOCAL_BLOB_ROOT` | When set, writes `result.json` + certificates under this directory instead of Azure |
| `PARSER_OS_SHA` | (v45.2) Stamped at build time by Dockerfile.  Surfaced via `/v1/version`. |
| `PARSER_OS_BUILD_LABEL` | (v45.2) Human label (e.g. `v45.2`) stamped at build time. |

## ADR

See `docs/decisions/0001-parser-os-pin.md` for the Parser OS Git ref policy and the v45.2 SHA-stamping fix.
