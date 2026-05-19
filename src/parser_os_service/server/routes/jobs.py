"""GET /v1/jobs/{job_id} — async mode reserved."""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

router = APIRouter(tags=["jobs"])


@router.get("/v1/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    _ = job_id
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={"detail": "async jobs not implemented in v1 (sync compile only)"},
    )
