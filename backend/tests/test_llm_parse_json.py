"""Конвейер базового провайдера: вырезание ограждений, повторы при невалидном JSON,
фолбэк на резерв/stub, таймаут и семафор."""

from __future__ import annotations

import asyncio

import pytest

from app.modules.llm.base import (
    MAX_ATTEMPTS,
    ConcurrencyGate,
    FallbackProvider,
    LLMOutputError,
    LLMParseError,
    LLMTimeoutError,
    LLMUnavailableError,
    build_messages,
    extract_json,
    parse_json_to_schema,
)
from app.modules.llm.prompts import LLMPromptError, load_prompt
from app.modules.llm.providers.stub import StubProvider
from app.modules.llm.schemas import JudgeReport, NeedProfile
from tests.test_llm_helpers import NEED_VARS, VALID_NEED_JSON, ScriptedProvider, reset_llm_state


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_llm_state()


# --- extract_json ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Вот ответ:\n```json\n{"a": 1}\n```\nНадеюсь, помог.',
        'Ответ: {"a": 1}. Готово.',
        '  \n{"a": 1}\n\n',
        'Сначала объясню. Потом JSON: {"a": 1} и ещё {"b": 2}',
    ],
)
def test_extract_json_variants(text: str) -> None:
    assert extract_json(text) == {"a": 1}


def test_extract_json_nested_and_array() -> None:
    assert extract_json('x {"a": {"b": [1, {"c": "}"}]}} y') == {"a": {"b": [1, {"c": "}"}]}}
    assert extract_json("[1, 2]") == [1, 2]


@pytest.mark.parametrize("text", ["", "   ", "нет json", "{'a': 1}", '{"a": 1', "```json\n```"])
def test_extract_json_invalid(text: str) -> None:
    with pytest.raises(LLMParseError):
        extract_json(text)


def test_parse_json_to_schema_reports_fields() -> None:
    with pytest.raises(LLMParseError) as exc:
        parse_json_to_schema('{"same_concept": true}', JudgeReport)
    assert "single_correct" in exc.value.message
    with pytest.raises(LLMParseError):
        parse_json_to_schema("[1, 2]", JudgeReport)
    report = parse_json_to_schema(
        '{"same_concept": true, "single_correct": true, "difficulty_delta": 0, "ambiguity": 1, '
        '"has_trap": true, "leak_in_statement": false, "reasons": [], "extra": "ignored"}',
        JudgeReport,
    )
    assert isinstance(report, JudgeReport) and report.accepted


def test_build_messages_requires_variables() -> None:
    prompt = load_prompt("need_parse")
    with pytest.raises(LLMPromptError):
        build_messages(prompt, {"need_text": "x"}, prompt.schema)
    messages = build_messages(prompt, NEED_VARS, prompt.schema)
    assert messages[0].role == "system" and "JSON Schema" in messages[0].content
    assert "NeedProfile" in messages[0].content or "specialization" in messages[0].content
    assert NEED_VARS["need_text"] in messages[1].content
    assert "{{" not in messages[1].content


# --- повторы --------------------------------------------------------------------------------------


async def test_retry_on_invalid_json_then_success() -> None:
    provider = ScriptedProvider(["не json", "```json\n" + VALID_NEED_JSON + "\n```"])
    result = await provider.complete(load_prompt("need_parse"), NEED_VARS)
    assert result.attempts == 2 and provider.calls == 2
    assert isinstance(result.parsed, NeedProfile)
    assert result.tokens_in == 20 and result.tokens_out == 10
    # второй вызов содержит текст ошибки и предыдущий ответ
    retry_messages = provider.messages_seen[1]
    assert retry_messages[-2].role == "assistant" and retry_messages[-2].content == "не json"
    assert "не прошёл проверку" in retry_messages[-1].content


async def test_retry_on_schema_violation() -> None:
    provider = ScriptedProvider(['{"specialization": "be"}', VALID_NEED_JSON])
    result = await provider.complete(load_prompt("need_parse"), NEED_VARS)
    assert result.attempts == 2
    assert "grade" in provider.messages_seen[1][-1].content


