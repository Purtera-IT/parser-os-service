"""GET /v1/health."""

from __future__ import annotations

import time

from fastapi import APIRouter

try:
    from app.core.schemas import COMPILER_VERSION as _PARSER_OS_VER
except Exception:  # pragma: no cover
    _PARSER_OS_VER = "unknown"

from parser_os_service.server.projector import PROJECTOR_VERSION

_START = time.monotonic()

router = APIRouter(tags=["health"])


@router.get("/v1/health")
def health() -> dict[str, str | float]:
    return {
        "status": "ok",
        "parser_os_version": str(_PARSER_OS_VER),
        "projector_version": PROJECTOR_VERSION,
        "uptime_seconds": time.monotonic() - _START,
    }
