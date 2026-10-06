"""Stub-провайдер и сервис LLM без ключей: фикстуры по схемам, детерминизм, эвристический
грейдинг, кэш по хэшу, дневной лимит, журнал без ПДн."""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings
from app.modules.llm.base import FallbackProvider, LLMBudgetExceededError
from app.modules.llm.journal import LlmJournal
from app.modules.llm.prompts import list_prompts, load_prompt
from app.modules.llm.providers.stub import (
    FLAG_HEURISTIC,
    FLAG_TOO_CLOSE,
    StubProvider,
    grade_by_keywords,
)
from app.modules.llm.sanitizer import contains_pii
from app.modules.llm.schemas import (
    AnswerBank,
    Grading,
    JudgeReport,
    NeedProfile,
    ResumeFacts,
    SummaryText,
    TaskVariant,
)
from app.modules.llm.service import (
    LLMService,
    build_provider,
    get_provider,
    to_api_error,
    usage_report,
)
from tests.test_llm_helpers import (
    NEED_VARS,
    RUBRIC,
    VALID_NEED_JSON,
    VARIANT,
    ScriptedProvider,
    reset_llm_state,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_llm_state()


TEMPLATE = {
    "id": "be.D4.L2.demo",
    "node": "be.D4",
    "level": 2,
    "kind": "short_design",
    "concept": "составной индекс под равенство и сортировку",
    "invariant": "WHERE по двум колонкам, ORDER BY по третьей",
    "vary": "предметная область",
    "forbidden_changes": "число условий",
    "trap": "диапазонная колонка первой",
    "stack_tags": ["postgresql"],
    "rubric": RUBRIC,
    "anchor_variant": {**VARIANT, "estimated_minutes": 4},
}

PROMPT_VARIABLES = {
    "need_parse": NEED_VARS,
    "variant_generate": {"template": TEMPLATE, "context_seed": {"domain": "платежи"}},
    "variant_judge": {"template": TEMPLATE, "anchor": VARIANT, "variant": VARIANT},
    "answer_grade": {"variant": VARIANT, "rubric": RUBRIC, "answer": "Составной индекс (a, b, c)."},
    "scenario_personal": {
        "accomplishment": {"title": "ускорил отчёты", "description": "переписал SQL"},
        "template": TEMPLATE,
    },
    "resume_parse": {
        "resume_text": "Backend-разработчик, 5 лет: Python, FastAPI, PostgreSQL, Docker.",
        "catalog": NEED_VARS["catalog"],
    },
    "answer_bank": {"variant": VARIANT, "rubric": RUBRIC},
    "graph_summary": {
        "profile": {"specialization": "Бэкенд", "grade": "junior", "next_grade": "middle"},
        "nodes": [
            {
                "id": "be.B1",
                "title": "Хранение данных",
                "layer": "B",
                "state": "fresh",
                "score": 0.85,
                "next_grade_target": True,
            },
            {
                "id": "be.B2",
                "title": "Интеграции",
                "layer": "B",
                "state": "fading",
                "score": 0.5,
                "next_grade_target": True,
            },
        ],
    },
    "selection_summary": {
        "need": {
            "title": "Backend-разработчик",
            "specialization": "be",
            "grade": "middle",
            "nodes": [
                {"id": "be.B1", "title": "Хранение данных", "importance": "must", "min_level": 1}
            ],
        },
        "candidates": [
            {
                "pseudonym": "B-7F3A",
                "grade": "middle",
                "grade_confirmed": True,
                "coverage_pct": 50,
                "must_closed": [],
                "must_missing": ["Хранение данных"],
            }
        ],
    },
}
EXPECTED_SCHEMAS = {
    "need_parse": NeedProfile,
    "variant_generate": TaskVariant,
    "variant_judge": JudgeReport,
    "answer_grade": Grading,
    "scenario_personal": TaskVariant,
    "resume_parse": ResumeFacts,
    "answer_bank": AnswerBank,
    "graph_summary": SummaryText,
    "selection_summary": SummaryText,
}


def test_default_provider_is_stub() -> None:
    assert get_settings().llm_provider == "stub"
    assert isinstance(get_provider(), StubProvider)


@pytest.mark.parametrize("name", sorted(PROMPT_VARIABLES))
async def test_stub_fixture_matches_schema(name: str) -> None:
    prompt = load_prompt(name)
    assert prompt.schema is EXPECTED_SCHEMAS[name]
    first = await StubProvider().complete(prompt, PROMPT_VARIABLES[name])
    second = await StubProvider().complete(prompt, PROMPT_VARIABLES[name])
    assert isinstance(first.parsed, EXPECTED_SCHEMAS[name])
    assert first.parsed == second.parsed  # детерминизм
    assert first.provider == "stub" and first.attempts == 1 and first.tokens_in > 0


def test_every_prompt_has_stub_fixture() -> None:
    assert {p.name for p in list_prompts()} == set(PROMPT_VARIABLES)


async def test_stub_need_parse_heuristics() -> None:
    profile = await StubProvider().complete_json("need_parse", NEED_VARS)
    assert isinstance(profile, NeedProfile)
    assert profile.specialization == "be" and profile.grade == "middle"
    ids = {n.id for n in profile.nodes}
    assert {"be.B1", "be.B2", "be.B3", "be.D4", "be.C1"} <= ids
    assert "python" in profile.stack_tags and "postgresql" in profile.stack_tags
    fe = await StubProvider().complete_json(
        "need_parse", {**NEED_VARS, "need_text": "Senior React/TypeScript фронтенд, вёрстка"}
    )
    assert fe.specialization == "fe" and fe.grade == "senior"
    assert all(n.min_level == 3 for n in fe.nodes)


def test_grade_by_keywords_orders_answers() -> None:
    strong = (
        "Нужен составной индекс: сначала колонки равенства, затем сортировка. Проверю планом "
        "EXPLAIN ANALYZE и посмотрю селективность. Цена индекса — запись и место; covering "
        "как опция через INCLUDE."
    )
    partial = "Сделал бы составной индекс и проверил план через EXPLAIN ANALYZE."
    weak = "Добавлю кэш и всё ускорится."
    g_strong = grade_by_keywords(strong, RUBRIC)
    g_partial = grade_by_keywords(partial, RUBRIC)
    g_weak = grade_by_keywords(weak, RUBRIC)
    assert g_strong.score > g_partial.score > g_weak.score
    assert g_strong.score >= 0.9 and g_weak.score == 0
    assert FLAG_HEURISTIC in g_strong.flags
    assert {c.id for c in g_strong.criteria} == {r["id"] for r in RUBRIC}
    assert all(c.hit in (0.0, 0.5, 1.0) for c in g_partial.criteria)
    assert g_strong.feedback_short.startswith("Оценка без ИИ")


def test_grade_by_keywords_flags_copy_and_empty() -> None:
    reference = VARIANT["reference_answer"]
    copied = grade_by_keywords(reference, RUBRIC, reference)
    assert FLAG_TOO_CLOSE in copied.flags
    empty = grade_by_keywords("", RUBRIC, reference)
    assert empty.score == 0 and "empty_answer" in empty.flags


async def test_stub_variant_judge_accepts_anchor_and_flags_leak() -> None:
    judge = StubProvider()
    ok = await judge.complete_json("variant_judge", PROMPT_VARIABLES["variant_judge"])
    assert isinstance(ok, JudgeReport) and ok.accepted
    leaky = {
        "statement": "Ответ: составной индекс (a, b, c DESC); проверить EXPLAIN ANALYZE; "
        "цена записи. Объясните, почему.",
        "reference_answer": VARIANT["reference_answer"],
        "trap_realized": "",
    }
    bad = await judge.complete_json(
        "variant_judge", {"template": TEMPLATE, "anchor": VARIANT, "variant": leaky}
    )
    assert bad.leak_in_statement and not bad.accepted


async def test_stub_resume_parse_years_and_stack() -> None:
    facts = await StubProvider().complete_json("resume_parse", PROMPT_VARIABLES["resume_parse"])
    assert isinstance(facts, ResumeFacts)
    assert facts.years == 5 and "fastapi" in facts.stack and "postgresql" in facts.stack
    assert facts.roles and "backend-разработчик" in facts.roles


# --- сервис ------------------------------------------------------------------------------------


async def test_service_records_journal_without_db_and_without_pii() -> None:
    journal = LlmJournal()
    service = LLMService(provider=StubProvider(), journal=journal)
    variables = {
        **NEED_VARS,
        "need_text": "Контакт Иванов Иван Иванович, ivan@example.com, +7 916 123-45-67. Python.",
    }
    result = await service.complete("need_parse", variables, session=None)
    assert result.status == "ok" and result.prompt_hash
    assert journal.counter.calls == 1 and journal.counter.tokens_total > 0
    record = journal.recent[-1]
    assert record.kind == "need_parse" and record.status == "ok"
    assert record.request_json["attempts"] == 1 and record.request_json["template_hash"]
    assert not contains_pii(str(record.request_json))
    assert "[email]" in record.request_json["variables"]["need_text"]
    assert record.response_json["specialization"] == "be"


async def test_service_cache_hit_by_hash() -> None:
    journal = LlmJournal()
    service = LLMService(provider=StubProvider(), journal=journal)
    first = await service.complete("need_parse", NEED_VARS)
    second = await service.complete("need_parse", NEED_VARS)
    assert first.status == "ok" and second.status == "cached"
    assert second.parsed == first.parsed and second.prompt_hash == first.prompt_hash
    assert journal.counter.by_status == {"ok": 1, "cached": 1}
    third = await service.complete("need_parse", NEED_VARS, use_cache=False)
    assert third.status == "ok"
    # другой вход — другой хэш
    other = await service.complete("need_parse", {**NEED_VARS, "need_text": "другое"})
    assert other.status == "ok" and other.prompt_hash != first.prompt_hash


async def test_service_daily_cap() -> None:
    """Лимит расходуют только платные провайдеры: stub cap не трогает, scripted
    (условно платный, 15 токенов за вызов) при cap=10 исчерпывает его первым вызовом."""
    settings = Settings(llm_daily_token_cap=10)
    journal = LlmJournal()
    free = LLMService(provider=StubProvider(), journal=journal, settings=settings)
    await free.complete("need_parse", NEED_VARS, use_cache=False)
    assert journal.counter.tokens_total > 0 and journal.counter.billable_total == 0
    service = LLMService(
        provider=ScriptedProvider([VALID_NEED_JSON]), journal=journal, settings=settings
    )
    await service.complete("need_parse", NEED_VARS, use_cache=False)
    assert journal.counter.billable_total == 15
    with pytest.raises(LLMBudgetExceededError) as exc:
        await service.complete("need_parse", NEED_VARS, use_cache=False)
    assert journal.recent[-1].status == "error"
    error = to_api_error(exc.value)
    assert error.status_code == 503 and error.code == "llm_budget_exceeded"
    # кэшированный ответ отдаётся и при исчерпанном лимите
    cached = await service.complete("need_parse", NEED_VARS)
    assert cached.status == "cached"
    assert journal.counter.snapshot(10)["cap_exhausted"] is True


async def test_service_complete_json_and_unknown_prompt() -> None:
    service = LLMService(provider=StubProvider(), journal=LlmJournal())
    parsed = await service.complete_json("need_parse", NEED_VARS)
    assert isinstance(parsed, NeedProfile)
    from app.modules.llm.prompts import LLMPromptError

    with pytest.raises(LLMPromptError):
        await service.complete_json("no_such_prompt", {})


def test_build_provider_chains() -> None:
    settings = get_settings()
    assert isinstance(build_provider(settings, "stub"), StubProvider)
    rules = build_provider(settings, "rules")
    assert isinstance(rules, FallbackProvider)
    assert [p.name for p in rules.providers] == ["rules", "stub"]
    giga = build_provider(settings, "gigachat")
    assert [p.name for p in giga.providers] == ["gigachat", "stub"]  # резерв без ключей не включён
    yandex = build_provider(settings, "yandex")
    assert [p.name for p in yandex.providers] == ["yandex", "stub"]
    assert build_provider(settings, "replay").name == "replay"
    with pytest.raises(ValueError):
        build_provider(settings, "unknown")


async def test_real_providers_without_keys_fall_back_to_stub() -> None:
    """Ключей на машине нет: gigachat/yandex поднимают LLMConfigError, цепочка отдаёт stub."""
    from app.modules.llm.base import LLMConfigError
    from app.modules.llm.providers.gigachat import GigaChatProvider
    from app.modules.llm.providers.yandex import YandexProvider

    prompt = load_prompt("need_parse")
    giga = GigaChatProvider(credentials="")
    assert not giga.configured and giga.model_for("fast") == "GigaChat-2-Lite"
    with pytest.raises(LLMConfigError) as exc:
        await giga.complete(prompt, NEED_VARS)
    assert "GIGACHAT_CREDENTIALS" in exc.value.message
    ya = YandexProvider(folder_id="", api_key="")
    assert not ya.configured
    with pytest.raises(LLMConfigError) as exc:
        await ya.complete(prompt, NEED_VARS)
    assert "YC_FOLDER_ID" in exc.value.message

    service = LLMService(provider=build_provider(get_settings(), "gigachat"), journal=LlmJournal())
    result = await service.complete("need_parse", NEED_VARS)
    assert result.provider == "stub" and result.status == "fallback"
    assert result.fallback_from == ["gigachat"]
    assert service.journal.recent[-1].status == "fallback"
    assert service.journal.recent[-1].request_json["fallback_from"] == ["gigachat"]


async def test_usage_report_shape() -> None:
    service = LLMService(provider=StubProvider())
    await service.complete("need_parse", NEED_VARS)
    report = usage_report()
    assert report["provider"] == "stub" and report["chain"] == ["stub"]
    assert report["today"]["calls"] == 1 and report["today"]["tokens_total"] > 0
    assert report["queue"]["limit"] >= 1 and report["queue"]["in_flight"] == 0
    assert report["recent"][-1]["kind"] == "need_parse"
    assert "variables" not in report["recent"][-1]


def test_gigachat_provider_stores_tls_options() -> None:
    """ca_bundle_file и verify_ssl_certs доходят до провайдера (НУЦ Минцифры)."""
    from app.modules.llm.providers.gigachat import GigaChatProvider

    provider = GigaChatProvider(
        credentials="x", ca_bundle_file="/certs/bundle.pem", verify_ssl_certs=False
    )
    assert provider._ca_bundle_file == "/certs/bundle.pem"  # noqa: SLF001
    assert provider._verify_ssl_certs is False  # noqa: SLF001


def test_gigachat_factory_wires_tls_settings() -> None:
    """Фабрика _gigachat передаёт поля Settings в провайдер."""
    from app.core.config import Settings
    from app.modules.llm.service import _gigachat

    settings = Settings(gigachat_ca_bundle="/certs/bundle.pem", gigachat_verify_ssl_certs=False)
    provider = _gigachat(settings)
    assert provider._ca_bundle_file == "/certs/bundle.pem"  # noqa: SLF001
    assert provider._verify_ssl_certs is False  # noqa: SLF001
