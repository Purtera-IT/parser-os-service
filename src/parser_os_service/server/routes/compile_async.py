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

import json
import os
from typing import Any

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


def _get_queue_client():
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
    return _queue_service.get_queue_client(QUEUE_NAME)


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


# ─── Async enqueue ──────────────────────────────────────────────────────


class CompileAsyncBody(BaseModel):
    compile_id: str = Field(..., min_length=1)
    deal_id: str = Field(..., min_length=1)
    manifest_blob_url: str = Field(..., min_length=1)
    domain_pack: str | None = None
    compile_options: dict[str, Any] | None = None


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
    try:
        queue_client = _get_queue_client()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Queue not reachable: {type(exc).__name__}: {exc}",
        ) from exc

    msg = {
        "compile_id": body.compile_id,
        "deal_id": body.deal_id,
        "manifest_blob_url": body.manifest_blob_url,
    }
    if body.domain_pack:
        msg["domain_pack"] = body.domain_pack
    if body.compile_options:
        msg["compile_options"] = body.compile_options

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
