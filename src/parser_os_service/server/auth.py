"""Bearer validation for internal service-to-service calls.

PR8 may replace this with Entra-validated middleware; v1 uses a shared secret.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


def verify_bearer(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("BANG_INTERNAL_BEARER", "").strip()
    if not token:
        return
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    got = authorization.removeprefix("Bearer ").strip()
    if got != token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="invalid bearer token")
