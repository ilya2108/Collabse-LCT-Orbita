"""Провайдер GigaChat (API Сбера, SDK `gigachat`) — основной провайдер (PLAN.md §6).

Модели: strong (`GIGACHAT_MODEL_STRONG`, по умолчанию GigaChat-2-Pro — судья, грейдер,
разбор потребности) и fast (`GIGACHAT_MODEL_FAST`, GigaChat-2-Lite — генерация вариантов).

Структурированный вывод (`response_format: json_schema`) в API в бете: пробуем его на
первом вызове; если сервер отвечает 400/422 на параметр — запоминаем и дальше шлём обычный
chat с инструкцией «только JSON» в системном сообщении (парсинг делает `base`). Ответ
всегда проходит через наш парсер, поэтому текст сохраняется для повторов и журнала.

SDK импортируется лениво: модуль импортируется без ключа, ошибка конфигурации — только
при первом вызове (`LLMConfigError`), и цепочка фолбэка уходит на резерв/stub.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.modules.llm.base import (
    ChatMessage,
    LLMConfigError,
    LLMParseError,
    LLMProvider,
    LLMUnavailableError,
    RawCompletion,
)

logger = logging.getLogger("app.llm.gigachat")

# API отвергает temperature = 0 (должна быть > 0): t=0 из промптов судьи/грейдера → минимум
_MIN_TEMPERATURE = 0.01
_ROLE_MAP = {"system": "system", "user": "user", "assistant": "assistant"}


class GigaChatProvider(LLMProvider):
    name = "gigachat"

    def __init__(
        self,
        *,
        credentials: str,
        scope: str = "GIGACHAT_API_PERS",
        model_strong: str = "GigaChat-2-Pro",
        model_fast: str = "GigaChat-2-Lite",
        verify_ssl_certs: bool = True,
        ca_bundle_file: str = "",
        structured_output: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._credentials = credentials or ""
        self._scope = scope
        self._model_strong = model_strong
        self._model_fast = model_fast
        self._verify_ssl_certs = verify_ssl_certs
        # PEM-бандл НУЦ Минцифры: без него системные хранилища вне РФ-сборок
        # не доверяют цепочке *.devices.sberbank.ru
        self._ca_bundle_file = ca_bundle_file or ""
        # None — ещё не проверяли; False — сервер отверг response_format
        self.structured_supported: bool | None = True if structured_output else False
        self._client: Any = None

    @property
    def configured(self) -> bool:
        return bool(self._credentials.strip())

    def model_for(self, tier: str) -> str:
        return self._model_fast if tier == "fast" else self._model_strong

    def _get_client(self) -> Any:
        if not self.configured:
            raise LLMConfigError(
                "GigaChat: не задан GIGACHAT_CREDENTIALS (authorization key из кабинета "
                "GigaChat API); укажите его в .env или переключите LLM_PROVIDER=stub"
            )
        if self._client is None:
            from gigachat import GigaChat

            self._client = GigaChat(
                credentials=self._credentials,
                scope=self._scope,
                timeout=self.timeout_seconds,
                verify_ssl_certs=self._verify_ssl_certs,
                ca_bundle_file=self._ca_bundle_file or None,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    def _build_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        schema: type[BaseModel],
        model: str,
        structured: bool,
    ) -> Any:
        from gigachat.models import Chat, Messages, MessagesRole

        chat = Chat(
            model=model,
            messages=[
                Messages(role=MessagesRole(_ROLE_MAP.get(m.role, "user")), content=m.content)
                for m in messages
            ],
            temperature=max(float(temperature), _MIN_TEMPERATURE),
        )
        if structured:
            from gigachat.models import JsonSchemaResponseFormat

            chat.response_format = JsonSchemaResponseFormat(schema=schema, strict=False)
        return chat

    async def _complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        schema: type[BaseModel],
        model: str,
    ) -> RawCompletion:
        client = self._get_client()
        import httpx
        from gigachat import exceptions as giga_exc

        structured = self.structured_supported is not False
        chat = self._build_chat(
            messages, temperature=temperature, schema=schema, model=model, structured=structured
        )
        try:
            try:
                completion = await client.achat(chat)
            except (giga_exc.BadRequestError, giga_exc.UnprocessableEntityError) as exc:
                if not structured:
                    raise
                # сервер не принял response_format — переходим на обычный chat навсегда
                logger.warning(
                    "GigaChat %s: structured output отклонён (%s), дальше — инструкция в промпте",
                    model,
                    exc,
                )
                self.structured_supported = False
                structured = False
                chat = self._build_chat(
                    messages, temperature=temperature, schema=schema, model=model, structured=False
                )
                completion = await client.achat(chat)
        except giga_exc.AuthenticationError as exc:
            raise LLMConfigError(f"GigaChat: ключ отклонён ({exc})") from exc
        except giga_exc.RateLimitError as exc:
            raise LLMUnavailableError(f"GigaChat: превышен лимит запросов ({exc})") from exc
        except giga_exc.GigaChatException as exc:
            raise LLMUnavailableError(f"GigaChat: ошибка API ({exc})") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"GigaChat: сетевая ошибка ({exc})") from exc

        if not completion.choices:
            raise LLMParseError("GigaChat: пустой список choices в ответе")
        choice = completion.choices[0]
        if structured and self.structured_supported is None:
            self.structured_supported = True
        if getattr(choice, "finish_reason", None) == "length":
            logger.warning("GigaChat %s: ответ обрезан по max_tokens", model)
        usage = getattr(completion, "usage", None)
        return RawCompletion(
            text=choice.message.content or "",
            model=getattr(completion, "model", None) or model,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
            structured=structured,
            raw=completion,
        )


__all__ = ["GigaChatProvider"]
