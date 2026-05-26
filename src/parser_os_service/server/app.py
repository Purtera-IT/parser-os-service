"""FastAPI ASGI entrypoint for parser-os-service."""

from __future__ import annotations

from contextlib import asynccontextmanager

from collections.abc import AsyncGenerator

from fastapi import FastAPI

from parser_os_service.server import postgres_client
from parser_os_service.server.routes import compile, health, jobs, orbitbrief_latest, version


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    # TODO(PR8): bearer middleware + Entra validation for internal ingress.
    yield
    await postgres_client.close_pool()


app = FastAPI(title="parser-os-service", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(compile.router)
app.include_router(jobs.router)
app.include_router(orbitbrief_latest.router)
app.include_router(version.router)
