# syntax=docker/dockerfile:1
#
# parser-os-service — production Docker image
#
# v45.2 hardening (2026-05-26): SHA-stamping so /v1/version can prove which
# parser-os commit the running container actually contains.  The dev deploy
# of v45.1 silently shipped commit 1a8176c (v32) — 39 commits behind — because
# the `COPY parser-os ./parser-os` layer was cached.  This Dockerfile fixes
# that:
#   1. REQUIRES --build-arg GIT_SHA at build time (defaults to "unknown" so
#      misconfiguration is loud, not silent).
#   2. Stamps both env var (PARSER_OS_SHA) AND on-disk file (/app/.git_sha)
#      so the running service can report it via /v1/version.
#   3. The build script (scripts/build_image.sh) MUST pass --no-cache to
#      defeat the layer-caching that caused the original incident.
#
# Build (from the parent of this repo, with parser-os cloned as a sibling):
#   DOCKER_BUILDKIT=1 docker build \
#     --no-cache \
#     --build-arg GIT_SHA=$(git -C parser-os rev-parse HEAD) \
#     --build-arg BUILD_LABEL=v45.2 \
#     -f parser-os-service/Dockerfile \
#     -t parserosacr.azurecr.io/parser-os-service:dev \
#     .
#
# Or use the wrapper: ./scripts/build_image.sh dev v45.2
#
# Verify after deploy:
#   curl -sS https://<service>/v1/version | jq -r .parser_os_sha
#   # MUST match the GIT_SHA you passed above.

FROM python:3.11-slim AS runtime

RUN useradd -m -u 10001 parseros
WORKDIR /build

# v60.2: bust the COPY/install cache every deploy so an unchanged parser-os ref
# (e.g. feat/clean-rubric-heads HEAD) still re-COPYs fresh source. Without this
# the `COPY parser-os` layer cached and /v1/version reported a STALE parser_os_sha,
# failing the deploy's verify step (mirrors the parser-os-worker CACHEBUST fix).
ARG CACHEBUST=unknown
RUN echo "cachebust=$CACHEBUST"

COPY parser-os ./parser-os
COPY parser-os-service ./parser-os-service

ENV PIP_ROOT_USER_ACTION=ignore
RUN set -eux; \
    pip install --no-cache-dir --upgrade pip setuptools wheel; \
    pip install --no-cache-dir ./parser-os; \
    pip install --no-cache-dir \
      "azure-identity>=1.15" \
      "azure-storage-blob>=12.19" \
      "azure-storage-queue>=12.10" \
      "fastapi>=0.110" \
      "psycopg[binary,pool]>=3.1" \
      "pydantic>=2.5" \
      "uvicorn[standard]>=0.27"; \
    pip install --no-cache-dir --no-deps ./parser-os-service; \
    rm -rf /build

WORKDIR /app

# ─── v45.2: git SHA stamping ───────────────────────────────────────────────
# These ARGs MUST be passed at build time.  The default "unknown" makes a
# misconfigured build immediately visible via /v1/version instead of silently
# running an unknown commit.
ARG GIT_SHA=unknown
ARG BUILD_LABEL=unknown

# Persist as env vars so the running container can read them at any time.
# parser-os v45.2+ /v1/version reads PARSER_OS_SHA first, then GIT_SHA env,
# then /app/.git_sha stamp file — see app/api/routes_health.py in parser-os.
ENV PARSER_OS_SHA=$GIT_SHA
ENV PARSER_OS_BUILD_LABEL=$BUILD_LABEL

# Belt-and-suspenders: write a stamp file in case env vars get stripped by
# some orchestrator wrapper.  parser-os's _resolve_parser_os_sha() will pick
# this up as a fallback.
RUN echo "$GIT_SHA" > /app/.git_sha \
 && echo "$BUILD_LABEL" > /app/.build_label \
 && chown parseros:parseros /app/.git_sha /app/.build_label

USER parseros
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "parser_os_service.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
