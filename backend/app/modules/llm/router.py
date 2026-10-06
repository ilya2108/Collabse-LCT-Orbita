"""Роутер модуля `llm` (PLAN.md §7.5): служебные эндпоинты, бизнес-вызовы LLM делают
модули assessment / matching / candidates через `service.LLMService`.

- `GET /llm/usage` — сводка дневных токенов, очереди семафора и последних вызовов;
  доступ по `X-Internal-Token` (джобы, k6, стенд) или администратору (роль `admin`
  в токене Keycloak — по контракту admin назначается только там);
- `POST /llm/dry-run` — прогон промпта на stub для отладки фронта и промптов (admin):
  возвращает отрендеренные сообщения, хэш и результат по схеме, в журнал не пишет.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import forbidden, unauthorized, validation_error
from app.core.security import (
    ROLE_ADMIN,
    TokenClaims,
    decode_token,
    require_roles,
    verify_internal_token,
)
from app.modules.llm.base import LLMError, build_messages
from app.modules.llm.journal import compute_prompt_hash
from app.modules.llm.prompts import LLMPromptError, list_prompts, load_prompt
from app.modules.llm.providers.stub import StubProvider
from app.modules.llm.sanitizer import sanitize_variables
from app.modules.llm.service import usage_report

router = APIRouter(prefix="/llm", tags=["llm"])

_bearer = HTTPBearer(auto_error=False)


async def optional_token_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> TokenClaims | None:
    """Claims токена, если Bearer передан; иначе None (без 401 — решает вызывающая зависимость)."""
    if credentials is None or not credentials.credentials:
        return None
    return await decode_token(credentials.credentials)


async def require_internal_or_admin(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
    claims: Annotated[TokenClaims | None, Depends(optional_token_claims)] = None,
) -> None:
    """Внутренний токен (constant-time) или admin из realm-ролей токена."""
    if x_internal_token is not None:
        if verify_internal_token(x_internal_token, get_settings().internal_api_token):
            return
        raise unauthorized("Неверный внутренний токен")
    if claims is None:
        raise unauthorized()
    if ROLE_ADMIN not in claims.roles:
        raise forbidden("Доступно только администратору или по внутреннему токену")


@router.get(
    "/usage",
    summary="Сводка использования LLM за день",
    dependencies=[Depends(require_internal_or_admin)],
)
async def get_usage() -> dict[str, Any]:
    return usage_report()


class PromptInfo(BaseModel):
    name: str
    version: str
    temperature: float
    tier: str
    schema_name: str
    variables: list[str]
    template_hash: str


@router.get(
    "/prompts",
    summary="Список промптов и их переменных",
    dependencies=[Depends(require_roles(ROLE_ADMIN))],
)
async def get_prompts() -> list[PromptInfo]:
    return [
        PromptInfo(
            name=p.name,
            version=p.version,
            temperature=p.temperature,
            tier=p.tier,
            schema_name=p.schema_name,
            variables=list(p.variables),
            template_hash=p.template_hash,
        )
        for p in list_prompts()
    ]


class DryRunRequest(BaseModel):
    prompt_name: str = Field(min_length=1, examples=["need_parse"])
    variables: dict[str, Any] = Field(default_factory=dict)
    temperature: float | None = Field(default=None, ge=0, le=2)
    sanitize: bool = True


class DryRunResponse(BaseModel):
    prompt_name: str
    prompt_version: str
    schema_name: str
    model: str
    prompt_hash: str
    messages: list[dict[str, str]]
    result: dict[str, Any]
    latency_ms: int
    attempts: int


@router.post(
    "/dry-run",
    summary="Прогон промпта на stub-провайдере (отладка)",
    dependencies=[Depends(require_roles(ROLE_ADMIN))],
)
async def dry_run(body: DryRunRequest) -> DryRunResponse:
    try:
        prompt = load_prompt(body.prompt_name)
    except LLMPromptError as exc:
        raise validation_error(
            str(exc), [{"field": "prompt_name", "code": "unknown", "message": str(exc)}]
        ) from exc
    variables = sanitize_variables(body.variables) if body.sanitize else body.variables
    try:
        messages = build_messages(prompt, variables, prompt.schema)
    except LLMPromptError as exc:
        raise validation_error(
            str(exc), [{"field": "variables", "code": "missing", "message": str(exc)}]
        ) from exc
    stub = StubProvider()
    try:
        result = await stub.complete(prompt, variables, temperature=body.temperature)
    except LLMError as exc:
        raise validation_error(exc.message) from exc
    model = stub.model_for(prompt.tier)
    return DryRunResponse(
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        schema_name=prompt.schema_name,
        model=model,
        prompt_hash=compute_prompt_hash(prompt.name, prompt.version, model, variables),
        messages=[{"role": m.role, "content": m.content} for m in messages],
        result=result.parsed.model_dump(mode="json"),
        latency_ms=result.latency_ms,
        attempts=result.attempts,
    )
