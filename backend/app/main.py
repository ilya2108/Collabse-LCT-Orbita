"""Точка входа: FastAPI-приложение «Орбита ФСП» (модульный монолит).

Модули — PLAN.md §7.1; все бизнес-роуты под префиксом /api/v1, Swagger — /api/v1/docs.
Фоновых задач в процессе нет: периодические джобы (пересчёт силы, протухание приглашений)
вызываются снаружи через эндпоинты `/internal/jobs/*` под X-Internal-Token (PLAN.md §7.5).

На старте загружаются и проверяются YAML-каталоги (графы навыков и банк шаблонов) и
собирается крипто-сервис ПДн: битый файл или отсутствующий ключ роняют приложение сразу,
а не первым запросом к /skills/*, /assessment/* или /auth/me.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.cache import close_cache
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import RequestIdMiddleware, register_error_handlers
from app.core.health import router as health_router
from app.core.pii import get_pii_crypto
from app.modules.assessment.router import router as assessment_router
from app.modules.assessment.templates import get_catalog as get_template_catalog
from app.modules.auth.router import router as auth_router
from app.modules.candidates.router import router as candidates_router
from app.modules.employers.router import router as employers_router
from app.modules.fsp.router import router as fsp_router
from app.modules.internal.router import router as internal_router
from app.modules.llm.router import router as llm_router
from app.modules.matching.router import router as matching_router
from app.modules.skills.router import router as skills_router
from app.modules.tgbot.router import router as tgbot_router
from app.modules.tma.router import router as tma_router
from app.modules.vacancies.router import router as vacancies_router
from app.skills.loader import get_catalog as get_skills_catalog


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
    )
    log = logging.getLogger("app.startup")
    # ключи ПДн собираются здесь: без них нельзя обслужить ни один аутентифицированный запрос
    get_pii_crypto()
    # каталоги валидируются при загрузке; исключение здесь = приложение не стартует
    skills = get_skills_catalog()
    templates = get_template_catalog()
    log.info(
        "каталоги загружены: специализаций=%d, шаблонов=%d, предупреждений=%d; LLM_PROVIDER=%s",
        len(skills.specializations),
        len(templates.templates),
        len(templates.warnings),
        settings.llm_provider,
    )
    yield
    await close_cache()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="«Орбита ФСП» — API",
        version="0.1.0",
        description=(
            "Платформа обратного подбора ИТ-специалистов (ЛЦТ 2026, спец. трек ФСП): "
            "граф навыков с затухающими подтверждениями, адаптивный тест, объяснимый подбор, "
            "приглашения от работодателя с зарплатой «от–до»."
        ),
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(RequestIdMiddleware)
    # локально фронт ходит через vite-прокси (same-origin); CORS нужен только при прямом
    # обращении к :8000 из браузера и в prod за Caddy не используется
    if settings.app_env != "prod":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.frontend_public_url],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-Id"],
        )
    register_error_handlers(app)

    app.include_router(health_router)

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth_router)
    api.include_router(skills_router)
    api.include_router(candidates_router)
    api.include_router(assessment_router)
    api.include_router(llm_router)
    api.include_router(matching_router)
    api.include_router(employers_router)
    api.include_router(vacancies_router)
    api.include_router(fsp_router)
    api.include_router(tgbot_router)
    api.include_router(tma_router)
    api.include_router(internal_router)
    app.include_router(api)

    return app


app = create_app()
