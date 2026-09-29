"""v45.2 async compile routes.

Production-ready alternative to the sync `/v1/compile` and
`/v1/orbitbrief/rebuild-latest` endpoints — instead of running the 8-15 min
compile inside the HTTP request (which OOMs the container and times out on
ingress), enqueue a message and let `parser-os-worker` (Container Apps Job)
process it.

Adds:
  POST /v1/compile/async        — enqueue, return 202 + compile_id
  GET  /v1/compile/status/{id}  — read status blob written by the worker

The existing /v1/compile and /v1/orbitbrief/rebuild-latest routes are left
intact for backward compat.  Migration path: new callers use /v1/compile/async
and poll for completion.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.parse import unquote, urlsplit

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueServiceClient
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from parser_os_service.server import auth


router = APIRouter(tags=["compile-async"])


# ─── Config ─────────────────────────────────────────────────────────────


ACCOUNT_NAME = os.environ.get("AZURE_STORAGE_ACCOUNT", "purpulsedevstg01")
QUEUE_NAME = os.environ.get(
    "PARSER_OS_COMPILE_QUEUE", "parser-os-compile-jobs"
)
# v59: interactive re-parses default to the PRIORITY queue, which the worker
# drains first — so a UI Re-parse never waits behind a bulk/batch backlog. Bulk
# callers MUST pass priority=false to take the normal lane (else they'd flood the
# fast lane and re-create the starvation problem).
PRIORITY_QUEUE_NAME = os.environ.get(
    "PARSER_OS_COMPILE_QUEUE_PRIORITY", "parser-os-compile-jobs-priority"
)
BLOB_CONTAINER = os.environ.get(
    "AZURE_STORAGE_BLOB_CONTAINER", "orbitbrief-artifacts"
)
# Connection string takes precedence (same pattern as orbitbrief-core-worker).
# Falls back to managed identity via DefaultAzureCredential if not set, but
# that path requires Storage Queue/Blob Data Contributor role on the storage
# account — which we don't always grant.
CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or \
    os.environ.get("ORBITBRIEF_ARTIFACTS_CONNECTION_STRING")


_cred: DefaultAzureCredential | None = None
_queue_service: QueueServiceClient | None = None
_blob_service: BlobServiceClient | None = None


def _get_queue_client(queue_name: str = QUEUE_NAME):
    global _cred, _queue_service
    if _queue_service is None:
        if CONNECTION_STRING:
            _queue_service = QueueServiceClient.from_connection_string(
                CONNECTION_STRING
            )
        else:
            _cred = _cred or DefaultAzureCredential()
            _queue_service = QueueServiceClient(
                account_url=f"https://{ACCOUNT_NAME}.queue.core.windows.net",
                credential=_cred,
            )
    return _queue_service.get_queue_client(queue_name)


def _get_blob_service():
    global _cred, _blob_service
    if _blob_service is None:
        if CONNECTION_STRING:
            _blob_service = BlobServiceClient.from_connection_string(
                CONNECTION_STRING
            )
        else:
            _cred = _cred or DefaultAzureCredential()
            _blob_service = BlobServiceClient(
                account_url=f"https://{ACCOUNT_NAME}.blob.core.windows.net",
                credential=_cred,
            )
    return _blob_service


# ─── Change-detection (v60) ─────────────────────────────────────────────


def _artifact_key(manifest: dict) -> str:
    """Stable fingerprint of a deal's input artifacts — the sorted set of
    content hashes. Identical inputs → identical key. MUST match the worker's
    computation (parser_os_worker.main writes the record with the same key)."""
    shas = sorted(
        str(a.get("content_sha256") or "")
        for a in (manifest.get("artifacts") or [])
    )
    return hashlib.sha256("\n".join(shas).encode("utf-8")).hexdigest()


def _blob_container_and_path(blob_url: str) -> tuple[str, str]:
    """Split an absolute blob URL into (container, blob_path)."""
    parts = urlsplit(blob_url)
    path = unquote(parts.path).lstrip("/")
    container, _, blob_path = path.partition("/")
    return container, blob_path


def _unchanged_since_last_compile(deal_id: str, manifest_blob_url: str) -> str | None:
    """Return the prior compile_id when this deal's artifacts are byte-identical
    to the last successful compile (so the new compile would be redundant), else
    None. Fails OPEN — any error returns None so we compile rather than wrongly
    skip."""
    try:
        blob = _get_blob_service()
        container, path = _blob_container_and_path(manifest_blob_url)
        manifest = json.loads(
            blob.get_blob_client(container=container, blob=path)
            .download_blob().readall()
        )
        incoming = _artifact_key(manifest)
        rec = json.loads(
            blob.get_blob_client(
                container=BLOB_CONTAINER,
                blob=f"deals/{deal_id}/orbitbrief/latest/compile-idempotency.json",
            ).download_blob().readall()
        )
        # Keyed on the RUN SCOPE too, not the artifact set alone. A cut compile
        # (documents up to a date) and a full one are different products of the
        # same deal; comparing only artifact_key let each stand in for the other.
        # After a cut compile, an explicit "All data" request on a deal whose
        # full corpus equals the cut set was skipped as "unchanged" and handed
        # back the cut -- the user's choice silently ignored. And because this
        # check runs BEFORE anything is queued, the worker's own scope-aware
        # idempotency never saw the request. The worker writes `as_of` into the
        # record; the manifest carries the requested scope in context.as_of.
        # A record with no as_of (legacy) matches a full run only.
        rec_scope = _norm_scope(rec.get("as_of"))
        want_scope = _norm_scope((manifest.get("context") or {}).get("as_of"))
        if (
            rec.get("artifact_key") == incoming
            and rec.get("compile_id")
            and rec_scope == want_scope
        ):
            return str(rec["compile_id"])
    except Exception:
        return None
    return None


def _norm_scope(v: object) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


#: Trigger kinds that mean a person is waiting for this compile.
_MANUAL_KINDS = {"manual", "reparse", "ui", "interactive"}

#: Kinds that cannot yet be told apart, and so keep the lane they have today.
#:
#: `deal_artifact_finalize` is what EVERY caller sends right now -- the UI
#: Re-parse button and the four-hourly timer alike. Routing it to the bulk lane
#: on the strength of its name would put a waiting person behind a backlog and
#: call that a fix. It stays interactive until the callers say which they are;
#: the moment the UI stamps "manual" and the timers stamp "timer", this set is
#: deleted and the split becomes real.
_AMBIGUOUS_LEGACY_KINDS = {"deal_artifact_finalize", ""}


def _trigger_kind(body: "CompileAsyncBody") -> str:
    """The trigger kind this compile declared, normalised.

    Falls back to the deprecated `priority` flag when no trigger is given, so a
    caller that has not been updated keeps the lane it used to get. A caller
    that gives neither is treated as automated — the safe default, because an
    unannounced caller is a timer far more often than it is a person.
    """
    t = body.trigger or {}
    kind = str(t.get("kind") or "").strip().lower()
    if kind:
        return kind
    if body.priority is True:
        return "manual"          # legacy caller that asked for the fast lane
    if body.priority is False:
        return "automated"       # legacy caller that asked for the bulk lane
    # Nothing declared at all. `priority` used to default to True, so silence
    # meant the fast lane; keep it there rather than quietly demoting a caller
    # that has not been updated. "" is ambiguous-legacy, not automated.
    return ""


# ─── Async enqueue ──────────────────────────────────────────────────────


class CompileAsyncBody(BaseModel):
    compile_id: str = Field(..., min_length=1)
    deal_id: str = Field(..., min_length=1)
    manifest_blob_url: str = Field(..., min_length=1)
    domain_pack: str | None = None
    compile_options: dict[str, Any] | None = None
    # Who asked for this compile, and why. The lane is DERIVED from it below.
    #
    # Every compile has always arrived carrying trigger.kind
    # "deal_artifact_finalize" — a manual Re-parse click and a four-hourly timer
    # were literally the same message. So nothing could count them, gate on
    # them, or tell them apart in the queue, and "random parses keep appearing"
    # stayed a feeling rather than a number.
    #
    #   manual                    -> priority lane  (a person is waiting)
    #   timer / backfill / other  -> bulk lane      (nobody is waiting)
    #
    # Anything unrecognised takes the bulk lane: a caller that does not say it
    # is interactive is not.
    trigger: dict[str, Any] | None = None

    # DEPRECATED — honoured only when `trigger` is absent, so callers outside
    # these repos keep working. It defaulted to True, which is why timer floods
    # have been landing in the FAST lane and starving the interactive re-parses
    # that lane exists to protect.
    priority: bool | None = None
    force: bool = False  # back-compat alias; force=true always runs (never skips)
    # v60.1: change-detection (skip when artifacts are unchanged) is now OPT-IN.
    # A manual UI Re-parse — and any default caller — ALWAYS runs and repopulates,
    # because a deliberate Re-parse click that silently does nothing is worse than
    # a redundant compile. Bulk/batch tooling can set skip_if_unchanged=true to
    # dedup unchanged deals. (The ~4-hourly auto-finalize floods are killed at the
    # source instead, by disabling ORBITBRIEF_AUTO_CORE_COMPILE on the PM API.)
    skip_if_unchanged: bool = False


class CompileAsyncResponse(BaseModel):
    compile_id: str
    deal_id: str
    status: str = "queued"
    status_url: str
    message: str = (
        "Enqueued for processing.  Poll /v1/compile/status/{compile_id} "
        "until status is 'completed' or 'failed'."
    )


@router.post(
    "/v1/compile/async",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CompileAsyncResponse,
)
def compile_async(
    body: CompileAsyncBody,
    _: str = Depends(auth.verify_bearer),
) -> CompileAsyncResponse:
    """Enqueue a compile job.  Returns 202 immediately — the worker picks it
    up asynchronously and writes status to blob.

    For large workloads (Pack 02 size and bigger), prefer this over the sync
    /v1/orbitbrief/rebuild-latest endpoint.  The sync one is kept for
    backward compat but will OOM-kill the container on real workloads.
    """
    # v62: change-detection is ON BY DEFAULT — per product rule, a compile runs ONLY
    # when the deal is new (no prior) or its documents changed. An unchanged deal
    # (any caller, incl. a manual Re-parse) resolves to the current results without
    # re-running. force=true bypasses (re-validate after a parser change).
    if not body.force:
        prior = _unchanged_since_last_compile(body.deal_id, body.manifest_blob_url)
        if prior is not None:
            return CompileAsyncResponse(
                compile_id=prior,
                deal_id=body.deal_id,
                status="skipped_unchanged",
                status_url=f"/v1/compile/status/{prior}",
                message=(
                    "Artifacts unchanged since the last compile — skipped "
                    "(results are current). Pass force=true to recompile."
                ),
            )

    lane_kind = _trigger_kind(body)
    interactive = lane_kind in _MANUAL_KINDS or lane_kind in _AMBIGUOUS_LEGACY_KINDS
    target_queue = PRIORITY_QUEUE_NAME if interactive else QUEUE_NAME
    try:
        queue_client = _get_queue_client(target_queue)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Queue not reachable: {type(exc).__name__}: {exc}",
        ) from exc

    msg = {
        "compile_id": body.compile_id,
        "deal_id": body.deal_id,
        "manifest_blob_url": body.manifest_blob_url,
        # Carried so the worker can record it and the queue UI can show WHY a
        # compile is running and WHO is waiting. Without this, every row in the
        # queue looks identical and a flood is indistinguishable from work
        # somebody asked for.
        "trigger": {
            "kind": lane_kind,
            "by": str((body.trigger or {}).get("by") or ""),
            "lane": "priority" if interactive else "bulk",
        },
    }
    if body.domain_pack:
        msg["domain_pack"] = body.domain_pack
    if body.compile_options:
        msg["compile_options"] = body.compile_options
    # v61: carry force into the message so parser-os-worker's change-detection
    # bypasses for a deliberate re-parse (the timer-driven floods never set it).
    if body.force:
        msg["force"] = True

    try:
        queue_client.send_message(json.dumps(msg))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to enqueue: {type(exc).__name__}: {exc}",
        ) from exc

    return CompileAsyncResponse(
        compile_id=body.compile_id,
        deal_id=body.deal_id,
        status="queued",
        status_url=f"/v1/compile/status/{body.compile_id}",
    )


# ─── Status polling ─────────────────────────────────────────────────────


@router.get("/v1/compile/status/{compile_id}")
def compile_status(
    compile_id: str,
    deal_id: str,
    _: str = Depends(auth.verify_bearer),
) -> dict[str, Any]:
    """Read the status blob that the worker writes during compile.

    Status flow:
      queued   → first write when worker picks up the message
      running  → during compile + projection
      completed → envelope uploaded successfully
      failed   → exception (with error + traceback in body)

    Both compile_id and deal_id are required because status blobs live at
    deals/{deal_id}/parser-jobs/{compile_id}.json and we don't keep a
    deal-lookup index.
    """
    try:
        blob_service = _get_blob_service()
        path = f"deals/{deal_id}/parser-jobs/{compile_id}.json"
        client = blob_service.get_blob_client(container=BLOB_CONTAINER, blob=path)
        try:
            data = client.download_blob().readall()
            return json.loads(data)
        except Exception:
            # No status blob yet means the worker hasn't picked it up.
            # That's "queued" from the caller's perspective.
            return {
                "compile_id": compile_id,
                "deal_id": deal_id,
                "status": "queued",
                "message": "Worker has not yet started this job.",
            }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Status lookup failed: {type(exc).__name__}: {exc}",
        ) from exc
