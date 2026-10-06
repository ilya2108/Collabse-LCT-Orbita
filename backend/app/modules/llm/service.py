"""Сервис LLM: фабрика провайдера по настройкам и конвейер вызова с журналом (PLAN.md §6).

`get_provider(settings)` собирает цепочку по `LLM_PROVIDER`:
- `gigachat` → GigaChat → YandexGPT (если заданы ключи) → stub;
- `yandex`   → YandexGPT → GigaChat (если задан ключ) → stub;
- `rules`    → словарь ключевых слов (need_parse) → stub (остальные промпты);
- `replay`   → только кэш из репозитория (нет записи — понятная ошибка, без подмены);
- `stub`     → фикстуры.

`LLMService.complete(prompt_name, variables, …)`:
1. промпт по имени, схема и температура из шапки (можно переопределить);
2. санитайзер ПДн по всем строковым переменным (`sanitize=True`);
3. `prompt_hash` = sha256(kind + версия + модель + вход) → кэш (память → БД);
4. проверка `LLM_DAILY_TOKEN_CAP`;
5. вызов цепочки провайдеров (повторы, фолбэк, семафор, таймаут — в `base`);
6. строка `llm_call` (status ok | fallback | cached | error) и счётчики; если цепочка
   дошла до резерва, по каждому не ответившему провайдеру пишется отдельная строка
   `error` с его токенами — платные повторы с невалидным JSON учитываются в лимите.

Модули-потребители: `matching` (need_parse), `assessment` (variant_*, answer_grade,
scenario_personal), `candidates` (resume_parse), `tools/pool`, `tools/llm_smoke`.
`to_api_error()` переводит LLMError в ответ API: лимит → 503 `llm_budget_exceeded`,
остальное → 502 `integration_unavailable` с нейтральным текстом: пути к файлам и подсказки
по конфигурации остаются в логе и в `llm_call.error`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, integration_unavailable
from app.modules.llm.base import (
    CompletionResult,
    FallbackProvider,
    LLMBudgetExceededError,
    LLMError,
    LLMProvider,
    get_gate,
)
from app.modules.llm.journal import LlmCallRecord, LlmJournal, compute_prompt_hash, get_journal
from app.modules.llm.prompts import PromptSpec, load_prompt
from app.modules.llm.providers.replay import ReplayProvider
from app.modules.llm.providers.rules import RulesProvider
from app.modules.llm.providers.stub import StubProvider
from app.modules.llm.sanitizer import sanitize_variables

logger = logging.getLogger("app.llm.service")

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_CACHED = "cached"
STATUS_FALLBACK = "fallback"


# --- фабрика провайдера ------------------------------------------------------------------------


def _gigachat(settings: Settings) -> LLMProvider:
    from app.modules.llm.providers.gigachat import GigaChatProvider

    return GigaChatProvider(
        credentials=settings.gigachat_credentials,
        scope=settings.gigachat_scope,
        model_strong=settings.gigachat_model_strong,
        model_fast=settings.gigachat_model_fast,
        timeout_seconds=settings.llm_timeout_seconds,
        verify_ssl_certs=settings.gigachat_verify_ssl_certs,
        ca_bundle_file=settings.gigachat_ca_bundle,
    )


def _yandex(settings: Settings) -> LLMProvider:
    from app.modules.llm.providers.yandex import YandexProvider

    return YandexProvider(
        folder_id=settings.yc_folder_id,
        api_key=settings.yc_api_key,
        model=settings.yandex_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def build_provider(settings: Settings | None = None, name: str | None = None) -> LLMProvider:
    """Цепочка провайдеров для `LLM_PROVIDER` (или явного `name`). Предупреждает в лог,
    если основной провайдер выбран, а ключей нет — тогда все вызовы уйдут в фолбэк."""
    settings = settings or get_settings()
    name = name or settings.llm_provider
    stub = StubProvider(timeout_seconds=settings.llm_timeout_seconds)
    has_giga = bool(settings.gigachat_credentials.strip())
    has_yandex = bool(settings.yc_folder_id.strip() and settings.yc_api_key.strip())

    if name == "stub":
        return stub
    if name == "rules":
        return FallbackProvider(RulesProvider(timeout_seconds=settings.llm_timeout_seconds), [stub])
    if name == "replay":
        return ReplayProvider(timeout_seconds=settings.llm_timeout_seconds)
    if name == "gigachat":
        if not has_giga:
            logger.warning(
                "LLM_PROVIDER=gigachat, но GIGACHAT_CREDENTIALS пуст: вызовы уйдут в фолбэк (%s)",
                "yandex → stub" if has_yandex else "stub",
            )
        backups: list[LLMProvider] = [_yandex(settings)] if has_yandex else []
        return FallbackProvider(_gigachat(settings), [*backups, stub])
    if name == "yandex":
        if not has_yandex:
            logger.warning(
                "LLM_PROVIDER=yandex, но YC_FOLDER_ID/YC_API_KEY пусты: вызовы уйдут в фолбэк (%s)",
                "gigachat → stub" if has_giga else "stub",
            )
        backups = [_gigachat(settings)] if has_giga else []
        return FallbackProvider(_yandex(settings), [*backups, stub])
    raise ValueError(f"неизвестный LLM_PROVIDER={name!r}")


_provider: LLMProvider | None = None


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Провайдер процесса (ленивая сборка по настройкам); `reset_provider()` — для тестов."""
    global _provider
    if _provider is None:
        _provider = build_provider(settings)
    return _provider


