"""Health-пробы (без аутентификации).

Контракт API — `GET /health/live`, `GET /health/ready` (PLAN.md §7.5); зарегистрированы и
под `/api/v1/health/*` (фронт-прокси и Caddy маршрутизируют на backend только `/api`),
и как `/healthz`, `/readyz` — для healthcheck'ов docker compose.

Liveness никогда не проверяет внешние зависимости (иначе падение PG каскадно
перезапускает контейнер); зависимостями управляет readiness.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.db import get_engine

router = APIRouter(tags=["health"], include_in_schema=False)

_LIVE_PATHS = ("/healthz", "/health/live", "/api/v1/health/live")
_READY_PATHS = ("/readyz", "/health/ready", "/api/v1/health/ready")


async def _select_one() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_postgres() -> bool:
    try:
        async with asyncio.timeout(3.0):
            await _select_one()
        return True
    except Exception:  # noqa: BLE001 — readiness должен пережить любую ошибку зависимости
        return False


async def liveness() -> dict[str, str]:
    return {"status": "ok"}


async def readiness(response: Response) -> dict[str, Any]:
    pg_ok = await _check_postgres()
    if not pg_ok:
        response.status_code = 503
    return {
        "status": "ok" if pg_ok else "unavailable",
        "checks": {"postgres": pg_ok},
    }


for path in _LIVE_PATHS:
    router.get(path)(liveness)
for path in _READY_PATHS:
    router.get(path)(readiness)
