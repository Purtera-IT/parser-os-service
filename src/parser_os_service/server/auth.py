"""Bearer validation for internal service-to-service calls.

PR8 may replace this with Entra-validated middleware; v1 uses a shared secret.

Why this fails CLOSED
---------------------
It used to fail open::

    token = os.environ.get("BANG_INTERNAL_BEARER", "").strip()
    if not token:
        return                      # no token configured -> no auth at all

So a deploy that dropped the variable, or set it to the empty string, silently
removed authentication from every endpoint on this service -- including the one
that triggers compiles and the ones that read a deal's parse output. Nothing
logged, nothing failed, and the service went on reporting healthy. The only way
to notice was to read this file.

An unconfigured secret is a misconfiguration, not a permission. It now refuses
every request with 503 and says why, which turns a silent exposure into an
obvious outage -- the failure you want, because somebody fixes it in minutes.

Running open is still possible, but it has to be asked for:
``PARSER_OS_ALLOW_UNAUTHENTICATED=1``. That is for local development and the
test suite -- a deliberate line in a config, rather than the default that
arrives when something goes wrong.
"""

from __future__ import annotations

import logging
import os
import secrets

from fastapi import Header, HTTPException, status

log = logging.getLogger(__name__)

_TRUTHY = ("1", "true", "yes", "on")

#: Warn once per process, not per request: an open service should say so in the
#: logs without burying everything else in the stream.
_warned_open = False


def _open_mode() -> bool:
    return os.environ.get(
        "PARSER_OS_ALLOW_UNAUTHENTICATED", ""
    ).strip().lower() in _TRUTHY


def verify_bearer(authorization: str | None = Header(default=None)) -> None:
    global _warned_open
    token = os.environ.get("BANG_INTERNAL_BEARER", "").strip()

    if not token:
        if _open_mode():
            if not _warned_open:
                log.warning(
                    "parser-os-service is running UNAUTHENTICATED: "
                    "BANG_INTERNAL_BEARER is unset and "
                    "PARSER_OS_ALLOW_UNAUTHENTICATED is on. Every endpoint is "
                    "open, including the compile trigger."
                )
                _warned_open = True
            return
        # Refuse, loudly. A service that cannot authenticate must not serve.
        log.error(
            "BANG_INTERNAL_BEARER is not configured; refusing all requests. "
            "Set the secret, or set PARSER_OS_ALLOW_UNAUTHENTICATED=1 to run "
            "open on purpose."
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service auth is not configured",
        )

    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")

    got = authorization.removeprefix("Bearer ").strip()
    # compare_digest rather than `!=`: a plain string compare returns as soon as
    # two bytes differ, so its timing leaks how many leading characters were
    # right -- a shared secret handed over one byte at a time.
    if not secrets.compare_digest(got, token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="invalid bearer token")
