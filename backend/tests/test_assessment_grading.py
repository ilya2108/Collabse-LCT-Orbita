"""Юниты движка сессий (`assessment/sessions.py`): подбор шаблонов по (демо-)профилю,
детерминированный подсчёт choice, агрегация баллов узлов, маппинг состояния узла в API
и кулдаун повторного теста. Всё — чистые функции, без БД и LLM (кроме теста фолбэка)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.core.config import get_settings

# модель под алиасом: имена Test* pytest пытается собрать как тест-классы
from app.models.assessment import TestItem as ItemModel
from app.models.candidate import CandidateCategory
from app.modules.assessment import sessions as engine
from app.modules.assessment.templates import TaskTemplate as TemplateDef
from app.modules.assessment.templates import TemplateCatalog
from app.skills.loader import get_catalog as get_skills_catalog

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def template_def(
    node: str = "be.B1",
    level: int = 1,
    kind: str = "mcq_reasoned",
    slug: str = "fixture_one",
    status: str = "draft",
) -> TemplateDef:
    anchor: dict = {
        "statement": "Условие тестового задания, достаточно длинное для валидатора схемы.",
        "reference_answer": "Эталонный ответ, достаточно длинный для валидатора схемы шаблона.",
        "estimated_minutes": 3,
    }
    if kind == "mcq_reasoned":
        anchor["options"] = [
            {"key": "A", "text": "Опция A"},
            {"key": "B", "text": "Опция B"},
            {"key": "C", "text": "Опция C"},
            {"key": "D", "text": "Опция D"},
        ]
        anchor["answer_key"] = "B"
    return TemplateDef(
        id=f"{node}.L{level}.{slug}",
        node=node,
        level=level,
        kind=kind,  # type: ignore[arg-type]
        title="Тестовый шаблон",
        concept="Проверяемая концепция для юнит-теста движка сессий.",
        invariant="Инвариант условия для юнит-теста движка сессий.",
        vary="Варьируемые детали условия.",
        forbidden_changes="Запрещённые изменения условия.",
        trap="Ловушка тестового задания.",
        stack_tags=["sql"],
        rubric=[
            {"id": "first", "w": 0.5, "text": "transaction isolation deadlock retry"},
            {"id": "second", "w": 0.5, "text": "index selectivity ordering"},
        ],
        anchor_variant=anchor,
        difficulty_anchor={"p_junior": 0.3, "p_middle": 0.6, "p_senior": 0.9},
        max_time_sec=300,
        status=status,  # type: ignore[arg-type]
    )


def catalog_of(*templates: TemplateDef) -> TemplateCatalog:
    return TemplateCatalog(skills_path=Path("."), templates=tuple(templates))


class TestSelectTemplate:
    def test_exact_level_preferred(self) -> None:
        t1 = template_def(level=1, slug="one")
        t2 = template_def(level=2, slug="two")
        chosen = engine.select_template(catalog_of(t1, t2), "be.B1", 2)
        assert chosen is t2

    def test_nearest_level_fallback(self) -> None:
        t1 = template_def(level=1, slug="one")
        t3 = template_def(level=3, slug="three")
        chosen = engine.select_template(catalog_of(t1, t3), "be.B1", 2)
        assert chosen is t1  # при равном расстоянии берётся уровень ниже

    def test_reviewed_beats_draft(self) -> None:
        draft = template_def(level=1, slug="one", status="draft")
        reviewed = template_def(level=2, slug="two", status="reviewed")
        chosen = engine.select_template(catalog_of(draft, reviewed), "be.B1", 1)
        assert chosen is reviewed

    def test_no_templates(self) -> None:
        assert engine.select_template(catalog_of(), "be.B1", 1) is None


class TestBuildPlan:
    def test_demo_plan_marks_drafts_and_missing_nodes(self) -> None:
        # демо-профиль junior be: {be.B1, be.B2, be.D1}; шаблон есть только у be.B1
        plan = engine.build_plan(
            "be",
            "junior",
            demo=True,
            templates=catalog_of(template_def(node="be.B1", level=1)),
            skills=get_skills_catalog(),
        )
        assert [t.node for t in plan.templates] == ["be.B1"]
        assert plan.draft_template_ids == ("be.B1.L1.fixture_one",)
        assert len(plan.warnings) == 2  # be.B2 и be.D1 пропущены

    def test_demo_plan_capped_at_five_items(self) -> None:
        skills = get_skills_catalog()
        nodes = skills.profile("be", "senior").demo.target_nodes
        templates = [
            template_def(node=node, level=1, slug=f"cap{i}") for i, node in enumerate(nodes)
        ]
        plan = engine.build_plan(
            "be", "senior", demo=True, templates=catalog_of(*templates), skills=skills
        )
        assert len(plan.templates) <= engine.DEMO_MAX_ITEMS


class TestScoring:
    def test_choice_deterministic(self) -> None:
        payload = engine.anchor_payload(template_def())
        assert engine.score_choice(payload, "B") == 1.0
        assert engine.score_choice(payload, "A") == 0.0
        assert engine.score_choice(payload, None) == 0.0

    def test_node_scores_mean_with_unanswered_zero(self) -> None:
        items = [
            ItemModel(node_id="be.B1", score=1, position=0),
            ItemModel(node_id="be.B1", score=0.5, position=1),
            ItemModel(node_id="be.B2", score=None, position=2),
        ]
        scores = engine.node_scores_of(items)
        assert scores == {"be.B1": 0.75, "be.B2": 0.0}

    def test_api_node_state_mapping(self) -> None:
        assert engine.api_node_state(False, 0.9, 0.8, 0.5) == "off"
        assert engine.api_node_state(True, 0.85, 0.8, 0.5) == "fresh"
        assert engine.api_node_state(True, 0.7, 0.8, 0.5) == "fading"
        assert engine.api_node_state(True, 0.4, 0.8, 0.5) == "expired"

    def test_api_kind_and_body(self) -> None:
        mcq = engine.anchor_payload(template_def())
        text = engine.anchor_payload(template_def(kind="short_design", slug="design_one"))
        assert engine.api_kind(mcq) == "choice"
        assert engine.api_kind(text) == "text"
        assert "Условие" in engine.body_md(mcq)
        code_payload = {**text, "code": "SELECT 1"}
        assert "```" in engine.body_md(code_payload)


class TestGradeTextFallback:
    async def test_llm_error_falls_back_to_rules(self) -> None:
        async def failing_complete(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("стенд недоступен")

        llm = SimpleNamespace(complete=failing_complete)
        payload = engine.anchor_payload(template_def(kind="short_design", slug="design_two"))
        grading, flags = await engine.grade_text_answer(
            llm,  # type: ignore[arg-type]
            payload,
            "transaction isolation deadlock retry; index selectivity ordering",
        )
        assert engine.LLM_FALLBACK_FLAG in flags
        assert grading.score == 1.0  # оба критерия закрыты ключевыми словами

    async def test_stub_provider_grades(self) -> None:
        from app.modules.llm.service import get_llm_service

        payload = engine.anchor_payload(template_def(kind="short_design", slug="design_three"))
        grading, flags = await engine.grade_text_answer(
            get_llm_service(),
            payload,
            "transaction isolation deadlock retry; index selectivity ordering",
        )
        assert flags == []  # stub отвечает сам, фолбэк не нужен
        assert grading.score == 1.0


class TestCooldown:
    def test_none_without_category(self) -> None:
        assert engine.cooldown_until(None, get_settings(), NOW) is None

    def test_stored_available_at_wins(self) -> None:
        category = CandidateCategory(
            id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
            specialization_code="be",
            grade="junior",
            computed_at=NOW - timedelta(days=1),
            grade_change_available_at=NOW + timedelta(seconds=30),
        )
        until = engine.cooldown_until(category, get_settings(), NOW)
        assert until == NOW + timedelta(seconds=30)

    def test_expired_cooldown_allows_test(self) -> None:
        category = CandidateCategory(
            id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
            specialization_code="be",
            grade="junior",
            computed_at=NOW - timedelta(days=2),
            grade_change_available_at=NOW - timedelta(seconds=5),
        )
        assert engine.cooldown_until(category, get_settings(), NOW) is None

    def test_demo_seconds_from_computed_at(self) -> None:
        # conftest: TEST_DEMO_MODE=true, DEMO_COOLDOWN_SECONDS=1
        category = CandidateCategory(
            id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
            specialization_code="be",
            grade="junior",
            computed_at=NOW,
            grade_change_available_at=None,
        )
        until = engine.cooldown_until(category, get_settings(), NOW)
        assert until == NOW + timedelta(seconds=1)
