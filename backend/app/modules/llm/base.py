"""Интерфейс провайдера LLM и общий конвейер «промпт → строгий JSON → Pydantic» (PLAN.md §6).

`LLMProvider.complete_json(prompt_name, variables, schema, temperature) -> BaseModel`:
- системная инструкция «только JSON по схеме» + JSON Schema модели ответа;
- парсинг ответа с вырезанием markdown-ограждений и обрамляющего текста;
- валидация Pydantic; при невалидном JSON — до 2 повторов, модели передаётся текст ошибки;
- семафор `LLM_MAX_CONCURRENCY` и таймаут `LLM_TIMEOUT_SECONDS` на каждый сетевой вызов.

Фолбэк на резервного провайдера и затем на stub — `FallbackProvider` (цепочку собирает
`service.build_provider`). Журнал, кэш по хэшу и дневной лимит токенов — `service.LLMService`
+ `journal.LlmJournal`: провайдеры о них не знают.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import re
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.modules.llm.prompts import PromptSpec, load_prompt

logger = logging.getLogger("app.llm")

# 1 попытка + 2 повтора с текстом ошибки
MAX_ATTEMPTS = 3
# сколько символов ответа модели возвращаем ей при повторе (экономим токены)
_RETRY_ECHO_LIMIT = 4000

JSON_ONLY_INSTRUCTION = (
    "Формат ответа: ровно один JSON-объект, соответствующий схеме ниже. Без markdown, "
    "без ограждений ```, без комментариев и текста до или после объекта. Все обязательные "
    "поля заполняются; строки — на русском языке, если задание не требует иного."
)


# --- ошибки ----------------------------------------------------------------------------------


class LLMError(Exception):
    """База ошибок конвейера LLM; `message` — человекочитаемый текст по-русски.
    `tokens_in/out` — токены, потраченные до сбоя (повторы с невалидным JSON): журнал
    учитывает их в дневном лимите, иначе «модель отвечает мусором» обходил бы cap."""

    def __init__(self, message: str, *, tokens_in: int = 0, tokens_out: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


class LLMConfigError(LLMError):
    """Провайдер не сконфигурирован (нет ключа, folder id и т. п.)."""


class LLMUnavailableError(LLMError):
    """Сетевой/HTTP-сбой провайдера, лимит запросов на стороне провайдера."""


class LLMTimeoutError(LLMUnavailableError):
    """Превышен LLM_TIMEOUT_SECONDS."""


class LLMUnsupportedError(LLMError):
    """Провайдер не умеет этот промпт (rules — только need_parse; replay — нет записи)."""


class LLMParseError(LLMError):
    """В ответе нет корректного JSON (одна попытка; после всех повторов — LLMOutputError)."""


class LLMOutputError(LLMError):
    """После всех повторов ответ так и не прошёл парсинг/валидацию."""

    def __init__(
        self,
        message: str,
        last_text: str = "",
        attempts: int = 0,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        super().__init__(message, tokens_in=tokens_in, tokens_out=tokens_out)
        self.last_text = last_text
        self.attempts = attempts


class LLMBudgetExceededError(LLMError):
    """Исчерпан LLM_DAILY_TOKEN_CAP (проверяется сервисом до вызова провайдера)."""


# --- данные ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class RawCompletion:
    """Сырой ответ провайдера: текст (JSON или около того) и учёт токенов."""

    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    structured: bool = False  # провайдер вернул structured output по схеме
    raw: Any = None


@dataclass(frozen=True)
class FallbackFailure:
    """Провайдер цепочки, который не ответил: причина и потраченные им токены —
    сервис пишет по такой записи отдельную строку `llm_call` со статусом error."""

    provider: str
    message: str
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class CompletionResult:
    """Результат `complete()`: распарсенный объект и метаданные для журнала."""

    parsed: BaseModel
    provider: str
    model: str
    prompt: PromptSpec
    attempts: int = 1
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    status: str = "ok"  # ok | fallback | cached
    fallback_from: list[str] = field(default_factory=list)
    fallback_failures: list[FallbackFailure] = field(default_factory=list)
    raw_text: str = ""
    structured: bool = False
    prompt_hash: str = ""  # проставляет сервис


# --- разбор JSON -------------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(?P<body>.*?)```", re.DOTALL)
_DECODER = json.JSONDecoder()


def extract_json(text: str) -> Any:
    """JSON из ответа модели: целиком, из первого ограждения ``` или от первой `{`/`[`.

    Поддерживает обрамляющий текст («Вот ответ: {...}. Надеюсь, помог») и завершающий
    мусор после объекта. Невалидный JSON — LLMParseError с коротким описанием.
    """
    if text is None:
        raise LLMParseError("пустой ответ модели")
    candidate = text.strip()
    if not candidate:
        raise LLMParseError("пустой ответ модели")
    # 1) весь текст — JSON
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    # 2) содержимое первого markdown-ограждения
    fence = _FENCE_RE.search(candidate)
    if fence is not None:
        body = fence.group("body").strip()
        try:
            return json.loads(body)
        except ValueError:
            candidate = body or candidate
    # 3) первый объект/массив, который удаётся декодировать с позиции `{` или `[`
    last_error: str | None = None
    for index, char in enumerate(candidate):
        if char not in "{[":
            continue
        try:
            value, _ = _DECODER.raw_decode(candidate, index)
        except ValueError as exc:
            if last_error is None:
                last_error = str(exc)
            continue
        return value
    raise LLMParseError("в ответе нет корректного JSON" + (f": {last_error}" if last_error else ""))


def format_validation_error(exc: ValidationError) -> str:
    """Компактный список ошибок Pydantic для возврата модели: `поле: сообщение`."""
    parts: list[str] = []
    for err in exc.errors()[:8]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


def parse_json_to_schema(text: str, schema: type[BaseModel]) -> BaseModel:
    """Текст ответа → объект схемы; ошибки парсинга и валидации — LLMParseError с текстом,
    который можно вернуть модели для повтора."""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise LLMParseError("ожидался JSON-объект, получен " + type(data).__name__)
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise LLMParseError(
            "ответ не соответствует схеме: " + format_validation_error(exc)
        ) from exc


def schema_json(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), ensure_ascii=False)


def build_messages(
    prompt: PromptSpec, variables: dict[str, Any], schema: type[BaseModel]
) -> list[ChatMessage]:
    """Системное сообщение = инструкция промпта + требование «только JSON» + JSON Schema."""
    rendered = prompt.render(variables)
    system = (
        f"{rendered.system}\n\n{JSON_ONLY_INSTRUCTION}\n\n"
        f"JSON Schema ответа:\n{schema_json(schema)}"
    )
    return [ChatMessage("system", system), ChatMessage("user", rendered.user)]


# --- семафор ---------------------------------------------------------------------------------


class ConcurrencyGate:
    """Семафор на N одновременных вызовов провайдера с счётчиками для `/llm/usage`.

    `asyncio.Semaphore` привязывается к циклу событий при первом ожидании; тесты создают
    новый цикл на каждый тест, поэтому семафор пересоздаётся при смене цикла.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.waiting = 0
        self.in_flight = 0
        self.total = 0
        self.max_in_flight = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._loop = loop
            self._semaphore = asyncio.Semaphore(self.limit)
        return self._semaphore

    @asynccontextmanager
    async def slot(self):
        semaphore = self._get_semaphore()
        self.waiting += 1
        acquired = False
        try:
            await semaphore.acquire()
            acquired = True
            self.waiting -= 1
            self.in_flight += 1
            self.total += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            yield
        finally:
            if acquired:
                self.in_flight -= 1
                semaphore.release()
            else:
                self.waiting -= 1

    def snapshot(self) -> dict[str, int]:
        return {
            "limit": self.limit,
            "in_flight": self.in_flight,
            "waiting": self.waiting,
            "total": self.total,
            "max_in_flight": self.max_in_flight,
        }


_gate: ConcurrencyGate | None = None


def get_gate(limit: int | None = None) -> ConcurrencyGate:
    """Общий на процесс семафор (лимит — LLM_MAX_CONCURRENCY, если не задан явно)."""
    global _gate
    if _gate is None:
        if limit is None:
            from app.core.config import get_settings

            limit = get_settings().llm_max_concurrency
        _gate = ConcurrencyGate(limit)
    return _gate


def reset_gate() -> None:
    global _gate
    _gate = None


# --- провайдер ---------------------------------------------------------------------------------


class LLMProvider(abc.ABC):
    """Базовый провайдер: подклассы реализуют `_complete` (один сетевой вызов) и `model_for`.

    `complete()` — общий конвейер с повторами; `complete_json()` — удобная обёртка с
    загрузкой промпта по имени. Семафор и таймаут общие для всех провайдеров процесса.
    """

    name: str = "base"

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        gate: ConcurrencyGate | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._gate = gate

    # --- настройки по умолчанию из Settings (лениво, чтобы импорт был дешёвым) -------------
    @property
    def timeout_seconds(self) -> float:
        if self._timeout is None:
            from app.core.config import get_settings

            self._timeout = get_settings().llm_timeout_seconds
        return self._timeout

    @property
    def gate(self) -> ConcurrencyGate:
        return self._gate if self._gate is not None else get_gate()

    # --- контракт подкласса --------------------------------------------------------------
    @abc.abstractmethod
    def model_for(self, tier: str) -> str:
        """Имя модели для яруса strong|fast (в stub/rules — условное)."""

    @abc.abstractmethod
    async def _complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        schema: type[BaseModel],
        model: str,
    ) -> RawCompletion:
        """Один вызов модели. Сетевые ошибки — LLMUnavailableError, нет ключа — LLMConfigError."""

    def supports(self, prompt_name: str) -> bool:
        """Умеет ли провайдер этот промпт (rules — только need_parse)."""
        return True

    # --- конвейер ------------------------------------------------------------------------
    async def _guarded_complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        schema: type[BaseModel],
        model: str,
    ) -> RawCompletion:
        async with self.gate.slot():
            try:
                return await asyncio.wait_for(
                    self._complete(messages, temperature=temperature, schema=schema, model=model),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                raise LLMTimeoutError(
                    f"{self.name}: ответ модели {model} не получен за {self.timeout_seconds:g} с"
                ) from exc

    async def complete(
        self,
        prompt: PromptSpec,
        variables: dict[str, Any],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> CompletionResult:
        """Промпт → сообщения → вызов → JSON → схема; до MAX_ATTEMPTS попыток."""
        schema = schema or prompt.schema
        temperature = prompt.temperature if temperature is None else temperature
        model = self.model_for(prompt.tier)
        messages = build_messages(prompt, variables, schema)
        started = time.perf_counter()
        tokens_in = tokens_out = 0
        last_text = ""
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            raw = await self._guarded_complete(
                messages, temperature=temperature, schema=schema, model=model
            )
            tokens_in += raw.tokens_in
            tokens_out += raw.tokens_out
            last_text = raw.text or ""
            try:
                parsed = parse_json_to_schema(last_text, schema)
            except LLMParseError as exc:
                last_error = exc.message
                logger.warning(
                    "llm %s/%s %s: попытка %d не прошла проверку: %s",
                    self.name,
                    model,
                    prompt.full_name,
                    attempt,
                    last_error,
                )
                messages = [
                    *messages,
                    ChatMessage("assistant", last_text[:_RETRY_ECHO_LIMIT]),
                    ChatMessage(
                        "user",
                        "Предыдущий ответ не прошёл проверку: "
                        f"{last_error}. Верни только один JSON-объект строго по схеме, "
                        "без пояснений и ограждений.",
                    ),
                ]
                continue
            return CompletionResult(
                parsed=parsed,
                provider=self.name,
                model=raw.model or model,
                prompt=prompt,
                attempts=attempt,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=int((time.perf_counter() - started) * 1000),
                raw_text=last_text,
                structured=raw.structured,
            )
        raise LLMOutputError(
            f"{self.name}: ответ не прошёл проверку схемы {schema.__name__} после "
            f"{MAX_ATTEMPTS} попыток: {last_error}",
            last_text=last_text,
            attempts=MAX_ATTEMPTS,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    async def complete_json(
        self,
        prompt_name: str,
        variables: dict[str, Any],
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> BaseModel:
        """Интерфейс из PLAN.md §6: промпт по имени, схема по умолчанию — из шапки промпта."""
        prompt = load_prompt(prompt_name)
        result = await self.complete(prompt, variables, schema=schema, temperature=temperature)
        return result.parsed


class FallbackProvider(LLMProvider):
    """Цепочка провайдеров: основной → резерв(ы) → stub (PLAN.md §6).

    Переходит к следующему при любой LLMError основного (нет ключа, сеть, таймаут,
    невалидный JSON после повторов, неподдерживаемый промпт). Результат помечается
    `status = fallback` и несёт список пропущенных провайдеров и их сбоев с токенами
    (`fallback_failures`) — журнал видит, что ответ пришёл не от основного, и учитывает
    токены неудачных попыток в дневном лимите.
    """

    name = "chain"

    def __init__(self, primary: LLMProvider, backups: Sequence[LLMProvider] = ()) -> None:
        super().__init__()
        self.primary = primary
        self.backups = list(backups)

    @property
    def providers(self) -> list[LLMProvider]:
        return [self.primary, *self.backups]

    def model_for(self, tier: str) -> str:
        return self.primary.model_for(tier)

    async def _complete(self, messages, *, temperature, schema, model) -> RawCompletion:
        raise NotImplementedError("FallbackProvider делегирует complete() провайдерам цепочки")

    async def complete(
        self,
        prompt: PromptSpec,
        variables: dict[str, Any],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> CompletionResult:
        skipped: list[str] = []
        failures: list[FallbackFailure] = []
        for provider in self.providers:
            if not provider.supports(prompt.name):
                skipped.append(provider.name)
                failures.append(
                    FallbackFailure(provider.name, f"промпт {prompt.name} не поддерживается")
                )
                continue
            try:
                result = await provider.complete(
                    prompt, variables, schema=schema, temperature=temperature
                )
            except LLMError as exc:
                skipped.append(provider.name)
                failures.append(
                    FallbackFailure(
                        provider.name,
                        exc.message,
                        tokens_in=exc.tokens_in,
                        tokens_out=exc.tokens_out,
                    )
                )
                logger.warning(
                    "llm fallback: %s не ответил на %s (%s)", provider.name, prompt.full_name, exc
                )
                continue
            if skipped:
                result.status = "fallback"
                result.fallback_from = list(skipped)
                result.fallback_failures = list(failures)
            return result
        error = LLMUnavailableError(
            "все провайдеры LLM недоступны: "
            + "; ".join(f"{f.provider}: {f.message}" for f in failures),
            tokens_in=sum(f.tokens_in for f in failures),
            tokens_out=sum(f.tokens_out for f in failures),
        )
        error.failures = failures  # type: ignore[attr-defined] — для журнала сервиса
        raise error


__all__ = [
    "JSON_ONLY_INSTRUCTION",
    "MAX_ATTEMPTS",
    "ChatMessage",
    "CompletionResult",
    "ConcurrencyGate",
    "FallbackFailure",
    "FallbackProvider",
    "LLMBudgetExceededError",
    "LLMConfigError",
    "LLMError",
    "LLMOutputError",
    "LLMParseError",
    "LLMProvider",
    "LLMTimeoutError",
    "LLMUnavailableError",
    "LLMUnsupportedError",
    "RawCompletion",
    "build_messages",
    "extract_json",
    "format_validation_error",
    "get_gate",
    "parse_json_to_schema",
    "reset_gate",
    "schema_json",
]
