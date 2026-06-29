"""FastAPI ASGI entrypoint for parser-os-service."""

from __future__ import annotations

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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    # TODO(PR8): bearer middleware + Entra validation for internal ingress.
    yield
    await postgres_client.close_pool()


app = FastAPI(title="parser-os-service", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
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
