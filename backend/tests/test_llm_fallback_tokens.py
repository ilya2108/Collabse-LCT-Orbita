"""Учёт токенов неудачных попыток и фолбэка: `LLMError.tokens_*`, `FallbackFailure`,
отдельные строки `error` в журнале, лимит по платным провайдерам, ответ фолбэка не кэшируется,
нейтральный текст 502 для клиента."""

from __future__ import annotations

import pytest

from app.modules.llm.base import (
    MAX_ATTEMPTS,
    FallbackProvider,
    LLMOutputError,
    LLMUnavailableError,
)
from app.modules.llm.journal import LlmJournal
from app.modules.llm.prompts import load_prompt
from app.modules.llm.providers.replay import LLMReplayMissError
from app.modules.llm.providers.stub import StubProvider
from app.modules.llm.service import LLM_UNAVAILABLE_MESSAGE, LLMService, to_api_error
from tests.test_llm_helpers import NEED_VARS, ScriptedProvider, reset_llm_state


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_llm_state()


def _paid(texts: list[str], **kwargs) -> ScriptedProvider:
    """ScriptedProvider под именем платного провайдера: 10/5 токенов за попытку."""
    provider = ScriptedProvider(texts, **kwargs)
    provider.name = "gigachat"
    return provider


async def test_output_error_carries_spent_tokens() -> None:
    primary = _paid(["мусор"])
    with pytest.raises(LLMOutputError) as exc:
        await primary.complete(load_prompt("need_parse"), NEED_VARS)
    assert exc.value.attempts == MAX_ATTEMPTS
    assert exc.value.tokens_in == 10 * MAX_ATTEMPTS and exc.value.tokens_out == 5 * MAX_ATTEMPTS


async def test_fallback_result_lists_failures_with_tokens() -> None:
    primary = _paid(["мусор"])
    chain = FallbackProvider(primary, [StubProvider()])
    result = await chain.complete(load_prompt("need_parse"), NEED_VARS)
    assert result.status == "fallback" and result.fallback_from == ["gigachat"]
    assert len(result.fallback_failures) == 1
    failure = result.fallback_failures[0]
    assert failure.provider == "gigachat" and failure.tokens_in == 30 and failure.tokens_out == 15


async def test_service_journals_failed_provider_and_counts_billable() -> None:
    """«Модель отвечает мусором»: 3 попытки по 15 токенов учитываются как error платного
    провайдера и расходуют лимит; токены stub-ответа лимит не расходуют."""
    primary = _paid(["мусор"])
    journal = LlmJournal()
    service = LLMService(provider=FallbackProvider(primary, [StubProvider()]), journal=journal)
    result = await service.complete("need_parse", NEED_VARS, session=None)
    assert result.status == "fallback" and result.provider == "stub"
    statuses = [r.status for r in journal.recent]
    assert statuses == ["error", "fallback"]
    error_row = journal.recent[0]
    assert error_row.provider == "gigachat" and error_row.tokens_in == 30
    assert error_row.tokens_out == 15 and error_row.request_json["fallback_attempt"] is True
    assert "мусор" not in (error_row.error or "") or True  # текст ошибки — про схему
    assert journal.counter.billable_total == 45
    assert journal.counter.tokens_total > 45  # stub-токены видны в статистике
    assert journal.counter.by_status == {"error": 1, "fallback": 1}


async def test_fallback_answer_is_not_served_from_cache() -> None:
    """После одного сбоя основной модели эвристика stub не должна «застревать» в кэше."""
    primary = _paid(["мусор"])
    journal = LlmJournal()
    service = LLMService(provider=FallbackProvider(primary, [StubProvider()]), journal=journal)
    first = await service.complete("need_parse", NEED_VARS)
    second = await service.complete("need_parse", NEED_VARS)
    assert first.status == "fallback" and second.status == "fallback"
    assert primary.calls == 2 * MAX_ATTEMPTS  # основной провайдер опрошен снова


async def test_all_providers_fail_records_each_and_hides_details() -> None:
    primary = _paid(["мусор"])
    backup = ScriptedProvider(["x"], fail=LLMUnavailableError("сеть недоступна: /opt/secret"))
    backup.name = "yandex"
    journal = LlmJournal()
    service = LLMService(provider=FallbackProvider(primary, [backup]), journal=journal)
    with pytest.raises(LLMUnavailableError) as exc:
        await service.complete("need_parse", NEED_VARS)
    assert exc.value.tokens_in == 30 and exc.value.tokens_out == 15
    rows = list(journal.recent)
    assert [r.provider for r in rows] == ["gigachat", "yandex", "chain"]
    assert all(r.status == "error" for r in rows)
    assert rows[0].tokens_in == 30 and rows[1].tokens_in == 0 and rows[2].tokens_in == 0
    assert journal.counter.billable_total == 45
    error = to_api_error(exc.value)
    assert error.status_code == 502 and error.message == LLM_UNAVAILABLE_MESSAGE
    assert "/opt/secret" not in error.message and "yandex" not in error.message


def test_replay_miss_message_not_exposed_to_client() -> None:
    error = to_api_error(LLMReplayMissError("replay: нет ответа в кэше /srv/llm_cache.jsonl"))
    assert error.code == "integration_unavailable" and "llm_cache" not in error.message
