"""Внутренние эндпоинты под `X-Internal-Token` (constant-time сравнение, без Keycloak).

Сюда добавляются джобы планировщика (`/internal/jobs/*`), импорт кандидатов и пар жюри
(`/internal/candidates/bulk`, `/internal/pairs/*`), статистика и демо-сброс (PLAN.md §7.5).
Сейчас: `POST /internal/ping` (проверка токена и формата ответа) и `GET /internal/llm/usage`
(сводка журнала LLM — та же функция, что у `GET /llm/usage`, но только по токену).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import request_id_var
from app.core.security import require_internal_token
from app.modules.llm.service import usage_report

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


@router.post("/ping", summary="Проверка внутреннего токена")
async def ping() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "llm_provider": settings.llm_provider,
        "request_id": request_id_var.get() or None,
    }


@router.get("/llm/usage", summary="Сводка вызовов LLM (провайдер, дневные счётчики, журнал)")
async def llm_usage() -> dict[str, Any]:
    return usage_report()


@router.post("/demo/reset", summary="Пересев демо-данных (жюри): удалить и засеять заново")
async def demo_reset(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    # импорт локальный: модуль employers тянет цепочку сидов, она не нужна остальным ручкам
    from app.modules.employers.demo import reset_demo

    return await reset_demo(session)
