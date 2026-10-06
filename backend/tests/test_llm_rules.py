"""Провайдер rules (словарь keywords.yaml) и replay (кэш jsonl): без сети и ключей."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.modules.llm.base import FallbackProvider, LLMUnsupportedError
from app.modules.llm.journal import LlmJournal, input_hash
from app.modules.llm.prompts import load_prompt
from app.modules.llm.providers.replay import (
    LLMReplayMissError,
    ReplayProvider,
    ReplayRecord,
    append_replay_record,
    load_replay_index,
)
from app.modules.llm.providers.rules import (
    WARNING_NOT_SUPPORTED,
    RulesProvider,
    detect_grade,
    find_keywords,
    load_keywords,
    parse_keywords_data,
    parse_need_by_rules,
    reset_keywords_cache,
    tokenize,
)
from app.modules.llm.providers.stub import StubProvider
from app.modules.llm.schemas import NeedProfile
from app.modules.llm.service import LLMService
from tests.test_llm_helpers import NEED_VARS, reset_llm_state

KEYWORDS = {
    "be": {
        "be.B1": ["rest", "api", "http", "контракт"],
        "be.D4": ["postgres", "postgresql", "индекс", "транзакц"],
        "be.C1": ["docker", "ci/cd", "деплой"],
        "be.B5": ["безопас", "oauth"],
    },
    "fe": {
        "fe.B1": ["react", "компонент"],
        "fe.B3": ["вёрстк", "верстк", "css"],
    },
    "unsupported_specializations": {"data": ["pandas", "dwh", "аналитик данных"]},
}


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_llm_state()
    reset_keywords_cache()


@pytest.fixture()
def keywords_path(tmp_path: Path) -> Path:
    path = tmp_path / "keywords.yaml"
    path.write_text(yaml.safe_dump(KEYWORDS, allow_unicode=True), encoding="utf-8")
    return path


def test_find_keywords_prefix_and_boundaries() -> None:
    assert find_keywords("Нужны индексы и транзакции", ["индекс", "транзакц"]) == (
        "индекс",
        "транзакц",
    )
    assert find_keywords("rapid prototyping", ["api"]) == ()  # граница слова для коротких
    assert find_keywords("REST API, Ёлка", ["api", "елка"]) == ("api", "елка")  # регистр и ё
    assert find_keywords("знаем C++ и C#", ["c++", "c#"]) == ("c++", "c#")
    assert "индек" in tokenize("индексация индексы") and len(tokenize("на 10 00")) == 0


def test_detect_grade() -> None:
    assert detect_grade("Ищем senior разработчика") == "senior"
    assert detect_grade("Ведущий инженер") == "senior"
    assert detect_grade("Junior/стажёр") == "junior"
    assert detect_grade("middle+ разработчик") == "middle"
    assert detect_grade("просто разработчик") == "middle"


def test_parse_need_by_rules(keywords_path: Path) -> None:
    keywords = load_keywords(keywords_path)
    assert not keywords.is_empty and keywords.source == keywords_path
    profile = parse_need_by_rules(
        "Нужен senior Python-разработчик: REST API на FastAPI, PostgreSQL, индексы и транзакции, "
        "деплой в Docker, OAuth.",
        keywords,
    )
    assert isinstance(profile, NeedProfile)
    assert profile.specialization == "be" and profile.grade == "senior"
    ids = [n.id for n in profile.nodes]
    assert ids[0] == "be.D4"  # больше всего совпадений — первым
    assert set(ids) == {"be.B1", "be.D4", "be.C1", "be.B5"}
    assert all(n.min_level == 3 for n in profile.nodes)
    assert max(n.importance for n in profile.nodes) == 1.0
    assert "python" in profile.stack_tags and "docker" in profile.stack_tags
    assert profile.warnings == []


def test_parse_need_by_rules_fe_and_unsupported(keywords_path: Path) -> None:
    keywords = load_keywords(keywords_path)
    fe = parse_need_by_rules("Junior React: компоненты, вёрстка, CSS", keywords)
    assert fe.specialization == "fe" and fe.grade == "junior"
    assert {n.id for n in fe.nodes} == {"fe.B1", "fe.B3"}
    data = parse_need_by_rules("Аналитик данных: pandas, DWH, отчёты", keywords)
    assert data.specialization is None and data.nodes == []
    assert f"{WARNING_NOT_SUPPORTED}:data" in data.warnings
    mixed = parse_need_by_rules("REST API на бэкенде и React-компоненты", keywords)
    assert any("затрагивает" in w for w in mixed.warnings)
    forced = parse_need_by_rules("REST API и React", keywords, specialization="fe")
    assert forced.specialization == "fe"
    # явная подсказка отключает «не поддерживается»
    forced_data = parse_need_by_rules("pandas и DWH, REST API", keywords, specialization="be")
    assert forced_data.specialization == "be" and forced_data.nodes


FLAT_KEYWORDS = {
    "version": 1,
    "specializations": {"be": ["бэкенд", "backend"], "fe": ["фронтенд", "frontend"]},
    "nodes": {
        "be.B1": ["rest", "api", "контракт"],
        "be.D4": ["индекс", "транзакц"],
        "fe.B1": ["react", "компонент"],
    },
    "unsupported_specializations": [
        {"code": "mobile", "title": "Мобильная разработка", "nearest": "fe",
         "keywords": ["android", "ios", "flutter"]},
    ],
}  # fmt: skip


def test_flat_format_like_w2_file() -> None:
    keywords = parse_keywords_data(FLAT_KEYWORDS)
    assert set(keywords.specializations) == {"be", "fe"}
    assert keywords.specializations["be"]["be.D4"] == ["индекс", "транзакц"]
    assert keywords.spec_keywords["fe"] == ["фронтенд", "frontend"]
    assert keywords.unsupported[0].code == "mobile" and keywords.unsupported[0].nearest == "fe"

    backend = parse_need_by_rules("Бэкенд: REST API, индексы", keywords)
    assert backend.specialization == "be" and {n.id for n in backend.nodes} == {"be.B1", "be.D4"}
    # слова специализации добавляются к попаданиям в узлы; при равенстве побеждает be
    front = parse_need_by_rules("Фронтенд на React: компоненты и контракты API", keywords)
    assert front.specialization == "fe"
    tie = parse_need_by_rules("React-компоненты и контракты API", keywords)
    assert tie.specialization == "be" and any(
        "затрагивает и специализацию fe" in w for w in tie.warnings
    )
    mobile = parse_need_by_rules("Android и iOS на Flutter", keywords)
    assert mobile.specialization is None
    assert f"{WARNING_NOT_SUPPORTED}:mobile" in mobile.warnings
    assert any("Мобильная разработка" in w and "fe" in w for w in mobile.warnings)


def test_real_keywords_file_if_present() -> None:
    """Файл W2 (если уже есть в репозитории) читается и даёт разбор бэкенд-потребности."""
    keywords = load_keywords()
    if keywords.is_empty:
        pytest.skip("app/skills/keywords.yaml ещё не создан")
    profile = parse_need_by_rules(
        "Backend на Python: REST API, PostgreSQL, индексы и транзакции, Docker", keywords
    )
    assert profile.specialization == "be" and profile.nodes


def test_missing_keywords_file_gives_empty_dict(tmp_path: Path) -> None:
    keywords = load_keywords(tmp_path / "nope.yaml")
    assert keywords.is_empty
    profile = parse_need_by_rules("Python backend", keywords)
    assert profile.nodes == [] and any("отсутствует" in w for w in profile.warnings)
    assert profile.specialization == "be"


async def test_rules_provider_and_chain(keywords_path: Path) -> None:
    rules = RulesProvider(keywords_path=keywords_path)
    profile = await rules.complete_json("need_parse", NEED_VARS)
    assert isinstance(profile, NeedProfile) and profile.warnings[0].startswith("режим без LLM")
    assert "be.D4" in {n.id for n in profile.nodes}
    with pytest.raises(LLMUnsupportedError):
        await rules.complete_json("answer_grade", {"variant": {}, "rubric": [], "answer": "x"})
    chain = FallbackProvider(rules, [StubProvider()])
    result = await chain.complete(
        load_prompt("answer_grade"), {"variant": {}, "rubric": [], "answer": "x"}
    )
    assert result.provider == "stub" and result.status == "fallback"
    assert result.fallback_from == ["rules"]
    need = await chain.complete(load_prompt("need_parse"), NEED_VARS)
    assert need.provider == "rules" and need.status == "ok"


# --- replay ------------------------------------------------------------------------------------


def _record(variables: dict) -> ReplayRecord:
    return ReplayRecord(
        kind="need_parse",
        prompt_version="v1",
        input_hash=input_hash(variables),
        model="GigaChat-2-Pro",
        response={
            "specialization": "be",
            "grade": "middle",
            "nodes": [{"id": "be.B1", "min_level": 2, "importance": 1.0, "evidence": "REST"}],
            "stack_tags": ["python"],
            "nice_to_have": [],
            "warnings": [],
        },
        tokens_in=500,
        tokens_out=120,
        hash="abc",
    )


async def test_replay_hit_and_miss(tmp_path: Path) -> None:
    path = tmp_path / "llm_cache.jsonl"
    provider = ReplayProvider(path=path)
    with pytest.raises(LLMReplayMissError) as exc:
        await provider.complete(load_prompt("need_parse"), NEED_VARS)
    assert str(path) in exc.value.message and "llm-freeze" in exc.value.message

    append_replay_record(path, _record(NEED_VARS))
    assert len(load_replay_index(path)) == 1
    result = await provider.complete(load_prompt("need_parse"), NEED_VARS)
    assert result.provider == "replay" and result.model == "GigaChat-2-Pro"
    assert result.tokens_in == 500 and result.parsed.nodes[0].id == "be.B1"
    # другой вход — промах, а не ближайший ответ
    with pytest.raises(LLMReplayMissError):
        await provider.complete(load_prompt("need_parse"), {**NEED_VARS, "need_text": "иное"})


async def test_replay_through_service(tmp_path: Path) -> None:
    path = tmp_path / "llm_cache.jsonl"
    append_replay_record(path, _record(NEED_VARS))
    path.write_text(path.read_text(encoding="utf-8") + "не json\n{}\n", encoding="utf-8")
    service = LLMService(provider=ReplayProvider(path=path), journal=LlmJournal())
    result = await service.complete("need_parse", NEED_VARS)
    assert result.provider == "replay" and result.status == "ok"
    assert service.journal.recent[-1].provider == "replay"
