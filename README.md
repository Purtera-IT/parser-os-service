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

## Docker build (PAT as BuildKit secret)

```bash
DOCKER_BUILDKIT=1 docker build \
  --secret id=github_pat,src=$HOME/.cursor/github_pat \
  -t parser-os-service:local .
```

The Dockerfile **requires** the `github_pat` BuildKit secret so `pip` can clone `Purtera-IT/parser-os` (private org). Do not bake the PAT into any image layer.

## Env

| Variable | Purpose |
|----------|---------|
| `BANG_INTERNAL_BEARER` | When set, `Authorization: Bearer …` required on routes that use `verify_bearer` |
| `DATABASE_URL` | Optional async Postgres pool (not written in PR5) |
| `PARSER_OS_SERVICE_LOCAL_BLOB_ROOT` | When set, writes `result.json` + certificates under this directory instead of Azure |

## ADR

See `docs/decisions/0001-parser-os-pin.md` for the Parser OS Git ref policy.
