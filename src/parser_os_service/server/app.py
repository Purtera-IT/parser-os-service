"""FastAPI ASGI entrypoint for parser-os-service."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from collections.abc import AsyncGenerator

from fastapi import FastAPI

from parser_os_service.server import postgres_client
from parser_os_service.server.routes import (
    compile,
    compile_async,
    health,
    jobs,
    orbitbrief_latest,
    version,
)


def _configure_logging() -> None:
    """Let the compile speak.

    This service runs the parser-os compile, and nothing here ever configured
    logging — so the root logger sat at WARNING with no handler and every
    `logging.getLogger("app.core.…")` line the pipeline emits went nowhere.
    From outside, a stage that ran and a stage that was switched off looked
    identical, which is exactly how a fusion pass stayed disabled through six
    attempts to fix what it was doing.

    stdout, because that is what the container log stream reads. Level is
    tunable via PARSER_OS_LOG_LEVEL; INFO by default, which is where the
    pipeline's own stage lines are written.

    Uvicorn configures its own loggers and leaves the root alone, so this adds
    a handler rather than replacing anything, and does nothing if one is
    already installed (a test harness, or a host that configured logging
    first).
    """
    root = logging.getLogger()
    level = getattr(
        logging, os.environ.get("PARSER_OS_LOG_LEVEL", "INFO").strip().upper(), logging.INFO
    )
    root.setLevel(level)
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(handler)


_configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    # TODO(PR8): bearer middleware + Entra validation for internal ingress.
    yield
    await postgres_client.close_pool()


app = FastAPI(title="parser-os-service", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)

# The synchronous POST /v1/compile is OFF by default.
#
# It runs a whole compile inside this container: no queue, no worker, no slot,
# no per-deal budget, and none of the status records the queue UI reads. So the
# same deal could be compiled two ways, in two environments, under two timeout
# regimes, and a result would depend on which door it came through. Its own
# docstring already warned that it "will OOM-kill the container on real
# workloads".
#
# One way to start a compile, or the pipeline cannot be said to do the same
# thing every time.
#
# Gated rather than deleted: a caller outside these repos may still reach for
# it, PARSER_OS_ENABLE_SYNC_COMPILE=1 brings it back in one variable, and a 404
# in the meantime names that caller instead of hiding it.
if os.environ.get("PARSER_OS_ENABLE_SYNC_COMPILE", "").strip().lower() in (
    "1", "true", "yes", "on",
):
    app.include_router(compile.router)

# v45.2: async compile path (enqueue-and-poll, runs in parser-os-worker)
app.include_router(compile_async.router)
app.include_router(jobs.router)
app.include_router(orbitbrief_latest.router)
app.include_router(version.router)

# PM correction loop: mount parser-os's feedback router (parser-os installs as the
# top-level `app` package). Exposes /projects/:id/feedback/{rule,complaint,correction}
# so the in-brief CorrectionChip → Azure Function → here → FeedbackStore loop closes.
# Best-effort: never let the feedback router break service startup.
try:
    from app.api.routes_feedback import router as _parser_os_feedback_router

    app.include_router(_parser_os_feedback_router)
except Exception:  # pragma: no cover - feedback loop is additive, never fatal
    pass
