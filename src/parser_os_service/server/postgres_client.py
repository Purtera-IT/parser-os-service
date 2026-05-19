"""Async Postgres pool (runtime persistence is owned by the Function worker in v1)."""

from __future__ import annotations

import os
from typing import Any

from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None


async def get_pool() -> AsyncConnectionPool | None:
    """Return a shared pool when ``DATABASE_URL`` is set; otherwise ``None``."""

    global _pool
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return None
    if _pool is None:
        _pool = AsyncConnectionPool(conninfo=dsn, open=False, kwargs={"autocommit": True})
        await _pool.open()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def health_ping() -> dict[str, Any]:
    """Cheap connectivity check for future readiness probes."""

    pool = await get_pool()
    if pool is None:
        return {"postgres": "skipped", "reason": "DATABASE_URL unset"}
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            row = await cur.fetchone()
    return {"postgres": "ok", "select1": row}
