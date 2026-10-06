"""Разбор графа навыков кандидата человеческим языком (`GET /candidates/me/graph-summary`).

Вход для промпта `graph_summary` собирается ТОЛЬКО из обезличенных данных: специализация,
грейд с исходом последнего теста, узлы (код, название, слой, состояние, балл) — ПДн в LLM
не передаются никогда. При любой ошибке LLM текст собирается детерминированным
rules-фолбэком из тех же данных, эндпоинт не падает (паттерн `grade_text_answer`).
Ответ кэшируется на час по ключу (кандидат, последний пересчёт снимка) — повторные клики
по кнопке не жгут GigaChat; новый пересчёт `candidate_skill_state` меняет ключ сам.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache
from app.core.errors import not_found
from app.models.assessment import TestSession
from app.models.candidate import CandidateCategory, CandidateProfile, CandidateSkillState
from app.modules.assessment.sessions import api_node_state, skills_catalog
from app.modules.candidates.schemas import GraphSummaryOut
from app.modules.llm.schemas import SummaryText
from app.modules.llm.service import get_llm_service

logger = logging.getLogger("app.candidates.summary")

CACHE_TTL_SECONDS = 3600
NEXT_GRADE = {"junior": "middle", "middle": "senior"}


async def build_graph_summary(session: AsyncSession, profile: CandidateProfile) -> GraphSummaryOut:
    """Сводка по графу кандидата: снимки узлов → категория → баллы последнего теста →
    кэш → LLM (или rules-фолбэк). 404 — если снимков узлов ещё нет (тест не пройден).

    Порядок запросов фиксирован (важно для тестов): снимки узлов → категория →
    последняя завершённая сессия."""
    rows = (
        (
            await session.execute(
                select(CandidateSkillState)
                .where(CandidateSkillState.candidate_id == profile.id)
                .order_by(CandidateSkillState.node_id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise not_found("Граф навыков ещё пуст: пройдите тест на грейд")
    category_query = select(CandidateCategory).where(CandidateCategory.candidate_id == profile.id)
    if profile.specialization_code:
        category_query = category_query.where(
            CandidateCategory.specialization_code == profile.specialization_code
        )
    category = (
        await session.execute(category_query.order_by(desc(CandidateCategory.computed_at)).limit(1))
    ).scalar_one_or_none()
    last_session = (
        await session.execute(
            select(TestSession)
            .where(TestSession.candidate_id == profile.id, TestSession.status == "finished")
            .order_by(desc(TestSession.finished_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    last_computed = max((row.computed_at for row in rows if row.computed_at), default=None)
    computed_key = last_computed.isoformat() if last_computed else "none"
    cache_suffix = f"graph-summary:{profile.id}:{computed_key}"
    cache = get_cache()
    cached = await cache.get_json(cache_suffix)
    if cached is not None:
        return GraphSummaryOut(**cached)

    profile_facts, node_facts = _collect_facts(profile, rows, category, last_session)
    llm = get_llm_service()
    try:
        result = await llm.complete(
            "graph_summary",
            {"profile": profile_facts, "nodes": node_facts},
            schema=SummaryText,
            session=None,
        )
        parsed = result.parsed
        if not isinstance(parsed, SummaryText):  # устаревший кэш другой схемы
            raise TypeError(f"graph_summary вернул {type(parsed).__name__}")
        summary, source = parsed.text, "llm"
    except Exception as exc:  # noqa: BLE001 — разбор не должен ронять эндпоинт
        logger.warning("graph_summary: ошибка LLM, rules-фолбэк: %s", exc)
        summary, source = rules_graph_summary(profile_facts, node_facts), "rules"

    out = GraphSummaryOut(summary=summary, source=source, generated_at=datetime.now(UTC))
    await cache.set_json(cache_suffix, out.model_dump(mode="json"), CACHE_TTL_SECONDS)
    return out


def _collect_facts(
    profile: CandidateProfile,
    rows: list[CandidateSkillState],
    category: CandidateCategory | None,
    last_session: TestSession | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Обезличенные факты для промпта: без псевдонима, ФИО и прочих ПДн."""
    catalog = skills_catalog()
    spec_code = profile.specialization_code or (category.specialization_code if category else None)
    spec = catalog.spec(spec_code) if spec_code else None
    next_targets: frozenset[str] = frozenset()
    if category is not None and spec_code:
        next_grade = NEXT_GRADE.get(category.grade)
        next_profile = catalog.profile(spec_code, next_grade) if next_grade else None
        if next_profile is not None:
            next_targets = frozenset(next_profile.target_nodes)
    scores = dict(last_session.node_scores or {}) if last_session else {}
    profile_facts: dict[str, Any] = {
        "specialization": spec.meta.title if spec else spec_code,
        "grade": category.grade if category else None,
        "grade_outcome": last_session.outcome if last_session else None,
        "next_grade": NEXT_GRADE.get(category.grade) if category else None,
    }
    node_facts: list[dict[str, Any]] = []
    for row in rows:
        node_spec = catalog.spec(row.node_id.split(".", 1)[0])
        node = node_spec.nodes_by_id.get(row.node_id) if node_spec else None
        if node_spec is None or node is None:
            continue  # узел вне справочника — в разбор не попадает
        thresholds = node_spec.file.thresholds
        node_facts.append(
            {
                "id": row.node_id,
                "title": node.title,
                "layer": node.layer,
                "state": api_node_state(
                    row.active_level >= 1, float(row.w_now), thresholds.fresh, thresholds.dim
                ),
                "score": scores.get(row.node_id),
                "next_grade_target": row.node_id in next_targets,
            }
        )
    # незакрытые узлы профиля следующего грейда (снимка нет вообще) — тоже данные для LLM
    known = {fact["id"] for fact in node_facts}
    for node_id in sorted(next_targets - known):
        node_spec = catalog.spec(node_id.split(".", 1)[0])
        node = node_spec.nodes_by_id.get(node_id) if node_spec else None
        if node is None:
            continue
        node_facts.append(
            {
                "id": node_id,
                "title": node.title,
                "layer": node.layer,
                "state": "off",
                "score": None,
                "next_grade_target": True,
            }
        )
    return profile_facts, node_facts


