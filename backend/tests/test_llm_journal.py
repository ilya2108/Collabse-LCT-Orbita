"""Журнал llm_call без БД: хэши, дневной счётчик и лимит, модель LlmCall из записи, промпты."""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.llm.base import LLMBudgetExceededError
from app.modules.llm.journal import (
    DailyTokenCounter,
    LlmCallRecord,
    LlmJournal,
    canonical_json,
    compute_prompt_hash,
    input_hash,
)
from app.modules.llm.prompts import LLMPromptError, load_prompt, reset_prompt_cache
from tests.test_llm_helpers import NEED_VARS, reset_llm_state


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_llm_state()


def test_hashes_are_canonical_and_distinct() -> None:
    a = compute_prompt_hash("need_parse", "v1", "m", {"x": 1, "y": [1, 2]})
    b = compute_prompt_hash("need_parse", "v1", "m", {"y": [1, 2], "x": 1})
    assert a == b and len(a) == 64
    assert compute_prompt_hash("need_parse", "v2", "m", {"x": 1}) != a
    assert compute_prompt_hash("need_parse", "v1", "other", {"x": 1}) != a
    assert input_hash({"x": "Ёж"}) == input_hash({"x": "Ёж"})
    assert canonical_json({"b": 1, "a": "ё"}) == '{"a":"ё","b":1}'


def test_daily_counter_and_cap() -> None:
    counter = DailyTokenCounter()
    counter.add(
        kind="need_parse",
        status="ok",
        provider="gigachat",
        tokens_in=60,
        tokens_out=40,
        latency_ms=12,
    )
    counter.add(
        kind="answer_grade",
        status="error",
        provider="gigachat",
        tokens_in=0,
        tokens_out=0,
        latency_ms=5,
    )
    assert counter.tokens_total == 100 and counter.calls == 2
    assert counter.by_kind == {"need_parse": 1, "answer_grade": 1}
    assert counter.by_status == {"ok": 1, "error": 1}
    counter.check_cap(0)  # без лимита
    counter.check_cap(101)
    with pytest.raises(LLMBudgetExceededError):
        counter.check_cap(100)
    snapshot = counter.snapshot(150)
    assert snapshot["remaining"] == 50 and snapshot["latency_ms"]["p50"] >= 5
    assert snapshot["day"] == date.today().isoformat() or snapshot["day"]
    counter.seed(500, 200, 10)
    assert counter.tokens_total == 700 and counter.calls == 10 and counter.seeded_day == counter.day


def test_free_providers_do_not_consume_daily_cap() -> None:
    """Токены stub/rules/replay/cache считаются в статистике, но не расходуют лимит;
    токены платного провайдера (в том числе неудачных попыток, status=error) — расходуют."""
    counter = DailyTokenCounter()
    for provider in ("stub", "rules", "replay", "cache"):
        counter.add(
            kind="need_parse",
            status="ok",
            provider=provider,
            tokens_in=1000,
            tokens_out=500,
            latency_ms=1,
        )
    assert counter.tokens_total == 6000 and counter.billable_total == 0
    counter.check_cap(100)  # лимит не исчерпан
    counter.add(
        kind="answer_grade",
        status="error",
        provider="gigachat",
        tokens_in=90,
        tokens_out=10,
        latency_ms=1,
    )
    assert counter.billable_total == 100
    with pytest.raises(LLMBudgetExceededError):
        counter.check_cap(100)
    snapshot = counter.snapshot(100)
    assert snapshot["billable_total"] == 100 and snapshot["remaining"] == 0
    assert snapshot["tokens_total"] == 6100 and snapshot["cap_exhausted"] is True
    # досев из БД: отдельно все токены и отдельно платные
    fresh = DailyTokenCounter()
    fresh.seed(700, 300, 5, billable_in=70, billable_out=30)
    assert fresh.tokens_total == 1000 and fresh.billable_total == 100


async def test_journal_record_without_session_and_cache() -> None:
    journal = LlmJournal()
    record = LlmCallRecord(
        kind="need_parse",
        provider="stub",
        model="stub-fixture",
        prompt_version="v1",
        prompt_hash="h" * 64,
        status="ok",
        tokens_in=10,
        tokens_out=5,
        latency_ms=1,
        request_json={"variables": NEED_VARS},
        response_json={"specialization": "be"},
    )
    assert await journal.record(None, record) is None
    assert journal.recent[-1] is record and journal.counter.tokens_total == 15
    assert journal.counter.billable_total == 0  # stub — бесплатный
    assert await journal.lookup(None, "h" * 64) == {"specialization": "be"}
    assert await journal.lookup(None, "x" * 64) is None
    # ответ фолбэка (stub вместо модели) в кэш не попадает: модель могла восстановиться
    fallback = LlmCallRecord(
        kind="need_parse",
        provider="stub",
        model="GigaChat-2-Pro",
        prompt_version="v1",
        prompt_hash="f" * 64,
        status="fallback",
        response_json={"specialization": "be"},
    )
    await journal.record(None, fallback)
    assert await journal.lookup(None, "f" * 64) is None
    row = record.to_model()
    assert row.kind == "need_parse" and row.cost_est is None and row.status == "ok"
    assert row.request_json["variables"]["need_text"] == NEED_VARS["need_text"]


def test_prompt_loader_versions_and_errors(tmp_path) -> None:
    spec = load_prompt("variant_judge")
    assert spec.version == "v1" and spec.temperature == 0 and spec.schema.__name__ == "JudgeReport"
    assert load_prompt("variant_judge", "v1") is spec
    with pytest.raises(LLMPromptError):
        load_prompt("variant_judge", "v9")
    with pytest.raises(LLMPromptError):
        load_prompt("nope")
    rendered = spec.render({"template": {"a": 1}, "anchor": "эталон", "variant": ["x"]})
    assert '"a": 1' in rendered.user and "эталон" in rendered.user and "{{" not in rendered.user
    assert "строгий методист" in rendered.system

    (tmp_path / "custom.v1.md").write_text(
        "---\nschema: Grading\ntier: fast\ntemperature: 0.3\nsystem: Системная.\n---\nТело {{x}}",
        encoding="utf-8",
    )
    (tmp_path / "custom.v2.md").write_text(
        "---\nschema: Grading\nsystem: Системная.\n---\nТело {{x}} {{y}}",
        encoding="utf-8",
    )
    reset_prompt_cache()
    latest = load_prompt("custom", prompts_dir=tmp_path)
    assert latest.version == "v2" and latest.variables == ("x", "y") and latest.tier == "strong"
    v1 = load_prompt("custom", "v1", prompts_dir=tmp_path)
    assert v1.temperature == 0.3 and v1.tier == "fast" and v1.template_hash != latest.template_hash
    (tmp_path / "broken.v1.md").write_text(
        "---\nschema: Nope\nsystem: s\n---\nТело", encoding="utf-8"
    )
    reset_prompt_cache()
    with pytest.raises(LLMPromptError):
        load_prompt("broken", prompts_dir=tmp_path)
    reset_prompt_cache()
