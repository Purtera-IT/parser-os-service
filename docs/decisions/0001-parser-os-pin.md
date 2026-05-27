# ADR 0001 — Parser OS dependency pin

**Status:** Accepted (v45.2, 2026-05-26)

**Decision:** `parser-os-service` installs `purtera-evidence-mvp` from `Purtera-IT/parser-os` at an immutable **Git tag**.

- **Current pin:** `v45.2` (see root `pyproject.toml`)
- **Bumped via:** edit `pyproject.toml` → push new commit → CI/CD builds with `scripts/build_image.sh dev v45.X`

**Rationale:** Reproducible compiles and auditable diffs for SOC 2.

## Incident: v45.1 dev deploy (2026-05-26)

The dev `deploy-dev.sh` rebuilt `parser-os-service:dev` against parser-os@`d931a38` (v45.1) but **`/v1/version` reported parser_os_sha=`1a8176c`** (v32, 39 commits behind). Root cause:

1. The Dockerfile did `COPY parser-os ./parser-os` from the local build context.
2. Docker BuildKit cached the `COPY` layer because file mtimes hadn't changed in the runner's checkout.
3. `pip install ./parser-os` reused that cached layer — so the image contained v32 source even though the build script "checked out" d931a38.
4. There was no SHA stamping, so `/v1/version` had no way to expose the mismatch — `git rev-parse` was happening at request time against a stripped `.git` (.dockerignore removes it), so the response was unreliable.

## v45.2 fix

Three changes:

1. **`Dockerfile`** — accepts `ARG GIT_SHA` + `ARG BUILD_LABEL`, sets `ENV PARSER_OS_SHA=$GIT_SHA`, and writes `/app/.git_sha` so `/v1/version` can report the real commit (parser-os v45.2's `routes_health.py` reads this env var with the stamp file as fallback).
2. **`scripts/build_image.sh`** — clones parser-os FRESH into a temp dir at the requested ref, then builds with `--no-cache --pull` to defeat Docker layer caching. Verifies the image's `PARSER_OS_SHA` env matches what was built.
3. **`pyproject.toml`** — pinned to immutable tag `v45.2` instead of mutable `main`. Bumping the tag is now a real, reviewed change.

## How to verify after deploy

```bash
curl -sS https://<service>/v1/version | jq -r '.parser_os_sha, .build_label'
# Must match the GIT_SHA passed to build_image.sh.  Mismatch → bad deploy.
```

CI should reject deploys where these don't match expected.

## Bumping the pin

```bash
# 1. Tag parser-os
cd ../parser-os
git tag -a v45.3 -m "v45.3 — <change description>"
git push origin v45.3

# 2. Update parser-os-service pyproject.toml
#    "purtera-evidence-mvp @ git+...@v45.3"

# 3. Build + push
cd ../parser-os-service
PUSH=true ./scripts/build_image.sh dev v45.3

# 4. Roll the Container App
az containerapp update -n parser-os-service-dev-eus2 -g <rg> \
  --image parserosacr.azurecr.io/parser-os-service:dev

# 5. Verify
curl -sS https://<service>/v1/version | jq
```
