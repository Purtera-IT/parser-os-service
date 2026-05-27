#!/usr/bin/env bash
# v45.2 — diagnose why ollama-mac-proxy can't reach Mac Ollama.
#
# Symptom from tonight's dev verify:
#   polish_stage: 0 polished / 113 fallback
#   proxy /healthz → 200 OK
#   proxy /api/tags → 500 Internal Server Error
#
# That means: the proxy container is alive, but its upstream — Mac Ollama via
# Tailscale — is unreachable.  This script tests each link in the chain so
# the failure point is obvious.
#
# Usage:
#   scripts/diagnose_ollama_proxy.sh
#     [--proxy-url https://ollama-mac-proxy-dev-eus2.<region>.azurecontainerapps.io]
#     [--mac-ssh "user@mac-host"]   # if you can SSH into the Mac
#
# Run from the Mac if possible — that lets us test localhost:11434 directly.

set -euo pipefail

PROXY_URL="${PROXY_URL:-https://ollama-mac-proxy-dev-eus2.whitehill-a3348ba5.eastus2.azurecontainerapps.io}"
MAC_SSH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --proxy-url)  PROXY_URL="$2"; shift 2 ;;
    --mac-ssh)    MAC_SSH="$2"; shift 2 ;;
    -h|--help)    grep -E '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

PASS=0
FAIL=0
WARN=0

check() {
  local name="$1"
  local result="$2"  # "pass" / "fail" / "warn"
  local detail="${3:-}"
  case "$result" in
    pass) echo "  ✓ $name"; ((PASS++)) ;;
    warn) echo "  ⚠ $name — $detail"; ((WARN++)) ;;
    fail) echo "  ✗ $name — $detail"; ((FAIL++)) ;;
  esac
}

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "  ollama-mac-proxy diagnostic"
echo "  proxy: $PROXY_URL"
echo "═══════════════════════════════════════════════════════════════════════"

# ─── 1. Proxy /healthz ────────────────────────────────────────────────────
echo ""
echo "── Layer 1: Proxy container alive ──"
HEALTHZ=$(curl -sS -m 5 -o /dev/null -w "%{http_code}" "$PROXY_URL/healthz" || echo "000")
if [[ "$HEALTHZ" == "200" ]]; then
  check "proxy /healthz returns 200" pass
else
  check "proxy /healthz returns 200" fail "got $HEALTHZ — proxy container is unhealthy"
fi

# ─── 2. Proxy /api/tags (the broken one) ──────────────────────────────────
echo ""
echo "── Layer 2: Proxy → Mac Ollama tunnel ──"
TAGS_CODE=$(curl -sS -m 10 -o /tmp/_tags_body -w "%{http_code}" "$PROXY_URL/api/tags" || echo "000")
TAGS_BODY=$(cat /tmp/_tags_body 2>/dev/null || echo "")

if [[ "$TAGS_CODE" == "200" ]]; then
  MODEL_COUNT=$(echo "$TAGS_BODY" | jq -r '.models | length' 2>/dev/null || echo "?")
  check "proxy /api/tags returns 200" pass
  check "Ollama reports $MODEL_COUNT models" pass

  # Check for the 3 critical models
  for model in qwen3:14b qwen3-embedding:8b qwen2.5vl:7b; do
    if echo "$TAGS_BODY" | jq -e ".models[] | select(.name | startswith(\"$model\"))" >/dev/null 2>&1; then
      check "model present: $model" pass
    else
      check "model present: $model" fail "not loaded on Mac — run 'ollama pull $model'"
    fi
  done
else
  check "proxy /api/tags returns 200" fail "got $TAGS_CODE"
  echo ""
  echo "  Response body (first 500 chars):"
  echo "  $(echo "$TAGS_BODY" | head -c 500)"
  echo ""
  echo "  → THIS is the failure.  Likely causes:"
  echo "    a) Mac Ollama not running         (ssh mac → curl localhost:11434/api/tags)"
  echo "    b) Tailscale down on Mac          (ssh mac → tailscale status)"
  echo "    c) Proxy upstream URL wrong       (check OLLAMA_UPSTREAM env on proxy Container App)"
  echo "    d) Mac firewall blocking Tailnet  (System Settings → Privacy → Firewall)"