async def test_gives_up_after_max_attempts() -> None:
    provider = ScriptedProvider(["мусор"])
    with pytest.raises(LLMOutputError) as exc:
        await provider.complete(load_prompt("need_parse"), NEED_VARS)
    assert provider.calls == MAX_ATTEMPTS
    assert exc.value.attempts == MAX_ATTEMPTS and exc.value.last_text == "мусор"


async def test_complete_json_returns_model() -> None:
    provider = ScriptedProvider([VALID_NEED_JSON])
    parsed = await provider.complete_json("need_parse", NEED_VARS)
    assert isinstance(parsed, NeedProfile) and parsed.nodes[0].id == "be.B1"


# --- фолбэк ---------------------------------------------------------------------------------------


async def test_fallback_to_backup_then_stub() -> None:
    primary = ScriptedProvider(["x"], fail=LLMUnavailableError("сеть"))
    backup = ScriptedProvider([VALID_NEED_JSON])
    chain = FallbackProvider(primary, [backup, StubProvider()])
    result = await chain.complete(load_prompt("need_parse"), NEED_VARS)
    assert result.provider == "scripted" and result.status == "fallback"
    assert result.fallback_from == ["scripted"]
    assert primary.calls == 1 and backup.calls == 1


async def test_fallback_after_bad_json_reaches_stub() -> None:
    primary = ScriptedProvider(["мусор"])
    chain = FallbackProvider(primary, [StubProvider()])
    result = await chain.complete(load_prompt("need_parse"), NEED_VARS)
    assert result.provider == "stub" and result.status == "fallback"
    assert primary.calls == MAX_ATTEMPTS


async def test_fallback_all_fail() -> None:
    chain = FallbackProvider(
        ScriptedProvider(["x"], fail=LLMUnavailableError("a")),
        [ScriptedProvider(["x"], fail=LLMUnavailableError("b"))],
    )
    with pytest.raises(LLMUnavailableError) as exc:
        await chain.complete(load_prompt("need_parse"), NEED_VARS)
    assert "a" in exc.value.message and "b" in exc.value.message


async def test_no_fallback_without_skips_keeps_ok_status() -> None:
    chain = FallbackProvider(ScriptedProvider([VALID_NEED_JSON]), [StubProvider()])
    result = await chain.complete(load_prompt("need_parse"), NEED_VARS)
    assert result.status == "ok" and result.fallback_from == []


# --- таймаут и семафор ----------------------------------------------------------------------------


async def test_timeout_raises_and_falls_back() -> None:
    slow = ScriptedProvider([VALID_NEED_JSON], delay=0.3, timeout_seconds=0.05)
    with pytest.raises(LLMTimeoutError):
        await slow.complete(load_prompt("need_parse"), NEED_VARS)
    chain = FallbackProvider(slow, [StubProvider()])
    result = await chain.complete(load_prompt("need_parse"), NEED_VARS)
    assert result.provider == "stub" and result.status == "fallback"


async def test_semaphore_limits_concurrency() -> None:
    gate = ConcurrencyGate(2)
    provider = ScriptedProvider([VALID_NEED_JSON], delay=0.03, gate=gate)
    prompt = load_prompt("need_parse")
    await asyncio.gather(*(provider.complete(prompt, NEED_VARS) for _ in range(6)))
    assert provider.calls == 6
    assert provider.max_active == 2
    snapshot = gate.snapshot()
    assert snapshot["max_in_flight"] == 2 and snapshot["in_flight"] == 0
    assert snapshot["waiting"] == 0 and snapshot["total"] == 6


async def test_gate_releases_on_error() -> None:
    gate = ConcurrencyGate(1)
    provider = ScriptedProvider(["x"], fail=LLMUnavailableError("сеть"), gate=gate)
    with pytest.raises(LLMUnavailableError):
        await provider.complete(load_prompt("need_parse"), NEED_VARS)
    assert gate.snapshot()["in_flight"] == 0
