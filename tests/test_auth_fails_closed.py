"""An unconfigured secret must refuse, not admit.

`verify_bearer` used to return early when BANG_INTERNAL_BEARER was unset, so a
deploy that dropped the variable served every endpoint unauthenticated --
including the compile trigger -- with nothing logged and health still green.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from parser_os_service.server import auth


def test_missing_secret_refuses_instead_of_admitting(monkeypatch):
    monkeypatch.delenv("BANG_INTERNAL_BEARER", raising=False)
    monkeypatch.delenv("PARSER_OS_ALLOW_UNAUTHENTICATED", raising=False)
    with pytest.raises(HTTPException) as exc:
        auth.verify_bearer(authorization=None)
    assert exc.value.status_code == 503
    # 503 rather than 401: the caller did nothing wrong, the service is
    # misconfigured, and the distinction is what gets it fixed.
    assert "not configured" in str(exc.value.detail)


def test_empty_secret_is_treated_as_missing(monkeypatch):
    """`BANG_INTERNAL_BEARER=""` is the shape a bad deploy actually produces."""
    monkeypatch.setenv("BANG_INTERNAL_BEARER", "   ")
    monkeypatch.delenv("PARSER_OS_ALLOW_UNAUTHENTICATED", raising=False)
    with pytest.raises(HTTPException) as exc:
        auth.verify_bearer(authorization="Bearer anything")
    assert exc.value.status_code == 503


def test_open_mode_is_opt_in_and_explicit(monkeypatch):
    monkeypatch.delenv("BANG_INTERNAL_BEARER", raising=False)
    monkeypatch.setenv("PARSER_OS_ALLOW_UNAUTHENTICATED", "1")
    auth._warned_open = False
    assert auth.verify_bearer(authorization=None) is None


def test_a_configured_secret_still_gates(monkeypatch):
    monkeypatch.setenv("BANG_INTERNAL_BEARER", "s3cret")
    monkeypatch.delenv("PARSER_OS_ALLOW_UNAUTHENTICATED", raising=False)

    assert auth.verify_bearer(authorization="Bearer s3cret") is None

    for bad, code in (
        (None, 401),
        ("s3cret", 401),          # no "Bearer " prefix
        ("Bearer wrong", 403),
        ("Bearer s3cre", 403),    # a prefix of the real token
        ("Bearer ", 403),
    ):
        with pytest.raises(HTTPException) as exc:
            auth.verify_bearer(authorization=bad)
        assert exc.value.status_code == code, bad


def test_open_mode_does_not_override_a_configured_secret(monkeypatch):
    """Open mode is a fallback for no secret, never a bypass for a real one."""
    monkeypatch.setenv("BANG_INTERNAL_BEARER", "s3cret")
    monkeypatch.setenv("PARSER_OS_ALLOW_UNAUTHENTICATED", "1")
    with pytest.raises(HTTPException) as exc:
        auth.verify_bearer(authorization="Bearer wrong")
    assert exc.value.status_code == 403