def rules_graph_summary(profile_facts: dict[str, Any], node_facts: list[dict[str, Any]]) -> str:
    """Детерминированный фолбэк: те же 3 абзаца шаблонно из данных, без LLM."""

    def titles(facts: list[dict[str, Any]], limit: int = 5) -> str:
        return ", ".join(f"«{f['title']}»" for f in facts[:limit])

    fresh = sorted(
        (f for f in node_facts if f["state"] == "fresh"),
        key=lambda f: -(f["score"] or 0.0),
    )
    fading = [f for f in node_facts if f["state"] in ("fading", "expired")]
    to_grow = [f for f in node_facts if f["next_grade_target"] and f["state"] != "fresh"]

    if fresh:
        p1 = (
            f"Сильная зона: {titles(fresh)} — эти узлы подтверждены и свежи, "
            "на практике это задачи, которые вы закрываете самостоятельно."
        )
    else:
        p1 = "Свежих подтверждённых узлов пока нет — сильная зона ещё не сформирована."

    if fading:
        p2 = (
            f"Затухает или погасло: {titles(fading)} — без нового подтверждения эти узлы "
            "перестанут учитываться в подборе и профиль ослабнет."
        )
    else:
        p2 = "Затухающих узлов нет: подтверждения свежие, профиль не теряет силу."

    next_grade = profile_facts.get("next_grade")
    if to_grow and next_grade:
        p3 = (
            f"Чтобы выйти на грейд {next_grade}, в первую очередь подтяните: {titles(to_grow)} — "
            "эти узлы входят в профиль следующего грейда и связаны с вашей сильной зоной."
        )
    elif to_grow:
        p3 = f"В первую очередь стоит подтвердить: {titles(to_grow)}."
    else:
        p3 = (
            "Все узлы целевого профиля подтверждены — поддерживайте свежесть подтверждений "
            "и расширяйте профиль смежными узлами."
        )
    return "\n\n".join([p1, p2, p3])
