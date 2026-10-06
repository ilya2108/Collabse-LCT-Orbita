"""Общие помощники тестов модуля llm: сброс процессных синглтонов, scripted-провайдер,
типовые переменные промптов. Тестов в файле нет."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from app.core.cache import get_cache
from app.modules.llm.base import ChatMessage, LLMProvider, RawCompletion, reset_gate
from app.modules.llm.journal import reset_journal
from app.modules.llm.service import reset_llm_service, reset_provider

NEED_VARS: dict[str, Any] = {
    "need_text": "Ищем middle backend-разработчика: Python, FastAPI, PostgreSQL, индексы, Docker.",
    "specializations": ["be", "fe"],
    "grades": ["junior", "middle", "senior"],
    "catalog": [
        {"id": "be.B1", "layer": "B", "title": "Проектирование API"},
        {"id": "be.D4", "layer": "D", "title": "Устройство СУБД"},
    ],
}

VALID_NEED_JSON = (
    '{"specialization": "be", "grade": "middle", "nodes": [{"id": "be.B1", "min_level": 2, '
    '"importance": 0.9, "evidence": "REST"}], "stack_tags": ["python"], "nice_to_have": [], '
    '"warnings": []}'
)

RUBRIC = [
    {"id": "composite_order", "w": 0.4, "text": "составной индекс: равенство, затем сортировка"},
    {"id": "plan_check", "w": 0.3, "text": "проверка планом EXPLAIN ANALYZE или селективность"},
    {"id": "tradeoff", "w": 0.3, "text": "цена индекса: запись, место, covering как опция"},
]  # fmt: skip

VARIANT = {
    "statement": "Запрос WHERE a = $1 AND b = $2 ORDER BY c DESC медленный. Что сделать?",
    "reference_answer": "Составной индекс (a, b, c DESC); проверить EXPLAIN ANALYZE; цена записи.",
    "trap_realized": "существующий индекс начинается с диапазонной колонки",
}


def reset_llm_state() -> None:
    """Сброс журнала, семафора, провайдера и in-process кэша между тестами."""
    reset_journal()
    reset_gate()
    reset_provider()
    reset_llm_service()
    cache = get_cache()
    cache._store.clear()  # noqa: SLF001 — тестовый сброс in-process кэша


class ScriptedProvider(LLMProvider):
    """Провайдер с заранее заданными ответами: для тестов повторов, таймаута, семафора."""

    name = "scripted"

    def __init__(
        self,
        texts: Sequence[str],
        *,
        delay: float = 0.0,
        fail: Exception | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.texts = list(texts)
        self.delay = delay
        self.fail = fail
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.messages_seen: list[list[ChatMessage]] = []

    def model_for(self, tier: str) -> str:
        return "scripted-model"

    async def _complete(self, messages, *, temperature, schema, model) -> RawCompletion:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.messages_seen.append(list(messages))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail is not None:
                raise self.fail
            text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
            return RawCompletion(text=text, model=model, tokens_in=10, tokens_out=5)
        finally:
            self.active -= 1