def reset_provider() -> None:
    global _provider
    _provider = None


# --- сервис ---------------------------------------------------------------------------------------


class LLMService:
    """Высокоуровневый вызов промпта с кэшем, лимитом и журналом."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        journal: LlmJournal | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._journal = journal

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    @property
    def provider(self) -> LLMProvider:
        return self._provider or get_provider(self.settings)

    @property
    def journal(self) -> LlmJournal:
        return self._journal or get_journal()

    async def complete(
        self,
        prompt_name: str,
        variables: Mapping[str, Any],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        session: AsyncSession | None = None,
        use_cache: bool = True,
        sanitize: bool = True,
        terms: Mapping[str, str] | None = None,
        meta: Mapping[str, Any] | None = None,
        prompt_version: str | None = None,
    ) -> CompletionResult:
        """Полный вызов с метаданными (см. docstring модуля). `terms` — известные ПДн
        (название компании, ФИО) → плейсхолдеры; `meta` — служебные поля для request_json
        (например, session_id — только UUID)."""
        prompt: PromptSpec = load_prompt(prompt_name, prompt_version)
        schema = schema or prompt.schema
        clean_variables: dict[str, Any] = (
            sanitize_variables(dict(variables), terms) if sanitize else dict(variables)
        )
        model = self.provider.model_for(prompt.tier)
        prompt_hash = compute_prompt_hash(prompt.name, prompt.version, model, clean_variables)
        request_json: dict[str, Any] = {
            "variables": clean_variables,
            "template_hash": prompt.template_hash,
            "temperature": prompt.temperature if temperature is None else temperature,
            "schema": schema.__name__,
            **({"meta": dict(meta)} if meta else {}),
        }
        journal = self.journal
        await journal.ensure_seeded(session)

        if use_cache:
            cached = await journal.lookup(session, prompt_hash)
            if cached is not None:
                try:
                    parsed = schema.model_validate(cached)
                except Exception:  # noqa: BLE001 — устаревший кэш другой схемы: игнорируем
                    parsed = None
                if parsed is not None:
                    await journal.record(
                        session,
                        LlmCallRecord(
                            kind=prompt.name,
                            provider="cache",
                            model=model,
                            prompt_version=prompt.version,
                            prompt_hash=prompt_hash,
                            status=STATUS_CACHED,
                            request_json=request_json,
                            response_json=cached,
                        ),
                    )
                    return CompletionResult(
                        parsed=parsed,
                        provider="cache",
                        model=model,
                        prompt=prompt,
                        attempts=0,
                        status=STATUS_CACHED,
                        raw_text=parsed.model_dump_json(),
                        prompt_hash=prompt_hash,
                    )

        started = time.perf_counter()
        try:
            journal.check_budget(self.settings.llm_daily_token_cap)
        except LLMBudgetExceededError as exc:
            await journal.record(
                session,
                LlmCallRecord(
                    kind=prompt.name,
                    provider=self.provider.name,
                    model=model,
                    prompt_version=prompt.version,
                    prompt_hash=prompt_hash,
                    status=STATUS_ERROR,
                    request_json=request_json,
                    error=exc.message[:2000],
                ),
            )
            raise

        try:
            result = await self.provider.complete(
                prompt, clean_variables, schema=schema, temperature=temperature
            )
        except LLMError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            failures = getattr(exc, "failures", None) or []
            for failure in failures:
                await journal.record(
                    session,
                    LlmCallRecord(
                        kind=prompt.name,
                        provider=failure.provider,
                        model=model,
                        prompt_version=prompt.version,
                        prompt_hash=prompt_hash,
                        status=STATUS_ERROR,
                        tokens_in=failure.tokens_in,
                        tokens_out=failure.tokens_out,
                        latency_ms=latency_ms,
                        request_json={**request_json, "fallback_attempt": True},
                        error=failure.message[:2000],
                    ),
                )
            await journal.record(
                session,
                LlmCallRecord(
                    kind=prompt.name,
                    provider=self.provider.name,
                    model=model,
                    prompt_version=prompt.version,
                    prompt_hash=prompt_hash,
                    status=STATUS_ERROR,
                    # токены цепочки уже учтены по провайдерам выше
                    tokens_in=0 if failures else exc.tokens_in,
                    tokens_out=0 if failures else exc.tokens_out,
                    latency_ms=latency_ms,
                    request_json=request_json,
                    error=exc.message[:2000],
                ),
            )
            raise

        result.prompt_hash = prompt_hash
        request_json["attempts"] = result.attempts
        if result.fallback_from:
            request_json["fallback_from"] = list(result.fallback_from)
        if result.structured:
            request_json["structured_output"] = True
        # не ответившие провайдеры цепочки: отдельные строки error с их токенами
        for failure in result.fallback_failures:
            await journal.record(
                session,
                LlmCallRecord(
                    kind=prompt.name,
                    provider=failure.provider,
                    model=model,
                    prompt_version=prompt.version,
                    prompt_hash=prompt_hash,
                    status=STATUS_ERROR,
                    tokens_in=failure.tokens_in,
                    tokens_out=failure.tokens_out,
                    latency_ms=result.latency_ms,
                    request_json={**request_json, "fallback_attempt": True},
                    error=failure.message[:2000],
                ),
            )
        await journal.record(
            session,
            LlmCallRecord(
                kind=prompt.name,
                provider=result.provider,
                model=result.model,
                prompt_version=prompt.version,
                prompt_hash=prompt_hash,
                status=STATUS_FALLBACK if result.status == STATUS_FALLBACK else STATUS_OK,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                latency_ms=result.latency_ms,
                request_json=request_json,
                response_json=result.parsed.model_dump(mode="json"),
            ),
        )
        return result

    async def complete_json(
        self,
        prompt_name: str,
        variables: Mapping[str, Any],
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> BaseModel:
        """Интерфейс PLAN.md §6: только распарсенный объект."""
        result = await self.complete(
            prompt_name, variables, schema=schema, temperature=temperature, **kwargs
        )
        return result.parsed


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _service
    if _service is None:
        _service = LLMService()
    return _service


def reset_llm_service() -> None:
    global _service
    _service = None


# --- ошибки API и отчёт ---------------------------------------------------------------------------


LLM_UNAVAILABLE_MESSAGE = "Сервис ИИ временно недоступен, повторите попытку позже"


def to_api_error(exc: LLMError) -> ApiError:
    """LLMError → ApiError по контракту ошибок: лимит токенов — 503 `llm_budget_exceeded`
    (PLAN.md §4.3), остальные сбои — 502 `integration_unavailable` с фиксированным текстом.
    Внутренний текст (пути к кэшу replay, имена переменных окружения) — только в лог."""
    if isinstance(exc, LLMBudgetExceededError):
        return ApiError(
            503,
            "llm_budget_exceeded",
            "Лимит ИИ-проверок на стенде исчерпан; закрытые задания оцениваются без ИИ",
            headers={"Retry-After": "3600"},
        )
    logger.warning("llm: сбой провайдера (%s): %s", type(exc).__name__, exc.message)
    return integration_unavailable(LLM_UNAVAILABLE_MESSAGE)


def usage_report(settings: Settings | None = None) -> dict[str, Any]:
    """Сводка для `GET /llm/usage` и `GET /internal/llm/usage`: провайдер, дневные счётчики,
    очередь семафора, последние вызовы (без переменных)."""
    settings = settings or get_settings()
    journal = get_journal()
    provider = get_provider(settings)
    chain = (
        [p.name for p in provider.providers]
        if isinstance(provider, FallbackProvider)
        else [provider.name]
    )
    recent = [
        {
            "kind": r.kind,
            "provider": r.provider,
            "model": r.model,
            "status": r.status,
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "latency_ms": r.latency_ms,
            "attempts": r.request_json.get("attempts"),
            "fallback_from": r.request_json.get("fallback_from"),
            "error": r.error,
            "created_at": r.created_at.isoformat(),
        }
        for r in list(journal.recent)[-20:]
    ]
    return {
        "provider": settings.llm_provider,
        "chain": chain,
        "models": {
            "strong": provider.model_for("strong"),
            "fast": provider.model_for("fast"),
        },
        "today": journal.counter.snapshot(settings.llm_daily_token_cap),
        "queue": get_gate().snapshot(),
        "timeout_seconds": settings.llm_timeout_seconds,
        "db_errors": journal.db_errors,
        "recent": recent,
    }


__all__ = [
    "LLM_UNAVAILABLE_MESSAGE",
    "STATUS_CACHED",
    "STATUS_ERROR",
    "STATUS_FALLBACK",
    "STATUS_OK",
    "LLMService",
    "build_provider",
    "get_llm_service",
    "get_provider",
    "reset_llm_service",
    "reset_provider",
    "to_api_error",
    "usage_report",
]
