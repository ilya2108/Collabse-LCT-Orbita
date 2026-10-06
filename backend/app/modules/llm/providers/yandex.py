"""Провайдер YandexGPT (Yandex AI Studio, SDK `yandex-cloud-ml-sdk`) — резерв (PLAN.md §6).

Одна модель для обоих ярусов (`YANDEX_MODEL`, по умолчанию `yandexgpt`; для fast можно
передать `model_fast`, например `yandexgpt-lite`). Структурированный вывод: SDK принимает
Pydantic-модель в `response_format` — используем; при отказе сервера переключаемся на
инструкцию в промпте. Температура в API ограничена [0, 1].

SDK импортируется лениво (он печатает предупреждение об устаревании при импорте — глушим);
без `YC_FOLDER_ID`/`YC_API_KEY` первый вызов даёт `LLMConfigError`, и цепочка уходит дальше.
"""

from __future__ import annotations

import logging
import warnings
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

logger = logging.getLogger("app.llm.yandex")


class YandexProvider(LLMProvider):
    name = "yandex"

    def __init__(
        self,
        *,
        folder_id: str,
        api_key: str,
        model: str = "yandexgpt",
        model_fast: str | None = None,
        structured_output: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._folder_id = folder_id or ""
        self._api_key = api_key or ""
        self._model_strong = model
        self._model_fast = model_fast or model
        self.structured_supported: bool | None = True if structured_output else False
        self._sdk: Any = None

    @property
    def configured(self) -> bool:
        return bool(self._folder_id.strip() and self._api_key.strip())

    def model_for(self, tier: str) -> str:
        return self._model_fast if tier == "fast" else self._model_strong

    def _get_sdk(self) -> Any:
        if not self.configured:
            raise LLMConfigError(
                "YandexGPT: не заданы YC_FOLDER_ID и YC_API_KEY (каталог и API-ключ "
                "сервисного аккаунта Yandex Cloud); укажите их в .env или отключите резерв"
            )
        if self._sdk is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from yandex_cloud_ml_sdk import AsyncYCloudML
            self._sdk = AsyncYCloudML(folder_id=self._folder_id, auth=self._api_key)
        return self._sdk

    async def _run(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        schema: type[BaseModel],
        model: str,
        structured: bool,
    ) -> Any:
        sdk = self._get_sdk()
        completions = sdk.models.completions(model)
        options: dict[str, Any] = {"temperature": min(max(float(temperature), 0.0), 1.0)}
        if structured:
            options["response_format"] = schema
        completions = completions.configure(**options)
        payload = [{"role": m.role, "text": m.content} for m in messages]
        return await completions.run(payload, timeout=self.timeout_seconds)

    async def _complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        schema: type[BaseModel],
        model: str,
    ) -> RawCompletion:
        structured = self.structured_supported is not False
        try:
            try:
                result = await self._run(
                    messages,
                    temperature=temperature,
                    schema=schema,
                    model=model,
                    structured=structured,
                )
            except LLMConfigError:
                raise
            except Exception as exc:  # noqa: BLE001 — SDK бросает grpc/aio-исключения разных типов
                if not structured or not _looks_like_bad_request(exc):
                    raise
                logger.warning(
                    "YandexGPT %s: structured output отклонён (%s), дальше — инструкция в промпте",
                    model,
                    exc,
                )
                self.structured_supported = False
                structured = False
                result = await self._run(
                    messages, temperature=temperature, schema=schema, model=model, structured=False
                )
        except LLMConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            if _looks_like_auth(exc):
                raise LLMConfigError(f"YandexGPT: ключ или каталог отклонены ({exc})") from exc
            raise LLMUnavailableError(f"YandexGPT: ошибка API ({exc})") from exc

        text = getattr(result, "text", None)
        if text is None:
            alternatives = getattr(result, "alternatives", None) or ()
            text = alternatives[0].text if alternatives else ""
        if not text:
            raise LLMParseError("YandexGPT: пустой ответ модели")
        if structured and self.structured_supported is None:
            self.structured_supported = True
        usage = getattr(result, "usage", None)
        return RawCompletion(
            text=text,
            model=model,
            tokens_in=int(getattr(usage, "input_text_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
            structured=structured,
            raw=result,
        )


def _looks_like_bad_request(exc: Exception) -> bool:
    text = str(exc).lower()
    return "invalid_argument" in text or "response_format" in text or "json_schema" in text


def _looks_like_auth(exc: Exception) -> bool:
    text = str(exc).lower()
    return "unauthenticated" in text or "permission_denied" in text or "api key" in text


__all__ = ["YandexProvider"]
