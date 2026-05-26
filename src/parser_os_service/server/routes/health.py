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


def _health_payload() -> dict[str, str | float]:
    return {
        "status": "ok",
        "parser_os_version": str(_PARSER_OS_VER),
        "projector_version": PROJECTOR_VERSION,
        "uptime_seconds": time.monotonic() - _START,
    }


@router.get("/v1/health")
def health() -> dict[str, str | float]:
    return _health_payload()


@router.get("/v1/health/live")
def health_live() -> dict[str, str | float]:
    """K8s-style liveness probe alias."""
    return _health_payload()


@router.get("/v1/health/ready")
def health_ready() -> dict[str, str | float]:
    """K8s-style readiness probe alias.

    Referenced in `contracts/DEVELOPER_INTEGRATION_PLAYBOOK.md` §10.
    Returns the same payload as `/v1/health` — no separate readiness
    check beyond the process being up + the projector module loaded.
    """
    return _health_payload()
