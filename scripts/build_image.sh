#!/usr/bin/env bash
# v45.2 — build the parser-os-service image with proper SHA stamping.
#
# This script defeats the cached-layer trap that hit the v45.1 dev deploy
# (deployed d931a38 but /v1/version reported 1a8176c because Docker reused
# a stale `COPY parser-os` layer).
#
# Steps:
#   1. Resolve the parser-os git ref to a concrete SHA (so we know what we built)
#   2. Re-clone parser-os fresh into a temp build context (no stale working tree)
#   3. docker build with --no-cache + --build-arg GIT_SHA=...
#   4. Optionally push to ACR
#   5. Verify by introspecting the image label
#
# Usage:
#   scripts/build_image.sh dev v45.2
#   scripts/build_image.sh dev claude/crazy-gauss-a9cfe0
#   scripts/build_image.sh dev 8f5a28a
#
# Env:
#   PARSER_OS_REPO   git URL (default: https://github.com/Purtera-IT/parser-os.git)
#   ACR_REGISTRY     ACR host (default: parserosacr.azurecr.io)
#   IMAGE_NAME       image name (default: parser-os-service)
#   PUSH             "true" to push after build (default: false)
#   GITHUB_PAT       optional PAT for private parser-os repo

set -euo pipefail

ENV_TAG="${1:?usage: $0 <env-tag (e.g. dev)> <parser-os-ref (e.g. v45.2)>}"
PARSER_OS_REF="${2:?usage: $0 <env-tag> <parser-os-ref>}"

PARSER_OS_REPO="${PARSER_OS_REPO:-https://github.com/Purtera-IT/parser-os.git}"
ACR_REGISTRY="${ACR_REGISTRY:-parserosacr.azurecr.io}"
IMAGE_NAME="${IMAGE_NAME:-parser-os-service}"
PUSH="${PUSH:-false}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARENT_DIR="$(dirname "$REPO_ROOT")"

# ─── 1. Resolve the ref to a concrete SHA ─────────────────────────────────
echo "→ resolving parser-os ref '$PARSER_OS_REF' to SHA..."
RESOLVED_SHA=$(
  GIT_TERMINAL_PROMPT=0 git ls-remote "$PARSER_OS_REPO" "$PARSER_OS_REF" \
    | awk '{print $1}' | head -n 1
)
if [[ -z "$RESOLVED_SHA" ]]; then
  # ls-remote returns nothing for short SHAs — try treating the ref AS a SHA
  if [[ "$PARSER_OS_REF" =~ ^[a-f0-9]{7,40}$ ]]; then
    RESOLVED_SHA="$PARSER_OS_REF"
    echo "  using short SHA as-is: $RESOLVED_SHA"
  else
    echo "✗ FAIL — could not resolve '$PARSER_OS_REF' to a SHA in $PARSER_OS_REPO" >&2
    exit 1
  fi
else
  echo "  $PARSER_OS_REF → $RESOLVED_SHA"
fi

# ─── 2. Re-clone parser-os into a temp build context ──────────────────────
BUILD_CTX=$(mktemp -d)
trap "rm -rf $BUILD_CTX" EXIT

echo "→ cloning parser-os @ $RESOLVED_SHA into $BUILD_CTX/parser-os ..."
GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch "$PARSER_OS_REF" \
  "$PARSER_OS_REPO" "$BUILD_CTX/parser-os" 2>&1 \
  || GIT_TERMINAL_PROMPT=0 git clone "$PARSER_OS_REPO" "$BUILD_CTX/parser-os"

(cd "$BUILD_CTX/parser-os" && git checkout "$RESOLVED_SHA")
ACTUAL_SHA=$(git -C "$BUILD_CTX/parser-os" rev-parse HEAD)

if [[ "$ACTUAL_SHA" != "$RESOLVED_SHA" ]]; then
  echo "✗ FAIL — checked-out SHA $ACTUAL_SHA != resolved $RESOLVED_SHA" >&2
  exit 1
fi
echo "  checked out: $ACTUAL_SHA"

