"""GET /v1/version — runtime version surface.

Reports parser-os + parser-os-service SHA/version + envelope schema
version. Lets the Azure deploy pipeline (and Purpulse Platform)
verify what's actually running vs what the contracts repo expects.

No auth required — purely informational.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["meta"])


def _git_sha(repo_dir: Path | None) -> str | None:
    """Best-effort short SHA from a git checkout. Returns None when
    the dir isn't a git repo or git isn't available."""
    if not repo_dir or not repo_dir.exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, check=False, timeout=2,
        )
        sha = (out.stdout or "").strip()
        return sha or None
    except Exception:
        return None


def _parser_os_module_path() -> Path | None:
    """Locate the parser-os checkout via the installed module."""
    try:
        import app.core.compiler as _c  # type: ignore
        return Path(_c.__file__).resolve().parents[2]
    except Exception:
        return None


def _orbitbrief_core_module_path() -> Path | None:
    try:
        import orbitbrief_core as _ob  # type: ignore
        return Path(_ob.__file__).resolve().parents[2]
    except Exception:
        return None


@router.get("/v1/version")
def version_endpoint() -> dict[str, Any]:
    parser_os_dir = _parser_os_module_path()
    orbitbrief_core_dir = _orbitbrief_core_module_path()
    service_dir = Path(__file__).resolve().parents[4]

    info: dict[str, Any] = {
        "service": "parser-os-service",
        "service_sha": _git_sha(service_dir)
            or os.environ.get("PARSER_OS_SERVICE_BUILD_SHA")
            or "unknown",
        "parser_os_sha": _git_sha(parser_os_dir)
            or os.environ.get("PARSER_OS_BUILD_SHA")
            or "unknown",
        "orbitbrief_core_sha": _git_sha(orbitbrief_core_dir)
            or os.environ.get("ORBITBRIEF_CORE_BUILD_SHA")
            or "unknown",
    }

    # Report parser-os contract versions
    try:
        from app.core.schemas import COMPILER_VERSION, SCHEMA_VERSION
        info["compiler_version"] = str(COMPILER_VERSION)
        info["schema_version"] = str(SCHEMA_VERSION)
    except Exception:
        info["compiler_version"] = "unknown"
        info["schema_version"] = "unknown"

    try:
        from app.core.orbitbrief_envelope import ENVELOPE_SCHEMA_VERSION
        info["envelope_schema_version"] = str(ENVELOPE_SCHEMA_VERSION)
    except Exception:
        info["envelope_schema_version"] = "unknown"

    # SowSmith (optional dep)
    try:
        from sowsmith import SOW_VERSION  # type: ignore
        info["sow_version"] = str(SOW_VERSION)
    except Exception:
        info["sow_version"] = "not installed"

    return info