fi

# ─── 3. End-to-end model call ─────────────────────────────────────────────
if [[ "$TAGS_CODE" == "200" ]]; then
  echo ""
  echo "── Layer 3: End-to-end LLM call ──"
  echo "  attempting generate with qwen3:14b (timeout 60s)..."
  GEN_RESPONSE=$(
    curl -sS -m 60 -X POST "$PROXY_URL/api/generate" \
      -H "Content-Type: application/json" \
      -d '{"model":"qwen3:14b","prompt":"reply with the word OK only","stream":false}' \
      | jq -r '.response // "ERROR"' 2>/dev/null || echo "ERROR"
  )
  if [[ "$GEN_RESPONSE" =~ OK ]]; then
    check "qwen3:14b generate returns expected answer" pass
  else
    check "qwen3:14b generate returns expected answer" warn \
      "got '$GEN_RESPONSE' — model loaded but slow/unhealthy"
  fi
fi

# ─── 4. Mac-side checks (if SSH available) ────────────────────────────────
if [[ -n "$MAC_SSH" ]]; then
  echo ""
  echo "── Layer 4: Mac-local checks via SSH ──"

  LOCAL_TAGS=$(ssh -o ConnectTimeout=5 "$MAC_SSH" "curl -sS -m 3 -o /dev/null -w '%{http_code}' http://localhost:11434/api/tags" || echo "ssh-fail")
  if [[ "$LOCAL_TAGS" == "200" ]]; then
    check "Mac localhost:11434/api/tags returns 200" pass
  elif [[ "$LOCAL_TAGS" == "ssh-fail" ]]; then
    check "SSH to Mac succeeds" fail "can't reach $MAC_SSH"
  else
    check "Mac localhost:11434/api/tags returns 200" fail "got $LOCAL_TAGS — start Ollama: 'ollama serve'"
  fi

  TS_STATUS=$(ssh -o ConnectTimeout=5 "$MAC_SSH" "tailscale status --json | jq -r .BackendState" || echo "ssh-fail")
  if [[ "$TS_STATUS" == "Running" ]]; then
    check "Tailscale backend state: Running" pass
  else
    check "Tailscale backend state" fail "got '$TS_STATUS' — run 'tailscale up' on Mac"
  fi

  # Did the proxy see this Mac as a tailnet peer?
  TS_PEERS=$(ssh -o ConnectTimeout=5 "$MAC_SSH" "tailscale status | grep -c proxy" || echo "0")
  if [[ "${TS_PEERS//[^0-9]/}" -gt 0 ]]; then
    check "Tailscale peer 'proxy' visible from Mac" pass
  else
    check "Tailscale peer 'proxy' visible from Mac" warn \
      "no peer named *proxy* — proxy may use a different tailnet name"
  fi
else
  echo ""
  echo "── Layer 4: Mac-local checks ── (SKIPPED — no --mac-ssh)"
  echo "  pass --mac-ssh user@host to test the Mac side"
fi

# ─── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "  Summary: $PASS pass / $WARN warn / $FAIL fail"
echo "═══════════════════════════════════════════════════════════════════════"

if (( FAIL > 0 )); then
  echo ""
  echo "Fix path (most likely):"
  echo "  1. SSH into Mac Studio"
  echo "  2. ollama serve   (or check Activity Monitor for ollama process)"
  echo "  3. tailscale up   (if 'tailscale status' shows Stopped)"
  echo "  4. Confirm: curl http://localhost:11434/api/tags  →  200 with models"
  echo "  5. Then re-run this script — proxy /api/tags should return 200"
  echo "  6. Trigger a fresh recompile via orbitbrief/recompile endpoint"
  echo "  7. Check polish_stage.items_polished > 0 in pm-handoff.json"
  exit 1
fi

exit 0