# Copy parser-os-service into the build context too
echo "→ copying parser-os-service into build context..."
cp -r "$REPO_ROOT" "$BUILD_CTX/parser-os-service"
# Strip .git and .venv to keep the layer slim
rm -rf "$BUILD_CTX/parser-os-service/.git" "$BUILD_CTX/parser-os-service/.venv"

# ─── 3. Determine a build label ───────────────────────────────────────────
# If the ref looks like a tag (vN.N or vN.N.N), use it directly; otherwise
# use the short SHA.
if [[ "$PARSER_OS_REF" =~ ^v[0-9]+(\.[0-9]+){1,2}$ ]]; then
  BUILD_LABEL="$PARSER_OS_REF"
else
  BUILD_LABEL="${ACTUAL_SHA:0:7}"
fi
echo "  build label: $BUILD_LABEL"

# ─── 4. docker build (NO CACHE — this is the whole point) ─────────────────
IMAGE_TAG="${ACR_REGISTRY}/${IMAGE_NAME}:${ENV_TAG}"
echo "→ docker build $IMAGE_TAG"
echo "  GIT_SHA=$ACTUAL_SHA"
echo "  BUILD_LABEL=$BUILD_LABEL"

DOCKER_BUILDKIT=1 docker build \
  --no-cache \
  --pull \
  --build-arg "GIT_SHA=$ACTUAL_SHA" \
  --build-arg "BUILD_LABEL=$BUILD_LABEL" \
  --label "org.opencontainers.image.revision=$ACTUAL_SHA" \
  --label "org.opencontainers.image.version=$BUILD_LABEL" \
  -f "$BUILD_CTX/parser-os-service/Dockerfile" \
  -t "$IMAGE_TAG" \
  "$BUILD_CTX"

# Also tag with the SHA so historical revisions are retrievable
SHA_TAG="${ACR_REGISTRY}/${IMAGE_NAME}:sha-${ACTUAL_SHA:0:12}"
docker tag "$IMAGE_TAG" "$SHA_TAG"

echo ""
echo "✓ Built:"
echo "  $IMAGE_TAG"
echo "  $SHA_TAG"

# ─── 5. Verify the env vars actually made it into the image ───────────────
echo ""
echo "→ verifying image contents..."
STAMPED_SHA=$(docker inspect "$IMAGE_TAG" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep '^PARSER_OS_SHA=' | cut -d= -f2)

if [[ "$STAMPED_SHA" != "$ACTUAL_SHA" ]]; then
  echo "✗ FAIL — image env PARSER_OS_SHA='$STAMPED_SHA' != built-from '$ACTUAL_SHA'" >&2
  exit 1
fi
echo "  ✓ PARSER_OS_SHA env = $STAMPED_SHA"

STAMPED_LABEL=$(docker inspect "$IMAGE_TAG" \
  --format '{{index .Config.Labels "org.opencontainers.image.version"}}')
echo "  ✓ image label version = $STAMPED_LABEL"

# ─── 6. Push if requested ─────────────────────────────────────────────────
if [[ "$PUSH" == "true" ]]; then
  echo ""
  echo "→ pushing to $ACR_REGISTRY ..."
  # Caller is expected to have already run `az acr login -n parserosacr`
  docker push "$IMAGE_TAG"
  docker push "$SHA_TAG"
  echo "  ✓ pushed"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "Build complete."
echo "  Image:        $IMAGE_TAG"
echo "  parser-os:    $ACTUAL_SHA ($BUILD_LABEL)"
echo "  Push?         $PUSH"
echo ""
echo "Next:"
if [[ "$PUSH" == "true" ]]; then
  echo "  az containerapp update -n parser-os-service-dev-eus2 ..."
  echo "  curl <service>/v1/version | jq .parser_os_sha"
  echo "  # MUST equal: $ACTUAL_SHA"
else
  echo "  Set PUSH=true to also push, OR run docker push manually."
fi
echo "═══════════════════════════════════════════════════════════════════════"
